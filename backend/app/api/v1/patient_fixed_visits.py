"""Patient fixed-visit pattern endpoints (W9-BE1 / W9-BE2).

GET    /patients/{patient_id}/fixed-visits[?mode=normal|special]
PUT    /patients/{patient_id}/fixed-visits   body: PatientFixedVisitsBulkPut
DELETE /patients/{patient_id}/fixed-visits?mode=normal|special
POST   /patients/{patient_id}/fixed-visits/from-week  (W9-BE2: 個別固定化)
POST   /patients/fixed-visits/from-week-bulk          (W9-BE2: 全患者一括固定化)

RBAC:
  GET             — admin / manager / staff (staff は担当患者のみ)
  PUT             — admin / manager のみ
  DELETE          — admin / manager のみ
  from-week       — admin / manager のみ
  from-week-bulk  — admin / manager のみ

UNIQUE (patient_id, mode, weekday) 違反は 409 Conflict。
"""

from __future__ import annotations

import logging
from datetime import date, time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User, normalize_user_role
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.v2.patient_fixed_visit import (
    PatientFixedVisitMode,
    PatientFixedVisitsBulkPut,
    PatientFixedVisitsBulkPutResponse,
    PatientFixedVisitV2Base,
    PatientFixedVisitV2Read,
    PfvCourseLoadCell,
    PfvCourseLoadResponse,
    PfvValidateRequest,
    PfvValidateResponse,
    PfvValidationWarningOut,
    PfvWeekSyncOut,
)
from app.services.constraint_override_notify import (
    ConstraintWarning,
    collect_constraint_warnings_for_patients,
    constraint_confirmation_detail,
    notify_constraint_override_for_course,
    resolve_week_course_for_template,
)
from app.services.patient_status_sync import is_schedulable_status
from app.services.scheduling.auto_allocator_v2 import (
    COURSE_MAX_MINUTES,
    reset_visits_to_fixed,
    resolve_reset_office_ids,
)
from app.services.scheduling.config import load_scheduling_config
from app.services.scheduling.guards import patient_not_active_detail
from app.services.scheduling.layer1_expander import _is_special_week_active
from app.services.scheduling.pfv_validator import errors_message, validate_pfv_changes
from app.services.scheduling.visit_frequency import frequency_rule_active, pfv_visits_in_week
from app.services.scheduling.visit_week_carry import (
    carried_visit_week as _carried_visit_week,
)
from app.services.scheduling.visit_week_carry import (
    load_visit_week_by_slot as _load_visit_week_by_slot,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _ensure_patient_exists(db: AsyncSession, patient_id: UUID) -> Patient:
    patient = await db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.deleted_at.is_(None),
        )
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


async def _staff_owns_patient(db: AsyncSession, *, staff_id: UUID, patient_id: UUID) -> bool:
    """staff_id が patient_id の担当者であるかを判定する (W7-BE1 パターン流用).

    判定条件 (どちらか一方でも該当すれば True):
      1. visits.primary_staff_id = staff_id の visit が当該患者に存在
      2. visit_staff_assignments 経由で staff_id が当該患者の visit に
         アサインされている

    soft-delete された visit (deleted_at IS NOT NULL) は除外する。
    """
    stmt_primary = (
        select(Visit.id)
        .where(
            Visit.patient_id == patient_id,
            Visit.primary_staff_id == staff_id,
            Visit.deleted_at.is_(None),
        )
        .limit(1)
    )
    if await db.scalar(stmt_primary) is not None:
        return True

    stmt_assign = (
        select(VisitStaffAssignment.staff_id)
        .join(Visit, Visit.id == VisitStaffAssignment.visit_id)
        .where(
            Visit.patient_id == patient_id,
            VisitStaffAssignment.staff_id == staff_id,
            Visit.deleted_at.is_(None),
        )
        .limit(1)
    )
    return await db.scalar(stmt_assign) is not None


async def _check_read_access(db: AsyncSession, user: User, patient_id: UUID) -> None:
    """admin/manager は全患者可; staff は担当患者のみ可 (範囲外は 403)."""
    if normalize_user_role(user.role) == "admin":
        return
    if user.role == "staff":
        if user.staff_id is None or not await _staff_owns_patient(
            db, staff_id=user.staff_id, patient_id=patient_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff is not assigned to this patient",
            )
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")


async def _validate_sub_office_items(
    db: AsyncSession,
    items: list[PatientFixedVisitV2Base],
) -> None:
    """Phase E-5 (項目 ⑥B): sub_office_id 関連のバリデーション.

    items 内の各エントリについて以下を検証する:
      1. ``sub_office_id`` が指定された場合、その office が DB に存在する.
      2. ``sub_office_id`` が指定されかつ ``course_template_id`` も指定された場合、
         course_template.office_id == sub_office_id である (= サブ拠点の course を
         サブ拠点経由で指していること; クロスオフィス参照を禁止).

    違反時は 422 を raise する.
    """
    # 一括ロード (N+1 回避)
    sub_office_ids = {item.sub_office_id for item in items if item.sub_office_id is not None}
    if not sub_office_ids:
        return

    office_rows = await db.scalars(
        select(Office.id).where(
            Office.id.in_(sub_office_ids),
            Office.deleted_at.is_(None),
        )
    )
    existing_office_ids = set(office_rows.all())

    # course_template_id × sub_office_id の対応関係を一括 lookup する
    ct_ids = {item.course_template_id for item in items if item.course_template_id is not None}
    ct_office_by_id: dict[UUID, UUID] = {}
    if ct_ids:
        ct_rows = await db.execute(
            select(CourseTemplate.id, CourseTemplate.office_id).where(
                CourseTemplate.id.in_(ct_ids),
                CourseTemplate.deleted_at.is_(None),
            )
        )
        for ct_id, ct_office_id in ct_rows.all():
            ct_office_by_id[ct_id] = ct_office_id

    for item in items:
        if item.sub_office_id is None:
            continue
        if item.sub_office_id not in existing_office_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"sub_office_id が存在しません: {item.sub_office_id}",
            )
        if item.course_template_id is None:
            continue
        ct_office_id = ct_office_by_id.get(item.course_template_id)
        if ct_office_id is None:
            # course_template が存在しない (or soft-deleted) → ここでは触れない
            # (course_template_id 側の検証は既存 FK / 他層に委ねる)
            continue
        if ct_office_id != item.sub_office_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"sub_office_id ({item.sub_office_id}) と course_template の office "
                    f"({ct_office_id}) が一致しません"
                ),
            )


async def _commit_or_409(db: AsyncSession) -> None:
    """Commit し、UNIQUE 違反を 409 に変換する."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                # W37 Phase 1: UNIQUE は (patient_id, mode, weekday, slot_index).
                detail="Conflict: duplicate (patient_id, mode, weekday, slot_index)",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc


# ---------------------------------------------------------------------------
# W37 hotfix C-1: helpers — multi-staff (slot 0/1) 対応の visit→PFV 取込
# ---------------------------------------------------------------------------


def _group_visits_by_weekday(visit_rows) -> dict[int, list]:
    """visits を weekday (0=Mon..6=Sun) 単位でグルーピング."""
    by_wd: dict[int, list] = {}
    for v in visit_rows:
        wd = v.visit_date.weekday()
        by_wd.setdefault(wd, []).append(v)
    return by_wd


def _is_valid_multi_staff_pair(visits: list, *, requires_multi: bool) -> bool:
    """同一 weekday に 2 visit が並んだとき multi-staff のペアとして妥当か.

    妥当条件:
      - patient.requires_multiple_staff = True
      - 2 visit がともに同じ visit_group_id を持つ (= ペア)
    """
    if not requires_multi:
        return False
    if len(visits) != 2:
        return False
    group_ids = {v.visit_group_id for v in visits}
    if None in group_ids:
        return False
    return len(group_ids) == 1


def _invalid_weekdays(visit_rows, *, requires_multi: bool) -> list[int]:
    """409 対象となる「異常な重複 weekday」リストを返す.

    1 visit / 0 visit は OK. 2 visit はペアとして妥当 (上記) なら OK.
    3+ visit は常に NG. 2 visit でペア条件不成立なら NG.
    """
    bad: list[int] = []
    for wd, visits in _group_visits_by_weekday(visit_rows).items():
        if len(visits) <= 1:
            continue
        if _is_valid_multi_staff_pair(visits, requires_multi=requires_multi):
            continue
        bad.append(wd)
    return sorted(bad)


def _weekday_groups_are_valid(visit_rows, *, requires_multi: bool) -> bool:
    """bulk 経路の判定: 異常重複が 1 件でもあれば False (= 当該患者 skip)."""
    return not _invalid_weekdays(visit_rows, requires_multi=requires_multi)


async def _load_pfv_pin_partition(
    db: AsyncSession,
    *,
    patient_id: UUID,
    mode: str,
) -> tuple[set[tuple[int, int]], dict[tuple[int, time], str]]:
    """既存 PFV (patient, mode) を読み、ピン消失修正に必要な 2 つを返す.

    Wave U-0 (§2.2-4, from-week-bulk ピン消失修正 / 既存バグ):
      - ``pinned_slots``: 残存させる pinned 行の (weekday, slot_index) 集合.
        週 visits からの再作成時、この slot と衝突する INSERT は skip する
        (完全固定の pin を全件保存が上書きしないようにする).
      - ``movability_by_wd_start``: (weekday, start_time) → movability. 非 pinned
        行を置換した新行に、同一 (weekday, start_time) の旧行から movability を
        引き継ぐために使う.
    """
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient_id,
                PatientFixedVisit.mode == mode,
            )
        )
    ).all()
    pinned_slots = {(r.weekday, r.slot_index) for r in rows if r.is_pinned}
    movability_by_wd_start = {(r.weekday, r.start_time): r.movability for r in rows}
    return pinned_slots, movability_by_wd_start


async def _load_off_week_slots_to_keep(
    db: AsyncSession, *, patient: Patient, mode: str, week_monday: date
) -> tuple[list[UUID], set[tuple[int, int]]]:
    """週の訪問から枠を作り直すとき、その週に訪問しない枠 (残す枠) を返す.

    訪問頻度 (biweekly-monthly-design-2026-10-30.md §3-3): 隔週・月の第何週の患者
    (設定が足りている方) は、A の週の訪問から作り直しても B の枠を消さない (逆も同じ).
    B の枠はその週に訪問が無いのが正しいため、週の訪問に無いことを「消えた」と読まない.
    作り直すのは、その週に訪問する枠 (A・毎回・第 N 週に当たる曜日) だけ.
    残す枠の (曜日, slot) に同じ週の訪問があっても、残す枠を優先して INSERT しない
    (赤ピンの行と同じ扱い・UNIQUE 衝突を避ける).

    毎週の患者・設定不足の患者・特別週の枠 (mode='special') は空 (挙動不変).

    Returns:
        (残す行の id, 残す行の (weekday, slot_index))。
    """
    if mode != "normal" or not frequency_rule_active(patient.weekly_pattern):
        return [], set()
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == mode,
            )
        )
    ).all()
    keep = [r for r in rows if not pfv_visits_in_week(patient.weekly_pattern, r, week_monday)]
    return [r.id for r in keep], {(r.weekday, r.slot_index) for r in keep}


async def _insert_pfv_from_visits(
    db: AsyncSession,
    *,
    patient: Patient,
    mode: str,
    visit_rows,
    pinned_slots: set[tuple[int, int]] | None = None,
    movability_by_wd_start: dict[tuple[int, time], str] | None = None,
    visit_week_by_slot: dict[tuple[int, int], str] | None = None,
) -> None:
    """visits を PatientFixedVisit に書き戻す (slot_index を決定論的に割当).

    - 単独 visit (1 件) → slot_index=0 で 1 行 INSERT (regression 維持).
    - multi-staff ペア (2 件 + visit_group_id 共有 + requires_multiple_staff=true) →
      visit.id 昇順で sort して slot 0/1 を決定論的に割当てて 2 行 INSERT.
    - その他 (異常重複) は呼び出し前段で弾かれている前提.

    Wave U-0 (§2.2-4): ``pinned_slots`` に含まれる (weekday, slot_index) は残存
    pinned 行が占有しているため INSERT を skip する (重複防止 = UNIQUE 衝突回避 +
    pin 保護). ``movability_by_wd_start`` があれば、同一 (weekday, start_time) の
    旧行から movability を引き継ぐ (無ければ 'unknown').

    NOTE: course_template_id 順での sort も検討したが、course_id が NULL の visit が
          混じる場合に不安定になるため id 昇順を採用 (FE 側の visitsByGroupId と同一基準).
    """
    pinned_slots = pinned_slots or set()
    movability_by_wd_start = movability_by_wd_start or {}
    visit_week_by_slot = visit_week_by_slot or {}
    requires_multi = bool(patient.requires_multiple_staff)
    by_wd = _group_visits_by_weekday(visit_rows)

    for wd in sorted(by_wd.keys()):
        visits = by_wd[wd]

        if len(visits) == 2 and _is_valid_multi_staff_pair(visits, requires_multi=requires_multi):
            # multi-staff ペア: visit.id 昇順で slot 0/1 を割当 (決定論).
            sorted_pair = sorted(visits, key=lambda v: str(v.id))
            for slot_idx, v in enumerate(sorted_pair):
                if (wd, slot_idx) in pinned_slots:
                    # 残存 pinned 行と同一 slot → skip (重複 / pin 上書き防止).
                    continue
                mov = movability_by_wd_start.get((wd, v.start_time), "unknown")
                await _add_one_pfv(
                    db,
                    patient=patient,
                    mode=mode,
                    visit=v,
                    slot_index=slot_idx,
                    movability=mov,
                    visit_week=visit_week_by_slot.get((wd, slot_idx), "every"),
                )
        else:
            # 1 visit (通常パターン) — slot_index=0 で 1 行
            v = visits[0]
            if (wd, 0) in pinned_slots:
                continue
            mov = movability_by_wd_start.get((wd, v.start_time), "unknown")
            await _add_one_pfv(
                db,
                patient=patient,
                mode=mode,
                visit=v,
                slot_index=0,
                movability=mov,
                visit_week=visit_week_by_slot.get((wd, 0), "every"),
            )


async def _add_one_pfv(
    db: AsyncSession,
    *,
    patient: Patient,
    mode: str,
    visit,
    slot_index: int,
    movability: str = "unknown",
    visit_week: str = "every",
) -> None:
    """visit から 1 行の PatientFixedVisit を組み立てて add する.

    Wave U-0 (§2.2-4): ``movability`` を引き継ぐ (from-week-bulk ピン消失修正の
    一部. 非 pinned 行の置換で旧行の可動域を維持する). 既定は 'unknown'.
    """
    duration_min = int(
        (visit.end_time.hour * 60 + visit.end_time.minute)
        - (visit.start_time.hour * 60 + visit.start_time.minute)
    )
    if duration_min <= 0:
        duration_min = 30
    # W22: visit.course_id → course.template_id を逆引き
    visit_course_template_id = None
    if visit.course_id is not None:
        course = await db.scalar(select(Course).where(Course.id == visit.course_id))
        if course is not None:
            visit_course_template_id = course.template_id
    # 赤ピンは 2 値 (locked / unknown) のみ書く. is_pinned は厳密なミラー.
    red = movability == "locked"
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode=mode,
            weekday=visit.visit_date.weekday(),
            start_time=visit.start_time,
            duration_min=duration_min,
            course_template_id=visit_course_template_id,
            slot_index=slot_index,
            movability="locked" if red else "unknown",
            is_pinned=red,
            # 0102: 置き換え前の同じ (曜日, slot) の訪問する週を引き継ぐ (無ければ毎回).
            visit_week=visit_week,
        )
    )


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{patient_id}/fixed-visits",
    response_model=list[PatientFixedVisitV2Read],
    summary="患者の固定訪問パターン一覧取得",
)
async def get_fixed_visits(
    patient_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    mode: PatientFixedVisitMode | None = Query(
        default=None, description="フィルタ: normal / special。未指定は両方。"
    ),
) -> list[PatientFixedVisitV2Read]:
    await _ensure_patient_exists(db, patient_id)
    await _check_read_access(db, user, patient_id)

    stmt = (
        select(PatientFixedVisit)
        .where(PatientFixedVisit.patient_id == patient_id)
        .order_by(
            PatientFixedVisit.mode,
            PatientFixedVisit.weekday,
            PatientFixedVisit.slot_index,
        )
    )
    if mode is not None:
        stmt = stmt.where(PatientFixedVisit.mode == mode)

    rows = (await db.scalars(stmt)).all()
    return [PatientFixedVisitV2Read.model_validate(r) for r in rows]


@router.post(
    "/{patient_id}/fixed-visits/validate",
    response_model=PfvValidateResponse,
    summary="固定訪問パターンの dry-run 検証 (保存しない・admin/manager)",
)
async def validate_fixed_visits(
    patient_id: UUID,
    body: PfvValidateRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> PfvValidateResponse:
    """案Z (PO 決定 2026-08-09): マスタ編集の入力中ライブ検査。

    PUT と同じ再検証カーネル (患者間衝突 V3 / 昼休み V4 / コース容量 V5 /
    2名体制 V7 / 今週の実配置 V8 / コース担当の NG・性別 V9) を
    **一切書き込まずに** 実行し、警告を常設表示用に返す。
    保存前に「当てずっぽう」を可視化するための read-only エンドポイント。
    """
    await _ensure_patient_exists(db, patient_id)
    config = await load_scheduling_config(db)
    validation = await validate_pfv_changes(db, patient_id, body.items, body.mode, config=config)
    return PfvValidateResponse(
        warnings=[
            PfvValidationWarningOut(
                code=w.code, message=w.message, weekday=w.weekday, severity=w.severity
            )
            for w in validation.warnings
        ]
    )


@router.get(
    "/{patient_id}/fixed-visits/course-load",
    response_model=PfvCourseLoadResponse,
    summary="コース負荷 (曜日×コースの他患者合計・空き表示用・admin/manager)",
)
async def get_fixed_visits_course_load(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    mode: PatientFixedVisitMode = Query(default="normal"),
) -> PfvCourseLoadResponse:
    """案Z: コースセレクトの空き情報 (○△×・残容量) の材料。

    同一拠点・同 mode の **他患者** の型を (曜日 × コース) で集計して返す。
    対象患者自身の行は含めない (編集中の値は FE が足す)。
    """
    await _ensure_patient_exists(db, patient_id)
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id))
    config = await load_scheduling_config(db)

    cells: list[PfvCourseLoadCell] = []
    if patient is not None and patient.primary_office_id is not None:
        rows = (
            await db.execute(
                select(PatientFixedVisit)
                .join(Patient, Patient.id == PatientFixedVisit.patient_id)
                .where(
                    PatientFixedVisit.mode == mode,
                    PatientFixedVisit.patient_id != patient_id,
                    Patient.primary_office_id == patient.primary_office_id,
                    Patient.deleted_at.is_(None),
                    Patient.status == "active",
                )
            )
        ).scalars()
        agg: dict[tuple[int, UUID | None], dict] = {}
        for pfv in rows:
            key = (pfv.weekday, pfv.course_template_id)
            cell = agg.setdefault(key, {"minutes": 0, "patients": set()})
            cell["minutes"] += pfv.duration_min
            cell["patients"].add(pfv.patient_id)
        cells = [
            PfvCourseLoadCell(
                weekday=wd,
                course_template_id=tpl,
                used_minutes=v["minutes"],
                patient_count=len(v["patients"]),
            )
            for (wd, tpl), v in sorted(agg.items(), key=lambda kv: (kv[0][0], str(kv[0][1] or "")))
        ]

    return PfvCourseLoadResponse(
        course_max_minutes=COURSE_MAX_MINUTES,
        max_patients_per_course=config.max_patients_per_course,
        cells=cells,
    )


@router.put(
    "/{patient_id}/fixed-visits",
    response_model=PatientFixedVisitsBulkPutResponse,
    summary="患者の固定訪問パターン一括上書き (admin/manager のみ)",
)
async def put_fixed_visits(
    patient_id: UUID,
    body: PatientFixedVisitsBulkPut,
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin"))],
) -> PatientFixedVisitsBulkPutResponse:
    return await apply_put_fixed_visits(db, patient_id, body, current_user)


async def apply_put_fixed_visits(
    db: AsyncSession,
    patient_id: UUID,
    body: PatientFixedVisitsBulkPut,
    current_user: User,
    *,
    commit: bool = True,
) -> PatientFixedVisitsBulkPutResponse:
    """患者の固定訪問パターンを一括上書きする (提案採用「固定訪問週間に登録」の実体).

    NG スタッフ / 性別制限 (§7-2): ``change_scope='pattern_and_week'`` のときのみ
    「登録する患者 × 配置先コースの現担当」を検査する。**``pattern_only`` /
    ``week_only`` 相当はスキップ**する — 型だけの更新では対象週が特定できず、
    「どのコースの誰に当たるか」が決まらないため (週が決まる経路 =
    ``pattern_and_week`` / ``visit-move-week-only`` / ``place-and-fix`` で塞ぐ)。
    検査対象は **変更/新設される行のみ** (既存とまったく同じ行は担当が変わらない)。
    違反があれば ``code=constraint_confirmation_required`` の 422、
    ``acknowledge_constraint_warnings=true`` の再送で続行 + 管理者へお知らせ。
    """
    patient = await _ensure_patient_exists(db, patient_id)

    # 入口ガード (Phase 2・設計 §7-3(d)): **型の編集は非稼働でも許可**する
    # (復帰時に型から作り直すため・§3-3)。週へ反映する pattern_and_week だけ 422。
    # detail は単一ソース (guards.patient_not_active_detail) に `allowed_scope` を足す。
    if body.change_scope == "pattern_and_week" and not is_schedulable_status(patient.status):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=patient_not_active_detail(patient, allowed_scope="pattern_only"),
        )

    # 定員超過の管理者相談プロセス (方式b): 採用時の理由記録 (監査はミドルウェア任せ).
    # 値があれば info ログに残すだけで、検証や挙動変更はしない (容量チェックは warning-only).
    if body.capacity_override_reason:
        logger.info(
            "capacity_override on PUT fixed-visits: patient_id=%s reason=%s",
            patient_id,
            body.capacity_override_reason,
        )

    # Wave U-1 (§2.2 A 経路): pattern_and_week は今週再生成のため iso_year/iso_week 必須.
    # 破壊的な DELETE→INSERT の前に 422 で弾く (欠落時にトランザクションを汚さない).
    if body.change_scope == "pattern_and_week" and (body.iso_year is None or body.iso_week is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="change_scope=pattern_and_week のときは iso_year / iso_week が必須です",
        )

    # Phase E-5 (項目 ⑥B): sub_office_id 関連の事前検証.
    await _validate_sub_office_items(db, body.items)

    # P0-2: 適用前再検証 (read-only). pinned 保護違反 (has_errors) は削除前に 422 を返し
    # トランザクションを汚さない. warning (患者間衝突 / 昼休み / 容量) は削除→INSERT 後に
    # レスポンスへ載せる.
    config = await load_scheduling_config(db)
    validation = await validate_pfv_changes(db, patient_id, body.items, body.mode, config=config)
    if validation.has_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": errors_message(
                    validation.errors, "完全固定 (is_pinned) の枠を変更・削除しようとしています"
                ),
                "violations": [
                    {
                        "code": w.code,
                        "message": w.message,
                        "weekday": w.weekday,
                        "severity": w.severity,
                    }
                    for w in validation.errors
                ],
            },
        )

    # NG スタッフ / 性別制限の確認フロー (§7-2)。
    # pinned 保護の 422 (= override 不可の絶対ブロック) の **後**・破壊的な DELETE の
    # **前** に置く: 絶対ブロックを先に効かせ、確認で通せる警告は後 (courses.py の
    # 新人 422 → 制約確認と同じ順序)。
    _cw: list[ConstraintWarning] = []
    _cw_by_course: list[tuple[Course, list[ConstraintWarning]]] = []
    if body.change_scope == "pattern_and_week":
        assert body.iso_year is not None and body.iso_week is not None  # 上で 422 済み
        # 既存行と完全一致する item は「維持」= 担当が変わらないので検査対象外にする。
        _existing = {
            (row.weekday, row.slot_index): (
                row.start_time,
                row.duration_min,
                row.course_template_id,
            )
            for row in (
                await db.scalars(
                    select(PatientFixedVisit).where(
                        PatientFixedVisit.patient_id == patient_id,
                        PatientFixedVisit.mode == body.mode,
                    )
                )
            ).all()
        }
        # 同一 (template, weekday) は 1 回だけ引く (N+1 禁止)。
        _checked: set[tuple[UUID, int]] = set()
        for item in body.items:
            if item.course_template_id is None:
                continue
            if _existing.get((item.weekday, item.slot_index)) == (
                item.start_time,
                item.duration_min,
                item.course_template_id,
            ):
                continue
            key = (item.course_template_id, item.weekday)
            if key in _checked:
                continue
            _checked.add(key)
            course = await resolve_week_course_for_template(
                db,
                course_template_id=item.course_template_id,
                iso_year=body.iso_year,
                iso_week=body.iso_week,
                weekday=item.weekday,
            )
            if course is None:
                continue
            _w = await collect_constraint_warnings_for_patients(
                db, course=course, patient_ids=[patient_id]
            )
            if _w:
                _cw.extend(_w)
                _cw_by_course.append((course, _w))
    if _cw and not body.acknowledge_constraint_warnings:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=constraint_confirmation_detail(_cw),
        )

    # 0102: 訪問する週を省略した item は既存行の値を引き継ぐ (旧 FE が送らなくても A/B が
    # 消えない). 決め方は _carried_visit_week (同じ曜日・slot → 同じ slot で 1 つの値 → every).
    _visit_week_by_slot = await _load_visit_week_by_slot(db, patient_id=patient_id, mode=body.mode)
    _resolved_visit_weeks: list[str] = [
        item.visit_week
        if item.visit_week is not None
        else _carried_visit_week(
            _visit_week_by_slot, weekday=item.weekday, slot_index=item.slot_index
        )
        for item in validation.corrected_items
    ]
    # 0102: 2 名体制の方は、同じ曜日の slot 0 と slot 1 の訪問する週をそろえる.
    # ずれると週の生成 (曜日ごとに 2 名で 1 回) と前の週のコピー (slot ごと) で
    # 作る訪問が食い違うため、保存の段階で止める.
    if body.mode == "normal" and bool(patient.requires_multiple_staff):
        _vw_by_wd: dict[int, set[str]] = {}
        for item, vw in zip(validation.corrected_items, _resolved_visit_weeks, strict=True):
            _vw_by_wd.setdefault(item.weekday, set()).add(vw)
        _mismatch = sorted(wd for wd, vws in _vw_by_wd.items() if len(vws) > 1)
        if _mismatch:
            _names = "・".join("月火水木金土日"[wd] for wd in _mismatch)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"2 名で訪問する方は、同じ曜日の 2 つの枠の「訪問する週」を"
                    f"そろえてください（{_names}曜日）。"
                ),
            )

    # 1 TX: 当該 (patient_id, mode) を DELETE → INSERT
    await db.execute(
        delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == body.mode,
        )
    )
    # P2-A: INSERT には validation.corrected_items (V6 で pinned 行の movability を
    # 'locked' 矯正済み) を使う. body.items をそのまま使うと矯正が DB に反映されない.
    for item, _vw in zip(validation.corrected_items, _resolved_visit_weeks, strict=True):
        db.add(
            PatientFixedVisit(
                patient_id=patient_id,
                mode=body.mode,
                weekday=item.weekday,
                start_time=item.start_time,
                duration_min=item.duration_min,
                # W22 Phase A: course_template_id (任意) を保存. 未指定なら NULL.
                course_template_id=item.course_template_id,
                # W37 Phase 1: slot_index (default 0) を反映. 1 名体制では常に 0.
                slot_index=item.slot_index,
                # Phase E-5 (項目 ⑥B): サブ拠点 ID (任意).
                sub_office_id=item.sub_office_id,
                # 統合 (2026-08-09): is_pinned は movability='locked' の非推奨ミラー。
                # クライアント値に依らずサーバ側で同期する (単一の真実 = movability)。
                is_pinned=(item.movability == "locked"),
                # P2-A (§1.3): 可動域フラグを運搬. 省略すると保存のたび 'unknown' に
                # 戻る (P0-2 の is_pinned BLOCKER と同型の罠). 必ず引き継ぐこと.
                movability=item.movability,
                # 0102: 訪問する週 (省略は既存行から引き継ぐ・_carried_visit_week).
                visit_week=_vw,
            )
        )

    # §7-3: acknowledge を通した事実を管理者へお知らせ (commit 前 = PFV 更新と同一 TX)。
    # 採用単位の識別子が無いため op_group_id=None (= 毎回通知)。
    for _c, _w in _cw_by_course:
        await notify_constraint_override_for_course(
            db, course=_c, warnings=_w, actor=current_user, op_group_id=None
        )
    if commit:
        await _commit_or_409(db)
    else:
        # コースの作業場の一括登録 (template_ws): 呼び出し側が 1 トランザクションで commit する.
        # pattern_only 専用 (週の再生成は commit 済みを前提にするため).
        assert body.change_scope == "pattern_only"
        await db.flush()

    # Wave U-1 (§2.2 A 経路 = プール採用): PFV 置換コミット後、同一エンドポイント内で
    # 当該患者の今週 visits を PFV から再生成する (reset_visits_to_fixed の 1 患者版).
    # source='manual' / status='completed' 保護は全患者版と同一. mode=body.mode に
    # 依らず reset は normal PFV から再生成する (プール採用は normal 前提).
    week_sync: PfvWeekSyncOut | None = None
    if body.change_scope == "pattern_and_week":
        assert body.iso_year is not None and body.iso_week is not None  # 上で 422 済み
        office_ids = await resolve_reset_office_ids(db, patient_id)
        try:
            sync_result = await reset_visits_to_fixed(
                db,
                iso_year=body.iso_year,
                iso_week=body.iso_week,
                office_ids=office_ids,
                mode="legacy",
                dry_run=False,
                config=config,
                patient_id=patient_id,
            )
            await db.commit()
        except IntegrityError as exc:
            # 同時実行の衝突は再試行可能 (PUT は同一 body で冪等) → 409。
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="他のユーザーが同じ週を処理中です。もう一度実行してください。",
            ) from exc
        except Exception:
            # レビュー指摘 (U-1 MEDIUM): PFV は既にコミット済みのため、ここで 500 を返すと
            # 「型には登録されたのに失敗と表示される」二重に紛らわしい状態になる。
            # week_sync=None の 200 で返し、FE が「固定訪問週間に登録済み・今週への反映は
            # 未実施 → 週を生成を再実行」と正確に案内する (visits 側は rollback 済みで無傷)。
            await db.rollback()
            logger.warning(
                "PUT fixed-visits pattern_and_week: week sync failed after PFV commit "
                "(patient_id=%s iso=%s-W%s)",
                patient_id,
                body.iso_year,
                body.iso_week,
                exc_info=True,
            )
        else:
            week_sync = PfvWeekSyncOut(
                visits_regenerated=int(sync_result.get("visits_regenerated", 0)),
                visits_soft_deleted=int(sync_result.get("visits_soft_deleted", 0)),
            )

    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id == patient_id,
                PatientFixedVisit.mode == body.mode,
            )
            .order_by(PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        )
    ).all()
    return PatientFixedVisitsBulkPutResponse(
        items=[PatientFixedVisitV2Read.model_validate(r) for r in rows],
        warnings=[
            PfvValidationWarningOut(
                code=w.code,
                message=w.message,
                weekday=w.weekday,
                severity=w.severity,
            )
            for w in validation.warnings_only
        ],
        week_sync=week_sync,
    )


@router.delete(
    "/{patient_id}/fixed-visits",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="患者の固定訪問パターン削除 (mode 指定必須; admin/manager のみ)",
)
async def delete_fixed_visits(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    mode: PatientFixedVisitMode = Query(..., description="削除する mode: normal または special"),
) -> None:
    await _ensure_patient_exists(db, patient_id)

    await db.execute(
        delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == mode,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# W9-BE2: 全患者一括固定化 (collection-level; {id} より先に登録すること)
# ---------------------------------------------------------------------------


@router.post(
    "/fixed-visits/from-week-bulk",
    summary="全患者一括固定化 (admin/manager のみ)",
    status_code=status.HTTP_200_OK,
)
async def from_week_bulk(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    iso_year: int = Query(...),
    iso_week: int = Query(...),
    mode: PatientFixedVisitMode | None = Query(default=None),
) -> dict:
    """全 active 患者に対し当該週 visits を patient_fixed_visits に書き戻す.

    冪等性保証: 既存 (patient_id, mode) の **非 pinned 行のみ** 削除 → INSERT を
    1 TX で実行 (pinned 行は残す).

    Wave U-0 (§2.2-4, 既存バグ修正): 旧実装は既存 PFV を無条件 DELETE →
    is_pinned=False で再作成していたため、**全件保存でピン留めが消失**していた.
    修正後は「全件保存しても pinned 行は残る」— is_pinned=True の行は削除せず、
    週 visits からの再作成時に同一 (weekday, slot) となる行は INSERT を skip する.
    非 pinned 行は従来どおり置換し、同一 (weekday, start_time) の旧行から
    movability を引き継ぐ.
    """
    # 全 active 患者を取得
    patients_rows = (
        await db.scalars(
            select(Patient).where(
                Patient.status == "active",
                Patient.deleted_at.is_(None),
            )
        )
    ).all()

    # 当該週の月曜を導出 (weekday 計算用)
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={iso_year} week={iso_week}",
        ) from exc

    week_sunday = date.fromordinal(week_monday.toordinal() + 6)

    updated_patient_ids: list[UUID] = []

    for patient in patients_rows:
        # mode 自動推定
        effective_mode: str = mode or (
            "special" if _is_special_week_active(patient, iso_year, iso_week) else "normal"
        )

        # 当該週の visits 取得 (deleted_at IS NULL)
        visit_rows = (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == patient.id,
                    Visit.visit_date >= week_monday,
                    Visit.visit_date <= week_sunday,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()

        # W37 hotfix C-1: weekday 重複チェック (multi-staff 対応).
        # 同一 weekday 2 件かつ visit_group_id 共有 + patient.requires_multiple_staff=true
        # の場合は許容 (slot 0/1 ペアとして扱う). それ以外の重複は bulk では当該患者だけ skip.
        if not _weekday_groups_are_valid(
            visit_rows, requires_multi=patient.requires_multiple_staff
        ):
            continue

        # Wave U-0 (§2.2-4): 削除前に pinned 行 / movability を退避.
        pinned_slots, movability_by_wd_start = await _load_pfv_pin_partition(
            db, patient_id=patient.id, mode=effective_mode
        )
        visit_week_by_slot = await _load_visit_week_by_slot(
            db, patient_id=patient.id, mode=effective_mode
        )
        # 訪問頻度: その週に訪問しない枠 (隔週の B の枠など) は消さずに残す.
        keep_ids, keep_slots = await _load_off_week_slots_to_keep(
            db, patient=patient, mode=effective_mode, week_monday=week_monday
        )

        # 既存削除 — ただし pinned 行 (is_pinned=True) は残す (ピン消失防止).
        _del = delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient.id,
            PatientFixedVisit.mode == effective_mode,
            PatientFixedVisit.is_pinned.is_(False),
        )
        if keep_ids:
            _del = _del.where(PatientFixedVisit.id.not_in(keep_ids))
        await db.execute(_del)

        # INSERT (C-1: multi-staff slot 0/1 対応 / U-0: pinned slot skip + movability 引継ぎ)
        await _insert_pfv_from_visits(
            db,
            patient=patient,
            mode=effective_mode,
            visit_rows=visit_rows,
            pinned_slots=pinned_slots | keep_slots,
            movability_by_wd_start=movability_by_wd_start,
            visit_week_by_slot=visit_week_by_slot,
        )

        updated_patient_ids.append(patient.id)

    await db.commit()

    return {
        "updated_count": len(updated_patient_ids),
        "patients": [str(pid) for pid in updated_patient_ids],
    }


# 赤ピン (完全固定) の付け外しは患者マスタの PUT /{patient_id}/fixed-visits だけ
# (2026-10-12 赤ピン整理 K-6)。盤面用の PATCH /fixed-visits/{id}/pin と
# POST /fixed-visits/pin/bulk は削除した。

# ---------------------------------------------------------------------------
# W9-BE2: 個別固定化 API
# ---------------------------------------------------------------------------


@router.post(
    "/{patient_id}/fixed-visits/from-week",
    response_model=list[PatientFixedVisitV2Read],
    summary="当該週の visits を patient_fixed_visits に書き戻し (admin/manager のみ)",
)
async def from_week(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    iso_year: int = Query(...),
    iso_week: int = Query(...),
    mode: PatientFixedVisitMode | None = Query(default=None),
) -> list[PatientFixedVisitV2Read]:
    """当該週の visits を patient_fixed_visits に書き戻す.

    mode 未指定: patient.special_week_active に当該週があれば 'special'、なければ 'normal'。
    visit_staff_assignments は読まない (時刻 + duration のみ)。
    visits が 0 件 → 既存 fixed_visits の **非 pinned 行** を削除して残りを返す。
    同一 weekday に複数 visit → 409。

    Wave U-0 (§2.2-4, from-week-bulk と同一のピン消失修正): pinned 行 (is_pinned=True)
    は削除せず残す. 週 visits からの再作成時、同一 (weekday, slot) となる行は INSERT を
    skip する. 非 pinned 行は従来どおり置換し、同一 (weekday, start_time) の旧行から
    movability を引き継ぐ. — 全件保存 (from-week) してもピンが残る.
    """
    patient = await _ensure_patient_exists(db, patient_id)

    # mode 自動推定
    effective_mode: str = mode or (
        "special" if _is_special_week_active(patient, iso_year, iso_week) else "normal"
    )

    # 当該週の月曜 / 日曜を導出
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={iso_year} week={iso_week}",
        ) from exc

    week_sunday = date.fromordinal(week_monday.toordinal() + 6)

    # 当該週の visits 取得 (deleted_at IS NULL)
    visit_rows = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient_id,
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()

    # W37 hotfix C-1: 同一 weekday 重複チェック (multi-staff 対応).
    # 同一 weekday に 2 件 + visit_group_id 共有 + patient.requires_multiple_staff=true なら
    # slot 0/1 ペアとして許容. それ以外の重複は従来通り 409.
    invalid_weekdays = _invalid_weekdays(visit_rows, requires_multi=patient.requires_multiple_staff)
    if invalid_weekdays:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Duplicate visits on weekday(s) {invalid_weekdays} for patient {patient_id} "
                "(multi-staff requires exactly 2 visits sharing visit_group_id)"
            ),
        )

    # Wave U-0 (§2.2-4): 削除前に pinned 行 / movability を退避.
    pinned_slots, movability_by_wd_start = await _load_pfv_pin_partition(
        db, patient_id=patient_id, mode=effective_mode
    )
    visit_week_by_slot = await _load_visit_week_by_slot(
        db, patient_id=patient_id, mode=effective_mode
    )
    # 訪問頻度: その週に訪問しない枠 (隔週の B の枠など) は消さずに残す.
    keep_ids, keep_slots = await _load_off_week_slots_to_keep(
        db, patient=patient, mode=effective_mode, week_monday=week_monday
    )

    # 1 TX: 非 pinned 行のみ削除 → INSERT (pinned 行は残す = ピン消失防止)
    _del = delete(PatientFixedVisit).where(
        PatientFixedVisit.patient_id == patient_id,
        PatientFixedVisit.mode == effective_mode,
        PatientFixedVisit.is_pinned.is_(False),
    )
    if keep_ids:
        _del = _del.where(PatientFixedVisit.id.not_in(keep_ids))
    await db.execute(_del)

    # INSERT (C-1: multi-staff slot 0/1 対応 / U-0: pinned slot skip + movability 引継ぎ)
    await _insert_pfv_from_visits(
        db,
        patient=patient,
        mode=effective_mode,
        visit_rows=visit_rows,
        pinned_slots=pinned_slots | keep_slots,
        movability_by_wd_start=movability_by_wd_start,
        visit_week_by_slot=visit_week_by_slot,
    )

    await _commit_or_409(db)

    # 結果を返す
    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id == patient_id,
                PatientFixedVisit.mode == effective_mode,
            )
            .order_by(PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        )
    ).all()
    return [PatientFixedVisitV2Read.model_validate(r) for r in rows]
