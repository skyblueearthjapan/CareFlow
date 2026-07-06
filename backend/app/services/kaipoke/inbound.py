"""カイポケ → CareFlow 逆反映 (R-1/R-2・docs/plans/kaipoke-reverse-sync-design.md).

「週のバトンリレー」: 週apply でその週の正はカイポケに移る。提供中の週に
カイポケ側で入った直し込み (キャンセル・時刻変更) を CareFlow visits へ
書き写して追いかけるのが本モジュール。

安全設計:
  * apply実績ゲート — CareFlow から実apply (dry_run=false) した記録のある週だけ
    取り込み可 (``real_apply_record``)。正がカイポケに移っていない週は取り込まない。
  * その週限りの原則 — visits のみ書き、固定パターン (patient_fixed_visits) には
    一切触れない。edit/date_change は ``source='manual_week'`` を刻み週再生成から保護。
  * キャンセルは ``status='cancelled'`` (soft-delete しない・履歴が残る)。
  * 2名体制の防御 — 対象 visit が visit_group_id を持つ場合はグループ全行に同じ
    操作を適用する (片割れだけ残さない)。※本番データは現状グループ0件。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.kaipoke_job import KaipokeJob
from app.models.visit import VISIT_STATUS_CANCELLED, Visit

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.correction_sheet import CorrectionSheetItem

# 取り込みの実績刻印 (visit.note に追記する行の接頭辞)。人間向け日本語のため
# モバイルの内部note非表示 (lib/visit-note.ts) の対象外 = 現場にも表示される。
NOTE_STAMP_PREFIX = "カイポケ取込"


async def real_apply_record(db: AsyncSession, week_start: date) -> KaipokeJob | None:
    """対象週に「実apply (dry_run=false)」の記録があれば最新の job を返す。

    週apply がバトンタッチの瞬間 = この記録がある週だけ正がカイポケに移っている。
    failed も対象に含める: 実書込が部分的に走った可能性があり、その週は既に
    混在状態のため、取り込みでカイポケ現況へ揃える方が安全に働く。
    week_start 列 (索引付き Date) で対象週へ絞ったうえで、params の突合だけ
    Python 側で行う (JSONB 演算子は SQLite テスト環境で使えないため)。
    """
    rows = await db.scalars(
        select(KaipokeJob)
        .where(
            KaipokeJob.job_type == "push",
            KaipokeJob.status.in_(("completed", "failed")),
            # trigger_apply は job.week_start = sheet.week_start を刻む (0056以降)。
            # 旧形式 (月初日) の apply は params にも week_start が無く元々対象外。
            KaipokeJob.week_start == week_start,
        )
        .order_by(KaipokeJob.created_at.desc())
    )
    iso = week_start.isoformat()
    for job in rows.all():
        p = job.params or {}
        if p.get("op") == "apply" and p.get("dry_run") is False and p.get("week_start") == iso:
            return job
    return None


def day_to_date(day: int, week_start: date, week_end: date) -> date | None:
    """CSV の「日」(1-31) を週レンジ内の実日付へ解決する (月境界の折返しも安全)。"""
    cur = week_start
    while cur <= week_end:
        if cur.day == day:
            return cur
        cur = date.fromordinal(cur.toordinal() + 1)
    return None


def parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


async def load_week_visit_index(
    db: AsyncSession, week_start: date, week_end: date
) -> dict[tuple[uuid.UUID, date, time], Visit]:
    """対象週の active visits を (patient_id, date, start_time) で索引化する。

    (patient, date, start) は部分UNIQUE (migration 0026) のため一意に引ける。
    """
    rows = await db.scalars(
        select(Visit).where(
            Visit.deleted_at.is_(None),
            Visit.visit_date >= week_start,
            Visit.visit_date <= week_end,
        )
    )
    return {(v.patient_id, v.visit_date, v.start_time): v for v in rows.all()}


async def _group_partners(db: AsyncSession, visit: Visit) -> list[Visit]:
    """同一 visit_group の全行 (自分含む)。グループ無しなら自分のみ。"""
    if visit.visit_group_id is None:
        return [visit]
    rows = await db.scalars(
        select(Visit).where(
            Visit.visit_group_id == visit.visit_group_id,
            Visit.deleted_at.is_(None),
        )
    )
    return list(rows.all())


def _stamp_note(visit: Visit, message: str, today: date) -> None:
    line = f"{NOTE_STAMP_PREFIX} {today.month}/{today.day}: {message}"
    visit.note = f"{visit.note}\n{line}" if visit.note else line


@dataclass
class InboundItemResult:
    item_id: str
    action: str
    outcome: str  # cancelled / updated / skipped / failed
    detail: str = ""
    patient_name: str = ""
    date: str = ""


@dataclass
class InboundApplySummary:
    cancelled: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[InboundItemResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cancelled": self.cancelled,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "results": [r.__dict__ for r in self.results],
        }


async def apply_inbound_items(
    db: AsyncSession,
    *,
    items: list[CorrectionSheetItem],
    week_start: date,
    week_end: date,
    days: list[date] | None,
    dry_run: bool,
    now: datetime,
) -> InboundApplySummary:
    """inbound CorrectionSheetItem を CareFlow visits へ適用する (同期・ローカル)。

    days 指定時はその日付の item だけを対象にする (曜日チップの複数選択)。
    dry_run=True では一切 mutate せず、予定される結果だけを返す。
    R-2 スコープ: delete(→キャンセル)・edit/date_change(→時刻/日付変更)。
    add (カイポケにのみ存在) とスタッフのみの変更は skipped として可視化 (R-3)。
    """
    summary = InboundApplySummary()
    index = await load_week_visit_index(db, week_start, week_end)
    today = now.date()
    day_set = set(days) if days else None

    for item in items:
        before = item.before or {}
        after = item.after or {}
        patient_name = str(before.get("user_name") or after.get("user_name") or "")

        def _finish(
            outcome: str,
            detail: str,
            target_date: date | None,
            *,
            _item=item,
            _pname=patient_name,
        ) -> None:
            summary.results.append(
                InboundItemResult(
                    item_id=str(_item.id),
                    action=_item.action,
                    outcome=outcome,
                    detail=detail,
                    patient_name=_pname,
                    date=target_date.isoformat() if target_date else "",
                )
            )
            if outcome == "cancelled":
                summary.cancelled += 1
            elif outcome == "updated":
                summary.updated += 1
            elif outcome == "skipped":
                summary.skipped += 1
            else:
                summary.failed += 1
            if not dry_run:
                _item.comment = f"{outcome}: {detail}" if detail else outcome

        # --- 対象日の解決 (before 側が CareFlow の現在地) --------------------
        raw_day = before.get("date") or after.get("date")
        try:
            day = int(str(raw_day))
        except (TypeError, ValueError):
            _finish("failed", f"日付が解釈できません: {raw_day!r}", None)
            continue
        target_date = day_to_date(day, week_start, week_end)
        if target_date is None:
            _finish("failed", f"日付 {day} が週レンジ外です", None)
            continue
        if day_set is not None and target_date not in day_set:
            continue  # 選択外の曜日 — 結果にも数えない (対象外)。

        # --- R-3 送り (追加はまだ取り込まない) ------------------------------
        if item.action == "add":
            _finish("skipped", "カイポケにのみ存在する予定の追加は未対応 (R-3)", target_date)
            continue

        # --- 対象 visit の特定 ----------------------------------------------
        if item.patient_id is None:
            _finish("failed", "利用者名を CareFlow 患者に解決できませんでした", target_date)
            continue
        start_before = parse_hhmm(str(before.get("start_time") or ""))
        visit = None
        if item.visit_id is not None:
            visit = await db.get(Visit, item.visit_id)
            if visit is not None and visit.deleted_at is not None:
                visit = None
        if visit is None and start_before is not None:
            visit = index.get((item.patient_id, target_date, start_before))
        if visit is None:
            _finish("failed", "対象の訪問が CareFlow に見つかりません", target_date)
            continue
        if visit.status == VISIT_STATUS_CANCELLED:
            _finish("skipped", "既にキャンセル済み", target_date)
            continue

        partners = await _group_partners(db, visit)

        # --- delete → キャンセル ---------------------------------------------
        if item.action == "delete":
            if not dry_run:
                for v in partners:
                    v.status = VISIT_STATUS_CANCELLED
                    _stamp_note(v, "カイポケ側で削除されたためキャンセル", today)
            _finish(
                "cancelled",
                f"{target_date.month}/{target_date.day} {before.get('start_time') or ''} をキャンセル",
                target_date,
            )
            continue

        # --- edit / date_change → 時刻・日付の変更 ---------------------------
        start_after = parse_hhmm(str(after.get("start_time") or ""))
        end_after = parse_hhmm(str(after.get("end_time") or ""))
        new_date: date | None = None
        if item.action == "date_change":
            try:
                after_day = int(str(after.get("date")))
            except (TypeError, ValueError):
                after_day = -1
            new_date = day_to_date(after_day, week_start, week_end)
            if new_date is None:
                _finish(
                    "failed", f"変更後の日付 {after.get('date')!r} が週レンジ外です", target_date
                )
                continue

        time_changed = (start_after is not None and start_after != visit.start_time) or (
            end_after is not None and end_after != visit.end_time
        )
        date_changed = new_date is not None and new_date != visit.visit_date
        staff_changed = (before.get("staff1") or "") != (after.get("staff1") or "") or (
            before.get("staff2") or ""
        ) != (after.get("staff2") or "")

        if not time_changed and not date_changed:
            if staff_changed:
                _finish(
                    "skipped",
                    "スタッフのみの変更は取り込み対象外 (CareFlow 側で手動確認)",
                    target_date,
                )
            else:
                _finish("skipped", "変更点なし", target_date)
            continue

        changes: list[str] = []
        if date_changed and new_date is not None:
            changes.append(
                f"{visit.visit_date.month}/{visit.visit_date.day}→{new_date.month}/{new_date.day}"
            )
        if time_changed and start_after is not None:
            changes.append(f"{visit.start_time.strftime('%H:%M')}→{start_after.strftime('%H:%M')}")
        detail = "・".join(changes)
        if staff_changed:
            detail += "（スタッフ変更は未反映・要手動確認）"

        if not dry_run:
            for v in partners:
                if date_changed and new_date is not None:
                    v.visit_date = new_date
                if time_changed:
                    if start_after is not None:
                        v.start_time = start_after
                    if end_after is not None:
                        v.end_time = end_after
                # その週限りの変更として週再生成から保護する。
                v.source = "manual_week"
                _stamp_note(v, detail, today)
        _finish("updated", detail, target_date)

    return summary
