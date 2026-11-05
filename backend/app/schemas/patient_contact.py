"""患者様の連絡先 API のスキーマ.

正典: ``docs/plans/contacts-phone-email-design-2026-11-05.md`` §5。
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ContactRelation = Literal["self", "family", "facility", "care_manager", "other"]
ContactNotifyChannel = Literal["sms", "email"]
ContactConsentStatus = Literal["unconfirmed", "confirmed", "ng"]
EffectiveChannel = Literal["sms", "email", "none"]


class ContactWrite(BaseModel):
    """PUT の 1 件. ``phone`` は生の入力 (サーバーで見分ける)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = Field(default=None, description="既存の連絡先なら付ける (同意の日を引き継ぐ)")
    relation: ContactRelation
    name: str | None = Field(default=None, max_length=120)
    relation_note: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    is_notify_target: bool = False
    notify_channel: ContactNotifyChannel = "sms"
    consent_status: ContactConsentStatus = "unconfirmed"


class ContactsPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contacts: list[ContactWrite]


class ContactRead(BaseModel):
    id: UUID
    sort_order: int
    relation: ContactRelation
    name: str | None = None
    relation_note: str | None = None
    phone: str | None = None
    phone_display: str | None = None
    phone_kind: str | None = None
    sms_capable: bool | None = None
    email: str | None = None
    email_carrier: bool | None = None
    is_notify_target: bool
    notify_channel: ContactNotifyChannel
    effective_channel: EffectiveChannel
    consent_status: ContactConsentStatus
    consent_on: date | None = None


class ContactList(BaseModel):
    contacts: list[ContactRead]
    notices: list[str] = Field(default_factory=list)


class ContactSummaryItem(BaseModel):
    patient_id: UUID
    contact_count: int
    targets: int
    sms: int
    email: int
    no_means: int
    pending_consent: int
    ng: int


class ContactSummary(BaseModel):
    items: list[ContactSummaryItem]
