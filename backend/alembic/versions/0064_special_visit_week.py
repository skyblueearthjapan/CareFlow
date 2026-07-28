"""特別訪問週間 (special visit week) — 期間 + マーク の 2 テーブル.

docs/plans/special-visit-week-design.md §1:
基本の固定訪問はそのまま生かしたまま、カレンダーの曜日セルに ○ を付けて
「追加の訪問枠」を週ごとにプールへ積み、毎週その週だけ配置する「上乗せ型」機能。
恒久パターン (patient_fixed_visits) には一切書き込まない。

- special_visit_periods: 患者ごとの期間 (開始日/終了日/週N回以上の目標)。
  同一患者で status='active' は同時に 1 本のみ (アプリ層で担保・422)。
- special_visit_marks:   週×曜日のセル単位マーク。
  kind='extra'     … ○ 追加枠 (プール → 配置)
  kind='displaced' … 固定訪問の日単位退避 (復元用 snapshot 付き)

既存の置換型 special_weekly_pattern / special_week_active / PFV mode='special'
とは別物で、本 migration はそれらに一切触れない (据え置き)。

Revision ID: 0064_special_visit_week
Revises: 0063_staff_event_blocking
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0064_special_visit_week"
down_revision: str | Sequence[str] | None = "0063_staff_event_blocking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- 期間 (患者ごと・active は 1 本) -----------------------------------
    op.create_table(
        "special_visit_periods",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("weekly_target", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'ended', 'cancelled')",
            name="ck_svp_status",
        ),
        sa.CheckConstraint("weekly_target >= 1 AND weekly_target <= 7", name="ck_svp_target"),
    )
    op.create_index(
        "ix_svp_patient_status", "special_visit_periods", ["patient_id", "status"]
    )

    # ---- マーク (週 × 曜日のセル) -----------------------------------------
    op.create_table(
        "special_visit_marks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "period_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("special_visit_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 非正規化 (プール一覧の join 削減).
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pool"),
        sa.Column(
            "placed_visit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("visits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # kind='displaced' のみ。復元用スナップショット (§1)。
        sa.Column("displaced_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('extra', 'displaced')", name="ck_svm_kind"),
        sa.CheckConstraint(
            "status IN ('pool', 'placed', 'cancelled')",
            name="ck_svm_status",
        ),
        # 日曜は対象外 (0=Mon..5=Sat)。
        sa.CheckConstraint("weekday BETWEEN 0 AND 5", name="ck_svm_weekday"),
    )
    # ○ は 1 セル 1 個 (取消済みは除外). 0062 と同じ部分ユニーク索引の書式。
    op.create_index(
        "uq_svm_extra_cell",
        "special_visit_marks",
        ["period_id", "iso_year", "iso_week", "weekday", "kind"],
        unique=True,
        postgresql_where=sa.text("status != 'cancelled' AND kind = 'extra'"),
    )
    op.create_index(
        "ix_svm_week_status", "special_visit_marks", ["iso_year", "iso_week", "status"]
    )
    op.create_index("ix_svm_patient", "special_visit_marks", ["patient_id"])


def downgrade() -> None:
    op.drop_index("ix_svm_patient", table_name="special_visit_marks")
    op.drop_index("ix_svm_week_status", table_name="special_visit_marks")
    op.drop_index("uq_svm_extra_cell", table_name="special_visit_marks")
    op.drop_table("special_visit_marks")

    op.drop_index("ix_svp_patient_status", table_name="special_visit_periods")
    op.drop_table("special_visit_periods")
