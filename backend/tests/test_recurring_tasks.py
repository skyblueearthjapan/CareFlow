"""毎月のやること (patient-tasks-recurring-design-2026-11-02) の試験.

1. 対象の判定: 区分・個別に外す・稼働中だけ (休止・入院・開始前は作らない)。
2. 冪等: 見回りを 2 回流しても同じ方・同じ月は 1 件。見回りが止まった日の抜けを作る。
3. 設定日 (1・15・28 日) → その日以降の最初の訪問に出る。作る日の前は作らない。
4. 同じ月に同じ項目を手で付けた方には作らない (取消は数えない)。
5. 止めた・変えた後も作り済みの分は変わらない。まとめて取り消すは未完了だけ。
6. 月の途中で稼働になった方は次の見回りで作る。
7. 項目を「使わない」にした設定は作らない。保存したとき今月の分を作る。
8. 権限 (管理者だけ)・GET /tasks に設定と月が出る・試し表示。
9. migration 0105 の chain と SQLite の upgrade/downgrade。
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, time, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select

from app.core.security import create_access_token, hash_password
from app.models import Patient, PatientTask, RecurringTaskRule, Staff, User, Visit
from app.services import recurring_tasks as svc
from app.services.patient_tasks import jst_today

T = jst_today()
THIS_MONTH = T.replace(day=1)
NEXT_MONTH = svc.next_month(T)


async def _user(db, name: str, role: str, staff_id=None) -> User:
    u = User(
        email=f"{name}@example.com",
        password_hash=hash_password("x"),
        role=role,
        staff_id=staff_id,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _h(u: User) -> dict[str, str]:
    token = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _patient(
    db, code: str, *, category: str = "psychiatric", status: str = "active"
) -> Patient:
    p = Patient(code=code, name=f"患者{code}", visit_category=category, status=status)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _staff(db, name: str) -> Staff:
    s = Staff(name=name, role="staff", status="active")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _visit(db, patient: Patient, d: date, *, primary: Staff | None = None) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=time(10, 0),
        end_time=time(10, 30),
        type="visit",
        status="planned",
        primary_staff_id=primary.id if primary else None,
        required_staff_count=1,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _item(client, admin, name: str) -> str:
    res = await client.post("/api/v1/task-item-types", json={"name": name}, headers=_h(admin))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _rule(client, admin, **body) -> dict:
    payload = {
        "target_categories": ["psychiatric"],
        "start_month": NEXT_MONTH.isoformat(),
        "day_of_month": 1,
        **body,
    }
    res = await client.post("/api/v1/recurring-task-rules", json=payload, headers=_h(admin))
    assert res.status_code == 201, res.text
    return res.json()


async def _rule_row(db, rule_id: str) -> RecurringTaskRule:
    from uuid import UUID

    return (
        await db.scalars(
            select(RecurringTaskRule)
            .where(RecurringTaskRule.id == UUID(rule_id))
            .execution_options(populate_existing=True)
        )
    ).one()


async def _tasks(db, rule_id: str | None = None) -> list[PatientTask]:
    from uuid import UUID

    stmt = select(PatientTask).execution_options(populate_existing=True)
    if rule_id is not None:
        stmt = stmt.where(PatientTask.recurring_rule_id == UUID(rule_id))
    return list((await db.scalars(stmt)).all())


async def _run(client, admin, rule_id: str, month: date) -> int:
    res = await client.post(
        f"/api/v1/recurring-task-rules/{rule_id}/run?month={month:%Y-%m}", headers=_h(admin)
    )
    assert res.status_code == 200, res.text
    return res.json()["created_count"]


# ---------------------------------------------------------------------------
# 対象の判定と冪等
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_targets_by_category_exclusion_and_status(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _patient(db, "A")
    b = await _patient(db, "B")
    out = await _patient(db, "C")  # 個別に外す
    await _patient(db, "G1", category="general")
    await _patient(db, "G2", category="general")
    await _patient(db, "S", status="suspended")
    await _patient(db, "H", status="admitted")
    await _patient(db, "P", status="pending")
    item = await _item(client, admin, "項目A")

    rule = await _rule(client, admin, item_type_id=item, excluded_patient_ids=[str(out.id)])
    assert rule["target_count"] == 2
    assert [e["patient_id"] for e in rule["excluded_patients"]] == [str(out.id)]
    assert rule["created_now"] == 0  # 最初に作る月は来月

    assert await _run(client, admin, rule["id"], NEXT_MONTH) == 2
    rows = await _tasks(db, rule["id"])
    assert {t.patient_id for t in rows} == {a.id, b.id}
    assert all(t.recurring_month == NEXT_MONTH for t in rows)
    assert all(t.start_date == NEXT_MONTH for t in rows)
    assert all(str(t.item_type_id) == item for t in rows)

    # 2 回目は作らない (同じ設定・同じ方・同じ月は 1 件)
    assert await _run(client, admin, rule["id"], NEXT_MONTH) == 0
    assert len(await _tasks(db, rule["id"])) == 2

    # 一覧の対象の人数・未完了の数
    res = await client.get("/api/v1/recurring-task-rules", headers=_h(admin))
    (row,) = res.json()
    assert row["target_count"] == 2
    assert row["open_count"] == 2
    assert row["item_type_name"] == "項目A"


@pytest.mark.asyncio
async def test_run_due_is_idempotent_and_catches_up(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    await _patient(db, "A")
    await _patient(db, "B")
    rule = await _rule(client, admin, memo="伝言だけ", start_month="2026-12-01", day_of_month=1)

    # 11 月 (最初に作る月より前) は作らない
    r = await svc.run_due(db, today=date(2026, 11, 30))
    assert r.created_total == 0

    # 見回りが 12/1・12/2 に止まり、12/3 に流れた → 抜けを作る (日付は今日)
    r = await svc.run_due(db, today=date(2026, 12, 3))
    await db.commit()
    assert r.created_total == 2
    assert r.by_rule == {rule["id"]: 2}
    rows = await _tasks(db, rule["id"])
    assert {t.recurring_month for t in rows} == {date(2026, 12, 1)}

    # 2 回目・次の日も作らない
    r = await svc.run_due(db, today=date(2026, 12, 3))
    r2 = await svc.run_due(db, today=date(2026, 12, 4))
    await db.commit()
    assert r.created_total == 0 and r2.created_total == 0
    assert len(await _tasks(db, rule["id"])) == 2

    # 次の月は新しく作る (先月の分が残っていても並べて作る・C1)
    r = await svc.run_due(db, today=date(2027, 1, 1))
    await db.commit()
    assert r.created_total == 2
    assert len(await _tasks(db, rule["id"])) == 4


@pytest.mark.asyncio
async def test_not_due_before_day_and_unique_index(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    await _patient(db, "A")
    rule = await _rule(client, admin, memo="x", start_month="2026-12-01", day_of_month=15)
    assert (await svc.run_due(db, today=date(2026, 12, 14))).created_total == 0
    r = await svc.run_due(db, today=date(2026, 12, 15))
    await db.commit()
    assert r.created_total == 1
    (t,) = await _tasks(db, rule["id"])
    assert t.start_date == date(2026, 12, 15)

    # DB の一意の索引でも 2 件目は落ちる
    db.add(
        PatientTask(
            patient_id=t.patient_id,
            start_date=t.start_date,
            memo="x",
            recurring_rule_id=t.recurring_rule_id,
            recurring_month=t.recurring_month,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("day", [1, 15, 28])
async def test_shows_on_first_visit_on_or_after_day(client, db, day: int) -> None:
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "A")
    m = NEXT_MONTH
    before = m.replace(day=day) - timedelta(days=1)
    if before >= m:
        await _visit(db, p, before, primary=s)
    target = await _visit(db, p, m.replace(day=day), primary=s)
    await _visit(db, p, m.replace(day=day) + timedelta(days=2), primary=s)

    rule = await _rule(client, admin, memo="伝言", day_of_month=day)
    assert await _run(client, admin, rule["id"], m) == 1
    res = await client.get("/api/v1/tasks", headers=_h(admin))
    (row,) = res.json()
    assert row["next_visit"]["visit_id"] == str(target.id)
    assert row["recurring_rule_id"] == rule["id"]
    assert row["recurring_month"] == m.isoformat()
    assert row["start_date"] == m.replace(day=day).isoformat()


# ---------------------------------------------------------------------------
# 手で付けた分・止めた・変えた・まとめて取り消す
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_patients_with_manual_task_same_item_same_month(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    manual = await _patient(db, "M")
    cancelled = await _patient(db, "X")
    other_month = await _patient(db, "O")
    other = await _patient(db, "Q")
    item = await _item(client, admin, "項目A")
    other_item = await _item(client, admin, "項目B")
    m = NEXT_MONTH

    async def add(p, start, iid):
        res = await client.post(
            "/api/v1/tasks",
            json={"patient_id": str(p.id), "start_date": start.isoformat(), "item_type_ids": [iid]},
            headers=_h(admin),
        )
        assert res.status_code == 201, res.text
        return res.json()["tasks"][0]["id"]

    await add(manual, m + timedelta(days=5), item)
    tid = await add(cancelled, m + timedelta(days=2), item)
    await client.post(f"/api/v1/tasks/{tid}/cancel", headers=_h(admin))
    await add(other_month, svc.next_month(m), item)
    await add(other, m, other_item)

    rule = await _rule(client, admin, item_type_id=item)
    assert await _run(client, admin, rule["id"], m) == 3
    made = {t.patient_id for t in await _tasks(db, rule["id"])}
    assert made == {cancelled.id, other_month.id, other.id}

    res = await client.get(
        f"/api/v1/recurring-task-rules/{rule['id']}/preview?month={m:%Y-%m}", headers=_h(admin)
    )
    assert res.status_code == 200, res.text
    by_patient = {r["patient_id"]: r["result"] for r in res.json()["rows"]}
    assert by_patient[str(manual.id)] == "manual"
    assert by_patient[str(other.id)] == "exists"


@pytest.mark.asyncio
async def test_stop_and_change_keep_made_tasks_and_cancel_open(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    a = await _patient(db, "A")
    await _patient(db, "B")
    await _visit(db, a, T, primary=s)
    rule = await _rule(client, admin, memo="最初の伝言", start_month=THIS_MONTH.isoformat())
    assert rule["created_now"] == 2  # 1 日から・今月から → 保存したときに今月の分を作る
    rows = await _tasks(db, rule["id"])
    done_task = next(t for t in rows if t.patient_id == a.id)
    res = await client.post(f"/api/v1/tasks/{done_task.id}/complete", json={}, headers=_h(admin))
    assert res.status_code == 200, res.text

    # 変えても作り済みの分はそのまま
    res = await client.patch(
        f"/api/v1/recurring-task-rules/{rule['id']}",
        json={"memo": "変えた伝言", "is_active": False},
        headers=_h(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is False
    assert res.json()["open_count"] == 1
    rows = await _tasks(db, rule["id"])
    assert {t.memo for t in rows} == {"最初の伝言"}
    assert all(t.cancelled_at is None for t in rows)

    # 止めた設定は見回りでも作らない・今すぐ作るも 409
    assert (await svc.run_due(db, today=NEXT_MONTH)).created_total == 0
    res = await client.post(f"/api/v1/recurring-task-rules/{rule['id']}/run", headers=_h(admin))
    assert res.status_code == 409

    # まとめて取り消す: 未完了だけ
    res = await client.post(
        f"/api/v1/recurring-task-rules/{rule['id']}/cancel-open", headers=_h(admin)
    )
    assert res.status_code == 200, res.text
    assert res.json()["cancelled_count"] == 1
    rows = {t.patient_id: t for t in await _tasks(db, rule["id"])}
    assert rows[a.id].done_at is not None and rows[a.id].cancelled_at is None
    assert all(t.cancelled_at is not None for pid, t in rows.items() if pid != a.id)

    # 取り消した方にも、同じ月にもう一度は作らない
    await client.patch(
        f"/api/v1/recurring-task-rules/{rule['id']}", json={"is_active": True}, headers=_h(admin)
    )
    assert len(await _tasks(db, rule["id"])) == 2


@pytest.mark.asyncio
async def test_mid_month_activation_creates_on_next_run(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    await _patient(db, "A")
    late = await _patient(db, "L", status="suspended")
    rule = await _rule(client, admin, memo="伝言", start_month="2026-12-01")
    assert (await svc.run_due(db, today=date(2026, 12, 1))).created_total == 1
    await db.commit()

    late.status = "active"
    await db.commit()
    r = await svc.run_due(db, today=date(2026, 12, 14))
    await db.commit()
    assert r.created_total == 1
    t = next(t for t in await _tasks(db, rule["id"]) if t.patient_id == late.id)
    assert t.recurring_month == date(2026, 12, 1)
    # 月の途中で作った分は、作った日から出す (作った日に「期日を過ぎた」にしない)
    assert t.start_date == date(2026, 12, 14)


@pytest.mark.asyncio
async def test_inactive_item_is_not_used(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    await _patient(db, "A")
    item = await _item(client, admin, "項目A")
    rule = await _rule(client, admin, item_type_id=item)
    await client.patch(
        f"/api/v1/task-item-types/{item}", json={"is_active": False}, headers=_h(admin)
    )
    assert (await svc.ensure_rule_month(db, await _rule_row(db, rule["id"]), NEXT_MONTH)) == 0
    res = await client.get("/api/v1/recurring-task-rules", headers=_h(admin))
    assert res.json()[0]["item_type_active"] is False
    # 使わない項目では新しく作れない
    res = await client.post(
        "/api/v1/recurring-task-rules",
        json={
            "item_type_id": item,
            "target_categories": ["psychiatric"],
            "start_month": NEXT_MONTH.isoformat(),
        },
        headers=_h(admin),
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_validation(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    base = {"target_categories": ["psychiatric"], "start_month": NEXT_MONTH.isoformat()}
    url = "/api/v1/recurring-task-rules"
    assert (await client.post(url, json=base, headers=_h(admin))).status_code == 422
    for bad in (
        {"memo": "x", "day_of_month": 29},
        {"memo": "x", "day_of_month": 0},
        {"memo": "x", "target_categories": []},
        {"memo": "x", "target_categories": ["other"]},
    ):
        res = await client.post(url, json={**base, **bad}, headers=_h(admin))
        assert res.status_code == 422, bad
    rule = await _rule(client, admin, memo="x", start_month="2026-12-15")
    assert rule["start_month"] == "2026-12-01"
    res = await client.patch(f"{url}/{rule['id']}", json={"memo": None}, headers=_h(admin))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_admin_only(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    us = await _user(db, "us", "staff", staff_id=s.id)
    rule = await _rule(client, admin, memo="x")
    url = "/api/v1/recurring-task-rules"
    calls = [
        client.get(url, headers=_h(us)),
        client.get(f"{url}/candidates?categories=psychiatric", headers=_h(us)),
        client.post(url, json={"memo": "x"}, headers=_h(us)),
        client.patch(f"{url}/{rule['id']}", json={"memo": "y"}, headers=_h(us)),
        client.get(f"{url}/{rule['id']}/preview", headers=_h(us)),
        client.post(f"{url}/{rule['id']}/run", headers=_h(us)),
        client.post(f"{url}/{rule['id']}/cancel-open", headers=_h(us)),
        client.post(f"{url}/run-due", headers=_h(us)),
    ]
    for c in calls:
        assert (await c).status_code == 403


@pytest.mark.asyncio
async def test_candidates_and_preview_rows(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _patient(db, "A")
    ex = await _patient(db, "E")
    h = await _patient(db, "H", status="admitted")
    await _patient(db, "C", status="cancelled")
    await _patient(db, "G", category="general")
    res = await client.get(
        "/api/v1/recurring-task-rules/candidates?categories=psychiatric", headers=_h(admin)
    )
    assert {r["patient_id"] for r in res.json()} == {str(a.id), str(ex.id), str(h.id)}

    rule = await _rule(client, admin, memo="x", excluded_patient_ids=[str(ex.id)])
    res = await client.get(
        f"/api/v1/recurring-task-rules/{rule['id']}/preview?month={NEXT_MONTH:%Y-%m}",
        headers=_h(admin),
    )
    body = res.json()
    results = {r["patient_id"]: r["result"] for r in body["rows"]}
    assert results == {str(a.id): "create", str(ex.id): "excluded", str(h.id): "not_active"}
    assert body["counts"] == {"create": 1, "excluded": 1, "not_active": 1}
    assert body["rows"][0]["visit"] is None  # 訪問が入ったら出ます
    # 試し表示では作らない
    assert await _tasks(db, rule["id"]) == []


@pytest.mark.asyncio
async def test_run_due_endpoint(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    await _patient(db, "A")
    await _rule(client, admin, memo="x", start_month=THIS_MONTH.isoformat(), is_active=False)
    res = await client.post("/api/v1/recurring-task-rules/run-due", headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["created_total"] == 0
    assert res.json()["month"] == THIS_MONTH.isoformat()


@pytest.mark.asyncio
async def test_deleted_excluded_patient_is_dropped_on_save(client, db) -> None:
    """外した後に削除された方の id が送り返されても保存できる (黙って捨てる)."""
    from datetime import UTC, datetime
    from uuid import uuid4

    admin = await _user(db, "adm", "admin")
    h = _h(admin)
    keep = str((await _patient(db, "K")).id)
    gone_id = (await _patient(db, "G")).id
    gone = str(gone_id)
    rule = await _rule(client, admin, memo="x", excluded_patient_ids=[keep, gone])
    await db.rollback()  # 自分の読み取りの取引を閉じてから書く (SQLite の接続を共有するため)
    await db.execute(
        sa.update(Patient).where(Patient.id == gone_id).values(deleted_at=datetime.now(UTC))
    )
    await db.commit()

    url = "/api/v1/recurring-task-rules"
    listed = (await client.get(url, headers=h)).json()[0]
    assert [e["patient_id"] for e in listed["excluded_patients"]] == [keep]

    res = await client.patch(
        f"{url}/{rule['id']}",
        json={"excluded_patient_ids": [keep, gone]},
        headers=h,
    )
    assert res.status_code == 200, res.text
    assert [e["patient_id"] for e in res.json()["excluded_patients"]] == [keep]

    # 一度もいない id は今までどおり 422
    res = await client.patch(
        f"{url}/{rule['id']}",
        json={"excluded_patient_ids": [keep, str(uuid4())]},
        headers=h,
    )
    assert res.status_code == 422


def test_task_start_date_past_month_is_today() -> None:
    """今月より前の月の分を作るときは今日にそろえる (作った直後に期日切れと出さない)."""
    from types import SimpleNamespace

    rule = SimpleNamespace(day_of_month=5)
    today = date(2026, 11, 2)
    assert svc.task_start_date(rule, date(2026, 10, 1), today) == today
    assert svc.task_start_date(rule, date(2026, 11, 1), today) == date(2026, 11, 5)
    assert svc.task_start_date(rule, date(2026, 12, 1), today) == date(2026, 12, 5)
    rule.day_of_month = 1
    assert svc.task_start_date(rule, date(2026, 11, 1), today) == today


# ---------------------------------------------------------------------------
# migration 0105
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _run_migration(engine: sa.Engine, revision: str, fn_name: str) -> None:
    path = _BACKEND_ROOT / "alembic" / "versions" / f"{revision}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"migration_{revision}"] = mod
    spec.loader.exec_module(mod)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, fn_name)()


def test_migration_0105_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0105_recurring_tasks")
    assert rev is not None
    assert rev.down_revision == "0104_monthly_actual"
    heads = list(script.get_heads())
    assert len(heads) == 1
    assert "0105_recurring_tasks" in {
        r.revision for r in script.iterate_revisions(heads[0], "base")
    }


def test_migration_0105_upgrade_downgrade(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0105.db'}")
    # batch (SQLite で表を作り直す) が外部キーの先の表を読むので、先の表だけ置く。
    with engine.begin() as conn:
        for name in ("users", "patients", "staff", "visits"):
            conn.execute(sa.text(f"CREATE TABLE {name} (id CHAR(32) PRIMARY KEY)"))
    _run_migration(engine, "0103_patient_tasks", "upgrade")
    _run_migration(engine, "0105_recurring_tasks", "upgrade")
    insp = inspect(engine)
    assert {"recurring_task_rules", "recurring_task_exclusions"} <= set(insp.get_table_names())
    cols = {c["name"] for c in insp.get_columns("patient_tasks")}
    assert {"recurring_rule_id", "recurring_month"} <= cols
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO patient_tasks (id, patient_id, start_date, memo, recurring_rule_id,"
                " recurring_month) VALUES ('a', 'p', '2026-12-01', 'm', 'r', '2026-12-01')"
            )
        )
    # 同じ設定・同じ方・同じ月の 2 件目は落ちる
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO patient_tasks (id, patient_id, start_date, memo, recurring_rule_id,"
                " recurring_month) VALUES ('b', 'p', '2026-12-01', 'm', 'r', '2026-12-01')"
            )
        )
    # 1〜28 日の外は落ちる
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO recurring_task_rules (id, memo, day_of_month, target_categories,"
                " start_month) VALUES ('r', 'm', 29, '[\"psychiatric\"]', '2026-12-01')"
            )
        )
    _run_migration(engine, "0105_recurring_tasks", "downgrade")
    insp = inspect(engine)
    assert not ({"recurring_task_rules", "recurring_task_exclusions"} & set(insp.get_table_names()))
    cols = {c["name"] for c in insp.get_columns("patient_tasks")}
    assert not ({"recurring_rule_id", "recurring_month"} & cols)
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM patient_tasks")).scalar() == 1
