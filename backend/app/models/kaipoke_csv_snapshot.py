"""KaipokeCsvSnapshot — カイポケ現況CSVの「最後に取得したもの」の保存.

正典 = ``docs/plans/week-cockpit-design.md`` §1 D3 / §2-4 (mig 0076)。

「●未送信」(らく助側の変更のうち、まだカイポケへ送っていないもの) を **RPA を
呼ばずに** 出すための土台。export に成功した経路がここへ CSV を丸ごと置き、
未送信は ``build_local_diff(current_csv=保存分)`` のローカル実行 1 本で求まる
(調査報告 §3-4 案 b — 同期刻印を 3 経路に分散させない)。

* 同 ``(office_id, month, week_start)`` は最新 1 件のみ保持 (保存時に古い行を削除)。
  週限定CSVと月まるごとCSVは共存し、どちらを使うかは ``get_latest`` が選ぶ。
* 保存CSVが古ければ「カイポケ側が違う」も混ざる → UI は ``fetched_at`` を出し、
  🔄突合 (RPA export) で更新する運用 (設計 §2-4)。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KaipokeCsvSnapshot(Base):
    """kaipoke_csv_snapshots テーブル (migration 0076)。"""

    __tablename__ = "kaipoke_csv_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # NULL = 拠点無指定での export (単一事業所運用の既定)。
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("offices.id", ondelete="CASCADE"), nullable=True
    )
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    # 週マージ export (export_current_week_csv) の対象週 (月曜)。月まるごとの
    # export は NULL。週CSVを別の週の「現況」と取り違えないための識別子であり、
    # upsert キーの一部でもある (月CSVを週CSVが消さない)。
    week_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    csv_text: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # どの経路の export か (diff-local / diff-inbound / smart-preview / ...)。
    source_op: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("ix_kaipoke_csv_snapshots_office_month", "office_id", "month"),)
