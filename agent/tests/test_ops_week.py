"""rk/ops/week.py（週を作る＝前の週をコピー）のテスト（偽の Api・本番に触らない）。"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk.ops import week as week_mod  # noqa: E402
from rk.opsbase import load_all  # noqa: E402
from rk.readonly_posts import is_readonly_post  # noqa: E402

REG = load_all()


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(week_mod, "_today", lambda: date(2026, 10, 12))


def make_op(**kw):
    o = {"op": "copy-week", "label": "週43 を週42 から作る", "source_iso_year": 2026, "source_iso_week": 42,
         "target_iso_year": 2026, "target_iso_week": 43}
    o.update(kw)
    return o


class FakeApi:
    def __init__(self, existing_total=0, source_visits=120, target_visits=0):
        self.existing_total = existing_total
        self.week_visits = {"2026-10-12": source_visits, "2026-10-19": target_visits}
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        assert path == "/schedule/v2/copy-week/sources"
        tgt = date.fromisoformat(params["target_week_start"])
        items = [{"week_start": k, "visits": v} for k, v in self.week_visits.items()
                 if date.fromisoformat(k) < tgt and v]
        return {"target_week_start": str(tgt), "items": items}

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/schedule/v2/copy-week/preview":
            return {"mode": "replace", "copy_count": 118, "fill_count": 0,
                    "existing": {"total": self.existing_total, "replace": self.existing_total},
                    "needs_manual_staff": [{"patient_id": "p"}]}
        if path == "/schedule/v2/copy-week":
            self.week_visits["2026-10-19"] = 118
            return {"created": 118, "filled": 0, "differs_from_preview": False, "mode": "replace"}
        raise AssertionError(path)


def test_registered_and_preview_is_readonly_post():
    assert REG.get("copy-week") is not None
    assert is_readonly_post("/schedule/v2/copy-week/preview")
    assert not is_readonly_post("/schedule/v2/copy-week")


def test_validate():
    impl = REG.get("copy-week")
    assert impl.validate(make_op()) == []
    assert any("8 週" in e for e in impl.validate(make_op(source_iso_week=30)))
    assert any("8 週" in e for e in impl.validate(make_op(source_iso_week=43)))
    assert any("今週以降" in e for e in impl.validate(make_op(source_iso_week=40, target_iso_week=41)))
    assert any("整数" in e for e in impl.validate(make_op(target_iso_week="43")))
    assert any("自動スタッフ割当" in e for e in impl.validate(make_op(assign_staff=True)))
    assert any("true / false" in e for e in impl.validate(make_op(allow_overwrite="yes")))


def test_apply_on_empty_week_uses_ui_api_without_auto_assign():
    impl = REG.get("copy-week")
    api = FakeApi()
    before = impl.snapshot(api, make_op())
    assert before["target_visits"] == 0
    res = impl.execute(api, make_op())
    post = [c for c in api.calls if c[1] == "/schedule/v2/copy-week"]
    assert len(post) == 1
    body = post[0][2]
    assert body["source_week_start"] == "2026-10-12" and body["target_week_start"] == "2026-10-19"
    assert body["assign_staff"] is False and body["fill_from_fixed"] is False and body["confirm"] is True
    assert body["expected_counts"] == {"copy_count": 118, "fill_count": 0, "replace_count": 0, "needs_manual_count": 1}
    assert impl.check_response(make_op(), res) == []
    assert impl.inverse(make_op(), before) is None
    assert impl.undo_note(make_op()) == "週を消すにはバックアップから"
    assert impl.verify(api, make_op()) == []


def test_refuses_overwrite_unless_allowed():
    impl = REG.get("copy-week")
    api = FakeApi(existing_total=40)
    with pytest.raises(RkError, match="空の週"):
        impl.snapshot(api, make_op())
    with pytest.raises(RkError, match="allow_overwrite"):
        impl.execute(api, make_op())
    assert not [c for c in api.calls if c[1] == "/schedule/v2/copy-week"]
    impl.execute(api, make_op(allow_overwrite=True))
    assert [c for c in api.calls if c[1] == "/schedule/v2/copy-week"]


def test_check_response_and_verify_problems():
    impl = REG.get("copy-week")
    assert impl.check_response(make_op(), {"created": 0, "filled": 0})
    assert impl.check_response(make_op(), {"created": 5, "differs_from_preview": True})
    assert "訪問がありません" in impl.verify(FakeApi(), make_op())[0]
    assert "大きく違います" in impl.verify(FakeApi(target_visits=60), make_op())[0]
    assert impl.verify(FakeApi(target_visits=115), make_op()) == []


def test_apply_plan_with_copy_week(tmp_path):
    from rk.applyrun import apply_plan
    from rk.config import Config
    cfg = Config(runs_dir=tmp_path / "runs", reports_dir=tmp_path / "rep")
    _rec = tmp_path / "runs"
    _rec.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt, timezone as _tz
    (_rec / "backups.jsonl").write_text(json.dumps({"label": "t", "path": "/x.sql.gz", "at_utc": _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + chr(10), encoding="utf-8")  # rk backup の記録
    plan = {"kind": "copy-week", "title": "週43 を作る", "target": {"iso_year": 2026, "iso_week": 43},
            "summary": ["週42 を写す"], "ops": [make_op()], "checks": [], "warnings": []}
    res = apply_plan(cfg, plan, FakeApi(), approved="今泉 10/12", backup="/x.sql.gz")
    assert res["ok"] and res["applied"] == 1
    assert res["not_undoable"] and res["not_undoable"][0]["op"] == "copy-week"
    res = apply_plan(cfg, plan, FakeApi(existing_total=3), approved="今泉 10/12", backup="/x.sql.gz")
    assert not res["ok"] and res["applied"] == 0 and "空の週" in res["error"]


# ---- clear-week / clear-week-undo / generate-week（青ピン廃止） ----
from rk.opsbase import ApiError  # noqa: E402


class ClearApi:
    def __init__(self, left=5, gen_409=False):
        self.left, self.gen_409, self.calls = left, gen_409, []

    def get(self, path, params=None):
        return {"items": [{"week_start": "2026-10-19", "visits": 3}]}

    def call(self, method, path, body=None):
        self.calls.append((path, dict(body or {})))
        if path.endswith("/clear"):
            if body["dry_run"]:
                return {"to_delete": self.left, "kaipoke_linked": 2, "kept_checked_in": 1, "snapshot_id": None}
            self.left = 0
            return {"to_delete": 5, "kaipoke_linked": 2, "kept_checked_in": 1, "snapshot_id": "snap-1"}
        if path == "/schedule/generate-week-only":
            if self.gen_409:
                raise ApiError(409, "not empty")
            if body["dry_run"]:
                return {"to_add": 100}
            return {"visits_created": 100, "unplaced_multi_staff_patients": []}
        raise AssertionError(path)


def test_clear_week_flow_and_inverse():
    impl = REG.get("clear-week")
    op = {"op": "clear-week", "iso_year": 2026, "iso_week": 43}
    assert impl.validate(op) == [] and impl.validate({"op": "clear-week"})
    api = ClearApi()
    before = impl.snapshot(api, op)
    assert before["dry_run"]["to_delete"] == 5 and api.calls[0][1]["dry_run"] is True
    impl.execute(api, op)
    inv = impl.inverse(op, before)
    assert inv == [{"op": "clear-week-undo", "iso_year": 2026, "iso_week": 43, "snapshot_id": "snap-1"}]
    assert impl.undoable and impl.verify(api, op) == []
    assert REG.get("clear-week-undo").request(inv[0]) == (
        "POST", "/schedule/v2/weeks/2026/43/clear/undo", {"snapshot_id": "snap-1"})
    assert REG.get("clear-week-undo").validate(inv[0]) == []


def test_generate_week_refuses_nonempty_and_is_not_undoable():
    impl = REG.get("generate-week")
    op = {"op": "generate-week", "iso_year": 2026, "iso_week": 43}
    assert impl.validate(op) == [] and impl.inverse(op, {}) is None
    assert "元に戻せません" in impl.undo_note(op)
    assert impl.snapshot(ClearApi(), op) == {"to_add": 100}
    with pytest.raises(RkError):
        impl.snapshot(ClearApi(gen_409=True), op)
    assert impl.check_response(op, {"visits_created": 0})


# ---- 期間で組む: clear-week・generate-week の date_from / date_to（agent-period-build-design §3-2） ----
def _ranged(name, **kw):
    o = {"op": name, "label": "範囲", "iso_year": 2026, "iso_week": 49, "date_from": "2026-12-01", "date_to": "2026-12-06"}
    o.update(kw)
    return o


@pytest.mark.parametrize("name", ["clear-week", "generate-week"])
def test_range_validation(name):
    impl = REG.get(name)
    assert impl.validate(_ranged(name)) == []
    assert impl.validate({"op": name, "iso_year": 2026, "iso_week": 49}) == []  # 範囲なし = 週全体
    assert any("組で" in e for e in impl.validate(_ranged(name, date_to=None)))
    assert any("組で" in e for e in impl.validate({"op": name, "iso_year": 2026, "iso_week": 49, "date_to": "2026-12-02"}))
    assert any("週の外" in e and "2026-11-29" in e for e in impl.validate(_ranged(name, date_from="2026-11-29")))
    assert any("週の外" in e for e in impl.validate(_ranged(name, date_to="2026-12-07")))
    assert any("より後" in e for e in impl.validate(_ranged(name, date_from="2026-12-04", date_to="2026-12-02")))
    assert any("YYYY-MM-DD" in e for e in impl.validate(_ranged(name, date_from="12/1")))
    assert any("YYYY-MM-DD" in e for e in impl.validate(_ranged(name, date_from="2026-12-32")))
    assert impl.validate(_ranged(name, date_from="2026-11-30", date_to="2026-11-30")) == []  # 1 日・月曜


def test_clear_week_range_in_every_request():
    impl = REG.get("clear-week")
    op = _ranged("clear-week")
    rng = {"date_from": "2026-12-01", "date_to": "2026-12-06"}
    assert impl.request(op) == ("POST", "/schedule/v2/weeks/2026/49/clear", {"dry_run": False, **rng})
    api = ClearApi()
    impl.snapshot(api, op)
    impl.execute(api, op)
    assert impl.verify(api, op) == []
    assert [b for _p, b in api.calls] == [{"dry_run": True, **rng}, {"dry_run": False, **rng}, {"dry_run": True, **rng}]
    # 戻し方は今までどおり（消した訪問の ID だけ書き戻す）
    assert impl.inverse(op, {}) == [{"op": "clear-week-undo", "iso_year": 2026, "iso_week": 49, "snapshot_id": "snap-1"}]
    left = ClearApi(left=2)
    assert "2026-12-01〜2026-12-06" in impl.verify(left, op)[0]


def test_clear_week_without_range_body_unchanged():
    impl = REG.get("clear-week")
    api = ClearApi()
    op = {"op": "clear-week", "iso_year": 2026, "iso_week": 43, "office_id": "o-1"}
    impl.snapshot(api, op)
    assert api.calls[0][1] == {"dry_run": True, "office_id": "o-1"}


def test_generate_week_range_body_and_verify():
    impl = REG.get("generate-week")
    op = _ranged("generate-week")
    rng = {"date_from": "2026-12-01", "date_to": "2026-12-06"}
    assert impl.request(op) == ("POST", "/schedule/generate-week-only",
                                {"iso_year": 2026, "iso_week": 49, "dry_run": False, **rng})
    api = ClearApi()
    assert impl.snapshot(api, op) == {"to_add": 100}
    assert api.calls[-1] == ("/schedule/generate-week-only", {"iso_year": 2026, "iso_week": 49, "dry_run": True, **rng})
    # 照合は同じ範囲の「空にする」の確認（dry_run）で、範囲の中に訪問があるかを読む（中継の中でも通る読み方）
    api.calls.clear()
    assert impl.verify(api, op) == []
    assert api.calls == [("/schedule/v2/weeks/2026/49/clear", {"dry_run": True, **rng})]
    from rk.readonly_posts import is_dry_run_post
    assert is_dry_run_post(api.calls[0][0], api.calls[0][1])
    empty = ClearApi(left=0)
    assert "作った訪問がありません" in impl.verify(empty, op)[0]
    with pytest.raises(RkError, match="2026-12-01〜2026-12-06 の日にはもう予定"):
        impl.snapshot(ClearApi(gen_409=True), op)


def test_generate_week_without_range_verify_unchanged():
    impl = REG.get("generate-week")
    api = ClearApi()
    assert impl.verify(api, {"op": "generate-week", "iso_year": 2026, "iso_week": 43}) == []
    assert api.calls == []  # 週全体は今までどおり copy-week/sources（GET）で読む


class NothingToAddApi(ClearApi):
    """範囲に型の枠が無い（日曜だけ等）: 確認は to_add 0・作ると 0 件・範囲は空のまま。"""

    def __init__(self):
        super().__init__(left=0)

    def call(self, method, path, body=None):
        if path == "/schedule/generate-week-only":
            self.calls.append((path, dict(body or {})))
            return {"to_add": 0} if body["dry_run"] else {"visits_created": 0, "unplaced_multi_staff_patients": []}
        return super().call(method, path, body)


def test_generate_week_range_with_nothing_to_add_is_not_a_failure():
    impl = REG.get("generate-week")
    op = _ranged("generate-week", date_from="2026-12-06", date_to="2026-12-06")  # 日曜だけ
    api = NothingToAddApi()
    assert impl.snapshot(api, op) == {"to_add": 0}
    assert op["_to_add"] == 0
    assert impl.check_response(op, impl.execute(api, op)) == []
    assert impl.verify(api, op) == []
    # rk verify で案を読み直したとき（覚えた値が無い）も、確認を読み直して 0 件なら失敗にしない
    fresh = _ranged("generate-week", date_from="2026-12-06", date_to="2026-12-06")
    assert impl.verify(NothingToAddApi(), fresh) == []
    # 作るものがあったのに範囲が空なら、今までどおり失敗
    assert "作った訪問がありません" in impl.verify(ClearApi(left=0), _ranged("generate-week"))[0]
    assert impl.check_response(_ranged("generate-week", _to_add=5), {"visits_created": 0})
