"""改善提案エンジン (P2-B).

配置済み患者の各固定枠 (PFV) について、「今の位置」と「入れ替え候補の位置」の
**限界コスト** を比べ、週あたりの移動 (+バッファー) / 距離を縮められる移動先を
提案する純ロジック + DB 集約層.

設計書: docs/plans/p2-improvement-mvp-design.md §2.

方針 (Phase 0-1 / propose-slots の資産を再利用. コピー禁止):
  - 限界コスト = **コース合計 (travel+buffer) の差** (W3 で厳密計算化:
    ``compute_exact_marginal``)。同住所・同時刻ペアの共有移動を誤計上しない。
    距離/移動/バッファーは ``proposal_solver`` の ``_travel_buffer_between`` /
    ``_is_same_address`` と ``auto_allocator_v2`` の ``haversine_km`` をそのまま使う
    (= 健康診断 / 自動割当と同一物差し)。
  - 候補列挙は propose-slots と同じ週バケット (``load_week_course_buckets``) に対し、
    **自分の visit を除いた** existing で ``find_available_slots_for_candidate`` を呼ぶ.
  - スタッフ実態警告は P0-1 (propose-slots) の bucket 情報を流用する.
  - 計算ベースは PFV (恒久パターン). 当週 Visit ベースは Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    _extract_weekly_entries,
    _parse_hhmm,
    compute_lunch_window,
    haversine_km,
)
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG, SchedulingConfig
from app.services.scheduling.pfv_validator import _find_conflict
from app.services.scheduling.proposal_solver import (
    Candidate,
    ExistingVisit,
    _is_same_address,
    _time_type_allows,
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

# P4-A 設計メモ: 希望訪問スケジュール (within_preference) 候補の優遇は「並び順のみ」で
# 実現する. sort キーの第 1 要素に ``not within_preference`` を置くことで希望内を先頭へ.
# delta 自体は改変しない. 優遇度は改善閾値 (10 分) と同オーダー相当の意図だが、定数化は
# 行わず効果量の比較ではなく純粋な順位付けとして実装している.

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
    """限界コスト (分, km) を返す (隣接 2 点による近似式).

    ``marginal = travel(prev→target) + travel(target→next) − travel(prev→next)``.
    先頭 (prev=None) / 末尾 (next=None) は該当辺のみ (無い辺は 0). 同住所は 0.

    Note (W3 修正): 同時刻開始の訪問 (同住所ペア等) があると prev/next の推定が
    実態とずれ、効果を過大/過少計上する盲点があるため、提案生成の delta 算出は
    本関数ではなく **厳密計算** (``compute_exact_marginal`` = コース合計の差) を
    使うこと。本関数は既存 API 互換のため残す。

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


def course_travel_buffer_total(
    evs: list[ExistingVisit], *, config: SchedulingConfig | None = None
) -> tuple[int, float]:
    """コース内訪問列の travel+buffer 合計 (分) と直線距離合計 (km) を返す.

    schedule_health `_compute_course_metrics` と同一規約:
    start 昇順に整列した連続ペアごとに、同住所 (<=100m) は 0/0、異住所は
    ``_travel_buffer_between`` (移動 + バッファー) と ``haversine_km``。
    同時刻開始のペア (同住所 2 名連続訪問等) も「隣接ペア」として自然に扱われる。
    """
    sv = sorted(evs, key=lambda e: _time_to_min(e.start_time))
    minutes = 0
    km = 0.0
    for i in range(1, len(sv)):
        a = sv[i - 1]
        b = sv[i]
        minutes += _travel_buffer_between(a.lat, a.lng, b.lat, b.lng, config=config)
        if not _is_same_address(a.lat, a.lng, b.lat, b.lng):
            km += haversine_km(a.lat, a.lng, b.lat, b.lng)
    return minutes, km


def compute_exact_marginal(
    existing: list[ExistingVisit],
    member: ExistingVisit,
    *,
    config: SchedulingConfig | None = None,
) -> tuple[int, float]:
    """厳密な限界コスト = 「member を加えたコース合計 − 加えないコース合計」.

    W3 修正 (実データ由来): 同住所・同時刻ペアの片割れを動かす提案で、共有している
    移動 (前の患者→同住所) を「動かせば浮く」と誤計上する盲点があった (近似式は
    同時刻の相方を隣とみなさないため)。コース合計の差で定義すれば、提案の delta と
    健康診断 (before/after) の改善量が数学的に一致する。

    Args:
        existing: member を **含まない** コース内の既存訪問列.
        member: 評価対象の訪問 (現在枠 or 候補枠).
    """
    base_min, base_km = course_travel_buffer_total(existing, config=config)
    with_min, with_km = course_travel_buffer_total([*existing, member], config=config)
    return with_min - base_min, with_km - base_km


# ---------------------------------------------------------------------------
# 希望訪問スケジュール (weekly_pattern) 範囲内判定 — P4-A
# ---------------------------------------------------------------------------


def _slot_within_preference(
    patient: Patient,
    weekday: int,
    start_time: time,
    end_time: time,
    *,
    config: SchedulingConfig | None = None,
) -> bool:
    """候補スロットが患者の希望訪問スケジュール (``weekly_pattern``) の範囲内かを判定する.

    「希望訪問スケジュール」= 患者から聞き取り済みの受け入れ可能範囲 (= 余白). 候補が
    この範囲に収まるなら患者は元々 OK と言っているため、movability や曜日跨ぎに関係なく
    患者確認不要で提案・スワップの権威となる (P4-A 3 層のうちの層 2).

    判定規約は propose-slots / auto_allocator と同一 (コピー禁止・正典再利用):
      - weekly_pattern の読み出しは ``_extract_weekly_entries`` (auto_allocator_v2) を再利用.
        リスト形式 / サマリ形式・仮開始時刻の解釈もそのまま踏襲する.
      - 該当 weekday の entry の time_type 制約充足は ``_time_type_allows``
        (proposal_solver) を再利用する. ``_time_type_allows`` は ``Candidate`` を取るため、
        entry から薄い ``Candidate`` アダプタを組んで正典判定に委譲する (出典: proposal_solver
        :310-357). 意味論は「固定=preferred_start 一致 / 時間帯=[preferred_start,
        preferred_end] 内に開始 / 午前=end<=12:00 / 午後=start>=13:00 / 終日・None=その曜日なら OK」.

    entries が空 / 該当 weekday の entry 無し → False (層 3 = 現行 movability ルールに
    フォールバックし、weekly_pattern 未登録患者の挙動は完全に不変).

    Args:
        patient: 判定対象の患者 (``weekly_pattern`` を読む). ``_extract_weekly_entries``
            が ``Patient`` を要求するため、``weekly_pattern`` 単体ではなく患者オブジェクトを取る.
        weekday: 候補スロットの曜日 (0=Mon..6=Sun).
        start_time: 候補スロットの開始時刻.
        end_time: 候補スロットの終了時刻.
        config: 仮開始時刻・営業時間の注入用 (propose-slots と同一の config 規約).
    """
    for wd, pref_start, _sm, time_type, _ps_raw, pe_raw in _extract_weekly_entries(
        patient, config=config
    ):
        if wd != weekday:
            continue
        # entry から time_type 判定用の薄い Candidate を組み、正典 _time_type_allows に委譲.
        # 位置系フィールド (lat/lng/service_minutes) は time_type 判定に無関係のためダミー.
        adapter = Candidate(
            lat=0.0,
            lng=0.0,
            service_minutes=0,
            time_type=time_type,
            preferred_start=pref_start,
            preferred_end=_parse_hhmm(pe_raw),
        )
        if _time_type_allows(adapter, start_time, end_time, config=config):
            return True
    return False


# ---------------------------------------------------------------------------
# 内部データ構造 (API schema に詰める前の内部表現)
# ---------------------------------------------------------------------------


@dataclass
class CourseSnapshotVisitData:
    """コーススナップショットの 1 訪問 (提案生成時点の状態)."""

    patient_id: UUID
    patient_name: str
    start_time: time
    end_time: time


@dataclass
class CourseSnapshotData:
    """提案が触るコースのスナップショット (タイムライン表示用・UI 統一).

    FE は visits を start 昇順で描画し、対象患者 (と swap 相手) に一致する行を
    ハイライト・移動矢印表示する (範囲最適化と同一の見せ方)。
    """

    office_id: UUID
    weekday: int
    course_code: str
    course_label: str
    staff_name: str | None
    visits: list[CourseSnapshotVisitData] = field(default_factory=list)


def _cached_snapshot(
    cache: dict[tuple[UUID, int, str], CourseSnapshotData], bucket: _CourseBucket
) -> CourseSnapshotData:
    """バケット単位でスナップショットをキャッシュする (候補数が多くても 1 回だけ構築)."""
    key = (bucket.office_id, bucket.weekday, bucket.course_code)
    snap = cache.get(key)
    if snap is None:
        snap = snapshot_course_bucket(bucket)
        cache[key] = snap
    return snap


def snapshot_course_bucket(bucket: _CourseBucket) -> CourseSnapshotData:
    """バケットの現在の訪問列をスナップショットする (start 昇順)."""
    return CourseSnapshotData(
        office_id=bucket.office_id,
        weekday=bucket.weekday,
        course_code=bucket.course_code,
        course_label=_course_label(bucket.office_code, bucket.course_code),
        staff_name=bucket.staff_name,
        visits=[
            CourseSnapshotVisitData(
                patient_id=v.patient_id,
                patient_name=v.patient_name,
                start_time=v.start_time,
                end_time=v.end_time,
            )
            for v in sorted(bucket.visits, key=lambda x: _time_to_min(x.start_time))
        ],
    )


def _position_phrase(snap: CourseSnapshotData, exclude_pid: UUID, at: time) -> str:
    """スナップショット内での位置を「◯◯様と△△様の間」等の日本語句にする (H2 理由文)."""
    prev_name: str | None = None
    next_name: str | None = None
    at_min = _time_to_min(at)
    for v in snap.visits:  # start 昇順
        if v.patient_id == exclude_pid:
            continue
        if _time_to_min(v.start_time) <= at_min:
            prev_name = v.patient_name
        elif next_name is None:
            next_name = v.patient_name
    if prev_name and next_name:
        return f"{prev_name}様と{next_name}様の間"
    if prev_name:
        return f"{prev_name}様の後"
    if next_name:
        return f"{next_name}様の前"
    return "単独配置"


def build_move_reason(
    src: CourseSnapshotData,
    dst: CourseSnapshotData | None,
    pid: UUID,
    old_start: time,
    new_start: time,
    delta_min: int,
) -> str:
    """move 提案の理由文 (H2): 現在の位置→移動先の位置と短縮量を 1 文で説明する.

    「原因がこうだから、対策はこう、効果はこれだけ」を管理者向けに言語化する
    (docs/plans/health-prescription-design.md §2.1)。
    """
    cur_pos = _position_phrase(src, pid, old_start)
    to_snap = dst if dst is not None else src
    new_pos = _position_phrase(to_snap, pid, new_start)
    to_label = f"{to_snap.course_label}の" if dst is not None else ""
    return (
        f"現在の{cur_pos}への配置で回り道が生じています。"
        f"{to_label}{new_pos}へ移すと移動が −{delta_min}分/週 短縮できます。"
    )


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
    # P4-A: 候補枠 (swap では X の新位置) が患者の希望訪問スケジュール範囲内なら True.
    within_preference: bool = False
    # kind='swap' のときのみ設定される相手患者 Y の情報 (P3-②). それ以外は None.
    swap_counterpart_patient_id: UUID | None = None
    swap_counterpart_name: str | None = None
    swap_counterpart_current_weekday: int | None = None
    swap_counterpart_current_start: time | None = None
    swap_counterpart_new_weekday: int | None = None
    swap_counterpart_new_start: time | None = None
    swap_counterpart_requires_confirmation: bool = False
    # P4-A: Y の新位置が Y 自身の希望訪問スケジュール範囲内なら True (swap のみ).
    swap_counterpart_within_preference: bool = False
    # UI 統一: 移動元 / 移動先コースのスナップショット (同一コース内は destination=None).
    # 改善提案 (patient 詳細) の生成経路で populate。scope_optimizer は模擬状態から
    # step 単位で別途キャプチャするため、そちらの経路では None のまま。
    source_course: CourseSnapshotData | None = None
    destination_course: CourseSnapshotData | None = None
    # H2: 理由文 (「原因→対策→効果」の 1 文)。move は build_move_reason で生成、
    # swap は簡易文。scope_optimizer の move はステップ確定時に付与。
    reason: str | None = None


@dataclass
class FilteredSummaryData:
    """提案を出さなかった内訳 (N-6). 内部表現.

    各カウンタの単位は ImprovementFilteredSummary の description を参照.
    カテゴリ間は重複しない (1 PFV / 候補は 1 カテゴリのみに計上).

    P4-A: ``day_restricted`` は「希望外 (within_preference=False) かつ movability が同曜日限定」
    の候補のみを数える. 希望訪問スケジュール範囲内の曜日跨ぎ候補は movability に関係なく
    提案されるため day_restricted には計上されない (= 意味が「希望外の曜日制限」に狭まった).

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


def _bucket_existing_excluding(bucket: _CourseBucket, patient_id: UUID) -> list[ExistingVisit]:
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
    # W3: 厳密計算 (コース合計の差)。同時刻ペアの共有移動を誤計上しない.
    self_ev = ExistingVisit(
        start_time=visit.start_time,  # type: ignore[attr-defined]
        end_time=visit.end_time,  # type: ignore[attr-defined]
        lat=patient_coord[0],
        lng=patient_coord[1],
        service_minutes=visit.service_minutes,  # type: ignore[attr-defined]
        patient_id=str(patient_id),
    )
    marginal_min, marginal_km = compute_exact_marginal(existing, self_ev, config=config)
    return _CurrentPlacement(
        bucket=bucket,
        office_code=bucket.office_code,
        start_time=visit.start_time,  # type: ignore[attr-defined]
        end_time=visit.end_time,  # type: ignore[attr-defined]
        marginal_min=marginal_min,
        marginal_km=marginal_km,
    )


def _staff_warnings_for_bucket(bucket: _CourseBucket, sex_restriction: str | None) -> list[str]:
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
    patient_by_id: dict[UUID, Patient],
    swap_dismissed: set[tuple[UUID, int]],
    config: SchedulingConfig | None,
    snapshot_cache: dict[tuple[UUID, int, str], CourseSnapshotData] | None = None,
) -> list[ImprovementCandidateData]:
    """X の 1 PFV について、同一 office の他患者 Y の枠との入れ替え候補を列挙する.

    ``snapshot_cache`` が非 None のとき、タイムライン表示用のコーススナップショットを
    候補へ付与する (scope_optimizer 経路は None = step 単位で別途キャプチャ)。

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
            y_coord: Coord = (vy.lat, vy.lng)  # type: ignore[attr-defined]

            # 曜日跨ぎスワップ: 移動先曜日に既に PFV を持つ患者は apply 時に 422 になるため
            # 候補生成前に除外する (デッドエンド提案防止 / MEDIUM-1).
            # - X が移動先 (wy) に PFV を持つ → X は wy を既に占有している.
            # - Y が移動先 (wx) に PFV を持つ → Y は wx を既に占有している.
            if weekday_changes and wy in x_occupied_weekdays:
                continue
            if weekday_changes and (y_pid, wx) in pfv_by_pw:
                continue

            # ---- P4-A 層 2: 各自の新位置が自分の希望訪問スケジュール範囲内か ----
            # X は Y の旧位置 (wy, sy) へ、Y は X の旧位置 (wx, sx) へ移る.
            within_pref_x = _slot_within_preference(
                patient, wy, sy, _add_minutes(sy, x_duration), config=config
            )
            patient_y = patient_by_id.get(y_pid)
            within_pref_y = (
                _slot_within_preference(
                    patient_y, wx, sx, _add_minutes(sx, y_duration), config=config
                )
                if patient_y is not None
                else False
            )

            # 双方の movability 尊重. ただし新位置が希望範囲内 (層 2) の側は movability に
            # 関係なく許可し患者確認も不要にする. 希望外の側は現行ルール (層 3):
            # 曜日跨ぎは day_flexible 必須 / 同曜日は unknown=要確認.
            if weekday_changes:
                x_ok = x_day_flexible or within_pref_x
                y_ok = (y_movability == "day_flexible") or within_pref_y
                if not x_ok or not y_ok:
                    continue
                # 許可される側は day_flexible (元々確認不要) か希望内 (確認不要) の
                # いずれか → 曜日跨ぎではどちらも要確認は付かない.
                x_conf = False
                y_conf = False
            else:
                x_conf = (x_movability == "unknown") and not within_pref_x
                y_conf = (y_movability == "unknown") and not within_pref_y

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

            # ---- delta = 入れ替え前後のコース合計の差 (W3: 厳密計算) ----
            # 近似式 (隣接 2 点) は同時刻ペアで誤計上するため、対象バケット全体の
            # travel+buffer 合計を before/after で直接比較する。
            x_pid_str = str(patient.id)
            y_pid_str = str(y_pid)
            bx_evs = [_ev_from_visit(v) for v in current.bucket.visits]
            if same_bucket:
                before_min, before_km = course_travel_buffer_total(bx_evs, config=config)
                base = [e for e in bx_evs if e.patient_id not in (x_pid_str, y_pid_str)]
                after_min, after_km = course_travel_buffer_total(
                    [*base, proposed_x, proposed_y], config=config
                )
            else:
                by_evs = [_ev_from_visit(v) for v in bucket_y.visits]
                bx_min, bx_km = course_travel_buffer_total(bx_evs, config=config)
                by_min, by_km = course_travel_buffer_total(by_evs, config=config)
                before_min, before_km = bx_min + by_min, bx_km + by_km
                bx_base = [e for e in bx_evs if e.patient_id != x_pid_str]
                by_base = [e for e in by_evs if e.patient_id != y_pid_str]
                ax_min, ax_km = course_travel_buffer_total([*bx_base, proposed_y], config=config)
                ay_min, ay_km = course_travel_buffer_total([*by_base, proposed_x], config=config)
                after_min, after_km = ax_min + ay_min, ax_km + ay_km

            delta_min = before_min - after_min
            delta_km = before_km - after_km
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
                    staff_warnings=_staff_warnings_for_bucket(bucket_y, patient.sex_restriction),
                    requires_patient_confirmation=x_conf,
                    within_preference=within_pref_x,
                    changes=changes,
                    unchanged=unchanged,
                    swap_counterpart_patient_id=y_pid,
                    swap_counterpart_name=vy.patient_name,  # type: ignore[attr-defined]
                    swap_counterpart_current_weekday=wy,
                    swap_counterpart_current_start=sy,
                    swap_counterpart_new_weekday=wx,
                    swap_counterpart_new_start=sx,
                    swap_counterpart_requires_confirmation=y_conf,
                    swap_counterpart_within_preference=within_pref_y,
                    # UI 統一: source=X のコース / destination=Y のコース (同一なら None).
                    source_course=(
                        _cached_snapshot(snapshot_cache, current.bucket)
                        if snapshot_cache is not None
                        else None
                    ),
                    destination_course=(
                        _cached_snapshot(snapshot_cache, bucket_y)
                        if snapshot_cache is not None and not same_bucket
                        else None
                    ),
                    # H2: swap は簡易理由文 (双方向のため move 文とは別書式).
                    reason=(
                        f"{vy.patient_name}様と枠を入れ替え、双方の回り道を"  # type: ignore[attr-defined]
                        f"同時に解消します（合計 −{delta_min}分/週）。"
                    ),
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
            select(SuggestionDismissal).where(SuggestionDismissal.patient_id == patient.id)
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
        await db.scalars(select(SuggestionDismissal).where(SuggestionDismissal.kind == "swap"))
    ).all()
    swap_dismissed: set[tuple[UUID, int]] = {
        (d.patient_id, d.target_weekday)
        for d in swap_dismissal_rows
        if d.expires_at is None or _as_naive(d.expires_at) > now
    }

    # スワップ相手 Y の可動域 / pin 判定用: バケットに現れる全患者の PFV
    # (通常週・slot0) を (patient_id, weekday) で引けるよう 1 クエリでロードする.
    bucket_pids: set[UUID] = {v.patient_id for bucket in buckets.values() for v in bucket.visits}
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

    # P4-A: swap 相手 Y の希望訪問スケジュール (weekly_pattern) 判定用に、バケットに現れる
    # 全患者の Patient を 1 クエリでロードする (X 自身も含める).
    patient_by_id: dict[UUID, Patient] = {patient.id: patient}
    if bucket_pids:
        other_patients = (
            await db.scalars(select(Patient).where(Patient.id.in_(bucket_pids - {patient.id})))
        ).all()
        for prow in other_patients:
            patient_by_id[prow.id] = prow

    # 昼休み window 算出パラメータ (propose-slots と同一. config 優先, 既定は module 定数).
    lunch_duration = config.lunch_duration_min if config is not None else _LUNCH_DURATION_PREFERRED
    lunch_window_start = config.lunch_window_start if config is not None else _LUNCH_EARLIEST_START
    lunch_window_end = config.lunch_window_end if config is not None else _LUNCH_LATEST_END

    # 対象患者 X が占有している全曜日セット (multi-PFV デッドエンド防止 / MEDIUM-1).
    x_occupied_weekdays: set[int] = {pfv.weekday for pfv in pfvs}

    candidates: list[ImprovementCandidateData] = []
    swap_candidates: list[ImprovementCandidateData] = []
    # UI 統一: タイムライン表示用のコーススナップショット (バケット単位でキャッシュ).
    snap_cache: dict[tuple[UUID, int, str], CourseSnapshotData] = {}

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
        current_course_label = _course_label(current.office_code, current.bucket.course_code)

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

        # 却下指紋フラグ. カウントはループ内の実 blocking 時に行う (per kind × PFV = 最大各 1 回).
        # P4-A 対応: 事前カウント方式では within_preference 経由の day_change 候補が
        # dismissed で阻止されたときに計上漏れが起きるため、ループ内初回 blocking 時に加算する.
        # (旧: day_change_allowed のときのみ事前カウント → within_pref 経路は条件外でカウントゼロ)
        time_dismissed = ("time_change", weekday) in dismissed_fp
        day_dismissed = ("day_change", weekday) in dismissed_fp
        _time_dismissed_counted = False  # 初回 blocking で True にする (二重計上防止)
        _day_dismissed_counted = False

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

                # W3: 厳密計算 (コース合計の差)。同時刻ペア枠 (same_address_pair 等)
                # への挿入も正しく 0 コスト評価される.
                cand_ev = ExistingVisit(
                    start_time=slot.start,
                    end_time=slot.end,
                    lat=patient_coord[0],
                    lng=patient_coord[1],
                    service_minutes=pfv.duration_min,
                    patient_id=str(patient.id),
                )
                cand_min, cand_km = compute_exact_marginal(existing, cand_ev, config=config)
                delta_min = current.marginal_min - cand_min
                delta_km = current.marginal_km - cand_km

                # P4-A 層 2: 候補が患者の希望訪問スケジュール範囲内 (= 余白) なら、movability
                # ゲートより前に権威づけする. 希望内なら曜日跨ぎでも患者確認不要で提案する.
                within_pref = _slot_within_preference(
                    patient, wd, slot.start, slot.end, config=config
                )

                # 曜日変更が許されない movability の場合、閾値超の改善は
                # day_restricted として抑制カウント (黙って消さない).
                # ただし希望内 (within_pref) の候補は movability に関係なく提案する (層 2 優先).
                if kind == "day_change" and not day_change_allowed and not within_pref:
                    if delta_min >= IMPROVEMENT_THRESHOLD_MIN:
                        summary.day_restricted += 1
                    continue

                # 却下指紋一致 → 生成しない. 初回 blocking 時のみ dismissed に加算
                # (per kind × PFV で最大 1 回 = ImprovementFilteredSummary の粒度仕様通り).
                if kind == "time_change" and time_dismissed:
                    if not _time_dismissed_counted:
                        summary.dismissed += 1
                        _time_dismissed_counted = True
                    continue
                if kind == "day_change" and day_dismissed:
                    if not _day_dismissed_counted:
                        summary.dismissed += 1
                        _day_dismissed_counted = True
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
                # UI 統一 (タイムライン) + H2 (理由文) 用のスナップショット.
                src_snap = _cached_snapshot(snap_cache, current.bucket)
                dst_snap = (
                    None
                    if (office_id, wd, course_code)
                    == (
                        current.bucket.office_id,
                        current.bucket.weekday,
                        current.bucket.course_code,
                    )
                    else _cached_snapshot(snap_cache, bucket)
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
                        staff_warnings=_staff_warnings_for_bucket(bucket, patient.sex_restriction),
                        requires_patient_confirmation=(
                            requires_confirmation and kind == "time_change" and not within_pref
                        ),
                        within_preference=within_pref,
                        changes=changes,
                        unchanged=unchanged,
                        source_course=src_snap,
                        destination_course=dst_snap,
                        reason=build_move_reason(
                            src_snap,
                            dst_snap,
                            patient.id,
                            current.start_time,
                            slot.start,
                            delta_min,
                        ),
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
                patient_by_id=patient_by_id,
                swap_dismissed=swap_dismissed,
                config=config,
                snapshot_cache=snap_cache,
            )
        )

    # スワップは患者あたり最大 MAX_SWAP_SUGGESTIONS_PER_PATIENT 件を delta 降順で採り、
    # 既存 move 提案の枠 (MAX_SUGGESTIONS_PER_PATIENT) に混ぜる. スワップが 0 件のとき
    # move の出力・順位はスワップ無効時と完全に同一 (追加のみ).
    # P4-A: 希望内 (within_preference) を最優先し、その中で delta 降順. sort キー第 1 要素
    # ``not c.within_preference`` により希望内 (True→False で先頭) を上位へ (delta は改変せず
    # 並び順のみ優遇). 以降は delta 降順 → 距離降順 → 早い時刻で安定化.
    swap_candidates.sort(
        key=lambda c: (
            not c.within_preference,
            -c.delta_minutes,
            -c.delta_km,
            _time_to_min(c.cand_start),
        )
    )
    candidates.extend(swap_candidates[:MAX_SWAP_SUGGESTIONS_PER_PATIENT])

    # 希望内優先 → delta (分) 降順 → 同点は (距離降順, 早い時刻) で安定化. 患者あたり最大 N 件.
    candidates.sort(
        key=lambda c: (
            not c.within_preference,
            -c.delta_minutes,
            -c.delta_km,
            _time_to_min(c.cand_start),
        )
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
    "CourseSnapshotData",
    "CourseSnapshotVisitData",
    "FilteredSummaryData",
    "ImprovementCandidateData",
    "build_move_reason",
    "compute_exact_marginal",
    "compute_marginal_cost",
    "course_travel_buffer_total",
    "find_improvement_candidates",
    "snapshot_course_bucket",
    "weekday_code",
]
