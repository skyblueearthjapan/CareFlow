"""訪問単位の「カイポケのサービス内容に合わせる」 — POST /schedule/v2/visit-service-override

正典 = docs/plans/kaipoke-service-content-design.md §2 (mig 0078)。

サービス内容は本来 **患者の区分 × 職員1の資格** から自動で決まるが、
「カイポケが正でらく助のマスタが追いついていない」1 件だけを合わせる逃げ道が
``visits.kaipoke_service_override``。マスタ (患者 / スタッフ) は動かさない。

検証観点:
  1. 設定 → 解除 の往復 (VisitRead に露出)
  2. 優先順位: 訪問上書き > 患者上書き > 区分 × 資格 (CSV 行生成まで貫通)
  3. 位置を変えないので **青ピン / 完了済み でも 422 にしない**
  4. item_id 経由の解決 (visit_id 列 / 日付+時刻+氏名フォールバック / 特定不能は 422)
  5. RBAC: staff は 403 / 未知の visit は 404 / 指定の排他は 422
  6. undo/redo (op_log ``set_visit_service_override``)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.office import Office
from app.services.kaipoke.csv_builder import BuildOptions, resolve_month_rows

_URL = "/api/v1/schedule/v2/visit-service-override"
_JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(UTC).astimezone(_JST).date()


def _future_day() -> date:
    return _today_jst() + timedelta(days=7)


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, *, code: str, name: str | None = None, **kwargs) -> Patient:
    p = Patient(
        code=code,
        name=name or f"患者{code}",
        status="active",
        insurance="medical",
        special_week_active=[],
        **kwargs,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db,
    *,
    patient: Patient,
    visit_date: date | None = None,
    start: time = time(10, 0),
    status_value: str = "planned",
    week_pinned: bool = False,
    primary_staff_id: uuid.UUID | None = None,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date or _future_day(),
        start_time=start,
        # 分の繰り上がりを自前で書かない (start=10:45 のような値で壊れる)。
        end_time=(datetime.combine(date(2000, 1, 1), start) + timedelta(minutes=35)).time(),
        type="regular",
        status=status_value,
        source="auto",
        week_pinned=week_pinned,
        required_staff_count=1,
        primary_staff_id=primary_staff_id,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


# ---------------------------------------------------------------------------
# 1. 設定 → 解除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_clear_round_trip(client, db) -> None:
    admin = await _make_user(db, email="vso-1@example.com", role="admin")
    patient = await _make_patient(db, code="VSO-1")
    visit = await _make_visit(db, patient=patient)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"visit_id": str(visit.id), "service_content": "基本療養費Ⅰ・准看"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["kaipoke_service_override"] == "基本療養費Ⅰ・准看"
    await db.refresh(visit)
    assert visit.kaipoke_service_override == "基本療養費Ⅰ・准看"

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "service_content": None}
    )
    assert res.status_code == 200, res.text
    assert res.json()["kaipoke_service_override"] is None
    await db.refresh(visit)
    assert visit.kaipoke_service_override is None


@pytest.mark.asyncio
async def test_blank_string_clears_the_override(client, db) -> None:
    """空文字 / 空白のみは解除扱い (FE の「解除」が空文字を送っても壊れない)."""
    admin = await _make_user(db, email="vso-2@example.com", role="admin")
    patient = await _make_patient(db, code="VSO-2")
    visit = await _make_visit(db, patient=patient)
    visit.kaipoke_service_override = "精神基本療養費Ⅰ・准看"
    await db.commit()

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "service_content": "  "}
    )
    assert res.status_code == 200, res.text
    await db.refresh(visit)
    assert visit.kaipoke_service_override is None


# ---------------------------------------------------------------------------
# 2. 優先順位 — CSV 行生成まで貫通する
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_override_beats_patient_override_and_branch(client, db) -> None:
    office = Office(name="稲毛")
    db.add(office)
    await db.flush()
    staff = Staff(
        name="准看花子", role="staff", primary_office_id=office.id, qualification="准看護師"
    )
    db.add(staff)
    await db.flush()
    patient = await _make_patient(
        db,
        code="VSO-3",
        primary_office_id=office.id,
        visit_category="general",
        kaipoke_service_content="精神基本療養費Ⅲ・正看",
    )
    target = _future_day()
    visit = await _make_visit(db, patient=patient, visit_date=target, primary_staff_id=staff.id)

    opts = BuildOptions(year=target.year, month=target.month)
    # ``db.refresh()`` を通した Visit は lazy="noload" の ``patient`` が
    # 「ロード済み = None」で固定されてしまう (テスト固有のセッション事情)。
    # 本番は毎回新しいセッションなので、ここで expire して同じ状態を作る。
    db.expire_all()
    rows = await resolve_month_rows(db, opts)
    assert [r.service_content for r in rows] == ["精神基本療養費Ⅲ・正看"]  # 患者上書きが勝つ

    admin = await _make_user(db, email="vso-3@example.com", role="admin")
    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"visit_id": str(visit.id), "service_content": "基本療養費Ⅰ・准看"},
    )
    assert res.status_code == 200, res.text

    db.expire_all()
    rows = await resolve_month_rows(db, opts)
    assert [r.service_content for r in rows] == ["基本療養費Ⅰ・准看"]  # 訪問上書きが最優先


# ---------------------------------------------------------------------------
# 3. 位置を変えない = 青ピン / 完了済みでも通る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_value", "week_pinned", "visit_date"),
    [
        ("planned", True, None),  # 青ピン (蓋) でも通す
        ("completed", False, None),  # 完了済みでも通す (請求に効くので直せる必要がある)
        ("planned", False, "past"),  # 過去日でも通す
    ],
)
async def test_no_422_for_pinned_or_finished_visits(
    client, db, status_value, week_pinned, visit_date
) -> None:
    admin = await _make_user(
        db, email=f"vso-4-{status_value}-{week_pinned}-{visit_date}@example.com", role="admin"
    )
    patient = await _make_patient(db, code=f"VSO-4-{status_value}-{week_pinned}-{visit_date}")
    visit = await _make_visit(
        db,
        patient=patient,
        status_value=status_value,
        week_pinned=week_pinned,
        visit_date=_today_jst() - timedelta(days=3) if visit_date == "past" else None,
    )

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"visit_id": str(visit.id), "service_content": "基本療養費Ⅰ・正看"},
    )
    assert res.status_code == 200, res.text
    await db.refresh(visit)
    assert visit.kaipoke_service_override == "基本療養費Ⅰ・正看"
    # マスタ不変 — 患者の区分 / 上書きには触れない (憲法1)。
    await db.refresh(patient)
    assert patient.kaipoke_service_content is None
    assert patient.visit_category == "psychiatric"


# ---------------------------------------------------------------------------
# 4. item_id 経由の解決
# ---------------------------------------------------------------------------


async def _make_item(
    db,
    *,
    week_start: date,
    action: str,
    user_name: str,
    day: int,
    start_time: str,
    visit_id: uuid.UUID | None = None,
) -> CorrectionSheetItem:
    sheet = CorrectionSheet(
        target_month=f"{week_start.year:04d}-{week_start.month:02d}",
        status="ready",
        direction="outbound",
        origin="cached",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
    )
    db.add(sheet)
    await db.flush()
    payload = {
        "user_name": user_name,
        "date": str(day),
        "start_time": start_time,
        "end_time": "11:00",
        "staff1": "看護太郎",
        "staff2": "",
        "service_type": "精神基本療養費Ⅰ・正看",
        "business_type": "医療保険",
        "remarks": "",
    }
    item = CorrectionSheetItem(
        sheet_id=sheet.id,
        patient_id=None,
        visit_id=visit_id,
        action=action,
        before=payload,
        after=payload,
        include=True,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@pytest.mark.asyncio
async def test_item_id_uses_visit_id_column_when_present(client, db) -> None:
    admin = await _make_user(db, email="vso-5@example.com", role="admin")
    patient = await _make_patient(db, code="VSO-5")
    target = _future_day()
    visit = await _make_visit(db, patient=patient, visit_date=target)
    item = await _make_item(
        db,
        week_start=_monday_of(target),
        action="delete",
        user_name="別人 太郎",  # visit_id があるので氏名は見ない
        day=target.day,
        start_time="10:00",
        visit_id=visit.id,
    )

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"item_id": str(item.id), "service_content": "基本療養費Ⅰ・准看"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == str(visit.id)
    await db.refresh(visit)
    assert visit.kaipoke_service_override == "基本療養費Ⅰ・准看"


@pytest.mark.asyncio
async def test_item_id_falls_back_to_date_time_and_name(client, db) -> None:
    """●未送信の 'cached' シートは visit_id を持たない → 日付+時刻+氏名で解決する."""
    admin = await _make_user(db, email="vso-6@example.com", role="admin")
    # カイポケ側は異体字 + 全角スペース、らく助側は半角スペース = 正規化で一致する。
    patient = await _make_patient(db, code="VSO-6", name="髙梨 桂子")
    target = _future_day()
    visit = await _make_visit(db, patient=patient, visit_date=target, start=time(14, 0))
    item = await _make_item(
        db,
        week_start=_monday_of(target),
        action="delete",
        user_name="高梨　桂子",
        day=target.day,
        start_time="14:00",
    )

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"item_id": str(item.id), "service_content": "精神基本療養費Ⅰ・准看"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == str(visit.id)
    await db.refresh(visit)
    assert visit.kaipoke_service_override == "精神基本療養費Ⅰ・准看"


@pytest.mark.asyncio
async def test_item_id_accepts_single_digit_hour(client, db) -> None:
    """カイポケの CSV は '9:00' のように時が 1 桁の行を出す (実データ)。"""
    admin = await _make_user(db, email="vso-6b@example.com", role="admin")
    patient = await _make_patient(db, code="VSO-6B")
    target = _future_day()
    visit = await _make_visit(db, patient=patient, visit_date=target, start=time(9, 0))
    item = await _make_item(
        db,
        week_start=_monday_of(target),
        action="delete",
        user_name=patient.name,
        day=target.day,
        start_time="9:00",
    )

    res = await client.post(
        _URL, headers=_bearer(admin), json={"item_id": str(item.id), "service_content": "X"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == str(visit.id)


@pytest.mark.asyncio
async def test_item_id_returns_422_when_two_visits_share_name_and_time(client, db) -> None:
    """同姓同名・同時刻が 2 件 → 当てずっぽうで片方を書き換えず 422。"""
    admin = await _make_user(db, email="vso-6c@example.com", role="admin")
    target = _future_day()
    # 表記だけ違う同姓同名 (正規化キーは一致する) を 2 人ぶん、同じ時刻に置く。
    twin_a = await _make_patient(db, code="VSO-6C-A", name="佐藤 太郎")
    twin_b = await _make_patient(db, code="VSO-6C-B", name="佐藤　太郎")
    await _make_visit(db, patient=twin_a, visit_date=target, start=time(11, 0))
    await _make_visit(db, patient=twin_b, visit_date=target, start=time(11, 0))
    item = await _make_item(
        db,
        week_start=_monday_of(target),
        action="delete",
        user_name="佐藤太郎",
        day=target.day,
        start_time="11:00",
    )

    res = await client.post(
        _URL, headers=_bearer(admin), json={"item_id": str(item.id), "service_content": "X"}
    )
    assert res.status_code == 422, res.text
    assert "一意に特定できません" in res.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["精神\n基本療養費Ⅰ・正看", "基本療養費Ⅰ\t・正看", "A\rB"])
async def test_rejects_control_characters(client, db, bad) -> None:
    """改行/タブ等は 18 列 CSV の行を壊す (列ズレ = 別患者の予定を書き換える)。"""
    admin = await _make_user(db, email=f"vso-15-{len(bad)}-{bad[:1]}@example.com", role="admin")
    patient = await _make_patient(db, code=f"VSO-15-{len(bad)}-{bad[:1]}")
    visit = await _make_visit(db, patient=patient)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "service_content": bad}
    )
    assert res.status_code == 422, res.text
    await db.refresh(visit)
    assert visit.kaipoke_service_override is None


@pytest.mark.asyncio
async def test_item_id_returns_422_when_visit_cannot_be_resolved(client, db) -> None:
    admin = await _make_user(db, email="vso-7@example.com", role="admin")
    target = _future_day()
    item = await _make_item(
        db,
        week_start=_monday_of(target),
        action="delete",
        user_name="誰も居ない 太郎",
        day=target.day,
        start_time="10:00",
    )

    res = await client.post(
        _URL, headers=_bearer(admin), json={"item_id": str(item.id), "service_content": "X"}
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_unknown_item_returns_404(client, db) -> None:
    admin = await _make_user(db, email="vso-8@example.com", role="admin")
    res = await client.post(
        _URL, headers=_bearer(admin), json={"item_id": str(uuid.uuid4()), "service_content": "X"}
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 5. RBAC / 指定の排他
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requires_admin(client, db) -> None:
    staff_user = await _make_user(db, email="vso-9@example.com", role="staff")
    patient = await _make_patient(db, code="VSO-9")
    visit = await _make_visit(db, patient=patient)
    res = await client.post(
        _URL, headers=_bearer(staff_user), json={"visit_id": str(visit.id), "service_content": "X"}
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_unknown_visit_returns_404(client, db) -> None:
    admin = await _make_user(db, email="vso-10@example.com", role="admin")
    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(uuid.uuid4()), "service_content": "X"}
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"visit_id": None, "item_id": None}])
async def test_requires_exactly_one_target(client, db, body) -> None:
    admin = await _make_user(db, email=f"vso-11-{len(body)}@example.com", role="admin")
    res = await client.post(_URL, headers=_bearer(admin), json={**body, "service_content": "X"})
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_rejects_both_targets(client, db) -> None:
    admin = await _make_user(db, email="vso-12@example.com", role="admin")
    patient = await _make_patient(db, code="VSO-12")
    visit = await _make_visit(db, patient=patient)
    target = _future_day()
    item = await _make_item(
        db,
        week_start=_monday_of(target),
        action="delete",
        user_name=patient.name,
        day=target.day,
        start_time="10:00",
    )
    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"visit_id": str(visit.id), "item_id": str(item.id), "service_content": "X"},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 6. undo / redo (op_log set_visit_service_override)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_redo_round_trip(client, db) -> None:
    admin = await _make_user(db, email="vso-13@example.com", role="admin")
    patient = await _make_patient(db, code="VSO-13")
    target = _future_day()
    visit = await _make_visit(db, patient=patient, visit_date=target)
    iso = target.isocalendar()
    week = {"iso_year": iso.year, "iso_week": iso.week}

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"visit_id": str(visit.id), "service_content": "基本療養費Ⅰ・准看"},
    )
    assert res.status_code == 200, res.text
    await db.refresh(visit)
    assert visit.kaipoke_service_override == "基本療養費Ⅰ・准看"

    undo = await client.post("/api/v1/schedule/v2/op-log/undo", headers=_bearer(admin), json=week)
    assert undo.status_code == 200, undo.text
    await db.refresh(visit)
    assert visit.kaipoke_service_override is None

    redo = await client.post("/api/v1/schedule/v2/op-log/redo", headers=_bearer(admin), json=week)
    assert redo.status_code == 200, redo.text
    await db.refresh(visit)
    assert visit.kaipoke_service_override == "基本療養費Ⅰ・准看"


@pytest.mark.asyncio
async def test_no_op_when_value_unchanged(client, db) -> None:
    """同じ値の再送は DB も操作ジャーナルも触らない (「戻る」を無駄に消費しない)."""
    from app.models.schedule_op_log import ScheduleOpLog

    admin = await _make_user(db, email="vso-14@example.com", role="admin")
    patient = await _make_patient(db, code="VSO-14")
    visit = await _make_visit(db, patient=patient)

    payload = {"visit_id": str(visit.id), "service_content": "基本療養費Ⅰ・正看"}
    assert (await client.post(_URL, headers=_bearer(admin), json=payload)).status_code == 200
    assert (await client.post(_URL, headers=_bearer(admin), json=payload)).status_code == 200

    logs = (
        await db.scalars(
            select(ScheduleOpLog).where(ScheduleOpLog.op_kind == "set_visit_service_override")
        )
    ).all()
    assert len(list(logs)) == 1
