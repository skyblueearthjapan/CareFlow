"""W15-codex-fix (3)(4): alembic migration 0021 (UNIQUE 再構築) tests.

検証観点:
  1. 0021 リビジョンが alembic スクリプト上で読み込め、down_revision が
     0020 を指している
  2. upgrade() が
     - course_templates の uq_course_templates_office_label を drop し、
       uq_course_templates_office_label_active を partial UNIQUE で再作成
     - courses の uq_courses_year_week_weekday_code を drop し、
       uq_courses_year_week_weekday_code_office に拡張する
     という命令を含む
  3. SQLAlchemy 上のモデル
     - Course の UNIQUE が (iso_year, iso_week, weekday, code, office_id)
     - CourseTemplate の partial UNIQUE Index 定義が deleted_at IS NULL を含む
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
# 1. リビジョンチェーン: 0021 が 0020 を down_revision に持つ
# ---------------------------------------------------------------------------


def test_migration_0021_revision_chain(alembic_cfg: Config) -> None:
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0021_v2_course_templates_courses_uniqueness")
    assert rev is not None, "0021 リビジョンが見つからない"
    assert rev.down_revision == "0020_v2_courses_office_id_not_null", (
        f"0021 の down_revision は 0020 のはず, got {rev.down_revision}"
    )


# ---------------------------------------------------------------------------
# 2. upgrade() の命令内容 (静的解析)
# ---------------------------------------------------------------------------


def test_migration_0021_upgrade_contents() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    src = (
        backend_root / "alembic" / "versions" / "0021_v2_course_templates_courses_uniqueness.py"
    ).read_text(encoding="utf-8")

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]

    # course_templates: 旧 UNIQUE drop + partial INDEX 作成
    assert 'drop_constraint("uq_course_templates_office_label"' in upgrade_body
    assert "uq_course_templates_office_label_active" in upgrade_body
    assert "deleted_at IS NULL" in upgrade_body
    assert "postgresql_where" in upgrade_body

    # courses: 旧 UNIQUE drop + 新 UNIQUE create (office_id 含む)
    assert 'drop_constraint("uq_courses_year_week_weekday_code"' in upgrade_body
    assert "uq_courses_year_week_weekday_code_office" in upgrade_body


# ---------------------------------------------------------------------------
# 3. モデル側: Course UNIQUE が拡張済 / CourseTemplate partial Index あり
# ---------------------------------------------------------------------------


def test_course_model_unique_includes_office_id() -> None:
    """W15-codex-fix (4): Course UNIQUE が office_id を含む."""
    from sqlalchemy import UniqueConstraint

    from app.models.course import Course

    uniques = [c for c in Course.__table__.constraints if isinstance(c, UniqueConstraint)]
    assert any(
        c.name == "uq_courses_year_week_weekday_code_office"
        and {col.name for col in c.columns}
        == {"iso_year", "iso_week", "weekday", "code", "office_id"}
        for c in uniques
    ), "Course UNIQUE は (iso_year, iso_week, weekday, code, office_id) のはず"


def test_course_template_model_has_partial_unique_index() -> None:
    """W15-codex-fix (3): CourseTemplate に partial UNIQUE Index がある."""
    from app.models.course_template import CourseTemplate

    indexes = list(CourseTemplate.__table__.indexes)
    matching = [i for i in indexes if i.name == "uq_course_templates_office_label_active"]
    assert len(matching) == 1, "partial UNIQUE INDEX が見つからない"
    idx = matching[0]
    assert idx.unique is True
    # postgresql_where dialect_options を持つこと
    assert "postgresql" in idx.dialect_options
    where_clause = idx.dialect_options["postgresql"].get("where")
    assert where_clause is not None, "partial UNIQUE の WHERE 節が無い"
