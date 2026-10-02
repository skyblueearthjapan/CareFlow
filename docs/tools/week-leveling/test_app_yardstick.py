"""アプリの「スケジュール診断」の計算を、ならしの道具で使ってよいかを確かめるテスト。

道具と同じ呼び方 (diagnose.app_metrics → backend の環境で app_yardstick.py) で呼ぶ。
期待値は手で計算した値で、アプリのコードは使わない。

実行 (リポジトリ直下):
  PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with pytest \
    python -m pytest docs/tools/week-leveling/test_app_yardstick.py -q
実データの全組み合わせの確認は、取り出し済みの week.json を WEEK_JSON で渡す (無ければ飛ばす):
  WEEK_JSON=docs/reports/<出力>/week.json ...
"""

from __future__ import annotations

import json
import math
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose import app_metrics  # noqa: E402
from solver import BUFFER_MIN, km, travel_min  # noqa: E402

A = (35.0, 140.0)
B = (35.01, 140.0)  # 経度が同じ → 距離 = 6371 × 0.01° (ラジアン) = 1.11195 km
NEAR_A = (35.0003, 140.0003)  # 約 40m (同じ建物の扱い)
KM_AB = 6371.0 * math.radians(0.01)


def v(loc, start, end, pid=None):
    return {
        "patient_id": pid or str(uuid.uuid4()),
        "start": start,
        "end": end,
        "lat": loc[0] if loc else None,
        "lng": loc[1] if loc else None,
    }


def one(route, settings=None):
    return app_metrics({"r": route}, settings)["metrics"]["r"]


def test_known_answer_two_addresses():
    m = one([v(A, 540, 600), v(B, 630, 660)])  # 9:00-10:00 → 10:30-11:00
    assert m["visit_count"] == 2
    assert m["travel_km"] == pytest.approx(KM_AB, abs=1e-6)
    assert m["travel_minutes"] == round(KM_AB / 20 * 60) == 3
    assert m["buffer_minutes"] == 8
    assert m["gap_minutes"] == 30 - 3 - 8  # 待ち時間 = すき間 − 移動 − ゆとり


def test_same_building_is_zero():
    m = one([v(A, 540, 600), v(NEAR_A, 600, 660)])
    assert (m["travel_minutes"], m["buffer_minutes"], m["travel_km"], m["gap_minutes"]) == (
        0,
        0,
        0,
        0,
    )


def test_unsorted_input_is_sorted_by_start():
    m1 = one([v(A, 540, 600), v(B, 630, 660), v(A, 700, 730)])
    m2 = one([v(A, 700, 730), v(B, 630, 660), v(A, 540, 600)])
    assert m1 == m2
    assert m1["travel_minutes"] == 6 and m1["buffer_minutes"] == 16


def test_overlap_is_not_detected_and_gap_clamps_to_zero():
    """重なり (今の盤面にある) は診断では見つからない: 移動は数え、待ち時間は 0 で止まる。
    → 重なりは道具の側で別に数える必要がある。"""
    m = one([v(A, 540, 600), v(B, 570, 630)])
    assert m["travel_minutes"] == 3 and m["buffer_minutes"] == 8 and m["gap_minutes"] == 0


def test_start_point_travel_not_counted():
    """事業所・自宅から 1 件目への移動は数えない (1 件だけなら移動 0)。"""
    m = one([v(B, 540, 600)])
    assert (m["travel_minutes"], m["buffer_minutes"], m["travel_km"]) == (0, 0, 0)


def test_settings_speed_and_buffer_applied():
    m = one([v(A, 540, 600), v(B, 630, 660)], {"travel_speed_kmh": 40, "visit_buffer_min": 5})
    assert m["travel_minutes"] == round(KM_AB / 40 * 60) == 2
    assert m["buffer_minutes"] == 5


def test_default_settings_match_tool():
    """設定が無い (本番は 0 行) ときの既定 = 道具と同じ 20km/h・ゆとり 8 分。"""
    out = app_metrics({"r": [v(A, 540, 600)]}, None)
    assert out["config"]["travel_speed_kmh"] == 20
    assert out["config"]["visit_buffer_min"] == BUFFER_MIN == 8
    assert out["threshold_min"] == 10  # アプリの改善提案が出す最小の効果 (分/週)


def test_missing_coords_counts_buffer_only():
    """座標の無い利用者は移動 0・ゆとりだけ (道具は座標が無いと止まるので実際には来ない)。"""
    m = one([v(A, 540, 600), v(None, 630, 660)])
    assert m["travel_minutes"] == 0 and m["buffer_minutes"] == 8


@pytest.mark.skipif(not os.environ.get("WEEK_JSON"), reason="WEEK_JSON が無い")
def test_real_coords_all_pairs_match_tool():
    """実際の利用者の座標の全組み合わせで、アプリの (移動＋ゆとり, 距離) と道具の値が一致する。"""
    d = json.loads(Path(os.environ["WEEK_JSON"]).read_text(encoding="utf-8"))
    pts = [(p["id"], (float(p["lat"]), float(p["lng"]))) for p in d["patients"] if p["lat"]]
    routes, expect = {}, {}
    for i, (pa, la) in enumerate(pts):
        for pb, lb in pts[i + 1 :]:
            key = f"{pa}|{pb}"
            routes[key] = [v(la, 600, 600, pa), v(lb, 700, 700, pb)]
            tr = travel_min(la, lb)
            expect[key] = (tr + (BUFFER_MIN if tr else 0), km(la, lb) if tr else 0.0)
    got = app_metrics(routes)["metrics"]
    assert len(got) == len(routes) > 1000
    bad = [
        k
        for k, (mins, dist) in expect.items()
        if got[k]["travel_minutes"] + got[k]["buffer_minutes"] != mins
        or abs(got[k]["travel_km"] - dist) > 1e-9
    ]
    assert not bad, f"{len(bad)} 組が食い違う 例: {bad[:3]}"
