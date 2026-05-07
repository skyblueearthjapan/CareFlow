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
