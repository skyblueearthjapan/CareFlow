"""入口ガード (患者ステータス連動 Phase 2) の共有 DTO.

正典 = ``docs/plans/patient-status-schedule-design-2026-09-09.md`` §3-3 / §7-3(d)。

単一患者向けの経路は 422 (``code='patient_not_active'``) で止めるが、**一括系**
(pool-overview / pool-bulk-simulate / pool-bulk-apply) は全体を落とさず
「その患者だけ除外して知らせる」。その除外エントリの型がこれ。

キーは ``app.services.scheduling.guards.split_schedulable_patient_ids`` が返す
dict とそのまま一致させる (FE との契約はこの 4 キー)。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExcludedPatient(BaseModel):
    """一括系で除外した患者 1 名分 (非稼働 / 存在しない)."""

    model_config = ConfigDict(extra="forbid")

    patient_id: str = Field(..., description="除外した患者 id (UUID 文字列)")
    status: str | None = Field(
        default=None,
        description="除外時点の Patient.status. 患者が見つからない場合は null",
    )
    status_label: str = Field(..., description="status の日本語表記 (例: 入院中)")
    message: str = Field(..., description="FE がそのまま出せる案内文")


__all__ = ["ExcludedPatient"]
