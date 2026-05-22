"""PatientSameAddressLink ORM model (Phase G-21).

同住所患者の紐付け 3 モードを表す.

| pair_mode | 意味 |
|-----------|------|
| blocked   | 同時訪問を禁止する (= 拠点/スタッフ重複避ける) |
| preferred | 同時訪問を優先する (= デフォルト, DB 行は省略可) |
| required  | 必ず同時訪問する |

* デフォルト ``preferred`` は **アプリ層で扱う**: 行が無ければ preferred 扱い.
  ``blocked`` と ``required`` のみ DB 記録する運用で行数を抑える.
* 複合主キー (patient_a_id, patient_b_id) + ``patient_a_id < patient_b_id``
  の CHECK 制約で重複行を防ぐ (UUID の文字列順比較).

## 削除挙動 (重要)

* DB FK は ``ondelete="CASCADE"`` だが、これは **patients の物理 DELETE のみ**
  で発火する.
* CareFlow の患者削除は通常 ``patients.deleted_at`` の更新による soft delete
  であり、この場合 ``patient_same_address_links`` は **CASCADE されない**.
* 紐付け解除はアプリ層で明示的に link 行を DELETE する必要がある
  (例: PatientService.soft_delete 内で link 行を物理削除する等).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PairMode = Literal["blocked", "preferred", "required"]

# PG の ENUM 型 ``same_address_pair_mode`` (migration 0037 で作成済) と一致させる.
# create_type=False で alembic migration 側に作成を委ね、ORM での暗黙 CREATE TYPE を防ぐ.
# native_enum=True で SQLite では CHECK 制約に fall back する.
_PAIR_MODE_SA_ENUM = SAEnum(
    "blocked",
    "preferred",
    "required",
    name="same_address_pair_mode",
    create_type=False,
    native_enum=True,
    validate_strings=True,
)


class PatientSameAddressLink(Base):
    """同住所患者紐付け (patient_same_address_links テーブル)."""

    __tablename__ = "patient_same_address_links"

    patient_a_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    patient_b_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # PG では ENUM ``same_address_pair_mode`` (migration 0037 が作成).
    # SQLite では native_enum=True が VARCHAR + CHECK 制約に fall back する.
    # Python 型は Literal["blocked","preferred","required"] で値域を明示.
    pair_mode: Mapped[PairMode] = mapped_column(
        _PAIR_MODE_SA_ENUM,
        nullable=False,
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "patient_a_id < patient_b_id",
            name="ck_same_address_links_ordered",
        ),
        Index("ix_pl_partner_a", "patient_a_id"),
        Index("ix_pl_partner_b", "patient_b_id"),
    )
