"""visit_recordings (訪問の音声記録・文字起こし・要約) 追加.

Revision ID: 0086_visit_recordings
Revises: 0085_drop_legacy_ck_ta_source
Create Date: 2026-09-17

## このマイグレーションの責務

正典設計書 ``docs/plans/visit-voice-record-design-2026-09-17.md`` §2-4 / §10-2 の
データ層。1 行 = 1 録音で、**音声バイナリは持たない** (``VISIT_AUDIO_DIR`` 配下の
ファイルへのパスだけを持つ / ``visit_photos`` と同じ作法)。

* ``visit_id`` / ``patient_id`` は NULL 可 = 「紐付け待ち」(``status='unlinked'``)。
  訪問や患者が消えても録音は残す (どちらも ``SET NULL``)。
* ``staff_id`` (録音者) は誰が録ったかが監査の要なので ``RESTRICT``。
* 保持期間パージは **音声だけ** を消す: ``audio_path`` / ``audio_bytes`` を NULL に
  して ``audio_deleted_at`` を立てる。``transcript`` / ``summary`` は訪問と同じ
  寿命で残る。
* 処理の進行は専用ジョブテーブルを作らず ``status`` + ``updated_at`` で表す
  (``transcribing`` のまま放置 → ``failed``)。``status`` の値は
  ``uploaded | transcribing | summarized | failed | unlinked``。DB CHECK は貼らず
  API 層で縛る (既存テーブルの作法に合わせる)。

index は 4 本 (設計 §10-2):
``(staff_id, recorded_at desc)`` / ``(patient_id, recorded_at desc)`` /
``(status)`` / partial ``(visit_id) where deleted_at is null``。

加えて受領の冪等キー (FE の再送・タブ二重送信対策) として partial unique
``(staff_id, client_id) where client_id is not null and deleted_at is null``。
``client_id`` は端末が 1 録音につき 1 個だけ発行する UUID で、同じ値の
2 回目の ``POST`` は新しい行を作らず既存行を 200 で返す。
``deleted_at is null`` を条件に含めるのは、**削除済みの録音を墓石として残す**
ため: 管理者が消した録音と同じ ``client_id`` で端末が再送してきたとき、
unique が墓石行と衝突して受領そのものが 500 で落ちる。生きている行だけを
一意にすれば、削除後の再送は素直に新しい行として受け取れる。

## downgrade

テーブル drop のみ。音声ファイル (``VISIT_AUDIO_DIR`` 配下) には触らない
(DB を戻しても実ファイルは残る — 消すのは運用の判断)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0086_visit_recordings"
down_revision: str | Sequence[str] | None = "0085_drop_legacy_ck_ta_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "visit_recordings"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    is_pg = _is_pg()
    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(36)
    json_type = postgresql.JSONB() if is_pg else sa.JSON()
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()
    false_lit = sa.text("false" if is_pg else "0")

    op.create_table(
        _TABLE,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "visit_id",
            uuid_type,
            sa.ForeignKey("visits.id", ondelete="SET NULL"),
            nullable=True,
            comment="紐付け待ちは NULL",
        ),
        sa.Column(
            "patient_id",
            uuid_type,
            sa.ForeignKey("patients.id", ondelete="SET NULL"),
            nullable=True,
            comment="紐付け待ちは NULL (status='unlinked')",
        ),
        sa.Column(
            "staff_id",
            uuid_type,
            sa.ForeignKey("staff.id", ondelete="RESTRICT"),
            nullable=False,
            comment="録音者",
        ),
        sa.Column(
            "office_id",
            uuid_type,
            sa.ForeignKey("offices.id", ondelete="SET NULL"),
            nullable=True,
            comment="表示のスコープ",
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="端末時刻 (サーバー受領時刻は created_at)",
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "audio_path",
            sa.String(512),
            nullable=True,
            comment="{VISIT_AUDIO_DIR}/{yyyy}/{mm}/{id}.{ext}。パージ後は NULL",
        ),
        sa.Column("audio_mime", sa.String(64), nullable=True),
        sa.Column("audio_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "device_mime",
            sa.String(64),
            nullable=True,
            comment="受領時に端末が名乗った MIME",
        ),
        sa.Column(
            "audio_deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="保持期間パージ済み",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="uploaded",
            comment="uploaded / transcribing / summarized / failed / unlinked",
        ),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column(
            "transcript_json",
            json_type,
            nullable=True,
            comment="セグメント (開始秒・話者・本文)",
        ),
        sa.Column(
            "summary",
            json_type,
            nullable=True,
            comment="構造化要約 (主訴・様子 / バイタル / 処置・ケア / 申し送り / 次回 / free)",
        ),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(16), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        # 1 件 ≈ $0.0016。丸め桁 (pricing._QUANT = 0.000001) をそのまま格納できる
        # 精度にする (Numeric(10,4) では 6 桁の見積もりが握り潰されていた)。
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "error_kind",
            sa.String(32),
            nullable=True,
            comment="失敗の種別 (config/auth/timeout/http/parse/too_large/audio_missing/stale)",
        ),
        sa.Column(
            "client_id",
            uuid_type,
            nullable=True,
            comment="端末が発行する受領の冪等キー (staff_id と組で unique)",
        ),
        sa.Column(
            "consent_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=false_lit,
            comment="録音同意チェック",
        ),
        sa.Column(
            "reviewed_by",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 一覧の既定順 (新しい順) をそのまま index に載せる。
    op.create_index(
        "ix_visit_recordings_staff_recorded",
        _TABLE,
        ["staff_id", sa.text("recorded_at DESC")],
    )
    op.create_index(
        "ix_visit_recordings_patient_recorded",
        _TABLE,
        ["patient_id", sa.text("recorded_at DESC")],
    )
    op.create_index("ix_visit_recordings_status", _TABLE, ["status"])
    # 訪問詳細カードは生きている行しか引かない → partial (PG のみ)。
    op.create_index(
        "ix_visit_recordings_visit",
        _TABLE,
        ["visit_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 受領の冪等キー。client_id が NULL の行 (旧 FE / 手 seed) と削除済みの行は
    # 制約の外 (削除した録音と同じ client_id の再送は新しい行として受け取る)。
    op.create_index(
        "uq_visit_recordings_staff_client",
        _TABLE,
        ["staff_id", "client_id"],
        unique=True,
        postgresql_where=sa.text("client_id IS NOT NULL AND deleted_at IS NULL"),
        sqlite_where=sa.text("client_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_visit_recordings_staff_client", table_name=_TABLE)
    op.drop_index("ix_visit_recordings_visit", table_name=_TABLE)
    op.drop_index("ix_visit_recordings_status", table_name=_TABLE)
    op.drop_index("ix_visit_recordings_patient_recorded", table_name=_TABLE)
    op.drop_index("ix_visit_recordings_staff_recorded", table_name=_TABLE)
    op.drop_table(_TABLE)
