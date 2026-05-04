"""FastAPI application entrypoint.

Wires routers under `/api/v1`, configures CORS, and exposes lifespan hooks
for clean engine shutdown.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.session import dispose_engine


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Startup hook (no eager DB connect — readiness probe handles that).
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "CareLink Backend — auth, masters, visits, integrations forwarder.\n\n"
            "All endpoints are versioned under `/api/v1/*`."
        ),
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=f"{settings.api_v1_prefix}/redoc",
        lifespan=_lifespan,
    )

    # slowapi: register the limiter on app state and wire its 429 handler.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
