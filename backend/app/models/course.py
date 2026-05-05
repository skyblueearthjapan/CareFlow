"""Course (コース) — Layer 2 (§3.6.3 / §4.5) で生成される患者グループ.

W2-BE4 (`docs/plans/v2-allocation-redesign.md` v0.9 §4.5 / 実装手順書 §3 W2-BE4).

## 概念

- **対象期間**: 1 週間 × 1 曜日
- **構成**: A〜D の 4 つ + M (マネージャー枠 / オーバーフロー専用)
- **紐付け**: その曜日に訪問予定の患者から最大 6 名を 1 コースに割り当て (§3.6.5)
- **担当**: 当該週のスタッフ (Layer 3 でローテーションで決定)

## 識別子

(iso_year, iso_week, weekday, code) UNIQUE

## 状態 (course_status enum, §4.5)

| 状態             | 意味                                            | 次の遷移先                |
| ---              | ---                                             | ---                       |
| `proposed`       | Layer 2 のコース分け案を生成した状態 (未確定)   | → `course_fixed`          |
| `course_fixed`   | 構成が固定済み、Layer 3 は未実行                | → `staff_assigned`        |
| `staff_assigned` | Layer 3 完了、スタッフ割当済み                  | (週終了でアーカイブ)      |

タイムスタンプ補助カラム:
- `course_fixed_at` — 構成固定日時
- `staff_assigned_at` — スタッフ割当日時

assigned_staff_id は Layer 3 (W4-BE9) で決定される担当スタッフ ID。
本チケット (CRUD のみ) では NULL のまま運用する。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# course_status 値 (§4.5).
# DB 物理型は String(16). Pydantic / API レイヤで CourseStatus enum バリデートする。
COURSE_STATUS_PROPOSED: str = "proposed"
COURSE_STATUS_COURSE_FIXED: str = "course_fixed"
COURSE_STATUS_STAFF_ASSIGNED: str = "staff_assigned"

# course code 値 (§3.6.5).
# A/B/C/D = 通常コース, M = マネージャー枠 (4 コース外)
COURSE_CODE_VALUES: tuple[str, ...] = ("A", "B", "C", "D", "M")


class Course(Base, TimestampMixin):
    """コース (§4.5).

    1 週間 × 1 曜日 × A/B/C/D/M で一意。
    """

    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 識別子 (§4.5)
    iso_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    iso_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(2), nullable=False)

    # 状態 (§4.5)
    course_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=COURSE_STATUS_PROPOSED
    )

    # 担当スタッフ (Layer 3 で決定. W2-BE4 の CRUD では NULL)
    assigned_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )

    course_fixed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    staff_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # (iso_year, iso_week, weekday, code) UNIQUE — §4.5
        UniqueConstraint(
            "iso_year",
            "iso_week",
            "weekday",
            "code",
            name="uq_courses_year_week_weekday_code",
        ),
        # コース記号は A/B/C/D/M のみ (§3.6.5)
        CheckConstraint(
            "code IN ('A', 'B', 'C', 'D', 'M')",
            name="ck_courses_code_v2",
        ),
        # 状態は 3 値のみ (§4.5)
        CheckConstraint(
            "course_status IN ('proposed', 'course_fixed', 'staff_assigned')",
            name="ck_courses_status_v2",
        ),
        # 曜日は 0-6
        CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_courses_weekday_range",
        ),
        # 一覧クエリ用 (週でフィルタ)
        Index("ix_courses_year_week", "iso_year", "iso_week"),
        # 担当スタッフ別の一覧 (Layer 3 のローテ集計で使う)
        Index("ix_courses_assigned_staff", "assigned_staff_id"),
    )
