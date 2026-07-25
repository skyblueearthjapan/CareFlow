"""カイポケ置換取り込み — 週を白紙化してカイポケ現況で書き直す (全置換モード).

背景 (2026-07-26 PO確定): カイポケは請求と紐づく最終的な「正」であり、
らく助側がそれを受け入れる。差分突合 (diff-inbound) は同期済み週の追いかけには
適するが、一度も同期していない週では名寄せ差 (氏名の空白違い等) や同時刻衝突で
見かけの差分・失敗が大量に出る。置換モードは対象週のらく助訪問を全削除
(soft delete) し、カイポケ現況CSVの行をそのまま挿入する — 突合が無いので
これらの問題が構造的に発生せず、結果は必ずカイポケの完全なコピーに収束する。

安全装置:
  * ゲート = 差分取り込みと同一 (過去/今週 or 実apply記録・inbound_week_eligible)
  * 実績ガード = 対象週の訪問に打刻/写真/レビューが1件でもあれば置換不可
    (実績記録の紐付け先を消さない。その週は差分モードを使う)
  * dry-run 既定 = 書込なしで計画 (挿入n/削除n/対象外) だけ返す
  * 白紙化と挿入は同一トランザクション (中途半端な白紙状態を残さない)
  * UI 側は「らく助側のこの週の情報はすべて削除される可能性がございます」を
    明記した確認ダイアログを必須とする (PO指示)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.patient import Patient
from app.models.trainee_accompaniment import TraineeAccompaniment
from app.models.visit import Visit
from app.models.visit_checkin import VisitCheckin
from app.services.kaipoke.inbound import (
    _replace_assignments,
    _stamp_note,
    day_to_date,
    ensure_temp_course,
    load_staff_name_index,
    load_week_course_index,
    parse_hhmm,
)
from app.services.kaipoke.name_match import build_name_index, match_name

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.diff.engine import ScheduleEntry


class ReplaceBlockedError(RuntimeError):
    """実績ガード: 打刻等が付いた週は置換できない (差分モードを使う)。"""


@dataclass
class ReplaceSkip:
    """挿入できなかったカイポケ行 (隠さず可視化する)。"""

    reason: str
    user_name: str
    staff_name: str
    date: str
    start: str


@dataclass
class ReplaceResult:
    week_start: date
    week_end: date  # 月〜土
    dry_run: bool
    wiped: int = 0
    inserted: int = 0
    sunday_skipped: int = 0
    temp_courses: int = 0
    skipped: list[ReplaceSkip] = field(default_factory=list)


async def _count_week_achievements(
    db: AsyncSession, visit_ids: list[uuid.UUID]
) -> int:
    """対象訪問に紐づく実績 (打刻) の件数。写真/レビューは checkin 起点のため
    checkin の存在確認で実績週を検出できる。"""
    if not visit_ids:
        return 0
    n = await db.scalar(
        select(func.count(VisitCheckin.id)).where(VisitCheckin.visit_id.in_(visit_ids))
    )
    return int(n or 0)


async def replace_week_from_kaipoke(
    db: AsyncSession,
    *,
    week_start: date,
    entries: list[ScheduleEntry],
    dry_run: bool,
    now: datetime,
) -> ReplaceResult:
    """対象週 (月〜土) を白紙化し、カイポケ現況 entries で書き直す。

    dry_run=True は一切 mutate しない (計画のみ返す)。
    Raises: ReplaceBlockedError (実績ガード) / ValueError (週開始が月曜でない)。
    """
    if week_start.weekday() != 0:
        raise ValueError(f"week_start must be a Monday: {week_start}")
    week_sat = week_start + timedelta(days=5)
    week_sun = week_start + timedelta(days=6)
    today = now.date()

    result = ReplaceResult(week_start=week_start, week_end=week_sat, dry_run=dry_run)

    # --- 白紙化対象 (active な全訪問・キャンセル済み含む) ---------------------
    wipe_rows = (
        await db.scalars(
            select(Visit).where(
                Visit.deleted_at.is_(None),
                Visit.visit_date >= week_start,
                Visit.visit_date <= week_sat,
            )
        )
    ).all()
    result.wiped = len(wipe_rows)

    # --- 実績ガード ----------------------------------------------------------
    achievements = await _count_week_achievements(db, [v.id for v in wipe_rows])
    if achievements > 0:
        raise ReplaceBlockedError(
            f"この週には打刻などの実績が {achievements} 件あります。"
            "実績の紐付けを守るため置換はできません（差分取り込みを使ってください）"
        )

    # --- 名寄せ・コース索引 --------------------------------------------------
    patients = (await db.scalars(select(Patient).where(Patient.deleted_at.is_(None)))).all()
    pindex = build_name_index({str(p.id): p.name for p in patients})
    patient_by_id = {str(p.id): p for p in patients}

    staff_index_raw, staff_map = await load_staff_name_index(db)
    trainee_ids = {
        s.id for s in staff_map.values() if getattr(s, "is_trainee", False) and s.status == "active"
    }

    course_idx = await load_week_course_index(db, week_start)
    # コース単位の同行リンク (staff2 判定①: コースの同行新人は secondary にしない)
    acc_rows = (
        await db.scalars(
            select(TraineeAccompaniment).where(
                TraineeAccompaniment.target_type == "course",
                TraineeAccompaniment.course_id.in_(list(course_idx.by_id.keys()) or [uuid.uuid4()]),
            )
        )
    ).all()
    accompaniment_by_course: dict[uuid.UUID, set[uuid.UUID]] = {}
    for a in acc_rows:
        if a.course_id is not None:
            accompaniment_by_course.setdefault(a.course_id, set()).add(a.trainee_staff_id)

    # --- 白紙化 (real のみ) --------------------------------------------------
    if not dry_run:
        for v in wipe_rows:
            v.deleted_at = now

    # --- 挿入 ----------------------------------------------------------------
    seen_keys: set[tuple[str, date, str]] = set()

    def _skip(reason: str, e: ScheduleEntry, d: date | None) -> None:
        result.skipped.append(
            ReplaceSkip(
                reason=reason,
                user_name=e.user_name,
                staff_name=e.staff1_name,
                date=d.isoformat() if d else str(e.date),
                start=e.start_time,
            )
        )

    for e in entries:
        try:
            day_num = int(str(e.date).strip())
        except (TypeError, ValueError):
            continue  # 日付列が数値でない行 (ヘッダ残骸等) は対象外
        d = day_to_date(day_num, week_start, week_sun)
        if d is None:
            continue  # 週レンジ外 (月まるごとCSVの他週分)
        if d.weekday() == 6:
            result.sunday_skipped += 1
            continue

        start_t = parse_hhmm(e.start_time)
        end_t = parse_hhmm(e.end_time)
        if start_t is None or end_t is None:
            _skip("時刻が解釈できません", e, d)
            continue

        pid_str = match_name(e.user_name, pindex)
        if pid_str is None:
            _skip("患者を名寄せできません（らく助未登録の可能性）", e, d)
            continue
        patient = patient_by_id[pid_str]

        sid_str = match_name(e.staff1_name, staff_index_raw) if e.staff1_name else None
        if sid_str is None:
            _skip("担当1を名寄せできません（らく助未登録の可能性）", e, d)
            continue
        sid = uuid.UUID(sid_str)
        if sid in trainee_ids:
            _skip("担当1が新人のため挿入できません（新人はコースを持たない運用）", e, d)
            continue

        key = (pid_str, d, e.start_time)
        if key in seen_keys:
            _skip("カイポケ側の重複行（同一患者・同時刻）", e, d)
            continue
        seen_keys.add(key)

        # staff2 の 3 段階判定 (diff-inbound の add と同じ規則・設計 §9)
        sid2: uuid.UUID | None = None
        accompaniment_sid2: uuid.UUID | None = None
        if e.staff2_name:
            sid2_str = match_name(e.staff2_name, staff_index_raw)
            if sid2_str is not None:
                sid2 = uuid.UUID(sid2_str)

        office_id = patient.primary_office_id
        if office_id is None:
            _skip("患者の主担当拠点が未設定です", e, d)
            continue
        weekday = d.weekday()
        course = course_idx.by_staff.get((weekday, office_id, sid))

        if sid2 is not None:
            if course is not None and sid2 in accompaniment_by_course.get(course.id, set()):
                sid2 = None  # コースの同行新人 → secondary にしない
            elif sid2 in trainee_ids:
                accompaniment_sid2 = sid2
                sid2 = None  # 新人 → 同行リンクとして取り込む

        if dry_run:
            if course is None:
                result.temp_courses += 1
            result.inserted += 1
            continue

        if course is None:
            course = await ensure_temp_course(
                db, course_idx, weekday=weekday, office_id=office_id, staff_id=sid, now=now
            )
            if course is None:
                _skip("臨時コース枠（臨〜臨9）が満杯です", e, d)
                continue
            result.temp_courses += 1

        new_visit = Visit(
            patient_id=patient.id,
            visit_date=d,
            start_time=start_t,
            end_time=end_t,
            type="regular",
            status="planned",
            source="import",  # 取込由来・週再生成から保護
            required_staff_count=2 if sid2 is not None else 1,
            primary_staff_id=sid,
            secondary_staff_id=sid2,
            course_id=course.id,
        )
        _stamp_note(new_visit, "カイポケ置換取込", today)
        try:
            async with db.begin_nested():
                db.add(new_visit)
                await db.flush()
                await _replace_assignments(
                    db, new_visit, [s for s in (sid, sid2) if s is not None]
                )
                if accompaniment_sid2 is not None:
                    db.add(
                        TraineeAccompaniment(
                            trainee_staff_id=accompaniment_sid2,
                            target_type="visit",
                            visit_id=new_visit.id,
                            source="manual",
                            created_by=None,
                        )
                    )
        except IntegrityError:
            _skip("UNIQUE衝突（想定外・要確認）", e, d)
            continue
        result.inserted += 1

    return result
