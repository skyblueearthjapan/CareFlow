"""Pydantic schemas for patient_ng_staff (NG スタッフ).

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §4-1.

* 行が存在する = NG (「NG のみ行を作る」方式). ``patient_same_address_link`` の
  schema 流儀を踏襲する.
* ``staff_name`` は ORM に無い派生項目 (staff テーブルを JOIN して endpoint 側で
  組み立てる) のため ``from_attributes`` ではなく明示構築する.
* ``staff_is_deleted`` は「退職済みスタッフの既存 NG 行」を表示側で「(退職)」と
  注記するためのフラグ. 退職しても行は残す (設計書 §4-1 バリデーション).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PatientNgStaffRead(BaseModel):
    """GET レスポンス 1 件 (= NG スタッフ 1 名)."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    staff_name: str
    # 退職 (staff.deleted_at IS NOT NULL) 済みでも行は残す. 表示側で「(退職)」注記.
    staff_is_deleted: bool = False
    note: str | None = None
    decided_by_user_id: UUID | None = None
    created_at: datetime


class PatientNgStaffUpsert(BaseModel):
    """PUT リクエスト body (理由メモのみ).

    patient_id / staff_id は path param で受けるため body には含めない.
    ``note`` の長さ制限は同住所リンク (``PatientSameAddressLinkBase.note``) に
    合わせて設けない (DB 側も Text).
    """

    model_config = ConfigDict(extra="forbid")

    note: str | None = None
