"""連携結果レポート (らく助 ⇄ カイポケ) の read スキーマ。

``services/kaipoke/sync_report.SyncReport.to_dict()` をそのまま検証して返す。
FE 慣例に合わせ camelCase の alias を持ち、`populate_by_name=True` なので
snake_case の dict からも構築できる。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CFG = ConfigDict(populate_by_name=True, extra="forbid")


class SyncReportJob(BaseModel):
    model_config = _CFG

    id: str
    op: str
    op_label: str = Field(alias="opLabel")
    direction: Literal["outbound", "inbound"]
    status: str
    week_start: str | None = Field(default=None, alias="weekStart")
    week_end: str | None = Field(default=None, alias="weekEnd")
    month: str | None = None
    started_at: datetime | None = Field(default=None, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    duration_sec: int | None = Field(default=None, alias="durationSec")
    executor_name: str | None = Field(default=None, alias="executorName")
    result_unknown: bool = Field(default=False, alias="resultUnknown")


class SyncReportSummary(BaseModel):
    model_config = _CFG

    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    excluded: int = 0
    # 結果が確定していない行 (pending / RPA 無応答)。> 0 なら「全件成功」ではない。
    unresolved: int = 0
    attention: int = 0


class SyncReportExclusion(BaseModel):
    model_config = _CFG

    reason: str
    label: str
    count: int


class SyncReportRow(BaseModel):
    model_config = _CFG

    date: str | None = None
    start: str = ""
    end: str = ""
    user_name: str = Field(default="", alias="userName")
    action: str = ""
    action_label: str = Field(default="", alias="actionLabel")
    outcome: str = ""
    outcome_label: str = Field(default="", alias="outcomeLabel")
    outcome_tag: str = Field(default="", alias="outcomeTag")
    change_text: str = Field(default="", alias="changeText")
    reason: str | None = None
    reason_label: str | None = Field(default=None, alias="reasonLabel")


class SyncReportAttention(BaseModel):
    model_config = _CFG

    date: str | None = None
    time: str = ""
    subject: str = ""
    what: str = ""
    outcome_label: str = Field(default="", alias="outcomeLabel")
    outcome_tag: str = Field(default="", alias="outcomeTag")
    reason_label: str = Field(default="", alias="reasonLabel")


class SyncReportDay(BaseModel):
    model_config = _CFG

    date: str
    weekday: str = ""
    label: str = ""
    compact: bool = True
    rows: list[SyncReportRow] = Field(default_factory=list)


class SyncReportReplaceDay(BaseModel):
    model_config = _CFG

    date: str
    weekday: str = ""
    wiped: int = 0
    inserted: int = 0
    sunday_skipped: bool = Field(default=False, alias="sundaySkipped")


class SyncReportSkip(BaseModel):
    model_config = _CFG

    date: str | None = None
    start: str = ""
    user_name: str = Field(default="", alias="userName")
    staff_name: str = Field(default="", alias="staffName")
    reason: str | None = None
    reason_label: str = Field(default="", alias="reasonLabel")


class SyncReportTraineeSolo(BaseModel):
    model_config = _CFG

    staff_name: str = Field(default="", alias="staffName")
    count: int = 0


class SyncReportEvent(BaseModel):
    model_config = _CFG

    date: str | None = None
    start: str = ""
    end: str = ""
    staff_name: str = Field(default="", alias="staffName")
    title: str = ""
    action: str = ""
    action_label: str = Field(default="", alias="actionLabel")
    outcome: str = ""
    outcome_label: str = Field(default="", alias="outcomeLabel")
    outcome_tag: str = Field(default="", alias="outcomeTag")
    change_text: str = Field(default="", alias="changeText")
    reason: str | None = None
    reason_label: str | None = Field(default=None, alias="reasonLabel")


class SyncReportVerification(BaseModel):
    model_config = _CFG

    available: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    fetched_at: datetime | None = Field(default=None, alias="fetchedAt")
    note: str | None = None


class SyncReportReasonCode(BaseModel):
    model_config = _CFG

    code: str
    label: str


class SyncReportRead(BaseModel):
    """GET /integrations/kaipoke/jobs/{id}/report の JSON 応答。"""

    model_config = _CFG

    job: SyncReportJob
    summary: SyncReportSummary
    conclusion_tone: Literal["green", "amber", "red"] = Field(alias="conclusionTone")
    conclusion_text: str = Field(alias="conclusionText")
    exclusions: list[SyncReportExclusion] = Field(default_factory=list)
    attention: list[SyncReportAttention] = Field(default_factory=list)
    days: list[SyncReportDay] = Field(default_factory=list)
    excluded_rows: list[SyncReportRow] = Field(default_factory=list, alias="excludedRows")
    replace_days: list[SyncReportReplaceDay] = Field(default_factory=list, alias="replaceDays")
    skips: list[SyncReportSkip] = Field(default_factory=list)
    trainee_solo: list[SyncReportTraineeSolo] = Field(default_factory=list, alias="traineeSolo")
    events: list[SyncReportEvent] = Field(default_factory=list)
    verification: SyncReportVerification = Field(default_factory=SyncReportVerification)
    detail_level: Literal["full", "summary_only"] = Field(default="full", alias="detailLevel")
    reason_codes: list[SyncReportReasonCode] = Field(default_factory=list, alias="reasonCodes")
    generated_at: datetime = Field(alias="generatedAt")
    html: str | None = None
