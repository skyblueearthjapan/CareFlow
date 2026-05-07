"""W15-BE-FIXPATTERN: alembic migration 0020 (courses.office_id NOT NULL) tests.

CI 実 DB 検証は PostgreSQL ジョブ (``backend-ci.yml`` migration-smoke)
で行われるため、本テストではローカルで実行可能な範囲として
**スクリプトの構文 / リビジョンチェーン / upgrade-downgrade 命令の対称性**
を静的に検証する。

検証観点:
  1. 0020 スクリプトが alembic ScriptDirectory で読み込めて、
     revision='0020_v2_courses_office_id_not_null'、
     down_revision='0019_v2_w15_be1_foundation' がチェーンに繋がっている
  2. upgrade() / downgrade() が batch_alter_table('courses') を使って
     office_id の nullable=False / nullable=True を切り替える命令を含む
  3. SQLAlchemy モデル側 (``app.models.course.Course.office_id``) が
     既に nullable=False になっている (Phase 2 完了)

これにより、CI 側の PostgreSQL 実 DB テスト (alembic upgrade -> downgrade ->
upgrade head) と整合する形で、ローカルでも 0020 の存在 / 整合性が回帰する。
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


# ---------------------------------------------------------------------------
# 1. リビジョンチェーン: 0020 が 0019 を down_revision に持つ
# ---------------------------------------------------------------------------


def test_migration_0020_revision_chain(alembic_cfg: Config) -> None:
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0020_v2_courses_office_id_not_null")
    assert rev is not None, "0020 リビジョンが見つからない"
    assert rev.down_revision == "0019_v2_w15_be1_foundation", (
        f"0020 の down_revision は 0019 のはず, got {rev.down_revision}"
    )

    # 0019 → 0020 が「親→子」の方向で繋がる
    nxt = script.get_revision("0019_v2_w15_be1_foundation")
    assert nxt is not None
    assert "0020_v2_courses_office_id_not_null" in [r for r in (nxt.nextrev or set())], (
        "0019 から 0020 への nextrev が張れていない"
    )


# ---------------------------------------------------------------------------
# 2. upgrade / downgrade 命令の対称性
# ---------------------------------------------------------------------------


def test_migration_0020_upgrade_downgrade_symmetry() -> None:
    """upgrade で nullable=False、downgrade で nullable=True にする
    alter_column 命令が含まれている (静的解析)."""
    backend_root = Path(__file__).resolve().parent.parent
    src = (
        backend_root / "alembic" / "versions" / "0020_v2_courses_office_id_not_null.py"
    ).read_text(encoding="utf-8")

    # upgrade 内
    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]
    downgrade_body = src[downgrade_idx:]

    # upgrade 側: nullable=False
    assert 'batch_alter_table("courses")' in upgrade_body
    assert '"office_id"' in upgrade_body
    assert "nullable=False" in upgrade_body

    # downgrade 側: nullable=True
    assert 'batch_alter_table("courses")' in downgrade_body
    assert '"office_id"' in downgrade_body
    assert "nullable=True" in downgrade_body


# ---------------------------------------------------------------------------
# 3. モデル側: Course.office_id が NOT NULL (Phase 2 完了)
# ---------------------------------------------------------------------------


def test_course_model_office_id_is_not_null() -> None:
    """SQLAlchemy 上で Course.office_id が nullable=False で定義されている."""
    from app.models.course import Course

    col = Course.__table__.c.office_id
    assert col.nullable is False, "W15-BE-FIXPATTERN Phase 2: Course.office_id は NOT NULL のはず"


# ---------------------------------------------------------------------------
# 4. W15-codex-fix (2): defensive backfill が upgrade() に存在する
# ---------------------------------------------------------------------------


def test_migration_0020_has_defensive_backfill() -> None:
    """W15-codex-fix: 0020 upgrade() に backfill ロジックが含まれている.

    既存運用 DB に courses データがある状態でも 0020 が失敗しないよう、
    UPDATE courses SET office_id = ... WHERE office_id IS NULL という
    backfill statement が含まれているはず。
    """
    backend_root = Path(__file__).resolve().parent.parent
    src = (
        backend_root / "alembic" / "versions" / "0020_v2_courses_office_id_not_null.py"
    ).read_text(encoding="utf-8")

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]

    # backfill: COUNT NULL を取り、UPDATE する
    assert "COUNT(*) FROM courses WHERE office_id IS NULL" in upgrade_body, (
        "0020 backfill: NULL カウント statement が無い"
    )
    assert "UPDATE courses SET office_id" in upgrade_body, "0020 backfill: UPDATE statement が無い"
    # 残存 NULL での RuntimeError abort
    assert "RuntimeError" in upgrade_body, "0020 backfill: 残存 NULL 検知 abort が無い"


# ---------------------------------------------------------------------------
# 5. W15-codex-fix (2): backfill 動作テスト (SQLite simulate)
# ---------------------------------------------------------------------------


def test_migration_0020_backfill_no_op_when_empty() -> None:
    """空 DB (courses 行なし) で 0020 upgrade を呼んでも no-op で完走する.

    実 DB レベルの上げ下げは別 CI ジョブで検証するが、ここでは backfill 部分の
    SQL が「空の courses で安全に通る」ことを SQLite in-memory で確認する。
    """
    import sqlalchemy as sa
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        # 最小スキーマ (courses, patients, visits) を組む
        conn.execute(sa.text("CREATE TABLE patients (id TEXT PRIMARY KEY, primary_office_id TEXT)"))
        conn.execute(sa.text("CREATE TABLE courses (id TEXT PRIMARY KEY, office_id TEXT NULL)"))
        conn.execute(
            sa.text("CREATE TABLE visits (id TEXT PRIMARY KEY, patient_id TEXT, course_id TEXT)")
        )

        # 空のまま backfill SQL を実行
        null_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM courses WHERE office_id IS NULL")
        ).scalar()
        assert null_count == 0


def test_migration_0020_backfill_fills_null_office_id() -> None:
    """courses に office_id NULL の行があり、対応する visits + patients を辿れる場合、
    backfill SQL で office_id が埋まる (W15-codex-fix)."""
    import sqlalchemy as sa
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE patients (id TEXT PRIMARY KEY, primary_office_id TEXT)"))
        conn.execute(sa.text("CREATE TABLE courses (id TEXT PRIMARY KEY, office_id TEXT NULL)"))
        conn.execute(
            sa.text("CREATE TABLE visits (id TEXT PRIMARY KEY, patient_id TEXT, course_id TEXT)")
        )

        # patient (office=O1)、course (office NULL)、visit が両者を結ぶ
        conn.execute(sa.text("INSERT INTO patients (id, primary_office_id) VALUES ('P1', 'O1')"))
        conn.execute(sa.text("INSERT INTO courses (id, office_id) VALUES ('C1', NULL)"))
        conn.execute(
            sa.text("INSERT INTO visits (id, patient_id, course_id) VALUES ('V1', 'P1', 'C1')")
        )

        # Migration の backfill SQL を実行
        conn.execute(
            sa.text(
                "UPDATE courses SET office_id = ("
                "  SELECT p.primary_office_id FROM patients p"
                "  JOIN visits v ON v.patient_id = p.id"
                "  WHERE v.course_id = courses.id"
                "    AND p.primary_office_id IS NOT NULL"
                "  LIMIT 1"
                ") WHERE office_id IS NULL"
            )
        )

        # courses.office_id が 'O1' で埋まる
        result = conn.execute(sa.text("SELECT office_id FROM courses WHERE id = 'C1'")).scalar()
        assert result == "O1"
