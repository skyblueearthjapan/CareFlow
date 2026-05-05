"""HTTP audit-log middleware (Wave 4-F).

Captures every state-changing request (POST/PUT/PATCH/DELETE) under the API
prefix and asynchronously persists an `audit_logs` row carrying:

  - actor_user_id / role  (decoded from the bearer token if present)
  - method, path, status_code, latency_ms
  - ip_address, user_agent
  - PII-redacted request body (passwords, tokens, names, addresses, phones)

Design notes
------------
- **Non-blocking**: the response is returned to the client *before* the audit
  row is committed. We launch the DB write as an `asyncio.Task` and swallow
  any exception so the audit pipeline can never break the API. Latency
  overhead on the request hot-path is the cost of building the redacted body
  dict and one `time.perf_counter()` (~ <1ms p99 in practice).
- **GET / HEAD / OPTIONS skipped**: read-side traffic dwarfs writes and
  carries no PHI in the request body. If you need read-side audits later,
  flip `_AUDITED_METHODS` to include them.
- **Token decoding inlined**: we don't reuse the FastAPI dependency because
  middleware runs before route resolution. The decode is best-effort; failure
  → actor recorded as NULL.
- **Body capture**: ASGI body is consumed once and re-injected via a custom
  receive shim so downstream handlers see the same bytes (the standard
  Starlette pattern for body-mutating middleware).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy.exc import ProgrammingError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.security import JWTError, decode_token
from app.db.session import get_session_factory
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


_AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Body keys whose VALUES must be fully scrubbed regardless of nesting.
_REDACT_FULL = {
    "password",
    "new_password",
    "old_password",
    "current_password",
    "temp_password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "client_secret",
}

# Body keys whose values are personally identifying — we keep a tiny prefix so
# auditors can correlate a record without leaking the whole field.
_REDACT_PARTIAL_NAMES = {"name", "kana", "patient_name", "staff_name", "full_name"}
_REDACT_PARTIAL_ADDR = {"address", "home_address", "shipping_address", "billing_address"}
_REDACT_PARTIAL_PHONE = {"phone", "tel", "mobile", "phone_number", "tel_number"}
_REDACT_EMAIL = {"email", "user_email", "contact_email"}

# Hard cap on body size we serialize (defense vs runaway uploads).
_MAX_BODY_BYTES = 16 * 1024
# Hard cap on the request body cell we persist (DB safety + cost).
_MAX_PERSIST_BYTES = 8 * 1024

_REDACTED = "[REDACTED]"


def _mask_name(value: str) -> str:
    """Keep the first character, blur the rest with full-width circles.

    Example: 「青栁 あかり」 → 「青◯ あ◯◯」.
    """
    if not value:
        return value
    parts = re.split(r"(\s+)", value)
    out: list[str] = []
    for part in parts:
        if part.isspace() or not part:
            out.append(part)
            continue
        first = part[0]
        rest = "◯" * max(1, len(part) - 1)
        out.append(first + rest)
    return "".join(out)


def _mask_address(value: str) -> str:
    """Keep the first 4 chars (prefecture-ish), mask the remainder."""
    if not value:
        return value
    if len(value) <= 4:
        return value[0] + "◯" * (len(value) - 1)
    return value[:4] + "◯" * (len(value) - 4)


def _mask_phone(value: str) -> str:
    """Keep first 3 / last 2 digits."""
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 5:
        return _REDACTED
    return f"{digits[:3]}-****-{digits[-2:]}"


def _mask_email(value: str) -> str:
    """Drop the local part entirely, keep the domain (`***@example.com`)."""
    if "@" not in value:
        return _REDACTED
    domain = value.rsplit("@", 1)[-1]
    return f"***@{domain}"


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively walk a JSON-ish payload, scrubbing sensitive fields.

    Public so tests can pin behaviour without going through the middleware.
    """
    if _depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key_lower = str(k).lower()
            if key_lower in _REDACT_FULL:
                out[k] = _REDACTED
            elif key_lower in _REDACT_EMAIL and isinstance(v, str):
                out[k] = _mask_email(v)
            elif key_lower in _REDACT_PARTIAL_NAMES and isinstance(v, str):
                out[k] = _mask_name(v)
            elif key_lower in _REDACT_PARTIAL_ADDR and isinstance(v, str):
                out[k] = _mask_address(v)
            elif key_lower in _REDACT_PARTIAL_PHONE and isinstance(v, str):
                out[k] = _mask_phone(v)
            else:
                out[k] = redact(v, _depth=_depth + 1)
        return out
    if isinstance(value, list):
        return [redact(item, _depth=_depth + 1) for item in value]
    return value


def _decode_actor(authorization: str | None) -> tuple[UUID | None, str | None]:
    """Best-effort decode of the bearer JWT → (user_id, role)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None, None
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token)
    except JWTError:
        return None, None
    sub = claims.get("sub")
    role = claims.get("role")
    try:
        user_id = UUID(sub) if sub else None
    except (TypeError, ValueError):
        user_id = None
    return user_id, role if isinstance(role, str) else None


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # First hop wins.
        return fwd.split(",", 1)[0].strip()[:64]
    if request.client:
        return request.client.host[:64]
    return None


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


async def _persist_async(payload: dict[str, Any]) -> None:
    """Insert one AuditLog row in a fresh session.

    Wrapped in a broad try/except so a misconfigured DB never crashes the
    server's request loop — we log the failure and move on.

    Schema-mismatch defence
    -----------------------
    A production incident (Wave 4-G) showed that an out-of-date
    ``audit_logs`` table (missing newer columns) raised
    :class:`sqlalchemy.exc.ProgrammingError` on every mutation, and the
    background task crash leaked back into Starlette's exception group via
    the asyncio task callback. We now catch ``ProgrammingError`` first with
    an *operator-targeted* WARN message so on-call sees the fix
    (`alembic upgrade head`) without grepping for stack frames; the broad
    ``except Exception`` below remains as a last-resort safety net so the
    fire-and-forget contract (response already sent) is never violated.
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            session.add(AuditLog(**payload))
            await session.commit()
    except ProgrammingError:
        # Most commonly `UndefinedColumnError` — schema drift between code
        # and DB. Surface clearly so ops can run the migration.
        logger.exception(
            "audit-log middleware: DB schema mismatch — "
            "alembic upgrade head が必要 (audit row dropped, response unaffected)"
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("audit-log middleware: persist failed (swallowed)")


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Persist an `audit_logs` row for every mutation request.

    Args:
        path_prefix: only audit URLs that start with this prefix (e.g. the
            `/api/v1` API mount). Static / health / docs routes outside the
            mount are silently skipped.
        audited_methods: HTTP verbs to audit. Defaults to POST/PUT/PATCH/DELETE.
    """

    def __init__(
        self,
        app,
        *,
        path_prefix: str = "/api/v1",
        audited_methods: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._prefix = path_prefix
        self._methods = {m.upper() for m in (audited_methods or _AUDITED_METHODS)}

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path

        # Cheap reject: anything outside the audited surface bypasses the
        # body-buffering cost entirely.
        should_audit = method in self._methods and path.startswith(self._prefix)
        if not should_audit:
            return await call_next(request)

        # ---- buffer body so handlers can re-read it ----
        body_bytes = b""
        try:
            body_bytes = await request.body()
        except Exception:  # pragma: no cover - defensive
            body_bytes = b""

        async def _receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        # Re-inject the buffered body into the ASGI scope.
        request._receive = _receive  # type: ignore[attr-defined]

        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            # Persist a 500 entry for visibility, then re-raise.
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self._schedule_persist(
                request=request,
                method=method,
                path=path,
                body_bytes=body_bytes,
                status_code=500,
                latency_ms=elapsed_ms,
            )
            raise

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._schedule_persist(
            request=request,
            method=method,
            path=path,
            body_bytes=body_bytes,
            status_code=response.status_code,
            latency_ms=elapsed_ms,
        )
        return response

    # ------------------------------------------------------------------
    def _schedule_persist(
        self,
        *,
        request: Request,
        method: str,
        path: str,
        body_bytes: bytes,
        status_code: int,
        latency_ms: int,
    ) -> None:
        actor_id, role = _decode_actor(request.headers.get("authorization"))
        body_redacted: dict[str, Any] | None = None
        if body_bytes:
            try:
                raw = body_bytes[:_MAX_BODY_BYTES]
                parsed = json.loads(raw.decode("utf-8", errors="replace"))
                redacted = redact(parsed)
                serialized = json.dumps(redacted, ensure_ascii=False)
                if len(serialized.encode("utf-8")) > _MAX_PERSIST_BYTES:
                    body_redacted = {"_truncated": True}
                else:
                    body_redacted = (
                        redacted if isinstance(redacted, dict) else {"_value": redacted}
                    )
            except (ValueError, UnicodeDecodeError):
                body_redacted = {"_unparseable": True}

        payload = {
            "actor_user_id": actor_id,
            "role": role,
            "action": method,
            "target_table": "http",
            "target_id": _truncate(path, 64) or "",
            "method": method,
            "path": _truncate(path, 255),
            "status_code": status_code,
            "latency_ms": latency_ms,
            "ip_address": _client_ip(request),
            "user_agent": _truncate(request.headers.get("user-agent"), 255),
            "request_body": body_redacted,
        }

        # Fire-and-forget. We catch the task creation failure too so a
        # missing event-loop in tests cannot abort the response.
        try:
            asyncio.get_running_loop().create_task(_persist_async(payload))
        except RuntimeError:  # pragma: no cover - defensive
            logger.warning("audit-log middleware: no running loop, skipping persist")


__all__ = ["AuditLogMiddleware", "redact"]
