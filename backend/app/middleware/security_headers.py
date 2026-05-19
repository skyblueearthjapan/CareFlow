"""Security response-header middleware (Wave 4-G).

Adds a baseline set of hardening headers to every HTTP response. Values are
tuned for the CareLink production deployment, which sits behind Cloudflare
Tunnel (TLS terminated upstream) and serves a single first-party SPA that
talks to Google Maps and the Gemini API.

Header rationale
----------------
- ``X-Content-Type-Options: nosniff`` — disables MIME sniffing (defence
  against polyglot uploads served from /api/v1/exports/*).
- ``X-Frame-Options: DENY`` — we never embed CareLink in an iframe; the SPA
  is the only consumer. Cloudflare Tunnel also blocks framing by policy.
- ``Referrer-Policy: strict-origin-when-cross-origin`` — keeps internal
  paths private when the user clicks an external link.
- ``Strict-Transport-Security`` — Cloudflare already injects HSTS for the
  edge, but we set it for defense-in-depth in case the SPA is ever served
  off-edge during incident response.
- ``Content-Security-Policy`` — locks script/style/img/connect origins to
  ``self`` plus the two third-party APIs we actually call. ``unsafe-inline``
  is required because the React build emits inline boot styles and the
  Vite dev hydration script. Adjust when the bundle migrates to nonces.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_DEFAULT_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self' https://generativelanguage.googleapis.com "
    "https://maps.googleapis.com"
)


_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": _DEFAULT_CSP,
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject the project's standard security headers on every response.

    Existing headers from upstream handlers are *not* overwritten — this
    keeps per-route CSP customisation (e.g. /docs needing swagger CDN
    scripts) working when route handlers set their own values first.
    """

    def __init__(self, app, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(app)
        self._headers = dict(headers or _HEADERS)

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for name, value in self._headers.items():
            # Preserve any header an inner handler explicitly set (e.g. a
            # tighter per-route CSP) so we never silently downgrade.
            if name not in response.headers:
                response.headers[name] = value
        return response


__all__ = ["SecurityHeadersMiddleware"]
