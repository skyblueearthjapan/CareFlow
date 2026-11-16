"""rk period scope（期間を週ごとの「組む日／触らない日」に分ける）のテスト。日付の計算だけ・本番に触らない。"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

from rk import RkError  # noqa: E402
from rk.commands import period  # noqa: E402
from rk.commands.period import build_scope, month_range  # noqa: E402

D = date.fromisoformat
TODAY = D("2026-11-16")


def weeks_of(scope):
    return [(w["monday"], w["date_from"], w["date_to"], len(w["build_days"]), len(w["frozen_days"]))
            for w in scope["weeks"]]


def test_december_2026_month():
    f, t = month_range("2026-12")
    assert (f, t) == (D("2026-12-01"), D("2026-12-31"))
    s = build_scope(f, t, TODAY)
    assert s["from"] == "2026-12-01" and s["to"] == "2026-12-31" and s["today"] == "2026-11-16"
    assert weeks_of(s) == [
        ("2026-11-30", "2026-12-01", "2026-12-06", 6, 1),
        ("2026-12-07", "2026-12-07", "2026-12-13", 7, 0),
        ("2026-12-14", "2026-12-14", "2026-12-20", 7, 0),
        ("2026-12-21", "2026-12-21", "2026-12-27", 7, 0),
        ("2026-12-28", "2026-12-28", "2026-12-31", 4, 3),
    ]
    first, last = s["weeks"][0], s["weeks"][-1]
    assert first["frozen_days"] == ["2026-11-30"]  # 11/30（月）は触らない
    d0 = first["days"][0]
    assert d0 == {"date": "2026-11-30", "weekday": "mon", "in_scope": False, "past": False, "build": False,
                  "frozen_reason": "out_of_scope", "visits": 0, "unassigned": 0, "checked_in": 0,
                  "cancelled": 0, "staff_off": 0}
    assert (first["iso_year"], first["iso_week"]) == (2026, 49)
    assert last["build_days"] == ["2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31"]
    assert last["frozen_days"] == ["2027-01-01", "2027-01-02", "2027-01-03"]
    assert (last["iso_year"], last["iso_week"]) == (2026, 53)
    assert last["level_args"] == ("--week 2026-12-28 --day 2026-12-28 --day 2026-12-29 "
                                  "--day 2026-12-30 --day 2026-12-31")
    assert all(isinstance(x, str) for x in s["summary"])
    assert "5 週" in s["summary"][0]
    json.dumps(s)  # JSON にできる


def test_single_day():
    s = build_scope(D("2026-12-02"), D("2026-12-02"), TODAY)
    assert weeks_of(s) == [("2026-11-30", "2026-12-02", "2026-12-02", 1, 6)]
    assert s["weeks"][0]["level_args"] == "--week 2026-11-30 --day 2026-12-02"


def test_two_days():
    s = build_scope(D("2026-12-03"), D("2026-12-04"), TODAY)
    assert weeks_of(s) == [("2026-11-30", "2026-12-03", "2026-12-04", 2, 5)]


def test_ten_days_across_two_weeks():
    s = build_scope(D("2026-12-03"), D("2026-12-12"), TODAY)
    assert weeks_of(s) == [("2026-11-30", "2026-12-03", "2026-12-06", 4, 3),
                           ("2026-12-07", "2026-12-07", "2026-12-12", 6, 1)]
    assert s["weeks"][1]["frozen_days"] == ["2026-12-13"]


def test_two_full_weeks():
    s = build_scope(D("2026-12-07"), D("2026-12-20"), TODAY)
    assert weeks_of(s) == [("2026-12-07", "2026-12-07", "2026-12-13", 7, 0),
                           ("2026-12-14", "2026-12-14", "2026-12-20", 7, 0)]


def test_month_starting_on_monday():
    f, t = month_range("2027-02")  # 2027-02-01 は月曜
    assert f.weekday() == 0
    s = build_scope(f, t, TODAY)
    assert weeks_of(s) == [("2027-02-01", "2027-02-01", "2027-02-07", 7, 0),
                           ("2027-02-08", "2027-02-08", "2027-02-14", 7, 0),
                           ("2027-02-15", "2027-02-15", "2027-02-21", 7, 0),
                           ("2027-02-22", "2027-02-22", "2027-02-28", 7, 0)]


def test_partly_past_range():
    s = build_scope(D("2026-11-12"), D("2026-11-22"), TODAY)
    w1, w2 = s["weeks"]
    # 11/9 の週: 11/12〜11/15 は範囲の中だが過ぎている → 組む日なし
    assert (w1["date_from"], w1["date_to"], w1["build_days"], w1["level_args"]) == (None, None, [], None)
    reasons = {d["date"]: d["frozen_reason"] for d in w1["days"]}
    assert reasons["2026-11-09"] == "out_of_scope" and reasons["2026-11-12"] == "past"
    # 11/16 の週: 今日（11/16）は組める（過ぎた日は今日より前だけ）
    assert w2["build_days"] == ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20",
                                "2026-11-21", "2026-11-22"]
    s2 = build_scope(D("2026-11-12"), D("2026-11-18"), TODAY)
    w = s2["weeks"][1]
    assert (w["date_from"], w["date_to"]) == ("2026-11-16", "2026-11-18")
    assert all(not d["build"] for d in s2["weeks"][0]["days"])
    assert any("過ぎた日 4 日" in x for x in s2["summary"])


def test_all_past():
    s = build_scope(D("2026-11-02"), D("2026-11-04"), TODAY)
    assert s["weeks"][0]["build_days"] == [] and any("組む日はありません" in x for x in s["summary"])


def test_counts_and_summary():
    counts = {"2026-12-01": {"visits": 28, "unassigned": 2, "checked_in": 0, "cancelled": 1, "staff_off": 1},
              "2026-11-30": {"visits": 30, "unassigned": 0, "checked_in": 5, "cancelled": 0, "staff_off": 0},
              "2026-12-02": {"visits": 3, "unassigned": 0, "checked_in": 1, "cancelled": 0, "staff_off": 0}}
    s = build_scope(D("2026-12-01"), D("2026-12-04"), TODAY, counts)
    days = {d["date"]: d for d in s["weeks"][0]["days"]}
    assert days["2026-12-01"]["visits"] == 28 and days["2026-12-01"]["staff_off"] == 1
    assert days["2026-11-30"]["visits"] == 30 and not days["2026-11-30"]["build"]
    text = "\n".join(s["summary"])
    assert "もう予定がある日 2 日（訪問 31 件・担当なし 2 件）" in text
    chk = [x for x in s["summary"] if "打刻" in x]
    assert chk == ["組む日のうち、打刻・実績のある訪問がある日（その訪問は空にしても残ります）: 12/2（水） 1 件"]


def test_span_limits():
    with pytest.raises(RkError, match="長すぎ"):
        build_scope(D("2026-12-01"), D("2027-02-15"), TODAY)
    with pytest.raises(RkError, match="より後"):
        build_scope(D("2026-12-05"), D("2026-12-01"), TODAY)
    build_scope(D("2026-12-01"), D("2027-01-31"), TODAY)  # 62 日は読める
    with pytest.raises(RkError, match="YYYY-MM"):
        month_range("2026/12")


def test_cli_reads_named_sql_over_touched_weeks(monkeypatch, capsys):
    from rk import cli, sqlrun
    seen = []

    def fake_run_sql(cfg, text, params=None, runner=None, name=None):
        seen.append((name, params, text))
        return [{"visit_date": "2026-11-30", "visits": 30, "unassigned": 0, "checked_in": 0, "cancelled": 0,
                 "staff_off": 0},
                {"visit_date": "2026-12-01", "visits": 4, "unassigned": 1, "checked_in": 0, "cancelled": 0,
                 "staff_off": 2}]

    monkeypatch.setattr(sqlrun, "run_sql", fake_run_sql)
    monkeypatch.setattr(period, "today_jst", lambda: TODAY)
    assert cli.main(["period", "scope", "--month", "2026-12"]) == 0
    ((name, params, text),) = seen
    assert name == "period_days"
    assert params == {"date_from": "2026-11-30", "date_to": "2027-01-03"}
    assert "params: date_from, date_to" in text.splitlines()[0]
    out = json.loads(capsys.readouterr().out)
    assert out["weeks"][0]["days"][0]["visits"] == 30 and out["weeks"][0]["days"][1]["staff_off"] == 2
    assert out["weeks"][0]["date_from"] == "2026-12-01"


def _no_sql(*_a, **_k):
    raise AssertionError("読まない")


@pytest.mark.parametrize("argv", [["period", "scope"], ["period", "scope", "--from", "2026-12-01"],
                                  ["period", "scope", "--month", "2026-12", "--from", "2026-12-01"],
                                  ["period", "scope", "--from", "2026-12-01", "--to", "2027-03-01"]])
def test_cli_errors_before_reading(monkeypatch, argv):
    from rk import cli, sqlrun
    monkeypatch.setattr(sqlrun, "run_sql", _no_sql)
    assert cli.main(argv) != 0


def test_period_sql_follows_conventions():
    from rk.sqlrun import declared_params, wrap_sql
    text = (AGENT / "sql" / "period_days.sql").read_text(encoding="utf-8")
    assert declared_params(text) == ["date_from", "date_to"]
    wrap_sql(text)  # 1 文・末尾にセミコロンなし・:'name' だけ
    assert "deleted_at is null" in text and "【検証】" in text and "in ('off', 'paid_off')" in text
    assert "not v.manual_staff_override" in text
    # 予定外の訪問も、空にしても残る側に数える（backend week_clear.plan_clear と同じ）
    assert "or v.is_unplanned" in text
