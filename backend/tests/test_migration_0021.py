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

from datetime import UTC
from pathlib import Path

import pytest
import pytest_asyncio  # noqa: F401 — needed for async test collection
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
    # partial UNIQUE は PG・SQLite 共に raw SQL (op.execute) で統一
    # — 旧実装の postgresql_where= keyword の代わりに op.execute を使う
    assert "op.execute" in upgrade_body

    # courses: 旧 UNIQUE drop + 新 UNIQUE create (office_id 含む)
    assert 'drop_constraint("uq_courses_year_week_weekday_code"' in upgrade_body
    assert "uq_courses_year_week_weekday_code_office" in upgrade_body


# ---------------------------------------------------------------------------
# 3. モデル側: Course UNIQUE が拡張済 / CourseTemplate partial Index あり
# ---------------------------------------------------------------------------


def test_course_model_unique_includes_office_id() -> None:
    """W15-codex-fix (4) + W41 v1.0: Course UNIQUE が office_id を含む.

    migration 0030 で旧 ``UniqueConstraint`` から partial UNIQUE INDEX
    (``WHERE course_status != 'proposed'``) に切り替えたため、本テストでは
    Index 側で同一カラム集合が宣言されていることを検証する.
    """
    from app.models.course import Course

    indexes = list(Course.__table__.indexes)
    matching = [
        i
        for i in indexes
        if i.name == "uq_courses_year_week_weekday_code_office_finalized" and i.unique is True
    ]
    assert len(matching) == 1, (
        "partial UNIQUE INDEX uq_courses_year_week_weekday_code_office_finalized が見つからない"
    )
    idx = matching[0]
    assert {col.name for col in idx.columns} == {
        "iso_year",
        "iso_week",
        "weekday",
        "code",
        "office_id",
    }, "Course partial UNIQUE INDEX のカラム集合が想定外"


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


# ---------------------------------------------------------------------------
# 4. SQLite partial UNIQUE: migration 0021 SQLite 分岐も partial UNIQUE を使う
# ---------------------------------------------------------------------------


def test_migration_0021_sqlite_branch_uses_partial_unique() -> None:
    """migration 0021 の SQLite 分岐が partial UNIQUE INDEX (WHERE 節付き) を
    使うことを静的解析で確認する (SQLite 3.8.0+ サポート対応)."""
    backend_root = Path(__file__).resolve().parent.parent
    src = (
        backend_root / "alembic" / "versions" / "0021_v2_course_templates_courses_uniqueness.py"
    ).read_text(encoding="utf-8")

    # SQLite 分岐は is_pg==False のとき実行されるコード。
    # 旧実装の「フル UNIQUE (WHERE 節なし)」が残っていないこと、
    # および両方の分岐に "WHERE deleted_at IS NULL" が含まれることを確認。
    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    upgrade_body = src[upgrade_idx:downgrade_idx]

    # partial WHERE 節が upgrade() に含まれること
    assert upgrade_body.count("WHERE deleted_at IS NULL") >= 2, (
        "upgrade() の SQLite 分岐も partial UNIQUE INDEX (WHERE deleted_at IS NULL) を持つこと"
    )

    # SQLite 向けに sqlite_where=... のみの create_index (WHERE 節なし) が
    # 残っていないこと — 旧コードの「フル UNIQUE」代替パターンを検出
    # (op.create_index(...) のみで WHERE が無い呼び出しが upgrade 内に無いこと)
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "upgrade":
            continue
        for subnode in ast.walk(node):
            if not isinstance(subnode, ast.Call):
                continue
            func = subnode.func
            func_name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if func_name != "create_index":
                continue
            # create_index 呼び出しに postgresql_where または sqlite_where、
            # または execute (raw SQL) が使われているかを確認
            kw_names = {kw.arg for kw in subnode.keywords}
            # op.create_index without any WHERE condition inside upgrade is the old SQLite branch
            has_where = "postgresql_where" in kw_names or "sqlite_where" in kw_names
            assert has_where, (
                f"upgrade() の create_index 呼び出しに WHERE 条件がありません: "
                f"line {subnode.lineno}"
            )


@pytest.mark.asyncio
async def test_migration_0021_sqlite_partial_unique_allows_relabel_after_soft_delete() -> None:
    """SQLite 環境で migration 0021 の partial UNIQUE 動作を実機確認.

    論理削除済み (deleted_at IS NOT NULL) の同一 (office_id, label) を持つ
    CourseTemplate を新規作成できること (partial UNIQUE の恩恵) を検証する。
    """
    import uuid
    from datetime import datetime

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # 最小テーブルを手動 DDL で作成 (alembic 依存を避ける)
        await conn.execute(
            sa.text(
                """
                CREATE TABLE offices (
                    id TEXT PRIMARY KEY
                )
                """
            )
        )
        await conn.execute(
            sa.text(
                """
                CREATE TABLE course_templates (
                    id TEXT PRIMARY KEY,
                    office_id TEXT NOT NULL REFERENCES offices(id),
                    label TEXT NOT NULL,
                    deleted_at TEXT
                )
                """
            )
        )
        # SQLite 3.8.0+ partial UNIQUE INDEX
        await conn.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_course_templates_office_label_active
                ON course_templates (office_id, label)
                WHERE deleted_at IS NULL
                """
            )
        )

    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    office_id = str(uuid.uuid4())

    async with async_session() as session:
        # office を insert
        await session.execute(sa.text("INSERT INTO offices (id) VALUES (:id)"), {"id": office_id})
        await session.commit()

    async with async_session() as session:
        # (A) label='X' で INSERT — 成功するはず
        id_a = str(uuid.uuid4())
        await session.execute(
            sa.text(
                "INSERT INTO course_templates (id, office_id, label, deleted_at) "
                "VALUES (:id, :oid, :label, NULL)"
            ),
            {"id": id_a, "oid": office_id, "label": "X"},
        )
        await session.commit()

    async with async_session() as session:
        # (B) 同 label='X' で INSERT → UNIQUE 違反 (deleted_at IS NULL)
        try:
            await session.execute(
                sa.text(
                    "INSERT INTO course_templates (id, office_id, label, deleted_at) "
                    "VALUES (:id, :oid, :label, NULL)"
                ),
                {"id": str(uuid.uuid4()), "oid": office_id, "label": "X"},
            )
            await session.commit()
            pytest.fail("UNIQUE 違反が発生しなかった (partial UNIQUE が機能していない)")
        except Exception:
            await session.rollback()

    async with async_session() as session:
        # (C) (A) を論理削除 — deleted_at をセット
        now = datetime.now(tz=UTC).isoformat()
        await session.execute(
            sa.text("UPDATE course_templates SET deleted_at = :now WHERE id = :id"),
            {"now": now, "id": id_a},
        )
        await session.commit()

    async with async_session() as session:
        # (D) 論理削除後なら同 label='X' で再 INSERT 可能 (partial UNIQUE 範囲外)
        id_d = str(uuid.uuid4())
        await session.execute(
            sa.text(
                "INSERT INTO course_templates (id, office_id, label, deleted_at) "
                "VALUES (:id, :oid, :label, NULL)"
            ),
            {"id": id_d, "oid": office_id, "label": "X"},
        )
        await session.commit()

    await engine.dispose()
