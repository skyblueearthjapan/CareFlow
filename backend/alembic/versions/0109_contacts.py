"""患者様・スタッフの連絡先 (電話番号・メール).

Revision ID: 0109_contacts
Revises: 0108_monthly_actual_care_record
Create Date: 2026-11-05

正典: ``docs/plans/contacts-phone-email-design-2026-11-05.md`` §4 (PO 了承 2026-11-05)。
予定のお知らせ (SMS / メール) に向けて、連絡先の欄を持たせる (送信はまだしない)。

1. 新しい表 ``patient_contacts`` (患者 1 人に 0〜3 件・ご本人は 1 件まで = 部分 UNIQUE)。
   ``relation``・``notify_channel``・``consent_status``・``source`` の値は CHECK で縛る。
2. ``staff`` に ``phone``・``phone_display``・``phone_kind``・``contact_email``・
   ``notify_channel`` (既定 none・CHECK sms/email/none)。

## SQLite 互換
``staff`` の列は 0099 と同じインライン列 CHECK 付きの ``ADD COLUMN`` (表の再構築なし)。
部分 UNIQUE は ``postgresql_where`` / ``sqlite_where`` の両方を書く。

## downgrade
表と列を消す (登録した連絡先は失われる)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0109_contacts"
down_revision: str | Sequence[str] | None = "0108_monthly_actual_care_record"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)
_SELF_WHERE = "relation = 'self'"
_STAFF_NOTIFY_CHECK = "ck_staff_notify_channel"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if _is_pg() else sa.Uuid()


def upgrade() -> None:
    op.create_table(
        "patient_contacts",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "patient_id",
            _uuid(),
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("relation_note", sa.String(120), nullable=True),
        sa.Column("phone", sa.String(16), nullable=True),
        sa.Column("phone_display", sa.String(20), nullable=True),
        sa.Column("phone_kind", sa.String(16), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("is_notify_target", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notify_channel", sa.String(8), nullable=False, server_default="sms"),
        sa.Column("consent_status", sa.String(16), nullable=False, server_default="unconfirmed"),
        sa.Column("consent_on", sa.Date(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "relation IN ('self','family','facility','care_manager','other')",
            name=op.f("ck_patient_contacts_relation"),
        ),
        sa.CheckConstraint(
            "notify_channel IN ('sms','email')", name=op.f("ck_patient_contacts_notify_channel")
        ),
        sa.CheckConstraint(
            "consent_status IN ('unconfirmed','confirmed','ng')",
            name=op.f("ck_patient_contacts_consent_status"),
        ),
        sa.CheckConstraint("source IN ('manual','excel')", name=op.f("ck_patient_contacts_source")),
    )
    op.create_index(op.f("ix_patient_contacts_patient_id"), "patient_contacts", ["patient_id"])
    op.create_index(
        op.f("uq_patient_contacts_self"),
        "patient_contacts",
        ["patient_id"],
        unique=True,
        postgresql_where=sa.text(_SELF_WHERE),
        sqlite_where=sa.text(_SELF_WHERE),
    )

    op.add_column("staff", sa.Column("phone", sa.String(16), nullable=True))
    op.add_column("staff", sa.Column("phone_display", sa.String(20), nullable=True))
    op.add_column("staff", sa.Column("phone_kind", sa.String(16), nullable=True))
    op.add_column("staff", sa.Column("contact_email", sa.String(255), nullable=True))
    op.execute(
        "ALTER TABLE staff ADD COLUMN notify_channel VARCHAR(8) DEFAULT 'none' NOT NULL "
        f"CONSTRAINT {_STAFF_NOTIFY_CHECK} CHECK (notify_channel IN ('sms','email','none'))"
    )


def downgrade() -> None:
    """注意: 登録した連絡先 (患者様・スタッフ) は失われる."""
    columns = ("contact_email", "phone_kind", "phone_display", "phone")
    if _is_pg():
        # 列の CHECK は列と一緒に消える。
        op.drop_column("staff", "notify_channel")
        for column in columns:
            op.drop_column("staff", column)
    else:
        # SQLite: CHECK に参照される列は直接 DROP できないので batch で再構築 (0099 と同じ)。
        with op.batch_alter_table("staff") as batch:
            batch.drop_constraint(op.f(_STAFF_NOTIFY_CHECK), type_="check")
            batch.drop_column("notify_channel")
            for column in columns:
                batch.drop_column(column)

    op.drop_index(op.f("uq_patient_contacts_self"), table_name="patient_contacts")
    op.drop_index(op.f("ix_patient_contacts_patient_id"), table_name="patient_contacts")
    op.drop_table("patient_contacts")
