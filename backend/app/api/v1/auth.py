"""Authentication endpoints: /login, /me, /refresh, /logout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbDep
from app.core.rate_limit import limiter
from app.core.security import (
    JWTError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    RefreshRequest,
    TokenPair,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Account-level lockout policy: matches T17 (login 5/15min lock) but applied
# per-user instead of per-IP for now. slowapi rate limiting can be layered on
# top later without changing this contract.
_MAX_FAILED_LOGINS = 5
_LOCK_DURATION = timedelta(minutes=15)


def _build_token_pair(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id),
        refresh_token=create_refresh_token(subject=user.id, role=user.role, staff_id=user.staff_id),
    )


@router.post("/login", response_model=LoginResponse, summary="Login with email + password")
@limiter.limit("5/15minutes")
async def login(request: Request, payload: LoginRequest, db: DbDep) -> LoginResponse:
    # `request` is required by slowapi to extract the client IP for the
    # per-IP 5/15min ceiling. The 6th attempt within the window returns 429
    # before we ever touch the DB, which keeps both account-enumeration and
    # lockout-driven DoS in check (Codex G2 followup).
    # deleted_at IS NULL: email is now a partial-unique (allows reuse after soft-delete),
    # so we must exclude soft-deleted rows to avoid authenticating a deleted account.
    user = await db.scalar(
        select(User).where(User.email == str(payload.email), User.deleted_at.is_(None))
    )
    now = datetime.now(tz=UTC)

    # Generic 401 for both unknown email and wrong password to avoid enumeration.
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if user.locked_until and user.locked_until > now:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account temporarily locked. Try again later.",
        )

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= _MAX_FAILED_LOGINS:
            user.locked_until = now + _LOCK_DURATION
            user.failed_login_count = 0
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Successful login → reset counters.
    user.failed_login_count = 0
    user.locked_until = None
    await db.commit()
    await db.refresh(user)

    return LoginResponse(user=UserOut.model_validate(user), tokens=_build_token_pair(user))


@router.get("/me", response_model=UserOut, summary="Current authenticated user")
async def me(current: CurrentUser) -> UserOut:
    return UserOut.model_validate(current)


@router.post(
    "/refresh", response_model=TokenPair, summary="Issue new access+refresh from refresh token"
)
async def refresh(payload: RefreshRequest, db: DbDep) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc

    if claims.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type",
        )

    sub = claims.get("sub")
    try:
        user_id = UUID(sub)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        ) from exc

    # Exclude soft-deleted accounts from refresh — a deleted user must not receive
    # new tokens even if they hold a valid refresh token.
    user = await db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return _build_token_pair(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Client-side logout helper")
async def logout(_current: CurrentUser) -> None:
    """No-op server side (stateless JWT). The client should drop tokens."""
    return None


# ---------------------------------------------------------------------------
# Wave 40 — self-service password change
# ---------------------------------------------------------------------------


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change own password (rate-limited 5/15min per IP)",
)
@limiter.limit("5/15minutes")
async def change_password(
    request: Request,
    payload: PasswordChangeRequest,
    db: DbDep,
    current: CurrentUser,
) -> None:
    """Authenticated self-service password change.

    Behaviour:
        * 401 if `current_password` is incorrect — same generic message used by
          /login to avoid revealing detail.
        * 422 (via Pydantic) if `new_password` is too short or fails the
          letter+digit complexity rule.
        * 422 if the new password equals the current — encourages an actual
          rotation rather than a no-op.
        * On success, updates `password_hash`, clears `must_change_password`,
          and returns 204.

    Rate limit: 5 attempts per 15 min per IP via slowapi (independent counter
    from `/login`). The audit log middleware records the call automatically;
    `current_password` / `new_password` are scrubbed by the redactor (their
    keys are in `_REDACT_FULL`).

    Note: we intentionally do NOT touch `failed_login_count` / `locked_until`
    on a wrong `current_password` — a typo on the change form should not lock
    the user out of /login. The slowapi 5/15min cap is the sole brute-force
    defense at this surface for now.
    """
    # `request` is required positionally by slowapi to inspect the client IP.
    _ = request

    if not verify_password(payload.current_password, current.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must differ from the current one",
        )

    current.password_hash = hash_password(payload.new_password)
    current.must_change_password = False
    await db.commit()
    return None
