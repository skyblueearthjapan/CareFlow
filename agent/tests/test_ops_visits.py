"""rk/ops/visits.py の部品のテスト（偽の Api・本番に触らない）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

opsbase = pytest.importorskip("rk.opsbase", reason="rk.opsbase（I1 の土台）がまだありません")
from rk.opsbase import ApiError, load_all  # noqa: E402

REG = load_all()

P1 = "11111111-1111-1111-1111-111111111111"
V1 = "22222222-2222-2222-2222-222222222222"
S1 = "33333333-3333-3333-3333-333333333333"
S2 = "44444444-4444-4444-4444-444444444444"
C1 = "55555555-5555-5555-5555-555555555555"
T1 = "66666666-6666-6666-6666-666666666666"
T2 = "77777777-7777-7777-7777-777777777777"
O1 = "88888888-8888-8888-8888-888888888888"


class FakeApi:
    """呼び出しを記録し、GET には用意した応答（dict か関数）を返す。"""

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
    impl = REG.get(name)
    assert impl is not None, name
    return impl


def visit(**kw):
    v = {"id": V1, "patient_id": P1, "patient_name": "テスト", "visit_date": "2026-10-14", "start_time": "09:30:00",
         "end_time": "10:05:00", "status": "planned", "source": "auto", "week_pinned": False, "course_id": C1,
         "primary_staff_id": S1, "visit_group_id": None, "staff_assignments": [{"staff_id": S1}]}
    v.update(kw)
    return v


# ---------------------------------------------------------------- assign-staff-week

def test_assign_request_and_inverse_roundtrip():
    o = {"op": "assign-staff-week", "visit_id": V1, "staff_id": S2, "label": "9:30 テスト S1→S2"}
    impl = op("assign-staff-week")
    assert impl.validate(o) == []
    assert impl.request(o) == ("POST", "/schedule/v2/visit-assign-staff-week", {"visit_id": V1, "staff_id": S2})
    api = FakeApi({f"/visits/{V1}": visit()})
    before = impl.snapshot(api, o)
    inv = impl.inverse(o, before)
    assert inv == [{"op": "assign-staff-week", "visit_id": V1, "staff_id": S1, "label": inv[0]["label"]}]
    assert "acknowledge_constraint_warnings" not in inv[0]  # 戻しでも NG を黙って通さない
    assert op(inv[0]["op"]).validate(inv[0]) == []
    # 反映後の照合
    api.routes[f"/visits/{V1}"] = visit(primary_staff_id=S2)
    assert impl.verify(api, o) == []
    assert impl.verify(api, inv[0])  # 戻し前なので食い違い


def test_assign_staff_code_resolved_at_execute():
    impl = op("assign-staff-week")
    o = {"op": "assign-staff-week", "visit_id": V1, "to_staff": "S005", "label": "x"}
    assert impl.validate(o) == []
    api = FakeApi({"/staff": [{"id": S2, "code": "S005"}, {"id": S1, "code": "S001"}]})
    impl.execute(api, o)
    assert api.calls[-1] == ("POST", "/schedule/v2/visit-assign-staff-week", {"visit_id": V1, "staff_id": S2})


def test_assign_unassign_and_validate_errors():
    impl = op("assign-staff-week")
    assert impl.request({"visit_id": V1, "staff_id": None})[2]["staff_id"] is None
    assert impl.validate({"visit_id": "x"})  # UUID でない・staff_id なし
    assert any("staff_id" in e for e in impl.validate({"visit_id": V1}))


def test_assign_snapshot_notes_non_planned():
    impl = op("assign-staff-week")
    before = impl.snapshot(FakeApi({f"/visits/{V1}": visit(status="completed")}), {"visit_id": V1, "staff_id": S2})
    assert before["notes"]


# ---------------------------------------------------------------- move-visit-week

def move_op(**kw):
    o = {"op": "move-visit-week", "iso_year": 2026, "iso_week": 42, "patient_id": P1, "old_weekday": 2,
         "old_start_time": "09:30:00", "new_weekday": 3, "new_start_time": "10:00", "label": "水→木"}
    o.update(kw)
    return o


def test_move_request_matches_build_ops_body():
    impl = op("move-visit-week")
    o = {"op": "move-visit-week", "label": "x", "body": move_op(new_course_template_id=T2)}
    del o["body"]["op"], o["body"]["label"]
    assert impl.validate(o) == []
    m, path, body = impl.request(o)
    assert (m, path) == ("POST", "/schedule/v2/visit-move-week-only")
    assert body["old_start_time"] == "09:30" and body["new_course_template_id"] == T2


def test_move_check_response_zero_moved_stops():
    impl = op("move-visit-week")
    assert impl.check_response(move_op(), {"visits_moved": 0})
    assert impl.check_response(move_op(), {"visits_moved": 1}) == []


def test_move_inverse_roundtrip_with_course_change():
    impl = op("move-visit-week")
    o = move_op(new_course_template_id=T2)
    api = FakeApi({
        "/visits": lambda p: [visit()] if p["week_start"] == "2026-10-14" else [],
        f"/courses/{C1}": {"id": C1, "office_id": O1, "code": "A", "assigned_staff_id": S1},
        "/course-templates": [{"id": T1, "label": "A"}, {"id": T2, "label": "B"}],
    })
    before = impl.snapshot(api, o)
    assert before["old_course_template_id"] == T1
    inv = impl.inverse(o, before)
    assert len(inv) == 1
    i = inv[0]
    assert (i["old_weekday"], i["old_start_time"], i["new_weekday"], i["new_start_time"]) == (3, "10:00", 2, "09:30")
    assert i["new_course_template_id"] == T1
    assert op("move-visit-week").validate(i) == []
    # 戻しの戻しで元の案の位置に戻る（往復）
    inv2 = impl.inverse(i, {"visits": [visit()], "course_ids": [C1], "old_course_template_id": T2})
    assert (inv2[0]["new_weekday"], inv2[0]["new_start_time"], inv2[0]["new_course_template_id"]) == (3, "10:00", T2)


def test_move_inverse_none_when_two_person_in_two_courses():
    impl = op("move-visit-week")
    before = {"visits": [visit(), visit(id="x")], "course_ids": [C1, "c2"], "old_course_template_id": None}
    assert impl.inverse(move_op(new_course_template_id=T2), before) is None
    # コースを変えない移動なら 2 名体制でも戻せる
    assert impl.inverse(move_op(), before)[0].get("new_course_template_id") is None


def test_move_validate_errors():
    errs = op("move-visit-week").validate({"iso_year": 2026, "iso_week": 60, "patient_id": P1, "old_weekday": 9,
                                           "old_start_time": "9時", "new_weekday": 1, "new_start_time": "10:00"})
    assert len(errs) >= 3


def test_move_verify():
    impl = op("move-visit-week")
    api = FakeApi({"/visits": lambda p: [visit(visit_date="2026-10-15", start_time="10:00:00")]
                   if p["week_start"] == "2026-10-15" else []})
    assert impl.verify(api, move_op()) == []
    api2 = FakeApi({"/visits": lambda p: [visit()] if p["week_start"] == "2026-10-14" else []})
    assert len(impl.verify(api2, move_op())) == 2


# ---------------------------------------------------------------- cancel-visit-week

def test_cancel_request_inverse_roundtrip():
    impl = op("cancel-visit-week")
    o = {"op": "cancel-visit-week", "visit_id": V1, "reason": "ご本人の都合", "label": "取消"}
    assert impl.validate(o) == []
    assert impl.request(o) == ("POST", "/schedule/v2/visit-cancel-week",
                               {"visit_id": V1, "cancel": True, "reason": "ご本人の都合"})
    api = FakeApi({f"/visits/{V1}": visit()})
    inv = impl.inverse(o, impl.snapshot(api, o))
    assert inv[0]["cancel"] is False and inv[0]["visit_id"] == V1
    api.routes[f"/visits/{V1}"] = visit(status="cancelled")
    assert impl.verify(api, o) == []
    assert impl.verify(api, inv[0])
    # 元から取消なら何もしない
    assert impl.inverse(o, {"visit": {"id": V1, "status": "cancelled"}}) == []


def test_cancel_snapshot_blue_pin_note_and_validate():
    impl = op("cancel-visit-week")
    before = impl.snapshot(FakeApi({f"/visits/{V1}": visit(week_pinned=True)}), {"visit_id": V1})
    assert any("青ピン" in n for n in before["notes"])
    assert impl.validate({"visit_id": V1, "cancel": "yes"})
    assert impl.validate({"visit_id": V1, "reason": "x" * 201})


# ---------------------------------------------------------------- return-to-pool

def test_return_to_pool_request_and_no_inverse():
    impl = op("return-to-pool")
    assert impl.undoable is False
    o = {"op": "return-to-pool", "visit_id": V1, "cascade_partner": False, "op_group_id": T1, "label": "x"}
    assert impl.validate(o) == []
    assert impl.request(o) == ("DELETE", f"/visits/{V1}?cascade_partner=false&op_group_id={T1}", None)
    assert impl.inverse(o, {"visit": visit()}) is None


def test_return_to_pool_locator_and_verify():
    impl = op("return-to-pool")
    o = {"op": "return-to-pool", "patient_id": P1, "date": "2026-10-14", "start_time": "09:30", "label": "x"}
    assert impl.validate(o) == []
    api = FakeApi({"/visits": [visit(), visit(id="other", start_time="13:00:00")]})
    impl.execute(api, o)
    assert api.calls[-1] == ("DELETE", f"/visits/{V1}?cascade_partner=true", None)
    assert impl.verify(api, o)  # まだ残っている（偽 API は消さない）
    assert impl.verify(FakeApi({"/visits": []}), o) == []
    with pytest.raises(ApiError):
        impl.execute(FakeApi({"/visits": []}), o)
    # visit_id 指定の照合: 404 = 消えた
    assert impl.verify(FakeApi({}), {"visit_id": V1}) == []


# ---------------------------------------------------------------- add-visit-week / place-from-pool

def test_add_visit_place_and_fix_and_inverse():
    impl = op("add-visit-week")
    o = {"op": "add-visit-week", "patient_id": P1, "date": "2026-10-16", "start_time": "11:00", "duration_min": 35,
         "course_template_id": T1, "label": "金 11:00 追加"}
    assert impl.validate(o) == []
    m, path, body = impl.request(o)
    assert (m, path) == ("POST", "/schedule/place-and-fix")
    assert body == {"patient_id": P1, "iso_year": 2026, "iso_week": 42, "weekday": 4, "start_time": "11:00",
                    "duration_min": 35, "staff_count": 1, "fix_pattern": False, "course_template_id": T1}
    before = impl.snapshot(FakeApi({"/visits": []}), o)
    inv = impl.inverse(o, before)
    assert inv[0]["op"] == "return-to-pool" and inv[0]["date"] == "2026-10-16" and inv[0]["start_time"] == "11:00"
    assert op("return-to-pool").validate(inv[0]) == []
    # 同じ時刻にもう予定があると戻せない
    assert impl.inverse(o, impl.snapshot(FakeApi({"/visits": [visit(visit_date="2026-10-16", start_time="11:00:00")]}), o)) is None


def test_add_visit_without_course_uses_post_visits():
    impl = op("add-visit-week")
    o = {"patient_id": P1, "iso_year": 2026, "iso_week": 42, "weekday": 5, "start_time": "12:00", "duration_min": 35}
    assert impl.validate(o) == []
    m, path, body = impl.request(o)
    assert (m, path) == ("POST", "/visits")
    assert body["visit_date"] == "2026-10-17" and body["end_time"] == "12:35" and body["source"] == "manual_week"


def test_add_visit_two_person_and_errors():
    impl = op("add-visit-week")
    o = {"patient_id": P1, "date": "2026-10-16", "start_time": "11:00", "duration_min": 60, "staff_count": 2,
         "course_template_ids": [T1, T2]}
    assert impl.validate(o) == []
    assert impl.request(o)[2]["course_template_ids"] == [T1, T2]
    assert impl.validate({**o, "course_template_ids": [T1, T1]})
    assert impl.validate({**o, "course_template_ids": None, "course_template_id": T1})  # 2 名なのに 1 コース
    assert impl.validate({"patient_id": P1, "start_time": "11:00", "duration_min": 0})


def test_place_from_pool_requires_course():
    impl = op("place-from-pool")
    o = {"patient_id": P1, "date": "2026-10-16", "start_time": "11:00", "duration_min": 35}
    assert any("course_template_id" in e for e in impl.validate(o))
    o["course_template_id"] = T1
    assert impl.validate(o) == []
    assert impl.request(o)[1] == "/schedule/place-and-fix"


# ---------------------------------------------------------------- patch-course-staff

def test_patch_course_staff_by_id_roundtrip():
    impl = op("patch-course-staff")
    o = {"op": "patch-course-staff", "course_id": C1, "staff_id": S2, "op_group_id": T1, "label": "稲A←S2"}
    assert impl.validate(o) == []
    assert impl.request(o) == ("PATCH", f"/courses/{C1}", {"assigned_staff_id": S2, "op_group_id": T1})
    api = FakeApi({f"/courses/{C1}": {"id": C1, "assigned_staff_id": S1, "code": "A", "weekday": 2, "office_id": O1}})
    inv = impl.inverse(o, impl.snapshot(api, o))
    assert inv[0]["course_id"] == C1 and inv[0]["staff_id"] == S1
    assert impl.verify(api, inv[0]) == []
    assert impl.verify(api, o)


def test_patch_course_staff_by_label_resolves_like_build_ops():
    impl = op("patch-course-staff")
    o = {"op": "patch-course-staff", "iso_year": 2026, "iso_week": 42, "weekday": 2, "course": "稲A",
         "staff_id": S2, "label": "x"}
    assert impl.validate(o) == []
    api = FakeApi({"/offices": [{"id": O1, "short_label": "稲"}],
                   "/courses": [{"id": C1, "office_id": O1, "code": "A"}, {"id": "c-m", "office_id": O1, "code": "M"}]})
    impl.execute(api, o)
    assert api.calls[-1] == ("PATCH", f"/courses/{C1}", {"assigned_staff_id": S2})
    with pytest.raises(ApiError):
        impl.execute(api, {**o, "course": "津A"})
    assert impl.validate({"staff_id": S2})  # コースの指定が無い


# ---------------------------------------------------------------- accompaniment-set

def test_accompaniment_roundtrip():
    impl = op("accompaniment-set")
    o = {"op": "accompaniment-set", "staff_id": S2, "iso_year": 2026, "iso_week": 43, "course_ids": [C1],
         "label": "新人さん同行"}
    assert impl.validate(o) == []
    assert impl.request(o) == ("PUT", "/accompaniments", {"staff_id": S2, "iso_year": 2026, "iso_week": 43,
                                                         "course_ids": [C1], "visit_ids": []})
    api = FakeApi({"/accompaniments": {"items": [{"staff_id": S2, "visit": {"id": V1}, "course": None}]}})
    before = impl.snapshot(api, o)
    inv = impl.inverse(o, before)
    assert inv[0]["course_ids"] == [] and inv[0]["visit_ids"] == [V1]
    assert impl.verify(api, inv[0]) == []
    assert impl.verify(api, o)
    assert impl.validate({**o, "defaults": [{"weekday": 0}]})
