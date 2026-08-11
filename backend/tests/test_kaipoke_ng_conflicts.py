"""カイポケ取込 dry-run の ⛔NG スタッフ衝突警告 (Phase 3).

正典設計書: docs/plans/patient-ng-staff-design.md §6 末尾 / §11。

方針: **カイポケが最終的な「正」** なので NG 該当でも取り込みはブロックしない。
dry-run (プレビュー) の応答に ``ngConflicts`` として可視化するだけ — 実適用
(apply) の挙動・件数・書き込み内容は一切変えない。
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import PatientNgStaff
from app.models.visit import Visit

# 差分側 (diff-inbound → apply-inbound) の共有フィクスチャ
from tests.test_kaipoke_inbound import (
    WEEK_START,
    _bearer,
    _diff_with_state,
    _kaipoke_csv,
    _kp_row,
    _make_admin,
    _seed_course,
    _seed_real_apply,
    _seed_second_staff,
    _seed_week,
    _staff_changed_state,
    stub_kaipoke,  # noqa: F401  (pytest fixture の再エクスポート)
)

# 置換側 (replace-inbound) のリクエストヘルパ
from tests.test_kaipoke_replace_inbound import _post_replace

APPLY_INBOUND_URL = "/api/v1/integrations/apply-inbound"


async def _add_ng(db, *, patient, staff, note: str | None = None) -> None:
    db.add(PatientNgStaff(patient_id=patient.id, staff_id=staff.id, note=note))
    await db.commit()


# ---------------------------------------------------------------------------
# 置換取り込み (replace-inbound)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_dry_run_reports_ng_conflicts(client, db, stub_kaipoke) -> None:  # noqa: F811
    """取込後に「コース担当 × 患者」が NG ペアになる → dry-run が警告を列挙する。"""
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _add_ng(db, patient=seeded["patient"], staff=seeded["staff"], note="相性")
    admin = await _make_admin(db)
    stub_kaipoke.responses["export"] = {
        "result": {
            "csv_content": _kaipoke_csv(_kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)))
        }
    }

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    # 取込自体はブロックしない (件数は従来どおり)
    assert body["inserted"] == 1
    conflicts = body["ngConflicts"]
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["patientName"] == seeded["patient"].name
    assert c["staffName"] == seeded["staff"].name
    assert c["date"] == "2026-07-07"
    assert c["weekday"] == 1
    assert c["courseCode"] == "A"


@pytest.mark.asyncio
async def test_replace_dry_run_no_ng_returns_empty(client, db, stub_kaipoke) -> None:  # noqa: F811
    """NG 行が無ければ ngConflicts は空 (従来どおり)。"""
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    admin = await _make_admin(db)
    stub_kaipoke.responses["export"] = {
        "result": {
            "csv_content": _kaipoke_csv(_kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)))
        }
    }

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=True)
    assert res.status_code == 200, res.text
    assert res.json()["ngConflicts"] == []


@pytest.mark.asyncio
async def test_replace_real_apply_unchanged_by_ng(client, db, stub_kaipoke) -> None:  # noqa: F811
    """NG があっても実適用は素通し (取込件数も書き込み結果も不変・警告は載せない)。"""
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _add_ng(db, patient=seeded["patient"], staff=seeded["staff"])
    admin = await _make_admin(db)
    stub_kaipoke.responses["export"] = {
        "result": {
            "csv_content": _kaipoke_csv(_kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)))
        }
    }

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["wiped"] == 3
    assert body["inserted"] == 1
    assert body["jobId"] is not None
    # 実適用の応答は警告を持たない (集計そのものを走らせない)
    assert body["ngConflicts"] == []

    visits = (await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all()
    assert len(visits) == 1
    assert visits[0].primary_staff_id == seeded["staff"].id


# ---------------------------------------------------------------------------
# smart-inbound (日単位ハイブリッド) — 置換パートに相乗りする
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smart_preview_replace_part_reports_ng(client, db, stub_kaipoke) -> None:  # noqa: F811
    """打刻なし週の統合プレビュー = 全日が置換担当 → replace.ngConflicts に載る。"""
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _add_ng(db, patient=seeded["patient"], staff=seeded["staff"])
    admin = await _make_admin(db)
    stub_kaipoke.responses["export"] = {
        "result": {
            "csv_content": _kaipoke_csv(_kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)))
        }
    }

    res = await client.post(
        "/api/v1/integrations/smart-inbound-preview",
        headers=_bearer(admin),
        json={"weekStart": WEEK_START.isoformat()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["protectedDays"] == []  # 打刻なし = 全日が置換担当
    conflicts = body["replace"]["ngConflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["staffName"] == seeded["staff"].name


# ---------------------------------------------------------------------------
# 差分取り込み (diff-inbound → apply-inbound)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_dry_run_reports_ng_conflicts(client, db, stub_kaipoke) -> None:  # noqa: F811
    """担当変更でコース担当が NG スタッフになる → dry-run が警告を返す。"""
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    sato = await _seed_second_staff(db, seeded["office"])
    await _seed_course(db, office=seeded["office"], staff=sato, weekday=1, code="B")
    await _add_ng(db, patient=seeded["patient"], staff=sato)

    body = await _diff_with_state(client, stub_kaipoke, admin, _staff_changed_state("佐藤　次郎"))
    res = await client.post(
        APPLY_INBOUND_URL,
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"]},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["dryRun"] is True
    assert out["updated"] == 1 and out["failed"] == 0  # 取込はブロックしない
    conflicts = out["ngConflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["patientName"] == seeded["patient"].name
    assert conflicts[0]["staffName"] == sato.name
    assert conflicts[0]["date"] == "2026-07-07"
    assert conflicts[0]["courseCode"] == "B"


@pytest.mark.asyncio
async def test_diff_dry_run_no_ng_returns_empty(client, db, stub_kaipoke) -> None:  # noqa: F811
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    sato = await _seed_second_staff(db, seeded["office"])
    await _seed_course(db, office=seeded["office"], staff=sato, weekday=1, code="B")

    body = await _diff_with_state(client, stub_kaipoke, admin, _staff_changed_state("佐藤　次郎"))
    res = await client.post(
        APPLY_INBOUND_URL,
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["ngConflicts"] == []


@pytest.mark.asyncio
async def test_diff_real_apply_unchanged_by_ng(client, db, stub_kaipoke) -> None:  # noqa: F811
    """NG があっても実適用は素通し (担当変更は反映され、警告は載らない)。"""
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    sato = await _seed_second_staff(db, seeded["office"])
    course_b = await _seed_course(db, office=seeded["office"], staff=sato, weekday=1, code="B")
    await _add_ng(db, patient=seeded["patient"], staff=sato)

    body = await _diff_with_state(client, stub_kaipoke, admin, _staff_changed_state("佐藤　次郎"))
    res = await client.post(
        APPLY_INBOUND_URL,
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": False},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["updated"] == 1 and out["failed"] == 0
    assert out["ngConflicts"] == []

    await db.refresh(seeded["tue"])
    assert seeded["tue"].primary_staff_id == sato.id
    assert seeded["tue"].course_id == course_b.id
