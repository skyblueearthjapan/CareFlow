"""PatientNgStaff ORM model (NG スタッフ / migration 0070).

患者ごとに「このスタッフは割り当てない」恒久ルールを表す.
正典設計書: ``docs/plans/patient-ng-staff-design.md`` §3.

* 1 患者に複数スタッフを設定可. 行が存在する = NG (「NG のみ行を作る」方式).
  ``patient_same_address_links`` (mig 0037) の型を踏襲する.
* 制約の性格は性別制限 (``Patient.sex_restriction``) と完全に同格:
  エンジン (Layer3) ではハード制約、人手操作は確認付きで通す.
* 一時的 NG (週限定) は不要と PO 確定 (2026-08-11 決定3) のため 1 テーブル構成.
* ``note`` は理由メモ (任意・内部管理用). ``decided_by_user_id`` は設定者 (監査用).

## インデックス

* 複合主キー ``(patient_id, staff_id)`` が patient → staff 方向を充足する.
* ``ix_patient_ng_staff_staff`` (staff_id) は **逆引き用**:
  エンジンの一括ロード / スタッフ詳細の「この方を NG 指定している患者」サマリで使う.

## 削除挙動 (重要)

* DB FK は ``ondelete="CASCADE"`` だが、これは patients / staff の **物理 DELETE
  のみ** で発火する.
* CareFlow の患者削除・スタッフ削除は通常 ``deleted_at`` の更新による soft delete
  であり、この場合 ``patient_ng_staff`` は **CASCADE されない**.
* したがって患者削除 API (``api/v1/patients.py`` の ``delete_patient``) と
  スタッフ削除 API (``api/v1/staff.py`` の ``delete_staff``) の **両方**で、
  アプリ層から NG 行を明示的に DELETE する必要がある
  (= soft delete 後に同 ID を再利用/復活させた時に古い NG が蘇らないことを保証).
* ``patient_same_address_links`` と同じ既知罠. 新しい削除経路を足す時は必ず追随すること.

## ORM 方針

* ``Patient`` / ``Staff`` に relationship を張らない. 利用側が明示クエリで
  一括ロードする (N+1 禁止. 設計書 §3 末尾).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PatientNgStaff(Base, TimestampMixin):
    """NG スタッフ (patient_ng_staff テーブル)."""

    __tablename__ = "patient_ng_staff"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # 理由メモ (任意・現場向け文言ではなく内部管理用).
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 設定者 (監査用). ユーザー削除時は SET NULL で行自体は残す.
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # 逆引き (staff_id → patients) 用. エンジンの一括ロードで使用.
        Index("ix_patient_ng_staff_staff", "staff_id"),
    )
