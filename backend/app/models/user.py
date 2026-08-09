"""User account (authentication subject)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.staff import Staff


# アカウント権限 (PO 決定 2026-08-09「二軸分離」): 管理者(admin) / 一般(staff) の 2 値。
# これは **ログイン権限** であり、スタッフマスタの業務ロール (Staff.role:
# staff/manager = スケジュールエンジン専用) とは無関係。
USER_ROLES = ("admin", "staff")

# 旧 'manager' 権限 (2026-08-09 廃止) は admin の別名として恒久的に受理する。
# 既存行は migration 0069 で admin へ移行済みだが、旧 JWT クレームや取り込み
# データに残る値を 403 にしないための安全弁。
LEGACY_USER_ROLE_ALIASES = {"manager": "admin"}


def normalize_user_role(role: str | None) -> str | None:
    """旧権限名を現行の 2 値へ正規化する ('manager' → 'admin')。"""
    if role is None:
        return None
    return LEGACY_USER_ROLE_ALIASES.get(role, role)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # email is nullable so staff accounts (code-based login, P1b) can exist
    # without a mailbox. The partial UNIQUE index below enforces uniqueness only
    # over alive rows with a non-NULL email (multiple NULLs are allowed).
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="staff")

    staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )

    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Wave 4-F admin/users CRUD additions
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    staff: Mapped[Staff | None] = relationship("Staff", lazy="selectin")

    __table_args__ = (
        # DB-level guard: every user must have at least one login identifier.
        # Combined with the schema-layer check in admin.py, this gives multi-layer
        # defence. migration 0046 adds this constraint to the production table.
        CheckConstraint(
            "email IS NOT NULL OR username IS NOT NULL",
            name="ck_users_has_login_identifier",
        ),
        # Partial UNIQUE indexes over alive, non-NULL rows only (staff.code の
        # ix_staff_code_unique_alive と同型). migration 0046 が production 側を担保する。
        Index(
            "ix_users_email_unique_alive",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL AND deleted_at IS NULL"),
            sqlite_where=text("email IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "ix_users_username_unique_alive",
            "username",
            unique=True,
            postgresql_where=text("username IS NOT NULL AND deleted_at IS NULL"),
            sqlite_where=text("username IS NOT NULL AND deleted_at IS NULL"),
        ),
        # 1 スタッフ = 1 アカウントを DB レベルで保証。
        Index(
            "ix_users_staff_id_unique_alive",
            "staff_id",
            unique=True,
            postgresql_where=text("staff_id IS NOT NULL AND deleted_at IS NULL"),
            sqlite_where=text("staff_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    @property
    def staff_name(self) -> str | None:
        """Linked staff member's display name (None when unlinked).

        ``staff`` is ``lazy="selectin"`` so this is safe to read synchronously
        after the row is loaded — no extra lazy I/O is triggered.
        """
        return self.staff.name if self.staff else None

    def __repr__(self) -> str:  # pragma: no cover - debug repr
        return f"<User id={self.id} email={self.email or self.username} role={self.role}>"
