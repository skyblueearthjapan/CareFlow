"""詰まり解消相談エンジン (W-12d BE).

個別提案 (プール患者クリック) で **候補 0 件** になったとき、「既存の訪問を 1〜2 手
ずらせば入ります」という具体的な開通手順を、乱れ (追加移動) の小さい順に提案する
純ロジック + DB 集約層.

設計書: docs/plans/unblock-consult-design.md §2.

方針 (正典再利用・コピー禁止):
  - ブロッカー除去で対象患者が入るかの判定は propose-slots の正典
    (``compute_all_proposed_slots``) をそのまま使う (2 名体制ペアの開通判定も内部で
    自動処理される = 通常患者は単バケットソルバ / 2 名体制は W-12a のペアアンカー)。
  - ブロッカーの退避先列挙は improvement_engine の move 列挙と同じく
    ``find_available_slots_for_candidate`` (自身を除いた既存) + ``compute_exact_marginal``。
  - 退避先の確認要フィルタ (P-3) は improvement_engine / scope_optimizer と同じ D-2 規則
    (within_preference または movability が許す手のみ)。
  - state_token は scope-optimization と同一規約 (office スコープの PFV 指紋)。
  - read-only: DB は読むだけ。乱数 / dict 順序依存を持たない (全走査を明示ソート)。

設計原則 (PO 承認済み):
  - P-1: 連鎖は最大 3 手 (動かす既存訪問 ≤ 2 + 対象患者の配置 1)。
  - P-2: pinned / locked / 2 名体制 / 同住所ペアの片割れ は動かさない (会計に計上)。
    却下記憶 (suggestion_dismissals) を尊重。
  - P-3: 確認不要の手のみ (退避先は希望内 or movability が許す範囲)。
  - P-5: 各手の効果は compute_exact_marginal (分/週)。total_delta = 全手の合計。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from datetime import datetime, time
from itertools import combinations
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
    V2Visit,
    compute_lunch_window,
)
from app.services.scheduling.config import SchedulingConfig
from app.services.scheduling.constants import (
    COURSE_MAX_MINUTES,
    MAX_PATIENTS_PER_COURSE,
)
from app.services.scheduling.improvement_engine import (
    CourseSnapshotData,
    CourseSnapshotEventData,
    CourseSnapshotVisitData,
    _bucket_existing_excluding,
    _slot_within_preference,
    compute_exact_marginal,
    snapshot_course_bucket,
)
from app.services.scheduling.proposal_solver import (
    Candidate,
    ExistingVisit,
    _course_total_minutes_from_existing,
    _is_same_address,
    find_available_slots_for_candidate,
)
from app.services.scheduling.propose_slots_service import (
    CandidateInput,
    ProposedSlot,
    _CourseBucket,
    _staff_ng_mismatch,
    _to_existing_visits,
    compute_all_proposed_slots,
    load_week_course_buckets,
)
from app.services.scheduling.scope_optimizer import (
    OptimizationScope,
    compute_current_state_token,
)

# 詰まり解消プランの既定返却上限 (上位 N 件).
DEFAULT_UNBLOCK_LIMIT: int = 5


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


# ---------------------------------------------------------------------------
# 公開データ構造 (API schema に詰める前の内部表現)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnblockMove:
    """プラン内の 1 手 (既存訪問 v を退避先へずらす)."""

    patient_id: UUID
    patient_name: str
    from_weekday: int
    from_course_code: str
    from_start: time
    to_weekday: int
    to_course_code: str
    to_start: time
    to_end: time
    # 退避で増える移動 (分/週) = compute_exact_marginal(退避先) − (現位置). 正 = 乱れ増.
    delta_minutes: int
    within_preference: bool


@dataclass(frozen=True)
class UnblockInsert:
    """対象患者を配置する枠 (2 名体制なら partner_course_code に相方コース)."""

    weekday: int
    course_code: str
    start: time
    end: time
    partner_course_code: str | None = None


@dataclass(frozen=True)
class UnblockCourseSnapshotData:
    """プランが影響するコースの before/after スナップショット (W-13b).

    entries は improvement_engine の正典 ``CourseSnapshotVisitData`` と同形。
    before=現状 / after=プラン (moves 適用 + insert 配置) 後の最終状態 (共に start 昇順)。
    """

    office_name: str | None
    weekday: int
    course_code: str
    course_label: str
    before: list[CourseSnapshotVisitData]
    after: list[CourseSnapshotVisitData]
    # イベント表示 (2026-07-27): 担当スタッフのイベント. 提案の前後で不変のため 1 本.
    events: list[CourseSnapshotEventData] = field(default_factory=list)


@dataclass(frozen=True)
class UnblockPlan:
    """開通プラン 1 件 (moves を適用 → insert に対象を配置)."""

    plan_id: str
    moves: list[UnblockMove]
    insert: UnblockInsert
    total_delta_minutes: int
    moved_count: int
    # W-15: 定員起因 (capacity_full) の詰まりを「ブロッカーの他バケット退避で定員を空けて」
    # 開通したプランか (FE の「ずらす」vs「+1名」表示分岐用)。時間起因プランは False。
    # 後方互換のため既定 False。
    frees_capacity: bool = False
    # W-13b: 影響コースの before/after ((weekday, course_code) 昇順・重複排除)。既定 [].
    courses: list[UnblockCourseSnapshotData] = field(default_factory=list)


@dataclass
class UnblockUnmovableSummary:
    """動かせない事情の内訳 (N-6「黙って諦めない」). 各カウントは real-blocker 単位."""

    pinned: int = 0
    locked: int = 0
    two_staff: int = 0
    pair: int = 0
    dismissed: int = 0
    confirmation_required: int = 0


@dataclass
class UnblockResult:
    """propose-unblock の結果一式."""

    plans: list[UnblockPlan]
    unmovable_summary: UnblockUnmovableSummary
    state_token: str


# ---------------------------------------------------------------------------
# 退避先 (内部表現)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Retreat:
    weekday: int
    course_code: str
    start: time
    end: time
    delta_minutes: int
    within_preference: bool


# ---------------------------------------------------------------------------
# バケット複製 (対象visit を除いた模擬状態)
# ---------------------------------------------------------------------------


def _bucket_without(bucket: _CourseBucket, *exclude: V2Visit) -> _CourseBucket:
    """指定 visit (同一オブジェクト) を除いた浅いコピー (他フィールドは共有)."""
    excluded_ids = {id(v) for v in exclude}
    return _CourseBucket(
        office_id=bucket.office_id,
        weekday=bucket.weekday,
        course_code=bucket.course_code,
        office_code=bucket.office_code,
        staff_name=bucket.staff_name,
        course_template_id=bucket.course_template_id,
        assigned_staff_id=bucket.assigned_staff_id,
        staff_sex=bucket.staff_sex,
        staff_absent=bucket.staff_absent,
        # NG スタッフ (§6) / イベント窓: 複製で落とすと NG 除外 (PO確定 2026-08-11) と
        # イベント判定が模擬状態だけ無効化されるため必ず引き継ぐ (共に不変オブジェクト).
        ng_patient_ids=bucket.ng_patient_ids,
        event_windows=bucket.event_windows,
        visits=[v for v in bucket.visits if id(v) not in excluded_ids],
    )


def _add_retreated_visit(
    bucket: _CourseBucket, v: V2Visit, new_start: time, new_end: time
) -> _CourseBucket:
    """v を new_start/new_end に置いた浅いコピーを返す (同一バケット退避の最終状態検証用).

    ``_bucket_without(bucket, v)`` でいったん v を除いてから v を新時刻で追加する。
    これにより「ブロッカーが同バケット内の別時刻へ退避した後、対象患者が入れるか」を
    ``_fits_involving_bucket`` で再評価できる。
    """
    retreated = _dc_replace(v, start_time=new_start, end_time=new_end)
    base = _bucket_without(bucket, v)
    return _CourseBucket(
        office_id=base.office_id,
        weekday=base.weekday,
        course_code=base.course_code,
        office_code=base.office_code,
        staff_name=base.staff_name,
        course_template_id=base.course_template_id,
        assigned_staff_id=base.assigned_staff_id,
        staff_sex=base.staff_sex,
        staff_absent=base.staff_absent,
        # _bucket_without と同じく NG 集合 / イベント窓を引き継ぐ (落とすと除外が無効化).
        ng_patient_ids=base.ng_patient_ids,
        event_windows=base.event_windows,
        visits=base.visits + [retreated],
    )


def _bucket_capacity_blocked(
    bucket: _CourseBucket,
    candidate: CandidateInput,
    *,
    config: SchedulingConfig,
) -> bool:
    """対象患者にとって bucket が定員起因 (件数上限 or 分数上限) で満杯か (W-15).

    判定は proposal_solver の ``find_available_slots_for_candidate`` の容量ガード
    (``used_count < max_patients`` かつ ``used_minutes + service <= COURSE_MAX_MINUTES``) と
    厳密に同一。すなわち「そもそも am/pm 走査に入れない = capacity_full」の条件そのもの。

    定員起因の詰まりは訪問を **他バケットへ** 退避して初めて枠が空く。同一バケット内の時間
    ずらしは件数も使用分も一切変えず定員を空けないため、定員起因では退避先を他バケット限定に
    する (呼出側で ``exclude_same_bucket=True`` にする根拠)。
    """
    max_patients = config.max_patients_per_course if config is not None else MAX_PATIENTS_PER_COURSE
    if len(bucket.visits) >= max_patients:
        return True
    used_minutes = _course_total_minutes_from_existing(_to_existing_visits(bucket), config=config)
    return used_minutes + int(candidate.service_minutes) > COURSE_MAX_MINUTES


def _is_pair_half(v: V2Visit, bucket: _CourseBucket) -> bool:
    """v が同住所ペアの片割れか (同バケットに別患者の同住所訪問がある)."""
    for w in bucket.visits:
        if w.patient_id == v.patient_id:
            continue
        if _is_same_address(v.lat, v.lng, w.lat, w.lng):
            return True
    return False


# ---------------------------------------------------------------------------
# 対象患者の開通判定 (propose-slots 正典を単一 weekday スコープで再利用)
# ---------------------------------------------------------------------------


def _fits_involving_bucket(
    weekday_buckets: dict[tuple[UUID, int, str], _CourseBucket],
    candidate: CandidateInput,
    *,
    office_id: UUID,
    office_name_by_id: dict[UUID, str],
    office_code_by_id: dict[UUID, str | None],
    config: SchedulingConfig,
    bucket_code: str,
) -> ProposedSlot | None:
    """対象患者が bucket_code のコースに絡む形で入れる最良枠を返す (入れなければ None).

    ``compute_all_proposed_slots`` は delta 昇順で返すため、bucket_code に絡む先頭候補が
    最小乱れの枠になる。2 名体制候補は同関数が内部でペア (slot0+slot1) を列挙するため、
    通常患者 / 2 名体制の分岐を意識せず同一経路で判定できる。
    """
    results = compute_all_proposed_slots(
        weekday_buckets,
        office_name_by_id,
        candidate,
        office_ids=[office_id],
        office_code_by_id=office_code_by_id,
        config=config,
    )
    for ps in results:
        if ps.course_code == bucket_code or ps.partner_course_code == bucket_code:
            return ps
    return None


# ---------------------------------------------------------------------------
# 退避先列挙 (improvement_engine の move と同じ物差し / P-3 フィルタ)
# ---------------------------------------------------------------------------


def _enumerate_retreats(
    v: V2Visit,
    blocker_bucket_key: tuple[UUID, int, str],
    blocker_bucket: _CourseBucket,
    patient: Patient,
    pfv: PatientFixedVisit,
    *,
    all_buckets: dict[tuple[UUID, int, str], _CourseBucket],
    occupied_weekdays: set[int],
    dismissed_fp: set[tuple[UUID, str, int]],
    config: SchedulingConfig,
) -> tuple[list[_Retreat], bool, bool]:
    """v (患者 patient) をどこへ退避できるか列挙する (退避先は詰まりバケット以外の全コース).

    Returns:
        ``(retreats, confirmation_blocked, dismissed_blocked)``。
        retreats は P-3 を通過した確認不要の退避先。confirmation_blocked /
        dismissed_blocked は「有効な退避先が 0 件だったとき理由を分類する」ためのフラグ。
    """
    lunch_duration = config.lunch_duration_min if config is not None else _LUNCH_DURATION_PREFERRED
    lunch_window_start = config.lunch_window_start if config is not None else _LUNCH_EARLIEST_START
    lunch_window_end = config.lunch_window_end if config is not None else _LUNCH_LATEST_END

    from_weekday = blocker_bucket.weekday
    patient_coord = (float(patient.lat), float(patient.lng))  # 呼出側で lat/lng None を除外済.

    # 現位置の限界コスト (退避で増える分の基準).
    existing_src = _bucket_existing_excluding(blocker_bucket, patient.id)
    self_ev = ExistingVisit(
        start_time=v.start_time,
        end_time=v.end_time,
        lat=patient_coord[0],
        lng=patient_coord[1],
        service_minutes=v.service_minutes,
        patient_id=str(patient.id),
    )
    cur_min, _cur_km = compute_exact_marginal(existing_src, self_ev, config=config)

    movability = pfv.movability  # 'unknown' | 'time_flexible' | 'day_flexible'
    day_flexible = movability == "day_flexible"

    cand = Candidate(
        lat=patient_coord[0],
        lng=patient_coord[1],
        service_minutes=v.service_minutes,
        time_type=None,
        preferred_start=None,
        preferred_end=None,
        patient_id=str(patient.id),
    )

    retreats: list[_Retreat] = []
    confirmation_blocked = False
    dismissed_blocked = False

    for (_oid, wd, code), bucket in sorted(
        all_buckets.items(), key=lambda kv: (kv[0][1], kv[0][2], str(kv[0][0]))
    ):
        # 同一バケット退避は許容する: 「Aさんを同コース 16:00→15:30」が最も自然な 1 手。
        # ただし同一バケット退避は _fits 呼出し元で最終状態検証 (_add_retreated_visit) を行い、
        # 退避先が対象の挿入枠を再占有するケースを弾く (挿入時刻は最終状態で確定する)。
        is_same_weekday = wd == from_weekday
        kind = "time_change" if is_same_weekday else "day_change"
        # 曜日跨ぎ: 移動先曜日を既に占有していたら apply 不能 (デッドエンド防止).
        if not is_same_weekday and wd in occupied_weekdays:
            continue
        # NG スタッフ (§6 / PO確定 2026-08-11): 退避させられる患者にとって NG のコースへは
        # 退避させない (ハード除外). 対象患者の投入先側は compute_all_proposed_slots が
        # 同じ判定で除外する (_fits_involving_bucket 経由).
        if _staff_ng_mismatch(patient.id, bucket.ng_patient_ids):
            continue

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
            event_windows=bucket.event_windows,
        )
        for slot in slots:
            # イベント考慮 (2026-07-27): 退避は「既存患者を動かす」操作のため、
            # イベントと衝突する枠 (フォールバック) へは退避させない (クリーンのみ).
            # 衝突を許すのは新規配置 (プール投入) 側の警告付き提案だけ.
            if slot.event_conflicts:
                continue
            cand_ev = ExistingVisit(
                start_time=slot.start,
                end_time=slot.end,
                lat=patient_coord[0],
                lng=patient_coord[1],
                service_minutes=v.service_minutes,
                patient_id=str(patient.id),
            )
            new_min, _new_km = compute_exact_marginal(existing, cand_ev, config=config)
            delta = new_min - cur_min  # 正 = 退避で増える移動 (乱れ).

            within = _slot_within_preference(patient, wd, slot.start, slot.end, config=config)
            # P-3 (D-2): 確認不要の手のみ. 希望内は無条件可 / 希望外は movability 次第.
            if not within:
                allowed = (
                    movability in ("time_flexible", "day_flexible")
                    if is_same_weekday
                    else day_flexible
                )
                if not allowed:
                    confirmation_blocked = True
                    continue
            # 却下記憶 (D-3): (patient, kind, 現在枠曜日) の指紋を尊重.
            if (patient.id, kind, from_weekday) in dismissed_fp:
                dismissed_blocked = True
                continue
            retreats.append(
                _Retreat(
                    weekday=wd,
                    course_code=code,
                    start=slot.start,
                    end=slot.end,
                    delta_minutes=delta,
                    within_preference=within,
                )
            )

    return retreats, confirmation_blocked, dismissed_blocked


def _best_retreat(retreats: list[_Retreat]) -> _Retreat:
    """退避先の決定的ベスト: 希望内優先 → delta 昇順 → 早い時刻 → コード → 曜日."""
    return min(
        retreats,
        key=lambda r: (
            not r.within_preference,
            r.delta_minutes,
            _time_to_min(r.start),
            r.course_code,
            r.weekday,
        ),
    )


# ---------------------------------------------------------------------------
# ブロッカー 1 件の評価 (適格性 → 退避先 → 手)
# ---------------------------------------------------------------------------


def _evaluate_blocker(
    v: V2Visit,
    blocker_bucket_key: tuple[UUID, int, str],
    blocker_bucket: _CourseBucket,
    *,
    all_buckets: dict[tuple[UUID, int, str], _CourseBucket],
    patient_by_id: dict[UUID, Patient],
    pfv_by_pw: dict[tuple[UUID, int], PatientFixedVisit],
    occupied_by_patient: dict[UUID, set[int]],
    dismissed_fp: set[tuple[UUID, str, int]],
    config: SchedulingConfig,
    summary: UnblockUnmovableSummary | None,
    exclude_same_bucket: bool = False,
) -> UnblockMove | None:
    """real-blocker v を 1 手に変換する。不可なら None (``summary`` 指定時のみ理由を会計).

    ``summary`` が非 None のときだけ、動かせなかった理由を 1 件計上する
    (深さ 1 パスのみ会計。深さ 2 は同じブロッカーを再評価するため会計しない = 二重計上防止)。
    ``exclude_same_bucket`` が True のとき、ブロッカーと同一 (weekday, course_code) の
    退避先は除外する (同一バケット退避の最終状態チェック失敗後のフォールバック用)。
    """
    patient = patient_by_id.get(v.patient_id)
    pfv = pfv_by_pw.get((v.patient_id, blocker_bucket.weekday))
    if pfv is None:
        # PFV 対応が取れず可動判定不能 (会計語彙外なので黙って skip).
        return None
    if pfv.is_pinned:
        if summary is not None:
            summary.pinned += 1
        return None
    if pfv.movability == "locked":
        if summary is not None:
            summary.locked += 1
        return None
    if patient is None or patient.lat is None or patient.lng is None:
        return None
    if bool(patient.requires_multiple_staff):
        if summary is not None:
            summary.two_staff += 1
        return None
    if _is_pair_half(v, blocker_bucket):
        if summary is not None:
            summary.pair += 1
        return None

    retreats, conf_blocked, dis_blocked = _enumerate_retreats(
        v,
        blocker_bucket_key,
        blocker_bucket,
        patient,
        pfv,
        all_buckets=all_buckets,
        occupied_weekdays=occupied_by_patient.get(v.patient_id, set()),
        dismissed_fp=dismissed_fp,
        config=config,
    )
    if exclude_same_bucket:
        retreats = [
            r
            for r in retreats
            if not (
                r.weekday == blocker_bucket.weekday and r.course_code == blocker_bucket.course_code
            )
        ]
    if not retreats:
        if summary is not None:
            # 却下記憶が阻んだ場合を優先計上、次いで確認要 (希望外+movability不明).
            if dis_blocked:
                summary.dismissed += 1
            elif conf_blocked:
                summary.confirmation_required += 1
        return None

    best = _best_retreat(retreats)
    return UnblockMove(
        patient_id=v.patient_id,
        patient_name=v.patient_name,
        from_weekday=blocker_bucket.weekday,
        from_course_code=blocker_bucket.course_code,
        from_start=v.start_time,
        to_weekday=best.weekday,
        to_course_code=best.course_code,
        to_start=best.start,
        to_end=best.end,
        delta_minutes=best.delta_minutes,
        within_preference=best.within_preference,
    )


# ---------------------------------------------------------------------------
# プラン組み立て
# ---------------------------------------------------------------------------


def compute_plan_id(
    move_tuples: list[tuple[UUID, int, str, int, str, str]],
    insert_weekday: int,
    insert_course_code: str,
    insert_start_hhmm: str,
    insert_partner_course: str | None,
    target_id: UUID | None,
) -> str:
    """``{target_id}:{sha256}`` を決定的に導出する (apply 検証と search engine の共有実装).

    Args:
        move_tuples: [(patient_id, from_weekday, from_start_hhmm, to_weekday, to_course_code, to_start_hhmm)]
        insert_weekday: 配置曜日 (0=Mon).
        insert_course_code: 配置コード ("A"〜"E" / "M"〜"M9").
        insert_start_hhmm: 配置開始 "HH:MM".
        insert_partner_course: 2 名体制相方コード (None なら通常配置).
        target_id: 対象患者 UUID (None のとき "None" になるが FE は opaque key として扱う).

    apply 側は ``ProposeUnblockApplyRequest.target_patient_id`` + ``plan`` から再導出して
    plan_id と照合することで「plan 内容の改竄」と「対象患者のすり替え」を同時に検出する。
    """
    parts = [
        f"{pid}|{fwd}|{fstart}|{twd}|{tcourse}|{tstart}"
        for pid, fwd, fstart, twd, tcourse, tstart in move_tuples
    ]
    parts.append(
        f"INS|{insert_weekday}|{insert_course_code}|{insert_start_hhmm}|{insert_partner_course}"
    )
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return f"{target_id}:{digest}"


def _plan_id(moves: list[UnblockMove], insert: UnblockInsert, target_id: UUID | None) -> str:
    """``{対象患者id}:{内容ハッシュ}`` の決定的文字列 (``compute_plan_id`` の内部ラッパ)."""
    move_tuples = [
        (
            m.patient_id,
            m.from_weekday,
            _hhmm(m.from_start),
            m.to_weekday,
            m.to_course_code,
            _hhmm(m.to_start),
        )
        for m in moves
    ]
    return compute_plan_id(
        move_tuples,
        insert.weekday,
        insert.course_code,
        _hhmm(insert.start),
        insert.partner_course_code,
        target_id,
    )


def _make_plan(
    moves: list[UnblockMove],
    ps: ProposedSlot,
    target_id: UUID | None,
    *,
    frees_capacity: bool = False,
) -> UnblockPlan:
    """moves + 対象枠 (ProposedSlot) からプランを組む (moves は決定的順にソート).

    ``frees_capacity`` は定員起因の詰まりを他バケット退避で開通したプランに立てる (W-15)。
    plan_id には含めない (内容ハッシュは moves+insert のみ = apply 側と一致させるため。
    frees_capacity は moves+insert から決定的に導かれるので冪等性を損なわない)。
    """
    ordered = sorted(
        moves, key=lambda m: (m.from_weekday, _time_to_min(m.from_start), str(m.patient_id))
    )
    insert = UnblockInsert(
        weekday=ps.weekday,
        course_code=ps.course_code,
        start=ps.start,
        end=ps.end,
        partner_course_code=ps.partner_course_code,
    )
    return UnblockPlan(
        plan_id=_plan_id(ordered, insert, target_id),
        moves=ordered,
        insert=insert,
        total_delta_minutes=sum(m.delta_minutes for m in ordered),
        moved_count=len(ordered),
        frees_capacity=frees_capacity,
    )


def _build_plan_courses(
    moves: list[UnblockMove],
    insert: UnblockInsert,
    *,
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_id: UUID,
    office_name_by_id: dict[UUID, str],
    target_patient_id: UUID,
    target_patient_name: str,
) -> list[UnblockCourseSnapshotData]:
    """プランが影響する全コースの before/after スナップショットを構築する (W-13b).

    影響コース = 各 move の移動元/移動先 + insert の配置先 (+ 2 名体制なら相方コース)。
    ``before`` は現状バケットの正典スナップショット (``snapshot_course_bucket``)。``after`` は
    「move の患者を移動元から除去し移動先へ配置 + insert 枠へ対象患者を追加」した最終状態。
    entries は improvement_engine の ``CourseSnapshotVisitData`` (時刻・患者名) と同形。
    """
    keys: set[tuple[int, str]] = set()
    for m in moves:
        keys.add((m.from_weekday, m.from_course_code))
        keys.add((m.to_weekday, m.to_course_code))
    keys.add((insert.weekday, insert.course_code))
    if insert.partner_course_code is not None:
        keys.add((insert.weekday, insert.partner_course_code))

    office_name = office_name_by_id.get(office_id)
    out: list[UnblockCourseSnapshotData] = []
    for wd, code in sorted(keys):
        bucket = buckets.get((office_id, wd, code))
        if bucket is None:
            # 影響コースだが当週バケット無し (空コース) はスナップショット不能なので skip.
            continue
        snap: CourseSnapshotData = snapshot_course_bucket(bucket)
        before = list(snap.visits)

        # after: このコースが移動元となる move の患者を除去 → 移動先となる move / insert を追加.
        removed_pids = {
            m.patient_id for m in moves if m.from_weekday == wd and m.from_course_code == code
        }
        after = [v for v in before if v.patient_id not in removed_pids]
        for m in moves:
            if m.to_weekday == wd and m.to_course_code == code:
                after.append(
                    CourseSnapshotVisitData(
                        patient_id=m.patient_id,
                        patient_name=m.patient_name,
                        start_time=m.to_start,
                        end_time=m.to_end,
                    )
                )
        is_insert_course = (insert.weekday, insert.course_code) == (wd, code)
        is_partner_course = insert.partner_course_code is not None and (
            insert.weekday,
            insert.partner_course_code,
        ) == (wd, code)
        if is_insert_course or is_partner_course:
            after.append(
                CourseSnapshotVisitData(
                    patient_id=target_patient_id,
                    patient_name=target_patient_name,
                    start_time=insert.start,
                    end_time=insert.end,
                )
            )
        after.sort(key=lambda v: _time_to_min(v.start_time))

        out.append(
            UnblockCourseSnapshotData(
                office_name=office_name,
                weekday=wd,
                course_code=code,
                course_label=snap.course_label,
                before=before,
                after=after,
                events=list(snap.events),
            )
        )
    return out


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


async def search_unblock_plans(
    db: AsyncSession,
    *,
    candidate: CandidateInput,
    office_id: UUID,
    iso_year: int,
    iso_week: int,
    config: SchedulingConfig,
    limit: int = DEFAULT_UNBLOCK_LIMIT,
) -> UnblockResult:
    """候補 0 件の対象患者について「1〜2 手ずらせば入る」開通プランを算出する (read-only).

    アルゴリズム (設計書 §2.1):
      1. 対象週 × 拠点のバケットをロード。
      2. 各バケット B について、対象患者が既に入るなら skip (詰まっていない)。
      3. 深さ 1: B 内の各既存 visit v を除去 → 対象が入るか → v の退避先 (P-3) を列挙 →
         成立プラン { moves:[v→r], insert:(B,T) } を生成。適格外 (pinned/locked/2名体制/
         ペア/却下/確認要) は unmovable_summary に計上。
      4. 深さ 2: 深さ 1 で開通しなかったバケットのみ、同一バケット内 2 visit の組合せを試す。
      5. ランキング (動かす人数 → 全手希望内 → total_delta → plan_id) 上位 limit。
    """
    buckets, office_name_by_id, office_code_by_id = await load_week_course_buckets(
        db, iso_year=iso_year, iso_week=iso_week, office_ids=[office_id]
    )

    # state_token は scope-optimization と同一規約 (office スコープ PFV 指紋). simulate と
    # apply で同じヘルパを使い、簡単に不一致 (409) を検出する.
    state_token = await compute_current_state_token(
        db, iso_year=iso_year, iso_week=iso_week, scope=OptimizationScope(office_id=office_id)
    )

    summary = UnblockUnmovableSummary()
    if not buckets:
        return UnblockResult(plans=[], unmovable_summary=summary, state_token=state_token)

    # バケットに現れる患者の Patient / PFV (normal slot0) / 却下記憶をまとめてロード.
    pids: set[UUID] = {v.patient_id for b in buckets.values() for v in b.visits}
    patient_rows = (await db.scalars(select(Patient).where(Patient.id.in_(pids)))).all()
    patient_by_id: dict[UUID, Patient] = {p.id: p for p in patient_rows}

    pfv_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id.in_(pids),
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.slot_index == 0,
            )
        )
    ).all()
    pfv_by_pw: dict[tuple[UUID, int], PatientFixedVisit] = {
        (r.patient_id, r.weekday): r for r in pfv_rows
    }
    occupied_by_patient: dict[UUID, set[int]] = {}
    for r in pfv_rows:
        occupied_by_patient.setdefault(r.patient_id, set()).add(r.weekday)

    now = datetime.now()
    dismissal_rows = (
        await db.scalars(
            select(SuggestionDismissal).where(SuggestionDismissal.patient_id.in_(pids))
        )
    ).all()
    dismissed_fp: set[tuple[UUID, str, int]] = {
        (d.patient_id, d.kind, d.target_weekday)
        for d in dismissal_rows
        if (d.expires_at is None or _as_naive(d.expires_at) > now)
        and d.kind in ("time_change", "day_change")
    }

    # W-13b: after スナップショットの insert 行に載せる対象患者名/ID を解決する.
    # 対象患者 (プール患者) はバケットに未配置なので patient_by_id に居ないことがある.
    target_pid: UUID = candidate.existing_patient_id or UUID(int=0)
    target_name = "対象患者"
    if candidate.existing_patient_id is not None:
        tp = patient_by_id.get(candidate.existing_patient_id)
        if tp is None:
            tp = await db.scalar(select(Patient).where(Patient.id == candidate.existing_patient_id))
        if tp is not None:
            target_name = tp.name

    # weekday ごとにバケットをグルーピング (対象患者の開通判定は単一 weekday スコープで行う).
    weekday_groups: dict[int, dict[tuple[UUID, int, str], _CourseBucket]] = {}
    for key, b in buckets.items():
        weekday_groups.setdefault(key[1], {})[key] = b

    plans: list[UnblockPlan] = []

    def _fits(wd_buckets, bucket_code) -> ProposedSlot | None:
        return _fits_involving_bucket(
            wd_buckets,
            candidate,
            office_id=office_id,
            office_name_by_id=office_name_by_id,
            office_code_by_id=office_code_by_id,
            config=config,
            bucket_code=bucket_code,
        )

    def _finalize(
        moves: list[UnblockMove], ps: ProposedSlot, *, frees_capacity: bool = False
    ) -> UnblockPlan:
        """plan を組み立て、影響コースの before/after スナップショット (W-13b) を付与する."""
        plan = _make_plan(moves, ps, candidate.existing_patient_id, frees_capacity=frees_capacity)
        courses = _build_plan_courses(
            plan.moves,
            plan.insert,
            buckets=buckets,
            office_id=office_id,
            office_name_by_id=office_name_by_id,
            target_patient_id=target_pid,
            target_patient_name=target_name,
        )
        return _dc_replace(plan, courses=courses)

    for wd in sorted(weekday_groups):
        wd_buckets = weekday_groups[wd]
        for b_key in sorted(wd_buckets, key=lambda k: (k[2], str(k[0]))):
            bucket = wd_buckets[b_key]
            b_code = b_key[2]

            # 既に入るなら詰まっていない (unblock 不要).
            if _fits(wd_buckets, b_code) is not None:
                continue

            # W-15: このバケットが対象患者にとって定員起因 (件数/分の上限) で詰まっているか。
            # 定員起因なら退避先は他バケット限定 (同一バケット内の時間ずらしは件数も使用分も
            # 変えず定員を空けないため) とし、成立プランに frees_capacity=True を付ける。
            cap_blocked = _bucket_capacity_blocked(bucket, candidate, config=config)

            visits_sorted = sorted(
                bucket.visits, key=lambda v: (_time_to_min(v.start_time), str(v.patient_id))
            )

            # ---- 深さ 1: 単一 visit 除去 ----
            depth1_found = False
            for v in visits_sorted:
                sub = dict(wd_buckets)
                sub[b_key] = _bucket_without(bucket, v)
                ps = _fits(sub, b_code)
                if ps is None:
                    continue  # v を除いても入らない = real-blocker ではない.
                move = _evaluate_blocker(
                    v,
                    b_key,
                    bucket,
                    all_buckets=buckets,
                    patient_by_id=patient_by_id,
                    pfv_by_pw=pfv_by_pw,
                    occupied_by_patient=occupied_by_patient,
                    dismissed_fp=dismissed_fp,
                    config=config,
                    summary=summary,
                    # W-15: 定員起因は他バケット退避のみが枠を空ける (同一バケット退避は除外).
                    exclude_same_bucket=cap_blocked,
                )
                if move is not None:
                    # 同一バケット退避の最終状態検証: ブロッカーを退避時刻に仮配置した状態で
                    # 対象患者の開通を再確認する。退避先が対象の挿入枠を再占有するケースは弾く。
                    # (定員起因では exclude_same_bucket により同一バケット退避は生じない = 時間起因のみ到達)
                    if move.to_weekday == b_key[1] and move.to_course_code == b_key[2]:
                        final_sub = dict(wd_buckets)
                        final_sub[b_key] = _add_retreated_visit(
                            bucket, v, move.to_start, move.to_end
                        )
                        ps_sb = _fits(final_sub, b_code)
                        if ps_sb is not None:
                            ps = ps_sb
                        else:
                            # 同一バケット退避が対象枠を再占有するため、他バケット退避を再探索。
                            move = _evaluate_blocker(
                                v,
                                b_key,
                                bucket,
                                all_buckets=buckets,
                                patient_by_id=patient_by_id,
                                pfv_by_pw=pfv_by_pw,
                                occupied_by_patient=occupied_by_patient,
                                dismissed_fp=dismissed_fp,
                                config=config,
                                summary=None,  # 二重計上防止
                                exclude_same_bucket=True,
                            )
                            if move is None:
                                continue  # 有効な退避先なし → このブロッカーはスキップ.
                    plans.append(_finalize([move], ps, frees_capacity=cap_blocked))
                    depth1_found = True

            # ---- 深さ 2: 深さ 1 で開通しなかったバケットのみ (同一バケット内 2 visit) ----
            if depth1_found:
                continue
            for v, w in combinations(visits_sorted, 2):
                sub = dict(wd_buckets)
                sub[b_key] = _bucket_without(bucket, v, w)
                ps = _fits(sub, b_code)
                if ps is None:
                    continue  # 2 件除いても入らない.
                # 深さ 2 は会計しない (深さ 1 で同じブロッカーを会計済 / 二重計上防止).
                # W-15: 定員起因は他バケット退避のみが枠を空ける (同一バケット退避は除外).
                move_v = _evaluate_blocker(
                    v,
                    b_key,
                    bucket,
                    all_buckets=buckets,
                    patient_by_id=patient_by_id,
                    pfv_by_pw=pfv_by_pw,
                    occupied_by_patient=occupied_by_patient,
                    dismissed_fp=dismissed_fp,
                    config=config,
                    summary=None,
                    exclude_same_bucket=cap_blocked,
                )
                move_w = _evaluate_blocker(
                    w,
                    b_key,
                    bucket,
                    all_buckets=buckets,
                    patient_by_id=patient_by_id,
                    pfv_by_pw=pfv_by_pw,
                    occupied_by_patient=occupied_by_patient,
                    dismissed_fp=dismissed_fp,
                    config=config,
                    summary=None,
                    exclude_same_bucket=cap_blocked,
                )
                if move_v is not None and move_w is not None:
                    # 深さ 2 でも同一バケット退避がある場合は最終状態で再検証.
                    same_v = move_v.to_weekday == b_key[1] and move_v.to_course_code == b_key[2]
                    same_w = move_w.to_weekday == b_key[1] and move_w.to_course_code == b_key[2]
                    if same_v or same_w:
                        # v, w 両方除去 → 同一バケット退避分を新時刻で仮追加
                        # (_add_retreated_visit は bucket に v が不在でも動作する).
                        final_sub = dict(wd_buckets)
                        temp = _bucket_without(bucket, v, w)
                        if same_v:
                            temp = _add_retreated_visit(temp, v, move_v.to_start, move_v.to_end)
                        if same_w:
                            temp = _add_retreated_visit(temp, w, move_w.to_start, move_w.to_end)
                        final_sub[b_key] = temp
                        ps_check = _fits(final_sub, b_code)
                        if ps_check is None:
                            # 同一バケット退避が最終状態で失敗 → 各ブロッカーを他バケットで再探索.
                            if same_v:
                                move_v = _evaluate_blocker(
                                    v,
                                    b_key,
                                    bucket,
                                    all_buckets=buckets,
                                    patient_by_id=patient_by_id,
                                    pfv_by_pw=pfv_by_pw,
                                    occupied_by_patient=occupied_by_patient,
                                    dismissed_fp=dismissed_fp,
                                    config=config,
                                    summary=None,
                                    exclude_same_bucket=True,
                                )
                            if same_w:
                                move_w = _evaluate_blocker(
                                    w,
                                    b_key,
                                    bucket,
                                    all_buckets=buckets,
                                    patient_by_id=patient_by_id,
                                    pfv_by_pw=pfv_by_pw,
                                    occupied_by_patient=occupied_by_patient,
                                    dismissed_fp=dismissed_fp,
                                    config=config,
                                    summary=None,
                                    exclude_same_bucket=True,
                                )
                            if move_v is None or move_w is None:
                                continue
                            # 再探索後の退避先も同一バケットなら再度最終状態検証.
                            rsame_v = (
                                move_v.to_weekday == b_key[1] and move_v.to_course_code == b_key[2]
                            )
                            rsame_w = (
                                move_w.to_weekday == b_key[1] and move_w.to_course_code == b_key[2]
                            )
                            if rsame_v or rsame_w:
                                continue  # まだ同一バケット混じり → このペアは諦める.
                            # 両方とも他バケット退避 → ps (sub = Mon A without v,w) が正しい.
                        else:
                            ps = ps_check
                    plans.append(_finalize([move_v, move_w], ps, frees_capacity=cap_blocked))

    # ランキング + dedup (同一 plan_id).
    plans.sort(
        key=lambda p: (
            p.moved_count,
            not all(m.within_preference for m in p.moves),
            p.total_delta_minutes,
            p.plan_id,
        )
    )
    seen: set[str] = set()
    unique: list[UnblockPlan] = []
    for p in plans:
        if p.plan_id in seen:
            continue
        seen.add(p.plan_id)
        unique.append(p)

    return UnblockResult(
        plans=unique[:limit],
        unmovable_summary=summary,
        state_token=state_token,
    )


def _as_naive(dt: datetime) -> datetime:
    """tz-aware / naive 混在に備え naive (UTC 相当) に揃えて比較する."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


__all__ = [
    "DEFAULT_UNBLOCK_LIMIT",
    "UnblockCourseSnapshotData",
    "UnblockInsert",
    "UnblockMove",
    "UnblockPlan",
    "UnblockResult",
    "UnblockUnmovableSummary",
    "compute_plan_id",
    "search_unblock_plans",
]
