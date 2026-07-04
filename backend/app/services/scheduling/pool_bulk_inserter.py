"""プール一括投入エンジン (W-1: simulate のみ / read-only).

設計書 ``docs/plans/pool-bulk-insert-design.md`` §3.

保留プールの患者列を **逐次シミュレーション** で 1 人ずつ決定論的にメモリ内バケットへ積む:

  1. ``load_week_course_buckets`` で現状バケットを 1 回ロード → scope_optimizer の
     ``_copy_bucket`` パターンで可変コピー (メモリ内シミュレーション状態).
  2. 各患者に ``compute_all_proposed_slots`` を 1 回実行して候補数・最良 delta・投入可能
     曜日数を採取 (= pool-overview と同一計算).
  3. D-1 ハイブリッド順序で患者列を確定:
       第1群 = 投入可能曜日が 1 つしかない患者 (候補数昇順 → delta 昇順)
       第2群 = 残りを最良 delta 昇順
       全タイブレークの最後は ``str(patient_id)`` 辞書順 (決定性必須・乱数禁止).
  4. 患者を順に処理: 現在のメモリ内バケットに対し ``compute_all_proposed_slots`` →
     不足曜日ごとに delta 最小の候補を 1 つ採用 → 採用枠を ``_insert_visit`` パターンで
     バケットに仮追加 → 次の患者はそれを既存訪問として見る (= 調停の実体).
  5. 全員分の placements / partial / unplaced / before-after 週ビュー / KPI を返す.

物差しの正典 (実現可能性 = proposal_solver / コスト = compute_exact_marginal) は
``compute_all_proposed_slots`` / ``_marginal_cost_minutes`` を **import 再利用** する
(コピー禁止). 既存エンジン (propose_slots_service / scope_optimizer / proposal_solver /
schedule_health) の挙動は一切変更しない.

**read-only 厳守**: DB 書込みコードを一切含めない. apply (W-2) と完全分離.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.scheduling.auto_allocator_v2 import NOON_HOUR, V2Visit
from app.services.scheduling.config import SchedulingConfig

# propose_slots_service の正典 (物差し統一・コピー禁止・import 再利用のみ).
from app.services.scheduling.propose_slots_service import (
    CandidateInput,
    ExcludedReasonSummary,
    ProposedSlot,
    _CourseBucket,
    _marginal_cost_minutes,
    _to_existing_visits,
    compute_all_proposed_slots,
    compute_overcapacity_slots,
    load_week_course_buckets,
)

# schedule_health の純関数 (前後メトリクス). scope_optimizer._metrics_of と同パターンで
# 模擬バケットへ適用する (計算式は健康診断と完全同一).
from app.services.scheduling.schedule_health import (
    _compute_course_metrics,
    _HealthCourse,
    _HealthVisit,
)

# scope_optimizer の可変バケット操作 (append + start 昇順 sort). import 再利用のみ.
from app.services.scheduling.scope_optimizer import _copy_bucket, _insert_visit

# 上限 (pool-overview の POOL_OVERVIEW_MAX_PATIENTS と同値). 超過は API 層で 422.
POOL_BULK_MAX_PATIENTS: int = 50

_INF = float("inf")

# CandidateInput を導出する関数の型 (依存性注入). 呼出側 (API) が pool-overview と
# 同一の ``_patient_to_pool_candidate`` を渡すことで最良候補の一致を保証する (単一ソース).
CandidateBuilder = Callable[[Patient], CandidateInput | None]


def _tmin(t: time) -> int:
    return t.hour * 60 + t.minute


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _delta_key(d: float | None) -> float:
    """delta 昇順ソート用. 未計算 (None) は末尾へ."""
    return d if d is not None else _INF


def _desired_weekly_visits(patient: Patient) -> int:
    """希望週訪問回数 (frequency_per_week, 1..7). 未設定/無効は 0.

    FE ``preferred-visits.ts:getDesiredWeeklyVisitCount`` と同一契約:
        - weekly_pattern 無し / frequency_per_week=0・無効値 → 0 (= pool 対象外)
        - それ以外 → min(7, floor(frequency_per_week))
    """
    wp = patient.weekly_pattern if isinstance(patient.weekly_pattern, dict) else None
    if not wp:
        return 0
    raw = wp.get("frequency_per_week")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return 0
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return 0
    if not (n > 0):
        return 0
    return min(7, int(n))


# ---------------------------------------------------------------------------
# 結果 dataclass (API 層で schema に詰める前の内部表現)
# ---------------------------------------------------------------------------


@dataclass
class BulkPlacement:
    seq: int
    patient_id: UUID
    patient_name: str
    weekday: int
    course_code: str
    office_id: UUID
    start: time
    service_minutes: int
    delta_minutes: float | None
    warnings: list[str]


@dataclass
class BulkPartial:
    patient_id: UUID
    patient_name: str
    placed_days: int
    missing_days: int
    unplaced_reasons: dict[int, str]  # weekday -> reason
    # 定員起因 (capacity_full) の未充足曜日に「定員 +1 なら入る候補」が何件あるか (方式b 橋渡し用).
    # A 案 (2026-07-04 PO 承認): 一括に超過は組み込まず、個別の相談フロー (方式b) へ橋渡しする.
    overcapacity_available_count: int = 0


@dataclass
class BulkUnplaced:
    patient_id: UUID
    patient_name: str
    reason: str
    # capacity_full で投入不能な患者に「定員 +1 なら入る候補」が何件あるか (方式b 橋渡し用).
    overcapacity_available_count: int = 0


@dataclass
class PoolBulkResult:
    placements: list[BulkPlacement] = field(default_factory=list)
    partial: list[BulkPartial] = field(default_factory=list)
    unplaced: list[BulkUnplaced] = field(default_factory=list)
    before_visits: list[V2Visit] = field(default_factory=list)
    after_visits: list[V2Visit] = field(default_factory=list)
    office_name_by_id: dict[UUID, str] = field(default_factory=dict)
    travel_minutes_before: int = 0
    travel_minutes_after: int = 0
    travel_km_before: float = 0.0
    travel_km_after: float = 0.0
    placed_patients: int = 0
    placed_slots: int = 0
    state_token: str = ""


# ---------------------------------------------------------------------------
# 前後メトリクス (schedule_health の純関数を模擬バケットへ適用)
# ---------------------------------------------------------------------------


def _compute_travel_totals(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    *,
    config: SchedulingConfig,
) -> tuple[int, float]:
    """模擬バケット群の週合計 (travel_minutes, travel_km) を算出する.

    scope_optimizer ``_metrics_of`` と同パターン: V2Visit → _HealthVisit の薄い変換のみ
    行い、計算式は健康診断と完全同一. travel_km は「生値を合計してから 1 回だけ丸める」
    (二重丸め防止・health と同方針).
    """
    total_min = 0
    raw_km = 0.0
    for (office_id, weekday, code), bucket in buckets.items():
        if not bucket.visits:
            continue
        hc = _HealthCourse(
            office_id=office_id,
            weekday=weekday,
            course_code=code,
            staff_name=bucket.staff_name,
            visits=[
                _HealthVisit(
                    patient_id=v.patient_id,
                    start_time=v.start_time,
                    end_time=v.end_time,
                    lat=v.lat,
                    lng=v.lng,
                )
                for v in bucket.visits
            ],
        )
        metrics, km = _compute_course_metrics(hc, config=config)
        total_min += metrics.travel_minutes
        raw_km += km
    return total_min, round(raw_km, 1)


# ---------------------------------------------------------------------------
# state_token (apply=W-2 の楽観ロック用指紋)
# ---------------------------------------------------------------------------


async def compute_bulk_state_token(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_id: UUID,
    buckets: dict[tuple[UUID, int, str], _CourseBucket] | None = None,
) -> str:
    """対象拠点の「全 normal PFV 行 + 当週 visits」の順序不変指紋 (SHA-256).

    scope_optimizer ``compute_state_token`` の思想を拡張したもの. 一括投入は PFV だけ
    でなく **visits** にも依存する (仮追加の容量調停が当週訪問状況で変わる) ため、両方を
    指紋に含める. simulate 時点と apply 時点で同一集合から再計算し、不一致なら 409 にする.

    - normal PFV 行: patient.primary_office_id == office_id の患者の mode='normal' 全行.
    - 当週 visits: ``load_week_course_buckets`` (= simulate と同一ローダ) の visit 群.
      座標欠損患者の visit はローダが除外するが、一括投入対象 (= 座標必須) には影響しない.

    Args:
        buckets: 呼出側が既にロード済みのバケット (simulate_pool_bulk_insert から渡す). None
            の場合のみ自前で ``load_week_course_buckets`` を実行する (二重ロード防止).

    apply (W-2) はこの関数を再利用して同一規約で再計算すること (集合がずれると楽観ロックが
    誤作動するため).
    """
    if buckets is None:
        buckets, _names, _codes = await load_week_course_buckets(
            db, iso_year=iso_year, iso_week=iso_week, office_ids=[office_id]
        )
    visit_lines = [
        f"V|{v.patient_id}|{v.weekday}|{v.course_code}|{_hhmm(v.start_time)}|{_hhmm(v.end_time)}"
        for b in buckets.values()
        for v in b.visits
    ]
    pfv_rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .join(Patient, Patient.id == PatientFixedVisit.patient_id)
            .where(
                Patient.primary_office_id == office_id,
                PatientFixedVisit.mode == "normal",
            )
        )
    ).all()
    pfv_lines = [
        f"P|{p.patient_id}|{p.weekday}|{p.slot_index}|{_hhmm(p.start_time)}|{p.duration_min}"
        f"|{p.course_template_id}|{int(p.is_pinned)}|{p.movability}"
        for p in pfv_rows
    ]
    lines = sorted(visit_lines + pfv_lines)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1 患者分の候補選定ヘルパ
# ---------------------------------------------------------------------------


def _best_by_weekday(results: list[ProposedSlot]) -> dict[int, ProposedSlot]:
    """delta 昇順済みの結果列から曜日ごとの最良候補 (先頭 = delta 最小) を採る.

    ``compute_all_proposed_slots`` は delta 昇順で返るため、曜日ごとに最初に現れた候補が
    その曜日の delta 最小候補になる (compute_coverage と同規約の setdefault).
    """
    best: dict[int, ProposedSlot] = {}
    for ps in results:
        best.setdefault(ps.weekday, ps)
    return best


def _ensure_delta(
    ps: ProposedSlot,
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    candidate: CandidateInput,
    *,
    config: SchedulingConfig,
) -> None:
    """不足曜日の先頭候補に delta が付いていない (DELTA_EVAL_LIMIT 圏外) 場合、その候補
    だけ追加で厳密 delta を計算する (compute_exact_marginal 1 回/曜日. _marginal_cost_minutes
    を再利用). 既に付いていれば何もしない (in-place 更新).
    """
    if ps.marginal_cost_minutes is not None:
        return
    bucket = buckets.get((ps.office_id, ps.weekday, ps.course_code))
    if bucket is None:
        return
    existing = _to_existing_visits(bucket)
    ps.marginal_cost_minutes = _marginal_cost_minutes(
        existing, ps.start, ps.end, candidate, config=config
    )


def _weekday_exclusion_reason(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    weekday: int,
    *,
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None,
    config: SchedulingConfig,
) -> str:
    """指定曜日で候補が 0 件になる理由コードを取得する (部分投入の不足曜日報告用).

    ``compute_all_proposed_slots`` の除外集約 (exclusions_out) を 1 曜日に絞って再利用する
    (excluded_summary と同一語彙). 候補が出た場合は理論上到達しないが安全に no_gap を返す.
    """
    cand_wd = replace(candidate, preferred_weekdays=frozenset({weekday}))
    excl: list[ExcludedReasonSummary] = []
    results = compute_all_proposed_slots(
        buckets,
        office_name_by_id,
        cand_wd,
        office_ids=office_ids,
        office_code_by_id=office_code_by_id,
        config=config,
        exclusions_out=excl,
    )
    if results:
        return "no_gap"
    for s in excl:
        if s.weekday == weekday:
            return s.reason
    return excl[0].reason if excl else "no_gap"


def _overcapacity_available_count(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    *,
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None,
    config: SchedulingConfig,
    weekdays: frozenset[int] | None = None,
) -> int:
    """最終 sim バケットに対する「定員 +1 なら入る候補」の件数を数える (方式b 橋渡し用).

    ``compute_overcapacity_slots`` を **import 再利用** する (コピー禁止). ``assign_marginal=False``
    で件数のみ (delta 計算はスキップ). 数え方は propose-slots endpoint の
    ``overcapacity_available_count`` と同一 = 返る slot 件数 (``len``). 一括の逐次シミュレーション
    で先行患者が枠を埋めた **最終状態** の buckets を渡すことで、「一括で先に埋まった枠」を考慮した
    +1 判定になる (現在人数 >= base 定員のバケットの枠のみ compute_overcapacity_slots が残す).

    Args:
        weekdays: 指定時はこの曜日のみに絞って列挙する (部分投入の capacity_full 未充足曜日限定).
            None なら候補の希望曜日すべて (投入不能患者用).
    """
    cand = candidate if weekdays is None else replace(candidate, preferred_weekdays=weekdays)
    over = compute_overcapacity_slots(
        buckets,
        office_name_by_id,
        cand,
        office_ids=office_ids,
        office_code_by_id=office_code_by_id,
        config=config,
        assign_marginal=False,
    )
    return len(over)


def _insert_placement(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    patient: Patient,
    candidate: CandidateInput,
    ps: ProposedSlot,
) -> None:
    """採用枠を模擬バケットへ仮追加する (scope_optimizer._insert_visit パターン).

    仮 visit は ``load_week_course_buckets`` が生成する既存 visit と同形の V2Visit で作り、
    次の患者のソルバ走査に既存訪問として見える (容量調停の実体). DB には書かない.
    """
    key = (ps.office_id, ps.weekday, ps.course_code)
    bucket = buckets[key]
    am_pm = "am" if ps.start.hour < NOON_HOUR else "pm"
    v = V2Visit(
        patient_id=patient.id,
        patient_name=patient.name,
        patient_code=patient.code,
        weekday=ps.weekday,
        start_time=ps.start,
        end_time=ps.end,
        service_minutes=candidate.service_minutes,
        lat=candidate.lat,
        lng=candidate.lng,
        office_id=ps.office_id,
        am_pm=am_pm,  # type: ignore[arg-type]
        source_kind="pool",
        course_code=ps.course_code,
        assigned_staff_id=bucket.assigned_staff_id,
        sex_restriction=candidate.sex_restriction,
        requires_multiple_staff=candidate.requires_multiple_staff,
    )
    _insert_visit(bucket, v)


def _flatten_with_staff(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
) -> list[V2Visit]:
    """バケット群を visit 平坦リストに落とす (before/after 週ビュー用).

    バケット割付スタッフを各 visit へ伝播する (load_week_course_buckets は visit 単位の
    assigned_staff_id を設定しないため、週ビューのコース担当表示用にここで補う).
    """
    out: list[V2Visit] = []
    for b in buckets.values():
        for v in b.visits:
            v.assigned_staff_id = b.assigned_staff_id
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# エンジン本体
# ---------------------------------------------------------------------------


async def simulate_pool_bulk_insert(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_id: UUID,
    patient_ids: list[UUID],
    config: SchedulingConfig,
    candidate_of: CandidateBuilder,
) -> PoolBulkResult:
    """プール一括投入の逐次シミュレーション (read-only. 設計書 §3).

    Args:
        candidate_of: Patient → CandidateInput|None の導出関数 (依存性注入). 呼出側 (API)
            が pool-overview と同一の ``_patient_to_pool_candidate`` を渡すことで、1 人目の
            投入先が pool-overview の best と厳密一致することを保証する (物差し単一ソース).

    Returns:
        placements / partial / unplaced / before-after visit 列 / 前後 KPI / state_token.
        before-after 週ビュー (V2WeekdayBeforeAfter) の構築は API 層 (既存
        ``_build_weekday_before_after`` 再利用) に委ねる.
    """
    office_ids = [office_id]

    # 1. 現状バケットを 1 回ロード → 可変コピー (メモリ内シミュレーション状態).
    buckets, office_name_by_id, office_code_by_id = await load_week_course_buckets(
        db, iso_year=iso_year, iso_week=iso_week, office_ids=office_ids
    )
    sim: dict[tuple[UUID, int, str], _CourseBucket] = {
        key: _copy_bucket(b) for key, b in buckets.items()
    }

    result = PoolBulkResult(office_name_by_id=office_name_by_id)
    # オリジナル buckets (sim 前) を渡して二重ロードを回避する.
    result.state_token = await compute_bulk_state_token(
        db, iso_year=iso_year, iso_week=iso_week, office_id=office_id, buckets=buckets
    )

    # 対象患者を 1 クエリでロード (入力順を保った dedup. 存在しない/削除済みは黙って除外).
    unique_ids = list(dict.fromkeys(patient_ids))
    if unique_ids:
        patient_rows = (
            await db.scalars(
                select(Patient).where(
                    Patient.id.in_(unique_ids),
                    Patient.deleted_at.is_(None),
                )
            )
        ).all()
    else:
        patient_rows = []
    patient_by_id: dict[UUID, Patient] = {p.id: p for p in patient_rows}

    # 座標未登録患者は投入不能 (no_coordinates). 順序算出/配置からは外す.
    orderable: list[tuple[Patient, CandidateInput]] = []
    for pid in unique_ids:
        patient = patient_by_id.get(pid)
        if patient is None:
            continue
        # W-6: 患者×拠点フィルタ (座標チェックより先). 主担当拠点が未設定/他拠点の患者は
        # 週次生成から除外されるか本拠点のコース対象外なので、配置計算から除外する.
        if patient.primary_office_id is None:
            result.unplaced.append(
                BulkUnplaced(patient_id=pid, patient_name=patient.name, reason="no_primary_office")
            )
            continue
        if patient.primary_office_id != office_id:
            result.unplaced.append(
                BulkUnplaced(patient_id=pid, patient_name=patient.name, reason="office_mismatch")
            )
            continue
        candidate = candidate_of(patient)
        if candidate is None:
            result.unplaced.append(
                BulkUnplaced(patient_id=pid, patient_name=patient.name, reason="no_coordinates")
            )
            continue
        orderable.append((patient, candidate))

    # 2. 各患者に compute_all_proposed_slots を 1 回実行して順序メトリクスを採取
    #    (INITIAL 状態. 仮追加はまだ行わない).
    #    order_info = (patient, candidate, candidate_count, best_delta, available_weekday_count)
    order_infos: list[tuple[Patient, CandidateInput, int, float | None, int]] = []
    for patient, candidate in orderable:
        results = compute_all_proposed_slots(
            sim,
            office_name_by_id,
            candidate,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            config=config,
        )
        weekdays = {ps.weekday for ps in results}
        best_delta = results[0].marginal_cost_minutes if results else None
        order_infos.append((patient, candidate, len(results), best_delta, len(weekdays)))

    # 3. D-1 ハイブリッド順序.
    #    第1群 = 投入可能曜日が 1 つ (詰みやすい): 候補数昇順 → delta 昇順 → pid.
    #    第2群 = 残り: 最良 delta 昇順 → pid.
    group1 = [oi for oi in order_infos if oi[4] == 1]
    group2 = [oi for oi in order_infos if oi[4] != 1]
    group1.sort(key=lambda oi: (oi[2], _delta_key(oi[3]), str(oi[0].id)))
    group2.sort(key=lambda oi: (_delta_key(oi[3]), str(oi[0].id)))
    ordered = group1 + group2

    # 前状態のメトリクス + before visit 列 (Phase 2 の仮追加より前に確定).
    result.travel_minutes_before, result.travel_km_before = _compute_travel_totals(
        sim, config=config
    )
    result.before_visits = _flatten_with_staff(sim)

    # 4. 患者を順に処理 (メモリ内バケットへ仮追加しながら).
    seq = 0
    for patient, candidate, _cc, _bd, _awc in ordered:
        pid = patient.id
        excl: list[ExcludedReasonSummary] = []
        results = compute_all_proposed_slots(
            sim,
            office_name_by_id,
            candidate,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            config=config,
            exclusions_out=excl,
        )
        best = _best_by_weekday(results)

        # 既に sim 内でこの患者が投入済みの曜日を除外 (同曜日への二重投入防止).
        # DB から読み込んだ既存 visit と仮追加済み visit の両方を含む.
        already_placed_weekdays = {
            v.weekday for b in sim.values() for v in b.visits if v.patient_id == pid
        }
        if already_placed_weekdays:
            best = {wd: ps for wd, ps in best.items() if wd not in already_placed_weekdays}

        for ps in best.values():
            _ensure_delta(ps, sim, candidate, config=config)

        desired = _desired_weekly_visits(patient)
        current_placed = sum(1 for b in sim.values() for v in b.visits if v.patient_id == pid)
        shortage = max(0, desired - current_placed)
        if shortage == 0:
            # 既に希望回数を満たしている (プール対象外). 何もしない.
            continue

        # 不足曜日ごとに delta 最小の候補を採用. 投入可能曜日数が不足数より少なければ
        # 部分投入. 決定的ソート (delta → 開始時刻 → 曜日 → コース → 拠点).
        available = sorted(
            best.values(),
            key=lambda ps: (
                _delta_key(ps.marginal_cost_minutes),
                _tmin(ps.start),
                ps.weekday,
                ps.course_code,
                str(ps.office_id),
            ),
        )
        n_place = min(shortage, len(available))
        chosen = available[:n_place]

        for ps in chosen:
            seq += 1
            _insert_placement(sim, patient, candidate, ps)
            result.placements.append(
                BulkPlacement(
                    seq=seq,
                    patient_id=pid,
                    patient_name=patient.name,
                    weekday=ps.weekday,
                    course_code=ps.course_code,
                    office_id=ps.office_id,
                    start=ps.start,
                    service_minutes=candidate.service_minutes,
                    delta_minutes=ps.marginal_cost_minutes,
                    warnings=list(ps.warnings),
                )
            )
        if chosen:
            result.placed_patients += 1

        if n_place == 0:
            # 1 枠も入らない (= 候補ゼロ). excl は results 空時に集約済み.
            reason = _pick_reason(excl)
            result.unplaced.append(
                BulkUnplaced(patient_id=pid, patient_name=patient.name, reason=reason)
            )
        elif n_place < shortage:
            # 部分投入: 投入できなかった希望曜日ごとに理由コードを付ける.
            covered = {ps.weekday for ps in chosen}
            target_wds = candidate.preferred_weekdays or frozenset(range(7))
            reasons: dict[int, str] = {}
            for wd in sorted(wd for wd in target_wds if wd not in covered):
                reasons[wd] = _weekday_exclusion_reason(
                    sim,
                    office_name_by_id,
                    candidate,
                    wd,
                    office_ids=office_ids,
                    office_code_by_id=office_code_by_id,
                    config=config,
                )
            result.partial.append(
                BulkPartial(
                    patient_id=pid,
                    patient_name=patient.name,
                    placed_days=n_place,
                    missing_days=shortage - n_place,
                    unplaced_reasons=reasons,
                )
            )

    result.placed_slots = len(result.placements)

    # 4b. 定員起因の未投入について「定員 +1 なら入る候補」件数を最終 sim 状態で算出 (A 案・方式b 橋渡し).
    #     ここまでで sim は全仮確定を含む最終状態 = 「一括で先に埋まった枠」を織り込んだ +1 判定になる.
    #     一括自体は超過を採用しない (D-2 の設計原則: 超過は 1 人ずつ・理由必須の個別フローで扱う).
    candidate_by_pid: dict[UUID, CandidateInput] = {p.id: c for p, c in orderable}
    for u in result.unplaced:
        if u.reason != "capacity_full":
            continue
        cand = candidate_by_pid.get(u.patient_id)
        if cand is None:
            continue
        u.overcapacity_available_count = _overcapacity_available_count(
            sim,
            office_name_by_id,
            cand,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            config=config,
        )
    for pt in result.partial:
        cap_wds = frozenset(
            wd for wd, reason in pt.unplaced_reasons.items() if reason == "capacity_full"
        )
        if not cap_wds:
            continue
        cand = candidate_by_pid.get(pt.patient_id)
        if cand is None:
            continue
        pt.overcapacity_available_count = _overcapacity_available_count(
            sim,
            office_name_by_id,
            cand,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            config=config,
            weekdays=cap_wds,
        )

    # 5. 後状態のメトリクス + after visit 列.
    result.travel_minutes_after, result.travel_km_after = _compute_travel_totals(sim, config=config)
    result.after_visits = _flatten_with_staff(sim)

    return result


# 除外集約の代表 reason 優先度 (pool-overview の _POOL_EXCLUSION_PRIORITY と同順).
_EXCLUSION_PRIORITY: tuple[str, ...] = (
    "capacity_full",
    "travel_shortage",
    "lunch_window",
    "no_gap",
    "course_closed",
)


def _pick_reason(excluded: list[ExcludedReasonSummary]) -> str:
    """除外集約から代表 reason を決定的に選ぶ (最多 count → 同数は優先度. 空なら no_gap)."""
    if not excluded:
        return "no_gap"

    def _priority_index(reason: str) -> int:
        try:
            return _EXCLUSION_PRIORITY.index(reason)
        except ValueError:
            return len(_EXCLUSION_PRIORITY)

    best = max(excluded, key=lambda e: (e.count, -_priority_index(e.reason)))
    return best.reason


__all__ = [
    "POOL_BULK_MAX_PATIENTS",
    "BulkPartial",
    "BulkPlacement",
    "BulkUnplaced",
    "PoolBulkResult",
    "compute_bulk_state_token",
    "simulate_pool_bulk_insert",
]
