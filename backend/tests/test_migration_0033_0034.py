"""CareFlow Wave Next 2 [C1] / [H1]: alembic migration 0033 & 0034 tests.

検証観点:
  0033 (M template seed for all offices):
    - upgrade で全 active office (deleted_at IS NULL) に M label template が
      INSERT される (active manager 0 の office にも)
    - 既に M template がある office は INSERT されない (idempotent)
    - downgrade で本 migration が seed した M template のみ DELETE される
      (0022 由来の M template は触らない)

  0034 (courses.code CHECK extension):
    - upgrade で CHECK 制約が M2..M9 を許容する形に再構築される
      (code='M2' で INSERT 成功)
    - downgrade で旧 CHECK ('A','B','C','D','E','M') に戻る
      (M2 行があると downgrade 失敗 — 運用上のガード)

PostgreSQL は ALTER TABLE DROP/ADD CONSTRAINT で同等の挙動.
"""

# ruff: noqa: I001
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine


def _load_migration(name: str):
    backend_root = Path(__file__).resolve().parent.parent
    path = backend_root / "alembic" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"migration_{name}"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _setup_offices_and_templates(engine) -> None:
    """0032 適用済みの最小スキーマを再現する.

    offices + course_templates + courses (CHECK 込み) を作成する.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE offices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    deleted_at TEXT
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE course_templates (
                    id TEXT PRIMARY KEY,
                    office_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    capacity_mon INTEGER NOT NULL DEFAULT 0,
                    capacity_tue INTEGER NOT NULL DEFAULT 0,
                    capacity_wed INTEGER NOT NULL DEFAULT 0,
                    capacity_thu INTEGER NOT NULL DEFAULT 0,
                    capacity_fri INTEGER NOT NULL DEFAULT 0,
                    capacity_sat INTEGER NOT NULL DEFAULT 0,
                    capacity_sun INTEGER NOT NULL DEFAULT 0,
                    notes TEXT,
                    deleted_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )
        # 0023 後の CHECK ('A','B','C','D','E','M') を再現
        conn.execute(
            sa.text(
                """
                CREATE TABLE courses (
                    id TEXT PRIMARY KEY,
                    iso_year INTEGER NOT NULL,
                    iso_week INTEGER NOT NULL,
                    weekday INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    course_status TEXT NOT NULL DEFAULT 'proposed',
                    office_id TEXT NOT NULL,
                    template_id TEXT,
                    deleted_at TEXT,
                    CONSTRAINT ck_courses_code_v2
                        CHECK (code IN ('A', 'B', 'C', 'D', 'E', 'M'))
                )
                """
            )
        )


def test_migration_0033_seeds_m_template_for_managerless_office(tmp_path: Path) -> None:
    """[C1]: 0033 upgrade が manager 不在 office にも M template を seed する."""
    db_path = tmp_path / "migration_0033.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _setup_offices_and_templates(engine)

    # 2 拠点 (office_a: M template 未seed / office_b: 既に M がある)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO offices (id, name, deleted_at) VALUES "
                "('office_a', 'office-a', NULL), "
                "('office_b', 'office-b', NULL), "
                "('office_del', 'office-deleted', '2026-01-01T00:00:00+00:00')"
            )
        )
        # office_a: A template だけ (M なし)
        conn.execute(
            sa.text(
                "INSERT INTO course_templates "
                "(id, office_id, label, notes, deleted_at, created_at, updated_at) "
                "VALUES ('ct_a', 'office_a', 'A', 'existing', NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # office_b: 既に M template が seed されている (0022 由来想定)
        conn.execute(
            sa.text(
                "INSERT INTO course_templates "
                "(id, office_id, label, notes, deleted_at, created_at, updated_at) "
                "VALUES ('ct_b_m', 'office_b', 'M', 'auto-seeded (W16): manager course', "
                "NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    # 0033 upgrade
    mod = _load_migration("0033_v2_seed_m_template_all_offices")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()  # type: ignore[attr-defined]

    # 確認:
    # 1) office_a に M template が新規 INSERT されている
    # 2) office_b の既存 M (0022 由来 notes) は touch されない
    # 3) office_del (deleted) には新規 INSERT されない
    with engine.begin() as conn:
        rows_a = conn.execute(
            sa.text(
                "SELECT label, notes FROM course_templates "
                "WHERE office_id='office_a' AND deleted_at IS NULL"
            )
        ).all()
        rows_b = conn.execute(
            sa.text(
                "SELECT label, notes FROM course_templates "
                "WHERE office_id='office_b' AND deleted_at IS NULL"
            )
        ).all()
        rows_del = conn.execute(
            sa.text(
                "SELECT label FROM course_templates "
                "WHERE office_id='office_del' AND deleted_at IS NULL"
            )
        ).all()

    labels_a = {(r[0], r[1]) for r in rows_a}
    assert ("A", "existing") in labels_a
    assert ("M", "auto-seeded (W-Next2): manager course (manager-less offices)") in labels_a, (
        f"office_a (manager 不在) に M template が seed されていない: {labels_a}"
    )

    # office_b は既存 0022 由来 M のみ
    assert {(r[0], r[1]) for r in rows_b} == {("M", "auto-seeded (W16): manager course")}, (
        f"office_b の既存 M template が変わってしまった: {rows_b}"
    )

    # 削除済み office にはなし
    assert rows_del == [], f"deleted office に seed してしまった: {rows_del}"

    # 0033 downgrade: 本 migration 由来 M template だけ削除される
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.downgrade()  # type: ignore[attr-defined]

    with engine.begin() as conn:
        rows_a_after = conn.execute(
            sa.text("SELECT label, notes FROM course_templates WHERE office_id='office_a'")
        ).all()
        rows_b_after = conn.execute(
            sa.text("SELECT label, notes FROM course_templates WHERE office_id='office_b'")
        ).all()

    # office_a: M (W-Next2 由来) は消えて A だけ残る
    labels_a_after = {(r[0], r[1]) for r in rows_a_after}
    assert (
        "M",
        "auto-seeded (W-Next2): manager course (manager-less offices)",
    ) not in labels_a_after, f"downgrade で W-Next2 由来 M が残っている: {labels_a_after}"
    assert ("A", "existing") in labels_a_after

    # office_b: 0022 由来 M は残る
    assert {(r[0], r[1]) for r in rows_b_after} == {("M", "auto-seeded (W16): manager course")}, (
        f"downgrade で 0022 由来 M を消してしまった: {rows_b_after}"
    )


def test_migration_0034_check_constraint_allows_m_overflow(tmp_path: Path) -> None:
    """[H1]: 0034 upgrade で M2..M9 code が INSERT できる."""
    db_path = tmp_path / "migration_0034.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _setup_offices_and_templates(engine)

    # offices + course_template を 1 件入れて courses.template_id を満たす
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO offices (id, name, deleted_at) VALUES ('o1', 'o1', NULL)")
        )

    # 0034 upgrade 前: M2 は CHECK 違反になるべき
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO courses (id, iso_year, iso_week, weekday, code, "
                    "course_status, office_id) VALUES "
                    "('c_pre_m2', 2026, 20, 0, 'M2', 'proposed', 'o1')"
                )
            )

    # 0034 upgrade
    mod_0034 = _load_migration("0034_courses_code_check_m_overflow")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod_0034.upgrade()  # type: ignore[attr-defined]

    # upgrade 後: M2, M3..M9 が INSERT 可能
    with engine.begin() as conn:
        for i, code in enumerate(["M", "M2", "M3", "M9"]):
            conn.execute(
                sa.text(
                    "INSERT INTO courses (id, iso_year, iso_week, weekday, code, "
                    "course_status, office_id) VALUES "
                    f"('c_post_{i}', 2026, 20, {i}, '{code}', 'proposed', 'o1')"
                )
            )

    # M10 は依然 NG (CHECK で M2..M9 までしか許容しない)
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO courses (id, iso_year, iso_week, weekday, code, "
                    "course_status, office_id) VALUES "
                    "('c_bad', 2026, 20, 0, 'M10', 'proposed', 'o1')"
                )
            )

    # 0034 downgrade: M2..M9 を削除してから戻す
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM courses WHERE code IN ('M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8', 'M9')"
            )
        )
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod_0034.downgrade()  # type: ignore[attr-defined]

    # downgrade 後: M2 は再び CHECK 違反
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO courses (id, iso_year, iso_week, weekday, code, "
                    "course_status, office_id) VALUES "
                    "('c_after_dg', 2026, 20, 0, 'M2', 'proposed', 'o1')"
                )
            )
    # ただし M / A 等は依然 INSERT 可能
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO courses (id, iso_year, iso_week, weekday, code, "
                "course_status, office_id) VALUES "
                "('c_after_M', 2026, 20, 0, 'M', 'proposed', 'o1')"
            )
        )
    engine.dispose()


def test_migration_0033_and_0034_chain_to_head() -> None:
    """[chain]: alembic スクリプト上で 0033 / 0034 がチェーン化されている."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    rev_0033 = script.get_revision("0033_v2_seed_m_template_all_offices")
    assert rev_0033 is not None
    assert rev_0033.down_revision == "0032_staff_code_unique_partial", (
        f"0033 の down_revision は 0032 のはず, got {rev_0033.down_revision}"
    )

    rev_0034 = script.get_revision("0034_courses_code_check_m_overflow")
    assert rev_0034 is not None
    assert rev_0034.down_revision == "0033_v2_seed_m_template_all_offices"

    # Phase E-5 で head は 0035 に進んでいる. このテストの本質は 0033→0034 のチェーン.
    # heads が単一 (= 分岐なし) であることだけ確認する.
    heads = list(script.get_heads())
    assert len(heads) == 1, f"alembic heads は単一であるべき, got {heads}"
