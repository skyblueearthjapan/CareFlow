"""Pydantic schemas for patient_same_address_links (Phase G-21).

同住所患者の紐付け 3 モード (blocked / preferred / required) を扱う.

* デフォルト ``preferred`` はアプリ層で扱う (= 行が無ければ preferred 扱い).
  ``blocked`` と ``required`` のみ DB 記録する運用.
* 順序: ``patient_a_id < patient_b_id`` を呼び出し側で保証する (DB CHECK で強制).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

PairMode = Literal["blocked", "preferred", "required"]


class PatientSameAddressLinkBase(BaseModel):
    """共通フィールド (pair_mode + note)."""

    model_config = ConfigDict(extra="forbid")

    pair_mode: PairMode
    note: str | None = None


class PatientSameAddressLinkCreate(PatientSameAddressLinkBase):
    """新規作成リクエスト body.

    呼び出し側で patient_a_id / patient_b_id の順序を気にしなくて済むよう、
    ``_ensure_order`` で UUID 値の小さい方を a に揃える (DB CHECK 制約と整合).
    同一 ID 同士の link はそもそも意味が無いので ValueError を投げる.
    """

    patient_a_id: UUID
    patient_b_id: UUID

    @model_validator(mode="after")
    def _ensure_order(self) -> PatientSameAddressLinkCreate:
        if self.patient_a_id == self.patient_b_id:
            raise ValueError("patient_a_id と patient_b_id は同じにできません")
        if self.patient_a_id > self.patient_b_id:
            # tuple swap で a < b に揃える (DB CHECK 制約 ck_same_address_links_ordered).
            self.patient_a_id, self.patient_b_id = (
                self.patient_b_id,
                self.patient_a_id,
            )
        return self


class PatientSameAddressLinkRead(PatientSameAddressLinkBase):
    """GET レスポンス."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    patient_a_id: UUID
    patient_b_id: UUID
    decided_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Phase G-21 T2: 同住所候補取得 (GET /patients/{id}/same-address-candidates)
# ---------------------------------------------------------------------------


class SameAddressCandidate(BaseModel):
    """同住所候補 1 件 (= ``_address_bucket`` & address 文字列が一致する他患者).

    pair_mode は :class:`PatientSameAddressLink` に紐付け行があればその値,
    無ければ ``"preferred"`` (= デフォルト) を返す.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    patient_id: UUID
    patient_code: str | None = None
    patient_name: str
    address: str | None = None
    pair_mode: PairMode = "preferred"
    decided_by_user_id: UUID | None = None
    note: str | None = None
