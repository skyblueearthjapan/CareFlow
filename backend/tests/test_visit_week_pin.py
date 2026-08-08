"""週のピン (青ピン) — PATCH /api/v1/schedule/v2/visits/{visit_id}/week-pin

PO 決定 2026-08-08〜09 / 仕様: docs/plans/pin-and-movability-spec.md

実体は ``visits.week_pinned`` フラグ (migration 0066)。source には触れないため、
カイポケ取込 (import) の訪問にも掛け外しでき、解除しても出所は失われない。
(旧方式は source='manual_week' への書き換えで、取込週では 119 件中 5 件しか
固定できず、さらに一括解除が取込の保護を剥がす欠陥があった。)

検証観点:
  1. 青ピンを刺すと week_pinned=true (source は不変)
  2. 型とズレていても刺せる / **import にも刺せる** (出所保持)
  3. 解除でフラグが下りる。manual_week だけは source='auto' へ戻す。
     **その場では訪問を動かさない**
  4. planned 以外は 422
  5. audit_log に before/after が残る
  6. RBAC: staff は不可 / 冪等
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
    week_pinned: bool = False,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=date(2026, 9, 4),  # 金曜
        start_time=start,
        end_time=time(start.hour + 1, start.minute),
        type="regular",
        status=status_value,
        source=source,
        week_pinned=week_pinned,
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
    # フラグ方式: source は不変 (出所を保持)。
    assert body["source"] == "auto"

    await db.refresh(visit)
    assert visit.week_pinned is True
    assert visit.source == "auto"


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
    assert visit.week_pinned is True
    assert visit.source == "auto"
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
    visit = await _make_visit(
        db, patient=patient, source="manual_week", start=time(10, 25), week_pinned=True
    )

    res = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(admin),
        json={"pinned": False},
    )
    assert res.status_code == 200, res.text
    assert res.json()["pinned"] is False
    assert res.json()["source"] == "auto"

    await db.refresh(visit)
    assert visit.week_pinned is False
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
async def test_week_pin_allows_import_and_manual_keeping_source(client, db, source: str) -> None:
    """PO 決定 (2026-08-09): import / manual にも刺せる。source は不変 = 出所保持。

    往復 (刺す→外す) しても source が変わらないこと = 「一括解除がカイポケ週の
    保護を剥がす」旧方式の欠陥が構造的に起きないことの確認。
    """
    admin = await _make_user(db, email=f"wp-5-{source}@example.com", role="admin")
    patient = await _make_patient(db, code=f"WP-5-{source}")
    visit = await _make_visit(db, patient=patient, source=source)

    for pinned, expected_flag in ((True, True), (False, False)):
        res = await client.patch(
            _WEEK_PIN_URL.format(vid=visit.id),
            headers=_bearer(admin),
            json={"pinned": pinned},
        )
        assert res.status_code == 200, res.text
        assert res.json()["source"] == source
        await db.refresh(visit)
        assert visit.week_pinned is expected_flag
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
    assert row.before == {"week_pinned": False, "source": "auto"}
    assert row.after == {"week_pinned": True, "source": "auto"}


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
    visit = await _make_visit(db, patient=patient, source="auto", week_pinned=True)

    for _ in range(2):
        res = await client.patch(
            _WEEK_PIN_URL.format(vid=visit.id),
            headers=_bearer(admin),
            json={"pinned": True},
        )
        assert res.status_code == 200, res.text
        assert res.json()["pinned"] is True


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
async def test_bulk_week_pin_pins_all_planned_including_import(client, db) -> None:
    """今週全件固定 (PO 決定 2026-08-09): import / manual を含む planned 全件が対象。

    旧方式は import / manual を除外しており、カイポケ取込週では 119 件中 5 件しか
    固定されなかった (PO 指摘)。フラグ方式では source を問わず固定でき、
    source 自体は不変 = 出所を保持する。planned 以外だけが対象外。
    """
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
    assert body["target_count"] == 4
    assert body["updated_count"] == 4

    for v, expected_flag, expected_source in [
        (v_auto, True, "auto"),
        (v_reset, True, "reset_v2"),
        (v_manual, True, "manual"),
        (v_import, True, "import"),
        (v_done, False, "auto"),  # planned 以外は対象外
    ]:
        await db.refresh(v)
        assert v.week_pinned is expected_flag, f"{v.start_time}: flag"
        assert v.source == expected_source, f"{v.start_time}: source"


@pytest.mark.asyncio
async def test_bulk_week_pin_unpin_releases_flag_and_keeps_provenance(client, db) -> None:
    """今週全件解除: フラグが下り、manual_week だけ auto へ。import の出所は無傷。

    「一括解除がカイポケ週の保護を剥がす」旧方式の欠陥が起きないことの核心確認:
    解除後も import は import のまま = 週生成の削除対象にならない。
    """
    admin = await _make_user(db, email="wpb-2@example.com", role="admin")
    patient = await _make_patient(db, code="WPB-2")
    v_moved = await _make_visit(
        db, patient=patient, source="manual_week", start=time(9, 0), week_pinned=True
    )
    v_import = await _make_visit(
        db, patient=patient, source="import", start=time(10, 0), week_pinned=True
    )
    v_free = await _make_visit(db, patient=patient, source="manual", start=time(11, 0))

    res = await client.post(_BULK_URL, headers=_bearer(admin), json={**_BULK_WEEK, "pinned": False})
    assert res.status_code == 200, res.text
    assert res.json()["updated_count"] == 2

    await db.refresh(v_moved)
    await db.refresh(v_import)
    await db.refresh(v_free)
    assert v_moved.week_pinned is False
    assert v_moved.source == "auto"  # この週だけの手動配置は型の管理へ戻す
    assert v_moved.start_time == time(9, 0)  # 解除しても動かない
    assert v_import.week_pinned is False
    assert v_import.source == "import"  # 出所無傷 = 保護継続
    assert v_free.week_pinned is False  # 元々未固定 (対象外)


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


# ---------------------------------------------------------------------------
# 蓋ロック (PO 決定 2026-08-09): 青ピン中は人手でも配置を触れない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blue_lid_blocks_week_only_move(client, db) -> None:
    """青ピンの訪問は「今週だけ移動」も 422 (解除してから動かす 2 段操作)."""
    admin = await _make_user(db, email="lid-1@example.com", role="admin")
    patient = await _make_patient(db, code="LID-1")
    visit = await _make_visit(
        db, patient=patient, source="import", start=time(10, 25), week_pinned=True
    )

    res = await client.post(
        "/api/v1/schedule/v2/visit-move-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 36,
            "patient_id": str(patient.id),
            "old_weekday": 4,
            "old_start_time": "10:25",
            "new_weekday": 4,
            "new_start_time": "14:00",
        },
    )
    assert res.status_code == 422, res.text
    assert "青ピン" in res.json()["detail"]
    await db.refresh(visit)
    assert visit.start_time == time(10, 25)


@pytest.mark.asyncio
async def test_blue_lid_blocks_delete(client, db) -> None:
    """青ピンの訪問は削除も 422。解除すれば削除できる."""
    admin = await _make_user(db, email="lid-2@example.com", role="admin")
    patient = await _make_patient(db, code="LID-2")
    visit = await _make_visit(db, patient=patient, source="auto", week_pinned=True)

    res = await client.delete(f"/api/v1/visits/{visit.id}", headers=_bearer(admin))
    assert res.status_code == 422, res.text
    assert "青ピン" in res.json()["detail"]

    # 解除 → 削除できる。
    res2 = await client.patch(
        _WEEK_PIN_URL.format(vid=visit.id),
        headers=_bearer(admin),
        json={"pinned": False},
    )
    assert res2.status_code == 200
    res3 = await client.delete(f"/api/v1/visits/{visit.id}", headers=_bearer(admin))
    assert res3.status_code == 204, res3.text


@pytest.mark.asyncio
async def test_blue_lid_blocks_placement_patch_but_allows_meta(client, db) -> None:
    """青ピン中は時刻・日付の PATCH は 422。担当スタッフ等の非配置フィールドは可."""
    admin = await _make_user(db, email="lid-3@example.com", role="admin")
    patient = await _make_patient(db, code="LID-3")
    visit = await _make_visit(db, patient=patient, source="auto", week_pinned=True)

    res = await client.patch(
        f"/api/v1/visits/{visit.id}",
        headers=_bearer(admin),
        json={"start_time": "14:00:00", "end_time": "15:00:00"},
    )
    assert res.status_code == 422, res.text
    assert "青ピン" in res.json()["detail"]

    # 非配置フィールド (note) は通る。
    res2 = await client.patch(
        f"/api/v1/visits/{visit.id}",
        headers=_bearer(admin),
        json={"note": "鍵は裏口"},
    )
    assert res2.status_code == 200, res2.text
    await db.refresh(visit)
    assert visit.note == "鍵は裏口"
    assert visit.start_time == time(10, 25)
