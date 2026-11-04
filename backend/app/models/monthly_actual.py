"""月の実績の作業 (らく助の QR 実績 × カイポケの実績) — mig 0104・0108 (記録書Ⅱの結果).

正典: ``docs/plans/kaipoke-actual-write-design-2026-11-01.md`` §5-3 (訪問記録の
「月の実績」タブ)。判定の決まりは ``app.services.monthly_actual.judge``。

* ``monthly_actual_runs``: 月ごとに 1 行。状態・カイポケの実績を読み取った時刻・件数・
  反映のジョブ・最後の反映の結果・確かめた結果・夜の予約。
* ``monthly_actual_rows``: 判定の 1 行。らく助の側 (訪問・到着・退出・新しい時刻) と
  カイポケの側 (旧時刻・職員・記録書Ⅱ)・印・「反映する／しない」の選択・送った結果。
  判定し直すと行は作り直す (管理者が変えた選択と、同じ鍵の送った結果は引き継ぐ)。

カイポケの実績の CSV は ``kaipoke_csv_snapshots`` (division='actual') が正。保存のたびに古い行が
消えるので、ここでは外部キーにせず id と読み取り時刻だけを持つ。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.pending_request import JSONBish

#: ``monthly_actual_runs.state`` の値。
RUN_STATES = ("judged", "applying", "applied", "verified", "failed")


class MonthlyActualRun(Base, TimestampMixin):
    """月の実績の作業 (月ごとに 1 行)."""

    __tablename__ = "monthly_actual_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: ``YYYY-MM``。
    month: Mapped[str] = mapped_column(String(7), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="judged")
    #: 判定に使ったカイポケの実績 (``kaipoke_csv_snapshots.id``・外部キーにしない)。
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    kaipoke_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 判定ごとの件数 ``{match, apply, review, missing, kaipoke_only, total}``。
    counts: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    #: 最後に立てた取り直し・反映のジョブ。
    refresh_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("kaipoke_jobs.id", ondelete="SET NULL"), nullable=True
    )
    apply_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("kaipoke_jobs.id", ondelete="SET NULL"), nullable=True
    )
    #: 最後の反映の結果 (試し／本番・件数・終わった時刻・失敗の理由)。
    last_apply: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    #: 反映の後に実績を読み取り直して確かめた結果。
    verify_result: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    #: 夜に流す予約 (この時刻を過ぎたら cron が反映を始める)。
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_dry_run: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    scheduled_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class MonthlyActualRow(Base, TimestampMixin):
    """判定の 1 行."""

    __tablename__ = "monthly_actual_rows"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("monthly_actual_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    #: match / apply / review / missing / kaipoke_only。
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 印 ``[{code, label, review}]``。
    flags: Mapped[list] = mapped_column(JSONBish, nullable=False, default=list)
    #: RPA へ送る項目の鍵 (日・利用者・カイポケの旧時刻・職員1)。カイポケの行が無ければ NULL。
    item_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    visit_date: Mapped[date] = mapped_column(Date, nullable=False)
    patient: Mapped[str] = mapped_column(String(100), nullable=False)

    # らく助の側
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("visits.id", ondelete="SET NULL"), nullable=True
    )
    patient_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rk_staff: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rk_arrival: Mapped[str | None] = mapped_column(String(5), nullable=True)
    rk_departure: Mapped[str | None] = mapped_column(String(5), nullable=True)
    #: 後から合わせた時刻 (「到着」「退出」「到着・退出」)。
    rk_adjusted: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rk_stay: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    new_end: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # カイポケの側
    kp_patient: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kp_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    kp_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    kp_staff1: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kp_staff: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kp_service: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kp_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    k2_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    k2_end: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # 反映する／しない (管理者が変えたら selected_by が入る)
    selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    selected_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 送った結果 (RPA の details[].status・reason)
    apply_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    apply_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    apply_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    apply_dry_run: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 試しで ``dry_run_ok`` になったときの「鍵|新しい開始-新しい終了」。本番はこれが今の行と
    #: 同じ行だけ送る (試しを通った行だけ)。判定し直しても同じ鍵の行へ引き継ぐ。
    dry_run_ok_sig: Mapped[str | None] = mapped_column(String(240), nullable=True)

    # 記録書Ⅱをそろえた結果 (mig 0108・RPA の care_record_status・care_record_reason)。
    # 実績と続けて送った行は実績の結果と別に、記録書Ⅱだけ送った行はここだけに書く。
    care_record_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    care_record_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    care_record_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    care_record_dry_run: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    care_record_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_monthly_actual_rows_run_seq", "run_id", "seq"),)


__all__ = ["RUN_STATES", "MonthlyActualRow", "MonthlyActualRun"]
