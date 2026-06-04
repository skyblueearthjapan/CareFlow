"""proposed_visits → normal PFV 設定の共通サービス (StageB-backend).

「新規患者の作成 + スケジュール確定を 1 承認で」行うために、承認 applier
(``_apply_patient_create``) から呼ばれる共通ロジックを切り出したもの。

責務:
    1 患者 (``patient_id``) に対し ``proposed_visits`` を受け取り、その患者の
    **normal モード** PatientFixedVisit (slot_index=0) を一括設定する。

設計方針:
    - 既存の PUT ``/patients/{id}/fixed-visits`` (mode='normal') と同じく
      「当該 (patient_id, mode='normal') を全削除 → INSERT」で冪等に上書きする。
    - special / 他患者の PFV には一切触れない。
    - duration_min → start/end は PatientFixedVisit が start_time + duration_min を
      保持する規約 (既存 fixed-visits bulk と同一) に合わせ、duration_min をそのまま
      格納する (end_time は持たない)。
    - course の解決は patient_excel.parse_course_token を流用する:
        - ``course_template_id`` (直接 UUID) が指定されればそれを優先。
        - ``course_code`` (拠点付きトークン 例 "稲A") があれば
          (office_code, label) → office → course_template を解決。
        - 解決できなければ course_template_id=None (best-effort; PFV 自体は保持)。

トランザクション境界:
    本関数は ``db.flush()`` のみ行い ``commit()`` / ``rollback()`` は呼ばない。
    呼び出し元 (applier / HTTP 層) が同一 TX で commit/rollback を司る。
"""

from __future__ import annotations

from datetime import time
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.patient_excel.schema import parse_course_token


class ProposedVisitsError(Exception):
    """proposed_visits の検証エラー。

    呼び出し元 (applier) はこれを業務エラーとして 422 に翻訳する想定。
    """

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.http_status = http_status


def _coerce_time(value: Any) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            h, m = value.split(":")[:2]
            return time(int(h), int(m))
        except (ValueError, IndexError):
            return None
    return None


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _resolve_course_template_id(
    db: AsyncSession,
    *,
    course_template_id: UUID | None,
    course_code: str | None,
) -> UUID | None:
    """course_template_id (直接) または course_code (拠点付きトークン) を解決する.

    - ``course_template_id`` が指定されれば最優先で採用 (存在検証はしない:
      FK / 他層に委ねる。SET NULL FK なので不正値でも PFV は壊さない)。
    - ``course_code`` トークン (例 "稲A") は parse_course_token で
      (office_code, label) に分解し、当該 office の course_template を逆引きする。
    - いずれも解決できなければ None (= best-effort; course 未解決でも PFV は保持)。
    """
    if course_template_id is not None:
        return course_template_id
    if not course_code:
        return None
    parsed = parse_course_token(course_code)
    if parsed is None:
        return None
    office_code, label = parsed
    office = await db.scalar(
        select(Office).where(Office.code == office_code, Office.deleted_at.is_(None))
    )
    if office is None:
        return None
    ct = await db.scalar(
        select(CourseTemplate).where(
            CourseTemplate.office_id == office.id,
            CourseTemplate.label == label,
            CourseTemplate.deleted_at.is_(None),
        )
    )
    return ct.id if ct is not None else None


async def apply_proposed_visits_as_normal_pfv(
    db: AsyncSession,
    *,
    patient: Patient,
    proposed_visits: list[dict[str, Any]],
) -> int:
    """``proposed_visits`` を patient の normal PFV (slot_index=0) として設定する.

    各 item: ``{weekday:int(0-6), start_time:"HH:MM", duration_min:int,
              course_template_id?:UUID, course_code?:str}``

    挙動:
        - 当該 (patient_id, mode='normal') を全削除 → INSERT (PUT 同等の冪等上書き)。
        - special / 他患者の PFV は触らない。
        - proposed_visits が空リストの場合は何もしない (既存 normal PFV も保持)。
          → 患者作成のみの現行挙動を壊さないため、呼び出し元は空/None のとき
            本関数を呼ばない契約とするが、空リストでも安全に no-op とする。

    戻り値: INSERT した PFV 行数。

    検証 (失敗時 ProposedVisitsError):
        - item が dict でない / weekday が 0-6 外 / start_time 不正 →422
        - duration_min が 1..480 外 →422
        - 同一 weekday の重複 (normal/slot0 は weekday ごと 1 行) →422
    """
    if not proposed_visits:
        return 0

    if not isinstance(proposed_visits, list):
        raise ProposedVisitsError("proposed_visits must be a list", http_status=422)

    validated: list[dict[str, Any]] = []
    seen_weekdays: set[int] = set()

    for i, item in enumerate(proposed_visits):
        if not isinstance(item, dict):
            raise ProposedVisitsError(f"proposed_visits[{i}] must be an object", http_status=422)

        weekday = item.get("weekday")
        if not isinstance(weekday, int) or isinstance(weekday, bool) or weekday < 0 or weekday > 6:
            raise ProposedVisitsError(
                f"proposed_visits[{i}].weekday must be int 0-6", http_status=422
            )
        if weekday in seen_weekdays:
            raise ProposedVisitsError(
                f"proposed_visits[{i}]: duplicate weekday {weekday}", http_status=422
            )
        seen_weekdays.add(weekday)

        start_time = _coerce_time(item.get("start_time"))
        if start_time is None:
            raise ProposedVisitsError(
                f"proposed_visits[{i}].start_time must be 'HH:MM'", http_status=422
            )

        duration_raw = item.get("duration_min", 30)
        try:
            duration_min = int(duration_raw)
        except (TypeError, ValueError):
            raise ProposedVisitsError(
                f"proposed_visits[{i}].duration_min must be int", http_status=422
            ) from None
        if duration_min < 1 or duration_min > 480:
            raise ProposedVisitsError(
                f"proposed_visits[{i}].duration_min must be 1..480", http_status=422
            )

        course_template_id = await _resolve_course_template_id(
            db,
            course_template_id=_coerce_uuid(item.get("course_template_id")),
            course_code=item.get("course_code"),
        )

        validated.append(
            {
                "weekday": weekday,
                "start_time": start_time,
                "duration_min": duration_min,
                "course_template_id": course_template_id,
            }
        )

    # 既存 normal PFV を全削除 → INSERT (PUT /fixed-visits と同等の冪等上書き).
    # special / 他患者には触れない (mode='normal' & patient_id で限定).
    await db.execute(
        delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient.id,
            PatientFixedVisit.mode == "normal",
        )
    )
    for spec in validated:
        db.add(
            PatientFixedVisit(
                patient_id=patient.id,
                mode="normal",
                weekday=spec["weekday"],
                start_time=spec["start_time"],
                duration_min=spec["duration_min"],
                course_template_id=spec["course_template_id"],
                slot_index=0,
            )
        )
    await db.flush()
    return len(validated)


__all__ = [
    "ProposedVisitsError",
    "_resolve_course_template_id",
    "apply_proposed_visits_as_normal_pfv",
]
