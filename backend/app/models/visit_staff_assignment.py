"""visit_staff_assignments — visit × staff 多対多 (W2-BE4 / §4.5).

設計仕様書 v0.9 §3.3 / §4.5 で導入された 2 名体制 (`required_staff_count = 2`)
を表現する関連テーブル。

## 1 visit に対して 1 or 2 行 (`required_staff_count` に応じて)

- 通常: required_staff_count = 1 → 1 行 (visit_id, staff_id)
- 2 名体制: required_staff_count = 2 → 2 行 (同じ visit_id, 異なる staff_id)

primary_staff_id / secondary_staff_id は v1 互換のため visit テーブルに残しているが、
**v2 の正規表現は visit_staff_assignments**。Layer 3 (W4-BE9) は本テーブルへ
2 名体制の場合 2 行を INSERT する。

## 主キー

(visit_id, staff_id) 複合主キー。同一 visit に同一 staff を 2 回割当できない。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ---------------------------------------------------------------------------
# Cross-dialect JSONB shim (SQLite test runner safety net).
#
# 既存モデル (audit_logs / kaipoke_jobs / correction_sheets
# 等) の一部はカラム型に PostgreSQL 専用 ``JSONB`` をそのまま使っており、
# SQLite (CI / pytest) では ``Compiler ... can't render element of type JSONB``
# で ``create_all`` が失敗する。W1-BE1 では patient.py に ``JSONB().with_variant(JSON())``
# を入れる方針で個別解決したが、他モデルには適用されていない (Python 3.14 +
# SQLAlchemy 2.0 の組合せで自動 fallback が無くなったため)。
#
# 本モジュールは、 ``app.models`` が import される最も浅い時点で **グローバル
# に JSONB → ローカルの JSON DDL を吐く** 互換シムを登録する。本シムは
# PostgreSQL では何の影響もなく (``compiles(..., 'sqlite')`` のみ反応する)、
# テスト時に SQLite の ``create_all`` を成立させる。
#
# 設計仕様書 v0.9 §4.5 / 実装手順書 v0.2 §3 W2-BE4 で要求された「pytest
# 該当ファイル全 pass」を満たすための test infra 補助であり、
# v2 ロジック自体には関与しない。
# ---------------------------------------------------------------------------


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):  # type: ignore[misc]
    """SQLite に対しては JSONB を JSON として DDL 出力する。"""
    return compiler.visit_JSON(element, **kw)


class VisitStaffAssignment(Base):
    """visit_staff_assignments テーブル (§4.5).

    1 visit に対して 1 or 2 行が存在する。
    """

    __tablename__ = "visit_staff_assignments"

    visit_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # スタッフ別の visit 検索 (RBAC / 自分軸の参照で使う)
        Index("ix_visit_staff_assignments_staff", "staff_id"),
    )
