"""予定のお知らせ (SMS) の表.

Revision ID: 0110_sms_notify
Revises: 0109_contacts
Create Date: 2026-11-16

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §6 (段階 1 = 土台)。
新しい表だけを作る (今ある表は変えない)。

1. ``sms_batches``: 1 回の「送る」(種類 week / change・週の始まり・押した人・件数)。
2. ``sms_messages``: 1 通 (本文・番号・状態・KDDI の受付の ID・やりとりの番号・キャリア・
   splitNum・届かなかった理由・照会の回数と次の時刻・二重送信を防ぐ鍵 UNIQUE)。
   ``contact_id`` は外部キーなし (連絡先の保存は消して入れ直すため)。
3. ``sms_status_events``: 1 通の状態の経過。
4. ``patient_week_notices``: 患者 × 週の「お伝えした内容」(UNIQUE patient_id, week_start)。
5. ``patient_notice_events``: 「お伝えした内容」の版ごとの記録。

## downgrade
5 つの表を消す (送信の記録とお伝えした内容は失われる)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0110_sms_notify"
down_revision: str | Sequence[str] | None = "0109_contacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)
_KINDS = "kind IN ('week', 'change')"
_STATUSES = (
    "status IN ('queued', 'sending', 'accepted', 'delivered', 'failed', 'unknown', 'skipped')"
)


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if _is_pg() else sa.Uuid()


def _json() -> sa.types.TypeEngine:
    return postgresql.JSONB() if _is_pg() else sa.JSON()


def _user_fk(name: str) -> sa.Column:
    return sa.Column(name, _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


def upgrade() -> None:
    op.create_table(
        "sms_batches",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        _user_fk("created_by_user_id"),
        sa.Column("patient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segments_est_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_KINDS, name=op.f("ck_sms_batches_kind")),
    )
    op.create_index("ix_sms_batches_week_start", "sms_batches", ["week_start"])

    op.create_table(
        "sms_messages",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "batch_id",
            _uuid(),
            sa.ForeignKey("sms_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column(
            "patient_id",
            _uuid(),
            sa.ForeignKey("patients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("contact_id", _uuid(), nullable=True),
        sa.Column("relation", sa.String(16), nullable=True),
        sa.Column("phone", sa.String(16), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segments_est", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("notice_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("status_raw", sa.String(32), nullable=True),
        sa.Column("skip_reason", sa.String(64), nullable=True),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("not_before", _TS, nullable=True),
        sa.Column("provider", sa.String(16), nullable=True),
        sa.Column("provider_msg_id", sa.String(64), nullable=True),
        sa.Column("transaction_id", sa.String(128), nullable=True),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.Column("carrier", sa.String(16), nullable=True),
        sa.Column("split_num", sa.SmallInteger(), nullable=True),
        sa.Column("fail_reason_raw", sa.String(255), nullable=True),
        sa.Column("sent_at", _TS, nullable=True),
        sa.Column("accepted_at", _TS, nullable=True),
        sa.Column("status_decided_at", _TS, nullable=True),
        sa.Column("status_checked_at", _TS, nullable=True),
        sa.Column("poll_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("next_poll_at", _TS, nullable=True),
        _user_fk("created_by_user_id"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_KINDS, name=op.f("ck_sms_messages_kind")),
        sa.CheckConstraint(_STATUSES, name=op.f("ck_sms_messages_status")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_sms_messages_dedupe_key")),
    )
    op.create_index("ix_sms_messages_batch_id", "sms_messages", ["batch_id"])
    op.create_index("ix_sms_messages_patient_id", "sms_messages", ["patient_id"])
    op.create_index("ix_sms_messages_status_not_before", "sms_messages", ["status", "not_before"])
    op.create_index("ix_sms_messages_created_at", "sms_messages", ["created_at"])

    op.create_table(
        "sms_status_events",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "message_id",
            _uuid(),
            sa.ForeignKey("sms_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("raw_status", sa.String(32), nullable=True),
        sa.Column("detail", sa.String(255), nullable=True),
    )
    op.create_index("ix_sms_status_events_message_id", "sms_status_events", ["message_id"])

    op.create_table(
        "patient_week_notices",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "patient_id",
            _uuid(),
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("visits", _json(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_channel", sa.String(8), nullable=False, server_default="sms"),
        _user_fk("told_by_user_id"),
        sa.Column("told_at", _TS, nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "patient_id", "week_start", name=op.f("uq_patient_week_notices_patient_week")
        ),
        sa.CheckConstraint(
            "last_channel IN ('sms', 'phone', 'none')",
            name=op.f("ck_patient_week_notices_last_channel"),
        ),
    )

    op.create_table(
        "patient_notice_events",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "notice_id",
            _uuid(),
            sa.ForeignKey("patient_week_notices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            _uuid(),
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("visits", _json(), nullable=False),
        sa.Column("detected_at", _TS, nullable=True),
        sa.Column("told_at", _TS, nullable=False),
        _user_fk("told_by_user_id"),
        sa.Column(
            "batch_id",
            _uuid(),
            sa.ForeignKey("sms_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_KINDS, name=op.f("ck_patient_notice_events_kind")),
        sa.CheckConstraint(
            "channel IN ('sms', 'phone', 'none')", name=op.f("ck_patient_notice_events_channel")
        ),
    )
    op.create_index(
        "ix_patient_notice_events_patient_week",
        "patient_notice_events",
        ["patient_id", "week_start"],
    )


def downgrade() -> None:
    """注意: 送信の記録とお伝えした内容は失われる."""
    op.drop_index("ix_patient_notice_events_patient_week", table_name="patient_notice_events")
    op.drop_table("patient_notice_events")
    op.drop_table("patient_week_notices")
    op.drop_index("ix_sms_status_events_message_id", table_name="sms_status_events")
    op.drop_table("sms_status_events")
    for name in (
        "ix_sms_messages_created_at",
        "ix_sms_messages_status_not_before",
        "ix_sms_messages_patient_id",
        "ix_sms_messages_batch_id",
    ):
        op.drop_index(name, table_name="sms_messages")
    op.drop_table("sms_messages")
    op.drop_index("ix_sms_batches_week_start", table_name="sms_batches")
    op.drop_table("sms_batches")
