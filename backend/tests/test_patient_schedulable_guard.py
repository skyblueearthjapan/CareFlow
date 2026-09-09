"""入口ガード (非稼働患者を予定に入れさせない) の単体テスト.

正典 = docs/plans/patient-status-schedule-design-2026-09-09.md §7-3(d)。
Phase 2 でエンドポイントへ結線する前に **契約 (422 の形)** を固定する。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.office import Office
from app.models.patient import Patient
from app.services.patient_status_sync import is_schedulable_status, status_label
from app.services.scheduling.guards import (
    PATIENT_NOT_ACTIVE_CODE,
    ensure_patient_schedulable,
    split_schedulable_patient_ids,
)


async def _make_patient(db, *, code: str, status: str, name: str | None = None) -> Patient:
    patient = Patient(
        code=code,
        name=name or f"患者{code}",
        status=status,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


# ---------------------------------------------------------------------------
# ラベル / 判定 (語彙の単一ソース = app.services.patient_status_sync)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("active", "稼働中"),
        ("suspended", "一時休止"),
        ("admitted", "入院中"),
        ("pending", "開始前"),
        ("cancelled", "解約済み"),
        ("inactive", "一時休止"),  # v1 の残骸も共有ラベルは引ける
        ("bogus", "bogus"),  # 未知の値は生値
        (None, "不明"),
    ],
)
def test_status_label(value: str | None, expected: str) -> None:
    assert status_label(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("active", True), ("pending", False), ("admitted", False), (None, False)],
)
def test_is_schedulable_status(value: str | None, expected: bool) -> None:
    assert is_schedulable_status(value) is expected


# ---------------------------------------------------------------------------
# ensure_patient_schedulable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_returns_patient_when_active(db) -> None:
    patient = await _make_patient(db, code="GUARD-A", status="active")
    got = await ensure_patient_schedulable(db, patient.id)
    assert got.id == patient.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("admitted", "入院中"),
        ("suspended", "一時休止"),
        ("pending", "開始前"),
        ("cancelled", "解約済み"),
    ],
)
async def test_ensure_raises_422_for_non_active(db, status: str, label: str) -> None:
    patient = await _make_patient(db, code=f"GUARD-{status}", status=status, name="小湊")
    with pytest.raises(HTTPException) as exc:
        await ensure_patient_schedulable(db, patient.id)
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert detail["code"] == PATIENT_NOT_ACTIVE_CODE
    assert detail["patient_id"] == str(patient.id)
    assert detail["status"] == status
    assert detail["status_label"] == label
    assert detail["can_override"] is True
    assert detail["message"] == f"小湊様は{label}のため予定に入れられません"


@pytest.mark.asyncio
async def test_ensure_raises_404_for_missing_patient(db) -> None:
    with pytest.raises(HTTPException) as exc:
        await ensure_patient_schedulable(db, uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_ensure_raises_404_for_soft_deleted_patient(db) -> None:
    from datetime import UTC, datetime

    patient = await _make_patient(db, code="GUARD-DEL", status="active")
    patient.deleted_at = datetime.now(UTC)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await ensure_patient_schedulable(db, patient.id)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# split_schedulable_patient_ids (一括系)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_separates_active_and_non_active(db) -> None:
    office = Office(name="guard-office")
    db.add(office)
    await db.flush()
    active = await _make_patient(db, code="GUARD-S1", status="active")
    admitted = await _make_patient(db, code="GUARD-S2", status="admitted", name="山田")
    active2 = await _make_patient(db, code="GUARD-S3", status="active")
    missing = uuid4()

    ok, excluded = await split_schedulable_patient_ids(
        db, [active.id, admitted.id, active2.id, missing, active.id]
    )

    # 入力順を保ち、重複は 1 回だけ
    assert ok == [active.id, active2.id]
    assert [e["patient_id"] for e in excluded] == [str(admitted.id), str(missing)]
    assert excluded[0]["status"] == "admitted"
    assert excluded[0]["status_label"] == "入院中"
    assert excluded[0]["message"] == "山田様は入院中のため予定に入れられません"
    assert excluded[1]["status"] is None


@pytest.mark.asyncio
async def test_split_with_empty_input(db) -> None:
    assert await split_schedulable_patient_ids(db, []) == ([], [])
