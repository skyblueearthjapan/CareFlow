"""schedule_op_log — 操作ジャーナル (Wave U-3 undo/redo 基盤).

v1 記録対象（高頻度の直感編集のみ）:
    a. POST /schedule/place-and-fix (fix_pattern=False のみ = 週だけ配置/移動)
       inverse = 生成 visit の soft-delete
    b. DELETE /visits/{id} (DnD テーブル内移動の前半の削除)
       inverse = soft-delete 解除（復元）
    c. PATCH /courses/{id} の assigned_staff_id 変更
       inverse = 旧 staff_id へ戻す
    d. POST /v2/visit-move-week-only
       inverse = 逆方向移動

v2 後続（スナップショット undo・記録しない操作）:
    - PUT fixed-visits / apply 系（PFV 系操作）は v1 では記録しない
    - 範囲最適化 apply・改善提案 apply 等の一括操作
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base

# Cross-dialect JSONB: PostgreSQL gets native JSONB, SQLite (tests) gets JSON.
# pending_request.py / course.py と同じ with_variant 戦略。
JSONBish = JSONB().with_variant(JSON(), "sqlite")


class ScheduleOpLog(Base):
    """操作ジャーナル (U-3).

    ユーザー×週 のスタックで undo/redo を実現する。
    1ユーザー操作 = 1 op_group_id でグルーピング（FE が DnD の delete+place に
    同値を送る）。単発操作は省略時に自動 UUID を発行。

    逆実行:
        undo = inverse_payload の実行（グループ内は created_at DESC 順）
        redo = forward_payload の再実行（グループ内は created_at ASC 順）

    楽観ロック:
        undo/redo の前に "forward が生み出した状態" の現在値を検証し、
        他者変更があれば 409「他の変更があったため戻せません」で中断。

    Excel 同等のブランチ動作:
        新操作が記録された時点で、その週の自分の undone=True 行（redo 枝）を削除。
    """

    __tablename__ = "schedule_op_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 操作者（ユーザー）— ForeignKey は付けない（ユーザー削除時も記録は残す）
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )

    # 対象 ISO 週
    iso_year: Mapped[int] = mapped_column(Integer, nullable=False)
    iso_week: Mapped[int] = mapped_column(Integer, nullable=False)

    # 1ユーザー操作 = 1グループ
    op_group_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )

    # 操作種別（place_and_fix / delete_visit / patch_course_staff / move_visit_week_only）
    op_kind: Mapped[str] = mapped_column(String(64), nullable=False)

    # 日本語ラベル（UI 表示用: 「◯◯様を 火10:00→水14:00 に移動」等）
    label: Mapped[str] = mapped_column(String(256), nullable=False)

    # forward_payload: この操作の効果を再現する指示（redo で使用）
    # 例: {"op": "restore_visits", "visit_ids": ["uuid1", ...]}
    forward_payload: Mapped[dict] = mapped_column(JSONBish, nullable=False, default=dict)

    # inverse_payload: この操作を逆転する指示（undo で使用）
    # 例: {"op": "soft_delete_visits", "visit_ids": ["uuid1", ...]}
    inverse_payload: Mapped[dict] = mapped_column(JSONBish, nullable=False, default=dict)

    # undo 済みフラグ（True = redo 枝、False = undo 対象）
    undone: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # (user_id, iso_year, iso_week, created_at) のインデックス — undo/redo クエリ用
        Index(
            "ix_schedule_op_log_user_year_week_created",
            "user_id",
            "iso_year",
            "iso_week",
            "created_at",
        ),
    )
