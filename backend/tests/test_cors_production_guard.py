"""Tests for CORS production-startup guard (Wave 4-G).

The guard lives in :func:`app.main.create_app` and refuses to start when
``APP_ENV=production`` is paired with either an empty CORS_ORIGINS list or a
``"*"`` wildcard. We exercise it by re-setting the environment, clearing the
cached settings, and re-invoking ``create_app`` directly.

Note on isolation
-----------------
``app.main`` calls ``create_app()`` at module-load (so uvicorn picks up
``app.main:app`` directly). Reloading the module under prod-without-CORS
would crash at import time, so we instead **import once then call
``create_app`` directly** with the env mutated and the settings cache
cleared. This keeps the module-level ``app`` instance alive (built from
the test conftest's safe defaults) and lets us exercise the guard purely
through the function entry point.
"""

from __future__ import annotations

import pytest

from app.core import config as config_module
from app.main import create_app


@pytest.fixture(autouse=True)
def _restore_settings_cache():
    """Always bust the cache after each test so the next case sees fresh env."""
    yield
    config_module.get_settings.cache_clear()


def _force_settings(monkeypatch, **env: str) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config_module.get_settings.cache_clear()


def test_create_app_rejects_wildcard_cors_in_production(monkeypatch) -> None:
    _force_settings(
        monkeypatch,
        APP_ENV="production",
        CORS_ORIGINS="*",
        JWT_SECRET="prod-secret-which-is-32-chars-long-x",
    )
    with pytest.raises(ValueError, match="CORS wildcard not allowed"):
        create_app()


def test_create_app_rejects_empty_cors_in_production(monkeypatch) -> None:
    _force_settings(
        monkeypatch,
        APP_ENV="production",
        CORS_ORIGINS="",
        JWT_SECRET="prod-secret-which-is-32-chars-long-x",
    )
    with pytest.raises(ValueError, match="CORS_ORIGINS must be set"):
        create_app()


def test_create_app_accepts_wildcard_in_dev(monkeypatch) -> None:
    """Wildcard is fine in development — the guard only fires for prod."""
    _force_settings(
        monkeypatch,
        APP_ENV="development",
        CORS_ORIGINS="*",
        JWT_SECRET="dev-secret",
    )
    app = create_app()
    assert app is not None


def test_create_app_accepts_explicit_origin_in_production(monkeypatch) -> None:
    _force_settings(
        monkeypatch,
        APP_ENV="production",
        CORS_ORIGINS="https://carelink.example.com",
        JWT_SECRET="prod-secret-which-is-32-chars-long-x",
    )
    app = create_app()
    assert app is not None
