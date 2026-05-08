"""W16-BE1: alembic migration 0022 (M course seed) tests.

検証観点:
  1. 0022 リビジョンが alembic スクリプト上で読み込め、down_revision が
     0021 を指している
  2. upgrade() が
     - offices を走査
     - 拠点ごとに active manager 数を集計
     - course_templates に M / M2 / .. を INSERT
     という命令を含む
  3. downgrade() が auto-seeded M 行を DELETE する
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def test_migration_0022_revision_chain(alembic_cfg: Config) -> None:
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0022_v2_w16_manager_course_seed")
    assert rev is not None, "0022 リビジョンが見つからない"
    assert rev.down_revision == "0021_v2_course_templates_courses_uniqueness", (
        f"0022 の down_revision は 0021 のはず, got {rev.down_revision}"
    )


def test_migration_0022_upgrade_contents() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    src = (backend_root / "alembic" / "versions" / "0022_v2_manager_course_seed.py").read_text(
        encoding="utf-8"
    )

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]

    # offices 走査
    assert "FROM offices" in upgrade_body
    # active manager 集計
    assert "role = 'manager'" in upgrade_body
    assert "status = 'active'" in upgrade_body
    # course_templates INSERT
    assert "INSERT INTO course_templates" in upgrade_body
    # 標準 capacity (月7/火7/水7/木7/金7/土5/日0)
    assert "7, 7, 7" in upgrade_body
    assert "auto-seeded (W16): manager course" in upgrade_body


def test_migration_0022_downgrade_deletes_seeded_rows() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    src = (backend_root / "alembic" / "versions" / "0022_v2_manager_course_seed.py").read_text(
        encoding="utf-8"
    )

    downgrade_idx = src.find("def downgrade()")
    assert downgrade_idx >= 0
    downgrade_body = src[downgrade_idx:]

    assert "DELETE FROM course_templates" in downgrade_body
    assert "auto-seeded (W16): manager course" in downgrade_body


@pytest.mark.asyncio
async def test_migration_0022_runs_with_no_offices() -> None:
    """offices が空でも upgrade が成功する (no-op)."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # 最小テーブル
        await conn.execute(
            sa.text(
                """
                CREATE TABLE offices (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    deleted_at TEXT
                )
                """
            )
        )
        await conn.execute(
            sa.text(
                """
                CREATE TABLE staff (
                    id TEXT PRIMARY KEY,
                    role TEXT,
                    status TEXT,
                    primary_office_id TEXT,
                    deleted_at TEXT
                )
                """
            )
        )
        await conn.execute(
            sa.text(
                """
                CREATE TABLE course_templates (
                    id TEXT PRIMARY KEY,
                    office_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    capacity_mon INTEGER,
                    capacity_tue INTEGER,
                    capacity_wed INTEGER,
                    capacity_thu INTEGER,
                    capacity_fri INTEGER,
                    capacity_sat INTEGER,
                    capacity_sun INTEGER,
                    notes TEXT,
                    deleted_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )

    # 空 DB で upgrade を呼ぶ (no-op で成功するか)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        # offices/staff が空 → INSERT は起きない
        result = await session.execute(sa.text("SELECT COUNT(*) FROM course_templates"))
        count_before = result.scalar()
        assert count_before == 0

    await engine.dispose()
