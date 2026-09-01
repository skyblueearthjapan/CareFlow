"""カイポケ個別業務(イベント)の取り込み — 週のバトンリレーのイベント版.

設計正典: docs/plans/kaipoke-event-inbound-design.md

原則:
  * カイポケ職員スケジュール(週間)の個別業務 (btnIndividual 行) を staff_events へ書き写す
  * 取り込みが管理するのは **source='kaipoke' の行だけ**。手動イベント (source='manual')
    には決して触れない (訪問取り込みの「ラウンドトリップ汚染防止」と同じ原則)
  * 冪等キー = external_id "{個別業務ID}:{職員内部ID}:{YYYY-MM-DD}" (RPA 側で生成)。
    upsert 意味論のため、プレビュー後にカイポケ側が動いても apply は安全 (stale 耐性)
  * 日曜分は取り込まない (PO確定・らく助は月〜土運用)。RPA は日曜も返すが本層で除外
  * メモ系 (start==end) もそのまま保存 (表示は📝チップ・ゼロ長のため割当へ影響なし)
  * スタッフ名寄せは訪問取り込みと同一機構 (load_staff_name_index / match_name)。
    未解決職員の予定は取り込まず unmatched として可視化する
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.patient import Patient
from app.models.staff import Staff, StaffEvent
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.kaipoke.inbound import load_staff_name_index
from app.services.kaipoke.name_match import match_name

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.kaipoke_client import KaipokeClient

# 取り込み行の識別子 (staff_events.source)
EVENT_SOURCE_KAIPOKE = "kaipoke"
# 取り込み行の event_type (canon 'event' → FE 表示は「イベント」)
EVENT_TYPE_IMPORTED = "event"
# RPA 同期取得のタイムアウト (ログイン+画面遷移+パースで ~60-90s)
FETCH_TIMEOUT_SECONDS = 150.0
# external_id の妥当性 (apply はクライアントエコーバックを受けるため必ず検証する)
RE_EXTERNAL_ID = re.compile(r"^\d{1,12}:\d{1,12}:\d{4}-\d{2}-\d{2}$")
# note 刻印 (訪問取り込みの NOTE_STAMP_PREFIX と同系)
NOTE_STAMP = "カイポケ個別業務取込"


class EventsFetchError(RuntimeError):
    """RPA からの取得失敗 (レスポンス不正・success=false)。"""


@dataclass
class EventChange:
    """プレビューで検出した1変更 (apply へエコーバックされる単位)。"""

    action: str  # 'add' | 'update' | 'delete'
    external_id: str
    staff_id: uuid.UUID
    staff_name: str
    date: date
    start: time
    end: time
    title: str
    # update のときの現在値 (UI 表示用)
    before_start: time | None = None
    before_end: time | None = None
    before_title: str | None = None

    @property
    def is_memo(self) -> bool:
        return self.start == self.end


@dataclass
class EventsPlan:
    """プレビュー結果 (差分計画)。"""

    week_start: date
    week_end: date  # 月〜土の土曜 (取り込み対象範囲の末日)
    changes: list[EventChange] = field(default_factory=list)
    # 名寄せ未解決: staff_name → 件数 (らく助未登録職員の予定は取り込まない)
    unmatched: dict[str, int] = field(default_factory=dict)
    sunday_skipped: int = 0
    fetched_total: int = 0
    # RPA の表示週に含まれなかった対象日 (月跨ぎ週の月初側で前月末日が出ないケース・
    # 2026-09-01 実測)。この日は追加も削除判定もしない (現状維持) — UI で明示する。
    uncovered_days: list[date] = field(default_factory=list)

    @property
    def memo_count(self) -> int:
        return sum(1 for c in self.changes if c.action != "delete" and c.is_memo)


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def week_range_mon_sat(week_start: date) -> tuple[date, date]:
    """取り込み対象の週レンジ (月〜土)。week_start は月曜であること。"""
    if week_start.weekday() != 0:
        raise ValueError(f"week_start must be a Monday: {week_start}")
    return week_start, week_start + timedelta(days=5)


async def fetch_week_tasks(
    kaipoke: KaipokeClient,
    week_start: date,
    credentials: dict[str, str] | None,
) -> dict[str, Any]:
    """RPA /api/individual-tasks から対象週の個別業務を取得する。

    Returns: RPA の result dict ({week_start, week_end, tasks: [...]})。
    Raises: EventsFetchError (success=false / 構造不正)。
    """
    payload: dict[str, Any] = {"date": week_start.isoformat()}
    if credentials:
        payload["credentials"] = credentials
    resp = await kaipoke.individual_tasks(payload, timeout=FETCH_TIMEOUT_SECONDS)
    result = resp.get("result") or {}
    if not resp.get("success") or not result.get("success"):
        raise EventsFetchError(str(resp.get("error") or "individual-tasks failed"))
    if not isinstance(result.get("tasks"), list):
        raise EventsFetchError("individual-tasks: tasks missing in response")
    return result


@dataclass
class EventVisitConflict:
    """取込イベント × 既存訪問の時間重なり (案A・2026-08-21 ユーザー確定).

    取り込み自体は行い (カイポケが正)、重なりは隠さず警告として返す。
    解消 (担当変更 / 訪問移動 / イベント側の訂正) は人の判断に委ねる。
    """

    staff_id: uuid.UUID
    staff_name: str
    date: date
    event_title: str
    event_start: time
    event_end: time
    patient_name: str
    visit_start: time
    visit_end: time


async def find_event_visit_conflicts(
    db: AsyncSession,
    week_start: date,
    items: list[tuple[uuid.UUID, date, time, time, str]],
) -> list[EventVisitConflict]:
    """イベント (staff_id, date, start, end, title) と当該週の訪問の時間重なりを検出する。

    - 対象訪問 = 削除・キャンセル以外で、そのスタッフが担当 (primary または
      2人体制の assignment) のもの。
    - メモ系 (start==end) は呼び出し側で除外してから渡すこと。
    - 判定は半開区間の交差 (visit.start < ev.end AND visit.end > ev.start)。
    """
    if not items:
        return []
    mon = week_start
    sunday = week_start + timedelta(days=6)
    staff_ids = {it[0] for it in items}

    # 当該週・対象スタッフの訪問 (primary + assignment の両経路)
    base = (
        select(
            Visit.id,
            Visit.visit_date,
            Visit.start_time,
            Visit.end_time,
            Patient.name,
        )
        .join(Patient, Patient.id == Visit.patient_id)
        .where(
            Visit.visit_date >= mon,
            Visit.visit_date <= sunday,
            Visit.deleted_at.is_(None),
            Visit.status != "cancelled",
        )
    )
    primary_rows = (
        await db.execute(
            base.add_columns(Visit.primary_staff_id).where(Visit.primary_staff_id.in_(staff_ids))
        )
    ).all()
    assign_rows = (
        await db.execute(
            base.add_columns(VisitStaffAssignment.staff_id)
            .join(VisitStaffAssignment, VisitStaffAssignment.visit_id == Visit.id)
            .where(VisitStaffAssignment.staff_id.in_(staff_ids))
        )
    ).all()

    # (staff_id, date) → [(patient_name, start, end)] (primary/assignment 重複は排除)
    by_staff_day: dict[tuple[uuid.UUID, date], list[tuple[str, time, time]]] = {}
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for vid, vdate, vstart, vend, pname, sid in [*primary_rows, *assign_rows]:
        if sid is None or (vid, sid) in seen:
            continue
        seen.add((vid, sid))
        by_staff_day.setdefault((sid, vdate), []).append((pname or "(無名)", vstart, vend))

    staff_names = {
        s.id: s.name for s in (await db.scalars(select(Staff).where(Staff.id.in_(staff_ids)))).all()
    }

    conflicts: list[EventVisitConflict] = []
    for staff_id, d, ev_start, ev_end, title in items:
        for pname, vstart, vend in by_staff_day.get((staff_id, d), []):
            if vstart < ev_end and vend > ev_start:
                conflicts.append(
                    EventVisitConflict(
                        staff_id=staff_id,
                        staff_name=staff_names.get(staff_id, "(不明)"),
                        date=d,
                        event_title=title,
                        event_start=ev_start,
                        event_end=ev_end,
                        patient_name=pname,
                        visit_start=vstart,
                        visit_end=vend,
                    )
                )
    conflicts.sort(key=lambda c: (c.date, c.staff_name, c.visit_start))
    return conflicts


async def load_kaipoke_events(db: AsyncSession, week_start: date) -> dict[str, StaffEvent]:
    """対象週 (月〜土) の source='kaipoke' 行を external_id → row で返す。"""
    mon, sat = week_range_mon_sat(week_start)
    range_start = datetime.combine(mon, time.min)
    range_end = datetime.combine(sat + timedelta(days=1), time.min)
    rows = await db.scalars(
        select(StaffEvent).where(
            StaffEvent.source == EVENT_SOURCE_KAIPOKE,
            StaffEvent.starts_at >= range_start,
            StaffEvent.starts_at < range_end,
        )
    )
    out: dict[str, StaffEvent] = {}
    for row in rows.all():
        if row.external_id:
            out[row.external_id] = row
    return out


async def build_events_plan(
    db: AsyncSession,
    *,
    week_start: date,
    tasks: list[dict[str, Any]],
    covered_dates: set[date] | None = None,
) -> EventsPlan:
    """RPA の tasks と staff_events(source='kaipoke') を突合して差分計画を作る。

    ``covered_dates`` = RPA が実際に表示できた週の日付集合 (result["week_dates"])。
    月跨ぎ週では要求週の一部 (例: 8/31) が表示されないことがあり、その日は
    「カイポケに無い」と誤認して delete を出さないよう判定から除外する。
    """
    mon, sat = week_range_mon_sat(week_start)
    plan = EventsPlan(week_start=mon, week_end=sat, fetched_total=len(tasks))
    if covered_dates is not None:
        plan.uncovered_days = [
            mon + timedelta(days=i)
            for i in range((sat - mon).days + 1)
            if (mon + timedelta(days=i)) not in covered_dates
        ]

    name_index, staff_map = await load_staff_name_index(db)
    id_by_str = {str(sid): sid for sid in staff_map}

    desired: dict[str, EventChange] = {}
    for t in tasks:
        d_str = str(t.get("date") or "")
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue  # RPA 側 day 検証済みのため実質到達しない
        if d.weekday() == 6:  # 日曜 (PO確定: 取り込まない)
            plan.sunday_skipped += 1
            continue
        if not (mon <= d <= sat):
            continue  # 週レンジ外 (表示週ズレの防御・RPA 側でも検証済み)

        external_id = t.get("external_key")
        start = _parse_hhmm(t.get("start"))
        end = _parse_hhmm(t.get("end"))
        title = str(t.get("title") or "").strip()
        staff_name = str(t.get("staff_name") or "").strip()
        if not external_id or start is None or end is None:
            # ID/時刻が取れない行は名寄せ以前の要確認 (unmatched に計上して可視化)
            plan.unmatched[staff_name or "(不明)"] = (
                plan.unmatched.get(staff_name or "(不明)", 0) + 1
            )
            continue

        matched = match_name(staff_name, name_index)
        if matched is None:
            plan.unmatched[staff_name] = plan.unmatched.get(staff_name, 0) + 1
            continue
        staff = staff_map[id_by_str[matched]]

        if external_id in desired:
            continue  # 重複キーの防御 (パーサ保証済みだが二重挿入は事故になるため)
        desired[external_id] = EventChange(
            action="add",
            external_id=str(external_id),
            staff_id=staff.id,
            staff_name=staff.name,
            date=d,
            start=start,
            end=end,
            title=title[:255],
        )

    existing = await load_kaipoke_events(db, week_start)

    for external_id, change in desired.items():
        row = existing.get(external_id)
        if row is None:
            plan.changes.append(change)  # add
            continue
        # 変更検出 (時刻/タイトル/担当)。日付は external_id に含まれるため不変
        same = (
            row.starts_at.time() == change.start
            and row.ends_at.time() == change.end
            and (row.title or "") == change.title
            and row.staff_id == change.staff_id
        )
        if not same:
            change.action = "update"
            change.before_start = row.starts_at.time()
            change.before_end = row.ends_at.time()
            change.before_title = row.title
            plan.changes.append(change)

    uncovered = set(plan.uncovered_days)
    for external_id, row in existing.items():
        if external_id not in desired:
            # 表示週に含まれなかった日は「取得できていない」だけ — 削除しない。
            if row.starts_at.date() in uncovered:
                continue
            # staff_map (名寄せ用に全スタッフをロード済み) を流用して N+1 を避ける
            staff_row = staff_map.get(row.staff_id)
            plan.changes.append(
                EventChange(
                    action="delete",
                    external_id=external_id,
                    staff_id=row.staff_id,
                    staff_name=staff_row.name if staff_row else "",
                    date=row.starts_at.date(),
                    start=row.starts_at.time(),
                    end=row.ends_at.time(),
                    title=row.title or "",
                )
            )

    # 表示安定のため 職員名→日付→開始 でソート (delete は末尾に寄せる)
    plan.changes.sort(
        key=lambda c: (c.action == "delete", c.staff_name, c.date, c.start.isoformat())
    )
    return plan


@dataclass
class EventApplyResult:
    action: str
    external_id: str
    staff_name: str
    date: str
    title: str
    outcome: str  # 'added' | 'updated' | 'deleted' | 'skipped' | 'failed'
    detail: str = ""


@dataclass
class EventsApplySummary:
    added: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[EventApplyResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def validate_change_input(
    *,
    action: str,
    external_id: str,
    d: date,
    week_start: date,
) -> str | None:
    """apply のエコーバック入力検証。問題があれば理由文字列を返す。"""
    mon, sat = week_range_mon_sat(week_start)
    if action not in ("add", "update", "delete"):
        return f"不明なaction: {action}"
    if not RE_EXTERNAL_ID.match(external_id):
        return "external_id の形式が不正です"
    if not external_id.endswith(d.isoformat()):
        return "external_id と date が一致しません"
    if d.weekday() == 6:
        return "日曜分は取り込み対象外です"
    if not (mon <= d <= sat):
        return "対象週の範囲外です"
    return None


async def apply_events_changes(
    db: AsyncSession,
    *,
    week_start: date,
    changes: list[EventChange],
    dry_run: bool,
    now: datetime,
) -> EventsApplySummary:
    """差分計画を staff_events へ適用する (dry_run=True は一切 mutate しない)。

    upsert 意味論 (stale 耐性):
      * add で既に同 external_id が居る → update として扱う
      * update で行が消えている → add として扱う
      * delete で行が消えている → skipped
    manual 行は external_id を持たない (部分索引の対象外) ため、本関数が
    触るのは source='kaipoke' の行のみ (existing の読み出しで絞り済み)。
    """
    summary = EventsApplySummary()
    existing = await load_kaipoke_events(db, week_start)
    stamp = f"{NOTE_STAMP} {now.date().isoformat()}"

    for change in changes:
        reason = validate_change_input(
            action=change.action,
            external_id=change.external_id,
            d=change.date,
            week_start=week_start,
        )
        result = EventApplyResult(
            action=change.action,
            external_id=change.external_id,
            staff_name=change.staff_name,
            date=change.date.isoformat(),
            title=change.title,
            outcome="failed",
        )
        if reason is not None:
            result.detail = reason
            summary.failed += 1
            summary.results.append(result)
            continue

        row = existing.get(change.external_id)

        if change.action == "delete":
            if row is None:
                result.outcome = "skipped"
                result.detail = "既に存在しません"
                summary.skipped += 1
            else:
                if not dry_run:
                    await db.delete(row)
                    existing.pop(change.external_id, None)
                result.outcome = "deleted"
                summary.deleted += 1
            summary.results.append(result)
            continue

        # add / update (upsert)
        staff = await db.get(Staff, change.staff_id)
        if staff is None or staff.deleted_at is not None:
            result.detail = "スタッフが見つかりません (削除済み?)"
            summary.failed += 1
            summary.results.append(result)
            continue

        starts_at = datetime.combine(change.date, change.start)
        ends_at = datetime.combine(change.date, change.end)

        if row is None:
            if not dry_run:
                new_row = StaffEvent(
                    staff_id=change.staff_id,
                    event_type=EVENT_TYPE_IMPORTED,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    title=change.title or None,
                    note=stamp,
                    source=EVENT_SOURCE_KAIPOKE,
                    external_id=change.external_id,
                )
                db.add(new_row)
                existing[change.external_id] = new_row
            result.outcome = "added"
            summary.added += 1
        else:
            changed = (
                row.starts_at != starts_at
                or row.ends_at != ends_at
                or (row.title or "") != change.title
                or row.staff_id != change.staff_id
            )
            if not changed:
                result.outcome = "skipped"
                result.detail = "変更なし"
                summary.skipped += 1
            else:
                if not dry_run:
                    row.staff_id = change.staff_id
                    row.starts_at = starts_at
                    row.ends_at = ends_at
                    row.title = change.title or None
                    row.note = stamp
                result.outcome = "updated"
                summary.updated += 1
        summary.results.append(result)

    return summary
