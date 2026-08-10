"""制約 override (性別制限 / NG スタッフ) の「確認して通した」お知らせ producer.

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §7 (決定2)。

PO 決定2 = 手動経路の「素通し」をやめる。操作ユーザーに確認を出し、OK なら通す。
**通した事実を管理者へお知らせ通知**し、気づかないまま放置されない仕組みにする。

本モジュールは 2 つの役割を持つ:

1. **検査** (``collect_constraint_warnings``): コース × 新担当スタッフの組で
   性別制限 / NG スタッフの違反を洗い出す。手動経路 (``PATCH /courses/{id}``)
   の 422 確認フローと、通知本文の生成の両方で使う単一ソース。
2. **通知** (``notify_constraint_override``): active な admin へお知らせを add する
   (**commit しない** = 呼び出し側が TX 境界を握る。``services/checkin/notify.py``
   と同方針)。

冪等キー: ``reference_type='constraint_override'`` + ``reference_id=op_group_id``。
一括変更 (週 5 日分) は同一 ``op_group_id`` で 1 通に集約される。``op_group_id`` を
持たない経路は ``reference_id=None`` = 毎回通知 (NULL 同士を突き合わせて誤って
抑止しないよう、None のときは重複検査自体をスキップする)。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.notification import Notification
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit

# 性別制限の判定意味論は Layer3 割当と単一ソースにする (AND 意味論 /
# 'female_only' → 'female' 正規化)。公開エイリアス経由で参照する
# (propose_slots_service.py:70-72 と同じ流儀)。
from app.services.scheduling.layer3_assignment import (
    sex_satisfies_restrictions as _sex_satisfies_restrictions,
)

logger = logging.getLogger(__name__)

# notifications.type / reference_type に使う種別 (frontend のアイコン選択キーと共用)。
NOTIFY_CONSTRAINT_OVERRIDE: Final = "constraint_override"

# notifications.title は String(200)。日本語でも安全側に倒して切り詰める。
_TITLE_MAX_LEN: Final = 200

_WEEKDAY_JA: Final = ("月", "火", "水", "木", "金", "土", "日")

ConstraintKind = Literal["gender", "ng_staff"]


@dataclass(frozen=True)
class ConstraintWarning:
    """制約違反 1 件 (= 患者 1 名 × 種別 1 つ)."""

    kind: ConstraintKind
    patient_id: UUID
    patient_name: str
    staff_id: UUID
    staff_name: str
    # NG スタッフの理由メモ。性別違反は常に None (設計書 §7-2 の detail 形状)。
    note: str | None = None

    def to_detail(self) -> dict[str, str | None]:
        """422 detail の 1 要素 (設計書 §7-2) へ変換する."""
        return {
            "kind": self.kind,
            "patient_id": str(self.patient_id),
            "patient_name": self.patient_name,
            "staff_id": str(self.staff_id),
            "staff_name": self.staff_name,
            "note": self.note,
        }


async def collect_constraint_warnings(
    db: AsyncSession,
    *,
    course_id: UUID,
    staff: Staff,
) -> list[ConstraintWarning]:
    """コース所属患者 × 新担当スタッフの性別制限 / NG スタッフ違反を洗い出す.

    検査対象 = そのコースに載っている planned visit (``deleted_at IS NULL``) の
    患者全員。コースは (iso_year, iso_week, weekday) スコープのため、course_id で
    絞るだけで「今週分」になる。

    **手動経路の性別判定は staff.sex が None なら違反にしない**:
        エンジン (Layer3) のハード制約は ``sex is None`` を「割当不可」として扱うが
        (誤割当を防ぐ安全側)、手動経路の目的は「人が意図した割当を、危ないものだけ
        確認して通す」ことにある。性別未登録スタッフを毎回ブロックすると確認 UX が
        破綻するため、ここは提案系と同じ **誤検知回避側** に倒す
        (= 性別不明は警告を出さない)。判定の意味論そのもの (AND / '_only' 正規化)
        は ``layer3_assignment.sex_satisfies_restrictions`` と同一ソース。

    返却は決定的な順序 (患者名 → NG → 性別) にする (テスト・UI 表示の安定のため)。
    """
    patient_ids = list(
        (
            await db.scalars(
                select(Visit.patient_id)
                .where(
                    Visit.course_id == course_id,
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                )
                .distinct()
            )
        ).all()
    )
    if not patient_ids:
        return []

    patients = list(
        (
            await db.scalars(
                select(Patient)
                .where(Patient.id.in_(patient_ids), Patient.deleted_at.is_(None))
                .order_by(Patient.name, Patient.id)
            )
        ).all()
    )
    if not patients:
        return []

    # NG 行は (patient_id, staff_id) の複合 PK。当該スタッフ分のみ 1 クエリで引く。
    ng_notes: dict[UUID, str | None] = {
        row.patient_id: row.note
        for row in (
            await db.scalars(
                select(PatientNgStaff).where(
                    PatientNgStaff.patient_id.in_(patient_ids),
                    PatientNgStaff.staff_id == staff.id,
                )
            )
        ).all()
    }

    staff_name = staff.name or "担当者"
    warnings: list[ConstraintWarning] = []
    for patient in patients:
        patient_name = patient.name or "利用者"
        if patient.id in ng_notes:
            warnings.append(
                ConstraintWarning(
                    kind="ng_staff",
                    patient_id=patient.id,
                    patient_name=patient_name,
                    staff_id=staff.id,
                    staff_name=staff_name,
                    note=ng_notes[patient.id],
                )
            )
        restriction = patient.sex_restriction
        if (
            restriction
            and staff.sex is not None
            and not _sex_satisfies_restrictions(staff.sex, frozenset({restriction}))
        ):
            warnings.append(
                ConstraintWarning(
                    kind="gender",
                    patient_id=patient.id,
                    patient_name=patient_name,
                    staff_id=staff.id,
                    staff_name=staff_name,
                    note=None,
                )
            )
    return warnings


async def _active_admin_users(db: AsyncSession) -> list[User]:
    """通知ターゲット: active な admin (deleted_at IS NULL).

    ロール二軸分離 (mig 0069) 以降 'manager' アカウントは存在しないが、万一の残存行も
    受け取れるよう別名込みで引く (``services/checkin/notify.py:68-81`` と同じ)。

    **操作者本人も除外しない** = このお知らせは監査ログを兼ねるため (設計書 §7-3)。
    うるさければ本人除外に変えられる: ここで ``u.id != actor.id`` で絞ればよい。
    """
    return list(
        (
            await db.scalars(
                select(User).where(
                    User.role.in_(("admin", "manager")),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def _resolve_actor_name(db: AsyncSession, actor: User | None) -> str:
    """操作者の表示名: 紐づくスタッフ名 → username → email の順で解決する."""
    if actor is None:
        return "不明"
    if actor.staff_id is not None:
        name = await db.scalar(select(Staff.name).where(Staff.id == actor.staff_id))
        if name:
            return name
    return actor.username or actor.email or "不明"


def _build_title(kinds: set[str], warnings: list[ConstraintWarning], staff_name: str) -> str:
    if warnings:
        head = f"{warnings[0].patient_name}様"
        patient_count = len({w.patient_id for w in warnings})
        if patient_count > 1:
            head = f"{head} 他{patient_count - 1}名"
    else:
        head = "利用者"
    if "ng_staff" in kinds and "gender" in kinds:
        label = "NGスタッフ・性別制限外の割当を承認"
    elif "ng_staff" in kinds:
        label = "NGスタッフ割当を承認"
    else:
        label = "性別制限外の割当を承認"
    return f"{label}: {head} ← {staff_name}"[:_TITLE_MAX_LEN]


async def notify_constraint_override(
    db: AsyncSession,
    *,
    kind_summary: Iterable[str],
    course: Course,
    staff: Staff,
    patient_warnings: list[ConstraintWarning],
    actor: User | None,
    op_group_id: UUID | None,
) -> int:
    """制約 override を active admin へお知らせする (**commit しない**).

    Args:
        kind_summary: override した制約の種類 ('gender' / 'ng_staff')。空なら
            ``patient_warnings`` から導出する。
        course: 対象コース。**属性は呼び出し時点で読める状態であること**
            (commit 後の expire 済みオブジェクトを渡さない)。
        staff: 新しく割り当てるスタッフ。
        patient_warnings: 本文に載せる違反明細 (空でも通知は作る)。
        actor: 操作者 (監査表示用)。
        op_group_id: 冪等キー。同一操作の重複通知を防ぐ。None なら毎回通知。

    Returns:
        新規に add した通知行数。
    """
    kinds = {k for k in kind_summary if k in ("gender", "ng_staff")}
    if not kinds:
        kinds = {w.kind for w in patient_warnings}
    if not kinds:
        return 0

    users = await _active_admin_users(db)
    if not users:
        return 0

    staff_name = staff.name or "担当者"
    actor_name = await _resolve_actor_name(db, actor)
    office_name = await db.scalar(select(Office.name).where(Office.id == course.office_id))
    weekday = course.weekday
    weekday_ja = _WEEKDAY_JA[weekday] if 0 <= weekday < len(_WEEKDAY_JA) else str(weekday)

    lines = [
        f"週: {course.iso_year}年 第{course.iso_week}週（{weekday_ja}曜）",
        f"コース: {course.code}",
        f"拠点: {office_name or '不明'}",
        f"操作者: {actor_name}",
    ]
    for w in patient_warnings:
        if w.kind == "ng_staff":
            memo = f"（メモ: {w.note}）" if w.note else ""
            lines.append(f"⛔ NGスタッフ: {w.patient_name}様 ← {w.staff_name}{memo}")
        else:
            lines.append(f"🔴 性別制限外: {w.patient_name}様 ← {w.staff_name}")

    title = _build_title(kinds, patient_warnings, staff_name)
    body = "\n".join(lines)

    # 冪等化: op_group_id がある時だけ既通知を突き合わせる。None を突き合わせると
    # 「reference_id IS NULL の過去通知」全部にヒットして以後の通知が止まるため、
    # None のときは重複検査ごとスキップする (= 毎回通知)。
    already: set[UUID] = set()
    if op_group_id is not None:
        already = set(
            (
                await db.scalars(
                    select(Notification.user_id).where(
                        Notification.reference_type == NOTIFY_CONSTRAINT_OVERRIDE,
                        Notification.reference_id == op_group_id,
                    )
                )
            ).all()
        )

    created = 0
    for u in users:
        if u.id in already:
            continue
        db.add(
            Notification(
                user_id=u.id,
                type=NOTIFY_CONSTRAINT_OVERRIDE,
                title=title,
                body=body,
                reference_type=NOTIFY_CONSTRAINT_OVERRIDE,
                reference_id=op_group_id,
            )
        )
        created += 1
    return created
