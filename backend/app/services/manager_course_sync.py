"""Manager course template sync service — W16 Phase A / A-4.

Wave 16 では Layer 3 でマネージャー (``staff.role='manager'``) を専用の
M コースに固定で割り付けるため、各拠点に対し M ラベルの ``course_templates``
が「常に active manager 数と同じ枚数」存在する状態を維持する必要がある。

本サービスはスタッフ CRUD (新規作成 / status 変更 / 退職) のタイミングで
呼び出され、対象拠点 (``office_id``) の M / M2 / .. ラベル course_templates
を自動同期する。

## sync ロジック (``sync_manager_course_templates``)

1. 対象拠点の active manager 数 N を集計
   - ``staff.status='active' AND staff.role='manager'
     AND staff.deleted_at IS NULL AND staff.primary_office_id = office_id``
2. 既存の ``course_templates`` から M 系ラベル (M, M2, M3, ..., M9) を取得
   - ``deleted_at IS NULL`` のみ
3. 不足分を新規 INSERT
   - capacity = 月7/火7/水7/木7/金7/土5/日0
4. 余剰分を論理削除 (``deleted_at`` セット)

## トランザクション

呼び出し側 (HTTP layer / PendingRequestApplier) が ``commit`` / ``rollback``
を司る。本サービスは ``await db.flush()`` のみ。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_template import CourseTemplate
from app.models.staff import Staff

# 1 拠点あたり想定する最大マネージャー数 (M, M2, .., M9 まで)
MAX_MANAGERS_PER_OFFICE: Final[int] = 9

# capacity プリセット (月7/火7/水7/木7/金7/土5/日0)
_DEFAULT_CAPACITY_MON: Final[int] = 7
_DEFAULT_CAPACITY_TUE: Final[int] = 7
_DEFAULT_CAPACITY_WED: Final[int] = 7
_DEFAULT_CAPACITY_THU: Final[int] = 7
_DEFAULT_CAPACITY_FRI: Final[int] = 7
_DEFAULT_CAPACITY_SAT: Final[int] = 5
_DEFAULT_CAPACITY_SUN: Final[int] = 0


def _label_for(index: int) -> str:
    """0-based index を ラベル ``M`` / ``M2`` / ``M3`` / ... に変換する."""
    if index == 0:
        return "M"
    return f"M{index + 1}"


def _is_manager_label(label: str) -> bool:
    """ラベルが M 系 (M / M2 / M3 / ...) かどうかを判定する."""
    if not label:
        return False
    if label == "M":
        return True
    if not label.startswith("M"):
        return False
    suffix = label[1:]
    return suffix.isdigit() and 2 <= int(suffix) <= MAX_MANAGERS_PER_OFFICE


async def _count_active_managers(db: AsyncSession, *, office_id: UUID) -> int:
    """対象拠点の active manager 数を返す (W16-A-4)."""
    stmt = select(Staff).where(
        Staff.status == "active",
        Staff.role == "manager",
        Staff.deleted_at.is_(None),
        Staff.primary_office_id == office_id,
    )
    rows = list((await db.scalars(stmt)).all())
    return len(rows)


async def sync_manager_course_templates(
    db: AsyncSession,
    *,
    office_id: UUID,
) -> tuple[int, int]:
    """対象拠点の M 系 course_templates を active manager 数に合わせて同期する.

    Args:
        db: 共有 SQLAlchemy セッション (commit/rollback は呼出側).
        office_id: 同期対象の拠点 ID.

    Returns:
        ``(created, soft_deleted)`` のタプル.
        - created: 新規 INSERT した course_template 行数
        - soft_deleted: ``deleted_at`` をセットした既存行数

    Notes:
        - 既存の M 系ラベルが手動作成され ``deleted_at`` が NULL なら、
          (M / M2 / .. / MN) の N 以下のラベルは触らない (= active manager
          数が増えるとさらに上のラベルを追加するだけ).
        - active manager 数 < 既存 M 系数 のときは、ラベル番号の大きい
          ものから順に論理削除する.
    """
    # ---- 1) active manager 数 ----
    n_managers = await _count_active_managers(db, office_id=office_id)
    n_managers = min(n_managers, MAX_MANAGERS_PER_OFFICE)

    # ---- 2) 既存 M 系 course_templates 取得 ----
    stmt = select(CourseTemplate).where(
        CourseTemplate.office_id == office_id,
        CourseTemplate.deleted_at.is_(None),
    )
    existing_rows = list((await db.scalars(stmt)).all())
    existing_by_label: dict[str, CourseTemplate] = {
        row.label: row for row in existing_rows if _is_manager_label(row.label)
    }

    expected_labels: set[str] = {_label_for(i) for i in range(n_managers)}
    existing_labels: set[str] = set(existing_by_label.keys())

    to_create = expected_labels - existing_labels
    to_soft_delete = existing_labels - expected_labels

    now = datetime.now(UTC)

    # ---- 3) 不足分 INSERT ----
    created = 0
    for label in sorted(to_create, key=lambda lbl: int(lbl[1:]) if lbl != "M" else 1):
        new_row = CourseTemplate(
            office_id=office_id,
            label=label,
            capacity_mon=_DEFAULT_CAPACITY_MON,
            capacity_tue=_DEFAULT_CAPACITY_TUE,
            capacity_wed=_DEFAULT_CAPACITY_WED,
            capacity_thu=_DEFAULT_CAPACITY_THU,
            capacity_fri=_DEFAULT_CAPACITY_FRI,
            capacity_sat=_DEFAULT_CAPACITY_SAT,
            capacity_sun=_DEFAULT_CAPACITY_SUN,
            notes="auto-synced (W16): manager course",
        )
        db.add(new_row)
        created += 1

    # ---- 4) 余剰分 soft delete ----
    soft_deleted = 0
    for label in to_soft_delete:
        existing_by_label[label].deleted_at = now
        soft_deleted += 1

    if created or soft_deleted:
        await db.flush()

    return (created, soft_deleted)


async def sync_all_offices_for_staff(
    db: AsyncSession,
    *,
    staff: Staff | None,
    previous_office_id: UUID | None = None,
) -> None:
    """staff 行の primary_office_id (現/旧) 双方を sync する shorthand.

    staff の primary_office_id を更新したり、role/status を変更したときに
    呼ばれる。

    Args:
        db: 共有セッション.
        staff: 対象 staff (None なら旧 office のみ sync).
        previous_office_id: 直前の primary_office_id (変更前). 省略時は None.
    """
    seen: set[UUID] = set()
    if staff is not None and staff.primary_office_id is not None:
        seen.add(staff.primary_office_id)
    if previous_office_id is not None:
        seen.add(previous_office_id)

    for office_id in seen:
        await sync_manager_course_templates(db, office_id=office_id)


__all__ = [
    "MAX_MANAGERS_PER_OFFICE",
    "sync_all_offices_for_staff",
    "sync_manager_course_templates",
]
