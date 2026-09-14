"""slowapi limiter shared across the app.

T17 mandates 5 login attempts / 15 minutes per IP. The slowapi limiter sits in
front of the per-account lockout in `auth.login` so brute-force attempts get
shed early (mitigates the lockout-as-DoS / email-enumeration combo flagged by
Codex).

2026-09-14 incident: every browser login is proxied by the Next.js server
(NextAuth `authorize` → backend `/auth/login`), so the backend only ever saw the
frontend container's IP. All staff therefore shared ONE 5/15min bucket and the
6th person to log in at the monthly meeting was rejected with 429 (which the
login form rendered as "wrong password"). Fixes:

* `client_ip` uses `CF-Connecting-IP`. Cloudflare overwrites that header for
  every request that arrives through the tunnel (`/api/v1/*` is routed
  straight to this backend), so it cannot be forged from the internet; the
  Next.js server copies the value it received onto its server-to-server login
  call. `X-Forwarded-For` is only consulted as the *rightmost* hop (the one
  appended by the trusted proxy), never the client-supplied leftmost one.
* `login_key` additionally scopes the login limit by the login identifier the
  frontend forwards in `X-Login-Identifier`, so colleagues behind the same
  office NAT do not consume each other's attempts. `auth.login` rejects a
  header that does not match the body identifier, so the header can only ever
  narrow a bucket, never widen it. A looser per-IP ceiling is layered on top.

Limits live in the in-process memory store: they are exact for the single
uvicorn worker we run and reset on every deploy.
"""

from __future__ import annotations

import hashlib

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

LOGIN_IDENTIFIER_HEADER = "x-login-identifier"

# /auth/login: brute force on one account from one place.
LOGIN_LIMIT_PER_IDENTIFIER = "5/15minutes"
# /auth/login: flood / lockout-DoS ceiling per client IP (an office NAT with
# ~8 staff logging in at once needs comfortably more than 5).
LOGIN_LIMIT_PER_IP = "30/15minutes"


def client_ip(request: Request) -> str:
    """Client IP as seen by the trusted edge.

    Order: `CF-Connecting-IP` (set/overwritten by Cloudflare, or copied by the
    Next.js server for its proxied login call) → rightmost `X-Forwarded-For`
    hop → socket peer.
    """
    cf = request.headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if hops:
        return hops[-1]
    return get_remote_address(request)


def normalized_login_identifier(request: Request) -> str | None:
    """`X-Login-Identifier` normalised like the body identifier (strip+lower)."""
    raw = request.headers.get(LOGIN_IDENTIFIER_HEADER)
    if raw is None:
        return None
    return raw.strip().lower()


def login_key(request: Request) -> str:
    """Rate-limit key for `/auth/login`: client IP + hashed identifier.

    The identifier comes from `X-Login-Identifier` (the request body is not
    available to slowapi's key function); it is hashed to keep key cardinality
    bounded. Requests without the header fall back to the plain per-IP key.
    """
    ident = normalized_login_identifier(request)
    ip = client_ip(request)
    if not ident:
        return ip
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:16]
    return f"{ip}|{digest}"


# Default key = trusted client IP (see `client_ip`). Also used by
# /auth/change-password, /allocate and /diff limits.
limiter = Limiter(key_func=client_ip)
