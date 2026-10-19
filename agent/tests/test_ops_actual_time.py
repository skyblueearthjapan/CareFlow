"""rk/ops/actual_time.py の試験（偽の Api だけ・本番には触らない）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk.opsbase import ApiError  # noqa: E402
from rk.ops.actual_time import ActualTimeAdjust, ActualTimeReset, jst_hhmm  # noqa: E402

VID = "11111111-1111-1111-1111-111111111111"


def visit(**kw):
    v = {"id": VID, "visit_date": "2026-10-12", "status": "completed", "deleted_at": None,
         "actual_arrival_at": "2026-10-12T00:05:00Z", "actual_arrival_read_at": "2026-10-12T00:05:00Z",
         "actual_arrival_adjusted": False, "actual_arrival_manual": False,
         "actual_departure_at": "2026-10-12T00:45:00Z", "actual_departure_read_at": "2026-10-12T00:45:00Z",
         "actual_departure_adjusted": False, "actual_departure_manual": False}
    v.update(kw)
    return v


class FakeApi:
    def __init__(self, v):
        self.v = v
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if path == f"/visits/{VID}":
            if self.v is None:
                raise ApiError(404, "Not found")
            return self.v
        raise AssertionError(path)

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "PUT":
            self.v = dict(self.v, **{f"actual_{body['kind']}_at": f"2026-10-12T{int(body['time'][:2]) - 9:02d}:{body['time'][3:]}:00Z",
                                     f"actual_{body['kind']}_adjusted": True})
        elif method == "DELETE":
            kind = path.split("kind=")[1]
            self.v = dict(self.v, **{f"actual_{kind}_at": self.v[f"actual_{kind}_read_at"],
                                     f"actual_{kind}_adjusted": False})
        return self.v


def op(**kw):
    o = {"op": "actual-time-adjust", "visit_id": VID, "kind": "arrival", "time": "09:00",
         "reason": "ご本人から 9:00 に着いたと連絡（インターホン待ち）", "reason_code": "intercom_wait"}
    o.update(kw)
    return o


def test_jst():
    assert jst_hhmm("2026-10-12T00:05:00Z") == "09:05" and jst_hhmm(None) is None


def test_validate():
    impl = ActualTimeAdjust()
    assert impl.validate(op()) == []
    assert any("reason" in e for e in impl.validate(op(reason=" ")))
    assert any("HH:MM" in e for e in impl.validate(op(time="9時")))
    assert any("kind" in e for e in impl.validate(op(kind="both")))
    assert any("reason_code" in e for e in impl.validate(op(reason_code="fix")))
    assert any("UUID" in e for e in impl.validate(op(visit_id="x")))
    assert any("200" in e for e in impl.validate(op(reason="あ" * 201)))


def test_request_body():
    assert ActualTimeAdjust().request(op()) == (
        "PUT", f"/visits/{VID}/actual-time",
        {"kind": "arrival", "time": "09:00", "reason_text": "ご本人から 9:00 に着いたと連絡（インターホン待ち）",
         "reason_code": "intercom_wait"})
    assert ActualTimeReset().request({"visit_id": VID, "kind": "departure"}) == (
        "DELETE", f"/visits/{VID}/actual-time?kind=departure", None)


def test_roundtrip_unadjusted_inverse_is_reset():
    impl = ActualTimeAdjust()
    api = FakeApi(visit())
    before = impl.snapshot(api, op())
    assert before["at"] == "09:05" and before["adjusted"] is False
    res = impl.execute(api, op())
    assert impl.check_response(op(), res) == []
    assert impl.verify(api, op()) == []
    inv = impl.inverse(op(), before)
    assert inv == [{"op": "actual-time-reset", "visit_id": VID, "kind": "arrival"}]
    reset = ActualTimeReset()
    reset.execute(api, inv[0])
    assert reset.verify(api, inv[0]) == []
    assert impl.verify(api, op())  # 戻った後は案の時刻ではない


def test_inverse_back_to_previous_adjusted_time():
    impl = ActualTimeAdjust()
    api = FakeApi(visit(actual_arrival_at="2026-10-12T00:02:00Z", actual_arrival_adjusted=True))
    before = impl.snapshot(api, op())
    inv = impl.inverse(op(), before)
    assert inv[0]["op"] == "actual-time-adjust" and inv[0]["time"] == "09:02"
    assert ActualTimeAdjust().validate(inv[0]) == []
    assert "直" not in inv[0]["reason"] and "修正" not in inv[0]["reason"]


@pytest.mark.parametrize("v,o,msg", [
    (None, op(), "見つかりません"),
    (visit(deleted_at="2026-10-12T01:00:00Z"), op(), "見つかりません"),
    (visit(actual_arrival_at=None, actual_arrival_read_at=None), op(), "読み取りがありません"),
    (visit(actual_departure_at="2026-10-12T00:00:00Z"), op(time="09:00"), "退出"),
    (visit(actual_arrival_at=None, actual_arrival_read_at=None), op(kind="departure", time="09:50"), "到着の記録"),
    (visit(), op(kind="departure", time="09:05"), "到着"),
])
def test_execute_refuses(v, o, msg):
    api = FakeApi(v)
    with pytest.raises(RkError, match=msg):
        ActualTimeAdjust().execute(api, o)
    assert not [c for c in api.calls if c[0] in ("PUT", "DELETE")]


def test_arrival_later_than_read_is_allowed():
    """同じお宅で続けて訪問する方の QR をまとめて読んだとき、読み取りより後へ合わせられる（PO 2026-10-19）。"""
    impl = ActualTimeAdjust()
    api = FakeApi(visit())
    impl.execute(api, op(time="09:30"))
    assert impl.verify(api, op(time="09:30")) == []


def test_manual_arrival_allowed_when_flagged():
    api = FakeApi(visit(actual_arrival_at=None, actual_arrival_read_at=None, actual_departure_at=None,
                        actual_departure_read_at=None))
    ActualTimeAdjust().execute(api, op(manual=True))
    assert any(c[0] == "PUT" for c in api.calls)


def test_reset_inverse():
    r = ActualTimeReset()
    assert r.inverse({"visit_id": VID, "kind": "arrival"}, {"adjusted": False, "at": "09:05"}) == []
    inv = r.inverse({"visit_id": VID, "kind": "arrival"}, {"adjusted": True, "at": "09:00"})
    assert inv[0]["op"] == "actual-time-adjust" and inv[0]["time"] == "09:00"
