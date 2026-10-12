import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk.auth import TokenManager  # noqa: E402
from rk.config import load_config, load_credentials  # noqa: E402
from rk.opsbase import ApiError  # noqa: E402


def _jwt(exp: float) -> str:
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b({'alg': 'HS256'})}.{b({'exp': exp})}.sig"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for k in ("RK_ENV", "RK_MODE", "RK_SSH", "RK_API_PORT", "RK_IDENTIFIER", "RK_PASSWORD", "RK_PG_CONTAINER"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses  # path -> callable(body) or value
        self.calls = []

    def request(self, method, path, body=None, params=None, token=None):
        self.calls.append((method, path))
        r = self.responses[path]
        return r(body) if callable(r) else r


def test_config_defaults_and_mode(home, monkeypatch):
    cfg = load_config()
    assert cfg.mode == "pc" and cfg.ssh == "root@72.60.211.213" and cfg.api_port == 18001
    assert cfg.pg_container == "carelink-postgres" and cfg.pg_user == "carelink" and cfg.pg_db == "carelink"
    assert cfg.token_path == home / ".rakusuke" / "token.json"
    monkeypatch.setenv("RK_MODE", "vps")
    assert load_config().mode == "vps"
    monkeypatch.setenv("RK_MODE", "cloud")
    with pytest.raises(RkError):
        load_config()


def test_config_rejects_injection(home, monkeypatch):
    monkeypatch.setenv("RK_PG_CONTAINER", "x; rm -rf /")
    with pytest.raises(RkError):
        load_config()


def test_credentials_env_then_file(home, monkeypatch):
    cfg = load_config()
    assert load_credentials(cfg) is None
    (home / ".rakusuke").mkdir()
    (home / ".rakusuke" / "credentials.json").write_text(json.dumps({"identifier": "agent", "password": "pw"}))
    assert load_credentials(cfg) == ("agent", "pw")
    monkeypatch.setenv("RK_IDENTIFIER", "envuser")
    monkeypatch.setenv("RK_PASSWORD", "envpw")
    assert load_credentials(cfg) == ("envuser", "envpw")


def test_valid_access_token_is_reused(home):
    cfg = load_config()
    now = 1_000_000.0
    tm = TokenManager(cfg, FakeHttp({}), now=lambda: now)
    tm.save({"access_token": _jwt(now + 3000), "refresh_token": _jwt(now + 86400)})
    assert tm.access_token() == _jwt(now + 3000)
    assert tm.http.calls == []


def test_refresh_used_before_login(home, monkeypatch):
    monkeypatch.setenv("RK_IDENTIFIER", "a")
    monkeypatch.setenv("RK_PASSWORD", "b")
    cfg = load_config()
    now = 1_000_000.0
    new_access = _jwt(now + 3600)
    http = FakeHttp({"/auth/refresh": {"access_token": new_access, "refresh_token": _jwt(now + 99999)}})
    tm = TokenManager(cfg, http, now=lambda: now)
    tm.save({"access_token": _jwt(now - 10), "refresh_token": _jwt(now + 86400)})
    http.calls.clear()
    assert tm.access_token() == new_access
    assert http.calls == [("POST", "/auth/refresh")]


def test_login_once_when_no_refresh_and_secrets_not_printed(home, monkeypatch, capsys):
    monkeypatch.setenv("RK_IDENTIFIER", "agent")
    monkeypatch.setenv("RK_PASSWORD", "s3cret")
    cfg = load_config()
    now = 1_000_000.0
    acc = _jwt(now + 3600)
    http = FakeHttp({"/auth/login": {"user": {}, "tokens": {"access_token": acc, "refresh_token": _jwt(now + 9e5)}}})
    tm = TokenManager(cfg, http, now=lambda: now)
    assert tm.access_token() == acc
    assert http.calls == [("POST", "/auth/login")]
    saved = json.loads(cfg.token_path.read_text())
    assert saved["access_expires_at"] == now + 3600
    assert "s3cret" not in cfg.token_path.read_text()
    out = capsys.readouterr()
    assert "s3cret" not in out.out + out.err and acc not in out.out + out.err


def test_login_failure_not_retried(home, monkeypatch):
    monkeypatch.setenv("RK_IDENTIFIER", "agent")
    monkeypatch.setenv("RK_PASSWORD", "bad")
    cfg = load_config()

    def fail(body):
        raise ApiError(401, {"detail": "invalid"})
    http = FakeHttp({"/auth/login": fail})
    tm = TokenManager(cfg, http, now=lambda: 1_000_000.0)
    with pytest.raises(RkError):
        tm.access_token()
    assert http.calls == [("POST", "/auth/login")]


def test_login_rate_guard(home, monkeypatch):
    monkeypatch.setenv("RK_IDENTIFIER", "agent")
    monkeypatch.setenv("RK_PASSWORD", "bad")
    cfg = load_config()

    def fail(body):
        raise ApiError(401, None)
    http = FakeHttp({"/auth/login": fail})
    tm = TokenManager(cfg, http, now=lambda: 1_000_000.0)
    for _ in range(3):
        with pytest.raises(RkError):
            tm.access_token()
    with pytest.raises(RkError, match="15 分"):
        tm.access_token()
    assert len(http.calls) == 3


def test_refresh_rejected_falls_back_to_single_login(home, monkeypatch):
    monkeypatch.setenv("RK_IDENTIFIER", "a")
    monkeypatch.setenv("RK_PASSWORD", "b")
    cfg = load_config()
    now = 1_000_000.0

    def rej(body):
        raise ApiError(401, None)
    acc = _jwt(now + 3600)
    http = FakeHttp({"/auth/refresh": rej, "/auth/login": {"tokens": {"access_token": acc, "refresh_token": "r"}}})
    tm = TokenManager(cfg, http, now=lambda: now)
    tm.save({"access_token": _jwt(now - 1), "refresh_token": _jwt(now + 9999)})
    assert tm.access_token() == acc
    assert [c[1] for c in http.calls] == ["/auth/refresh", "/auth/login"]


def test_no_credentials_message(home):
    tm = TokenManager(load_config(), FakeHttp({}), now=lambda: 1.0)
    with pytest.raises(RkError, match="資格情報"):
        tm.access_token()


def test_rk_env_staging_separates_files(monkeypatch, tmp_path):
    from rk.config import load_config
    monkeypatch.setenv("RK_ENV", "staging")
    for k in ("RK_API_PORT", "RK_PG_CONTAINER", "RK_REMOTE_DIR", "RK_RUNS_DIR"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.api_port == 18101 and cfg.pg_container == "stg-postgres"
    assert cfg.remote_dir == "/opt/carelink/agent-staging"
    assert cfg.token_path.name == "token-staging.json" and cfg.credentials_path.name == "credentials-staging.json"
    assert cfg.runs_dir.name == "staging"
    monkeypatch.setenv("RK_ENV", "prod")
    p = load_config()
    assert p.api_port == 18001 and p.token_path.name == "token.json" and p.runs_dir.name == "runs"
