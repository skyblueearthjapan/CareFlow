"""月の実績の API (/api/v1/monthly-actual/*) と反映のジョブ.

権限 (管理者のみ)・判定の一覧・反映する／しない・反映 (dry_run が RPA に正しい items を送る・
本番の後に読み取り直して確かめる・二重送信の止め・過去の月だけ)・予約・取り直し・Excel の列。
RPA (KaipokeClient) はモックに差し替える (本物のカイポケにはつながない)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Notification, Patient, Staff, User, Visit, VisitCheckin
from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot
from app.models.kaipoke_job import KaipokeJob
from app.models.monthly_actual import MonthlyActualRow
from app.services import kaipoke_client
from app.services.kaipoke import sources
from app.services.monthly_actual import service as svc
from app.services.monthly_actual.export_xlsx import ASK_HEADERS, MAIN_HEADERS, STAFF_HEADERS

JST = ZoneInfo("Asia/Tokyo")
MONTH = "2026-10"
URL = f"/api/v1/monthly-actual/{MONTH}"
HEADER = (
    "職員名１,職種１,職員名２,職種２,職員名３,日付,利用者,業務種別,サービス内容,"
    "開始時間,終了時間,提供時間（分）,記録Ⅱ開始時間,記録Ⅱ終了時間"
)


def _csv(lines: list[tuple[str, int, str, str, str]]) -> str:
    """(職員, 日, 利用者, 開始, 終了) → 実績 CSV。"""
    body = []
    for staff, day, patient, start, end in lines:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        minutes = (eh * 60 + em) - (sh * 60 + sm)
        body.append(
            f"{staff},看護師,,,,{day},{patient},医療保険,訪問看護基本療養費,"
            f"{start},{end},{minutes},,"
        )
    return "\n".join([HEADER, *body])


#: カイポケの実績 (反映前)。
KP_BEFORE = [
    ("佐藤　花子", 1, "青木　さくら", "10:00", "10:40"),  # 一致
    ("佐藤　花子", 1, "石田　健", "11:00", "11:35"),  # 反映する (3 分)
    ("佐藤　花子", 2, "木下　誠", "16:15", "16:50"),  # 30 分未満 → 到着＋35
    ("佐藤　花子", 3, "斉藤　大地", "17:20", "17:55"),  # 1 時間以上 → 人が見る
    ("佐藤　花子", 6, "上野　ゆり", "13:00", "13:35"),  # カイポケだけ
]


class FakeKaipoke:
    """RPA のモック。``actual_time_apply`` に届いた payload を覚え、結果を返す。"""

    def __init__(self, *, busy: bool = False, export_lines: list | None = None) -> None:
        self.busy = busy
        self.export_lines = export_lines or KP_BEFORE
        self.apply_payloads: list[dict[str, Any]] = []
        self.exports: list[dict[str, Any]] = []
        self._job_id: str | None = None
        self._items: list[dict[str, Any]] = []
        self._dry_run = True

    async def status(self) -> dict[str, Any]:
        return {"current_task": {"running": self.busy}}

    async def actual_time_apply(self, payload, *, timeout=None) -> dict[str, Any]:
        self.apply_payloads.append(dict(payload))
        self._job_id = payload["job_id"]
        self._items = list(payload["items"])
        self._dry_run = payload["dry_run"]
        return {
            "success": True,
            "async": True,
            "job_id": payload["job_id"],
            "total": len(self._items),
            "dry_run": payload["dry_run"],
        }

    async def actual_time_result(self) -> dict[str, Any]:
        status = "dry_run_ok" if self._dry_run else "success"
        details = [{**item, "user": item["user_name"], "status": status} for item in self._items]
        return {
            "success": True,
            "status": "completed",
            "job_id": self._job_id,
            "result": {
                "month": MONTH,
                "dry_run": self._dry_run,
                "total": len(details),
                "success": 0 if self._dry_run else len(details),
                "dry_run_ok": len(details) if self._dry_run else 0,
                "skipped": 0,
                "failed": 0,
                "error": 0,
                "details": details,
            },
        }

    async def export(self, payload, *, timeout=None) -> dict[str, Any]:
        self.exports.append(dict(payload))
        return {
            "result": {
                "success": True,
                "division": payload["division"],
                "csv_content": _csv(self.export_lines),
                "row_count": len(self.export_lines),
            }
        }


@pytest.fixture(autouse=True)
def _fast(monkeypatch) -> None:
    monkeypatch.setattr(svc, "APPLY_POLL_SECONDS", 0.0)
    monkeypatch.setattr(sources, "RETRY_INTERVAL_SECONDS", 0.0)
    yield
    kaipoke_client.set_test_client(None)


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _user(db, role: str) -> User:
    user = User(
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("x"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _visit(db, staff: Staff, patient: Patient, day: int, arr: str, dep: str | None) -> Visit:
    visit_day = date(2026, 10, day)
    ah, am = (int(x) for x in arr.split(":"))
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=visit_day,
        start_time=time(ah, 0),
        end_time=time(ah, 30),
        type="regular",
        status="completed",
    )
    db.add(visit)
    await db.flush()
    for kind, value in (("arrival", arr), ("departure", dep)):
        if value is None:
            continue
        h, m = (int(x) for x in value.split(":"))
        db.add(
            VisitCheckin(
                visit_id=visit.id,
                patient_id=patient.id,
                staff_id=staff.id,
                kind=kind,
                scanned_at=datetime.combine(visit_day, time(h, m), tzinfo=JST).astimezone(UTC),
                match_status="match",
                checkin_source="qr",
                threshold_snapshot={"v": 1},
            )
        )
    await db.commit()
    return visit


async def _seed(db, *, with_snapshot: bool = True) -> dict[str, Visit]:
    nurse = Staff(name="佐藤 花子")
    db.add(nurse)
    patients = {
        name: Patient(code=f"P{i:03d}", name=name)
        for i, name in enumerate(["青木 さくら", "石田 健", "木下 誠", "斉藤 大地", "武藤 榛那"])
    }
    db.add_all(patients.values())
    await db.commit()
    visits = {
        "青木": await _visit(db, nurse, patients["青木 さくら"], 1, "10:00", "10:40"),
        "石田": await _visit(db, nurse, patients["石田 健"], 1, "11:03", "11:41"),
        "木下": await _visit(db, nurse, patients["木下 誠"], 2, "16:16", "16:34"),
        "斉藤": await _visit(db, nurse, patients["斉藤 大地"], 3, "16:04", "16:40"),
        "武藤": await _visit(db, nurse, patients["武藤 榛那"], 5, "15:32", None),
    }
    if with_snapshot:
        db.add(
            KaipokeCsvSnapshot(
                office_id=None,
                month=MONTH,
                week_start=None,
                fetched_at=datetime(2026, 11, 1, 7, 29, tzinfo=UTC),
                csv_text=_csv(KP_BEFORE),
                row_count=len(KP_BEFORE),
                source_op="plan-actual",
                division="actual",
            )
        )
        await db.commit()
    return visits


def _by_patient(rows: list[dict]) -> dict[str, dict]:
    return {r["patient"]: r for r in rows}


ENDPOINTS = [
    ("get", URL, None),
    ("post", f"{URL}/refresh", None),
    ("post", f"{URL}/judge", None),
    ("patch", f"{URL}/rows/{uuid.uuid4()}", {"selected": True}),
    ("patch", f"/api/v1/monthly-actual/rows/{uuid.uuid4()}", {"selected": True}),
    ("post", f"{URL}/apply", {"dry_run": True}),
    ("get", f"{URL}/apply/status", None),
    ("post", f"{URL}/schedule", {}),
    ("delete", f"{URL}/schedule", None),
    ("get", f"{URL}/export.xlsx", None),
    ("post", "/api/v1/monthly-actual/scheduled/run", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "url", "body"), ENDPOINTS)
async def test_admin_only(client, db, method, url, body) -> None:
    staff_user = await _user(db, "staff")
    kwargs = {"json": body} if body is not None else {}
    res = await client.request(method.upper(), url, headers=_bearer(staff_user), **kwargs)
    assert res.status_code == 403, res.text
    res = await client.request(method.upper(), url, **kwargs)
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_get_judges_from_latest_actual_snapshot(client, db) -> None:
    admin = await _user(db, "admin")
    res = await client.get(URL, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["run"] is None and res.json()["rows"] == []

    await _seed(db)
    res = await client.get(URL, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_past_month"] is True
    assert body["counts"] == {
        "review": 1,
        "apply": 2,
        "missing": 1,
        "kaipoke_only": 1,
        "match": 1,
        "total": 6,
    }
    rows = _by_patient(body["rows"])
    assert rows["青木 さくら"]["status"] == "match"
    assert rows["石田 健"]["status"] == "apply" and rows["石田 健"]["selected"] is True
    assert (rows["木下 誠"]["new_start"], rows["木下 誠"]["new_end"]) == ("16:16", "16:51")
    assert rows["斉藤 大地"]["status"] == "review" and rows["斉藤 大地"]["selected"] is False
    assert rows["斉藤 大地"]["selectable"] is True
    assert rows["武藤 榛那"]["status"] == "missing" and rows["武藤 榛那"]["new_end"] == "16:07"
    assert rows["上野 ゆり"]["status"] == "kaipoke_only"
    assert body["selected_count"] == 2
    assert body["kaipoke"]["row_count"] == len(KP_BEFORE)

    bad = await client.get("/api/v1/monthly-actual/2026-13", headers=_bearer(admin))
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_patch_row_selection_and_keeps_it_on_rejudge(client, db) -> None:
    admin = await _user(db, "admin")
    await _seed(db)
    rows = _by_patient((await client.get(URL, headers=_bearer(admin))).json()["rows"])

    review_id = rows["斉藤 大地"]["id"]
    res = await client.patch(
        f"{URL}/rows/{review_id}", json={"selected": True}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    assert res.json()["selected"] is True and res.json()["selected_by_user"] is True

    match_id = rows["青木 さくら"]["id"]
    res = await client.patch(
        f"{URL}/rows/{match_id}", json={"selected": True}, headers=_bearer(admin)
    )
    assert res.status_code == 422
    res = await client.patch(
        f"{URL}/rows/{uuid.uuid4()}", json={"selected": True}, headers=_bearer(admin)
    )
    assert res.status_code == 404

    # 月を付けない形でも同じ。別の月を名指しすると 404。
    res = await client.patch(
        f"/api/v1/monthly-actual/rows/{rows['石田 健']['id']}",
        json={"selected": False},
        headers=_bearer(admin),
    )
    assert res.status_code == 200 and res.json()["selected"] is False
    res = await client.patch(
        f"/api/v1/monthly-actual/2026-09/rows/{review_id}",
        json={"selected": True},
        headers=_bearer(admin),
    )
    assert res.status_code == 404

    # 判定し直しても、管理者が変えた選択は引き継ぐ。
    res = await client.post(f"{URL}/judge", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert _by_patient(res.json()["rows"])["斉藤 大地"]["selected"] is True


@pytest.mark.asyncio
async def test_apply_dry_run_sends_only_selected_items(client, db, monkeypatch) -> None:
    admin = await _user(db, "admin")
    await _user(db, "admin")  # 通知は管理者全員へ
    await _seed(db)
    await client.get(URL, headers=_bearer(admin))
    fake = FakeKaipoke()
    kaipoke_client.set_test_client(fake)  # type: ignore[arg-type]
    started: list[uuid.UUID] = []

    async def fake_run(*, job_id, client):
        started.append(job_id)

    monkeypatch.setattr(svc, "run_apply_job", fake_run)
    res = await client.post(f"{URL}/apply", json={}, headers=_bearer(admin))
    assert res.status_code == 202, res.text
    assert res.json()["dry_run"] is True and res.json()["total"] == 2
    job_id = uuid.UUID(res.json()["job_id"])
    assert started == [job_id]

    # 動いている間は二重に送らない (409)。
    res = await client.post(f"{URL}/apply", json={}, headers=_bearer(admin))
    assert res.status_code == 409

    job = await svc.run_apply(db, fake, job_id)  # type: ignore[arg-type]
    assert job is not None and job.status == "completed", job and job.result_summary
    assert len(fake.apply_payloads) == 1
    payload = fake.apply_payloads[0]
    assert payload["month"] == MONTH and payload["dry_run"] is True
    assert payload["job_id"] == f"careflow-actual-{job_id}"
    items = sorted(payload["items"], key=lambda i: i["date"])
    assert [{k: v for k, v in i.items() if k != "item_id"} for i in items] == [
        {
            "user_name": "石田 健",
            "date": "2026-10-01",
            "old_start": "11:00",
            "old_end": "11:35",
            "staff1": "佐藤 花子",
            "new_start": "11:03",
            "new_end": "11:41",
        },
        {
            "user_name": "木下 誠",
            "date": "2026-10-02",
            "old_start": "16:15",
            "old_end": "16:50",
            "staff1": "佐藤 花子",
            "new_start": "16:16",
            "new_end": "16:51",
        },
    ]
    assert fake.exports == []  # 試しのときは読み取り直さない

    status_res = await client.get(f"{URL}/apply/status", headers=_bearer(admin))
    assert status_res.status_code == 200, status_res.text
    body = status_res.json()
    assert body["running"] is False and body["counts"]["dry_run_ok"] == 2
    assert {r["apply_status"] for r in body["rows"]} == {"dry_run_ok"}
    assert body["last_apply"]["dry_run"] is True

    notes = (
        await db.scalars(select(Notification).where(Notification.reference_id == job_id))
    ).all()
    assert len(notes) == 2
    assert "試し" in notes[0].title and "登録の直前まで進めた 2 件" in (notes[0].body or "")


@pytest.mark.asyncio
async def test_apply_real_then_verifies_and_does_not_send_twice(client, db, monkeypatch) -> None:
    admin = await _user(db, "admin")
    await _seed(db)
    await client.get(URL, headers=_bearer(admin))
    after = [
        ("佐藤　花子", 1, "青木　さくら", "10:00", "10:40"),
        ("佐藤　花子", 1, "石田　健", "11:03", "11:41"),
        ("佐藤　花子", 2, "木下　誠", "16:16", "16:51"),
        ("佐藤　花子", 3, "斉藤　大地", "17:20", "17:55"),
        ("佐藤　花子", 6, "上野　ゆり", "13:00", "13:35"),
    ]
    fake = FakeKaipoke(export_lines=after)
    kaipoke_client.set_test_client(fake)  # type: ignore[arg-type]

    async def fake_run(*, job_id, client):
        return None

    monkeypatch.setattr(svc, "run_apply_job", fake_run)
    res = await client.post(f"{URL}/apply", json={"dry_run": False}, headers=_bearer(admin))
    assert res.status_code == 202, res.text
    job_id = uuid.UUID(res.json()["job_id"])
    job = await svc.run_apply(db, fake, job_id)  # type: ignore[arg-type]
    assert job is not None and job.status == "completed", job and job.result_summary
    assert fake.exports and fake.exports[0]["division"] == "actual"

    view = (await client.get(URL, headers=_bearer(admin))).json()
    run = view["run"]
    assert run["state"] == "verified"
    assert run["verify_result"]["applied"] == 2 and run["verify_result"]["matched"] == 2
    assert run["last_apply"]["counts"]["success"] == 2
    rows = _by_patient(view["rows"])
    assert rows["石田 健"]["status"] == "match" and rows["木下 誠"]["status"] == "match"

    # もう送る行が無い (反映した行は一致になり、人が見るは選んでいない)。
    res = await client.post(f"{URL}/apply", json={"dry_run": False}, headers=_bearer(admin))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_apply_guards(client, db, monkeypatch) -> None:
    admin = await _user(db, "admin")
    await _seed(db)
    await client.get(URL, headers=_bearer(admin))

    kaipoke_client.set_test_client(FakeKaipoke(busy=True))  # type: ignore[arg-type]
    res = await client.post(f"{URL}/apply", json={}, headers=_bearer(admin))
    assert res.status_code == 409 and res.json()["detail"] == "kaipoke busy"

    kaipoke_client.set_test_client(FakeKaipoke())  # type: ignore[arg-type]
    res = await client.post("/api/v1/monthly-actual/2099-01/apply", json={}, headers=_bearer(admin))
    assert res.status_code == 422 and "終わった月" in res.json()["detail"]

    # 選んでいない行を名指しすると 422。
    rows = _by_patient((await client.get(URL, headers=_bearer(admin))).json()["rows"])
    res = await client.post(
        f"{URL}/apply", json={"row_ids": [rows["斉藤 大地"]["id"]]}, headers=_bearer(admin)
    )
    assert res.status_code == 422

    # 同じカイポケの行を 2 件が指していたら送らない。
    stored = (await db.scalars(select(MonthlyActualRow))).all()
    targets = [r for r in stored if r.status == "apply"]
    targets[1].item_key = targets[0].item_key
    await db.commit()
    res = await client.post(f"{URL}/apply", json={}, headers=_bearer(admin))
    assert res.status_code == 422 and "2 件が指して" in res.json()["detail"]


@pytest.mark.asyncio
async def test_export_xlsx_has_the_four_sheets_and_columns(client, db) -> None:
    admin = await _user(db, "admin")
    await _seed(db)
    await client.get(URL, headers=_bearer(admin))
    res = await client.get(f"{URL}/export.xlsx", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert "spreadsheetml" in res.headers["content-type"]
    wb = load_workbook(BytesIO(res.content))
    assert wb.sheetnames == ["10月の実績", "要確認", "看護師別", "読み方"]
    main = wb["10月の実績"]
    assert tuple(c.value for c in main[1]) == MAIN_HEADERS
    assert main.max_row == 1 + len(KP_BEFORE)
    values = [[c.value for c in row] for row in main.iter_rows(min_row=2)]
    aoki = next(v for v in values if v[2] == "青木 さくら")
    assert aoki[9] == "○"
    kinoshita = next(v for v in values if v[2] == "木下 誠")
    assert "到着＋35 分" in kinoshita[10]
    ask = wb["要確認"]
    assert tuple(c.value for c in ask[1]) == ASK_HEADERS
    assert ask.max_row == 1 + 3  # 人が見る・カイポケに無い・カイポケだけ
    staff = wb["看護師別"]
    assert tuple(c.value for c in staff[1]) == STAFF_HEADERS
    assert staff["A2"].value == "佐藤 花子" and staff["B2"].value == len(KP_BEFORE)
    assert "2026 年 10 月" in wb["読み方"]["A1"].value


@pytest.mark.asyncio
async def test_schedule_and_cron_start_the_apply(client, db, monkeypatch) -> None:
    admin = await _user(db, "admin")
    await _seed(db)
    await client.get(URL, headers=_bearer(admin))
    res = await client.post(f"{URL}/schedule", json={}, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    at = datetime.fromisoformat(res.json()["scheduled_at"]).astimezone(JST)
    assert (at.hour, at.minute) == (22, 0)
    assert res.json()["scheduled_dry_run"] is False

    # まだ時刻前: cron は何も始めない。
    kaipoke_client.set_test_client(FakeKaipoke())  # type: ignore[arg-type]
    started: list[uuid.UUID] = []

    async def fake_run(*, job_id, client):
        started.append(job_id)

    monkeypatch.setattr(svc, "run_apply_job", fake_run)
    res = await client.post("/api/v1/monthly-actual/scheduled/run", headers=_bearer(admin))
    assert res.status_code == 200 and res.json()["started"] == []

    tick = await svc.run_due_schedules(db, now=datetime.now(UTC) + timedelta(days=2))
    assert len(tick.started) == 1 and tick.started[0]["dry_run"] is False
    job = await db.get(KaipokeJob, uuid.UUID(tick.started[0]["job_id"]))
    assert job is not None and (job.params or {})["dry_run"] is False
    view = (await client.get(URL, headers=_bearer(admin))).json()
    assert view["run"]["scheduled_at"] is None and view["run"]["state"] == "applying"

    res = await client.post(f"{URL}/schedule", json={}, headers=_bearer(admin))
    assert res.status_code == 409  # 反映の途中は予約できない
    res = await client.delete(f"{URL}/schedule", headers=_bearer(admin))
    assert res.status_code == 200 and res.json()["scheduled_at"] is None


@pytest.mark.asyncio
async def test_refresh_fetches_actual_and_rejudges(client, db, monkeypatch) -> None:
    admin = await _user(db, "admin")
    await _seed(db, with_snapshot=False)
    res = await client.post(f"{URL}/judge", headers=_bearer(admin))
    assert res.status_code == 422  # まだ実績を読み取っていない

    fake = FakeKaipoke()
    kaipoke_client.set_test_client(fake)  # type: ignore[arg-type]
    started: list[uuid.UUID] = []

    async def fake_run(*, job_id, client):
        started.append(job_id)

    monkeypatch.setattr(svc, "run_refresh_job", fake_run)
    res = await client.post(f"{URL}/refresh", headers=_bearer(admin))
    assert res.status_code == 202, res.text
    job_id = uuid.UUID(res.json()["job_id"])
    assert started == [job_id]
    res = await client.post(f"{URL}/refresh", headers=_bearer(admin))
    assert res.status_code == 409  # 動いている間は 409

    job = await svc.run_refresh(db, fake, job_id)  # type: ignore[arg-type]
    assert job is not None and job.status == "completed", job and job.result_summary
    assert fake.exports == [
        {"month": MONTH, "division": "actual", "async": False},
    ]
    body = (await client.get(URL, headers=_bearer(admin))).json()
    assert body["counts"]["total"] == 6
    assert body["kaipoke"]["source_op"] == "monthly-actual"


@pytest.mark.asyncio
async def test_status_reads_rpa_result_into_rows(client, db, monkeypatch) -> None:
    admin = await _user(db, "admin")
    await _seed(db)
    await client.get(URL, headers=_bearer(admin))
    fake = FakeKaipoke()
    kaipoke_client.set_test_client(fake)  # type: ignore[arg-type]

    async def fake_run(*, job_id, client):
        return None

    monkeypatch.setattr(svc, "run_apply_job", fake_run)
    res = await client.post(f"{URL}/apply", json={}, headers=_bearer(admin))
    job_id = uuid.UUID(res.json()["job_id"])
    # 背景が RPA へ送った直後の姿を作る (RPA は受け付けて結果を持っている)。
    rows = (await db.scalars(select(MonthlyActualRow).where(MonthlyActualRow.selected))).all()
    await fake.actual_time_apply(
        {
            "job_id": svc.rpa_job_id(job_id),
            "dry_run": True,
            "items": svc.build_rpa_items(list(rows)),
        }
    )
    assert await svc._touch(db, job_id, rpa_started=True)

    body = (await client.get(f"{URL}/apply/status", headers=_bearer(admin))).json()
    # 背景の読み直しが生きている間は、状態の API はジョブを決着させない (行だけ書く)。
    assert body["running"] is True
    assert body["counts"]["dry_run_ok"] == 2
    assert {r["apply_status"] for r in body["rows"]} == {"dry_run_ok"}
