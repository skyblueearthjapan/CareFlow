"""P0-2 再検証カーネル: PFV 変更の適用前再検証 (N-4 / I-03, I-04, I-05).

PUT /patients/{id}/fixed-visits（手動系統＋候補採用系統）の削除→INSERT の**前**に、
恒久パターン (``PatientFixedVisit``) に対する最小再検証を行う **read-only** カーネル.

検証項目 (設計書 docs/plans/p0-2-apply-safety-net-design.md §2):
    - V2  pinned 保護（同一性規約: 保持=OK / 変更・削除=422）      → error
    - V3  患者間時間衝突（同コース同曜日・90分占有込み・前方/後方制約） → warning
          (W-12a: slot0 に加え slot1 も他患者衝突検査対象に含める)
    - V4  H10 昼休み重複（``_is_in_lunch_break`` + config lunch 窓）  → warning
    - V5  コース容量 480 分 / 6 名 超過                              → warning
    - V7  2名体制の相方枠なし（W-12a: 片肺の曜日・非ブロッキング）    → warning

設計方針:
    - 距離・移動・バッファー・同住所・90 分占有・昼休み・容量のプリミティブは
      ``proposal_solver`` / ``auto_allocator_v2`` から **import して再利用** する
      (ロジック複製禁止). これにより自動割当・提案ソルバと挙動が一致する.
    - V3 の対象は **PFV テーブル（恒久パターン同士）**. 当週 placed visits は見ない
      (PFV=恒久宣言. 当週衝突は reset-to-fixed 系で別途検出).
    - 検証は SELECT のみ (DB を変更しない).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.schemas.v2.patient_fixed_visit import PatientFixedVisitV2Base
from app.services.scheduling.auto_allocator_v2 import (
    COURSE_MAX_MINUTES,
    _add_minutes,
    _is_in_lunch_break,
    _time_to_min,
)
from app.services.scheduling.config import SchedulingConfig
from app.services.scheduling.proposal_solver import (
    ExistingVisit,
    _course_total_minutes_from_existing,
    _existing_occupancy_end,
    _is_same_address,
    _travel_buffer_between,
)

# ---------------------------------------------------------------------------
# 警告 code (設計書 §3 の 4 種)
# ---------------------------------------------------------------------------
CODE_PINNED = "pinned_protection"  # V2 (error)
CODE_PATIENT_CONFLICT = "patient_time_conflict"  # V3 (warning)
CODE_LUNCH = "lunch_break_overlap"  # V4 (warning)
CODE_CAPACITY = "course_capacity_exceeded"  # V5 (warning)
CODE_MOVABILITY_CORRECTED = "movability_corrected"  # V6 (warning)
CODE_MULTI_STAFF_INCOMPLETE_PAIR = "multi_staff_incomplete_pair"  # V7 (warning)

_WEEKDAY_JP: tuple[str, ...] = ("月", "火", "水", "木", "金", "土", "日")


def _wd_name(weekday: int) -> str:
    return _WEEKDAY_JP[weekday] if 0 <= weekday <= 6 else str(weekday)


# ---------------------------------------------------------------------------
# 結果データ構造
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PfvValidationWarning:
    """再検証で検出した 1 件の指摘.

    severity="error" は 422 対象 (pinned 保護違反), "warning" は続行可.
    """

    code: str
    message: str
    weekday: int
    severity: str  # "error" | "warning"


@dataclass(frozen=True)
class PfvValidationResult:
    """再検証結果. ``warnings`` は error/warning の両方を含む.

    ``corrected_items`` は V6 (movability 矯正) 適用後の items. 呼出側 (PUT INSERT) は
    ``proposed_items`` ではなく **これ** を保存すること (pinned 行の movability='locked'
    矯正を DB に反映するため). 矯正が無い場合も全 items をそのまま含む.
    """

    warnings: list[PfvValidationWarning]
    has_errors: bool
    corrected_items: list[PatientFixedVisitV2Base] = field(default_factory=list)

    @property
    def errors(self) -> list[PfvValidationWarning]:
        return [w for w in self.warnings if w.severity == "error"]

    @property
    def warnings_only(self) -> list[PfvValidationWarning]:
        return [w for w in self.warnings if w.severity == "warning"]


# ---------------------------------------------------------------------------
# V2: pinned 保護 (同一性規約)
# ---------------------------------------------------------------------------

# 保持とみなす完全一致の対象属性.
_IdentityTuple = tuple[int, int, time, int, UUID | None, UUID | None, bool]


def _identity_of_item(item: PatientFixedVisitV2Base) -> _IdentityTuple:
    return (
        item.weekday,
        item.slot_index,
        item.start_time,
        item.duration_min,
        item.sub_office_id,
        item.course_template_id,
        item.is_pinned,
    )


def _identity_of_row(row: PatientFixedVisit) -> _IdentityTuple:
    return (
        row.weekday,
        row.slot_index,
        row.start_time,
        row.duration_min,
        row.sub_office_id,
        row.course_template_id,
        bool(row.is_pinned),
    )


def _check_pinned(
    existing_rows: list[PatientFixedVisit],
    proposed_items: list[PatientFixedVisitV2Base],
) -> list[PfvValidationWarning]:
    """V2: 既存 pinned 行が body に完全一致で含まれていなければ error.

    設計書 §2.1: 「pinned が存在したら 422」ではない.
        - body に既存 pinned 行と完全一致する行がある → 保持とみなし OK
        - 完全一致が無い (=削除 or 属性変更) → 422 (error)
    """
    body_identities = {_identity_of_item(i) for i in proposed_items}
    out: list[PfvValidationWarning] = []
    for row in existing_rows:
        if not row.is_pinned:
            continue
        if _identity_of_row(row) in body_identities:
            continue  # 完全一致 = 保持
        out.append(
            PfvValidationWarning(
                code=CODE_PINNED,
                message=(
                    f"{_wd_name(row.weekday)}曜 枠{row.slot_index} のピン留めされた枠を"
                    "変更・削除しようとしています。"
                    "ピン留めを解除してから変更してください。"
                ),
                weekday=row.weekday,
                severity="error",
            )
        )
    return out


# ---------------------------------------------------------------------------
# V3: 患者間時間衝突 (前方/後方制約)
# ---------------------------------------------------------------------------


def _find_conflict(
    proposed: ExistingVisit,
    others: list[ExistingVisit],
    *,
    config: SchedulingConfig,
) -> ExistingVisit | None:
    """proposed が同コースの他患者訪問と時間衝突するか (solver 前方/後方制約と同一規則).

    - 同住所 (<=100m) の相手は「同時刻ペア」として許容 (衝突対象外).
    - 異住所の相手とは移動 + バッファーの分離が必要:
        * proposed が相手より後: ``相手の占有終端 + 移動 <= proposed.start`` (前方制約)
        * proposed が相手より前: ``proposed.end + 移動 <= 相手.start`` (後方制約)
      いずれかが破れたら衝突 (相手 ExistingVisit を返す).
    """
    all_visits = [*others, proposed]
    p_start = _time_to_min(proposed.start_time)
    p_end = _time_to_min(proposed.end_time)
    for e in others:
        if e.patient_id == proposed.patient_id:
            continue
        if _is_same_address(proposed.lat, proposed.lng, e.lat, e.lng):
            continue  # 同住所ペアは許容
        travel = _travel_buffer_between(proposed.lat, proposed.lng, e.lat, e.lng, config=config)
        e_start = _time_to_min(e.start_time)
        e_occ_end = _time_to_min(_existing_occupancy_end(e, all_visits))
        if p_start >= e_start:
            # proposed が後方: 前 visit の占有終端 + 移動 が proposed.start を超えたら衝突.
            if e_occ_end + travel > p_start:
                return e
        else:
            # proposed が前方: proposed.end + 移動 が 次 visit.start を超えたら衝突.
            if p_end + travel > e_start:
                return e
    return None


# ---------------------------------------------------------------------------
# 内部: 他患者情報の保持
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _OtherVisit:
    """他患者 PFV から組み立てた 1 訪問 (名前付き ExistingVisit)."""

    ev: ExistingVisit
    patient_name: str


CourseKey = tuple[int, object]  # (weekday, course_template_id)


# ---------------------------------------------------------------------------
# V6: pinned 行の movability 矯正 (含意 is_pinned=True ⇒ movability='locked')
# ---------------------------------------------------------------------------


def _correct_pinned_movability(
    proposed_items: list[PatientFixedVisitV2Base],
) -> tuple[list[PatientFixedVisitV2Base], list[PfvValidationWarning]]:
    """V6: is_pinned=True かつ movability != 'locked' の行を 'locked' に矯正する.

    設計書 §1.1 / §1.3: is_pinned=True ⇒ movability='locked' の含意を、DB CHECK では
    なくここで担保する (既存 PATCH pin 経路を壊さないため). 矯正は **in-place ではなく
    コピー** (``model_copy``) で行い、呼出側の元 items を汚さない.

    Returns:
        (矯正済み items, 矯正 warning のリスト). 矯正が不要なら元と同じ内容の新リスト
        + 空 warning を返す. ``validate_pfv_changes`` は V2 同一性比較の **前** にこれを
        呼び、以降の検証は矯正済みリストで行う (同一性タプルに movability は含めない).
    """
    corrected: list[PatientFixedVisitV2Base] = []
    warnings: list[PfvValidationWarning] = []
    for item in proposed_items:
        if item.is_pinned and item.movability != "locked":
            corrected.append(item.model_copy(update={"movability": "locked"}))
            warnings.append(
                PfvValidationWarning(
                    code=CODE_MOVABILITY_CORRECTED,
                    message=(
                        f"{_wd_name(item.weekday)}曜 枠{item.slot_index} はピン留めのため、"
                        "可動域を「完全固定」に自動調整しました。"
                    ),
                    weekday=item.weekday,
                    severity="warning",
                )
            )
        else:
            corrected.append(item)
    return corrected, warnings


async def validate_pfv_changes(
    db: AsyncSession,
    patient_id: UUID,
    proposed_items: list[PatientFixedVisitV2Base],
    mode: str = "normal",
    *,
    config: SchedulingConfig,
) -> PfvValidationResult:
    """PFV 変更の適用前再検証 (read-only).

    Args:
        db: セッション (SELECT のみ実行).
        patient_id: 対象患者.
        proposed_items: PUT body の items (削除→INSERT 予定の新パターン).
        mode: 'normal' / 'special'. 他患者 PFV の照合も同一 mode で行う.
        config: 事業所別スケジューリング設定 (呼出側で load_scheduling_config 済).

    Returns:
        PfvValidationResult. ``has_errors=True`` (pinned 違反) の場合、呼出側は
        削除前に 422 を返すこと (トランザクションを汚さない).
    """
    warnings: list[PfvValidationWarning] = []

    # --- V6: pinned 行の movability 矯正 (V2 同一性比較の前に実施) ----------
    # is_pinned=True かつ movability != 'locked' の行を 'locked' に矯正する.
    # 以降の検証 (V2/V3/V4/V5) は矯正済みリストを使い、呼出側もこの corrected_items を
    # INSERT する (元 body.items は汚さない).
    proposed_items, v6_warnings = _correct_pinned_movability(proposed_items)
    warnings.extend(v6_warnings)

    # --- V2: pinned 保護 (Q1: 既存 PFV 行) -------------------------------
    existing_rows = list(
        (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == patient_id,
                    PatientFixedVisit.mode == mode,
                )
            )
        ).all()
    )
    warnings.extend(_check_pinned(existing_rows, proposed_items))

    # --- V4: H10 昼休み重複 (座標不要・per item) --------------------------
    lunch_s = config.lunch_window_start.strftime("%H:%M")
    lunch_e = config.lunch_window_end.strftime("%H:%M")
    for item in proposed_items:
        end_t = _add_minutes(item.start_time, item.duration_min)
        if _is_in_lunch_break(
            item.start_time,
            end_t,
            window_start=config.lunch_window_start,
            window_end=config.lunch_window_end,
        ):
            warnings.append(
                PfvValidationWarning(
                    code=CODE_LUNCH,
                    message=(
                        f"{_wd_name(item.weekday)}曜 {item.start_time.strftime('%H:%M')} の枠が"
                        f"昼休み（{lunch_s}-{lunch_e}）と重なります。"
                    ),
                    weekday=item.weekday,
                    severity="warning",
                )
            )

    # --- V3 / V5: 他患者 PFV との衝突・容量 (座標・拠点が必要) --------------
    # Q2: 対象患者 (lat/lng/primary_office_id).
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id))

    # --- V7: 2 名体制の相方枠なし (非ブロッキング警告・座標不要) -----------
    # W-12a (D-4): normal mode で requires_multiple_staff 患者の稼働曜日に slot0/slot1 の
    # 片方しか無い (片肺) → warning. 採用 UI が常に両 slot を送れば発生しないが、手動編集で
    # 片肺になった場合に「黙って visit が生成されない」を防ぐための注意喚起 (N-7).
    if mode == "normal" and patient is not None and bool(patient.requires_multiple_staff):
        slots_by_weekday: dict[int, set[int]] = {}
        for item in proposed_items:
            slots_by_weekday.setdefault(item.weekday, set()).add(item.slot_index)
        for wd in sorted(slots_by_weekday):
            if {0, 1} <= slots_by_weekday[wd]:
                continue  # slot0/slot1 揃い = 貫通する.
            warnings.append(
                PfvValidationWarning(
                    code=CODE_MULTI_STAFF_INCOMPLETE_PAIR,
                    message=(
                        f"{_wd_name(wd)}曜は 2 名体制ですが相方の枠が揃っていません。"
                        "2 名分 (同時刻・別コース) を登録してください。"
                    ),
                    weekday=wd,
                    severity="warning",
                )
            )

    if (
        patient is not None
        and patient.primary_office_id is not None
        and patient.lat is not None
        and patient.lng is not None
    ):
        p_lat = float(patient.lat)
        p_lng = float(patient.lng)

        # 提案アイテムを course 単位 (weekday, course_template_id) に振り分け.
        proposed_by_course: dict[CourseKey, list[ExistingVisit]] = {}
        for item in proposed_items:
            ev = ExistingVisit(
                start_time=item.start_time,
                end_time=_add_minutes(item.start_time, item.duration_min),
                lat=p_lat,
                lng=p_lng,
                service_minutes=item.duration_min,
                patient_id=str(patient_id),
            )
            key: CourseKey = (item.weekday, item.course_template_id)
            proposed_by_course.setdefault(key, []).append(ev)

        # Q3: 同一拠点・同 mode の他患者 PFV を 1 クエリで取得.
        # apply_individual_proposal と同等パターン (Patient を join して座標を解決).
        # W-12a: slot_index=0 に限定せず slot1 も衝突検査対象に含める (2 名体制の相方枠を
        # 見落とさない). 他患者の slot0/slot1 は別コース (course_template_id 別) なので、
        # 下記 others_by_course の (weekday, course_template_id) 単位で自然に振り分く.
        other_rows = (
            await db.execute(
                select(PatientFixedVisit, Patient)
                .join(Patient, Patient.id == PatientFixedVisit.patient_id)
                .where(
                    PatientFixedVisit.mode == mode,
                    PatientFixedVisit.patient_id != patient_id,
                    Patient.primary_office_id == patient.primary_office_id,
                    Patient.deleted_at.is_(None),
                    Patient.status == "active",
                    Patient.lat.is_not(None),
                    Patient.lng.is_not(None),
                )
            )
        ).all()

        others_by_course: dict[CourseKey, list[_OtherVisit]] = {}
        for pfv, other_patient in other_rows:
            ev = ExistingVisit(
                start_time=pfv.start_time,
                end_time=_add_minutes(pfv.start_time, pfv.duration_min),
                lat=float(other_patient.lat),
                lng=float(other_patient.lng),
                service_minutes=pfv.duration_min,
                patient_id=str(pfv.patient_id),
            )
            key = (pfv.weekday, pfv.course_template_id)
            others_by_course.setdefault(key, []).append(
                _OtherVisit(ev=ev, patient_name=other_patient.name)
            )

        # 変更対象 course のみ評価する (無関係な既存過密は警告しない).
        for key, proposed_evs in proposed_by_course.items():
            weekday = key[0]
            others = others_by_course.get(key, [])
            other_evs = [o.ev for o in others]

            # V3: 各提案枠が他患者訪問と衝突しないか.
            for pev in proposed_evs:
                conflict = _find_conflict(pev, other_evs, config=config)
                if conflict is not None:
                    other_name = next(
                        (o.patient_name for o in others if o.ev is conflict),
                        "他患者",
                    )
                    warnings.append(
                        PfvValidationWarning(
                            code=CODE_PATIENT_CONFLICT,
                            message=(
                                f"{_wd_name(weekday)}曜 "
                                f"{pev.start_time.strftime('%H:%M')} の枠が "
                                f"{other_name} 様の訪問と重なる可能性があります"
                                "（移動時間込み）。"
                            ),
                            weekday=weekday,
                            severity="warning",
                        )
                    )

            # V5: コース容量 (人数 / 総所要分). 対象 course の全訪問で評価.
            course_note = "（コース未指定）" if key[1] is None else ""
            all_evs = [*other_evs, *proposed_evs]
            distinct_patients = {ev.patient_id for ev in all_evs if ev.patient_id is not None}
            if len(distinct_patients) > config.max_patients_per_course:
                warnings.append(
                    PfvValidationWarning(
                        code=CODE_CAPACITY,
                        message=(
                            f"{_wd_name(weekday)}曜{course_note}のコース人数"
                            f" {len(distinct_patients)} 名が"
                            f"上限 {config.max_patients_per_course} 名を超えます。"
                        ),
                        weekday=weekday,
                        severity="warning",
                    )
                )
            total_min = _course_total_minutes_from_existing(all_evs, config=config)
            if total_min > COURSE_MAX_MINUTES:
                warnings.append(
                    PfvValidationWarning(
                        code=CODE_CAPACITY,
                        message=(
                            f"{_wd_name(weekday)}曜{course_note}のコース総所要 {total_min} 分が"
                            f"上限 {COURSE_MAX_MINUTES} 分を超えます。"
                        ),
                        weekday=weekday,
                        severity="warning",
                    )
                )

    has_errors = any(w.severity == "error" for w in warnings)
    return PfvValidationResult(
        warnings=warnings,
        has_errors=has_errors,
        corrected_items=proposed_items,
    )
