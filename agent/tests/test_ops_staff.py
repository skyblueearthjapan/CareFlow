"""rk/ops/staff.py の部品のテスト（偽の Api・本番に触らない）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("rk.opsbase", reason="rk.opsbase（I1 の土台）がまだありません")
from rk.opsbase import ApiError, load_all  # noqa: E402

REG = load_all()

S1 = "33333333-3333-3333-3333-333333333333"
S2 = "44444444-4444-4444-4444-444444444444"
OV = "99999999-9999-9999-9999-999999999999"
C1 = "55555555-5555-5555-5555-555555555555"


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


# ---------------------------------------------------------------- staff-day-off

def test_day_off_new_posts_and_inverse_removes():
    impl = op("staff-day-off")
    o = {"op": "staff-day-off", "staff_id": S1, "date": "2026-10-15", "note": "体調不良", "label": "S1 休み"}
    assert impl.validate(o) == []
    assert impl.request(o) == ("POST", f"/staff/{S1}/overrides", {"date": "2026-10-15", "type": "休み", "note": "体調不良"})
    api = FakeApi({f"/staff/{S1}/overrides": []})
    before = impl.snapshot(api, o)
    assert before["override"] is None
    impl.execute(api, o)
    assert api.calls[-1][0:2] == ("POST", f"/staff/{S1}/overrides")
    inv = impl.inverse(o, before)
    assert inv[0]["action"] == "remove" and op("staff-day-off").validate(inv[0]) == []
    # 戻し: 行があれば DELETE
    api.routes[f"/staff/{S1}/overrides"] = [{"id": OV, "date": "2026-10-15", "type": "休み"}]
    assert impl.verify(api, o) == []
    impl.execute(api, inv[0])
    assert api.calls[-1] == ("DELETE", f"/staff/{S1}/overrides/{OV}", None)


def test_day_off_existing_patches_and_inverse_restores():
    impl = op("staff-day-off")
    o = {"staff_id": S1, "date": "2026-10-15", "type": "off", "label": "x"}
    api = FakeApi({f"/staff/{S1}/overrides": [{"id": OV, "date": "2026-10-15", "type": "時間変更",
                                               "start_time": "09:00", "end_time": "13:00", "note": "通院"}]})
    before = impl.snapshot(api, o)
    impl.execute(api, o)
    assert api.calls[-1] == ("PATCH", f"/staff/{S1}/overrides/{OV}",
                             {"type": "休み", "start_time": None, "end_time": None, "note": None})
    inv = impl.inverse(o, before)[0]
    assert inv == {"op": "staff-day-off", "staff_id": S1, "date": "2026-10-15", "label": inv["label"],
                   "action": "set", "type": "時間変更", "start_time": "09:00", "end_time": "13:00", "note": "通院"}
    assert impl.validate(inv) == []
    m, path, body = impl.request(inv)
    assert body == {"date": "2026-10-15", "type": "時間変更", "start_time": "09:00", "end_time": "13:00", "note": "通院"}


def test_day_off_validate_errors():
    impl = op("staff-day-off")
    assert impl.validate({"staff_id": S1, "date": "10/15"})
    assert impl.validate({"staff_id": S1, "date": "2026-10-15", "type": "有休"})
    assert impl.validate({"staff_id": S1, "date": "2026-10-15", "type": "時間変更"})  # 時刻が無い
    assert impl.validate({"staff_id": S1, "date": "2026-10-15", "action": "delete"})


# ---------------------------------------------------------------- staff-off-week

def test_off_week_request_and_no_inverse():
    impl = op("staff-off-week")
    assert impl.undoable is False
    o = {"op": "staff-off-week", "staff_id": S1, "date": "2026-10-15", "to_staff_id": S2, "label": "急休代替"}
    assert impl.validate(o) == []
    assert impl.request(o) == ("POST", "/schedule/v2/staff-off-week",
                               {"staff_id": S1, "date": "2026-10-15", "to_staff_id": S2})
    assert impl.inverse(o, {}) is None
    o2 = {"staff_id": S1, "date": "2026-10-15", "to_staff_id": None}
    assert impl.validate(o2) == [] and impl.request(o2)[2]["to_staff_id"] is None


def test_off_week_validate_errors():
    impl = op("staff-off-week")
    assert impl.validate({"staff_id": S1, "date": "2026-10-15"})  # to_staff_id が無い
    assert impl.validate({"staff_id": S1, "date": "2026-10-15", "to_staff_id": S1})  # 本人


def test_off_week_snapshot_and_verify():
    impl = op("staff-off-week")
    o = {"staff_id": S1, "date": "2026-10-15", "to_staff_id": S2}
    api = FakeApi({
        "/visits": [{"id": "v1", "status": "planned", "primary_staff_id": S1,
                     "start_time": "09:00:00", "staff_assignments": []}],
        "/courses": [{"id": C1, "assigned_staff_id": S1, "code": "A"}, {"id": "c2", "assigned_staff_id": S2}],
        f"/staff/{S1}/overrides": [],
    })
    before = impl.snapshot(api, o)
    assert before["notes"] == [] and before["courses"][0]["id"] == C1
    errs = impl.verify(api, o)
    assert len(errs) == 3  # 休みなし・コース残り・主担当残り
    api.routes.update({"/visits": [], "/courses": [{"id": C1, "assigned_staff_id": S2}],
                       f"/staff/{S1}/overrides": [{"id": OV, "date": "2026-10-15", "type": "休み"}]})
    assert impl.verify(api, o) == []


def test_off_week_leave_types():
    impl = op("staff-off-week")
    base = {"staff_id": S1, "date": "2026-10-15", "to_staff_id": None}
    for t, api_type in (("午前休", "am_off"), ("pm_off", "pm_off"), ("有給", "paid_off")):
        o = {**base, "type": t}
        assert impl.validate(o) == []
        assert impl.request(o)[2]["leave_type"] == api_type
    assert "leave_type" not in impl.request({**base, "type": "休み"})[2]
    assert impl.validate({**base, "type": "時間変更"})  # 時間変更は急休代替にしない
    assert impl.validate({**base, "type": "有休"})


def test_off_week_verify_half_day_checks_only_the_record():
    impl = op("staff-off-week")
    o = {"staff_id": S1, "date": "2026-10-15", "to_staff_id": S2, "type": "午後休"}
    api = FakeApi({
        # 半休はコースが本人のまま・休みの時間帯の外（午前）の訪問も本人に残る
        "/visits": [{"id": "v1", "status": "planned", "primary_staff_id": S1, "start_time": "09:00:00"}],
        "/courses": [{"id": C1, "assigned_staff_id": S1}],
        f"/staff/{S1}/overrides": [{"id": OV, "date": "2026-10-15", "type": "午後休"}],
    })
    assert impl.verify(api, o) == []
    # 休みの時間帯（終わりが 12:30 より後）にかかる本人の予定が残っていれば知らせる
    api.routes["/visits"] = [
        {"id": "v1", "status": "planned", "primary_staff_id": S1, "start_time": "11:55:00", "end_time": "12:30:00"},
        {"id": "v2", "status": "planned", "primary_staff_id": S1, "start_time": "12:00:00", "end_time": "12:35:00"},
        {"id": "v3", "status": "planned", "primary_staff_id": S2, "start_time": "14:00:00", "end_time": "14:35:00"},
    ]
    assert impl.verify(api, o) == ["2026-10-15 に まだ本人が主担当の午後休の時間帯の予定が 1 件あります"]
    # 主担当が空でも、印 (明示の担当なし) が無くコースの担当が本人なら本人の予定として数える
    api.routes["/visits"] = [
        {"id": "v4", "status": "planned", "primary_staff_id": None, "manual_staff_override": False,
         "course_id": C1, "start_time": "14:00:00", "end_time": "14:35:00"},
        {"id": "v5", "status": "planned", "primary_staff_id": None, "manual_staff_override": True,
         "course_id": C1, "start_time": "15:00:00", "end_time": "15:35:00"},
    ]
    assert impl.verify(api, o) == ["2026-10-15 に まだ本人が主担当の午後休の時間帯の予定が 1 件あります"]
    api.routes[f"/staff/{S1}/overrides"] = [{"id": OV, "date": "2026-10-15", "type": "休み"}]
    assert impl.verify(api, o)[0] == "2026-10-15 の午後休が登録されていません"


@pytest.mark.parametrize(("label", "start", "end", "hit"), [
    ("午前休", "12:59:00", "13:30:00", True), ("午前休", "13:00:00", "13:35:00", False),
    ("午後休", "12:00:00", "12:30:00", False), ("午後休", "12:00:00", "12:31:00", True),
    ("午後休", "12:30:00", None, True), ("午後休", "12:29:00", None, False),
    ("休み", "10:00:00", "10:35:00", False), ("時間変更", "10:00:00", "10:35:00", False),
])
def test_half_day_leave_boundaries(label, start, end, hit):
    from rk.ops.staff import hits_half_day_leave
    assert hits_half_day_leave(label, start, end) is hit


def test_off_week_verify_paid_off_is_full_day():
    impl = op("staff-off-week")
    o = {"staff_id": S1, "date": "2026-10-15", "to_staff_id": S2, "type": "有給"}
    api = FakeApi({
        "/visits": [], "/courses": [{"id": C1, "assigned_staff_id": S1}],
        f"/staff/{S1}/overrides": [{"id": OV, "date": "2026-10-15", "type": "有給"}],
    })
    assert len(impl.verify(api, o)) == 1  # 終日なのでコースが本人に残っていれば知らせる


def test_day_off_paid_leave_label():
    impl = op("staff-day-off")
    o = {"staff_id": S1, "date": "2026-10-15", "type": "paid_off"}
    assert impl.validate(o) == []
    assert impl.request(o)[2]["type"] == "有給"
