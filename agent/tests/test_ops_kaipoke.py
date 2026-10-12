"""rk/ops/kaipoke.py の試験（偽の Api と差し替えた時計だけ・本番にもカイポケにも触らない）。"""

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk.ops.kaipoke import (  # noqa: E402
    JST,
    KaipokeSend,
    diff_local_request,
    find_apply_jobs,
    item_date,
    items_table,
    list_items,
    send_window_problems,
    snapshot_time_text,
    summarize_items,
)

SHEET = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def item(i, action="add", day="20", staff1="鈴木", include=True):
    side = {"user_name": "試験 花子", "date": day, "start_time": "09:30", "staff1": staff1}
    return {"id": f"it{i}", "action": action, "include": include,
            "before": side if action != "add" else {},
            "after": side if action != "delete" else {}}


class FakeApi:
    def __init__(self, items, jobs=None):
        self.items = items
        self.jobs = jobs or []
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if path == f"/integrations/correction-sheets/{SHEET}/items":
            off, lim = params["offset"], params["limit"]
            rows = self.items[off:off + lim]
            return {"items": rows, "total": len(self.items), "limit": lim, "offset": off}
        if path == "/integrations/kaipoke/jobs":
            return {"items": self.jobs, "total": len(self.jobs)}
        raise AssertionError(path)

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        return {"jobId": "job-1", "status": "running"}


def make(now):
    impl = KaipokeSend()
    impl.clock = lambda: now
    return impl


def base_op(**kw):
    op = {"op": "kaipoke-send", "sheet_id": SHEET, "month": "2026-10", "week_start": "2026-10-19",
          "week_end": "2026-10-25", "expected_item_count": 3, "diff_reviewed_at": "2026-10-19T10:00:00+09:00"}
    op.update(kw)
    return op


NOON = datetime(2026, 10, 19, 10, 30, tzinfo=JST)


def test_request_mapping_dryrun_false_camelcase():
    impl = make(NOON)
    assert impl.request(base_op()) == ("POST", "/integrations/apply", {"sheetId": SHEET, "dryRun": False})
    _, _, body = impl.request(base_op(item_ids=["a", "b", "c"]))
    assert body["itemIds"] == ["a", "b", "c"]
    assert impl.undoable is False and impl.inverse(base_op(), {}) is None
    assert "別の送信" in impl.undo_note(base_op())


def test_validate_ok_in_daytime():
    assert make(NOON).validate(base_op()) == []


@pytest.mark.parametrize("missing", ["sheet_id", "month", "week_start", "week_end", "expected_item_count",
                                     "diff_reviewed_at"])
def test_validate_requires_fields(missing):
    op = base_op()
    op.pop(missing)
    assert any(missing in e for e in make(NOON).validate(op))


@pytest.mark.parametrize("hh, mm", [(8, 59), (17, 0), (17, 45), (18, 30), (21, 0)])
def test_validate_refuses_outside_window(hh, mm):
    now = datetime(2026, 10, 19, hh, mm, tzinfo=JST)
    op = base_op(diff_reviewed_at=now.isoformat())
    errs = make(now).validate(op)
    assert any("09:00〜17:00" in e for e in errs), errs


def test_validate_refuses_when_finish_after_17():
    now = datetime(2026, 10, 19, 16, 0, tzinfo=JST)
    errs = make(now).validate(base_op(expected_item_count=172, diff_reviewed_at=now.isoformat()))
    assert any("件数を分ける" in e for e in errs)


def test_utc_clock_is_converted_to_jst():
    # VPS は UTC。01:30 UTC = 10:30 JST → 送ってよい
    utc_now = datetime(2026, 10, 19, 1, 30).replace(tzinfo=__import__("datetime").timezone.utc)
    assert send_window_problems(utc_now) == []


def test_validate_stale_or_bad_review_time():
    impl = make(NOON)
    assert any("取り直して" in e for e in impl.validate(base_op(diff_reviewed_at="2026-10-19T06:00:00+09:00")))
    assert any("タイムゾーン" in e for e in impl.validate(base_op(diff_reviewed_at="2026-10-19T10:00:00")))
    assert any("未来" in e for e in impl.validate(base_op(diff_reviewed_at="2026-10-19T12:00:00+09:00")))


def test_validate_month_spanning_week_needs_item_ids():
    impl = make(datetime(2026, 9, 28, 10, 0, tzinfo=JST))
    op = base_op(month="2026-09", week_start="2026-09-28", week_end="2026-10-04",
                 diff_reviewed_at="2026-09-28T09:30:00+09:00")
    assert any("月ごとに分けて" in e for e in impl.validate(op))
    op.update(item_ids=["a", "b", "c"])
    assert impl.validate(op) == []
    assert any("含まれない" in e for e in impl.validate({**op, "month": "2026-11"}))
    assert any("item_ids の数" in e for e in impl.validate({**op, "item_ids": ["a"]}))


def test_execute_rechecks_count_and_time():
    items = [item(1), item(2), item(3), item(4, include=False)]
    api = FakeApi(items)
    resp = make(NOON).execute(api, base_op())
    assert resp["jobId"] == "job-1"
    assert api.calls[-1] == ("POST", "/integrations/apply", {"sheetId": SHEET, "dryRun": False})
    # 件数が変わっていたら送らない
    api2 = FakeApi(items[:2])
    with pytest.raises(RkError):
        make(NOON).execute(api2, base_op())
    assert not [c for c in api2.calls if c[0] == "POST"]
    # 夕方は送らない（案の検査を通っていても、送る瞬間にもう一度見る）
    api3 = FakeApi(items)
    with pytest.raises(RkError):
        make(datetime(2026, 10, 19, 17, 10, tzinfo=JST)).execute(api3, base_op())
    assert not [c for c in api3.calls if c[0] == "POST"]


def test_execute_refuses_other_month_rows():
    items = [item(1, day="30"), item(2, day="1")]
    api = FakeApi(items)
    now = datetime(2026, 9, 28, 10, 0, tzinfo=JST)
    op = base_op(month="2026-09", week_start="2026-09-28", week_end="2026-10-04", expected_item_count=2,
                 item_ids=["it1", "it2"], diff_reviewed_at=now.isoformat())
    with pytest.raises(RkError, match="月"):
        make(now).execute(api, op)


def test_snapshot_and_verify():
    api = FakeApi([item(1), item(2, "delete"), item(3, staff1="-")],
                  jobs=[{"status": "running", "params": {"op": "apply", "sheet_id": SHEET, "dry_run": False}}])
    impl = make(NOON)
    snap = impl.snapshot(api, base_op())
    assert snap["count"] == 3 and snap["summary"]["unassigned"] == 1
    assert snap["summary"]["by_action"] == {"add": 2, "delete": 1}
    assert impl.verify(api, base_op()) == []
    api.jobs[0]["status"] = "failed"
    assert impl.verify(api, base_op())
    assert make(NOON).verify(FakeApi([], jobs=[]), base_op())


def test_helpers():
    assert diff_local_request("2026-10", "2026-10-19", "2026-10-25") == (
        "POST", "/integrations/diff-local", {"month": "2026-10", "weekStart": "2026-10-19", "weekEnd": "2026-10-25"})
    ws = date(2026, 9, 28)
    assert item_date(item(1, day="2"), ws) == date(2026, 10, 2)
    assert item_date({"action": "delete", "before": {"date": "29"}}, ws) == date(2026, 9, 29)
    assert item_date({"date_iso": "2026-10-03"}, ws) == date(2026, 10, 3)
    s = summarize_items([item(1, day="28"), item(2, day="2")], ws, today=date(2026, 9, 28))
    assert s["past"] == 1 and s["months"] == {"2026-09": 1, "2026-10": 1}
    rows = items_table([item(1)], date(2026, 10, 19))
    assert rows[0]["日付"] == "2026-10-20" and rows[0]["種類"] == "追加"
    api = FakeApi([item(i) for i in range(1203)])
    assert len(list_items(api, SHEET)) == 1203
    jobs = [{"params": {"op": "apply", "sheet_id": "other", "dry_run": False}},
            {"params": {"op": "apply", "sheet_id": SHEET, "dry_run": True}},
            {"params": {"op": "apply", "sheet_id": SHEET, "dry_run": False}, "id": "ok"}]
    assert [j["id"] for j in find_apply_jobs(FakeApi([], jobs), "2026-10-19", SHEET)] == ["ok"]
    assert "10/19 01:00" in snapshot_time_text({"snapshot": {"fetched_at": "2026-10-18T16:00:00Z",
                                                             "month": "2026-10", "row_count": 300}})
    assert "写しがありません" in snapshot_time_text({})


def test_cli_items(monkeypatch, capsys):
    import contextlib
    import json

    import rk.config
    import rk.session
    from rk.ops import kaipoke

    api = FakeApi([item(1), item(2, staff1="-")])
    monkeypatch.setattr(rk.config, "load_config", lambda: object())
    monkeypatch.setattr(rk.session, "open_api", lambda cfg: contextlib.nullcontext(api))
    assert kaipoke.main(["items", "--sheet", SHEET, "--week-start", "2026-10-19"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["unassigned"] == 1 and len(out["table"]) == 2
    with pytest.raises(SystemExit):  # diff は了承の記録が無いと動かない
        kaipoke.main(["diff", "--month", "2026-10", "--week-start", "2026-10-19"])
