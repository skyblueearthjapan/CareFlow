"""サインで記録: visit_checkins.checkin_source に 'signature' + visit_signatures テーブル.

Revision ID: 0090_visit_signatures
Revises: 0089_multi_office_settings
Create Date: 2026-10-07

## このマイグレーションの責務

正典設計書 ``docs/plans/signature-checkin-design-2026-10-06.md`` §4・§5-1。

QR を置けない利用者さん (または QR を忘れたとき) は、退出のときに利用者さんの
サインをもらい、その時刻と GPS で退出を記録する。

1. ``visit_checkins.checkin_source`` の CHECK に ``'signature'`` を足す
   (``'qr','manual'`` → ``'qr','manual','signature'``)。到着の「到着を記録」は
   今の ``'manual'`` のまま (PO 決定 Q2)。
2. 新しい表 ``visit_signatures`` (サインの画像 1 枚 = 退出の打刻 1 行)。

* ``visit_id`` / ``checkin_id`` は監査証跡として **ON DELETE RESTRICT**
  (``visit_checkins`` と同じ)。``checkin_id`` は UNIQUE。
* ``client_id`` は端末が発行する再送の冪等キー (NULL は制約の外 = 部分 UNIQUE)。
* 署名した人は記録しない (PO 決定 Q4)。
* 画像は 5 年で消す (``image_path`` を NULL・``image_deleted_at``)。行は残す。

## CHECK の実名

0041 は ``op.create_table`` で ``ck_visit_checkins_checkin_source`` を作ったが、
PostgreSQL では ``target_metadata`` の命名規約が掛かって実名は
``ck_visit_checkins_ck_visit_checkins_checkin_source`` になっている (0088 の注記と
同じ)。環境ごとの名前の違いに左右されないよう、PostgreSQL では
``pg_constraint`` から ``checkin_source`` を含む CHECK を探して落とし、
**同じ名前で** 付け直す。SQLite (テスト) は batch で作り直す。

## downgrade

``visit_signatures`` を drop し、``checkin_source='signature'`` の打刻を
``'manual'`` に戻してから CHECK を元の 2 値へ戻す (サインの記録は「QRなし」に
見えるようになる・画像ファイルは消さない)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0090_visit_signatures"
down_revision: str | Sequence[str] | None = "0089_multi_office_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "visit_signatures"
_CHECKINS = "visit_checkins"
#: PostgreSQL での実名 (命名規約が掛かった形)。見つからなければこの名前で付ける。
_PG_CHECK_NAME = "ck_visit_checkins_ck_visit_checkins_checkin_source"

_SOURCES_NEW = "'qr','manual','signature'"
_SOURCES_OLD = "'qr','manual'"


def _replace_source_check(values: str) -> None:
    """``checkin_source`` の CHECK を ``checkin_source IN (values)`` に付け替える。"""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                f"""
                DO $$
                DECLARE
                    r record;
                    cname text := '{_PG_CHECK_NAME}';
                BEGIN
                    FOR r IN
                        SELECT conname FROM pg_constraint
                         WHERE conrelid = '{_CHECKINS}'::regclass
                           AND contype = 'c'
                           AND pg_get_constraintdef(oid) LIKE '%checkin_source%'
                    LOOP
                        cname := r.conname;
                        EXECUTE format('ALTER TABLE {_CHECKINS} DROP CONSTRAINT %I', r.conname);
                    END LOOP;
                    EXECUTE format(
                        'ALTER TABLE {_CHECKINS} ADD CONSTRAINT %I CHECK '
                        '(checkin_source IN ({values.replace("'", "''")}))',
                        cname
                    );
                END $$;
                """
            )
        )
        return
    # SQLite: 既存の CHECK の名前を読み取り、batch (表の作り直し) で付け替える。
    names = [
        ck["name"]
        for ck in sa.inspect(bind).get_check_constraints(_CHECKINS)
        if ck.get("name") and "checkin_source" in (ck.get("sqltext") or "")
    ]
    name = names[0] if names else "ck_visit_checkins_checkin_source"
    with op.batch_alter_table(_CHECKINS, recreate="always") as batch:
        for old in names:
            batch.drop_constraint(old, type_="check")
        batch.create_check_constraint(name, f"checkin_source IN ({values})")


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    _replace_source_check(_SOURCES_NEW)

    uuid_type: sa.types.TypeEngine = (
        postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)
    )
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()
    id_column = (
        sa.Column("id", uuid_type, primary_key=True, server_default=sa.text("gen_random_uuid()"))
        if is_pg
        else sa.Column("id", uuid_type, primary_key=True)
    )
    op.create_table(
        _TABLE,
        id_column,
        sa.Column(
            "visit_id",
            uuid_type,
            sa.ForeignKey(
                "visits.id", ondelete="RESTRICT", name="fk_visit_signatures_visit_id_visits"
            ),
            nullable=False,
        ),
        sa.Column(
            "checkin_id",
            uuid_type,
            sa.ForeignKey(
                "visit_checkins.id",
                ondelete="RESTRICT",
                name="fk_visit_signatures_checkin_id_visit_checkins",
            ),
            nullable=False,
        ),
        sa.Column(
            "image_path",
            sa.String(length=512),
            nullable=True,
            comment="画像ファイルのパス。保持期間を過ぎて消したら NULL",
        ),
        sa.Column("image_mime", sa.String(length=32), nullable=False),
        sa.Column("image_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("device_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            uuid_type,
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_visit_signatures_created_by_user_id_users"
            ),
            nullable=True,
        ),
        sa.Column(
            "created_by_staff_id",
            uuid_type,
            sa.ForeignKey(
                "staff.id",
                ondelete="SET NULL",
                name="fk_visit_signatures_created_by_staff_id_staff",
            ),
            nullable=True,
        ),
        sa.Column("client_id", uuid_type, nullable=True),
        sa.Column("image_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
        sa.UniqueConstraint("checkin_id", name="uq_visit_signatures_checkin_id"),
    )
    op.create_index("ix_visit_signatures_visit", _TABLE, ["visit_id"], unique=False)
    op.create_index(
        "uq_visit_signatures_client_id",
        _TABLE,
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("client_id IS NOT NULL"),
        sqlite_where=sa.text("client_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_visit_signatures_client_id", table_name=_TABLE)
    op.drop_index("ix_visit_signatures_visit", table_name=_TABLE)
    op.drop_table(_TABLE)
    # 元の CHECK に通らない行を先に戻す (サインの退出は「QRなし」になる)。
    op.execute(
        sa.text(
            f"UPDATE {_CHECKINS} SET checkin_source = 'manual' WHERE checkin_source = 'signature'"
        )
    )
    _replace_source_check(_SOURCES_OLD)
