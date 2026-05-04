"""slowapi limiter shared across the app.

T17 mandates 5 login attempts / 15 minutes per IP. The slowapi limiter sits in
front of the per-account lockout in `auth.login` so brute-force attempts get
shed early (mitigates the lockout-as-DoS / email-enumeration combo flagged by
Codex).
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# Per-IP limiter. The default key function returns the request's client IP,
# which works behind a sane reverse proxy (Caddy/Nginx forwarding headers).
limiter = Limiter(key_func=get_remote_address)
