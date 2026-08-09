"""受け入れ枠マトリックス 集計サービス — P1.

拠点 (office) × 曜日 × 時間帯で「受け入れ可能か」を ○ (available) / △ (consult)
/ × (unavailable) に落とす **read-only** な自動算出ロジック。

設計の要点 (ユーザー / Codex と合意済):
  - **容量ベース**の粗い目安。新規患者の住所が未知なため移動時間は見ない。
  - 集計源は **当週の実 Visit**。空コース (0 人) も拾うため **Course 行を起点**にし、
    座標なし visit も占有として計上する (同住所ペア判定のときだけ座標を要求)。
  - 既存 ``find_available_slots_for_candidate`` は候補座標必須でダミー座標が移動/
    同住所ペアを汚染するため **流用しない**。定数・概念のみ再利用する。

純ロジック (``build_course_state`` / ``classify_slot``) は DB 非依存で、
``compute_acceptance_matrix`` が DB ロードと組み立てを担う。本モジュールは
``auto_allocator_v2`` (巨大) を import せず、leaf な ``constants`` / ``config`` と
ORM モデルのみに依存する (循環 import 回避 + 軽量化)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.acceptance_calendar_week import AcceptanceCalendarWeek
from app.models.course import COURSE_STATUS_PROPOSED, Course
from app.models.office import Office, OfficeCity
from app.models.patient import Patient
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.schemas.v2.acceptance import AcceptanceStatus
from app.services.scheduling.config import SchedulingConfig, load_scheduling_config
from app.services.scheduling.constants import (
    COURSE_MAX_MINUTES,
    DEFAULT_OFFICE_OPERATING_WEEKDAYS,
    SAME_ADDRESS_TOLERANCE,
)

# ---------------------------------------------------------------------------
# モジュール定数 (auto_allocator_v2 の対応定数と同値; 巨大モジュール import を避け
# るため本モジュールに併記する).
# ---------------------------------------------------------------------------

# 営業枠の昼休みコア境界. AM 枠終了 = 12:00, PM 枠開始 = 13:00.
# (= auto_allocator_v2.AM_BLOCK_END / PM_BLOCK_START と同値. 12:00-13:00 は常に休み)
_AM_END = time(12, 0)
_PM_START = time(13, 0)

# 同住所 2 名の最低占有 (分). (= auto_allocator_v2.SAME_ADDRESS_PAIR_MIN_OCCUPANCY)
_SAME_ADDRESS_PAIR_MIN_OCCUPANCY = 90

# △ (consult) と判定する部分空きの下限 (分).
_PARTIAL_FIT_MIN = 30

# 既定の時間帯レンジ (開始時刻列). デザイン準拠で 10:00〜17:00 の 1 時間刻み.
DEFAULT_SLOT_START = time(10, 0)
DEFAULT_SLOT_END = time(17, 0)
DEFAULT_SERVICE_MINUTES = 60


def _is_manager_course(code: str | None) -> bool:
    """コードが M系 (M / M2..M9) = マネージャー予備枠かどうか.

    受け入れ枠マトリックスの母集合から除外するために使う (常に空で ○ を汚染するため)。
    'M' 単体と 'M' + 1桁数字のみ対象。'臨' 系 (臨時コース) は含めない。
    """
    if not code:
        return False
    up = code.strip().upper()
    if up == "M":
        return True
    return len(up) == 2 and up[0] == "M" and up[1].isdigit()


# ---------------------------------------------------------------------------
# 時刻 / 区間ヘルパ (純関数)
# ---------------------------------------------------------------------------


def _to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _to_time(total: int) -> time:
    # 24:00 は time(23,59) にクランプ (datetime.time は 0..23:59 のみ表現可能).
    # 営業終了 18:00 までしか扱わないため実害はないが防御的にクランプする.
    total = max(0, min(total, 24 * 60 - 1))
    return time(total // 60, total % 60)


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """2 区間 [a) [b) の重なり分 (>=0)."""
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """昇順マージ (重なり / 隣接を 1 区間に)."""
    out: list[tuple[int, int]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _subtract(base: list[tuple[int, int]], blocks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """base 各区間から blocks を差し引いた残り区間."""
    blocks = _merge(blocks)
    out: list[tuple[int, int]] = []
    for bs, be in base:
        cur = bs
        for ks, ke in blocks:
            if ke <= cur or ks >= be:
                continue
            if ks > cur:
                out.append((cur, min(ks, be)))
            cur = max(cur, ke)
            if cur >= be:
                break
        if cur < be:
            out.append((cur, be))
    return [(s, e) for s, e in out if e > s]


def _same_address(
    a: tuple[float | None, float | None], b: tuple[float | None, float | None]
) -> bool:
    """lat/lng が許容誤差内で同住所か (座標欠損は False)."""
    if a[0] is None or a[1] is None or b[0] is None or b[1] is None:
        return False
    return abs(a[0] - b[0]) <= SAME_ADDRESS_TOLERANCE and abs(a[1] - b[1]) <= SAME_ADDRESS_TOLERANCE


# ---------------------------------------------------------------------------
# 集計データ構造
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatrixVisit:
    """集計入力 (1 訪問の最小フィールド)."""

    start_min: int
    end_min: int
    patient_id: UUID
    lat: float | None = None
    lng: float | None = None


@dataclass(frozen=True)
class CourseState:
    """1 コース分の集計済み状態."""

    free_intervals: list[tuple[int, int]] = field(default_factory=list)
    remaining_count: int = 0
    remaining_minutes: int = 0


def _business_blocks(config: SchedulingConfig) -> list[tuple[int, int]]:
    """営業枠 (AM ∪ PM). 12:00-13:00 の昼休みコアは常に除外済み."""
    am = (_to_min(config.business_start), _to_min(_AM_END))
    pm = (_to_min(_PM_START), _to_min(config.business_end))
    return [b for b in (am, pm) if b[1] > b[0]]


def build_course_state(visits: list[MatrixVisit], *, config: SchedulingConfig) -> CourseState:
    """1 コースの visit リストから空き区間・残人数・残時間を算出する.

    - 占有区間 = visit 区間 (同住所 2 名・同時刻/重複は 90 分占有へ底上げ).
    - 営業枠 (AM∪PM) − 占有 = 空き区間.
    - 残人数 = max_patients − 実患者数 (同一 patient_id は 1 人; 2 名体制 2 行も 1 人).
    - 残時間 = COURSE_MAX_MINUTES − 占有分 (営業枠内に clip).
    """
    blocks = _business_blocks(config)

    # --- 占有区間の構築 (同住所同時刻ペアは 90 分底上げ) ---
    # 注: アンカー (vi) の座標に対して貪欲にグルーピングする簡易実装. 1 コース内で
    # 同時間帯に 3 件以上の別住所が連鎖する稀ケースでは完全な推移閉包にならないが、
    # 同住所ペアは通常 2 名・同時刻のため「粗い目安」の範囲では十分 (完全閉包が要れ
    # ば union-find に置換). 占有区間自体は _merge で重なりを吸収するため過小評価は
    # しない (底上げ漏れの可能性のみ).
    occupied: list[tuple[int, int]] = []
    processed: set[int] = set()
    for i, vi in enumerate(visits):
        if i in processed:
            continue
        processed.add(i)
        block_s, block_e = vi.start_min, vi.end_min
        member_pids = {vi.patient_id}
        for j in range(i + 1, len(visits)):
            if j in processed:
                continue
            vj = visits[j]
            if (
                _same_address((vi.lat, vi.lng), (vj.lat, vj.lng))
                and _overlap((block_s, block_e), (vj.start_min, vj.end_min)) > 0
            ):
                processed.add(j)
                block_s = min(block_s, vj.start_min)
                block_e = max(block_e, vj.end_min)
                member_pids.add(vj.patient_id)
        if len(member_pids) >= 2:
            block_e = max(block_e, block_s + _SAME_ADDRESS_PAIR_MIN_OCCUPANCY)
        if block_e > block_s:
            occupied.append((block_s, block_e))

    occupied = _merge(occupied)
    free_intervals = _subtract(blocks, occupied)

    # 占有分 (営業枠内に clip) → 残時間.
    occupied_in_business = 0
    for blk in blocks:
        for occ in occupied:
            occupied_in_business += _overlap(blk, occ)
    remaining_minutes = max(0, COURSE_MAX_MINUTES - occupied_in_business)

    distinct_patients = len({v.patient_id for v in visits})
    remaining_count = max(0, config.max_patients_per_course - distinct_patients)

    return CourseState(
        free_intervals=free_intervals,
        remaining_count=remaining_count,
        remaining_minutes=remaining_minutes,
    )


@dataclass(frozen=True)
class CellResult:
    status: AcceptanceStatus
    remaining_patients_total: int
    remaining_minutes_total: int
    available_course_count: int
    consult_course_count: int
    max_free_gap_minutes: int
    reasons: list[str]


def classify_slot(
    states: list[CourseState],
    slot_start_min: int,
    *,
    service_min: int = DEFAULT_SERVICE_MINUTES,
) -> CellResult:
    """時間帯 [slot, slot+service) を ○△× に判定し、メトリクスを返す.

    - ○ = いずれかのコースで 残人数>=1 ∧ 残時間>=service ∧ 標準枠が空き区間に完全内包.
    - △ = ○ なし ∧ いずれかのコースで 残人数>=1 ∧ 空き区間との重なり>=30分.
    - × = 上記なし (コース無し / 満床 / 残時間不足 / 枠なし).
    """
    slot = (slot_start_min, slot_start_min + service_min)
    available_course_count = 0
    consult_course_count = 0
    max_free_gap = 0
    remaining_patients_total = 0
    remaining_minutes_total = 0

    for st in states:
        remaining_patients_total += st.remaining_count
        remaining_minutes_total += st.remaining_minutes
        best_overlap = max((_overlap(slot, fi) for fi in st.free_intervals), default=0)
        max_free_gap = max(max_free_gap, best_overlap)
        full_fit = (
            st.remaining_count >= 1
            and st.remaining_minutes >= service_min
            and any(fi[0] <= slot[0] and fi[1] >= slot[1] for fi in st.free_intervals)
        )
        if full_fit:
            available_course_count += 1
        elif (
            st.remaining_count >= 1
            and st.remaining_minutes >= _PARTIAL_FIT_MIN
            and best_overlap >= _PARTIAL_FIT_MIN
        ):
            consult_course_count += 1

    if available_course_count > 0:
        status: AcceptanceStatus = "available"
        reasons = [f"{service_min}分枠あり ({available_course_count}コース)"]
    elif consult_course_count > 0:
        status = "consult"
        reasons = ["部分空きのみ(相談)"]
    else:
        status = "unavailable"
        reasons = ["コースなし"] if not states else ["枠なし"]

    return CellResult(
        status=status,
        remaining_patients_total=remaining_patients_total,
        remaining_minutes_total=remaining_minutes_total,
        available_course_count=available_course_count,
        consult_course_count=consult_course_count,
        max_free_gap_minutes=max_free_gap,
        reasons=reasons,
    )


def build_slot_starts(slot_start: time, slot_end: time) -> list[time]:
    """slot_start..slot_end (含む) を 1 時間刻みで列挙.

    既定 10:00-17:00 には昼休みコアの 12:00 枠も含む (auto は常に × になる). 行を
    抜くとマトリックスの行が欠けて見た目が崩れる + 常設手動上書きで △/○ にする運用も
    あり得るため、敢えて列挙してフロント側で表示判断させる.
    """
    out: list[time] = []
    cur = _to_min(slot_start)
    end = _to_min(slot_end)
    while cur <= end:
        out.append(_to_time(cur))
        cur += 60
    return out


def _coerce_operating_weekdays(raw: object) -> set[int]:
    """``Office.operating_weekdays`` を ``set[int]`` (0..6) に正規化 (不正値は月-土).

    (= auto_allocator_v2._coerce_operating_weekdays と同一の防御ロジック)
    """
    if not isinstance(raw, list) or not raw:
        return set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    out: set[int] = set()
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, int):
            return set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
        if v < 0 or v > 6:
            return set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
        out.add(v)
    if not out:
        return set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    return out


# ---------------------------------------------------------------------------
# DB ロード + 組み立て
# ---------------------------------------------------------------------------


async def compute_acceptance_matrix(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID] | None = None,
    service_minutes: int = DEFAULT_SERVICE_MINUTES,
    slot_start: time = DEFAULT_SLOT_START,
    slot_end: time = DEFAULT_SLOT_END,
) -> dict:
    """受け入れ枠マトリックスを算出して dict (= レスポンス schema 互換) で返す.

    DB 書込なし。``AcceptanceMatrixResponse.model_validate`` でそのまま検証できる
    形を返す。
    """
    config = await load_scheduling_config(db)
    slot_starts = build_slot_starts(slot_start, slot_end)
    slot_start_mins = [_to_min(t) for t in slot_starts]

    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        week_sunday = date.fromisocalendar(iso_year, iso_week, 7)
    except ValueError as exc:
        raise ValueError(f"無効な ISO 週: {iso_year}-W{iso_week}") from exc
    week_upper = week_sunday + timedelta(days=1)

    # --- 拠点ロード (区名 eager) ---
    office_stmt = (
        select(Office)
        .where(Office.deleted_at.is_(None))
        .options(selectinload(Office.cities).selectinload(OfficeCity.city))
        .order_by(Office.code.asc().nulls_last(), Office.created_at.asc())
    )
    if office_ids:
        office_stmt = office_stmt.where(Office.id.in_(office_ids))
    offices = (await db.scalars(office_stmt)).all()
    target_office_ids = [o.id for o in offices]

    if not target_office_ids:
        return {
            "iso_year": iso_year,
            "iso_week": iso_week,
            "service_minutes": service_minutes,
            "slots": slot_starts,
            "week_generated_any": False,
            "offices": [],
        }

    # --- 当週のコース (診断のため proposed も含めて取得し、後で分ける) ---
    all_course_rows = (
        await db.scalars(
            select(Course).where(
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.office_id.in_(target_office_ids),
                Course.deleted_at.is_(None),
            )
        )
    ).all()
    # 設定漏れ診断 (PO 要望 2026-08-10): proposed しか無い拠点 = 自動スタッフ割当が
    # 未実行 (この工程がコース確定 proposed→course_fixed を兼ねる)。
    offices_with_any_courses: set[UUID] = {
        c.office_id for c in all_course_rows if not _is_manager_course(c.code)
    }
    # マトリックスの母集合は従来どおり proposed 除外。
    course_rows = [c for c in all_course_rows if c.course_status != COURSE_STATUS_PROPOSED]
    # M系コース (M/M2..M9) はマネージャー予備枠で常に空のため、受け入れ枠の ○△× 対象
    # から除外する (PO決定 2026-07-10「Mは受け入れ先になり得ない」)。除外しないと A-D が
    # 満床でも空の M が「6名受けられる」と誤カウントし、満床の枠まで ○ になっていた。
    # 臨時コース (臨..) は実訪問を持つ稼働コースなので除外しない。
    course_rows = [c for c in course_rows if not _is_manager_course(c.code)]
    # 業務ロール「マネージャー」在籍拠点 (週生成のコース作成前提・診断用)。
    from app.models.staff import Staff

    manager_office_ids: set[UUID] = set(
        (
            await db.scalars(
                select(Staff.primary_office_id).where(
                    Staff.role == "manager",
                    Staff.status == "active",
                    Staff.deleted_at.is_(None),
                    Staff.primary_office_id.in_(target_office_ids),
                )
            )
        ).all()
    )

    courses_by_day: dict[tuple[UUID, int], list[Course]] = {}
    course_id_to_key: dict[UUID, tuple[UUID, int]] = {}
    offices_with_courses: set[UUID] = set()
    for c in course_rows:
        key = (c.office_id, c.weekday)
        courses_by_day.setdefault(key, []).append(c)
        course_id_to_key[c.id] = key
        offices_with_courses.add(c.office_id)

    # --- 当週 planned visit (確定コースに属するもの) ---
    visits_by_course: dict[UUID, list[Visit]] = {}
    if course_id_to_key:
        visit_rows = (
            await db.scalars(
                select(Visit).where(
                    Visit.deleted_at.is_(None),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.visit_date >= week_monday,
                    Visit.visit_date < week_upper,
                    Visit.course_id.in_(list(course_id_to_key.keys())),
                )
            )
        ).all()
        # 患者座標ロード (同住所判定用; 欠損でも占有計上する).
        patient_ids = {v.patient_id for v in visit_rows}
        coords: dict[UUID, tuple[float | None, float | None]] = {}
        if patient_ids:
            prows = (await db.scalars(select(Patient).where(Patient.id.in_(patient_ids)))).all()
            for p in prows:
                coords[p.id] = (
                    float(p.lat) if p.lat is not None else None,
                    float(p.lng) if p.lng is not None else None,
                )
        for v in visit_rows:
            if v.course_id is None:
                continue
            visits_by_course.setdefault(v.course_id, []).append(v)
        # 後で MatrixVisit を組むため、座標も保持しておく.
        visit_coords = coords
    else:
        visit_coords = {}

    # --- 常設手動上書き (acceptance_calendar). 値は (status, note). ---
    manual_map: dict[tuple[UUID, int, time], tuple[AcceptanceStatus, str | None]] = {}
    ac_rows = (
        await db.scalars(
            select(AcceptanceCalendar).where(AcceptanceCalendar.office_id.in_(target_office_ids))
        )
    ).all()
    for ac in ac_rows:
        manual_map[(ac.office_id, ac.weekday, ac.time_slot)] = (
            cast(AcceptanceStatus, ac.status),
            ac.notes,
        )

    # --- 週別手動上書き (acceptance_calendar_week, 当週のみ). 値は (status, note). ---
    week_map: dict[tuple[UUID, int, time], tuple[AcceptanceStatus, str | None]] = {}
    acw_rows = (
        await db.scalars(
            select(AcceptanceCalendarWeek).where(
                AcceptanceCalendarWeek.office_id.in_(target_office_ids),
                AcceptanceCalendarWeek.iso_year == iso_year,
                AcceptanceCalendarWeek.iso_week == iso_week,
            )
        )
    ).all()
    for acw in acw_rows:
        week_map[(acw.office_id, acw.weekday, acw.time_slot)] = (
            cast(AcceptanceStatus, acw.status),
            acw.notes,
        )

    # --- 組み立て ---
    offices_out: list[dict] = []
    for office in offices:
        op_weekdays = _coerce_operating_weekdays(office.operating_weekdays)
        city_names = [oc.city.name for oc in office.cities if oc.city is not None]
        office_has_courses = office.id in offices_with_courses

        days_out: list[dict] = []
        for weekday in range(7):
            day_date = (week_monday + timedelta(days=weekday)).isoformat()
            office_closed = weekday not in op_weekdays

            # コース状態を構築 (定休 or コース無しなら空).
            states: list[CourseState] = []
            if not office_closed:
                for course in courses_by_day.get((office.id, weekday), []):
                    mvs = [
                        MatrixVisit(
                            start_min=_to_min(v.start_time),
                            end_min=_to_min(v.end_time),
                            patient_id=v.patient_id,
                            lat=visit_coords.get(v.patient_id, (None, None))[0],
                            lng=visit_coords.get(v.patient_id, (None, None))[1],
                        )
                        for v in visits_by_course.get(course.id, [])
                        if _to_min(v.end_time) > _to_min(v.start_time)
                    ]
                    states.append(build_course_state(mvs, config=config))

            cells_out: list[dict] = []
            for slot_t, slot_min in zip(slot_starts, slot_start_mins, strict=True):
                if office_closed:
                    auto_status: AcceptanceStatus = "unavailable"
                    metrics = {
                        "remaining_patients_total": 0,
                        "remaining_minutes_total": 0,
                        "available_course_count": 0,
                        "consult_course_count": 0,
                        "max_free_gap_minutes": 0,
                        "reasons": ["定休日"],
                    }
                else:
                    res = classify_slot(states, slot_min, service_min=service_minutes)
                    auto_status = res.status
                    reasons = res.reasons
                    if not office_has_courses:
                        reasons = ["週未生成"]
                    metrics = {
                        "remaining_patients_total": res.remaining_patients_total,
                        "remaining_minutes_total": res.remaining_minutes_total,
                        "available_course_count": res.available_course_count,
                        "consult_course_count": res.consult_course_count,
                        "max_free_gap_minutes": res.max_free_gap_minutes,
                        "reasons": reasons,
                    }

                manual_entry = manual_map.get((office.id, weekday, slot_t))
                week_entry = week_map.get((office.id, weekday, slot_t))
                manual_status = manual_entry[0] if manual_entry is not None else None
                week_status = week_entry[0] if week_entry is not None else None
                # 3 層: 週別 > 常設 > 自動. note は勝った層のコメント.
                if week_entry is not None:
                    effective = week_entry[0]
                    source = "week_override"
                    note = week_entry[1]
                elif manual_entry is not None:
                    effective = manual_entry[0]
                    source = "manual_standing"
                    note = manual_entry[1]
                else:
                    effective = auto_status
                    source = "auto"
                    note = None
                cells_out.append(
                    {
                        "time_slot": slot_t,
                        "auto_status": auto_status,
                        "manual_status": manual_status,
                        "week_status": week_status,
                        "effective_status": effective,
                        "source": source,
                        "note": note,
                        "metrics": metrics,
                    }
                )

            days_out.append(
                {
                    "weekday": weekday,
                    "date": day_date,
                    "office_closed": office_closed,
                    "cells": cells_out,
                }
            )

        # 設定漏れ診断 (優先順: マネージャー不在 > 週未生成 > 割当未実行)。
        if office_has_courses:
            setup_state = None
        elif office.id not in manager_office_ids:
            setup_state = "no_manager"
        elif office.id in offices_with_any_courses:
            setup_state = "assignment_pending"
        else:
            setup_state = "not_generated"
        offices_out.append(
            {
                "office_id": office.id,
                "office_name": office.name,
                "city_names": city_names,
                "operating_weekdays": sorted(op_weekdays),
                "week_generated": office_has_courses,
                "setup_state": setup_state,
                "days": days_out,
            }
        )

    return {
        "iso_year": iso_year,
        "iso_week": iso_week,
        "service_minutes": service_minutes,
        "slots": slot_starts,
        "week_generated_any": bool(offices_with_courses),
        "offices": offices_out,
    }
