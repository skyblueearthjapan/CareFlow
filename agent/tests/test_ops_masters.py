"""rk/ops/masters.py の試験（偽の Api だけ・本番に触らない）。"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk.opsbase import ApiError  # noqa: E402
from rk.ops.masters import (  # noqa: E402
    NgStaffUpdate,
    PatientCreate,
    PatientStatus,
    PatientUpdate,
    PfvUpdate,
    PreferenceUpdate,
    StaffHoursUpdate,
    StaffMasterUpdate,
)

PID = "11111111-1111-1111-1111-111111111111"
SID = "22222222-2222-2222-2222-222222222222"
TPL = "33333333-3333-3333-3333-333333333333"
OFFICE = "44444444-4444-4444-4444-444444444444"


class FakeServer:
    """患者 1 名・職員 1 名の状態を持ち、画面と同じ API を真似る偽の Api。"""

    def __init__(self):
        self.calls = []
        self.patient = {"id": PID, "name": "試験 花子", "status": "active", "sex_restriction": None,
                        "requires_multiple_staff": False, "visit_category": "psychiatric",
                        "weekly_pattern": {"frequency_per_week": 2, "preferred_weekdays": ["Tue", "Fri"]}}
        self.pfv = {"normal": [
            {"id": "r1", "patient_id": PID, "mode": "normal", "weekday": 1, "start_time": "09:30:00",
             "duration_min": 35, "course_template_id": TPL, "sub_office_id": None, "slot_index": 0,
             "is_pinned": True, "movability": "locked", "created_at": "x", "updated_at": "x"},
            {"id": "r2", "patient_id": PID, "mode": "normal", "weekday": 4, "start_time": "14:00:00",
             "duration_min": 35, "course_template_id": None, "sub_office_id": None, "slot_index": 0,
             "is_pinned": False, "movability": "unknown", "created_at": "x", "updated_at": "x"},
        ], "special": []}
        self.ng = {}
        self.staff = {"id": SID, "name": "試験 職員", "status": "active", "qualification": "看護師"}
        self.shifts = [{"weekday": w, "is_on": w < 5, "start_time": "09:00" if w < 5 else None,
                        "end_time": "18:00" if w < 5 else None} for w in range(7)]

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if path == f"/patients/{PID}":
            return copy.deepcopy(self.patient)
        if path == f"/patients/{PID}/fixed-visits":
            mode = (params or {}).get("mode")
            rows = self.pfv["normal"] + self.pfv["special"] if mode is None else self.pfv[mode]
            return copy.deepcopy(rows)
        if path == f"/patients/{PID}/status-impact":
            return {"visits": {"total": 3}}
        if path == f"/patients/{PID}/ng-staff":
            return [{"staff_id": k, "staff_name": "x", "note": v} for k, v in self.ng.items()]
        if path == f"/staff/{SID}":
            return copy.deepcopy(self.staff)
        if path == f"/staff/{SID}/shifts":
            return {"shifts": copy.deepcopy(self.shifts)}
        if path == "/patients":
            return [copy.deepcopy(self.patient)] if (params or {}).get("offset", 0) == 0 else []
        raise ApiError(404, path)

    def call(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "PATCH" and path == f"/patients/{PID}":
            assert "status" not in body
            self.patient.update(body)
            return copy.deepcopy(self.patient)
        if method == "PUT" and path == f"/patients/{PID}/fixed-visits":
            rows = []
            for i, it in enumerate(body["items"]):
                rows.append({**it, "id": f"n{i}", "patient_id": PID, "mode": body["mode"],
                             "start_time": it["start_time"] + ":00", "created_at": "y", "updated_at": "y"})
            self.pfv[body["mode"]] = rows
            return {"items": rows, "warnings": [], "week_sync": None}
        if method == "POST" and path == f"/patients/{PID}/status-change":
            self.patient["status"] = body["status"]
            if body.get("remove_schedule"):
                self.pfv = {"normal": [], "special": []}
            return {"patient": self.patient, "direction": "none"}
        if path.startswith(f"/patients/{PID}/ng-staff/"):
            sid = path.rsplit("/", 1)[1]
            if method == "PUT":
                self.ng[sid] = body.get("note")
                return {"staff_id": sid, "note": body.get("note")}
            if sid not in self.ng:
                raise ApiError(404, "Not found")
            del self.ng[sid]
            return None
        if method == "PUT" and path == f"/staff/{SID}/shifts":
            self.shifts = copy.deepcopy(body["shifts"])
            return {"shifts": self.shifts}
        if method == "PATCH" and path == f"/staff/{SID}":
            self.staff.update(body)
            return self.staff
        if method == "POST" and path == "/patients":
            return {**body, "id": "new-id"}
        raise ApiError(404, path)


def run(impl, api, op):
    """rk apply の 1 件分と同じ順: validate → snapshot → execute → check_response → verify。"""
    assert impl.validate(op) == []
    before = impl.snapshot(api, op)
    resp = impl.execute(api, op)
    assert impl.check_response(op, resp) == []
    assert impl.verify(api, op) == []
    return before


def pfv_items_of(server, mode="normal"):
    return sorted((r["weekday"], r["start_time"][:5], r["duration_min"], r["course_template_id"], r["movability"])
                  for r in server.pfv[mode])


# --- patient-update -----------------------------------------------------------


def test_patient_update_mapping_and_roundtrip():
    s, impl = FakeServer(), PatientUpdate()
    op = {"op": "patient-update", "patient_id": PID, "changes": {"sex_restriction": "female_only",
                                                                 "requires_multiple_staff": True}}
    assert impl.request(op) == ("PATCH", f"/patients/{PID}", op["changes"])
    before = run(impl, s, op)
    assert s.patient["sex_restriction"] == "female_only"
    inv = impl.inverse(op, before)
    assert inv == [{"op": "patient-update", "patient_id": PID, "label": inv[0]["label"],
                    "changes": {"sex_restriction": None, "requires_multiple_staff": False}}]
    run(impl, s, inv[0])
    assert s.patient["sex_restriction"] is None and s.patient["requires_multiple_staff"] is False


@pytest.mark.parametrize("changes, word", [
    ({"status": "admitted"}, "patient-status"),
    ({"name": "別名"}, "氏名"),
    ({"weekly_pattern": {}}, "preference-update"),
    ({"visit_category": None}, "visit_category"),
    ({"address": "千葉市"}, "lat"),
    ({"foo": 1}, "変えられる項目ではありません"),
    ({"address": "千葉市", "lat": 35.6, "lng": 140.1}, "visit_same_as_insurance: false"),
    ({"visit_same_as_insurance": None}, "visit_same_as_insurance は true / false"),
])
def test_patient_update_validate_rejects(changes, word):
    errs = PatientUpdate().validate({"patient_id": PID, "changes": changes})
    assert any(word in e for e in errs), errs


@pytest.mark.parametrize("changes", [
    # 訪問先だけを別の場所へ（座標つき）
    {"address": "千葉市", "lat": 35.6, "lng": 140.1, "visit_same_as_insurance": False},
    # 保険証の住所だけ（座標のきまりは訪問先にだけかかる）
    {"insurance_address": "千葉市稲毛区"},
    {"visit_same_as_insurance": True},
])
def test_patient_update_two_addresses_accepted(changes):
    assert PatientUpdate().validate({"patient_id": PID, "changes": changes}) == []


def test_patient_update_inverse_drops_null_same_flag():
    inv = PatientUpdate().inverse(
        {"patient_id": PID, "changes": {"visit_same_as_insurance": False}},
        {"fields": {"visit_same_as_insurance": None}},
    )
    assert inv is None


class _AddressApi:
    """住所まわりだけの小さな偽物: GET は今の患者、PATCH は決まった 422 を返す。"""

    def __init__(self, detail=None):
        self.detail = detail
        self.patient = {"name": "患者", "insurance_address": "保険証の住所", "visit_same_as_insurance": True,
                        "address": "保険証の住所", "lat": 35.6, "lng": 140.1, "note": "備考"}

    def get(self, path, params=None):
        return dict(self.patient)

    def call(self, method, path, body=None):
        raise ApiError(422, {"detail": self.detail})


def test_patient_update_snapshot_keeps_all_address_fields_for_undo():
    impl = PatientUpdate()
    op = {"patient_id": PID, "changes": {"insurance_address": "引っ越し先"}, "label": "保険証の住所"}
    before = impl.snapshot(_AddressApi(), op)
    assert set(before["fields"]) == {"insurance_address", "visit_same_as_insurance", "address", "lat", "lng"}
    [inv] = impl.inverse(op, before)
    assert inv["changes"]["address"] == "保険証の住所" and inv["changes"]["lat"] == 35.6
    assert impl.validate(inv) == []  # 戻しの案も検査を通る
    # 住所に関係しない変更はその項目だけ
    assert set(impl.snapshot(_AddressApi(), {"patient_id": PID, "changes": {"note": "x"}})["fields"]) == {"note"}


def test_patient_update_explains_address_422():
    impl = PatientUpdate()
    op = {"patient_id": PID, "changes": {"insurance_address": "どこか"}}
    msg = "訪問先の住所から位置が見つかりませんでした。緯度・経度を入れてください。"
    with pytest.raises(ApiError) as ei:
        impl.execute(_AddressApi(msg), op)
    assert ei.value.status == 422 and msg in str(ei.value) and "visit_same_as_insurance" in str(ei.value)
    # 住所と関係ない 422 はそのまま
    with pytest.raises(ApiError) as ei:
        impl.execute(_AddressApi("別の理由"), op)
    assert "visit_same_as_insurance" not in str(ei.value)


# --- patient-status -------------------------------------------------------------


def test_patient_status_end_requires_explicit_remove_flag():
    impl = PatientStatus()
    errs = impl.validate({"patient_id": PID, "to_status": "cancelled"})
    assert any("remove_schedule" in e for e in errs)
    assert impl.validate({"patient_id": PID, "to_status": "admitted", "remove_schedule": True})
    assert impl.validate({"patient_id": PID, "to_status": "bogus"})


def test_patient_status_end_with_remove_is_backup_only():
    s, impl = FakeServer(), PatientStatus()
    op = {"op": "patient-status", "patient_id": PID, "to_status": "cancelled", "remove_schedule": True,
          "from_date": "2026-10-13"}
    method, path, body = impl.request(op)
    assert (method, path) == ("POST", f"/patients/{PID}/status-change")
    assert body["remove_schedule"] is True and body["status"] == "cancelled" and body["from_date"] == "2026-10-13"
    before = run(impl, s, op)
    assert len(before["fixed_visits"]) == 2  # 消える枠の写しを残す
    assert impl.inverse(op, before) is None
    assert "バックアップ" in impl.undo_note(op, before)
    assert impl.undoable is False


def test_patient_status_end_without_remove_sends_false():
    _, _, body = PatientStatus().request({"patient_id": PID, "to_status": "cancelled", "remove_schedule": False})
    assert body["remove_schedule"] is False


def test_patient_status_inverse_only_when_no_schedule_link():
    impl = PatientStatus()
    op = {"patient_id": PID, "to_status": "suspended"}
    assert impl.inverse(op, {"status": "active"}) is None  # 稼働→休止は予定の取消が伴う
    inv = impl.inverse({"patient_id": PID, "to_status": "admitted"}, {"status": "suspended"})
    assert inv and inv[0]["to_status"] == "suspended"  # 非稼働どうしは戻せる


# --- pfv-update -------------------------------------------------------------------


def _cur_items():
    return [
        {"weekday": 1, "start_time": "09:30", "duration_min": 35, "course_template_id": TPL, "slot_index": 0,
         "movability": "locked", "is_pinned": True},
        {"weekday": 4, "start_time": "14:00", "duration_min": 35, "course_template_id": None, "slot_index": 0,
         "movability": "unknown", "is_pinned": False},
    ]


def test_pfv_update_mapping_and_roundtrip():
    s, impl = FakeServer(), PfvUpdate()
    original = pfv_items_of(s)
    items = _cur_items()
    items[1]["start_time"] = "15:00"
    items.append({"weekday": 2, "start_time": "10:00", "duration_min": 35, "course_template_id": TPL,
                  "slot_index": 0, "movability": "unknown", "is_pinned": False})
    op = {"op": "pfv-update", "patient_id": PID, "mode": "normal", "items": items}
    method, path, body = impl.request(op)
    assert (method, path) == ("PUT", f"/patients/{PID}/fixed-visits")
    assert body["change_scope"] == "pattern_only" and "iso_year" not in body
    assert body["items"][0]["movability"] == "locked"  # 赤は運ぶ（unknown に戻さない）
    before = run(impl, s, op)
    assert len(s.pfv["normal"]) == 3
    inv = impl.inverse(op, before)
    assert inv and inv[0]["change_scope"] == "pattern_only"
    run(impl, s, inv[0])
    assert pfv_items_of(s) == original


def test_pfv_update_week_scope_not_undoable():
    impl = PfvUpdate()
    op = {"patient_id": PID, "mode": "normal", "items": _cur_items(), "change_scope": "pattern_and_week",
          "iso_year": 2026, "iso_week": 43}
    assert impl.validate(op) == []
    _, _, body = impl.request(op)
    assert body["iso_year"] == 2026 and body["iso_week"] == 43
    assert impl.inverse(op, {"mode": "normal", "items": []}) is None
    assert "バックアップ" in impl.undo_note(op)


@pytest.mark.parametrize("mut, word", [
    (lambda it: it[1].pop("movability"), "movability"),
    (lambda it: it[1].update(start_time="07:30"), "08:00"),
    (lambda it: it[1].update(start_time="14:03"), "5 分"),
    (lambda it: it[1].update(weekday=6), "日曜"),
    (lambda it: it[1].update(weekday=1), "重複"),
    (lambda it: it[1].update(is_pinned=True), "is_pinned"),
])
def test_pfv_update_validate_rejects(mut, word):
    items = _cur_items()
    mut(items)
    errs = PfvUpdate().validate({"patient_id": PID, "mode": "normal", "items": items})
    assert any(word in e for e in errs), errs


def test_pfv_update_refuses_ack_and_red_pin_toggle():
    impl = PfvUpdate()
    assert impl.validate({"patient_id": PID, "mode": "normal", "items": _cur_items(),
                          "acknowledge_constraint_warnings": True})
    s = FakeServer()
    items = _cur_items()
    items[0].update(movability="unknown", is_pinned=False)  # 赤を外そうとする
    with pytest.raises(RkError):
        impl.execute(s, {"patient_id": PID, "mode": "normal", "items": items})
    items = _cur_items()
    items.append({"weekday": 0, "start_time": "10:00", "duration_min": 35, "slot_index": 0,
                  "movability": "locked", "is_pinned": True})  # 新しい枠を赤にしようとする
    with pytest.raises(RkError):
        impl.execute(s, {"patient_id": PID, "mode": "normal", "items": items})
    assert not [c for c in s.calls if c[0] == "PUT"]


def test_pfv_update_red_slot_course_and_duration_change_allowed():
    s, impl = FakeServer(), PfvUpdate()
    items = _cur_items()
    items[0].update(course_template_id=None, duration_min=45)  # 赤のままコース・長さを変える
    run(impl, s, {"op": "pfv-update", "patient_id": PID, "mode": "normal", "items": items})
    assert [c for c in s.calls if c[0] == "PUT"]


@pytest.mark.parametrize("field, value", [("start_time", "10:00"), ("weekday", 2)])
def test_pfv_update_red_slot_weekday_time_change_refused(field, value):
    s, impl = FakeServer(), PfvUpdate()
    items = _cur_items()
    items[0][field] = value
    with pytest.raises(RkError):
        impl.execute(s, {"patient_id": PID, "mode": "normal", "items": items})
    assert not [c for c in s.calls if c[0] == "PUT"]


def test_pfv_update_pin_change_needs_explicit_approval():
    s, impl = FakeServer(), PfvUpdate()
    items = _cur_items()
    items[0].update(movability="unknown", is_pinned=False)
    run(impl, s, {"op": "pfv-update", "patient_id": PID, "mode": "normal", "items": items,
                  "pin_change_approved_by": "PO"})
    assert s.pfv["normal"][0]["movability"] == "unknown"


# --- preference-update ------------------------------------------------------------


def test_preference_update_roundtrip():
    s, impl = FakeServer(), PreferenceUpdate()
    original = copy.deepcopy(s.patient["weekly_pattern"])
    op = {"op": "preference-update", "patient_id": PID,
          "weekly_pattern": {"frequency_per_week": 3, "preferred_weekdays": ["Mon", "Wed", "Fri"],
                             "time_type": "時間帯", "preferred_start": "09:00", "preferred_end": "12:00"}}
    assert impl.request(op) == ("PATCH", f"/patients/{PID}", {"weekly_pattern": op["weekly_pattern"]})
    before = run(impl, s, op)
    inv = impl.inverse(op, before)
    run(impl, s, inv[0])
    assert s.patient["weekly_pattern"] == original


def test_preference_update_validate():
    impl = PreferenceUpdate()
    assert impl.validate({"patient_id": PID, "weekly_pattern": {"frequency_per_week": 9}})
    assert impl.validate({"patient_id": PID, "weekly_pattern": {"preferred_weekdays": ["火"]}})
    assert impl.validate({"patient_id": PID}) and impl.validate({"patient_id": PID, "weekly_pattern": None}) == []


# --- ng-staff-update ----------------------------------------------------------------


def test_ng_staff_add_remove_and_inverse():
    s, impl = FakeServer(), NgStaffUpdate()
    add = {"op": "ng-staff-update", "patient_id": PID, "staff_id": SID, "action": "add", "note": "ご本人の希望"}
    assert impl.request(add) == ("PUT", f"/patients/{PID}/ng-staff/{SID}", {"note": "ご本人の希望"})
    before = run(impl, s, add)
    inv = impl.inverse(add, before)
    assert inv[0]["action"] == "remove"
    run(impl, s, inv[0])
    assert s.ng == {}
    # 既にある NG の解除 → 戻すと理由メモごと戻る
    s.ng[SID] = "前の理由"
    rm = {"op": "ng-staff-update", "patient_id": PID, "staff_id": SID, "action": "remove"}
    assert impl.request(rm)[0] == "DELETE"
    before = run(impl, s, rm)
    inv = impl.inverse(rm, before)
    run(impl, s, inv[0])
    assert s.ng == {SID: "前の理由"}


# --- staff-hours-update / staff-master-update ------------------------------------------


def test_staff_hours_update_roundtrip_and_never_patches():
    s, impl = FakeServer(), StaffHoursUpdate()
    original = copy.deepcopy(s.shifts)
    new = copy.deepcopy(s.shifts)
    new[2].update(start_time="10:00", end_time="16:00")
    op = {"op": "staff-hours-update", "staff_id": SID, "shifts": new}
    assert impl.request(op)[:2] == ("PUT", f"/staff/{SID}/shifts")
    before = run(impl, s, op)
    inv = impl.inverse(op, before)
    run(impl, s, inv[0])
    assert s.shifts == original
    assert not [c for c in s.calls if c[0] == "PATCH"]  # 職員の PATCH は呼ばない（退職が戻る罠）


def test_staff_hours_validate():
    impl = StaffHoursUpdate()
    assert impl.validate({"staff_id": SID, "shifts": []})
    bad = [{"weekday": w, "is_on": True, "start_time": "18:00", "end_time": "09:00"} for w in range(7)]
    assert impl.validate({"staff_id": SID, "shifts": bad})


def test_staff_master_update_only_changed_fields():
    s, impl = FakeServer(), StaffMasterUpdate()
    op = {"op": "staff-master-update", "staff_id": SID, "changes": {"qualification": "准看護師"}}
    assert impl.request(op) == ("PATCH", f"/staff/{SID}", {"qualification": "准看護師"})
    before = run(impl, s, op)
    inv = impl.inverse(op, before)
    assert inv[0]["changes"] == {"qualification": "看護師"}
    assert impl.validate({"staff_id": SID, "changes": {"name": "x"}})
    assert impl.validate({"staff_id": SID, "changes": {"role": "manager"}})
    retire = {"staff_id": SID, "changes": {"status": "retired"}}
    assert impl.inverse(retire, {"status": "active", "fields": {"status": "active"}}) is None


# --- patient-create -------------------------------------------------------------------


def test_patient_create():
    s, impl = FakeServer(), PatientCreate()
    assert impl.validate({"patient": {"name": "新規 太郎"}})  # 拠点なし
    assert impl.validate({"patient": {"name": "新規 太郎", "primary_office_id": OFFICE, "age": 80}})
    op = {"op": "patient-create", "patient": {"name": "試験 花子", "primary_office_id": OFFICE, "status": "pending"}}
    assert impl.validate(op) == []
    assert impl.request(op) == ("POST", "/patients", op["patient"])
    snap = impl.snapshot(s, op)
    assert snap["same_name"] and snap["same_name"][0]["id"] == PID  # 同じ氏名がいることを記録
    assert impl.check_response(op, {"id": "new", "name": "試験 花子"}) == []
    assert impl.check_response(op, {}) != []
    assert impl.inverse(op, snap) is None and impl.undoable is False


def test_patient_create_two_addresses_need_coords_for_visit():
    impl = PatientCreate()
    base = {"name": "新規 太郎", "primary_office_id": OFFICE}
    # 「保険証の住所と同じ」→ 保険証の住所が訪問先になるので座標が要る
    assert any("lat" in e for e in impl.validate(
        {"patient": {**base, "insurance_address": "千葉市", "visit_same_as_insurance": True}}))
    assert impl.validate({"patient": {**base, "insurance_address": "千葉市", "lat": 35.6, "lng": 140.1}}) == []
    # 「別の場所」でも訪問先が空ならサーバーが「同じ」に戻す → 座標が要る
    assert any("lat" in e for e in impl.validate(
        {"patient": {**base, "insurance_address": "千葉市", "visit_same_as_insurance": False}}))
    assert impl.validate({"patient": {**base, "insurance_address": "千葉市", "visit_same_as_insurance": False,
                                      "address": "別の場所", "lat": 35.6, "lng": 140.1}}) == []


# --- 訪問頻度・NG 曜日・訪問する週（biweekly-monthly-design-2026-10-30） ----------------


def test_preference_update_validate_frequency_keys():
    impl = PreferenceUpdate()

    def errs(wp):
        return impl.validate({"patient_id": PID, "weekly_pattern": wp})

    assert errs({"visit_frequency": "biweekly", "biweekly_anchor": "2026-11-02", "frequency_per_week": 1}) == []
    assert errs({"visit_frequency": "monthly", "visit_weeks": "1,3"}) == []
    assert errs({"ng_weekdays": ["Mon", "Wed"]}) == []
    # 起点の週が無い隔週は保存できる（毎週作られる・今の希望を写すだけの変更を止めない）
    assert errs({"visit_frequency": "biweekly"}) == []
    assert errs({"visit_frequency": "隔週"})
    assert any("月曜" in e for e in errs({"visit_frequency": "biweekly", "biweekly_anchor": "2026-11-04"}))
    assert any("YYYY-MM-DD" in e for e in errs({"biweekly_anchor": "11/2"}))
    assert errs({"visit_weeks": "1,6"})
    assert errs({"visit_weeks": "1,1"})
    assert errs({"visit_weeks": "第1"})
    assert errs({"ng_weekdays": ["月"]})
    assert errs({"ng_weekdays": "Mon"})


def test_pfv_update_visit_week():
    impl = PfvUpdate()
    base = {"weekday": 1, "start_time": "09:30", "duration_min": 35, "course_template_id": None,
            "sub_office_id": None, "slot_index": 0, "is_pinned": False, "movability": "unknown"}
    ok = {"patient_id": PID, "mode": "normal", "items": [{**base, "visit_week": "A"},
                                                         {**base, "weekday": 4, "visit_week": "B"}]}
    assert impl.validate(ok) == []
    assert impl.validate({**ok, "items": [base]}) == []  # 省略は今の値を引き継ぐ
    bad = impl.validate({**ok, "items": [{**base, "visit_week": "C"}]})
    assert any("visit_week" in e for e in bad)
    # GET の行の visit_week は運ぶ（undo で A/B が消えない）・無ければ書かない（API が引き継ぐ）
    from rk.ops.masters import pfv_item
    assert pfv_item({**base, "visit_week": "B"})["visit_week"] == "B"
    assert "visit_week" not in pfv_item(base)
