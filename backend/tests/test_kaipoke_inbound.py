"""カイポケ → CareFlow 逆反映 (diff-inbound / apply-inbound) のテスト — R-1/R-2.

docs/plans/kaipoke-reverse-sync-design.md:
  * apply実績ゲート (実apply した週だけ取り込み可)
  * inbound 差分 (before=CareFlow / after=カイポケ現況) と visit_id 解決
  * dry-run は無書込・実適用は cancelled / manual_week / note 刻印
  * days (曜日チップ) フィルタ・applied 再適用 409・方向ガード
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.correction_sheet import CorrectionSheet
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.kaipoke_job import KaipokeJob
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import Visit
from app.services import kaipoke_client as kc_module
from app.services.kaipoke.csv_builder import KaipokeCsvRow, StaffCell, build_csv

# 対象週: 2026-07-06(月) 〜 2026-07-11(土)。
# 過去週のため時間ゲート (週開始<=今日) で無条件開放される (2026-07-26 改訂)。
WEEK_START = date(2026, 7, 6)
MONTH = "2026-07"
# 未来週 (ゲートブロックの検証用)。2100-01-04 = 月曜。
FUTURE_MONDAY = date(2100, 1, 4)

PATIENT_NAME = "山田　花子"
STAFF_NAME = "田中　看護師"
SERVICE = "精神基本療養費Ⅰ・正看"


# --- stub / helpers ----------------------------------------------------------


class StubKaipokeClient:
    """export だけ差し替える最小スタブ。"""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover
        pass

    def _dispatch(self, name: str, payload: Any) -> dict[str, Any]:
        self.calls.append((name, payload))
        return self.responses.get(name, {})

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self._dispatch("export", payload)

    async def status(self) -> dict[str, Any]:
        return self._dispatch("status", None)


@pytest.fixture
def stub_kaipoke():
    stub = StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


async def _make_admin(db) -> User:
    user = User(
        email="inbound-admin@example.com",
        password_hash=hash_password("does-not-matter-here"),
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_week(db) -> dict[str, Any]:
    """office / staff / patient / 対象週の visits (火・水・木) を作る。"""
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name=STAFF_NAME, role="staff", primary_office_id=office.id)
    staff.qualification = "看護師"
    db.add(staff)
    await db.flush()
    patient = Patient(
        code="PT-INB-1",
        name=PATIENT_NAME,
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()

    def _visit(d: date, start: time, end: time) -> Visit:
        return Visit(
            patient_id=patient.id,
            visit_date=d,
            start_time=start,
            end_time=end,
            type="regular",
            status="planned",
            source="auto",
            required_staff_count=1,
            primary_staff_id=staff.id,
        )

    visit_tue = _visit(date(2026, 7, 7), time(10, 0), time(10, 35))  # カイポケで 14:00 に変更
    visit_wed = _visit(date(2026, 7, 8), time(11, 0), time(11, 35))  # カイポケで削除
    visit_thu = _visit(date(2026, 7, 9), time(9, 0), time(9, 35))  # 変更なし
    db.add_all([visit_tue, visit_wed, visit_thu])
    await db.commit()
    for v in (visit_tue, visit_wed, visit_thu):
        await db.refresh(v)
    return {
        "office": office,
        "staff": staff,
        "patient": patient,
        "tue": visit_tue,
        "wed": visit_wed,
        "thu": visit_thu,
    }


async def _seed_real_apply(db, week_start: date = WEEK_START) -> None:
    """apply実績ゲートを通すための「実apply 完了」ジョブを作る。"""
    db.add(
        KaipokeJob(
            job_type="push",
            week_start=week_start,
            params={
                "op": "apply",
                "sheet_id": "dummy",
                "dry_run": False,
                "week_start": week_start.isoformat(),
            },
            status="completed",
        )
    )
    await db.commit()


def _kaipoke_csv(*rows: KaipokeCsvRow) -> str:
    """カイポケ現況CSV (export csv_content) を csv_builder で組み立てる。"""
    return build_csv(list(rows), encoding="utf-8-sig").decode("utf-8-sig")


def _kp_row(
    d: date,
    start: time,
    end: time,
    *,
    patient_name: str = PATIENT_NAME,
    staff_name: str = STAFF_NAME,
) -> KaipokeCsvRow:
    return KaipokeCsvRow(
        patient_name=patient_name,
        visit_date=d,
        start_time=start,
        end_time=end,
        office_name="稲毛",
        business_type="医療保険",
        service_content=SERVICE,
        primary=StaffCell(name=staff_name, qualification="看護師"),
    )


async def _seed_course(db, *, office, staff, weekday: int, code: str) -> Course:
    """対象週 (WEEK_START の ISO 週) にスタッフ担当のコースを作る。"""
    iso = WEEK_START.isocalendar()
    course = Course(
        iso_year=iso[0],
        iso_week=iso[1],
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


async def _seed_second_staff(db, office, name: str = "佐藤　次郎") -> Staff:
    staff = Staff(name=name, role="staff", primary_office_id=office.id)
    staff.qualification = "看護師"
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


def _default_kaipoke_state() -> str:
    """カイポケ現況: 火曜は 14:00 に変更済み・水曜は削除済み・木曜は不変。"""
    return _kaipoke_csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),
        _kp_row(date(2026, 7, 9), time(9, 0), time(9, 35)),
    )


async def _run_diff_inbound(client, db, stub_kaipoke, admin) -> dict[str, Any]:
    stub_kaipoke.responses["export"] = {"result": {"csv_content": _default_kaipoke_state()}}
    res = await client.post(
        "/api/v1/integrations/diff-inbound",
        headers=_bearer(admin),
        json={"month": MONTH, "weekStart": WEEK_START.isoformat()},
    )
    assert res.status_code == 202, res.text
    return res.json()


# --- 1. 取り込みゲート (2026-07-26 改訂: 過去/今週=無条件・未来週=実apply要) -----


@pytest.mark.asyncio
async def test_diff_inbound_blocked_for_future_week(client, db, stub_kaipoke) -> None:
    """未来週は実apply記録が無い限り 422 (計画中の週の全滅事故防止)。"""
    await _seed_week(db)
    admin = await _make_admin(db)
    res = await client.post(
        "/api/v1/integrations/diff-inbound",
        headers=_bearer(admin),
        json={"month": "2100-01", "weekStart": FUTURE_MONDAY.isoformat()},
    )
    assert res.status_code == 422, res.text
    assert "④反映" in res.json()["detail"]


@pytest.mark.asyncio
async def test_inbound_eligibility_endpoint(client, db, stub_kaipoke) -> None:
    admin = await _make_admin(db)

    # 過去週: 実apply記録なしでも時間ゲートで開放 (2026-07-26 改訂)
    res = await client.get(
        f"/api/v1/integrations/inbound-eligibility?weekStart={WEEK_START.isoformat()}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["eligible"] is True

    # 未来週: 実apply記録が無いと閉鎖 → 記録を作ると開放
    future_url = f"/api/v1/integrations/inbound-eligibility?weekStart={FUTURE_MONDAY.isoformat()}"
    res = await client.get(future_url, headers=_bearer(admin))
    assert res.json()["eligible"] is False

    await _seed_real_apply(db, FUTURE_MONDAY)
    res = await client.get(future_url, headers=_bearer(admin))
    assert res.json()["eligible"] is True


@pytest.mark.asyncio
async def test_dry_run_apply_job_does_not_open_gate(client, db, stub_kaipoke) -> None:
    """dry-run の apply 記録では未来週を開放しない (実apply のみがバトンタッチ)。"""
    admin = await _make_admin(db)
    db.add(
        KaipokeJob(
            job_type="push",
            week_start=FUTURE_MONDAY,
            params={
                "op": "apply",
                "sheet_id": "dummy",
                "dry_run": True,
                "week_start": FUTURE_MONDAY.isoformat(),
            },
            status="completed",
        )
    )
    await db.commit()
    res = await client.get(
        f"/api/v1/integrations/inbound-eligibility?weekStart={FUTURE_MONDAY.isoformat()}",
        headers=_bearer(admin),
    )
    assert res.json()["eligible"] is False


# --- 2. diff-inbound ----------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_inbound_creates_inbound_sheet(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)

    body = await _run_diff_inbound(client, db, stub_kaipoke, admin)
    assert body["summary"]["edit"] == 1
    assert body["summary"]["delete"] == 1
    assert body["summary"].get("add", 0) == 0
    assert body["summary"]["auto_selected"] == 2

    sheet = await db.scalar(
        select(CorrectionSheet).where(CorrectionSheet.id == UUID(body["sheetId"]))
    )
    assert sheet is not None
    assert sheet.direction == "inbound"
    assert sheet.week_start == WEEK_START
    assert sheet.week_end == date(2026, 7, 12)

    res = await client.get(
        f"/api/v1/integrations/correction-sheets/{sheet.id}/items",
        headers=_bearer(admin),
    )
    items = res.json()["items"] if isinstance(res.json(), dict) else res.json()
    by_action = {it["action"]: it for it in items}
    # edit = 火曜の時刻変更 → visit_id まで解決され include=True。
    assert by_action["edit"]["visit_id"] == str(seeded["tue"].id)
    assert by_action["edit"]["include"] is True
    assert by_action["edit"]["after"]["start_time"] == "14:00"
    # delete = 水曜のカイポケ側削除。
    assert by_action["delete"]["visit_id"] == str(seeded["wed"].id)
    assert by_action["delete"]["include"] is True


# --- 3. apply-inbound ---------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_inbound_dry_run_then_real(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    body = await _run_diff_inbound(client, db, stub_kaipoke, admin)
    sheet_id = body["sheetId"]

    # dry-run (既定): 予定される結果だけ返し、何も書き込まない。
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": sheet_id},
    )
    assert res.status_code == 200, res.text
    dry = res.json()
    assert dry["dryRun"] is True
    assert dry["cancelled"] == 1 and dry["updated"] == 1 and dry["failed"] == 0

    await db.refresh(seeded["tue"])
    await db.refresh(seeded["wed"])
    assert seeded["tue"].start_time == time(10, 0)
    assert seeded["wed"].status == "planned"

    # 実適用: 火曜=時刻変更 (manual_week + note)・水曜=キャンセル。
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": sheet_id, "dryRun": False},
    )
    assert res.status_code == 200, res.text
    real = res.json()
    assert real["cancelled"] == 1 and real["updated"] == 1 and real["failed"] == 0
    assert real["jobId"] is not None

    await db.refresh(seeded["tue"])
    await db.refresh(seeded["wed"])
    await db.refresh(seeded["thu"])
    assert seeded["tue"].start_time == time(14, 0)
    assert seeded["tue"].end_time == time(14, 35)
    assert seeded["tue"].source == "manual_week"
    assert "カイポケ取込" in (seeded["tue"].note or "")
    assert seeded["wed"].status == "cancelled"
    assert "カイポケ取込" in (seeded["wed"].note or "")
    assert seeded["thu"].status == "planned"  # 変更なしの訪問は不変。
    assert seeded["thu"].note is None

    # 適用済みシートの再適用は 409。
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": sheet_id, "dryRun": False},
    )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_apply_inbound_day_filter(client, db, stub_kaipoke) -> None:
    """days (曜日チップ) 指定日は適用され、指定外は据え置かれる。"""
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    body = await _run_diff_inbound(client, db, stub_kaipoke, admin)

    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": False, "days": ["2026-07-08"]},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["cancelled"] == 1 and out["updated"] == 0

    await db.refresh(seeded["tue"])
    await db.refresh(seeded["wed"])
    assert seeded["tue"].start_time == time(10, 0)  # 火曜は選択外 → 据え置き。
    assert seeded["wed"].status == "cancelled"


@pytest.mark.asyncio
async def test_direction_guards(client, db, stub_kaipoke) -> None:
    """inbound シートは /apply で拒否、outbound シートは /apply-inbound で拒否。"""
    await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    body = await _run_diff_inbound(client, db, stub_kaipoke, admin)

    res = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": True},
    )
    assert res.status_code == 422, res.text

    outbound = CorrectionSheet(target_month=MONTH, status="ready", direction="outbound")
    db.add(outbound)
    # NOTE: ここは commit ではなく flush にする。テストは StaticPool の単一共有
    # 接続なので flush で app セッションからも見える。commit だと Python 3.14 +
    # aiosqlite のカーソル解放タイミングにより "cannot commit - SQL statements
    # in progress" のフレークが ~1/3 で発生する (本番 PG では起きない環境問題)。
    await db.flush()
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": str(outbound.id)},
    )
    assert res.status_code == 422, res.text


# --- 4. R-3: スタッフ変更 = コースの変更 --------------------------------------


async def _diff_with_state(client, stub_kaipoke, admin, csv_text: str) -> dict[str, Any]:
    stub_kaipoke.responses["export"] = {"result": {"csv_content": csv_text}}
    res = await client.post(
        "/api/v1/integrations/diff-inbound",
        headers=_bearer(admin),
        json={"month": MONTH, "weekStart": WEEK_START.isoformat()},
    )
    assert res.status_code == 202, res.text
    return res.json()


def _staff_changed_state(staff_name: str) -> str:
    """火曜の担当だけ差し替え、水・木は不変のカイポケ現況。"""
    return _kaipoke_csv(
        _kp_row(date(2026, 7, 7), time(10, 0), time(10, 35), staff_name=staff_name),
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35)),
        _kp_row(date(2026, 7, 9), time(9, 0), time(9, 35)),
    )


@pytest.mark.asyncio
async def test_staff_change_moves_visit_to_new_staff_course(client, db, stub_kaipoke) -> None:
    """単発の担当変更 → 新担当がその日持っているコースへ訪問を移動する。"""
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    sato = await _seed_second_staff(db, seeded["office"])
    course_b = await _seed_course(db, office=seeded["office"], staff=sato, weekday=1, code="B")

    body = await _diff_with_state(client, stub_kaipoke, admin, _staff_changed_state("佐藤　次郎"))
    assert body["summary"]["edit"] == 1

    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": False},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["updated"] == 1 and out["failed"] == 0

    await db.refresh(seeded["tue"])
    assert seeded["tue"].course_id == course_b.id
    assert seeded["tue"].primary_staff_id == sato.id
    assert seeded["tue"].source == "manual_week"
    assert "担当" in (seeded["tue"].note or "")


@pytest.mark.asyncio
async def test_staff_change_creates_temp_course(client, db, stub_kaipoke) -> None:
    """新担当がその日コースを持たない → 臨時コース (臨・テンプレ「臨時」) を新設。"""
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    sato = await _seed_second_staff(db, seeded["office"])

    body = await _diff_with_state(client, stub_kaipoke, admin, _staff_changed_state("佐藤　次郎"))

    # dry-run では臨時コースを作らない。
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 1
    assert (await db.scalar(select(Course).where(Course.code == "臨"))) is None

    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": False},
    )
    assert res.status_code == 200, res.text

    temp = await db.scalar(select(Course).where(Course.code == "臨"))
    assert temp is not None
    assert temp.assigned_staff_id == sato.id
    assert temp.weekday == 1  # 火曜
    tpl = await db.scalar(select(CourseTemplate).where(CourseTemplate.id == temp.template_id))
    assert tpl is not None and tpl.label == "臨時"
    await db.refresh(seeded["tue"])
    assert seeded["tue"].course_id == temp.id
    assert seeded["tue"].primary_staff_id == sato.id


@pytest.mark.asyncio
async def test_course_takeover_changes_course_staff(client, db, stub_kaipoke) -> None:
    """コースの planned 全訪問が同じ新担当へ → コース担当の丸ごと交代。"""
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    course_a = await _seed_course(
        db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A"
    )
    sato = await _seed_second_staff(db, seeded["office"])

    # 火曜のコースAに2患者を乗せる (既存 tue + 患者2)。
    p2 = Patient(
        code="PT-INB-2",
        name="鈴木　一郎",
        status="active",
        insurance="medical",
        primary_office_id=seeded["office"].id,
    )
    db.add(p2)
    await db.flush()
    v2 = Visit(
        patient_id=p2.id,
        visit_date=date(2026, 7, 7),
        start_time=time(11, 0),
        end_time=time(11, 35),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
        primary_staff_id=seeded["staff"].id,
        course_id=course_a.id,
    )
    seeded["tue"].course_id = course_a.id
    db.add(v2)
    await db.commit()
    await db.refresh(v2)

    state = _kaipoke_csv(
        _kp_row(date(2026, 7, 7), time(10, 0), time(10, 35), staff_name="佐藤　次郎"),
        _kp_row(
            date(2026, 7, 7),
            time(11, 0),
            time(11, 35),
            patient_name="鈴木　一郎",
            staff_name="佐藤　次郎",
        ),
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35)),
        _kp_row(date(2026, 7, 9), time(9, 0), time(9, 35)),
    )
    body = await _diff_with_state(client, stub_kaipoke, admin, state)
    assert body["summary"]["edit"] == 2

    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": False},
    )
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 2

    await db.refresh(course_a)
    await db.refresh(seeded["tue"])
    await db.refresh(v2)
    assert course_a.assigned_staff_id == sato.id  # コース丸ごと交代。
    assert seeded["tue"].course_id == course_a.id  # 訪問はコースに残る。
    assert v2.course_id == course_a.id
    assert seeded["tue"].primary_staff_id == sato.id
    assert v2.primary_staff_id == sato.id
    assert "丸ごと交代" in (seeded["tue"].note or "")


# --- 5. R-3: add = カイポケにのみ存在する予定の取り込み ------------------------


@pytest.mark.asyncio
async def test_add_inserts_visit_with_temp_course(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)

    state = _kaipoke_csv(
        _kp_row(date(2026, 7, 7), time(10, 0), time(10, 35)),
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35)),
        _kp_row(date(2026, 7, 9), time(9, 0), time(9, 35)),
        _kp_row(date(2026, 7, 10), time(15, 0), time(15, 35)),  # 金曜に追加された予定。
    )
    body = await _diff_with_state(client, stub_kaipoke, admin, state)
    assert body["summary"]["add"] == 1
    assert body["summary"]["auto_selected"] == 1  # 患者・担当が解決できた add は既定ON。

    # dry-run: visit もコースも作られない。
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["added"] == 1
    assert (await db.scalar(select(Visit).where(Visit.visit_date == date(2026, 7, 10)))) is None

    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": False},
    )
    assert res.status_code == 200, res.text
    assert res.json()["added"] == 1 and res.json()["failed"] == 0

    new_visit = await db.scalar(select(Visit).where(Visit.visit_date == date(2026, 7, 10)))
    assert new_visit is not None
    assert new_visit.source == "import"
    assert new_visit.start_time == time(15, 0)
    assert new_visit.primary_staff_id == seeded["staff"].id
    assert "カイポケ側で追加された予定" in (new_visit.note or "")
    temp = await db.scalar(select(Course).where(Course.id == new_visit.course_id))
    assert temp is not None and temp.code == "臨" and temp.weekday == 4  # 金曜。


@pytest.mark.asyncio
async def test_add_with_unresolved_staff_defaults_off(client, db, stub_kaipoke) -> None:
    """担当が名寄せできない add は既定OFF (取り込み対象外) のまま可視化。"""
    await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)

    state = _kaipoke_csv(
        _kp_row(date(2026, 7, 7), time(10, 0), time(10, 35)),
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35)),
        _kp_row(date(2026, 7, 9), time(9, 0), time(9, 35)),
        _kp_row(date(2026, 7, 10), time(15, 0), time(15, 35), staff_name="実在　しない"),
    )
    body = await _diff_with_state(client, stub_kaipoke, admin, state)
    assert body["summary"]["add"] == 1
    assert body["summary"]["auto_selected"] == 0


# --- 6. 2026-07-26 改訂: 名寄せ正規化 + キャンセル枠の復活 --------------------


@pytest.mark.asyncio
async def test_name_spacing_mismatch_produces_edit_not_delete_add(
    client, db, stub_kaipoke
) -> None:
    """氏名の空白違い (半角/全角) でも同一人物に束ね、edit として検出する。

    正規化前はカイポケ「山田 花子」(半角) と CareFlow「山田　花子」(全角) が
    別人扱いになり delete+add ペアが出ていた (2026-07-26 実データで11件)。
    """
    await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)

    # カイポケ現況: 火曜のみ・時刻変更 (10:00→14:00)・氏名は半角スペース
    csv_rows = _kaipoke_csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35), patient_name="山田 花子"),
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35), patient_name="山田 花子"),
        _kp_row(date(2026, 7, 9), time(9, 0), time(9, 35), patient_name="山田 花子"),
    )
    stub_kaipoke.responses["export"] = {"result": {"csv_content": csv_rows}}
    res = await client.post(
        "/api/v1/integrations/diff-inbound",
        headers=_bearer(admin),
        json={"month": MONTH, "weekStart": WEEK_START.isoformat()},
    )
    assert res.status_code == 202, res.text
    summary = res.json()["summary"]
    assert summary.get("edit", 0) == 1  # 火曜の時刻変更のみ
    assert summary.get("delete", 0) == 0  # 偽のキャンセル候補が出ない
    assert summary.get("add", 0) == 0


@pytest.mark.asyncio
async def test_same_slot_delete_add_pair_revives_cancelled_visit(client, db) -> None:
    """delete+add が同一 (患者,日,時刻) を指す場合、キャンセル枠を復活して上書きする。

    2026-07-26 改訂: 以前は add が「同時刻の予定が既にあります」で失敗し、
    キャンセルだけが適用されて訪問が消えていた。
    """
    from datetime import UTC, datetime

    from app.models.correction_sheet import CorrectionSheetItem
    from app.services.kaipoke.inbound import apply_inbound_items

    seeded = await _seed_week(db)
    visit = seeded["tue"]  # 7/7 10:00
    patient = seeded["patient"]

    sheet = CorrectionSheet(
        target_month=MONTH,
        direction="inbound",
        week_start=WEEK_START,
        week_end=date(2026, 7, 11),
    )
    db.add(sheet)
    await db.flush()
    item_add = CorrectionSheetItem(
        sheet_id=sheet.id,
        action="add",
        include=True,
        patient_id=patient.id,
        after={
            "user_name": PATIENT_NAME,
            "date": "7",
            "start_time": "10:00",
            "end_time": "10:40",
            "staff1": STAFF_NAME,
            "staff2": "",
        },
    )
    item_del = CorrectionSheetItem(
        sheet_id=sheet.id,
        action="delete",
        include=True,
        patient_id=patient.id,
        visit_id=visit.id,
        before={
            "user_name": PATIENT_NAME,
            "date": "7",
            "start_time": "10:00",
            "end_time": "10:35",
        },
    )
    # add を先に渡しても action ソートで delete が先に処理される
    db.add_all([item_add, item_del])
    await db.commit()

    now = datetime.now(UTC)

    # dry-run: 復活を予測 (failed にならない)
    summary_dry = await apply_inbound_items(
        db,
        items=[item_add, item_del],
        week_start=WEEK_START,
        week_end=date(2026, 7, 11),
        days=None,
        dry_run=True,
        now=now,
    )
    assert summary_dry.failed == 0
    assert summary_dry.cancelled == 1
    assert summary_dry.added == 1
    # dry-run は一切 mutate しないため rollback 不要 (そのまま実適用へ)

    # 実適用: 訪問は1件のまま復活・上書きされる
    summary = await apply_inbound_items(
        db,
        items=[item_add, item_del],
        week_start=WEEK_START,
        week_end=date(2026, 7, 11),
        days=None,
        dry_run=False,
        now=now,
    )
    await db.commit()
    assert summary.failed == 0
    assert summary.cancelled == 1
    assert summary.added == 1

    await db.refresh(visit)
    assert visit.status == "planned"  # キャンセルで消えず復活
    assert visit.source == "import"
    assert visit.end_time == time(10, 40)  # カイポケの内容で上書き
    active = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.visit_date == date(2026, 7, 7),
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(active) == 1  # 二重挿入しない
