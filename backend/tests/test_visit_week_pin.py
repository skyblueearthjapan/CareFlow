"""週のピン (青ピン) — PATCH /api/v1/schedule/v2/visits/{visit_id}/week-pin

PO 決定 2026-08-08 / 仕様: docs/plans/pin-and-movability-spec.md

赤ピン (PFV.is_pinned) との違い:
  - 赤ピンは **型** に対するもの。毎週効く。型と一致する訪問にしか刺せない。
  - 青ピンは **今週の訪問** に対するもの。その週だけ効く。
    **型とズレていても刺せる** — ズレた訪問を今の位置で守れるのはこちらだけ。

実体は ``visit.source='manual_week'``。この値には既に「週生成の削除対象から除外」
「再生成ループが当該 (patient, visit_date) を skip」という意味論があり、本機能は
その入口を足すもの (DB 列の追加なし)。

検証観点:
  1. 青ピンを刺すと source='manual_week' になる
  2. 型とズレていても刺せる (赤ピンとの決定的な違い)
  3. 解除すると source='auto' に戻る。**その場では訪問を動かさない**
  4. planned 以外は 422
  5. 型の管理下に無い source ('manual' / 'import') は 422
  6. audit_log に before/after が残る
  7. RBAC: staff は不可
  8. 冪等 (同じ値を 2 回送っても壊れない)
"""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit
from app.models.audit_log import AuditLog
from app.models.patient_fixed_visit import PatientFixedVisit

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_WEEK_PIN_URL = "/api/v1/schedule/v2/visits/{vid}/week-pin"


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, *, code: str) -> Patient:
    p = Patient(code=code, name=f"患者{code}", status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db,
    *,
    patient: Patient,
    source: str = "auto",
    status_value: str = "planned",
    start: time = time(10, 25),
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=date(2026, 9, 4),  # 金曜
        start_time=start,
        end_time=time(start.hour + 1, start.minute),
        type="regular",
        status=status_value,
        source=source,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


# ---------------------------------------------------------------------------
# 1-2. 刺せること / 型とズレていても刺せること
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_pin_sets_manual_week(client, db) -> None:
    admin = await _make_user(db, email="wp-1@example.com", role="admin")
    patient = await _make_patient(db, code="WP-1")
    visit = await _make_visit(db, patient=patient, source="auto")

    res = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(admin),
        json={"pinned": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pinned"] is True
    assert body["source"] == "manual_week"

    await db.refresh(visit)
    assert visit.source == "manual_week"


@pytest.mark.asyncio
async def test_week_pin_works_even_when_diverged_from_master(client, db) -> None:
    """核心: 型とズレている訪問にも刺せる.

    赤ピンは型と一致する訪問にしか刺せないため、ズレた訪問を今の位置で守る手段が
    存在しなかった。青ピンはその穴を埋めるもの。
    """
    admin = await _make_user(db, email="wp-2@example.com", role="admin")
    patient = await _make_patient(db, code="WP-2")
    # 型は 13:00、今週の実配置は 10:25 (2 時間半のズレ = 本番で実在したケース)。
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=4,
            start_time=time(13, 0),
            duration_min=35,
            slot_index=0,
            is_pinned=False,
        )
    )
    await db.commit()
    visit = await _make_visit(db, patient=patient, source="auto", start=time(10, 25))

    res = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(admin),
        json={"pinned": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["pinned"] is True

    await db.refresh(visit)
    assert visit.source == "manual_week"
    # 訪問の時刻は動かさない (ズレたまま今の位置で固定する、が青ピンの意味).
    assert visit.start_time == time(10, 25)


# ---------------------------------------------------------------------------
# 3. 解除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_pin_release_restores_auto_without_moving(client, db) -> None:
    """解除は source を 'auto' に戻すだけで、その場では訪問を動かさない.

    実際に型の時刻へ戻るのは次に週生成を実行したとき (PO 確認済の挙動)。
    """
    admin = await _make_user(db, email="wp-3@example.com", role="admin")
    patient = await _make_patient(db, code="WP-3")
    visit = await _make_visit(db, patient=patient, source="manual_week", start=time(10, 25))

    res = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(admin),
        json={"pinned": False},
    )
    assert res.status_code == 200, res.text
    assert res.json()["pinned"] is False
    assert res.json()["source"] == "auto"

    await db.refresh(visit)
    assert visit.source == "auto"
    # 解除しただけでは動かない.
    assert visit.start_time == time(10, 25)
    assert visit.visit_date == date(2026, 9, 4)


# ---------------------------------------------------------------------------
# 4-5. 422 になる条件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_pin_rejects_non_planned(client, db) -> None:
    """完了済みなど planned 以外は対象外."""
    admin = await _make_user(db, email="wp-4@example.com", role="admin")
    patient = await _make_patient(db, code="WP-4")
    visit = await _make_visit(db, patient=patient, source="auto", status_value="completed")

    res = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(admin),
        json={"pinned": True},
    )
    assert res.status_code == 422, res.text
    await db.refresh(visit)
    assert visit.source == "auto"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["manual", "import"])
async def test_week_pin_rejects_sources_outside_master_control(client, db, source: str) -> None:
    """型の管理下に無い source は対象外.

    'manual' (毎週の手動作成) / 'import' (カイポケ取込) は週生成でも消えないため、
    青ピンの掛け外しに意味が無い。勝手に source を書き換えると保護が変わってしまう。
    """
    admin = await _make_user(db, email=f"wp-5-{source}@example.com", role="admin")
    patient = await _make_patient(db, code=f"WP-5-{source}")
    visit = await _make_visit(db, patient=patient, source=source)

    res = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(admin),
        json={"pinned": True},
    )
    assert res.status_code == 422, res.text
    await db.refresh(visit)
    assert visit.source == source


# ---------------------------------------------------------------------------
# 6-8. 監査 / RBAC / 冪等 / 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_pin_writes_audit_log(client, db) -> None:
    admin = await _make_user(db, email="wp-6@example.com", role="admin")
    patient = await _make_patient(db, code="WP-6")
    visit = await _make_visit(db, patient=patient, source="auto")

    res = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(admin),
        json={"pinned": True},
    )
    assert res.status_code == 200, res.text

    row = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "visit_week_pin_toggle",
            AuditLog.target_id == str(visit.id),
        )
    )
    assert row is not None
    assert row.before == {"source": "auto"}
    assert row.after == {"source": "manual_week"}


@pytest.mark.asyncio
async def test_week_pin_forbidden_for_staff(client, db) -> None:
    staff_user = await _make_user(db, email="wp-7@example.com", role="staff")
    patient = await _make_patient(db, code="WP-7")
    visit = await _make_visit(db, patient=patient, source="auto")

    res = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(staff_user),
        json={"pinned": True},
    )
    assert res.status_code == 403, res.text
    await db.refresh(visit)
    assert visit.source == "auto"


@pytest.mark.asyncio
async def test_week_pin_is_idempotent(client, db) -> None:
    """同じ値を 2 回送っても壊れない (2 回目は no-op で 200)."""
    admin = await _make_user(db, email="wp-8@example.com", role="admin")
    patient = await _make_patient(db, code="WP-8")
    visit = await _make_visit(db, patient=patient, source="manual_week")

    for _ in range(2):
        res = await client.patch(
            _WEEK_PIN_URL.format(vid=visit.id),
            headers=_bearer(admin),
            json={"pinned": True},
        )
        assert res.status_code == 200, res.text
        assert res.json()["source"] == "manual_week"


@pytest.mark.asyncio
async def test_week_pin_404_for_unknown_visit(client, db) -> None:
    admin = await _make_user(db, email="wp-9@example.com", role="admin")
    res = await client.patch(
        _WEEK_PIN_URL.format(vid=uuid.uuid4()),
        headers=_bearer(admin),
        json={"pinned": True},
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 一括 (青の全件固定 / 全件解除) — PO 決定 2026-08-08
# ---------------------------------------------------------------------------

_BULK_URL = "/api/v1/schedule/v2/visits/week-pin/bulk"
# _make_visit の visit_date=2026-09-04 (金) が属する ISO 週。
_BULK_WEEK = {"iso_year": 2026, "iso_week": 36}


@pytest.mark.asyncio
async def test_bulk_week_pin_pins_all_toggleable(client, db) -> None:
    """今週全件固定: 型の管理下の source だけが manual_week になり、対象外は据え置き."""
    admin = await _make_user(db, email="wpb-1@example.com", role="admin")
    patient = await _make_patient(db, code="WPB-1")
    v_auto = await _make_visit(db, patient=patient, source="auto", start=time(9, 0))
    v_reset = await _make_visit(db, patient=patient, source="reset_v2", start=time(10, 0))
    v_manual = await _make_visit(db, patient=patient, source="manual", start=time(11, 0))
    v_import = await _make_visit(db, patient=patient, source="import", start=time(12, 0))
    v_done = await _make_visit(
        db, patient=patient, source="auto", status_value="completed", start=time(13, 0)
    )

    res = await client.post(_BULK_URL, headers=_bearer(admin), json={**_BULK_WEEK, "pinned": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["target_count"] == 2
    assert body["updated_count"] == 2

    for v, expected in [
        (v_auto, "manual_week"),
        (v_reset, "manual_week"),
        (v_manual, "manual"),
        (v_import, "import"),
        (v_done, "auto"),
    ]:
        await db.refresh(v)
        assert v.source == expected, f"{v.start_time}: {v.source} != {expected}"


@pytest.mark.asyncio
async def test_bulk_week_pin_unpins_only_manual_week(client, db) -> None:
    """今週全件解除: manual_week だけが auto に戻る。時刻は動かない."""
    admin = await _make_user(db, email="wpb-2@example.com", role="admin")
    patient = await _make_patient(db, code="WPB-2")
    v_pinned = await _make_visit(db, patient=patient, source="manual_week", start=time(9, 0))
    v_manual = await _make_visit(db, patient=patient, source="manual", start=time(10, 0))

    res = await client.post(_BULK_URL, headers=_bearer(admin), json={**_BULK_WEEK, "pinned": False})
    assert res.status_code == 200, res.text
    assert res.json()["updated_count"] == 1

    await db.refresh(v_pinned)
    await db.refresh(v_manual)
    assert v_pinned.source == "auto"
    assert v_pinned.start_time == time(9, 0)  # 解除しても動かない
    assert v_manual.source == "manual"


@pytest.mark.asyncio
async def test_bulk_week_pin_dry_run_counts_without_changing(client, db) -> None:
    """dry_run: 件数だけ返して何も変更しない (確認ダイアログの件数表示用)."""
    admin = await _make_user(db, email="wpb-3@example.com", role="admin")
    patient = await _make_patient(db, code="WPB-3")
    v = await _make_visit(db, patient=patient, source="auto")

    res = await client.post(
        _BULK_URL,
        headers=_bearer(admin),
        json={**_BULK_WEEK, "pinned": True, "dry_run": True},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"target_count": 1, "updated_count": 0}
    await db.refresh(v)
    assert v.source == "auto"


@pytest.mark.asyncio
async def test_bulk_week_pin_writes_single_audit_row(client, db) -> None:
    admin = await _make_user(db, email="wpb-4@example.com", role="admin")
    patient = await _make_patient(db, code="WPB-4")
    await _make_visit(db, patient=patient, source="auto")

    res = await client.post(_BULK_URL, headers=_bearer(admin), json={**_BULK_WEEK, "pinned": True})
    assert res.status_code == 200, res.text

    row = await db.scalar(select(AuditLog).where(AuditLog.action == "visit_week_pin_bulk"))
    assert row is not None
    assert row.target_id == "2026-W36"
    assert row.after == {"pinned": True, "count": 1}


@pytest.mark.asyncio
async def test_bulk_week_pin_forbidden_for_staff(client, db) -> None:
    staff_user = await _make_user(db, email="wpb-5@example.com", role="staff")
    res = await client.post(
        _BULK_URL, headers=_bearer(staff_user), json={**_BULK_WEEK, "pinned": True}
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_bulk_week_pin_other_week_untouched(client, db) -> None:
    """対象週の外の訪問には触れない."""
    admin = await _make_user(db, email="wpb-6@example.com", role="admin")
    patient = await _make_patient(db, code="WPB-6")
    v = await _make_visit(db, patient=patient, source="auto")

    res = await client.post(
        _BULK_URL,
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 40, "pinned": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["target_count"] == 0
    await db.refresh(v)
    assert v.source == "auto"
