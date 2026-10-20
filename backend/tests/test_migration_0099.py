"""alembic migration 0099 (休み申請と「休み・勤務表」の表と列) のテスト.

1. 0099 が 0098 から派生し、head が単一。revision id は 32 文字以内。
2. SQLite で upgrade → override・staff の新しい列 (既定と CHECK)・新しい表 7 つができる。
3. ``pending_requests.request_type`` の CHECK: 本番 PG と同じ旧 9 種の CHECK がある表で、
   旧 9 種以外 (staff_status_update など) が upgrade 前は落ち、upgrade 後はアプリの 12 種
   すべてが通る。12 種以外は落ちる。
4. 既存の休みの override (公休・半休・有給) ごとに staff_leave_kaipoke が「照合前」で
   でき、時間変更には作らない。override のある月に leave_months ができる。
5. downgrade で表と列が消え、CHECK は旧 9 種 (SQLite は mig0013 どおり無し) に戻り、
   再 upgrade で戻る。
6. モデルの CHECK (create_all で作る試験の DB) も 12 種とそろっている。
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.models.pending_request import PendingRequest
from app.schemas.v2.enums import RequestType

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REVISION = "0099_leave_and_kinmu"

_OLD_TYPES = (
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
_NEW_TABLES = {
    "office_closed_days",
    "staff_leave_kaipoke",
    "kaipoke_leave_judgements",
    "leave_compare_runs",
    "leave_months",
    "kinmu_sheets",
    "google_drive_connections",
}

# 0098 までの状態のうち、0099 が触る表だけ (pending_requests は本番 PG と同じ旧 9 種の CHECK)。
_PREREQ_SQL = (
    "CREATE TABLE users (id VARCHAR(36) NOT NULL PRIMARY KEY)",
    "CREATE TABLE kaipoke_jobs (id VARCHAR(36) NOT NULL PRIMARY KEY)",
    """
    CREATE TABLE staff (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        name VARCHAR(120) NOT NULL
    )
    """,
    f"""
    CREATE TABLE pending_requests (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        requester_user_id VARCHAR(36) NOT NULL REFERENCES users (id),
        request_type VARCHAR(40) NOT NULL,
        payload JSON NOT NULL,
        status VARCHAR(16) DEFAULT 'pending' NOT NULL,
        CONSTRAINT ck_pending_requests_request_type
            CHECK (request_type IN ({", ".join(f"'{t}'" for t in _OLD_TYPES)}))
    )
    """,
    """
    CREATE TABLE staff_weekly_overrides (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        staff_id VARCHAR(36) NOT NULL REFERENCES staff (id) ON DELETE CASCADE,
        iso_year INTEGER NOT NULL,
        iso_week SMALLINT NOT NULL,
        weekday SMALLINT NOT NULL,
        override_type VARCHAR(16) NOT NULL,
        start_time TIME,
        end_time TIME,
        reason TEXT,
        CONSTRAINT uq_staff_week_override UNIQUE (staff_id, iso_year, iso_week, weekday)
    )
    """,
)


def _run(engine: sa.Engine, fn_name: str) -> None:
    path = _BACKEND_ROOT / "alembic" / "versions" / f"{_REVISION}.py"
    spec = importlib.util.spec_from_file_location("migration_0099", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0099"] = mod
    spec.loader.exec_module(mod)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, fn_name)()


_STAFF_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
_STAFF_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")


def _prepare(tmp_path: Path) -> sa.Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0099.db'}")
    with engine.begin() as conn:
        for sql in _PREREQ_SQL:
            conn.execute(sa.text(sql))
        conn.execute(sa.text("INSERT INTO users (id) VALUES ('u1')"))
        for sid, name in ((_STAFF_A, "A"), (_STAFF_B, "B")):
            # 試験の DB と同じく UUID は 32 桁の 16 進 (sa.Uuid の SQLite 表現) で置く。
            conn.execute(
                sa.text("INSERT INTO staff (id, name) VALUES (:id, :name)"),
                {"id": sid.hex, "name": name},
            )
        # (職員, ISO 年, ISO 週, 曜日, 種類)
        overrides = (
            (_STAFF_A, 2026, 31, 1, "off"),  # 2026-07-28 (火)
            (_STAFF_A, 2026, 40, 4, "am_off"),  # 2026-10-02 (金)
            (_STAFF_B, 2026, 40, 0, "custom_time"),  # 時間変更 → 作らない
            (_STAFF_B, 2026, 45, 2, "paid_off"),  # 2026-11-04 (水)
            (_STAFF_B, 2026, 53, 3, "pm_off"),  # 2026-12-31 (木)
            (_STAFF_B, 2026, 53, 4, "off"),  # 2027-01-01 (金)・ISO 年は 2026
        )
        for i, (sid, y, w, wd, kind) in enumerate(overrides):
            conn.execute(
                sa.text(
                    "INSERT INTO staff_weekly_overrides "
                    "(id, staff_id, iso_year, iso_week, weekday, override_type) "
                    "VALUES (:id, :sid, :y, :w, :wd, :kind)"
                ),
                {"id": f"o{i}", "sid": sid.hex, "y": y, "w": w, "wd": wd, "kind": kind},
            )
    return engine


def _insert_request(conn: sa.Connection, id_: str, request_type: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO pending_requests (id, requester_user_id, request_type, payload) "
            "VALUES (:id, 'u1', :t, '{}')"
        ),
        {"id": id_, "t": request_type},
    )


def test_migration_0099_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision(_REVISION)
    assert rev is not None
    assert rev.down_revision == "0098_kaipoke_source_snapshots"
    assert len(_REVISION) <= 32
    assert len(list(script.get_heads())) == 1


def test_migration_0099_request_type_check_fixed(tmp_path: Path) -> None:
    engine = _prepare(tmp_path)
    # 本番 PG と同じく、旧 9 種以外は保存できなかった。
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        _insert_request(conn, "before", "staff_status_update")

    _run(engine, "upgrade")
    with engine.begin() as conn:
        for t in RequestType:
            _insert_request(conn, f"r-{t.value}", t.value)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        _insert_request(conn, "bad", "visit_reschedule")

    _run(engine, "downgrade")
    names = {c.get("name") for c in inspect(engine).get_check_constraints("pending_requests")}
    assert "ck_pending_requests_request_type" not in names
    with engine.begin() as conn:
        count = conn.execute(sa.text("SELECT count(*) FROM pending_requests")).scalar()
    assert count == len(RequestType), "downgrade で申請の行は消えない"


def test_migration_0099_columns_tables_and_baseline(tmp_path: Path) -> None:
    engine = _prepare(tmp_path)
    _run(engine, "upgrade")
    insp = inspect(engine)

    assert _NEW_TABLES <= set(insp.get_table_names())
    override_cols = {c["name"] for c in insp.get_columns("staff_weekly_overrides")}
    assert {"source", "pending_request_id"} <= override_cols
    staff_cols = {c["name"] for c in insp.get_columns("staff")}
    assert {"employment_type", "work_form", "admin_hours_per_day", "kinmu_title"} <= staff_cols
    assert {c["name"] for c in insp.get_columns("staff_leave_kaipoke")} == {
        "id",
        "staff_id",
        "leave_date",
        "sent_type",
        "baseline_pending",
        "sent_at",
        "sent_by",
        "kaipoke_external_id",
        "updated_at",
    }
    assert {c["name"] for c in insp.get_columns("leave_months")} == {
        "month",
        "required_hours",
        "last_leave_changed_at",
        "last_compared_at",
        "last_compare_run_id",
        "actual_recorded_at",
        "actual_recorded_by",
    }

    with engine.begin() as conn:
        # 既定: 既存の override は source='admin'、職員は常勤。
        assert set(
            conn.execute(sa.text("SELECT DISTINCT source FROM staff_weekly_overrides")).scalars()
        ) == {"admin"}
        assert set(
            conn.execute(sa.text("SELECT DISTINCT employment_type FROM staff")).scalars()
        ) == {"full_time"}
        leave = conn.execute(
            sa.text(
                "SELECT staff_id, leave_date, sent_type, baseline_pending "
                "FROM staff_leave_kaipoke ORDER BY leave_date"
            )
        ).all()
        months = conn.execute(
            sa.text("SELECT month, last_compared_at FROM leave_months ORDER BY month")
        ).all()

    assert [(uuid.UUID(r[0]), str(r[1])) for r in leave] == [
        (_STAFF_A, "2026-07-28"),
        (_STAFF_A, "2026-10-02"),
        (_STAFF_B, "2026-11-04"),
        (_STAFF_B, "2026-12-31"),
        (_STAFF_B, "2027-01-01"),
    ]
    assert all(r[2] is None and r[3] == 1 for r in leave), "全部「照合前」"
    assert [str(m[0]) for m in months] == [
        "2026-07-01",
        "2026-10-01",
        "2026-11-01",
        "2026-12-01",
        "2027-01-01",
    ]
    assert all(m[1] is None for m in months)

    # CHECK
    bad_sql = (
        "UPDATE staff_weekly_overrides SET source = 'excel'",
        "UPDATE staff SET employment_type = 'contract'",
        "UPDATE staff SET work_form = 'E'",
        "UPDATE staff SET admin_hours_per_day = 25",
        "UPDATE staff_leave_kaipoke SET sent_type = 'custom_time'",
        "INSERT INTO kaipoke_leave_judgements (id, staff_id, event_date, content_hash, subject, "
        f"chosen_kind) VALUES ('j1', '{_STAFF_A.hex}', '2026-10-01', 'h', '休み', 'maybe')",
        "INSERT INTO kinmu_sheets (id, month, kind) VALUES ('k1', '2026-10-01', 'draft')",
        "INSERT INTO google_drive_connections (id, status) VALUES ('g1', 'broken')",
        # UNIQUE(staff_id, leave_date)
        "INSERT INTO staff_leave_kaipoke (id, staff_id, leave_date) "
        f"VALUES ('dup', '{_STAFF_A.hex}', '2026-07-28')",
    )
    for sql in bad_sql:
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            conn.execute(sa.text(sql))
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE staff SET employment_type = 'part_time', work_form = 'B', "
                "admin_hours_per_day = 4.0, kinmu_title = '管理者'"
            )
        )
        conn.execute(sa.text("UPDATE staff_weekly_overrides SET source = 'request'"))
        conn.execute(
            sa.text("INSERT INTO google_drive_connections (id) VALUES (:id)"),
            {"id": uuid.uuid4().hex},
        )
    # google_drive_connections は 1 行だけ。
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO google_drive_connections (id) VALUES (:id)"),
            {"id": uuid.uuid4().hex},
        )

    _run(engine, "downgrade")
    insp = inspect(engine)
    assert not (_NEW_TABLES & set(insp.get_table_names()))
    assert not (
        {"source", "pending_request_id"}
        & {c["name"] for c in insp.get_columns("staff_weekly_overrides")}
    )
    assert not (
        {"employment_type", "work_form", "admin_hours_per_day", "kinmu_title"}
        & {c["name"] for c in insp.get_columns("staff")}
    )
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM staff_weekly_overrides")).scalar() == 6

    _run(engine, "upgrade")
    assert _NEW_TABLES <= set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM staff_leave_kaipoke")).scalar() == 5


def test_model_request_type_check_matches_app_types() -> None:
    """create_all で作る試験の DB にも、アプリの 12 種と同じ CHECK が付く."""
    checks = [
        c
        for c in PendingRequest.__table__.constraints
        if isinstance(c, sa.CheckConstraint) and "request_type" in str(c.sqltext)
    ]
    assert len(checks) == 1
    text = str(checks[0].sqltext)
    assert all(f"'{t.value}'" in text for t in RequestType)
    assert text.count("'") == 2 * len(RequestType)
