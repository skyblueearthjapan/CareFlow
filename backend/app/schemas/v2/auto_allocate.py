"""Auto-allocate (Wave 41 v1.0 / auto-schedule) Pydantic schemas.

設計仕様書: ``docs/plans/auto-schedule-v1.md`` (v0.4) §10 データ書込仕様
実装計画書: ``docs/plans/auto-schedule-implementation-plan.md`` (v0.2) §4 Phase 2

エンドポイント:
  - POST /api/v1/schedule/auto-allocate
      request : ``AutoAllocateRequest``
      response: ``AutoAllocateResponse``
  - POST /api/v1/schedule/proposal/{proposal_batch_id}/apply
      response: ``ProposalApplyResponse``
  - POST /api/v1/schedule/proposal/{proposal_batch_id}/discard
      response: ``ProposalDiscardResponse``

実装方針:
  - auto_allocator.auto_allocate(...) の戻り値 dict をそのまま map できる
    形にフィールドを揃える.
  - mode は v1.0 では "mode_1" のみ実体実装。"mode_2" は service 層で
    NotImplementedError → API 層で 501 に変換 (本 schema 自体は許容).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AutoAllocateRequest(BaseModel):
    """``POST /api/v1/schedule/auto-allocate`` のリクエストボディ."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    # H3 (W41 v1.0 cross-review): 異常な巨大リクエストを防ぐため最大 50 拠点に制限.
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    mode: Literal["mode_1", "mode_2"] = "mode_1"


class KpiResponse(BaseModel):
    """``auto_allocate`` 戻り値の KPI dict と 1:1 対応するレスポンス型."""

    model_config = ConfigDict(extra="forbid")

    total_distance_km_before: float
    total_distance_km_after: float
    distance_reduction_pct: float
    staff_load_stddev_before: float
    staff_load_stddev_after: float
    # ハード制約 H1〜H8 の違反件数 (キーは "H1"…"H8").
    h_violations: dict[str, int]
    improvement_score: float


class ProposedCourseSummary(BaseModel):
    """1 提案コースのサマリ (auto_allocator proposals の API 公開形)."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    office_id: uuid.UUID
    weekday: int = Field(ge=0, le=6)
    code: str
    assigned_staff_id: uuid.UUID | None = None
    # M2: UI から staff の表示名を引きやすくするための補助フィールド.
    # auto_allocate endpoint は Staff を join して埋める. 不明な場合は None.
    assigned_staff_name: str | None = None
    visits_count: int = Field(ge=0)


class AutoAllocateResponse(BaseModel):
    """``POST /api/v1/schedule/auto-allocate`` のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    proposal_batch_id: uuid.UUID
    proposals: list[ProposedCourseSummary] = Field(default_factory=list)
    kpi: KpiResponse
    warnings: list[str] = Field(default_factory=list)


class ProposalApplyResponse(BaseModel):
    """``POST /api/v1/schedule/proposal/{proposal_batch_id}/apply`` のレスポンス.

    提案バッチを採用し ``proposed → course_fixed → staff_assigned`` に遷移させた
    結果の件数を返す.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_batch_id: uuid.UUID
    courses_committed: int = Field(ge=0)
    visits_committed: int = Field(ge=0)
    # C2: apply で再投入はしない. **既に auto_allocate 段階で INSERT 済**の
    # visit_staff_assignments の件数 (= 採用された visits に対応する行数) を返す.
    staff_assignments_committed: int = Field(ge=0)
    # M3: 採用された course_id のリスト (UI 側で後続 fetch を絞り込むための補助情報).
    course_ids: list[uuid.UUID] = Field(default_factory=list)


class ProposalDiscardResponse(BaseModel):
    """``POST /api/v1/schedule/proposal/{proposal_batch_id}/discard`` のレスポンス.

    提案バッチに紐付く未採用 (proposed) course + visit を物理削除した件数を返す.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_batch_id: uuid.UUID
    courses_deleted: int = Field(ge=0)
    visits_deleted: int = Field(ge=0)


__all__ = [
    "AutoAllocateRequest",
    "AutoAllocateResponse",
    "KpiResponse",
    "ProposalApplyResponse",
    "ProposalDiscardResponse",
    "ProposedCourseSummary",
]
