"""courses.code CHECK に 臨 / 臨2-臨9 を追加 (R-3 臨時コース).

カイポケ→CareFlow 逆反映 (kaipoke-reverse-sync-design.md §8-1):
カイポケ側でスタッフが変更され、新担当がその日コースを持たない場合に
「臨時コース」(その日だけ1人で対応する回り) を新設する。行名 = 臨 / 臨2..臨9。

Revision ID: 0057_course_code_rin
Revises: 0056_correction_sheet_direction
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0057_course_code_rin"
down_revision: Union[str, Sequence[str], None] = "0056_correction_sheet_direction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_CODES = "('A', 'B', 'C', 'D', 'E', 'M', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8', 'M9')"
_NEW_CODES = (
    "('A', 'B', 'C', 'D', 'E', 'M', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8', 'M9', "
    "'臨', '臨2', '臨3', '臨4', '臨5', '臨6', '臨7', '臨8', '臨9')"
)


def upgrade() -> None:
    op.drop_constraint("ck_courses_code_v2", "courses", type_="check")
    op.create_check_constraint("ck_courses_code_v2", "courses", f"code IN {_NEW_CODES}")


def downgrade() -> None:
    # 臨コースの行が存在すると旧 CHECK の再作成に失敗するため先に掃除が必要
    # (運用上は 臨コースを削除してから downgrade する)。
    op.drop_constraint("ck_courses_code_v2", "courses", type_="check")
    op.create_check_constraint("ck_courses_code_v2", "courses", f"code IN {_OLD_CODES}")
