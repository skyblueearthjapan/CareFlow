"""改善提案エンジン (P2-B).

配置済み患者の各固定枠 (PFV) について、「今の位置」と「入れ替え候補の位置」の
**限界コスト** を比べ、週あたりの移動 (+バッファー) / 距離を縮められる移動先を
提案する純ロジック + DB 集約層.

設計書: docs/plans/p2-improvement-mvp-design.md §2.

方針 (Phase 0-1 / propose-slots の資産を再利用. コピー禁止):
  - 限界コスト = ``travel(prev→X)+travel(X→next) − travel(prev→next)``. 先頭/末尾は
    該当辺のみ. 同住所は 0. 距離/移動/バッファーは ``proposal_solver`` の
    ``_travel_buffer_between`` / ``_is_same_address`` と ``auto_allocator_v2`` の
    ``haversine_km`` をそのまま使う (= 健康診断 / 自動割当と同一物差し).
  - 候補列挙は propose-slots と同じ週バケット (``load_week_course_buckets``) に対し、
    **自分の visit を除いた** existing で ``find_available_slots_for_candidate`` を呼ぶ.
  - スタッフ実態警告は P0-1 (propose-slots) の bucket 情報を流用する.
  - 計算ベースは PFV (恒久パターン). 当週 Visit ベースは Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.suggestion_dismissal import SuggestionDismissal
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_DURATION_PREFERRED as _LUNCH_DURATION_PREFERRED,
)
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_EARLIEST_START as _LUNCH_EARLIEST_START,
)
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_LATEST_END as _LUNCH_LATEST_END,
)
from app.services.scheduling.auto_allocator_v2 import (
    _add_minutes,
    compute_lunch_window,
    haversine_km,
)
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG, SchedulingConfig
from app.services.scheduling.pfv_validator import _find_conflict
from app.services.scheduling.proposal_solver import (
    Candidate,
    ExistingVisit,
    _is_same_address,
    _travel_buffer_between,
    find_available_slots_for_candidate,
)
from app.services.scheduling.propose_slots_service import (
    _course_label,
    _CourseBucket,
    _fmt_hhmm,
    _staff_sex_mismatch,
    load_week_course_buckets,
)

# 改善とみなす最小効果 (分/週). config 化は Phase 3 (設計書 §2.1).
IMPROVEMENT_THRESHOLD_MIN: int = 10

# 患者 1 名あたりの最大提案件数.
MAX_SUGGESTIONS_PER_PATIENT: int = 5

# 患者 1 名あたりの最大スワップ提案件数 (既存 5 件枠に delta 降順で混ぜる).
MAX_SWAP_SUGGESTIONS_PER_PATIENT: int = 2

# スタッフ実態警告コード (propose-slots P0-1 と同一 raw code. FE が日本語化する).
_WARN_STAFF_UNASSIGNED = "staff_unassigned"
_WARN_STAFF_ABSENT = "staff_absent"
_WARN_STAFF_SEX_MISMATCH = "staff_sex_mismatch"

# 曜日ラベル (0=Mon..6=Sun).
_WEEKDAY_JA: tuple[str, ...] = ("月", "火", "水", "木", "金", "土", "日")
_WEEKDAY_CODE: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# 座標 (lat, lng). 先頭/末尾は None.
Coord = tuple[float, float]


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


# ---------------------------------------------------------------------------
# 限界コスト
# ---------------------------------------------------------------------------


def _edge_minutes(a: Coord | None, b: Coord | None, *, config: SchedulingConfig | None) -> int:
    """1 辺の移動 + バッファー (分). 端が無ければ 0. 同住所は 0 (`_travel_buffer_between`)."""
    if a is None or b is None:
        return 0
    return _travel_buffer_between(a[0], a[1], b[0], b[1], config=config)


def _edge_km(a: Coord | None, b: Coord | None) -> float:
    """1 辺の直線距離 (km). 端が無ければ 0. 同住所は 0."""
    if a is None or b is None:
        return 0.0
    if _is_same_address(a[0], a[1], b[0], b[1]):
        return 0.0
    return haversine_km(a[0], a[1], b[0], b[1])


def compute_marginal_cost(
    target: Coord,
    prev: Coord | None,
    next: Coord | None,  # noqa: A002 - 設計書 §2.1 の signature に合わせる
    *,
    config: SchedulingConfig | None = None,
) -> tuple[int, float]:
    """限界コスト (分, km) を返す.

    ``marginal = travel(prev→target) + travel(target→next) − travel(prev→next)``.
    先頭 (prev=None) / 末尾 (next=None) は該当辺のみ (無い辺は 0). 同住所は 0.

    Args:
        target: 対象訪問の (lat, lng).
        prev: 直前訪問の (lat, lng). コース先頭なら None.
        next: 直後訪問の (lat, lng). コース末尾なら None.

    Returns:
        ``(minutes, km)``. minutes は移動 + バッファー (分), km は直線距離.
    """
    minutes = (
        _edge_minutes(prev, target, config=config)
        + _edge_minutes(target, next, config=config)
        - _edge_minutes(prev, next, config=config)
    )
    km = _edge_km(prev, target) + _edge_km(target, next) - _edge_km(prev, next)
    return minutes, km


# ---------------------------------------------------------------------------
# 内部データ構造 (API schema に詰める前の内部表現)
# ---------------------------------------------------------------------------


@dataclass
class ImprovementCandidateData:
    """改善提案 1 件 (内部表現)."""

    kind: str  # 'time_change' | 'day_change' | 'swap'
    target_weekday: int  # 却下指紋の対象曜日 (= 現在枠の曜日)
    # 現在枠.
    current_office_id: UUID
    current_weekday: int
    current_start: time
    current_end: time
    current_course_label: str
    current_staff_name: str | None
    # 候補枠.
    cand_office_id: UUID
    cand_office_name: str | None
    cand_weekday: int
    cand_start: time
    cand_end: time
    cand_course_code: str
    cand_course_label: str
    cand_staff_name: str | None
    # 効果 / 付帯情報.
    delta_minutes: int
    delta_km: float
    staff_warnings: list[str]
    requires_patient_confirmation: bool
    changes: list[str]
    unchanged: list[str]
    # kind='swap' のときのみ設定される相手患者 Y の情報 (P3-②). それ以外は None.
    swap_counterpart_patient_id: UUID | None = None
    swap_counterpart_name: str | None = None
    swap_counterpart_current_weekday: int | None = None
    swap_counterpart_current_start: time | None = None
    swap_counterpart_new_weekday: int | None = None
    swap_counterpart_new_start: time | None = None
    swap_counterpart_requires_confirmation: bool = False


@dataclass
class FilteredSummaryData:
    """提案を出さなかった内訳 (N-6). 内部表現.

    各カウンタの単位は ImprovementFilteredSummary の description を参照.
    カテゴリ間は重複しない (1 PFV / 候補は 1 カテゴリのみに計上).

    設計決定 D1: スワップ (kind='swap') 除外は現状カウント対象外.
    スワップ提案は「X+Y のペア」を単位として生成されるため、PFV 単位・候補スロット単位と
    粒度が異なり既存カウンタに加算すると意味が曖昧になる.
    将来 swap_dismissed_pairs 等の専用カウンタを追加できる (後方互換フィールド追加のみ).
    """

    pinned: int = 0
    locked: int = 0
    no_current_visit: int = 0
    dismissed: int = 0
    below_threshold: int = 0
    day_restricted: int = 0


@dataclass
class _CurrentPlacement:
    """PFV の当週実配置 (bucket + 自 visit の座標)."""

    bucket: _CourseBucket
    office_code: str | None
    start_time: time
    end_time: time
    marginal_min: int
    marginal_km: float


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _bucket_existing_excluding(
    bucket: _CourseBucket, patient_id: UUID
) -> list[ExistingVisit]:
    """bucket の既存訪問から対象患者自身を除いた ExistingVisit 列 (start 昇順)."""
    out = [
        ExistingVisit(
            start_time=v.start_time,
            end_time=v.end_time,
            lat=v.lat,
            lng=v.lng,
            service_minutes=v.service_minutes,
            patient_id=str(v.patient_id),
        )
        for v in bucket.visits
        if v.patient_id != patient_id
    ]
    out.sort(key=lambda e: _time_to_min(e.start_time))
    return out


def _neighbors_at(
    existing_sorted: list[ExistingVisit], at_min: int
) -> tuple[Coord | None, Coord | None]:
    """時刻 ``at_min`` の直前 / 直後の既存訪問座標を返す (同時刻はどちらでもない)."""
    prev: Coord | None = None
    nxt: Coord | None = None
    for e in existing_sorted:
        sm = _time_to_min(e.start_time)
        if sm < at_min:
            prev = (e.lat, e.lng)
        elif sm > at_min and nxt is None:
            nxt = (e.lat, e.lng)
    return prev, nxt


def _find_current_placement(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    *,
    patient_id: UUID,
    patient_coord: Coord,
    weekday: int,
    pfv_start: time,
    config: SchedulingConfig | None,
) -> _CurrentPlacement | None:
    """対象患者の PFV (weekday) が当週どのコースに配置されているかを特定し限界コストを算出.

    当週 visit が見つからない (未展開等) 場合は None (評価不能として skip).
    weekday 内に複数 visit がある場合は start_time が PFV と一致する行を優先する.
    """
    matched: tuple[_CourseBucket, object] | None = None
    for (_oid, wd, _code), bucket in buckets.items():
        if wd != weekday:
            continue
        for v in bucket.visits:
            if v.patient_id != patient_id:
                continue
            if matched is None or v.start_time == pfv_start:
                matched = (bucket, v)
            if v.start_time == pfv_start:
                break
    if matched is None:
        return None
    bucket, visit = matched
    existing = _bucket_existing_excluding(bucket, patient_id)
    prev, nxt = _neighbors_at(existing, _time_to_min(visit.start_time))  # type: ignore[attr-defined]
    marginal_min, marginal_km = compute_marginal_cost(
        patient_coord, prev, nxt, config=config
    )
    return _CurrentPlacement(
        bucket=bucket,
        office_code=bucket.office_code,
        start_time=visit.start_time,  # type: ignore[attr-defined]
        end_time=visit.end_time,  # type: ignore[attr-defined]
        marginal_min=marginal_min,
        marginal_km=marginal_km,
    )


def _staff_warnings_for_bucket(
    bucket: _CourseBucket, sex_restriction: str | None
) -> list[str]:
    """候補コースのスタッフ実態警告 (P0-1 の 3 コード). 除外はせず注意喚起のみ."""
    warnings: list[str] = []
    if bucket.assigned_staff_id is None:
        warnings.append(_WARN_STAFF_UNASSIGNED)
    if bucket.staff_absent:
        warnings.append(_WARN_STAFF_ABSENT)
    if _staff_sex_mismatch(bucket.staff_sex, sex_restriction):
        warnings.append(_WARN_STAFF_SEX_MISMATCH)
    return warnings


def _build_changes(
    *,
    current_weekday: int,
    current_start: time,
    current_course_label: str,
    current_staff_name: str | None,
    cand_weekday: int,
    cand_start: time,
    cand_course_label: str,
    cand_staff_name: str | None,
) -> tuple[list[str], list[str]]:
    """曜日 / 時刻 / コース / 担当の差分を日本語で組み立てる."""
    changes: list[str] = []
    unchanged: list[str] = []

    def _emit(label: str, cur: str, nxt: str) -> None:
        if cur == nxt:
            unchanged.append(f"{label}: {cur}")
        else:
            changes.append(f"{label}: {cur} → {nxt}")

    _emit("曜日", _WEEKDAY_JA[current_weekday], _WEEKDAY_JA[cand_weekday])
    _emit("時刻", _fmt_hhmm(current_start), _fmt_hhmm(cand_start))
    _emit("コース", current_course_label, cand_course_label)
    _emit(
        "担当",
        current_staff_name or "未割当",
        cand_staff_name or "未割当",
    )
    return changes, unchanged


# ---------------------------------------------------------------------------
# スワップ (2 患者入れ替え) 提案 — P3-②
# ---------------------------------------------------------------------------


def _ev_from_visit(v: object) -> ExistingVisit:
    """bucket.visits の 1 要素 (V2Visit) から ExistingVisit を組む."""
    return ExistingVisit(
        start_time=v.start_time,  # type: ignore[attr-defined]
        end_time=v.end_time,  # type: ignore[attr-defined]
        lat=v.lat,  # type: ignore[attr-defined]
        lng=v.lng,  # type: ignore[attr-defined]
        service_minutes=v.service_minutes,  # type: ignore[attr-defined]
        patient_id=str(v.patient_id),  # type: ignore[attr-defined]
    )


def _swap_candidates_for_pfv(
    *,
    patient: Patient,
    patient_coord: Coord,
    wx: int,
    sx: time,
    x_duration: int,
    x_movability: str,
    x_day_flexible: bool,
    x_occupied_weekdays: set[int],
    current: _CurrentPlacement,
    current_course_label: str,
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    pfv_by_pw: dict[tuple[UUID, int], PatientFixedVisit],
    swap_dismissed: set[tuple[UUID, int]],
    config: SchedulingConfig | None,
) -> list[ImprovementCandidateData]:
    """X の 1 PFV について、同一 office の他患者 Y の枠との入れ替え候補を列挙する.

    双方向 feasibility (X が Y の位置に自分の duration で置け、かつ Y が X の位置に
    自分の duration で置ける) を ``_find_conflict`` で相互整合に判定し、閾値超の
    combined delta を持つスワップのみ返す (最終的な件数上限は呼出側で適用).

    others の相互整合 (設計原則 N-1): 同一バケット内スワップでは、X の新位置判定に
    「Y を X の旧位置に置いた仮想 visit」を含め、Y の新位置判定に「X を Y の旧位置に
    置いた仮想 visit」を含める. 90 分占有・移動バッファーは ``_find_conflict`` /
    ``compute_marginal_cost`` の正典をそのまま使う.
    """
    # _find_conflict は config 必須 (非 optional). None のときは全既定を注入
    # (None フォールバックと同一物差し = 移動/バッファー既定値).
    eff_config = config if config is not None else DEFAULT_SCHEDULING_CONFIG

    # X の現在枠が swap 却下済みなら、この PFV 起点のスワップは一切出さない.
    if (patient.id, wx) in swap_dismissed:
        return []

    sx_min = _time_to_min(sx)
    out: list[ImprovementCandidateData] = []

    for (office_id_y, wy, course_code_y), bucket_y in buckets.items():
        # 同一 office のバケットのみ交換候補にする.
        if office_id_y != current.bucket.office_id:
            continue
        same_bucket = bucket_y is current.bucket
        weekday_changes = wy != wx

        for vy in bucket_y.visits:
            y_pid = vy.patient_id  # type: ignore[attr-defined]
            if y_pid == patient.id:
                continue
            # Y の可動域 / pin は Y の PFV から判定 (PFV 基準). 無ければ保守的に skip.
            pfv_y = pfv_by_pw.get((y_pid, wy))
            if pfv_y is None or pfv_y.is_pinned or pfv_y.movability == "locked":
                continue
            y_movability = pfv_y.movability
            y_duration = vy.service_minutes  # type: ignore[attr-defined]
            sy = vy.start_time  # type: ignore[attr-defined]
            sy_min = _time_to_min(sy)
            y_coord: Coord = (vy.lat, vy.lng)  # type: ignore[attr-defined]

            # 曜日跨ぎスワップ: 移動先曜日に既に PFV を持つ患者は apply 時に 422 になるため
            # 候補生成前に除外する (デッドエンド提案防止 / MEDIUM-1).
            # - X が移動先 (wy) に PFV を持つ → X は wy を既に占有している.
            # - Y が移動先 (wx) に PFV を持つ → Y は wx を既に占有している.
            if weekday_changes and wy in x_occupied_weekdays:
                continue
            if weekday_changes and (y_pid, wx) in pfv_by_pw:
                continue

            # 双方の movability 尊重: 曜日が変わる側は day_flexible 必須.
            if weekday_changes:
                if not x_day_flexible or y_movability != "day_flexible":
                    continue
                x_conf = False
                y_conf = False
            else:
                x_conf = x_movability == "unknown"
                y_conf = y_movability == "unknown"

            # Y 側の swap 却下指紋も尊重.
            if (y_pid, wy) in swap_dismissed:
                continue

            # ---- feasibility (相互整合の others 構築) ----
            others_x = [
                _ev_from_visit(v)
                for v in bucket_y.visits
                if v.patient_id not in (patient.id, y_pid)  # type: ignore[attr-defined]
            ]
            others_y = [
                _ev_from_visit(v)
                for v in current.bucket.visits
                if v.patient_id not in (patient.id, y_pid)  # type: ignore[attr-defined]
            ]
            if same_bucket:
                # Y は X の旧位置 (sx) へ移る → X の新位置判定 others に Y 仮想を追加.
                others_x.append(
                    ExistingVisit(
                        start_time=sx,
                        end_time=_add_minutes(sx, y_duration),
                        lat=y_coord[0],
                        lng=y_coord[1],
                        service_minutes=y_duration,
                        patient_id=str(y_pid),
                    )
                )
                # X は Y の旧位置 (sy) へ移る → Y の新位置判定 others に X 仮想を追加.
                others_y.append(
                    ExistingVisit(
                        start_time=sy,
                        end_time=_add_minutes(sy, x_duration),
                        lat=patient_coord[0],
                        lng=patient_coord[1],
                        service_minutes=x_duration,
                        patient_id=str(patient.id),
                    )
                )

            proposed_x = ExistingVisit(
                start_time=sy,
                end_time=_add_minutes(sy, x_duration),
                lat=patient_coord[0],
                lng=patient_coord[1],
                service_minutes=x_duration,
                patient_id=str(patient.id),
            )
            proposed_y = ExistingVisit(
                start_time=sx,
                end_time=_add_minutes(sx, y_duration),
                lat=y_coord[0],
                lng=y_coord[1],
                service_minutes=y_duration,
                patient_id=str(y_pid),
            )
            if _find_conflict(proposed_x, others_x, config=eff_config) is not None:
                continue
            if _find_conflict(proposed_y, others_y, config=eff_config) is not None:
                continue

            # ---- delta = (X現在 + Y現在) − (X新 + Y新) ----
            # Y 現在の限界コスト (現状: bucket_y から Y のみ除外, X は残す).
            y_cur_existing = sorted(
                (
                    _ev_from_visit(v)
                    for v in bucket_y.visits
                    if v.patient_id != y_pid  # type: ignore[attr-defined]
                ),
                key=lambda e: _time_to_min(e.start_time),
            )
            yc_prev, yc_nxt = _neighbors_at(y_cur_existing, sy_min)
            y_cur_min, y_cur_km = compute_marginal_cost(
                y_coord, yc_prev, yc_nxt, config=config
            )
            # X 新 (sy, bucket_y): others_x の隣接.
            ox_sorted = sorted(others_x, key=lambda e: _time_to_min(e.start_time))
            xn_prev, xn_nxt = _neighbors_at(ox_sorted, sy_min)
            x_new_min, x_new_km = compute_marginal_cost(
                patient_coord, xn_prev, xn_nxt, config=config
            )
            # Y 新 (sx, bucket_x): others_y の隣接.
            oy_sorted = sorted(others_y, key=lambda e: _time_to_min(e.start_time))
            yn_prev, yn_nxt = _neighbors_at(oy_sorted, sx_min)
            y_new_min, y_new_km = compute_marginal_cost(
                y_coord, yn_prev, yn_nxt, config=config
            )

            delta_min = (current.marginal_min + y_cur_min) - (x_new_min + y_new_min)
            delta_km = (current.marginal_km + y_cur_km) - (x_new_km + y_new_km)
            if delta_min < IMPROVEMENT_THRESHOLD_MIN:
                continue

            cand_label = _course_label(bucket_y.office_code, course_code_y)
            changes, unchanged = _build_changes(
                current_weekday=wx,
                current_start=sx,
                current_course_label=current_course_label,
                current_staff_name=current.bucket.staff_name,
                cand_weekday=wy,
                cand_start=sy,
                cand_course_label=cand_label,
                cand_staff_name=bucket_y.staff_name,
            )
            out.append(
                ImprovementCandidateData(
                    kind="swap",
                    target_weekday=wx,
                    current_office_id=current.bucket.office_id,
                    current_weekday=wx,
                    current_start=sx,
                    current_end=current.end_time,
                    current_course_label=current_course_label,
                    current_staff_name=current.bucket.staff_name,
                    cand_office_id=office_id_y,
                    cand_office_name=office_name_by_id.get(office_id_y),
                    cand_weekday=wy,
                    cand_start=sy,
                    cand_end=_add_minutes(sy, x_duration),
                    cand_course_code=course_code_y,
                    cand_course_label=cand_label,
                    cand_staff_name=bucket_y.staff_name,
                    delta_minutes=delta_min,
                    delta_km=round(delta_km, 2),
                    staff_warnings=_staff_warnings_for_bucket(
                        bucket_y, patient.sex_restriction
                    ),
                    requires_patient_confirmation=x_conf,
                    changes=changes,
                    unchanged=unchanged,
                    swap_counterpart_patient_id=y_pid,
                    swap_counterpart_name=vy.patient_name,  # type: ignore[attr-defined]
                    swap_counterpart_current_weekday=wy,
                    swap_counterpart_current_start=sy,
                    swap_counterpart_new_weekday=wx,
                    swap_counterpart_new_start=sx,
                    swap_counterpart_requires_confirmation=y_conf,
                )
            )
    return out


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


async def find_improvement_candidates(
    db: AsyncSession,
    *,
    patient: Patient,
    iso_year: int,
    iso_week: int,
    config: SchedulingConfig | None = None,
) -> tuple[list[ImprovementCandidateData], FilteredSummaryData]:
    """対象患者の各 PFV について改善提案を算出する.

    read-only: DB は読むだけ.

    Returns:
        ``(suggestions, filtered_summary)``. suggestions は delta 降順・最大
        ``MAX_SUGGESTIONS_PER_PATIENT`` 件. filtered_summary は「黙って消さない」内訳.
    """
    summary = FilteredSummaryData()

    if patient.lat is None or patient.lng is None:
        return [], summary
    patient_coord: Coord = (float(patient.lat), float(patient.lng))

    # 当週の実 Visit をコース単位に集計 (propose-slots と同じローダ. 全拠点対象).
    buckets, office_name_by_id, _office_code_by_id = await load_week_course_buckets(
        db, iso_year=iso_year, iso_week=iso_week, office_ids=[]
    )

    # 対象患者の PFV (通常週・slot_index=0).
    pfvs = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.slot_index == 0,
            )
        )
    ).all()

    # 却下記憶 (未失効) の指紋集合 (kind, target_weekday).
    now = datetime.now()
    dismissals = (
        await db.scalars(
            select(SuggestionDismissal).where(
                SuggestionDismissal.patient_id == patient.id
            )
        )
    ).all()
    dismissed_fp: set[tuple[str, int]] = {
        (d.kind, d.target_weekday)
        for d in dismissals
        if d.expires_at is None or _as_naive(d.expires_at) > now
    }

    # スワップ却下記憶 (kind='swap') は相手患者 Y の分も要るため全患者ぶんを読む.
    # 指紋 = (patient_id, target_weekday=現在枠曜日). 未失効のみ.
    swap_dismissal_rows = (
        await db.scalars(
            select(SuggestionDismissal).where(SuggestionDismissal.kind == "swap")
        )
    ).all()
    swap_dismissed: set[tuple[UUID, int]] = {
        (d.patient_id, d.target_weekday)
        for d in swap_dismissal_rows
        if d.expires_at is None or _as_naive(d.expires_at) > now
    }

    # スワップ相手 Y の可動域 / pin 判定用: バケットに現れる全患者の PFV
    # (通常週・slot0) を (patient_id, weekday) で引けるよう 1 クエリでロードする.
    bucket_pids: set[UUID] = {
        v.patient_id for bucket in buckets.values() for v in bucket.visits
    }
    pfv_by_pw: dict[tuple[UUID, int], PatientFixedVisit] = {}
    if bucket_pids:
        cand_pfvs = (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id.in_(bucket_pids),
                    PatientFixedVisit.mode == "normal",
                    PatientFixedVisit.slot_index == 0,
                )
            )
        ).all()
        for row in cand_pfvs:
            pfv_by_pw[(row.patient_id, row.weekday)] = row

    # 昼休み window 算出パラメータ (propose-slots と同一. config 優先, 既定は module 定数).
    lunch_duration = (
        config.lunch_duration_min if config is not None else _LUNCH_DURATION_PREFERRED
    )
    lunch_window_start = (
        config.lunch_window_start if config is not None else _LUNCH_EARLIEST_START
    )
    lunch_window_end = config.lunch_window_end if config is not None else _LUNCH_LATEST_END

    # 対象患者 X が占有している全曜日セット (multi-PFV デッドエンド防止 / MEDIUM-1).
    x_occupied_weekdays: set[int] = {pfv.weekday for pfv in pfvs}

    candidates: list[ImprovementCandidateData] = []
    swap_candidates: list[ImprovementCandidateData] = []

    for pfv in pfvs:
        weekday = pfv.weekday

        # movability / is_pinned の除外 (設計書 §2.2).
        if pfv.is_pinned:
            summary.pinned += 1
            continue
        if pfv.movability == "locked":
            summary.locked += 1
            continue
        movability = pfv.movability  # 'unknown' | 'time_flexible' | 'day_flexible'
        day_change_allowed = movability == "day_flexible"
        requires_confirmation = movability == "unknown"

        # 当週の実配置 (= 現在の限界コスト). 見つからなければ評価不能で skip.
        current = _find_current_placement(
            buckets,
            patient_id=patient.id,
            patient_coord=patient_coord,
            weekday=weekday,
            pfv_start=pfv.start_time,
            config=config,
        )
        if current is None:
            # 当週 visit が未展開等で評価不能 (N-6 で明示する).
            summary.no_current_visit += 1
            continue
        current_course_label = _course_label(
            current.office_code, current.bucket.course_code
        )

        # 候補走査用の Candidate. movability が可否の権威なので time_type は自由
        # (None) にして営業枠内を素直に探索する (固定希望に縛らない).
        cand = Candidate(
            lat=patient_coord[0],
            lng=patient_coord[1],
            service_minutes=pfv.duration_min,
            time_type=None,
            preferred_start=None,
            preferred_end=None,
            patient_id=str(patient.id),
        )

        # 却下指紋に一致する kind は先に抑制カウント (生成前).
        time_dismissed = ("time_change", weekday) in dismissed_fp
        day_dismissed = ("day_change", weekday) in dismissed_fp
        if time_dismissed:
            summary.dismissed += 1
        if day_change_allowed and day_dismissed:
            summary.dismissed += 1

        for (office_id, wd, course_code), bucket in buckets.items():
            existing = _bucket_existing_excluding(bucket, patient.id)
            lunch = compute_lunch_window(
                bucket.visits,
                warnings=None,
                weekday=wd,
                duration=lunch_duration,
                window_start=lunch_window_start,
                window_end=lunch_window_end,
            )
            slots = find_available_slots_for_candidate(
                existing,
                cand,
                lunch_window=lunch,
                weekday=wd,
                config=config,
            )
            if not slots:
                continue

            is_same_weekday = wd == weekday
            kind = "time_change" if is_same_weekday else "day_change"

            for slot in slots:
                # 現在位置そのもの (同拠点・同曜日・同コース・同時刻) は候補にしない.
                if (
                    is_same_weekday
                    and office_id == current.bucket.office_id
                    and course_code == current.bucket.course_code
                    and slot.start == current.start_time
                ):
                    continue

                prev, nxt = _neighbors_at(existing, _time_to_min(slot.start))
                cand_min, cand_km = compute_marginal_cost(
                    patient_coord, prev, nxt, config=config
                )
                delta_min = current.marginal_min - cand_min
                delta_km = current.marginal_km - cand_km

                # 曜日変更が許されない movability の場合、閾値超の改善は
                # day_restricted として抑制カウント (黙って消さない).
                if kind == "day_change" and not day_change_allowed:
                    if delta_min >= IMPROVEMENT_THRESHOLD_MIN:
                        summary.day_restricted += 1
                    continue

                # 却下指紋一致 (この kind は上で 1 回カウント済) → 生成しない.
                if kind == "time_change" and time_dismissed:
                    continue
                if kind == "day_change" and day_dismissed:
                    continue

                if delta_min < IMPROVEMENT_THRESHOLD_MIN:
                    summary.below_threshold += 1
                    continue

                cand_label = _course_label(bucket.office_code, course_code)
                changes, unchanged = _build_changes(
                    current_weekday=weekday,
                    current_start=current.start_time,
                    current_course_label=current_course_label,
                    current_staff_name=current.bucket.staff_name,
                    cand_weekday=wd,
                    cand_start=slot.start,
                    cand_course_label=cand_label,
                    cand_staff_name=bucket.staff_name,
                )
                candidates.append(
                    ImprovementCandidateData(
                        kind=kind,
                        target_weekday=weekday,
                        current_office_id=current.bucket.office_id,
                        current_weekday=weekday,
                        current_start=current.start_time,
                        current_end=current.end_time,
                        current_course_label=current_course_label,
                        current_staff_name=current.bucket.staff_name,
                        cand_office_id=office_id,
                        cand_office_name=office_name_by_id.get(office_id),
                        cand_weekday=wd,
                        cand_start=slot.start,
                        cand_end=slot.end,
                        cand_course_code=course_code,
                        cand_course_label=cand_label,
                        cand_staff_name=bucket.staff_name,
                        delta_minutes=delta_min,
                        delta_km=round(delta_km, 2),
                        staff_warnings=_staff_warnings_for_bucket(
                            bucket, patient.sex_restriction
                        ),
                        requires_patient_confirmation=(
                            requires_confirmation and kind == "time_change"
                        ),
                        changes=changes,
                        unchanged=unchanged,
                    )
                )

        # ---- スワップ (2 患者入れ替え) 提案 (P3-②). move 提案には一切影響しない ----
        swap_candidates.extend(
            _swap_candidates_for_pfv(
                patient=patient,
                patient_coord=patient_coord,
                wx=weekday,
                sx=current.start_time,
                x_duration=pfv.duration_min,
                x_movability=movability,
                x_day_flexible=day_change_allowed,
                x_occupied_weekdays=x_occupied_weekdays,
                current=current,
                current_course_label=current_course_label,
                buckets=buckets,
                office_name_by_id=office_name_by_id,
                pfv_by_pw=pfv_by_pw,
                swap_dismissed=swap_dismissed,
                config=config,
            )
        )

    # スワップは患者あたり最大 MAX_SWAP_SUGGESTIONS_PER_PATIENT 件を delta 降順で採り、
    # 既存 move 提案の枠 (MAX_SUGGESTIONS_PER_PATIENT) に混ぜる. スワップが 0 件のとき
    # move の出力・順位はスワップ無効時と完全に同一 (追加のみ).
    swap_candidates.sort(
        key=lambda c: (-c.delta_minutes, -c.delta_km, _time_to_min(c.cand_start))
    )
    candidates.extend(swap_candidates[:MAX_SWAP_SUGGESTIONS_PER_PATIENT])

    # delta (分) 降順 → 同点は (距離降順, 早い時刻) で安定化. 患者あたり最大 N 件.
    candidates.sort(
        key=lambda c: (-c.delta_minutes, -c.delta_km, _time_to_min(c.cand_start))
    )
    return candidates[:MAX_SUGGESTIONS_PER_PATIENT], summary


def _as_naive(dt: datetime) -> datetime:
    """tz-aware / naive 混在に備え naive (UTC 相当) に揃えて比較する."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def weekday_code(weekday: int) -> str:
    """0=Mon..6=Sun を曜日コードに変換する (endpoint 用)."""
    return _WEEKDAY_CODE[weekday]


__all__ = [
    "IMPROVEMENT_THRESHOLD_MIN",
    "MAX_SUGGESTIONS_PER_PATIENT",
    "FilteredSummaryData",
    "ImprovementCandidateData",
    "compute_marginal_cost",
    "find_improvement_candidates",
    "weekday_code",
]
