"""AutoAllocator — Wave 41 v1.0 (auto-schedule MVP).

設計仕様書: ``docs/plans/auto-schedule-v1.md`` (v0.4)
実装計画書: ``docs/plans/auto-schedule-implementation-plan.md`` (v0.2) §4 Phase 1a/1b/1c

責務 (v1.0 / Mode 1 / ハード制約 + 基本 KPI):
    - Phase 1: 固定枠集約 (patient_fixed_visits を当該週マップへ展開)
    - Phase 2 (Mode 1): プール患者 (active && fixed_visits 行なし) を空きに差し込み
    - Phase 3: K-Means でコース分け (layer2_clustering の ``run_kmeans`` を再利用)
              * (office, weekday) ごとに K = staff 数 でクラスタリング
              * 後処理で H2 (同住所ペアリング) と容量上限 6 を enforce
    - Phase 4: スタッフ割当 (layer3_assignment の ``Layer3Assigner.solve`` を再利用)
              * 後処理で H4 (全訪問同スタッフ禁止) を enforce
    - 永続化: Course + Visit + VisitStaffAssignment を一括 INSERT
    - Phase 5: 訪問順は希望時刻ソート (TSP は v1.2)
    - ハード制約 H1〜H8 を関数化して 1 箇所に集約
    - 基本 KPI (距離・スタッフ負荷標準偏差・H 違反数・改善スコア) を算出

W41 レビュー対応 (Critical C1/C2, High H1〜H5, Medium M1〜M4):
    - C1: H2/H3/H4 を検出だけでなく enforce する後処理を追加
    - C2: visit_staff_assignments を新規 INSERT し Course.assigned_staff_id を更新
    - H1: 永続化を Phase 3→4→persist の順に整理し flush を最小化
    - H2/H3/H4/H5 (review): private API 解消 / per (office, weekday) クラスタ /
      容量制約 enforce
    - M1: Course.decision_log を JSON で populate
    - M2: patient query を 1 回に削減
    - M3: course code "E" 以降の衝突対策 (staff > 5 → RuntimeError + warning)
    - M4: source="auto_alloc" に変更し Layer 1 ("auto") と区別

スコープ外 (v1.0):
    - Mode 2 (全面最適化) は NotImplementedError
    - 新人同行 (Pattern A) は is_trainee=false のみで割当
    - TSP (P5) / AI 要約 / Justification ポップアップ
    - acceptance_calendar の △ ペナルティ (v1.1 で重み調整)

トランザクション境界:
    本サービスは ``db.commit()`` / ``db.rollback()`` を **呼ばない**。
    呼び出し側 (API 層) が 1 HTTP リクエストを 1 トランザクションで包み、
    例外時に rollback する契約。内部では ``await db.flush()`` のみ。

出力 (戻り値 dict):
    - ``proposal_batch_id`` (UUID): 当該実行を識別するバッチ ID
    - ``proposals`` (list[dict]): 各 proposed course のサマリ
      ({course_id, office_id, weekday, code, patient_ids, total_distance_km})
    - ``kpi`` (dict): 基本 KPI (Phase 1c)
    - ``warnings`` (list[str]): 軽微な問題 (例: 性別制約で未割当の visit)
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_PROPOSED,
    COURSE_STATUS_STAFF_ASSIGNED,
    Course,
)
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.scheduling.constants import (
    MAX_PATIENTS_PER_COURSE,
    SAME_ADDRESS_TOLERANCE,
)
from app.services.scheduling.layer2_clustering import (
    haversine_km,
    run_kmeans,
)
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
    VisitTimeSlot,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — evaluation weights (auto-schedule-v1 §7 O4)
# ---------------------------------------------------------------------------

WEIGHT_DISTANCE: float = 1.0
WEIGHT_LOAD: float = 2.0
WEIGHT_ROTATION: float = 3.0
WEIGHT_SOFT_VIOLATIONS: float = 1.0

# H2 同住所判定の許容誤差 (緯度経度の絶対差).
# 0.001 deg ≒ 約 100 m。仕様: "ほぼ一致 (誤差 ≤ 0.001)".
# Phase G-88: 正準値は ``constants`` に単一ソース化 (上部 import 済み). 値 = 0.001.

# H2: 同住所同時刻に配置できる最大人数.
SAME_ADDRESS_MAX: int = 2

# H5 (review): 1 コースあたりの上限人数 (Layer 2 の §3.6.3 と整合).
# Phase G-88: 正準値は ``constants`` に単一ソース化 (上部 import 済み). 値 = 6.
# 既存の再エクスポート名 ``MAX_PATIENTS_PER_COURSE`` (``__all__`` 経由) は維持される.

# auto_allocator が新規生成する visit の source 値.
# M4: Layer 1 の "auto" と区別するため "auto_alloc" を採用.
AUTO_ALLOC_VISIT_SOURCE: str = "auto_alloc"

# auto_allocator が生成する visit の type / status.
AUTO_ALLOC_VISIT_TYPE: str = "regular"
AUTO_ALLOC_VISIT_STATUS: str = VISIT_STATUS_PLANNED


# ---------------------------------------------------------------------------
# Dataclasses (in-memory representation of proposed visits)
# ---------------------------------------------------------------------------


@dataclass
class ProposedVisit:
    """1 件の提案 visit (DB INSERT 前の中間状態).

    Phase 1〜2 で確定する: patient_id / weekday / start_time / end_time / lat / lng.
    Phase 3 で確定する: course_index (cluster id) → 後に course_id.
    Phase 4 で確定する: assigned_staff_id.
    """

    patient_id: UUID
    weekday: int  # 0=Mon..6=Sun
    start_time: time
    end_time: time
    service_minutes: int
    lat: float
    lng: float
    source_kind: str  # "fixed" (patient_fixed_visits 由来) / "pool" (差し込み)
    office_id: UUID
    course_template_id: UUID | None = None
    # Phase 3 で割当てるクラスタ index (0..K-1). 同 office × weekday 内で完結.
    course_index: int | None = None
    # Phase 4 後に判明
    assigned_staff_id: UUID | None = None
    # Phase 後の永続化用 (DB INSERT 後に確定)
    visit_id: UUID | None = None
    course_id: UUID | None = None


# ---------------------------------------------------------------------------
# Hard constraints (H1-H8)
# ---------------------------------------------------------------------------


def _address_bucket(lat: float, lng: float) -> tuple[float, float]:
    """H2/H3 で住所同一性を判定するためのバケットキー (lat/lng を tolerance で量子化)."""
    lat_b = round(lat / SAME_ADDRESS_TOLERANCE) * SAME_ADDRESS_TOLERANCE
    lng_b = round(lng / SAME_ADDRESS_TOLERANCE) * SAME_ADDRESS_TOLERANCE
    return (lat_b, lng_b)


def _is_same_address(
    a_lat: float, a_lng: float, b_lat: float, b_lng: float, *, tol: float = SAME_ADDRESS_TOLERANCE
) -> bool:
    """H2/H3: lat/lng がほぼ一致する (同住所) か判定."""
    return abs(a_lat - b_lat) <= tol and abs(a_lng - b_lng) <= tol


def _check_h1_weekly_unified(visits: list[ProposedVisit]) -> int:
    """H1: 同患者の visit 時刻は週通して同じ.

    違反件数 (= 同一 patient_id が 2 つ以上の start_time を持つケース) を返す.
    """
    by_patient: dict[UUID, set[time]] = {}
    for v in visits:
        by_patient.setdefault(v.patient_id, set()).add(v.start_time)
    return sum(1 for times in by_patient.values() if len(times) > 1)


def _check_h2_same_address_pairing(visits: list[ProposedVisit]) -> int:
    """H2: 同住所患者は同 timeslot 配置で最大 2 人.

    違反件数 (= 同住所同曜日同時刻に 3 人以上配置されたグループ数) を返す.
    """
    groups: dict[tuple[int, time, float, float], int] = {}
    for v in visits:
        lat_b, lng_b = _address_bucket(v.lat, v.lng)
        key = (v.weekday, v.start_time, lat_b, lng_b)
        groups[key] = groups.get(key, 0) + 1
    return sum(1 for cnt in groups.values() if cnt > SAME_ADDRESS_MAX)


def _check_h3_same_address_contiguity(visits: list[ProposedVisit]) -> int:
    """H3: course 内ルートで同住所 visit を連続配置.

    v1.0 簡易判定: 同 course × 同 weekday × 同住所の visit は同 start_time
    (= 同 timeslot にペアリング) なら "連続" とみなす. start_time が異なる場合のみ違反.
    (TSP 連動は v1.2 で実装)
    """
    by_group: dict[tuple[int, int, float, float], set[time]] = {}
    for v in visits:
        if v.course_index is None:
            continue
        lat_b, lng_b = _address_bucket(v.lat, v.lng)
        key = (v.course_index, v.weekday, lat_b, lng_b)
        by_group.setdefault(key, set()).add(v.start_time)
    return sum(1 for times in by_group.values() if len(times) > 1)


def _check_h4_all_visits_same_staff(visits: list[ProposedVisit]) -> int:
    """H4: 全訪問が同スタッフは禁止 (週訪問 2 回以上の患者).

    違反件数 (= 週 2 回以上訪問なのに distinct(staff_id) == 1 の患者) を返す.
    """
    by_patient: dict[UUID, list[UUID | None]] = {}
    for v in visits:
        by_patient.setdefault(v.patient_id, []).append(v.assigned_staff_id)
    violations = 0
    for staff_ids in by_patient.values():
        if len(staff_ids) < 2:
            continue
        non_null = [s for s in staff_ids if s is not None]
        if len(non_null) >= 2 and len(set(non_null)) == 1:
            violations += 1
    return violations


def _filter_h5_acceptance_calendar(
    candidates: list[time],
    *,
    office_id: UUID,
    weekday: int,
    unavailable_slots: dict[tuple[UUID, int], set[time]],
) -> list[time]:
    """H5: acceptance_calendar の × (unavailable) 時刻を候補から除外."""
    blocked = unavailable_slots.get((office_id, weekday), set())
    return [t for t in candidates if t not in blocked]


# H6 (staff_shifts + overrides) と H7 (sex_restriction) は Layer 3 既存実装に委任.
# H8 (新人単独訪問禁止) は v1.0 では新人を割り当てない (is_trainee=false のみ) ため
# 自動的に satisfy される.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WEEKDAY_CODE_TO_INT: dict[str, int] = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}

# Course.code の CHECK 制約 ('A','B','C','D','E','M') に従う.
# M3: 5 を超える staff_count はサポート外 (Course CHECK 制約上 E までしか入らない).
_COURSE_CODES_AVAILABLE: tuple[str, ...] = ("A", "B", "C", "D", "E")
_COURSE_CODES_MAX: int = len(_COURSE_CODES_AVAILABLE)


def _resolve_weekday(value: Any) -> int | None:
    """JSONB 上の weekday 値を 0-6 の int に正規化 (Layer 1 と整合)."""
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    if isinstance(value, str):
        return _WEEKDAY_CODE_TO_INT.get(value)
    return None


def _parse_hhmm(value: Any) -> time | None:
    """``HH:MM`` 文字列を ``time`` に変換 (失敗時は None)."""
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return time(h, m)


def _add_minutes(t: time, minutes: int) -> time:
    """``time`` に分を加算 (24:00 を超える場合は 23:59 にクランプ).

    Low fix: clamp 発生時に WARNING ログを 1 回出力する (1 日 0:00 を跨ぐ予約は本来発生しない).
    """
    total = t.hour * 60 + t.minute + minutes
    if total >= 24 * 60:
        logger.warning(
            "auto_allocator: end_time clamped to 23:59 (start=%s, minutes=%d)", t, minutes
        )
        return time(23, 59)
    if total < 0:
        return time(0, 0)
    return time(total // 60, total % 60)


def _course_code_for_index(idx: int, *, warnings: list[str] | None = None) -> str:
    """クラスタ index を course code (A/B/C/D/E) に変換.

    M3: 5 (= len(_COURSE_CODES_AVAILABLE)) を超える index は CHECK 制約に違反するため
    RuntimeError を送出. 呼び出し側で staff_count を _COURSE_CODES_MAX 以下に制限する.
    """
    if 0 <= idx < _COURSE_CODES_MAX:
        return _COURSE_CODES_AVAILABLE[idx]
    msg = (
        f"course code index {idx} exceeds maximum {_COURSE_CODES_MAX - 1}"
        f" (only {_COURSE_CODES_AVAILABLE} are CHECK-allowed)"
    )
    if warnings is not None:
        warnings.append(msg)
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Patient loader (M2: 一括ロードして使い回す)
# ---------------------------------------------------------------------------


async def _load_patients_by_offices(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
) -> dict[UUID, Patient]:
    """対象拠点の active 患者を 1 度だけロードし dict で返す.

    M2: Phase 1/2/4 (sex_restriction 用) で同じ患者集合を query するのを 1 回に削減.
    """
    if not office_ids:
        return {}
    rows = await db.scalars(
        select(Patient).where(
            Patient.status == "active",
            Patient.deleted_at.is_(None),
            Patient.primary_office_id.in_(office_ids),
        )
    )
    return {p.id: p for p in rows.all()}


# ---------------------------------------------------------------------------
# Phase 1: 固定枠集約
# ---------------------------------------------------------------------------


async def _collect_fixed_visits(
    db: AsyncSession,
    *,
    patients_by_id: dict[UUID, Patient],
) -> tuple[list[ProposedVisit], set[UUID]]:
    """Phase 1: ``patient_fixed_visits`` (mode='normal') を ProposedVisit に展開.

    Returns:
        (visits, patients_with_fixed):
            visits — 固定枠由来の ProposedVisit リスト
            patients_with_fixed — 固定枠を持つ patient_id 集合 (プール抽出で使用)
    """
    if not patients_by_id:
        return [], set()

    pfv_rows = await db.scalars(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id.in_(list(patients_by_id.keys())),
            PatientFixedVisit.mode == "normal",
            PatientFixedVisit.slot_index == 0,
        )
    )
    pfv_list = list(pfv_rows.all())

    visits: list[ProposedVisit] = []
    patients_with_fixed: set[UUID] = set()

    for pfv in pfv_list:
        patient = patients_by_id.get(pfv.patient_id)
        if patient is None or patient.lat is None or patient.lng is None:
            continue
        if patient.primary_office_id is None:
            continue
        patients_with_fixed.add(patient.id)
        end_t = _add_minutes(pfv.start_time, pfv.duration_min)
        if end_t <= pfv.start_time:
            continue
        visits.append(
            ProposedVisit(
                patient_id=patient.id,
                weekday=pfv.weekday,
                start_time=pfv.start_time,
                end_time=end_t,
                service_minutes=pfv.duration_min,
                lat=float(patient.lat),
                lng=float(patient.lng),
                source_kind="fixed",
                office_id=patient.primary_office_id,
                course_template_id=pfv.course_template_id,
            )
        )
    return visits, patients_with_fixed


# ---------------------------------------------------------------------------
# Mode 2 KPI before: 既存 active visits ロード (H3 cross-review)
# ---------------------------------------------------------------------------


async def _build_before_visits_from_existing(
    db: AsyncSession,
    *,
    patients_by_id: dict[UUID, Patient],
    week_monday: date,
) -> list[ProposedVisit]:
    """Mode 2 専用: 対象週内の既存 active visits を ``ProposedVisit`` 配列に変換する.

    H3 (W41 v1.0 cross-review): Mode 2 の before は PFV 由来ではなく、当該週の
    ``visits`` (``deleted_at IS NULL``) を実データとして読み出して構築する.

    * 患者 (active かつ対象 office) ごとに対象週 (月〜日) の visits を取得
    * 各 visit を ``ProposedVisit`` (source_kind="existing") として返す
    * 紐付く ``VisitStaffAssignment`` から ``assigned_staff_id`` を埋める
      (staff_load_stddev の before 算出用)
    * 既存 visits が無い患者は単に before に存在しないだけ (KPI 計算では skip).
    """
    if not patients_by_id:
        return []

    week_sunday = date.fromordinal(week_monday.toordinal() + 6)
    patient_ids = list(patients_by_id.keys())
    rows = await db.scalars(
        select(Visit).where(
            Visit.patient_id.in_(patient_ids),
            Visit.deleted_at.is_(None),
            Visit.visit_date >= week_monday,
            Visit.visit_date <= week_sunday,
        )
    )
    existing = list(rows.all())
    if not existing:
        return []

    # staff 割当を取得 (KPI staff_load_stddev_before 用)
    vsa_rows = await db.scalars(
        select(VisitStaffAssignment).where(
            VisitStaffAssignment.visit_id.in_([v.id for v in existing])
        )
    )
    staff_by_visit_id: dict[UUID, UUID] = {a.visit_id: a.staff_id for a in vsa_rows.all()}

    out: list[ProposedVisit] = []
    for v in existing:
        patient = patients_by_id.get(v.patient_id)
        if patient is None or patient.lat is None or patient.lng is None:
            continue
        if patient.primary_office_id is None:
            continue
        weekday = v.visit_date.weekday()
        # duration を end_time - start_time から算出 (PFV 換算)
        start_min = v.start_time.hour * 60 + v.start_time.minute
        end_min = v.end_time.hour * 60 + v.end_time.minute
        duration_min = max(1, end_min - start_min)
        out.append(
            ProposedVisit(
                patient_id=v.patient_id,
                weekday=weekday,
                start_time=v.start_time,
                end_time=v.end_time,
                service_minutes=duration_min,
                lat=float(patient.lat),
                lng=float(patient.lng),
                source_kind="existing",
                office_id=patient.primary_office_id,
                assigned_staff_id=staff_by_visit_id.get(v.id),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Phase 2: プール患者の差し込み (Mode 1)
# ---------------------------------------------------------------------------


async def _load_unavailable_slots(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
) -> dict[tuple[UUID, int], set[time]]:
    """H5 用: acceptance_calendar から × (unavailable) 時刻をロード."""
    if not office_ids:
        return {}
    rows = await db.scalars(
        select(AcceptanceCalendar).where(
            AcceptanceCalendar.office_id.in_(office_ids),
            AcceptanceCalendar.status == "unavailable",
        )
    )
    result: dict[tuple[UUID, int], set[time]] = {}
    for row in rows.all():
        key = (row.office_id, row.weekday)
        result.setdefault(key, set()).add(row.time_slot)
    return result


def _extract_pool_preferences(patient: Patient) -> list[tuple[int, time, int]]:
    """プール患者の ``weekly_pattern`` から (weekday, start_time, service_minutes) を取り出す.

    NOTE (W41 v1.0 cross-review verifier 指摘 / H2): 現状は全 weekday に対し
    最初の entry の ``preferred_start`` を流用しているため、weekday ごとに異なる
    希望時刻 (例: 月=09:00, 火=14:00) を尊重できていない. 修正は v1.1 で対応する
    (個別 entry から (weekday, start, minutes) を抽出するロジックに置換).
    """
    pattern = patient.weekly_pattern
    if not isinstance(pattern, dict):
        return []
    entries = pattern.get("entries")
    if not isinstance(entries, list):
        return []

    base_start: time | None = None
    base_service: int = 30
    weekdays: list[int] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        wd = _resolve_weekday(entry.get("weekday"))
        if wd is None:
            continue
        st = _parse_hhmm(entry.get("preferred_start"))
        if st is None:
            continue
        if base_start is None:
            base_start = st
            sm = entry.get("service_minutes")
            if isinstance(sm, int) and 0 < sm <= 480:
                base_service = sm
        weekdays.append(wd)

    if base_start is None or not weekdays:
        return []
    weekdays = sorted(set(weekdays))
    return [(wd, base_start, base_service) for wd in weekdays]


def _insert_pool_patients_mode1(
    *,
    patients_by_id: dict[UUID, Patient],
    patients_with_fixed: set[UUID],
    unavailable_slots: dict[tuple[UUID, int], set[time]],
    warnings: list[str],
) -> list[ProposedVisit]:
    """Phase 2 (Mode 1): プール患者を空き時間に挿入する.

    プール条件 (auto-schedule-v1 §2): ``status='active' AND patient_fixed_visits 行無し``.
    既存固定枠は一切動かさず、H1 (週次統一) と H5 (受入×) を満たす形で新規 visit
    候補を追加する.

    M2: 患者を再 query せず ``patients_by_id`` をそのまま使う.
    """
    pool = [
        p
        for p in patients_by_id.values()
        if p.id not in patients_with_fixed
        and p.primary_office_id is not None
        and p.lat is not None
        and p.lng is not None
    ]

    new_visits: list[ProposedVisit] = []
    for patient in pool:
        prefs = _extract_pool_preferences(patient)
        if not prefs:
            warnings.append(
                f"pool patient {patient.code} (id={patient.id}) has no usable weekly_pattern"
                " entries; skipped"
            )
            continue
        office_id = patient.primary_office_id
        assert office_id is not None  # type narrow
        for wd, st, sm in prefs:
            filtered = _filter_h5_acceptance_calendar(
                [st],
                office_id=office_id,
                weekday=wd,
                unavailable_slots=unavailable_slots,
            )
            if not filtered:
                warnings.append(
                    f"pool patient {patient.code} weekday={wd} start={st.strftime('%H:%M')}"
                    " blocked by acceptance_calendar (unavailable)"
                )
                continue
            end_t = _add_minutes(st, sm)
            if end_t <= st:
                continue
            new_visits.append(
                ProposedVisit(
                    patient_id=patient.id,
                    weekday=wd,
                    start_time=st,
                    end_time=end_t,
                    service_minutes=sm,
                    lat=float(patient.lat),
                    lng=float(patient.lng),
                    source_kind="pool",
                    office_id=office_id,
                )
            )
    return new_visits


# ---------------------------------------------------------------------------
# Phase 2 (Mode 2): 全面最適化 — 全 active 患者からスロット生成
# ---------------------------------------------------------------------------


async def _build_fixed_visit_fallback_prefs(
    db: AsyncSession,
    *,
    patient_ids: list[UUID],
) -> dict[UUID, list[tuple[int, time, int]]]:
    """``patient_fixed_visits`` (mode='normal', slot_index=0) を
    (weekday, start_time, duration_min) のリストに変換した dict.

    Mode 2 で ``weekly_pattern`` が空の患者向けに使うフォールバック.
    """
    if not patient_ids:
        return {}
    rows = await db.scalars(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id.in_(patient_ids),
            PatientFixedVisit.mode == "normal",
            PatientFixedVisit.slot_index == 0,
        )
    )
    by_patient: dict[UUID, list[tuple[int, time, int]]] = {}
    for pfv in rows.all():
        by_patient.setdefault(pfv.patient_id, []).append(
            (pfv.weekday, pfv.start_time, pfv.duration_min)
        )
    return by_patient


def _insert_all_patients_mode2(
    *,
    patients_by_id: dict[UUID, Patient],
    fixed_fallback_prefs: dict[UUID, list[tuple[int, time, int]]],
    unavailable_slots: dict[tuple[UUID, int], set[time]],
    warnings: list[str],
) -> list[ProposedVisit]:
    """Phase 2 (Mode 2): 全 active 患者からスロット候補を生成する.

    Mode 2 (auto-schedule-v1 §3 Phase 2 / モード 2 全面最適化):
      * 入力: 全 active 患者 (固定枠の有無に依らない)
      * ``weekly_pattern`` から候補スロットを生成
      * 既存 ``patient_fixed_visits`` は **参考バッファ** として
        ``weekly_pattern`` が空の場合のフォールバックに使う
      * 既存固定枠は提案結果に含まれない (採用時に置換される)
      * 出力: 全患者の希望スロットを ProposedVisit (source_kind='pool') として返す

    Mode 1 との違い:
      * patients_with_fixed を除外しない (= 全 active 患者を対象)
      * weekly_pattern が空でも PFV から fallback で候補生成

    H2 (W41 v1.0 cross-review): 旧実装は lat/lng/office_id が欠損している患者を
    silent skip していたため、ユーザーが「Mode 2 = 全 active 対象」のはずなのに
    座標未設定の患者が結果に現れない問題があった. 現実装は skip を warnings に
    記録する.
    """
    new_visits: list[ProposedVisit] = []
    targets: list[Patient] = []
    for p in patients_by_id.values():
        # H2 (W41 v1.0 cross-review): silent skip ではなく warnings に追加.
        if p.primary_office_id is None:
            warnings.append(f"mode_2 patient {p.code} (id={p.id}) は拠点未設定のためスキップ")
            continue
        if p.lat is None or p.lng is None:
            warnings.append(f"mode_2 patient {p.code} (id={p.id}) は座標未設定のためスキップ")
            continue
        targets.append(p)

    for patient in targets:
        prefs = _extract_pool_preferences(patient)
        if not prefs:
            # フォールバック: PFV から (weekday, start, duration) を取り出す
            fb = fixed_fallback_prefs.get(patient.id)
            if fb:
                prefs = fb
                logger.debug(
                    "auto_allocate mode_2: patient %s using PFV fallback (%d entries)",
                    patient.code,
                    len(fb),
                )
            else:
                warnings.append(
                    f"mode_2 patient {patient.code} (id={patient.id}) has no weekly_pattern"
                    " entries and no patient_fixed_visits; skipped"
                )
                continue
        office_id = patient.primary_office_id
        assert office_id is not None  # type narrow
        for wd, st, sm in prefs:
            filtered = _filter_h5_acceptance_calendar(
                [st],
                office_id=office_id,
                weekday=wd,
                unavailable_slots=unavailable_slots,
            )
            if not filtered:
                warnings.append(
                    f"mode_2 patient {patient.code} weekday={wd} start={st.strftime('%H:%M')}"
                    " blocked by acceptance_calendar (unavailable)"
                )
                continue
            end_t = _add_minutes(st, sm)
            if end_t <= st:
                continue
            new_visits.append(
                ProposedVisit(
                    patient_id=patient.id,
                    weekday=wd,
                    start_time=st,
                    end_time=end_t,
                    service_minutes=sm,
                    lat=float(patient.lat),
                    lng=float(patient.lng),
                    source_kind="pool",
                    office_id=office_id,
                )
            )
    return new_visits


# ---------------------------------------------------------------------------
# Phase 3: コース割当 (K-Means, per (office, weekday))
# ---------------------------------------------------------------------------


async def _count_staff_per_office(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
    warnings: list[str],
) -> dict[UUID, int]:
    """拠点ごとの is_trainee=false スタッフ数を返す (= コース数 K).

    M3: K が ``_COURSE_CODES_MAX`` (= 5) を超える場合は警告 + クランプ.
    """
    rows = await db.scalars(
        select(Staff).where(
            Staff.status == "active",
            Staff.deleted_at.is_(None),
            Staff.role == "staff",
            Staff.is_trainee.is_(False),
            Staff.primary_office_id.in_(office_ids),
        )
    )
    counter: Counter[UUID] = Counter()
    for s in rows.all():
        if s.primary_office_id is not None:
            counter[s.primary_office_id] += 1
    result: dict[UUID, int] = {}
    for office_id, k in counter.items():
        if k > _COURSE_CODES_MAX:
            warnings.append(
                f"office {office_id}: staff_count={k} exceeds course_code max"
                f" ({_COURSE_CODES_MAX}); clamped to {_COURSE_CODES_MAX}"
            )
            result[office_id] = _COURSE_CODES_MAX
        else:
            result[office_id] = k
    return result


def _enforce_h2_same_address_pairing(
    visits_in_slot: list[ProposedVisit],
    warnings: list[str],
) -> None:
    """C1 / H2 enforce: 同 (office, weekday) 内で同住所の visits を同 course_index に揃える.

    手順:
      1. (address_bucket) ごとに同住所グループを構築
      2. グループ内の visits の course_index を「最も多く出現した index」に統一
      3. 3 人以上の場合は警告のみ (timeslot 配置の調整は v1.1+ で別途)

    ``visits_in_slot`` は **同一 (office_id, weekday)** に絞られた visits リスト
    (course_index は既に設定済み). 関数は in-place で書き換える.
    """
    if not visits_in_slot:
        return
    by_addr: dict[tuple[float, float], list[ProposedVisit]] = {}
    for v in visits_in_slot:
        if v.course_index is None:
            continue
        by_addr.setdefault(_address_bucket(v.lat, v.lng), []).append(v)
    for addr_key, group in by_addr.items():
        if len(group) <= 1:
            continue
        # 過半数の course_index を採用 (タイ時は最小値)
        ci_counts = Counter(v.course_index for v in group)
        # 最多出現 (タイブレーク: 小さい index を優先)
        target_ci = min(
            ci_counts.items(),
            key=lambda kv: (-kv[1], kv[0] if kv[0] is not None else 0),
        )[0]
        for v in group:
            v.course_index = target_ci
        if len(group) >= 3:
            warnings.append(
                f"H2 same-address group at lat~{addr_key[0]:.4f},lng~{addr_key[1]:.4f}"
                f" has {len(group)} visits (>= 3); merged into course_index={target_ci}"
                " but timeslot pairing (max=2) cannot be satisfied"
            )


def _enforce_course_capacity(
    visits_in_slot: list[ProposedVisit],
    *,
    k: int,
    warnings: list[str],
) -> None:
    """H5 (review) enforce: 各 course の visits 数を ``MAX_PATIENTS_PER_COURSE`` 以下に抑える.

    K-Means 後にクラスタが偏った場合、超過したクラスタから最も「クラスタ重心から遠い」
    visit を ``MAX_PATIENTS_PER_COURSE`` 未満で受け入れ可能な最も近いクラスタへ移動する.

    H2 で既に同住所をまとめた visits は同じクラスタに残るよう、同住所グループは
    "アトミックな移動単位" として扱う (グループ内の 1 件を動かすと他も連動).
    """
    if not visits_in_slot or k <= 0:
        return

    # クラスタ別にグループ化
    def _by_cluster() -> dict[int, list[ProposedVisit]]:
        out: dict[int, list[ProposedVisit]] = {ci: [] for ci in range(k)}
        for v in visits_in_slot:
            if v.course_index is None:
                continue
            out.setdefault(v.course_index, []).append(v)
        return out

    def _centroid(cluster: list[ProposedVisit]) -> tuple[float, float] | None:
        if not cluster:
            return None
        return (
            sum(v.lat for v in cluster) / len(cluster),
            sum(v.lng for v in cluster) / len(cluster),
        )

    for _safety in range(50):  # 無限ループ防止
        clusters = _by_cluster()
        # 最大超過クラスタを見つける
        src_idx: int | None = None
        src_overflow = 0
        for ci, members in clusters.items():
            overflow = len(members) - MAX_PATIENTS_PER_COURSE
            if overflow > src_overflow:
                src_overflow = overflow
                src_idx = ci
        if src_idx is None:
            return
        src = clusters[src_idx]
        src_centroid = _centroid(src)
        if src_centroid is None:
            return
        # 同住所グループ単位で「最も中心から遠いグループ」を移動候補にする
        groups: dict[tuple[float, float], list[ProposedVisit]] = {}
        for v in src:
            groups.setdefault(_address_bucket(v.lat, v.lng), []).append(v)
        # group 重心が src centroid から最も遠いものから順に検討
        sorted_groups = sorted(
            groups.values(),
            key=lambda gp: -haversine_km(src_centroid[0], src_centroid[1], gp[0].lat, gp[0].lng),
        )
        moved = False
        for group in sorted_groups:
            # 受け入れ先候補: src 以外 + group 全件追加後に MAX_PATIENTS_PER_COURSE 以下
            best_tgt: int | None = None
            best_dist = float("inf")
            for tgt_ci, members in clusters.items():
                if tgt_ci == src_idx:
                    continue
                if len(members) + len(group) > MAX_PATIENTS_PER_COURSE:
                    continue
                tgt_centroid = _centroid(members)
                if tgt_centroid is None:
                    # 空クラスタは無条件採用
                    if best_tgt is None:
                        best_tgt = tgt_ci
                    continue
                d = haversine_km(tgt_centroid[0], tgt_centroid[1], group[0].lat, group[0].lng)
                if d < best_dist:
                    best_dist = d
                    best_tgt = tgt_ci
            if best_tgt is not None:
                for v in group:
                    v.course_index = best_tgt
                moved = True
                break
        if not moved:
            warnings.append(
                f"course capacity overflow: cluster {src_idx} has {len(src)} visits"
                f" but no target cluster can absorb the largest same-address group"
                f" (max={MAX_PATIENTS_PER_COURSE})"
            )
            return


def _assign_courses_per_office_weekday(
    visits: list[ProposedVisit],
    *,
    staff_count_by_office: dict[UUID, int],
    warnings: list[str],
) -> None:
    """Phase 3: (office, weekday) ごとに K-Means で visits を K クラスタに分割.

    H4 (review): 拠点単位ではなく **拠点 × 曜日** 単位でクラスタリング.
    各 weekday の K = staff_count_by_office[office_id] とし、course_index は
    同じ (office, weekday) 内で 0..K-1 でユニーク.

    後処理:
      - H2 enforce: 同住所 visits を同 course_index に揃える
      - H5 (review) enforce: course 容量 (MAX_PATIENTS_PER_COURSE) を超えるクラスタを解消
    """
    by_office_weekday: dict[tuple[UUID, int], list[ProposedVisit]] = {}
    for v in visits:
        by_office_weekday.setdefault((v.office_id, v.weekday), []).append(v)

    for (office_id, _weekday), ovs in by_office_weekday.items():
        k = max(1, staff_count_by_office.get(office_id, 1))
        # M3: code 数を超えないようクランプ (warnings は _count_staff_per_office 側で済).
        k = min(k, _COURSE_CODES_MAX)
        if k == 1 or len(ovs) <= 1:
            for v in ovs:
                v.course_index = 0
        else:
            points = [(v.lat, v.lng) for v in ovs]
            labels = run_kmeans(points, k, random_state=42)
            if len(labels) != len(ovs):
                for v in ovs:
                    v.course_index = 0
            else:
                for v, lab in zip(ovs, labels, strict=True):
                    v.course_index = lab

        # C1 / H2 enforce: 同住所を同 course にまとめる
        _enforce_h2_same_address_pairing(ovs, warnings)
        # H5 (review) enforce: 容量上限を超えるクラスタを再分配
        _enforce_course_capacity(ovs, k=k, warnings=warnings)


# ---------------------------------------------------------------------------
# Phase 4: スタッフ割当 (layer3_assignment.Layer3Assigner.solve を再利用)
# ---------------------------------------------------------------------------


async def _load_staff_info(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
    iso_year: int,
    iso_week: int,
    week_monday: date,
) -> list[StaffInfo]:
    """Phase 4 用: Layer3Assigner の public API ``load_active_staff`` を委任.

    H6 (実出勤枠) は Layer 3 内部で staff_shifts + weekly_overrides を見ているため
    そのまま流用できる. is_trainee=false のみを返す (H8 簡易対応).
    """
    assigner = Layer3Assigner()
    all_staff = await assigner.load_active_staff(
        db, iso_year=iso_year, iso_week=iso_week, week_monday=week_monday
    )
    staff_office_map: dict[UUID, UUID | None] = {}
    rows = await db.scalars(
        select(Staff).where(
            Staff.id.in_([s.staff_id for s in all_staff]),
        )
    )
    for s in rows.all():
        staff_office_map[s.id] = s.primary_office_id

    return [
        s
        for s in all_staff
        if not s.is_trainee and s.role == "staff" and staff_office_map.get(s.staff_id) in office_ids
    ]


def _build_course_targets_from_visits(
    visits: list[ProposedVisit],
    *,
    course_id_by_key: dict[tuple[UUID, int, int], UUID],
    patient_restrictions: dict[UUID, str | None],
    warnings: list[str],
) -> list[CourseAssignmentTarget]:
    """Phase 4 入力: (office_id, weekday, course_index) ごとに CourseAssignmentTarget を構築."""
    groups: dict[tuple[UUID, int, int], list[ProposedVisit]] = {}
    for v in visits:
        if v.course_index is None:
            continue
        groups.setdefault((v.office_id, v.weekday, v.course_index), []).append(v)

    targets: list[CourseAssignmentTarget] = []
    for (office_id, weekday, course_index), gv in groups.items():
        course_id = course_id_by_key.get((office_id, weekday, course_index))
        if course_id is None:
            continue
        lats = [v.lat for v in gv]
        lngs = [v.lng for v in gv]
        centroid_lat = sum(lats) / len(lats) if lats else None
        centroid_lng = sum(lngs) / len(lngs) if lngs else None
        restrictions: set[str] = set()
        for v in gv:
            r = patient_restrictions.get(v.patient_id)
            if r:
                restrictions.add(r)
        visit_slots = [VisitTimeSlot(start_time=v.start_time, end_time=v.end_time) for v in gv]
        targets.append(
            CourseAssignmentTarget(
                course_id=course_id,
                weekday=weekday,
                course_code=_course_code_for_index(course_index, warnings=warnings),
                centroid_lat=centroid_lat,
                centroid_lng=centroid_lng,
                gender_restrictions=frozenset(restrictions),
                patient_ids=[v.patient_id for v in gv],
                visits=visit_slots,
            )
        )
    return targets


def _enforce_h4_distinct_staff(
    visits: list[ProposedVisit],
    *,
    staff_pool: list[StaffInfo],
    warnings: list[str],
    patient_restrictions: dict[UUID, str | None],
) -> None:
    """C1 / H4 enforce: 週 2 回以上訪問する患者は distinct staff を 2 名以上経由させる.

    手順:
      1. 同 patient_id の visits 集合を取得
      2. 全 visit が同一 staff の場合、片方の visit を別 staff にスワップ
      3. スワップ候補は ``staff_pool`` の中で work_days 制約と sex_restriction (H7)
         を満たすもの。見つからなければ warning に追加 (= ハード違反として記録)

    C2 (W41 v1.0 cross-review): ``patient_restrictions`` を受け取り、代替候補が
    sex_restriction (例: ``female_only``) を満たすかを check する。これにより H7
    違反 (女性限定患者に男性スタッフが割り当たる) を防ぐ.

    注意: ``visits`` の ``assigned_staff_id`` を in-place で書き換える.
    Course.assigned_staff_id は変更しない (= 個別 visit を swap するだけで
    course 単位の代表 staff は維持).
    """
    if not visits:
        return
    by_patient: dict[UUID, list[ProposedVisit]] = {}
    for v in visits:
        if v.assigned_staff_id is not None:
            by_patient.setdefault(v.patient_id, []).append(v)

    staff_by_id = {s.staff_id: s for s in staff_pool}

    for patient_id, pv in by_patient.items():
        if len(pv) < 2:
            continue
        unique_staff = {v.assigned_staff_id for v in pv}
        if len(unique_staff) >= 2:
            continue
        # 全員同じ staff → swap 候補を探す
        current_staff_id = next(iter(unique_staff))
        # swap 対象は最後の 1 件 (任意; weekday が最も大きいもの)
        target_visit = max(pv, key=lambda v: (v.weekday, v.start_time))
        # 候補: staff_pool 内で work_days に target.weekday を含み、current と違う
        # かつ sex_restriction (H7) を満たす
        restriction = patient_restrictions.get(target_visit.patient_id)
        required_sex: str | None = None
        if restriction and restriction.endswith("_only"):
            # 例: 'female_only' → 'female', 'male_only' → 'male'
            required_sex = restriction.replace("_only", "")
        alternative: UUID | None = None
        for s in staff_pool:
            if s.staff_id == current_staff_id:
                continue
            if target_visit.weekday not in s.work_days:
                continue
            # H7: sex_restriction を check (Layer 3 既存制約と整合)
            if required_sex is not None and s.sex != required_sex:
                continue
            alternative = s.staff_id
            break
        if alternative is None:
            warnings.append(
                f"H4 enforce failed: patient {patient_id} has {len(pv)} visits but"
                f" no alternative staff found (current={current_staff_id});"
                " staff swap skipped"
            )
            continue
        target_visit.assigned_staff_id = alternative
        # 注: staff_by_id を使うのは将来拡張用 (work_days lookup 等を高速化).
        _ = staff_by_id


# ---------------------------------------------------------------------------
# Phase 5: 訪問順 (希望時刻ソート, TSP 未実装)
# ---------------------------------------------------------------------------


def _sort_visits_by_preferred_time(visits: list[ProposedVisit]) -> list[ProposedVisit]:
    """Phase 5 (v1.0): 希望時刻でソート. TSP は v1.2 で導入予定."""
    return sorted(visits, key=lambda v: (v.weekday, v.start_time, v.patient_id))


# ---------------------------------------------------------------------------
# KPI 計算 (Phase 1c)
# ---------------------------------------------------------------------------


def _total_route_distance_km(visits: list[ProposedVisit]) -> float:
    """course × weekday ごとに patient lat/lng の隣接 Haversine 合計を取る."""
    if not visits:
        return 0.0
    groups: dict[tuple[UUID, int, int], list[ProposedVisit]] = {}
    for v in visits:
        ci = v.course_index if v.course_index is not None else -1
        groups.setdefault((v.office_id, v.weekday, ci), []).append(v)

    total = 0.0
    for gv in groups.values():
        gv_sorted = sorted(gv, key=lambda v: (v.start_time, v.patient_id))
        for i in range(1, len(gv_sorted)):
            a = gv_sorted[i - 1]
            b = gv_sorted[i]
            total += haversine_km(a.lat, a.lng, b.lat, b.lng)
    return total


def _staff_load_stddev(visits: list[ProposedVisit]) -> float:
    """各スタッフの訪問件数の標準偏差."""
    counts = Counter(v.assigned_staff_id for v in visits if v.assigned_staff_id is not None)
    if len(counts) < 2:
        return 0.0
    return statistics.pstdev(counts.values())


@dataclass
class KpiSnapshot:
    """KPI 計算の中間表現."""

    total_distance_km: float
    staff_load_stddev: float
    visits_count: int


def _calc_snapshot(visits: list[ProposedVisit]) -> KpiSnapshot:
    return KpiSnapshot(
        total_distance_km=_total_route_distance_km(visits),
        staff_load_stddev=_staff_load_stddev(visits),
        visits_count=len(visits),
    )


def _calc_h_violations(visits: list[ProposedVisit]) -> dict[str, int]:
    """ハード制約 H1-H8 の違反件数を集計."""
    return {
        "H1": _check_h1_weekly_unified(visits),
        "H2": _check_h2_same_address_pairing(visits),
        "H3": _check_h3_same_address_contiguity(visits),
        "H4": _check_h4_all_visits_same_staff(visits),
        "H5": 0,
        "H6": 0,
        "H7": 0,
        "H8": 0,
    }


def _calc_improvement_score(
    *,
    before: KpiSnapshot,
    after: KpiSnapshot,
    h_violations: dict[str, int],
) -> float:
    """重み付き改善スコア."""
    dist_delta = before.total_distance_km - after.total_distance_km
    load_delta = before.staff_load_stddev - after.staff_load_stddev
    violations_sum = sum(h_violations.values())
    return round(
        WEIGHT_DISTANCE * dist_delta
        + WEIGHT_LOAD * load_delta
        + WEIGHT_ROTATION * 0.0
        - WEIGHT_SOFT_VIOLATIONS * violations_sum,
        4,
    )


def calc_kpis(
    before_visits: list[ProposedVisit],
    after_visits: list[ProposedVisit],
) -> dict[str, Any]:
    """Phase 1c: Before/After visits から基本 KPI を算出する."""
    before = _calc_snapshot(before_visits)
    after = _calc_snapshot(after_visits)
    h_violations = _calc_h_violations(after_visits)
    if before.total_distance_km > 0:
        reduction_pct = (
            (before.total_distance_km - after.total_distance_km) / before.total_distance_km * 100.0
        )
    else:
        reduction_pct = 0.0
    return {
        "total_distance_km_before": round(before.total_distance_km, 4),
        "total_distance_km_after": round(after.total_distance_km, 4),
        "distance_reduction_pct": round(reduction_pct, 2),
        "staff_load_stddev_before": round(before.staff_load_stddev, 4),
        "staff_load_stddev_after": round(after.staff_load_stddev, 4),
        "h_violations": h_violations,
        "improvement_score": _calc_improvement_score(
            before=before, after=after, h_violations=h_violations
        ),
    }


# ---------------------------------------------------------------------------
# Persistence (proposed courses + planned visits + visit_staff_assignments)
# ---------------------------------------------------------------------------


async def _resolve_course_template_for_office(
    db: AsyncSession, *, office_id: UUID
) -> CourseTemplate | None:
    """拠点の最初の非 M course_template を返す (Layer 1 と同じ簡易戦略)."""
    rows = (
        await db.scalars(
            select(CourseTemplate)
            .where(
                CourseTemplate.office_id == office_id,
                CourseTemplate.deleted_at.is_(None),
            )
            .order_by(CourseTemplate.label, CourseTemplate.created_at)
        )
    ).all()
    for tpl in rows:
        label = (tpl.label or "").strip().upper()
        if not label or label.startswith("M"):
            continue
        return tpl
    return rows[0] if rows else None


async def _persist_courses_visits_and_assignments(
    db: AsyncSession,
    *,
    visits: list[ProposedVisit],
    iso_year: int,
    iso_week: int,
    week_monday: date,
    proposal_batch_id: UUID,
    warnings: list[str],
) -> dict[tuple[UUID, int, int], UUID]:
    """新規 Course + Visit + VisitStaffAssignment を一括 INSERT.

    H1 (review): Phase 3 → Phase 4 後の単一 persist で全て書き込む.
    Course.assigned_staff_id と Course.decision_log (M1) もここで populate する.

    Returns: ``{(office_id, weekday, course_index): course_id}`` のマップ.
    """
    keys: dict[tuple[UUID, int, int], list[ProposedVisit]] = {}
    for v in visits:
        if v.course_index is None:
            continue
        keys.setdefault((v.office_id, v.weekday, v.course_index), []).append(v)

    template_cache: dict[UUID, CourseTemplate | None] = {}
    course_id_by_key: dict[tuple[UUID, int, int], UUID] = {}
    courses_by_key: dict[tuple[UUID, int, int], Course] = {}

    # ----- Course を INSERT (ID 解決のため 1 度だけ flush) -----
    for (office_id, weekday, course_index), _gv in keys.items():
        if office_id not in template_cache:
            template_cache[office_id] = await _resolve_course_template_for_office(
                db, office_id=office_id
            )
        template = template_cache[office_id]
        # 同 (office, weekday) 内では code がユニーク
        course_code = _course_code_for_index(course_index, warnings=warnings)
        course = Course(
            iso_year=iso_year,
            iso_week=iso_week,
            weekday=weekday,
            code=course_code,
            course_status=COURSE_STATUS_PROPOSED,
            template_id=template.id if template is not None else None,
            office_id=office_id,
            proposal_batch_id=proposal_batch_id,
        )
        db.add(course)
        courses_by_key[(office_id, weekday, course_index)] = course

    # Course の id を解決するため 1 度 flush
    await db.flush()
    for key, course in courses_by_key.items():
        course_id_by_key[key] = course.id

    # ----- W41 v1.0 hotfix: 同 (patient_id, visit_date, start_time, visit_group_id) の
    # 既存 active visits を pre-cleanup として soft-delete する.
    # visits テーブルの partial UNIQUE INDEX `uq_visits_pds_group_active`
    # (patient_id, visit_date, start_time, COALESCE(visit_group_id, '00000000-...'::uuid))
    # WHERE deleted_at IS NULL があり、Layer 1 等が事前に作成した active visits
    # (source='auto' 等) と auto_allocator が INSERT する新規 visits (source='auto_alloc')
    # が同 patient × date × time で衝突する. auto-allocator は「自動算出は既存
    # スケジュールを上書き候補とする」セマンティクスなので、INSERT 前に既存
    # active visits を soft-delete してから INSERT する.
    zero_uuid = uuid.UUID("00000000-0000-0000-0000-000000000000")
    target_keys: set[tuple[UUID, date, time, UUID]] = set()
    for gv in keys.values():
        for v in gv:
            v_date = date.fromordinal(week_monday.toordinal() + v.weekday)
            # auto_allocator は required_staff_count=1 固定なので visit_group_id は常に NULL.
            target_keys.add((v.patient_id, v_date, v.start_time, zero_uuid))

    if target_keys:
        patient_ids_set = {k[0] for k in target_keys}
        dates_set = {k[1] for k in target_keys}
        times_set = {k[2] for k in target_keys}
        existing_visits = (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id.in_(patient_ids_set),
                    Visit.visit_date.in_(dates_set),
                    Visit.start_time.in_(times_set),
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
        now = datetime.now(tz=UTC)
        soft_deleted_count = 0
        for ev in existing_visits:
            ev_vgid_key = ev.visit_group_id if ev.visit_group_id is not None else zero_uuid
            if (ev.patient_id, ev.visit_date, ev.start_time, ev_vgid_key) in target_keys:
                ev.deleted_at = now
                soft_deleted_count += 1
        if soft_deleted_count > 0:
            warnings.append(
                f"既存の active visits {soft_deleted_count} 件を上書き候補として soft-delete しました"
            )
            logger.info(
                "auto_allocate: soft-deleted %d existing visits before insert (batch=%s)",
                soft_deleted_count,
                proposal_batch_id,
            )
            # soft-delete を flush してから新規 INSERT (UNIQUE 衝突回避)
            await db.flush()

    # ----- Visit を INSERT (course_id を紐付け) -----
    visits_to_persist: list[tuple[ProposedVisit, Visit]] = []
    for (office_id, weekday, course_index), gv in keys.items():
        course = courses_by_key[(office_id, weekday, course_index)]
        for v in gv:
            visit_date = date.fromordinal(week_monday.toordinal() + v.weekday)
            visit = Visit(
                patient_id=v.patient_id,
                visit_date=visit_date,
                start_time=v.start_time,
                end_time=v.end_time,
                type=AUTO_ALLOC_VISIT_TYPE,
                status=AUTO_ALLOC_VISIT_STATUS,
                source=AUTO_ALLOC_VISIT_SOURCE,
                required_staff_count=1,
                course_id=course.id,
                note=f"auto_allocator: batch={proposal_batch_id}",
            )
            db.add(visit)
            visits_to_persist.append((v, visit))

    # Visit の id を解決するため flush
    await db.flush()
    for pv, visit_obj in visits_to_persist:
        pv.visit_id = visit_obj.id
        pv.course_id = visit_obj.course_id

    return course_id_by_key


def _build_course_decision_log(
    *,
    course_index: int,
    office_id: UUID,
    weekday: int,
    visits_in_course: list[ProposedVisit],
    chosen_staff_id: UUID | None,
    alternatives: list[UUID],
) -> dict[str, Any]:
    """M1: Course.decision_log に書き込む JSON を構築する."""
    distance_km = 0.0
    if len(visits_in_course) >= 2:
        sorted_v = sorted(visits_in_course, key=lambda v: (v.start_time, v.patient_id))
        for i in range(1, len(sorted_v)):
            distance_km += haversine_km(
                sorted_v[i - 1].lat,
                sorted_v[i - 1].lng,
                sorted_v[i].lat,
                sorted_v[i].lng,
            )
    return {
        "phase_3": {
            "cluster_method": "kmeans",
            "office_id": str(office_id),
            "weekday": weekday,
            "n_visits": len(visits_in_course),
            "cluster_index": course_index,
        },
        "phase_4": {
            "chosen_staff_id": str(chosen_staff_id) if chosen_staff_id is not None else None,
            "alternatives": [str(s) for s in alternatives],
        },
        "kpi_contribution": {
            "distance_km": round(distance_km, 4),
            "load": len(visits_in_course),
        },
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def auto_allocate(
    session: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
    mode: Literal["mode_1", "mode_2"],
) -> dict[str, Any]:
    """v1.0 自動算出エントリ.

    Phase 1 → 2 (Mode 1) → 3 → 4 → persist → 5 を順に実行し、結果を dict で返す.

    Args:
        session: 共有 AsyncSession (commit/rollback は呼ばない).
        iso_year: ISO 年 (例 2026).
        iso_week: ISO 週 (1-53).
        office_ids: 対象拠点リスト (空なら早期 return).
        mode: "mode_1" (既存固定枠維持 + 新規最適化) / "mode_2" (全面最適化) 両方サポート済み.

    Returns:
        {
            "proposal_batch_id": UUID,
            "proposals": [{course_id, office_id, weekday, code,
                           patient_ids, total_distance_km, assigned_staff_id}, ...],
            "kpi": {... see calc_kpis ...},
            "warnings": [str, ...],
        }
    """
    if mode not in ("mode_1", "mode_2"):
        raise ValueError(f"unsupported mode: {mode!r}")
    if iso_year < 2000 or iso_year > 2100:
        raise ValueError(f"iso_year out of range: {iso_year}")
    if iso_week < 1 or iso_week > 53:
        raise ValueError(f"iso_week out of range: {iso_week}")

    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: year={iso_year} week={iso_week}") from exc

    proposal_batch_id = uuid.uuid4()
    warnings: list[str] = []

    # ----- H4 (W41 v1.0 cross-review): 空 office_ids = "全拠点を対象" -----
    # フロントが拠点未選択時に空配列を送る契約 (max_length 上限を回避し、
    # かつ「全拠点」の意味を明示化するため). 空なら DB から active office を
    # 全件ロードする. 1 件も無ければ早期 return.
    if not office_ids:
        all_office_rows = await session.scalars(
            select(Office.id).where(Office.deleted_at.is_(None))
        )
        office_ids = list(all_office_rows.all())
        if not office_ids:
            return {
                "proposal_batch_id": proposal_batch_id,
                "proposals": [],
                "kpi": calc_kpis([], []),
                "warnings": ["対象拠点が登録されていません"],
            }

    # ----- H4 (W41 v1.0 cross-review): observability — 入口ログ -----
    logger.info(
        "auto_allocate: batch=%s year=%s week=%s offices=%s mode=%s",
        proposal_batch_id,
        iso_year,
        iso_week,
        office_ids,
        mode,
    )

    # ----- W41 v1.0 feedback: 確定済み (status != proposed) Course は「上書き候補」 -----
    # 旧仕様では 409 Conflict を返していたが、ユーザーは「週生成 → 自動割付」で
    # 全 Course が staff_assigned になった状態で再算出したいケースが多数。
    # migration 0030 で UNIQUE を partial UNIQUE INDEX
    # (WHERE course_status != 'proposed') に切り替えたため、新規 proposed Course は
    # 既存 finalized Course と (year, week, weekday, code, office) を共有しても
    # 制約違反にならない。
    # 採用 (apply) 時に既存 finalized を soft-delete する契約 (schedule.py 側).
    finalized_existing_rows = await session.scalars(
        select(Course.id).where(
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.office_id.in_(office_ids),
            Course.course_status.in_((COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_STAFF_ASSIGNED)),
            Course.deleted_at.is_(None),
        )
    )
    finalized_existing_ids = list(finalized_existing_rows.all())
    if finalized_existing_ids:
        warnings.append(
            f"既存の確定済みコース {len(finalized_existing_ids)} 件を上書き候補とします"
            " (採用時に soft-delete されます)"
        )
        logger.info(
            "auto_allocate: batch=%s found %d finalized course(s) to overwrite",
            proposal_batch_id,
            len(finalized_existing_ids),
        )

    # ----- H1 (review): 同一 (iso_year, iso_week, office) の既存 proposed batch を invalidate -----
    # 同じ週・拠点に対して再度 auto-allocate を実行した場合、過去の未採用 proposed
    # courses + 紐付く visits / visit_staff_assignments を破棄してから新しい batch を作る.
    # 関連 visit_staff_assignments → visits → courses の順に物理削除する (SQLite 互換).
    existing_proposed_rows = await session.scalars(
        select(Course).where(
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.office_id.in_(office_ids),
            Course.course_status == COURSE_STATUS_PROPOSED,
            Course.deleted_at.is_(None),
        )
    )
    existing_proposed_ids = [c.id for c in existing_proposed_rows.all()]
    if existing_proposed_ids:
        # visit_staff_assignments を先に削除 (visit_id 経由)
        await session.execute(
            delete(VisitStaffAssignment).where(
                VisitStaffAssignment.visit_id.in_(
                    select(Visit.id).where(
                        Visit.course_id.in_(existing_proposed_ids),
                        Visit.deleted_at.is_(None),
                    )
                )
            )
        )
        # 次に visits を削除
        await session.execute(
            delete(Visit).where(
                Visit.course_id.in_(existing_proposed_ids),
                Visit.deleted_at.is_(None),
            )
        )
        # 最後に courses を削除
        await session.execute(
            delete(Course).where(
                Course.id.in_(existing_proposed_ids),
                Course.course_status == COURSE_STATUS_PROPOSED,
                Course.deleted_at.is_(None),
            )
        )
        await session.flush()
        warnings.append(
            f"既存の proposed batch {len(existing_proposed_ids)} 件を invalidate しました"
        )

    # ----- M2: 患者一括ロード (Phase 1/2/4 で使い回す) -----
    patients_by_id = await _load_patients_by_offices(session, office_ids=office_ids)

    # ----- H2 (W41 v1.0 cross-review): Mode 2 = "全 active 患者対象" の整合性 -----
    # _load_patients_by_offices は ``primary_office_id IN (office_ids)`` で
    # フィルタするため、active だが office_id が NULL の患者は silent に
    # 取りこぼされる. Mode 2 では「全 active 対象」が UI 上の契約なので、
    # 漏れた患者を明示的に warnings へ追加する.
    if mode == "mode_2":
        orphan_rows = await session.scalars(
            select(Patient).where(
                Patient.status == "active",
                Patient.deleted_at.is_(None),
                Patient.primary_office_id.is_(None),
            )
        )
        for orphan in orphan_rows.all():
            warnings.append(
                f"mode_2 patient {orphan.code} (id={orphan.id}) は拠点未設定のためスキップ"
            )

    # ----- Phase 1: 固定枠集約 -----
    # Mode 1: 既存 PFV を保持しつつプール患者を差し込む.
    # Mode 2: 既存 PFV は参考バッファとして退避し、全 active 患者を再配置する
    #         → Phase 1 の fixed_visits は空 (= 提案には含めない). 既存 PFV
    #         自体は採用時に置換されるため、ここでは生成しない.
    if mode == "mode_2":
        fixed_visits: list[ProposedVisit] = []
        patients_with_fixed: set[UUID] = set()
        # H3 (W41 v1.0 cross-review): Mode 2 では before を「全 active 患者の
        # 既存 visits (deleted_at IS NULL かつ対象週内)」から構築する.
        # 旧実装は PFV 由来のみで before を作っていたため、weekly_pattern のみ
        # で after に入る患者が before に存在せず、距離改善率 (distance_reduction_pct)
        # が無意味化していた. 新実装は同 (iso_year, iso_week) のすべての既存
        # active visits を before として扱うことで、after との差分が実体ある
        # 「全面最適化による変化」を表す.
        before_visits = await _build_before_visits_from_existing(
            session,
            patients_by_id=patients_by_id,
            week_monday=week_monday,
        )
    else:
        fixed_visits, patients_with_fixed = await _collect_fixed_visits(
            session, patients_by_id=patients_by_id
        )
        before_visits = list(fixed_visits)

    # ----- H2 (W41 v1.0 cross-review): KPI before の staff_load_stddev を
    # 既存 visit_staff_assignments から populate して意味のある値にする.
    # 同 (iso_year, iso_week, office) の planned/scheduled visits を経由して
    # 既存 staff 割当を before_visits[*].assigned_staff_id にコピーする.
    # 注: 本値はあくまで KPI 比較用. _extract_pool_preferences の H2 ペアリングは
    # v1.1 で対応する (今回スコープ外).
    if before_visits:
        # patient_id × visit_date (= week_monday + weekday) のキーで join.
        visit_date_for_weekday = [
            (v.patient_id, date.fromordinal(week_monday.toordinal() + v.weekday))
            for v in before_visits
        ]
        patient_ids = list({pid for pid, _ in visit_date_for_weekday})
        if patient_ids:
            existing_visits_rows = await session.scalars(
                select(Visit).where(
                    Visit.patient_id.in_(patient_ids),
                    Visit.visit_date.in_([d for _, d in visit_date_for_weekday]),
                    Visit.deleted_at.is_(None),
                )
            )
            existing_visits_by_key: dict[tuple[UUID, date], UUID] = {}
            for ev in existing_visits_rows.all():
                existing_visits_by_key[(ev.patient_id, ev.visit_date)] = ev.id
            if existing_visits_by_key:
                vsa_rows = await session.scalars(
                    select(VisitStaffAssignment).where(
                        VisitStaffAssignment.visit_id.in_(list(existing_visits_by_key.values()))
                    )
                )
                staff_by_visit_id: dict[UUID, UUID] = {
                    a.visit_id: a.staff_id for a in vsa_rows.all()
                }
                for bv in before_visits:
                    vid = existing_visits_by_key.get(
                        (
                            bv.patient_id,
                            date.fromordinal(week_monday.toordinal() + bv.weekday),
                        )
                    )
                    if vid is None:
                        continue
                    bv.assigned_staff_id = staff_by_visit_id.get(vid)

    logger.info(
        "auto_allocate phase1 done: batch=%s fixed_visits=%d patients_with_fixed=%d",
        proposal_batch_id,
        len(fixed_visits),
        len(patients_with_fixed),
    )

    # ----- Phase 2: プール患者 (Mode 1) / 全患者 (Mode 2) のスロット生成 -----
    unavailable_slots = await _load_unavailable_slots(session, office_ids=office_ids)
    if mode == "mode_2":
        # Mode 2: weekly_pattern が空な患者向けに PFV を fallback にする.
        fixed_fallback_prefs = await _build_fixed_visit_fallback_prefs(
            session,
            patient_ids=list(patients_by_id.keys()),
        )
        pool_visits = _insert_all_patients_mode2(
            patients_by_id=patients_by_id,
            fixed_fallback_prefs=fixed_fallback_prefs,
            unavailable_slots=unavailable_slots,
            warnings=warnings,
        )
    else:
        pool_visits = _insert_pool_patients_mode1(
            patients_by_id=patients_by_id,
            patients_with_fixed=patients_with_fixed,
            unavailable_slots=unavailable_slots,
            warnings=warnings,
        )

    all_visits = fixed_visits + pool_visits
    logger.info(
        "auto_allocate phase2 done: batch=%s pool_visits=%d all_visits=%d",
        proposal_batch_id,
        len(pool_visits),
        len(all_visits),
    )

    if not all_visits:
        return {
            "proposal_batch_id": proposal_batch_id,
            "proposals": [],
            "kpi": calc_kpis(before_visits, []),
            "warnings": warnings + ["no visits to allocate"],
        }

    # ----- Phase 3: コース割当 (K-Means per (office, weekday) + H2/H5 enforce) -----
    staff_count_by_office = await _count_staff_per_office(
        session, office_ids=office_ids, warnings=warnings
    )
    # H1 (W41 v1.0 cross-review): スタッフゼロなら course/visit を永続化せずに早期 return.
    # K=0 で K-Means が走らず後段で破綻するため、proposals=[] + 警告で抜ける.
    if not any(staff_count_by_office.values()):
        warnings.append("スタッフ不在のため算出不可")
        logger.info(
            "auto_allocate aborted: batch=%s no eligible staff for offices=%s",
            proposal_batch_id,
            office_ids,
        )
        return {
            "proposal_batch_id": proposal_batch_id,
            "proposals": [],
            "kpi": calc_kpis(before_visits, []),
            "warnings": warnings,
        }
    _assign_courses_per_office_weekday(
        all_visits,
        staff_count_by_office=staff_count_by_office,
        warnings=warnings,
    )
    logger.info(
        "auto_allocate phase3 done: batch=%s offices_with_staff=%d",
        proposal_batch_id,
        sum(1 for k in staff_count_by_office.values() if k > 0),
    )

    # ----- Phase 4: スタッフ割当 (Layer 3 solve 再利用) -----
    # H1 (review): persist は Phase 4 後にまとめて 1 回 (course_id 解決のため
    # 内部で 2 度 flush するが、論理的には Phase 3→4→persist の順序を維持).
    staff_pool = await _load_staff_info(
        session,
        office_ids=office_ids,
        iso_year=iso_year,
        iso_week=iso_week,
        week_monday=week_monday,
    )

    # M2: patient_restrictions は既にロード済の patients_by_id から取得 (追加 query 不要)
    patient_restrictions: dict[UUID, str | None] = {
        p.id: p.sex_restriction for p in patients_by_id.values()
    }

    # ----- 永続化 (Course + Visit; Phase 4 で staff を決定後 Course.assigned_staff_id を更新) -----
    course_id_by_key = await _persist_courses_visits_and_assignments(
        session,
        visits=all_visits,
        iso_year=iso_year,
        iso_week=iso_week,
        week_monday=week_monday,
        proposal_batch_id=proposal_batch_id,
        warnings=warnings,
    )

    course_targets = _build_course_targets_from_visits(
        all_visits,
        course_id_by_key=course_id_by_key,
        patient_restrictions=patient_restrictions,
        warnings=warnings,
    )

    layer3_assignments: list[tuple[UUID, UUID]] = []  # (course_id, staff_id)
    if staff_pool and course_targets:
        assigner = Layer3Assigner()
        layer3_result = assigner.solve(
            course_targets,
            staff_pool,
            history=None,
            fixed_staff_by_course=None,
            same_course_prev_day_penalty=True,
            events_by_staff=None,
            week_monday=week_monday,
        )
        staff_by_course = {a.course_id: a.staff_id for a in layer3_result.assignments}
        layer3_assignments = list(staff_by_course.items())
        for v in all_visits:
            if v.course_id is not None:
                v.assigned_staff_id = staff_by_course.get(v.course_id)
    else:
        warnings.append("staff assignment skipped: empty staff_pool or no course_targets")

    # ----- C1 / H4 enforce: 全訪問同スタッフ禁止 (+ C2: sex_restriction check) -----
    _enforce_h4_distinct_staff(
        all_visits,
        staff_pool=staff_pool,
        warnings=warnings,
        patient_restrictions=patient_restrictions,
    )

    # ----- C2: VisitStaffAssignment を新規 INSERT + Course.assigned_staff_id 更新 -----
    staff_by_course_id = dict(layer3_assignments)
    # Course.assigned_staff_id を Layer 3 結果で更新
    course_rows = await session.scalars(
        select(Course).where(Course.id.in_(list(course_id_by_key.values())))
    )
    courses_by_id_map: dict[UUID, Course] = {c.id: c for c in course_rows.all()}
    for course_id, course in courses_by_id_map.items():
        course.assigned_staff_id = staff_by_course_id.get(course_id)

    # VisitStaffAssignment を新規 INSERT (auto_allocator が persist した visits のみ)
    for v in all_visits:
        if v.visit_id is None or v.assigned_staff_id is None:
            continue
        session.add(
            VisitStaffAssignment(
                visit_id=v.visit_id,
                staff_id=v.assigned_staff_id,
            )
        )

    # ----- M1: Course.decision_log を populate -----
    # course_id -> visits マップ
    visits_by_course_id: dict[UUID, list[ProposedVisit]] = {}
    for v in all_visits:
        if v.course_id is None:
            continue
        visits_by_course_id.setdefault(v.course_id, []).append(v)
    for (office_id, weekday, course_index), course_id in course_id_by_key.items():
        course = courses_by_id_map.get(course_id)
        if course is None:
            continue
        visits_in_course = visits_by_course_id.get(course_id, [])
        chosen_staff_id = staff_by_course_id.get(course_id)
        # alternatives: 同 weekday に staff_pool で work 可能なスタッフ (chosen 以外)
        alternatives = [
            s.staff_id
            for s in staff_pool
            if weekday in s.work_days and s.staff_id != chosen_staff_id
        ]
        course.decision_log = _build_course_decision_log(
            course_index=course_index,
            office_id=office_id,
            weekday=weekday,
            visits_in_course=visits_in_course,
            chosen_staff_id=chosen_staff_id,
            alternatives=alternatives,
        )

    logger.info(
        "auto_allocate phase4 done: batch=%s assigned_courses=%d staff_pool=%d",
        proposal_batch_id,
        len(staff_by_course_id),
        len(staff_pool),
    )

    # ----- Phase 5: 訪問順 (希望時刻ソート, v1.0 簡易) -----
    _ = _sort_visits_by_preferred_time(all_visits)

    # ----- 提案サマリ生成 -----
    proposals: list[dict[str, Any]] = []
    grouped: dict[tuple[UUID, int, int], list[ProposedVisit]] = {}
    for v in all_visits:
        if v.course_index is None or v.course_id is None:
            continue
        grouped.setdefault((v.office_id, v.weekday, v.course_index), []).append(v)
    for (office_id, weekday, course_index), gv in grouped.items():
        lats = [v.lat for v in gv]
        lngs = [v.lng for v in gv]
        if len(gv) >= 2:
            gv_sorted = sorted(gv, key=lambda v: v.start_time)
            dist = sum(
                haversine_km(
                    gv_sorted[i - 1].lat,
                    gv_sorted[i - 1].lng,
                    gv_sorted[i].lat,
                    gv_sorted[i].lng,
                )
                for i in range(1, len(gv_sorted))
            )
        else:
            dist = 0.0
        proposals.append(
            {
                "course_id": gv[0].course_id,
                "office_id": office_id,
                "weekday": weekday,
                "code": _course_code_for_index(course_index, warnings=warnings),
                "patient_ids": [v.patient_id for v in gv],
                "centroid_lat": (sum(lats) / len(lats)) if lats else None,
                "centroid_lng": (sum(lngs) / len(lngs)) if lngs else None,
                "total_distance_km": round(dist, 4),
                "assigned_staff_id": gv[0].assigned_staff_id,
            }
        )

    # ----- KPI -----
    kpi = calc_kpis(before_visits, all_visits)

    # H1 (review): 全 Phase 完了後に最終 flush (commit は呼び出し側)
    await session.flush()

    logger.info(
        "auto_allocate done: batch=%s proposals=%d warnings=%d improvement_score=%s",
        proposal_batch_id,
        len(proposals),
        len(warnings),
        kpi.get("improvement_score"),
    )

    return {
        "proposal_batch_id": proposal_batch_id,
        "proposals": proposals,
        "kpi": kpi,
        "warnings": warnings,
    }


__all__ = [
    "AUTO_ALLOC_VISIT_SOURCE",
    "AUTO_ALLOC_VISIT_STATUS",
    "AUTO_ALLOC_VISIT_TYPE",
    "MAX_PATIENTS_PER_COURSE",
    "ProposedVisit",
    "SAME_ADDRESS_MAX",
    "SAME_ADDRESS_TOLERANCE",
    "WEIGHT_DISTANCE",
    "WEIGHT_LOAD",
    "WEIGHT_ROTATION",
    "WEIGHT_SOFT_VIOLATIONS",
    "auto_allocate",
    "calc_kpis",
]
