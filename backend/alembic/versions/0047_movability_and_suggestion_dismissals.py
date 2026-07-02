"""P2-A: PFV.movability (可動域フラグ) + suggestion_dismissals (却下記憶).

Revision ID: 0047_movability_and_suggestion_dismissals
Revises: 0046_user_username_and_uniques
Create Date: 2026-07-03

## このマイグレーションの責務 (改善提案MVP P2-A データ基盤)

設計書 docs/plans/p2-improvement-mvp-design.md §1 に従い 2 つの変更を適用する.

### 変更 1: patient_fixed_visits.movability カラム追加

「可動域フラグ」= 提案の可否 (是正の 3 軸: is_pinned=自動化保護 / time_type=希望時刻の
性質 / movability=提案の可否) を PFV 単位で持たせる.

* 値: ``unknown`` (既定・保守的) / ``time_flexible`` (同曜日内の時刻変更可) /
  ``day_flexible`` (曜日も変更可) / ``locked`` (完全固定).
* ``String(16), NOT NULL, server_default='unknown'``. ADD COLUMN 時点で既存全行が
  'unknown' で埋まる (= UPDATE 全行 backfill を避ける). 新規 INSERT も 'unknown'.
* DB CHECK ``ck_pfv_movability`` で 4 値に拘束. index 不要.
* **backfill**: ``is_pinned=true`` の既存行は ``movability='locked'`` に更新する
  (含意: is_pinned=True ⇒ movability='locked'). is_pinned との矛盾防止は DB CHECK
  ではなく pfv_validator V6 (proposed_items の自動矯正) が担う (既存 PATCH pin 経路を
  壊さないため). 本 migration は既存行の初期整合のみ担保する.

### 変更 2: suggestion_dismissals テーブル新設 (設計書 §1.2)

改善提案の「却下記憶」. 同一提案の指紋 = (patient_id, kind, target_weekday).

* CHECK 3 本 (kind / reason / target_weekday) + index(patient_id, kind, target_weekday).
* dismissed_by は監査用に ``ON DELETE SET NULL`` (ユーザー削除で NULL 化).
* patient_id は ``ON DELETE CASCADE`` (患者物理削除で却下記憶も消える).

## SQLite 互換

* PG: raw ``ALTER TABLE ... ADD CONSTRAINT`` で CHECK を張る. UUID PK は gen_random_uuid().
* SQLite (テスト): CHECK は ``batch_alter_table`` / ``create_table`` inline CHECK で表現.
  UUID は String(36) にフォールバック (app 側で uuid4 を供給).

## downgrade

変更 2 → 1 の逆順で完全に元に戻す (suggestion_dismissals を drop, movability の
CHECK + カラムを drop). backfill した movability 値は失われる.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0047_movability_and_suggestion_dismissals"
down_revision: Union[str, Sequence[str], None] = "0046_user_username_and_uniques"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MOVABILITY_VALUES = ("unknown", "time_flexible", "day_flexible", "locked")
_MOVABILITY_CHECK_NAME = "ck_pfv_movability"
_MOVABILITY_CHECK_COND = (
    "movability IN ('unknown','time_flexible','day_flexible','locked')"
)

_KIND_CHECK_COND = "kind IN ('time_change','day_change')"
_REASON_CHECK_COND = (
    "reason IN ('day_immovable','time_immovable','staff_relation','other')"
)
_WEEKDAY_CHECK_COND = "target_weekday BETWEEN 0 AND 6"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ----- 変更 1: PFV.movability -----
    # server_default='unknown' で NOT NULL カラム追加. ADD COLUMN 時点で既存全行が
    # 'unknown' で埋まる (= UPDATE 全行 backfill 不要). 新規 INSERT も 'unknown'.
    op.add_column(
        "patient_fixed_visits",
        sa.Column(
            "movability",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'unknown'"),
            comment=(
                "P2-A: 可動域フラグ = 提案の可否. "
                "unknown(既定) / time_flexible / day_flexible / locked. "
                "is_pinned=true の既存行は migration で locked に backfill 済."
            ),
        ),
    )

    # backfill: is_pinned=true の既存行は movability='locked' に矯正
    # (含意: is_pinned=True ⇒ movability='locked').
    op.execute(
        "UPDATE patient_fixed_visits SET movability = 'locked' WHERE is_pinned = "
        + ("true" if is_pg else "1")
    )

    # CHECK 制約: 4 値に拘束.
    if is_pg:
        op.execute(
            f"ALTER TABLE patient_fixed_visits ADD CONSTRAINT {_MOVABILITY_CHECK_NAME} "
            f"CHECK ({_MOVABILITY_CHECK_COND})"
        )
    else:
        # SQLite は ALTER TABLE ... ADD CONSTRAINT 不可のため batch 再構築.
        with op.batch_alter_table("patient_fixed_visits") as batch:
            batch.create_check_constraint(_MOVABILITY_CHECK_NAME, _MOVABILITY_CHECK_COND)

    # ----- 変更 2: suggestion_dismissals -----
    if is_pg:
        uuid_type: sa.types.TypeEngine = postgresql.UUID(as_uuid=True)
        id_col = sa.Column(
            "id",
            uuid_type,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        )
    else:
        uuid_type = sa.String(36)
        id_col = sa.Column("id", uuid_type, primary_key=True)

    op.create_table(
        "suggestion_dismissals",
        id_col,
        sa.Column(
            "patient_id",
            uuid_type,
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target_weekday", sa.SmallInteger(), nullable=False),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.Column("reason_note", sa.Text(), nullable=True),
        sa.Column(
            "dismissed_by",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now() if is_pg else sa.func.current_timestamp(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_KIND_CHECK_COND, name="ck_sd_kind"),
        sa.CheckConstraint(_REASON_CHECK_COND, name="ck_sd_reason"),
        sa.CheckConstraint(_WEEKDAY_CHECK_COND, name="ck_sd_weekday"),
        # 指紋 UNIQUE (= UNIQUE index 兼用. dismiss upsert の TOCTOU 競合防止).
        sa.UniqueConstraint(
            "patient_id",
            "kind",
            "target_weekday",
            name="uq_sd_fingerprint",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ----- 変更 2: suggestion_dismissals -----
    # UNIQUE 制約 (uq_sd_fingerprint) はテーブル DROP で一緒に消えるため個別 drop 不要.
    op.drop_table("suggestion_dismissals")

    # ----- 変更 1: PFV.movability -----
    if is_pg:
        op.drop_constraint(
            _MOVABILITY_CHECK_NAME, "patient_fixed_visits", type_="check"
        )
        op.drop_column("patient_fixed_visits", "movability")
    else:
        # SQLite: CHECK 付きカラム drop は batch 再構築で一括.
        with op.batch_alter_table("patient_fixed_visits") as batch:
            batch.drop_constraint(_MOVABILITY_CHECK_NAME, type_="check")
            batch.drop_column("movability")
