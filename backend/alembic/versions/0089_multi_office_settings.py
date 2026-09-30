"""別の事業所へ提供する準備: 事業所の情報・時刻を合わせる上限・拠点の略称の設定化.

Revision ID: 0089_multi_office_settings
Revises: 0088_visit_time_adjustments
Create Date: 2026-10-01

## このマイグレーションの責務

正典 ``docs/plans/multi-office-readiness-audit-2026-10-01.md`` の #1 / #4 / #7。
コードに書かれていたお客様固有の値を設定へ移す。**いまのお客様 (よりより様) の
表示・帳票・上限は変わらない** — データの手順で、今までコードにあった値をそのまま入れる。

1. 新テーブル ``business_profile`` (事業所の情報・シングルトン 1 行)。患者 QR カードの
   事業所名・電話・対応時間・対応日・ロゴ (#1)。
2. ``checkin_settings`` に 4 列 (#7)。NULL = コード既定 (他の設定列と同じ方式)。
   * ``arrival_max_back_min``     到着をさかのぼれる上限 (分)        既定 90  / 10..240
   * ``departure_max_ahead_min``  退出を読取時刻より後ろへ動かせる上限 既定 30  / 0..180
   * ``staff_adjust_window_days`` スタッフが合わせられる期間 (日)     既定 7   / 0..31
   * ``unplanned_default_minutes`` 予定外訪問の仮の所要時間 (分)      既定 60  / 10..240
3. データ:
   * ``checkin_settings`` の 4 列に 90 / 30 / 7 / 60 を入れる (行が無ければ 1 行作る。
     他の列は NULL のまま = 既定のまま)。
   * ``business_profile``: **よりより様の DB (拠点コード INAGE / TSUGA の拠点がある) のときだけ**
     今まで ``frontend/lib/qr-print-contact.ts`` と ``qr-print/page.tsx`` にあった値を入れる。
     別の事業所の新しい DB には入れない (よりより様の電話番号が別の事業所のカードに
     出ないようにするため)。未設定の項目はカードに載らない。
   * ``offices``: ``short_label`` / ``sort_order`` が空の拠点に、今までコードにあった
     INAGE→稲・1 / TSUGA→津・2 を入れる (0059 は拠点名で入れていたので、拠点名が違う
     場合の取りこぼしを拠点コードで埋める。値が入っている拠点には触れない)。

## SQLite 互換

``checkin_settings`` の列追加は 0043 と同じ **インライン列 CHECK 付き ADD COLUMN**
(1 文の標準 SQL・テーブル再構築なし)。downgrade は SQLite だけ batch 再構築。
``business_profile`` は 0041 と同じ dialect 分岐 (PG = UUID / gen_random_uuid()、
SQLite = String(36))。データの INSERT は 0051 と同じく Python の uuid4 を渡す。

## downgrade

``business_profile`` を drop し、``checkin_settings`` の 4 列を drop する (設定値は失われ、
コードの既定に戻る)。``offices`` に埋めた略称・並び順はそのまま残す (0088 以前のコードは
INAGE→稲 / TSUGA→津 を決め打ちで出していたので、残しても表示は変わらない)。
"""

# ruff: noqa: I001
from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0089_multi_office_settings"
down_revision: str | Sequence[str] | None = "0088_visit_time_adjustments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROFILE_TABLE = "business_profile"
_PROFILE_SINGLETON_INDEX = "uq_business_profile_singleton"

# (列名, CHECK 名, CHECK 式, 今までコードにあった値)
CHECKIN_LIMIT_COLUMNS: tuple[tuple[str, str, str, int], ...] = (
    (
        "arrival_max_back_min",
        "ck_checkin_settings_arrival_max_back_range",
        "arrival_max_back_min IS NULL OR "
        "(arrival_max_back_min >= 10 AND arrival_max_back_min <= 240)",
        90,
    ),
    (
        "departure_max_ahead_min",
        "ck_checkin_settings_departure_max_ahead_range",
        "departure_max_ahead_min IS NULL OR "
        "(departure_max_ahead_min >= 0 AND departure_max_ahead_min <= 180)",
        30,
    ),
    (
        "staff_adjust_window_days",
        "ck_checkin_settings_staff_adjust_window_range",
        "staff_adjust_window_days IS NULL OR "
        "(staff_adjust_window_days >= 0 AND staff_adjust_window_days <= 31)",
        7,
    ),
    (
        "unplanned_default_minutes",
        "ck_checkin_settings_unplanned_default_range",
        "unplanned_default_minutes IS NULL OR "
        "(unplanned_default_minutes >= 10 AND unplanned_default_minutes <= 240)",
        60,
    ),
)

# 今まで frontend/lib/qr-print-contact.ts と qr-print/page.tsx にあった値 (よりより様)。
CURRENT_BUSINESS_PROFILE: dict[str, str] = {
    "station_name": "訪問看護ステーション よりより",
    "contact_tel": "043-215-8991",
    "contact_hours": "9:00〜18:00",
    "contact_days": "日曜・年末年始休暇を除く",
    "logo_url": "/brand/yoriyori-logo-h.svg",
}
# よりより様の DB を見分ける拠点コード (この 2 つのどちらかがあれば事業所の情報を入れる)。
CURRENT_CLIENT_OFFICE_CODES: tuple[str, ...] = ("INAGE", "TSUGA")
# 今まで board_service / propose_slots_service / patient_excel にあった略称と並び順。
CURRENT_OFFICE_LABELS: tuple[tuple[str, str, int], ...] = (
    ("INAGE", "稲", 1),
    ("TSUGA", "津", 2),
)


def _create_business_profile(is_pg: bool) -> None:
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
        _PROFILE_TABLE,
        id_column,
        sa.Column(
            "is_singleton",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true") if is_pg else sa.text("1"),
        ),
        sa.Column("station_name", sa.String(length=120), nullable=True),
        sa.Column("contact_tel", sa.String(length=40), nullable=True),
        sa.Column("contact_hours", sa.String(length=60), nullable=True),
        sa.Column("contact_days", sa.String(length=120), nullable=True),
        sa.Column("logo_url", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now_default),
    )
    if is_pg:
        op.create_index(
            _PROFILE_SINGLETON_INDEX,
            _PROFILE_TABLE,
            ["is_singleton"],
            unique=True,
            postgresql_where=sa.text("is_singleton = true"),
        )
    else:
        op.create_index(
            _PROFILE_SINGLETON_INDEX,
            _PROFILE_TABLE,
            ["is_singleton"],
            unique=True,
            sqlite_where=sa.text("is_singleton = 1"),
        )


def _seed(bind: sa.engine.Connection) -> None:  # type: ignore[type-arg]
    """データの手順 (テストから直接呼べるよう分離)。今までコードにあった値を入れる。"""
    is_pg = bind.dialect.name == "postgresql"
    true_lit = "true" if is_pg else "1"

    # ---- checkin_settings: 時刻を合わせる上限 + 予定外訪問の仮の所要時間 ----
    values = {col: current for col, _name, _sql, current in CHECKIN_LIMIT_COLUMNS}
    row = bind.execute(
        sa.text(f"SELECT id FROM checkin_settings WHERE is_singleton = {true_lit}")
    ).fetchone()
    if row is None:
        bind.execute(
            sa.text(
                "INSERT INTO checkin_settings (id, is_singleton, "
                + ", ".join(values)
                + f") VALUES (:id, {true_lit}, "
                + ", ".join(f":{c}" for c in values)
                + ")"
            ),
            {"id": str(uuid.uuid4()), **values},
        )
    else:
        bind.execute(
            sa.text(
                "UPDATE checkin_settings SET "
                + ", ".join(f"{c} = :{c}" for c in values)
                + f" WHERE is_singleton = {true_lit}"
            ),
            values,
        )

    # ---- offices: 略称・並び順が空の拠点を、今までコードにあった値で埋める ----
    for code, short, order in CURRENT_OFFICE_LABELS:
        bind.execute(
            sa.text(
                "UPDATE offices SET short_label = :short "
                "WHERE code = :code AND (short_label IS NULL OR short_label = '')"
            ),
            {"code": code, "short": short},
        )
        bind.execute(
            sa.text("UPDATE offices SET sort_order = :order WHERE code = :code AND sort_order IS NULL"),
            {"code": code, "order": order},
        )

    # ---- business_profile: よりより様の DB のときだけ、今までの QR カードの値 ----
    params = {f"c{i}": c for i, c in enumerate(CURRENT_CLIENT_OFFICE_CODES)}
    is_current_client = bind.execute(
        sa.text(
            "SELECT 1 FROM offices WHERE deleted_at IS NULL AND code IN ("
            + ", ".join(f":{k}" for k in params)
            + ")"
        ),
        params,
    ).fetchone()
    if is_current_client is not None:
        bind.execute(
            sa.text(
                f"INSERT INTO {_PROFILE_TABLE} (id, is_singleton, "
                + ", ".join(CURRENT_BUSINESS_PROFILE)
                + f") VALUES (:id, {true_lit}, "
                + ", ".join(f":{c}" for c in CURRENT_BUSINESS_PROFILE)
                + ")"
            ),
            {"id": str(uuid.uuid4()), **CURRENT_BUSINESS_PROFILE},
        )


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    _create_business_profile(is_pg)

    # インライン列 CHECK 付き ADD COLUMN (0043 と同じ・PG / SQLite 共通の標準 SQL)。
    for column, check_name, check_sql, _current in CHECKIN_LIMIT_COLUMNS:
        op.execute(
            f"ALTER TABLE checkin_settings ADD COLUMN {column} INTEGER "
            f"CONSTRAINT {check_name} CHECK ({check_sql})"
        )

    _seed(bind)


def downgrade() -> None:
    """注意: 本番で巻き戻すと事業所の情報と上限の設定は失われる (コードの既定に戻る)."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.drop_index(_PROFILE_SINGLETON_INDEX, table_name=_PROFILE_TABLE)
    op.drop_table(_PROFILE_TABLE)

    if is_pg:
        for column, check_name, _sql, _current in reversed(CHECKIN_LIMIT_COLUMNS):
            op.drop_constraint(check_name, "checkin_settings", type_="check")
            op.drop_column("checkin_settings", column)
    else:
        # SQLite は CHECK に参照される列を直接 DROP できないため batch 再構築 (0043 と同じ)。
        with op.batch_alter_table("checkin_settings") as batch:
            for column, check_name, _sql, _current in reversed(CHECKIN_LIMIT_COLUMNS):
                batch.drop_constraint(check_name, type_="check")
                batch.drop_column(column)
