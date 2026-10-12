"""rk plan resolve（入らない訪問を 1 件ずつ決める）のテスト。架空のデータだけ。本番に触らない。"""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError, cli  # noqa: E402
from rk.plan import check_plan  # noqa: E402
from rk.report import render_html  # noqa: E402
from rk.resolve import resolve, unresolved  # noqa: E402

V1 = "11111111-1111-4111-8111-111111111111"
V2 = "22222222-2222-4222-8222-222222222222"
P1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
P2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NOW = datetime(2026, 10, 12, 15, 0, 0)


def _unplaced(vid, pid, label, start):
    return {"visit_id": vid, "patient_id": pid, "date": "2026-10-22", "weekday": 3, "start_time": start,
            "duration_min": 30, "course": "稲A", "staff_id": None, "staff_code": "S004", "staff_name": "試験 休子",
            "label": label, "reason": "その時刻に空いている人がいません", "hint": "時刻の相談か応援 1 名"}


def _pool(vid, pid, label, start):
    return {"op": "return-to-pool", "step": 1, "visit_id": vid, "patient_id": pid, "date": "2026-10-22",
            "start_time": start, "label": f"{label} → 保留プール"}


BASE = {
    "kind": "staff-absence", "title": "10/22（木）お休みの組み直し",
    "target": {"iso_year": 2026, "iso_week": 43, "week_start": "2026-10-19", "date": "2026-10-22"},
    "summary": ["訪問 30 件のうち 入る 28 件・入らない 2 件"],
    "ops": [_pool(V1, P1, "10/22（木） 甲様 10:00", "10:00"), _pool(V2, P2, "10/22（木） 乙様 14:00", "14:00"),
            {"op": "assign-staff-week", "step": 3, "visit_id": "33333333-3333-4333-8333-333333333333",
             "staff_id": "S001", "label": "丙様 → S001"}],
    "checks": ["○ NG の職員", "× 全部の訪問が入る（問題 2 件）: 入らない訪問 2 件"],
    "warnings": ["入らない: 10/22（木） 甲様 10:00（30 分）— その時刻に空いている人がいません。"],
    "blockers": ["入らない訪問が 2 件あります（rk plan resolve で 1 件ずつ決めると反映できます）"],
    "ready": False,
    "source": {"basis_hash": "abc123"},
    "unplaced": [_unplaced(V1, P1, "10/22（木） 甲様 10:00", "10:00"), _unplaced(V2, P2, "10/22（木） 乙様 14:00", "14:00")],
    "resolutions": [],
}


def plan():
    return copy.deepcopy(BASE)


def test_pool_keeps_return_to_pool_and_partial_stays_blocked():
    p = plan()
    (r,) = resolve(p, "pool", approved="今泉 10/12", key="11111111", now=NOW)
    assert r["choice"] == "pool" and r["approved"] == "今泉 10/12" and r["at"] == "2026-10-12T15:00:00"
    assert sum(1 for o in p["ops"] if o["op"] == "return-to-pool") == 2
    assert p["ready"] is False and p["blockers"] == ["入らない訪問が 1 件あります（rk plan resolve で 1 件ずつ決めると反映できます）"]
    assert [u["visit_id"] for u in unresolved(p)] == [V2]
    assert any(c.startswith("× 全部の訪問が入る（入らない訪問 2 件のうち 1 件は決めた・1 件は未定）") for c in p["checks"])
    assert any("バックアップから" in w for w in p["warnings"])


def test_allow_over_replaces_pool_with_assign_and_flips_ready():
    p = plan()
    resolve(p, "pool", approved="今泉", key="乙様", now=NOW)
    resolve(p, "allow-over", approved="今泉", key="甲様", staff="S001", now=NOW)
    assert not [o for o in p["ops"] if o["op"] == "return-to-pool" and o["visit_id"] == V1]
    (asg,) = [o for o in p["ops"] if o.get("resolution_of") == V1]
    assert asg["op"] == "assign-staff-week" and asg["staff_id"] == "S001"
    assert p["ready"] is True and p["blockers"] == []
    assert p["source"]["basis_hash"] == "abc123"
    assert any(c.startswith("△ 1 人の上限（承知の上で超過 1 件）") for c in p["checks"])
    assert any(c.startswith("△ 全部の訪問が入る") for c in p["checks"])
    assert any(w.startswith("承知の上で超過:") and "S001" in w for w in p["warnings"])
    assert check_plan(p) == []


def test_shift_with_staff_adds_assign_then_move():
    p = plan()
    resolve(p, "shift", approved="今泉", key=V2[:8], start="15:30", staff="S005", now=NOW)
    ops = [o for o in p["ops"] if o.get("resolution_of") == V2]
    assert [o["op"] for o in ops] == ["assign-staff-week", "move-visit-week"]
    mv = ops[1]
    assert (mv["old_weekday"], mv["old_start_time"], mv["new_weekday"], mv["new_start_time"]) == (3, "14:00:00", 3, "15:30:00")
    assert (mv["iso_year"], mv["iso_week"], mv["patient_id"]) == (2026, 43, P2)
    assert not [o for o in p["ops"] if o["op"] == "return-to-pool" and o["visit_id"] == V2]
    with pytest.raises(RkError, match="--start"):
        resolve(plan(), "shift", approved="今泉", key="乙様", start="25:00")


def test_keep_leaves_visit_and_warns():
    p = plan()
    resolve(p, "keep", approved="今泉", key="甲様", now=NOW)
    assert not [o for o in p["ops"] if o.get("visit_id") == V1]  # いまの担当が分からないときは操作なし
    p = plan()
    p["unplaced"][0]["staff_id"] = "S004"
    resolve(p, "keep", approved="今泉", key="甲様", now=NOW)
    assert [(o["op"], o["staff_id"]) for o in p["ops"] if o.get("visit_id") == V1] == [("assign-staff-week", "S004")]
    assert any(w.startswith("承知の上で残す:") and "お休みなのに訪問が残ります" in w for w in p["warnings"])
    with pytest.raises(RkError, match="--approved"):
        resolve(plan(), "keep", approved="", key="甲様")


def test_all_and_redecide_and_other_blockers_remain():
    p = plan()
    p["blockers"].insert(0, "新人が担当になっています")
    made = resolve(p, "pool", approved="今泉", all_=True, now=NOW)
    assert len(made) == 2 and p["ready"] is False and p["blockers"] == ["新人が担当になっています"]
    # 決め直し: pool → allow-over → pool に戻すと return-to-pool が元の位置（step 1 の並び）に戻る
    resolve(p, "allow-over", approved="今泉", key="甲様", staff="S001")
    resolve(p, "pool", approved="今泉", key="甲様")
    assert [o["op"] for o in p["ops"]][:2] == ["return-to-pool", "return-to-pool"]
    assert not [o for o in p["ops"] if o.get("resolution_of")]
    assert len(p["resolutions"]) == 2
    with pytest.raises(RkError, match="もう全部決めて"):
        resolve(p, "pool", approved="今泉", all_=True)
    with pytest.raises(RkError, match="--all では使えません"):
        resolve(plan(), "shift", approved="今泉", all_=True, start="10:00")
    with pytest.raises(RkError, match="様"):
        resolve(plan(), "pool", approved="今泉", key="様")  # 2 件に当たる


def test_cli_resolve_check_and_report(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RK_MODE", raising=False)
    f = tmp_path / "p.json"
    f.write_text(json.dumps(plan(), ensure_ascii=False), encoding="utf-8")
    assert cli.main(["plan", "check", str(f)]) == 1
    out = capsys.readouterr().out
    assert "まだ決めていない入らない訪問（2 件" in out and "甲様" in out and "乙様" in out
    assert cli.main(["plan", "resolve", str(f), "--visit", "甲様", "--choice", "pool", "--approved", "今泉 10/12"]) == 0
    assert cli.main(["plan", "resolve", str(f), "--all", "allow-over", "--staff", "S001", "--approved", "今泉 10/12"]) == 0
    out = capsys.readouterr().out
    assert "反映できる形になりました" in out
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert saved["ready"] is True and len(saved["resolutions"]) == 2
    assert cli.main(["plan", "check", str(f)]) == 0
    h = render_html(saved)
    assert "入らなかった訪問と決めたこと" in h and "保留プールへ戻す" in h and "今泉 10/12" in h
    h2 = render_html(plan())
    assert "まだ決めていない" in h2
    assert cli.main(["plan", "resolve", str(f), "--visit", "甲様", "--approved", "今泉"]) == 1  # --choice が無い
