"""rk week preview-clear / preview-generate は dry_run:true だけを送る（偽の API・本番に触らない）。"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
cli = pytest.importorskip("rk.cli")


class FakeApi:
    def __init__(self, calls, fail=None):
        self.calls, self.fail = calls, fail

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail:
            raise self.fail
        if path.endswith("/clear"):
            return {"to_delete": 12, "kaipoke_linked": 5, "kept_checked_in": 2, "snapshot_id": None}
        return {"to_add": 118}


def _patch(monkeypatch, calls, fail=None):
    import rk.session as session

    @contextlib.contextmanager
    def fake_open(cfg):
        yield FakeApi(calls, fail)

    monkeypatch.setattr(session, "open_api", fake_open)


def test_preview_clear_sends_dry_run_only(monkeypatch, capsys):
    calls = []
    _patch(monkeypatch, calls)
    assert cli.main(["week", "preview-clear", "--year", "2026", "--week", "43", "--office", "o-1"]) == 0
    assert calls == [("POST", "/schedule/v2/weeks/2026/43/clear", {"dry_run": True, "office_id": "o-1"})]
    out = capsys.readouterr().out
    assert "12 件" in out and json.loads(out[out.index("{"):])["kaipoke_linked"] == 5


def test_preview_generate_sends_dry_run_only(monkeypatch, capsys):
    calls = []
    _patch(monkeypatch, calls)
    assert cli.main(["week", "preview-generate", "--year", "2026", "--week", "43"]) == 0
    assert calls == [("POST", "/schedule/generate-week-only", {"iso_year": 2026, "iso_week": 43, "dry_run": True})]
    assert "118 件" in capsys.readouterr().out


def test_preview_generate_409_message(monkeypatch, capsys):
    from rk.opsbase import ApiError
    _patch(monkeypatch, [], fail=ApiError(409, {"detail": "予定あり"}))
    assert cli.main(["week", "preview-generate", "--year", "2026", "--week", "43"]) == 0
    out = capsys.readouterr().out
    assert "もう予定があります" in out and '"status": 409' in out
