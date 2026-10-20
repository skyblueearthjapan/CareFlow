"""休み申請の本格化と「休み・勤務表」の表と列.

Revision ID: 0099_leave_and_kinmu
Revises: 0098_kaipoke_source_snapshots
Create Date: 2026-10-20

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §2・§2-5・§8-1・§9 #1
(食い違ったら ``shared-foundation-leave-acceptance-2026-10-20.md`` が正)。

1. ``staff_weekly_overrides`` に ``source`` (request / admin / kaipoke・既定 admin) と
   ``pending_request_id`` (承認元の申請・ON DELETE SET NULL)。
2. ``pending_requests.request_type`` の CHECK を、mig0013 の旧 9 種からアプリの 12 種へ
   貼り直す (``staff_status_update``・``patient_status_update``・``patient_visit_add`` が
   本番 PG で保存に失敗していた別件のバグ・§8-1)。
3. ``staff`` に ``employment_type`` (full_time / part_time・既定 full_time)・``work_form``
   (勤務表の区分 A〜D)・``admin_hours_per_day`` (管理時間・NULL = 管理者でない)・
   ``kinmu_title`` (勤務表の 1 行目の役職)。
4. 新しい表: ``office_closed_days``・``staff_leave_kaipoke``・``kaipoke_leave_judgements``・
   ``leave_compare_runs``・``leave_months``・``kinmu_sheets``・``google_drive_connections``。
5. 既存の休み (本番 96 件・全部 ``off``) を「照合前」にする: 休みの種類の override ごとに
   ``staff_leave_kaipoke`` を ``sent_type=NULL・baseline_pending=TRUE`` で作り、override の
   ある月に ``leave_months`` の行 (``last_compared_at=NULL``) を作る (§2-5 H-f)。

## SQLite 互換
列の追加は 0089 と同じインライン列 CHECK / REFERENCES 付きの ``ADD COLUMN`` (標準 SQL・
表の再構築なし)。CHECK の名前は ``op.drop_constraint`` / ``op.create_check_constraint`` を
使うと命名規約で名前が変わるので、PG は生の SQL で書く。mig0013 の CHECK は本番 PG で
命名規約が二重にかかり ``ck_pending_requests_ck_pending_requests_request_type`` になって
いるので、0085 と同じく旧名・新名とも ``DROP CONSTRAINT IF EXISTS`` で落としてから 1 つ付ける。
SQLite の ``pending_requests`` は batch で再構築して付ける (downgrade は外すだけ)。
月の列の「1 日だけ」の CHECK は PG だけ (SQLite に EXTRACT が無い)。

## downgrade
新しい表と列を消す (照合前の印・事業所の休み・勤務表の記録・Google ドライブの接続は
失われる)。``pending_requests`` の CHECK は旧 9 種・本番の旧名 1 つへ戻すが、PG では ``NOT VALID`` で付ける
(新しい 3 種の行が既にあっても downgrade を止めない。新しい行の検査だけ旧 9 種に戻る)。
"""

# ruff: noqa: I001
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0099_leave_and_kinmu"
down_revision: str | Sequence[str] | None = "0098_kaipoke_source_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)

# mig0013 の旧 9 種 (downgrade で戻す)。
_OLD_REQUEST_TYPES: tuple[str, ...] = (
    "staff_off",
    "staff_event",
    "staff_mentor",
    "staff_create",
    "patient_create",
    "patient_cancel",
    "patient_reschedule",
    "patient_special_week_on",
    "patient_special_week_off",
)
# アプリの 12 種 (``app/schemas/v2/enums.py`` の ``RequestType``・モデルの CHECK と同じ)。
_REQUEST_TYPES: tuple[str, ...] = (
    *_OLD_REQUEST_TYPES,
    "staff_status_update",
    "patient_status_update",
    "patient_visit_add",
)
_REQUEST_TYPE_CHECK = "ck_pending_requests_request_type"
# mig0013 が本番 PG に付けた名前 (命名規約が二重にかかった)。
_LEGACY_REQUEST_TYPE_CHECK = "ck_pending_requests_ck_pending_requests_request_type"
_REQUEST_TYPE_CHECK_NAMES = (_LEGACY_REQUEST_TYPE_CHECK, _REQUEST_TYPE_CHECK)

# 休みの種類 (``app/core/leave.py`` の ``LEAVE_TYPES`` と同じ値。migration は書いた時点の
# 値で固定するため、アプリのコードは読まない)。
_LEAVE_TYPES: tuple[str, ...] = ("off", "am_off", "pm_off", "paid_off")

# month 列 (月の 1 日) を持つ表。
_MONTH_TABLES = ("leave_compare_runs", "leave_months", "kinmu_sheets")

_OVERRIDE_SOURCE_CHECK = "ck_staff_weekly_overrides_source"
_OVERRIDE_FK = "fk_staff_weekly_overrides_pending_request_id_pending_requests"
# staff の列の CHECK: (列名, CHECK の名前, 条件)
_STAFF_CHECKS: tuple[tuple[str, str, str], ...] = (
    (
        "employment_type",
        "ck_staff_employment_type",
        "employment_type IN ('full_time','part_time')",
    ),
    ("work_form", "ck_staff_work_form", "work_form IS NULL OR work_form IN ('A','B','C','D')"),
    (
        "admin_hours_per_day",
        "ck_staff_admin_hours_per_day",
        "admin_hours_per_day IS NULL OR (admin_hours_per_day >= 0 AND admin_hours_per_day <= 24)",
    ),
)


def _csv(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _json() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()) if _is_pg() else sa.JSON()


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if _is_pg() else sa.Uuid()


def _user_fk(name: str) -> sa.Column:
    return sa.Column(name, _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def _add_override_columns(is_pg: bool) -> None:
    op.execute(
        "ALTER TABLE staff_weekly_overrides ADD COLUMN source VARCHAR(16) "
        "DEFAULT 'admin' NOT NULL "
        f"CONSTRAINT {_OVERRIDE_SOURCE_CHECK} CHECK (source IN ('request','admin','kaipoke'))"
    )
    uuid_sql = "UUID" if is_pg else "CHAR(32)"
    op.execute(
        f"ALTER TABLE staff_weekly_overrides ADD COLUMN pending_request_id {uuid_sql} "
        f"CONSTRAINT {_OVERRIDE_FK} REFERENCES pending_requests (id) ON DELETE SET NULL"
    )


def _add_staff_columns() -> None:
    checks = {col: (name, cond) for col, name, cond in _STAFF_CHECKS}
    for column, sql_type in (
        ("employment_type", "VARCHAR(16) DEFAULT 'full_time' NOT NULL"),
        ("work_form", "VARCHAR(1)"),
        ("admin_hours_per_day", "NUMERIC(4, 2)"),
    ):
        name, cond = checks[column]
        op.execute(
            f"ALTER TABLE staff ADD COLUMN {column} {sql_type} CONSTRAINT {name} CHECK ({cond})"
        )
    op.add_column("staff", sa.Column("kinmu_title", sa.String(64), nullable=True))


def _set_request_type_check(values: Sequence[str], *, name: str, not_valid: bool = False) -> None:
    """request_type を縛る CHECK を旧名・新名とも落としてから ``name`` で 1 つだけ付ける."""
    if _is_pg():
        # 0085 と同じく、存在するものだけ落とす (名前は引用符で命名規約を通さない)。
        for old in _REQUEST_TYPE_CHECK_NAMES:
            op.execute(f'ALTER TABLE pending_requests DROP CONSTRAINT IF EXISTS "{old}"')
        op.execute(
            f'ALTER TABLE pending_requests ADD CONSTRAINT "{name}" '
            f"CHECK (request_type IN ({_csv(values)}))" + (" NOT VALID" if not_valid else "")
        )
        return
    # SQLite: CHECK の付け外しは表の再構築 (batch)。
    existing = {
        c.get("name") for c in sa.inspect(op.get_bind()).get_check_constraints("pending_requests")
    }
    with op.batch_alter_table("pending_requests", recreate="always") as batch:
        for old in _REQUEST_TYPE_CHECK_NAMES:
            if old in existing:
                batch.drop_constraint(op.f(old), type_="check")
        batch.create_check_constraint(op.f(name), f"request_type IN ({_csv(values)})")


def _create_tables() -> None:
    op.create_table(
        "office_closed_days",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("label", sa.String(64), nullable=False),
        _user_fk("created_by"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "staff_leave_kaipoke",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "staff_id", _uuid(), sa.ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("leave_date", sa.Date(), nullable=False),
        sa.Column("sent_type", sa.String(16), nullable=True),
        sa.Column("baseline_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_at", _TS, nullable=True),
        _user_fk("sent_by"),
        sa.Column("kaipoke_external_id", sa.String(64), nullable=True),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            f"sent_type IS NULL OR sent_type IN ({_csv(_LEAVE_TYPES)})",
            name=op.f("ck_staff_leave_kaipoke_sent_type"),
        ),
        sa.UniqueConstraint(
            "staff_id", "leave_date", name=op.f("uq_staff_leave_kaipoke_staff_id_leave_date")
        ),
    )

    op.create_table(
        "kaipoke_leave_judgements",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "staff_id", _uuid(), sa.ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("starts", sa.Time(), nullable=True),
        sa.Column("ends", sa.Time(), nullable=True),
        sa.Column("chosen_kind", sa.String(16), nullable=False),
        _user_fk("decided_by"),
        sa.Column("decided_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            f"chosen_kind IN ('not_leave', {_csv(_LEAVE_TYPES)})",
            name=op.f("ck_kaipoke_leave_judgements_chosen_kind"),
        ),
        sa.UniqueConstraint("content_hash", name=op.f("uq_kaipoke_leave_judgements_content_hash")),
    )

    op.create_table(
        "leave_compare_runs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column(
            "kaipoke_job_id",
            _uuid(),
            sa.ForeignKey("kaipoke_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fetched_at", _json(), nullable=True),
        sa.Column("diffs", _json(), nullable=False),
        sa.Column("diff_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planned_send_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unconfirmed_days", sa.Integer(), nullable=False, server_default="0"),
        _user_fk("created_by"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        op.f("ix_leave_compare_runs_month_created_at"),
        "leave_compare_runs",
        ["month", "created_at"],
    )

    op.create_table(
        "leave_months",
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("required_hours", sa.Numeric(5, 1), nullable=True),
        sa.Column("last_leave_changed_at", _TS, nullable=True),
        sa.Column("last_compared_at", _TS, nullable=True),
        sa.Column(
            "last_compare_run_id",
            _uuid(),
            sa.ForeignKey("leave_compare_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actual_recorded_at", _TS, nullable=True),
        _user_fk("actual_recorded_by"),
    )

    op.create_table(
        "kinmu_sheets",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("drive_file_id", sa.String(128), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        # 確定版を作り直した理由 (§7-2 の 3)。
        sa.Column("reason", sa.Text(), nullable=True),
        _user_fk("created_by"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('plan','final')", name=op.f("ck_kinmu_sheets_kind")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_kinmu_sheets_version")),
        sa.UniqueConstraint(
            "month", "kind", "version", name=op.f("uq_kinmu_sheets_month_kind_version")
        ),
    )

    op.create_table(
        "google_drive_connections",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("is_singleton", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("folder_id", sa.String(128), nullable=True),
        sa.Column("shared_drive_id", sa.String(128), nullable=True),
        _user_fk("connected_by"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('connected','needs_reconnect')",
            name=op.f("ck_google_drive_connections_status"),
        ),
        # 1 行だけ: is_singleton は真しか入らず、真の行は部分 UNIQUE で 1 つだけ。
        sa.CheckConstraint("is_singleton", name=op.f("ck_google_drive_connections_singleton")),
    )
    op.create_index(
        op.f("uq_google_drive_connections_singleton"),
        "google_drive_connections",
        ["is_singleton"],
        unique=True,
        postgresql_where=sa.text("is_singleton = true"),
        sqlite_where=sa.text("is_singleton = 1"),
    )

    if _is_pg():
        # 月の列は月の 1 日だけ (SQLite は EXTRACT が無いので PG だけ・スキーマでも月初にそろえる)。
        for table in _MONTH_TABLES:
            op.execute(
                f'ALTER TABLE {table} ADD CONSTRAINT "ck_{table}_month_first_day" '
                "CHECK (EXTRACT(DAY FROM month) = 1)"
            )


def _seed_baseline() -> None:
    """既存の休みの override を「照合前」にする (§2-5 H-f)."""
    bind = op.get_bind()
    overrides = sa.table(
        "staff_weekly_overrides",
        sa.column("staff_id", _uuid()),
        sa.column("iso_year", sa.Integer()),
        sa.column("iso_week", sa.Integer()),
        sa.column("weekday", sa.Integer()),
        sa.column("override_type", sa.String()),
    )
    rows = bind.execute(
        sa.select(
            overrides.c.staff_id, overrides.c.iso_year, overrides.c.iso_week, overrides.c.weekday
        ).where(overrides.c.override_type.in_(_LEAVE_TYPES))
    ).all()
    if not rows:
        return

    leave_rows: list[dict[str, object]] = []
    months: set[date] = set()
    for staff_id, iso_year, iso_week, weekday in rows:
        # weekday は 0=月..6=日、ISO は 1=月..7=日。
        day = date.fromisocalendar(int(iso_year), int(iso_week), int(weekday) + 1)
        leave_rows.append(
            {
                "id": uuid.uuid4(),
                "staff_id": staff_id,
                "leave_date": day,
                "sent_type": None,
                "baseline_pending": True,
            }
        )
        months.add(day.replace(day=1))

    op.bulk_insert(
        sa.table(
            "staff_leave_kaipoke",
            sa.column("id", _uuid()),
            sa.column("staff_id", _uuid()),
            sa.column("leave_date", sa.Date()),
            sa.column("sent_type", sa.String()),
            sa.column("baseline_pending", sa.Boolean()),
        ),
        leave_rows,
    )
    op.bulk_insert(
        sa.table("leave_months", sa.column("month", sa.Date())),
        [{"month": m} for m in sorted(months)],
    )


def upgrade() -> None:
    is_pg = _is_pg()
    _add_override_columns(is_pg)
    _set_request_type_check(_REQUEST_TYPES, name=_REQUEST_TYPE_CHECK)
    _add_staff_columns()
    _create_tables()
    _seed_baseline()


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """注意: 照合前の印・事業所の休み・照合の結果・勤務表の記録・Google の接続は失われる."""
    is_pg = _is_pg()

    op.drop_index(
        op.f("uq_google_drive_connections_singleton"), table_name="google_drive_connections"
    )
    op.drop_table("google_drive_connections")
    op.drop_table("kinmu_sheets")
    op.drop_table("leave_months")
    op.drop_index(op.f("ix_leave_compare_runs_month_created_at"), table_name="leave_compare_runs")
    op.drop_table("leave_compare_runs")
    op.drop_table("kaipoke_leave_judgements")
    op.drop_table("staff_leave_kaipoke")
    op.drop_table("office_closed_days")

    if is_pg:
        # 列の CHECK・FK は列と一緒に消える。
        op.drop_column("staff", "kinmu_title")
        for column, _name, _cond in reversed(_STAFF_CHECKS):
            op.drop_column("staff", column)
        op.drop_column("staff_weekly_overrides", "pending_request_id")
        op.drop_column("staff_weekly_overrides", "source")
        # 0098 までの状態 (本番の名前) に 1 つだけ戻す。
        _set_request_type_check(_OLD_REQUEST_TYPES, name=_LEGACY_REQUEST_TYPE_CHECK, not_valid=True)
        return

    # SQLite: CHECK に参照される列は直接 DROP できないので batch で再構築 (0089 と同じ)。
    with op.batch_alter_table("staff") as batch:
        batch.drop_column("kinmu_title")
        for column, name, _cond in reversed(_STAFF_CHECKS):
            batch.drop_constraint(op.f(name), type_="check")
            batch.drop_column(column)
    with op.batch_alter_table("staff_weekly_overrides") as batch:
        # SQLite は列の REFERENCES の名前を反映しないので、FK は列と一緒に落とす。
        batch.drop_column("pending_request_id")
        batch.drop_constraint(op.f(_OVERRIDE_SOURCE_CHECK), type_="check")
        batch.drop_column("source")
    # mig0013 の SQLite は request_type に CHECK を付けていなかったので、外すだけにする。
    existing = {
        c.get("name") for c in sa.inspect(op.get_bind()).get_check_constraints("pending_requests")
    }
    with op.batch_alter_table("pending_requests", recreate="always") as batch:
        for old in _REQUEST_TYPE_CHECK_NAMES:
            if old in existing:
                batch.drop_constraint(op.f(old), type_="check")
