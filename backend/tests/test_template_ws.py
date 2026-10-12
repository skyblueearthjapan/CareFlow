"""型の作業場 (template_ws) のテスト: 下書き・動かす・履歴(取り消す/やり直す)・版の競合・最適化・登録(① 型 / ② 週).

小さな世界: 拠点 1・コース A/B・職員 2 名(月曜出勤)・患者 4 名(月曜の固定訪問)・枠の無い方 1 名。
"""

# ruff: noqa: N812, N806, N803, N818, B905

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import (
    Course,
    CourseTemplate,
    Office,
    OfficeFeatureFlag,
    Patient,
    PatientFixedVisit,
    Staff,
    User,
    Visit,
)
from app.models.staff import StaffShift
from app.services.template_ws import optimise as O
from app.services.template_ws import service as S

API = "/api/v1/template-ws"


async def _world(db, *, flag: bool = True):
    office = Office(name="稲毛", short_label="稲", code="ina")
    db.add(office)
    await db.flush()
    tA = CourseTemplate(office_id=office.id, label="A", capacity_mon=6)
    tB = CourseTemplate(office_id=office.id, label="B", capacity_mon=6)
    db.add_all([tA, tB])
    staff = []
    for i, (name, sex) in enumerate([("山田 花子", "female"), ("佐藤 太郎", "male")]):
        s = Staff(name=name, code=f"S{i + 1}", sex=sex, status="active", role="staff", primary_office_id=office.id)
        db.add(s)
        staff.append(s)
    await db.flush()
    for s in staff:
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True, start_time=time(9), end_time=time(18)))
    pats = []
    spots = [(35.6000, 140.1000), (35.6010, 140.1010), (35.6500, 140.1500), (35.6510, 140.1510)]
    for i, (la, ln) in enumerate(spots):
        p = Patient(code=f"P{i + 1}", name=f"患者{i + 1} 様", status="active", primary_office_id=office.id,
                    lat=la, lng=ln, special_week_active=[],
                    weekly_pattern={"time_type": "時間帯", "preferred_start": "09:30", "preferred_end": "13:00",
                                    "service_minutes": 35})
        db.add(p)
        pats.append(p)
    cand = Patient(code="P9", name="新人 様", status="active", primary_office_id=office.id, lat=35.601, lng=140.101,
                   special_week_active=[],
                   weekly_pattern={"time_type": "午前", "preferred_weekdays": ["mon"], "frequency_per_week": 1,
                                   "service_minutes": 35})
    db.add(cand)
    await db.flush()
    # 全員コース A に入っていて、P3/P4 は遠い → B へ動かす余地がある
    for p, hh in zip(pats, (9, 10, 11, 12)):
        db.add(PatientFixedVisit(patient_id=p.id, mode="normal", weekday=0, start_time=time(hh, 30),
                                 duration_min=35, course_template_id=tA.id, slot_index=0))
    if flag:
        db.add(OfficeFeatureFlag(office_id=office.id, feature_key="template_workspace",
                                 enabled_at=datetime.now(UTC)))
    admin = User(email="adm@example.com", password_hash=hash_password("pw"), role="admin")
    db.add(admin)
    await db.commit()
    return office, (tA, tB), staff, pats, cand, admin


def _h(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)}"}


async def _create(client, admin, office, name="d1") -> dict:
    r = await client.post(f"{API}/drafts", json={"office_id": str(office.id), "name": name}, headers=_h(admin))
    assert r.status_code == 201, r.text
    return r.json()


def _slot(view, code):
    return next(s for d in view["days"] for s in d["slots"] if s["code"] == code and not s["new"])


@pytest.mark.asyncio
async def test_flag_off_hides_everything(db, client):
    office, _t, _s, _p, _c, admin = await _world(db, flag=False)
    r = await client.get(f"{API}/availability", headers=_h(admin))
    assert r.json() == {"office_ids": []}
    r = await client.post(f"{API}/drafts", json={"office_id": str(office.id), "name": "x"}, headers=_h(admin))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_admin_only(db, client):
    office, *_rest, admin = await _world(db)
    staff_user = User(email="s@example.com", password_hash=hash_password("pw"), role="staff")
    db.add(staff_user)
    await db.commit()
    r = await client.get(f"{API}/availability", headers=_h(staff_user))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_copies_master_and_reads(db, client):
    office, (tA, tB), _s, pats, _c, admin = await _world(db)
    v = await _create(client, admin, office)
    assert v["version"] == 1 and v["office"]["label"] == "稲"
    mon = next(d for d in v["days"] if d["weekday"] == 0)
    assert len(mon["slots"]) == 4
    assert {c["label"] for c in mon["courses"]} == {"稲A", "稲B"}  # 出勤 2 名 → コース 2 本
    assert all(not s["changed"] for s in mon["slots"])
    # 候補(枠の無い方)は「置く前」のトレイに出る
    assert [t["code"] for t in v["tray"]] == ["P9"]
    # ETag
    r = await client.get(f"{API}/drafts/{v['id']}", headers=_h(admin))
    tag = r.headers["etag"]
    r2 = await client.get(f"{API}/drafts/{v['id']}", headers={**_h(admin), "If-None-Match": tag})
    assert r2.status_code == 304


@pytest.mark.asyncio
async def test_move_validates_and_records_steps_undo_redo(db, client):
    office, (tA, tB), _s, pats, _c, admin = await _world(db)
    v = await _create(client, admin, office)
    key = _slot(v, "P3")["key"]
    body = {"key": key, "course_id": str(tB.id), "start": "12:00", "expected_version": 1, "actor": "agent",
            "reason": "遠い方を B へ"}
    r = await client.post(f"{API}/drafts/{v['id']}/move", json=body, headers=_h(admin))
    assert r.status_code == 200, r.text
    nv = r.json()["view"]
    assert nv["version"] == 2
    s = _slot(nv, "P3")
    assert s["course_id"] == str(tB.id) and s["start"] == 720 and s["changed"] and s["held"]
    assert nv["latest_step"]["actor_label"] == "らく助エージェント" and nv["latest_step"]["reason"] == "遠い方を B へ"
    # 古い版 → 409
    r = await client.post(f"{API}/drafts/{v['id']}/move", json={**body, "expected_version": 1}, headers=_h(admin))
    assert r.status_code == 409
    # 希望の範囲の外 → 422 / 曜日は動かせない(キーを変えても存在しない)
    r = await client.post(f"{API}/drafts/{v['id']}/move",
                          json={**body, "start": "16:00", "expected_version": 2}, headers=_h(admin))
    assert r.status_code == 422 and "希望の範囲" in r.json()["detail"]
    # undo / redo
    r = await client.post(f"{API}/drafts/{v['id']}/undo", json={"expected_version": 2}, headers=_h(admin))
    assert r.status_code == 200, r.text
    uv = r.json()["view"]
    assert not _slot(uv, "P3")["changed"] and uv["can_redo"] and uv["version"] == 3
    r = await client.post(f"{API}/drafts/{v['id']}/redo", json={"expected_version": 3}, headers=_h(admin))
    rv = r.json()["view"]
    assert _slot(rv, "P3")["course_id"] == str(tB.id)
    # 新しい操作でやり直しの枝は消える
    await client.post(f"{API}/drafts/{v['id']}/undo", json={"expected_version": 4}, headers=_h(admin))
    r = await client.post(f"{API}/drafts/{v['id']}/move", json={**body, "expected_version": 5, "start": "12:05"},
                          headers=_h(admin))
    assert not r.json()["view"]["can_redo"]


@pytest.mark.asyncio
async def test_red_pin_keeps_time(db, client):
    office, (tA, tB), _s, pats, _c, admin = await _world(db)
    f = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == pats[2].id))
    f.movability = "locked"
    f.is_pinned = True
    await db.commit()
    v = await _create(client, admin, office)
    s = _slot(v, "P3")
    assert s["locked"]
    r = await client.post(f"{API}/drafts/{v['id']}/move", json={
        "key": s["key"], "course_id": str(tB.id), "start": "12:05", "expected_version": 1}, headers=_h(admin))
    assert r.status_code == 422 and "赤ピン" in r.json()["detail"]
    r = await client.post(f"{API}/drafts/{v['id']}/move", json={
        "key": s["key"], "course_id": str(tB.id), "start": "11:30", "expected_version": 1}, headers=_h(admin))
    assert r.status_code == 200  # コースだけ替える(時刻は元のまま)


@pytest.mark.asyncio
async def test_whatif_and_add_remove_patient(db, client):
    office, *_rest, admin = await _world(db)
    v = await _create(client, admin, office)
    r = await client.post(f"{API}/drafts/{v['id']}/whatif", json={
        "staff_count": {"mon": 1}, "expected_version": 1, "actor": "agent", "reason": "月曜を 1 人減らしたら"},
        headers=_h(admin))
    assert r.status_code == 200, r.text
    nv = r.json()["view"]
    assert nv["scenario"]["staff_count"] == {"0": 1}
    mon = next(d for d in nv["days"] if d["weekday"] == 0)
    assert len(mon["courses"]) == 1
    r = await client.post(f"{API}/drafts/{v['id']}/undo", json={"expected_version": 2}, headers=_h(admin))
    assert r.json()["view"]["scenario"]["staff_count"] == {}
    r = await client.post(f"{API}/drafts/{v['id']}/add-patient", json={
        "code": "P9", "expected_version": 3, "actor": "human"}, headers=_h(admin))
    assert r.status_code == 200 and "P9" in r.json()["view"]["scenario"]["add_patients"]
    r = await client.post(f"{API}/drafts/{v['id']}/remove-patient", json={"code": "P9", "expected_version": 4},
                          headers=_h(admin))
    assert r.status_code == 200 and "P9" not in r.json()["view"]["scenario"]["add_patients"]


@pytest.mark.asyncio
async def test_inputs_view(db, client):
    office, *_rest, admin = await _world(db)
    v = await _create(client, admin, office)
    r = await client.get(f"{API}/drafts/{v['id']}/inputs?weekday=0", headers=_h(admin))
    assert r.status_code == 200
    j = r.json()
    assert j["rules"]["speed_kmh"] == 20 and j["rules"]["buffer_min"] == 8
    assert len(j["matrix"]["minutes"]) == 4 and j["matrix"]["minutes"][0][0] == 0
    assert j["matrix"]["minutes"][0][2] > j["matrix"]["minutes"][0][1]  # 遠い方ほど時間がかかる
    assert {s["rule"] for s in j["slots"]} <= {"red", "fixed", "window"}
    assert {c["label"] for c in j["courses"]} == {"稲A", "稲B"}


@pytest.mark.asyncio
async def test_optimise_places_new_patient_and_balances(db, client):
    pytest.importorskip("ortools")
    office, (tA, tB), _s, pats, cand, admin = await _world(db)
    tb_id, admin_id, hdr = str(tB.id), admin.id, _h(admin)
    v = await _create(client, admin, office)
    await client.post(f"{API}/drafts/{v['id']}/add-patient", json={"code": "P9", "expected_version": 1},
                      headers=_h(admin))
    await db.rollback()
    res = await O.execute_optimise(db, __import__("uuid").UUID(v["id"]), admin_id, actor="agent", weekdays=[0],
                                   seconds=3, reason="試し", base_version=2)
    assert res["changed"] > 0
    r = await client.get(f"{API}/drafts/{v['id']}", headers=hdr)
    nv = r.json()
    mon = next(d for d in nv["days"] if d["weekday"] == 0)
    courses = {s["course_id"] for s in mon["slots"] if s["placed"]}
    assert tb_id in courses  # 全員 A から、B にも分かれる
    assert any(s["new"] and s["placed"] for s in mon["slots"])  # 枠の無い方が置かれた
    assert nv["latest_step"]["kind"] == "optimise" and nv["latest_step"]["actor"] == "agent"
    # 計算の途中に版が進んでいたら 409
    with pytest.raises(S.TemplateConflict):
        await O.execute_optimise(db, __import__("uuid").UUID(v["id"]), admin_id, actor="human", weekdays=[0],
                                 seconds=1, reason=None, base_version=1)


@pytest.mark.asyncio
async def test_optimise_job_endpoint_polling(db, client):
    pytest.importorskip("ortools")
    office, *_rest, admin = await _world(db)
    v = await _create(client, admin, office)
    r = await client.post(f"{API}/drafts/{v['id']}/optimise", json={
        "weekdays": ["mon"], "seconds": 2, "expected_version": 1, "actor": "human"}, headers=_h(admin))
    assert r.status_code == 202, r.text
    jid = r.json()["id"]
    import asyncio

    for _ in range(60):
        j = (await client.get(f"{API}/jobs/{jid}", headers=_h(admin))).json()
        if j["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.5)
    assert j["status"] == "done", j


@pytest.mark.asyncio
async def test_commit_to_master_dry_run_and_apply(db, client):
    office, (tA, tB), staff, pats, _c, admin = await _world(db)
    hdr = _h(admin)
    ids = {"tA": tA.id, "tB": tB.id, "p": [x.id for x in pats], "s": [x.id for x in staff]}
    v = await _create(client, admin, office)
    r = await client.get(f"{API}/drafts/{v['id']}/commit-preview", headers=hdr)
    assert r.json()["ready"] is False and "違う枠がありません" in r.json()["blockers"][0]
    key = _slot(v, "P3")["key"]
    await client.post(f"{API}/drafts/{v['id']}/move", json={
        "key": key, "course_id": str(ids["tB"]), "start": "11:30", "expected_version": 1}, headers=hdr)
    pv = (await client.get(f"{API}/drafts/{v['id']}/commit-preview", headers=hdr)).json()
    assert pv["ready"] and pv["counts"]["course"] == 1 and pv["counts"]["time"] == 0 and pv["counts"]["patients"] == 1
    assert "次に「週を生成」" in pv["note"]
    # confirm 無しは断る
    r = await client.post(f"{API}/drafts/{v['id']}/commit", json={"expected_version": 2}, headers=hdr)
    assert r.status_code == 422
    r = await client.post(f"{API}/drafts/{v['id']}/commit", json={"expected_version": 2, "confirm": True},
                          headers=hdr)
    assert r.status_code == 200, r.text
    db.expire_all()
    f = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == ids["p"][2]))
    assert f.course_template_id == ids["tB"]
    # 登録済みは変えられない
    r = await client.post(f"{API}/drafts/{v['id']}/move", json={
        "key": key, "course_id": str(ids["tA"]), "start": "11:30", "expected_version": 3}, headers=hdr)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_commit_refuses_when_master_changed(db, client):
    office, (tA, tB), staff, pats, _c, admin = await _world(db)
    hdr = _h(admin)
    ids = {"tA": tA.id, "tB": tB.id, "p": [x.id for x in pats], "s": [x.id for x in staff]}
    v = await _create(client, admin, office)
    key = _slot(v, "P3")["key"]
    await client.post(f"{API}/drafts/{v['id']}/move", json={
        "key": key, "course_id": str(ids["tB"]), "start": "11:30", "expected_version": 1}, headers=hdr)
    f = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == ids["p"][0]))
    f.start_time = time(9, 45)  # 下書きを作った後にマスターが変わった
    await db.commit()
    r = await client.post(f"{API}/drafts/{v['id']}/commit", json={"expected_version": 2, "confirm": True},
                          headers=hdr)
    assert r.status_code == 422 and "変わった" in r.json()["detail"]
    db.expire_all()
    f3 = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == ids["p"][2]))
    assert f3.course_template_id == ids["tA"]  # 何も書かれていない


async def _make_week(db, office, tA, pats, staffA):
    iso = date(2026, 10, 12).isocalendar()
    cA = Course(iso_year=iso.year, iso_week=iso.week, weekday=0, code="A", office_id=office.id, template_id=tA.id,
                assigned_staff_id=staffA.id)
    db.add(cA)
    await db.flush()
    for p, hh in zip(pats, (9, 10, 11, 12)):
        db.add(Visit(patient_id=p.id, primary_staff_id=staffA.id, visit_date=date(2026, 10, 12),
                     start_time=time(hh, 30), end_time=time(hh, 30 + 35 - 60 if False else 59), type="regular",
                     status="planned", source="auto", course_id=cA.id))
    await db.commit()
    return iso


@pytest.mark.asyncio
async def test_apply_to_one_week_dry_run_apply_and_undo(db, client):
    office, (tA, tB), staff, pats, _c, admin = await _world(db)
    hdr = _h(admin)
    ids = {"tA": tA.id, "tB": tB.id, "p": [x.id for x in pats], "s": [x.id for x in staff]}
    iso = await _make_week(db, office, tA, pats, staff[0])
    v = await _create(client, admin, office)
    key = _slot(v, "P3")["key"]
    await client.post(f"{API}/drafts/{v['id']}/move", json={
        "key": key, "course_id": str(ids["tB"]), "start": "11:30", "expected_version": 1}, headers=hdr)
    q = f"iso_year={iso.year}&iso_week={iso.week}"
    pv = (await client.get(f"{API}/drafts/{v['id']}/week-preview?{q}", headers=hdr)).json()
    assert pv["counts"]["course"] == 1 and pv["counts"]["add"] == 0
    assert any("担当がいない" in n["note"] or "作ります" in n["note"] for n in pv["course_notes"])
    # 空の週 → 断る
    r = await client.get(f"{API}/drafts/{v['id']}/week-preview?iso_year=2030&iso_week=5", headers=hdr)
    assert r.status_code == 422 and "まだ作られていません" in r.json()["detail"]
    # 版違いのハッシュは断る
    r = await client.post(f"{API}/drafts/{v['id']}/week-apply", json={
        "iso_year": iso.year, "iso_week": iso.week, "week_hash": "bad", "confirm": True}, headers=hdr)
    assert r.status_code == 409
    r = await client.post(f"{API}/drafts/{v['id']}/week-apply", json={
        "iso_year": iso.year, "iso_week": iso.week, "week_hash": pv["week_hash"], "confirm": True},
        headers=hdr)
    assert r.status_code == 200, r.text
    db.expire_all()
    vis = await db.scalar(select(Visit).where(Visit.patient_id == ids["p"][2]))
    cB = await db.get(Course, vis.course_id)
    assert cB.template_id == ids["tB"] and vis.primary_staff_id is None  # 担当なしのまま
    f = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == ids["p"][2]))
    assert f.course_template_id == ids["tA"]  # 型は変わらない
    # アプリの「戻る」で戻る
    r = await client.post("/api/v1/schedule/v2/op-log/undo",
                          json={"iso_year": iso.year, "iso_week": iso.week}, headers=hdr)
    assert r.status_code == 200, r.text
    db.expire_all()
    vis = await db.scalar(select(Visit).where(Visit.patient_id == ids["p"][2]))
    cc = await db.get(Course, vis.course_id)
    assert cc.template_id == ids["tA"] and vis.primary_staff_id == ids["s"][0]


@pytest.mark.asyncio
async def test_create_from_draft_copies_only_unchanged_patients_and_same_office(db, client):
    office, (tA, tB), _s, pats, _c, admin = await _world(db)
    hdr, tb_id, office_id = _h(admin), str(tB.id), office.id
    p1_id = pats[0].id
    d1 = await _create(client, admin, office, "d1")
    key = _slot(d1, "P3")["key"]
    r = await client.post(f"{API}/drafts/{d1['id']}/move", json={
        "key": key, "course_id": tb_id, "start": "11:30", "expected_version": 1}, headers=hdr)
    assert r.status_code == 200, r.text
    # 元の下書きを作った後に、P1 のマスターが他の人によって変わった
    f = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p1_id))
    f.start_time = time(9, 45)
    await db.commit()
    r = await client.post(f"{API}/drafts", json={"office_id": str(office_id), "name": "d2", "from_draft_id": d1["id"]},
                          headers=hdr)
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert _slot(v2, "P3")["course_id"] == tb_id  # マスターが変わっていない方は元の下書きの動かし方を写す
    assert _slot(v2, "P1")["start"] == 9 * 60 + 45  # 変わった方は今のマスターから作り直す(黙って戻さない)
    assert v2["scenario"] == d1["scenario"]
    # 別の拠点の下書きからは写せない
    office2 = Office(name="都賀", short_label="都", code="tsuga")
    db.add(office2)
    await db.flush()
    db.add(OfficeFeatureFlag(office_id=office2.id, feature_key="template_workspace", enabled_at=datetime.now(UTC)))
    await db.commit()
    r = await client.post(f"{API}/drafts", json={"office_id": str(office2.id), "name": "x", "from_draft_id": d1["id"]},
                          headers=hdr)
    assert r.status_code == 422 and "別の拠点" in r.json()["detail"]


@pytest.mark.asyncio
async def test_week_preview_skips_multi_staff_visit_and_set_visit_course_keeps_second_vsa(db, client):
    from app.models import VisitStaffAssignment
    from app.services import op_log_service as OL

    office, (tA, tB), staff, pats, _c, admin = await _world(db)
    iso = await _make_week(db, office, tA, pats, staff[0])
    ids = {"tB": tB.id, "p": [x.id for x in pats], "s": [x.id for x in staff], "hdr": _h(admin)}
    v3 = await db.scalar(select(Visit).where(Visit.patient_id == ids["p"][2]))
    v3_id, course_a = v3.id, v3.course_id
    db.add(VisitStaffAssignment(visit_id=v3_id, staff_id=ids["s"][0]))
    db.add(VisitStaffAssignment(visit_id=v3_id, staff_id=ids["s"][1]))  # 2 人目の担当
    await db.commit()
    d = await _create(client, admin, office)
    key = _slot(d, "P3")["key"]
    await client.post(f"{API}/drafts/{d['id']}/move", json={
        "key": key, "course_id": str(ids["tB"]), "start": "11:30", "expected_version": 1}, headers=ids["hdr"])
    pv = (await client.get(f"{API}/drafts/{d['id']}/week-preview?iso_year={iso.year}&iso_week={iso.week}",
                           headers=ids["hdr"])).json()
    assert pv["counts"]["course"] == 0 and any("2 名以上" in s["reason"] for s in pv["skipped"])
    # op の undo/redo: 主担当の VSA だけ入れ替え、2 人目の VSA は残す
    payload = {"op": "set_visit_course", "visit_id": str(v3_id), "course_id": str(course_a),
               "staff_id": str(ids["s"][0]), "manual": False}
    await OL._set_visit_course(db, {**payload, "staff_id": str(ids["s"][0])})
    await db.commit()
    rows = {r.staff_id for r in (await db.scalars(select(VisitStaffAssignment).where(
        VisitStaffAssignment.visit_id == v3_id))).all()}
    assert rows == {ids["s"][0], ids["s"][1]}
    await OL._assert_visit_course(db, payload)  # 一致なら通る
    with pytest.raises(OL.OpLogConflictError):
        await OL._assert_visit_course(db, {**payload, "staff_id": str(ids["s"][1])})  # 担当が違えば断る
