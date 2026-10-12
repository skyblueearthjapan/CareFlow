"""rk/ops/special.py の部品のテスト（偽の Api・本番に触らない）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("rk.opsbase", reason="rk.opsbase（I1 の土台）がまだありません")
from rk.opsbase import ApiError, load_all  # noqa: E402

REG = load_all()

P1 = "11111111-1111-1111-1111-111111111111"
PER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MK = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
T1 = "66666666-6666-6666-6666-666666666666"
V1 = "22222222-2222-2222-2222-222222222222"


class FakeApi:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        r = self.routes.get(path)
        if r is None:
            raise ApiError(404, "not found")
        return r(params) if callable(r) else r

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self.routes.get((method, path), {})


def op(name):
    return REG.get(name)


def period(**kw):
    p = {"id": PER, "patient_id": P1, "start_date": "2026-10-19", "end_date": "2026-11-01", "weekly_target": 5,
         "status": "active"}
    p.update(kw)
    return p


def calendar(marks):
    return {"weeks": [{"days": [{"date": d, "extra_mark": m} for d, m in marks.items()]}]}


# ---------------------------------------------------------------- special-period-create

def test_period_create_posts_period_and_marks_then_inverse_cancels():
    impl = op("special-period-create")
    o = {"op": "special-period-create", "patient_id": P1, "start_date": "2026-10-19", "end_date": "2026-11-01",
         "weekly_target": 5, "marks": ["2026-10-20", "2026-10-22"], "label": "特別訪問週間"}
    assert impl.validate(o) == []
    assert impl.request(o) == ("POST", "/special-visit-periods", {
        "patient_id": P1, "start_date": "2026-10-19", "end_date": "2026-11-01", "weekly_target": 5})
    api = FakeApi({"/special-visit-periods": [], ("POST", "/special-visit-periods"): period()})
    before = impl.snapshot(api, o)
    impl.execute(api, o)
    posts = [c for c in api.calls if c[0] == "POST"]
    assert posts[1] == ("POST", f"/special-visit-periods/{PER}/marks", {"iso_year": 2026, "iso_week": 43, "weekday": 1})
    assert posts[2][2]["weekday"] == 3
    inv = impl.inverse(o, before)
    assert inv[0]["action"] == "cancel" and impl.validate(inv[0]) == []
    # 戻しの実行 = 有効な期間を引いて PATCH status=cancelled
    api2 = FakeApi({"/special-visit-periods": [period()]})
    impl.execute(api2, inv[0])
    assert api2.calls[-1] == ("PATCH", f"/special-visit-periods/{PER}", {"status": "cancelled"})
    assert impl.verify(FakeApi({"/special-visit-periods": []}), inv[0]) == []


def test_period_create_verify_marks():
    impl = op("special-period-create")
    o = {"patient_id": P1, "start_date": "2026-10-19", "end_date": "2026-11-01", "marks": ["2026-10-20"]}
    api = FakeApi({"/special-visit-periods": [period()],
                   f"/special-visit-periods/{PER}/calendar": calendar({"2026-10-20": {"id": MK, "status": "pool"}})})
    assert impl.verify(api, o) == []
    api.routes[f"/special-visit-periods/{PER}/calendar"] = calendar({})
    assert impl.verify(api, o)


def test_period_existing_blocks_inverse_and_add_marks_roundtrip():
    impl = op("special-period-create")
    o = {"patient_id": P1, "start_date": "2026-10-19", "end_date": "2026-11-01"}
    assert impl.inverse(o, {"existing_period": period()}) is None
    add = {"op": "special-period-create", "action": "add-marks", "period_id": PER,
           "marks": ["2026-10-20", "2026-10-21"], "label": "○を足す"}
    assert impl.validate(add) == []
    api = FakeApi({f"/special-visit-periods/{PER}/calendar": calendar({"2026-10-20": {"id": MK, "status": "pool"}})})
    before = impl.snapshot(api, add)
    inv = impl.inverse(add, before)
    assert inv[0]["action"] == "remove-marks" and inv[0]["marks"] == ["2026-10-21"]
    api.routes[f"/special-visit-periods/{PER}/calendar"] = calendar({"2026-10-21": {"id": "m2", "status": "pool"}})
    impl.execute(api, inv[0])
    assert api.calls[-1] == ("DELETE", "/special-visit-marks/m2", None)
    api.routes[f"/special-visit-periods/{PER}/calendar"] = calendar({"2026-10-21": {"id": "m2", "status": "placed"}})
    with pytest.raises(ApiError):
        impl.execute(api, inv[0])


def test_period_validate_errors():
    impl = op("special-period-create")
    assert impl.validate({"patient_id": P1, "start_date": "2026-10-19", "end_date": "2026-10-01"})
    assert impl.validate({"patient_id": P1, "start_date": "2026-10-19", "end_date": "2026-11-01", "weekly_target": 9})
    assert impl.validate({"patient_id": P1, "start_date": "2026-10-19", "end_date": "2026-11-01",
                          "marks": ["2026-10-25"]})  # 日曜
    assert impl.validate({"action": "x"})


# ---------------------------------------------------------------- special-visit-place

def test_place_request_matches_build_ops_and_inverse_returns_to_pool():
    impl = op("special-visit-place")
    o = {"op": "special-visit-place", "mark_id": MK, "iso_year": 2026, "iso_week": 43,
         "body": {"course_template_id": T1, "start_time": "14:00"}, "label": "10/20 14:00 特別"}
    assert impl.validate(o) == []
    assert impl.request(o) == ("POST", f"/special-visit-marks/{MK}/place",
                               {"start_time": "14:00", "course_template_id": T1})
    pool = [{"mark": {"id": MK, "patient_id": P1, "iso_year": 2026, "iso_week": 43, "weekday": 1}}]
    before = impl.snapshot(FakeApi({"/special-visit-marks/pool": pool}), o)
    assert before["date"] == "2026-10-20"
    inv = impl.inverse(o, before)
    assert inv[0] == {"op": "return-to-pool", "patient_id": P1, "date": "2026-10-20", "start_time": "14:00",
                      "cascade_partner": True, "label": inv[0]["label"]}
    assert op("return-to-pool").validate(inv[0]) == []
    # ○の曜日を動かした配置は戻せない
    assert impl.inverse({**o, "move_to_weekday": 3}, before) is None


def test_place_locator_and_verify():
    impl = op("special-visit-place")
    o = {"patient_id": P1, "date": "2026-10-20", "course_template_id": T1, "start_time": "14:00"}
    assert impl.validate(o) == []
    pool = [{"mark": {"id": MK, "patient_id": P1, "iso_year": 2026, "iso_week": 43, "weekday": 1}}]
    api = FakeApi({"/special-visit-marks/pool": pool, "/visits": [
        {"id": V1, "status": "planned", "start_time": "14:00:00"}]})
    impl.execute(api, o)
    assert api.calls[-1] == ("POST", f"/special-visit-marks/{MK}/place", {"start_time": "14:00", "course_template_id": T1})
    assert impl.verify(api, o) == []
    with pytest.raises(ApiError):
        impl.execute(FakeApi({"/special-visit-marks/pool": []}), o)


def test_place_validate_errors():
    impl = op("special-visit-place")
    assert impl.validate({"mark_id": MK, "start_time": "14:00", "course_template_id": T1})  # 週が無い
    assert impl.validate({"mark_id": MK, "iso_year": 2026, "iso_week": 43, "start_time": "14:00"})  # 置き先なし
    assert impl.validate({"mark_id": MK, "iso_year": 2026, "iso_week": 43, "start_time": "14:00",
                          "course_template_id": T1, "course_id": T1})  # 2 つ
