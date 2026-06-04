"""Phase G-86 A-1: generate_next_patient_code の単体試験.

採番仕様 (`docs/plans/phase-g86-patient-code-autonumber.md`):
    - ``^P(\\d+)$`` 一致コードの数値最大 + 1 を ``f"P{n:03d}"`` で返す。
    - 一致無しなら ``P001``。1000 以上は自然桁。
    - deleted_at を問わず全 ``patients`` 行を対象 (UNIQUE 衝突回避)。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models import Patient
from app.services.patient_code import generate_next_patient_code


async def _add_patient(db, *, code: str, deleted: bool = False) -> Patient:
    p = Patient(code=code, name="患者", status="active")
    if deleted:
        p.deleted_at = datetime(2026, 1, 1, 0, 0, 0)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@pytest.mark.asyncio
async def test_generate_next_returns_p001_when_no_patients(db) -> None:
    assert await generate_next_patient_code(db) == "P001"


@pytest.mark.asyncio
async def test_generate_next_returns_p001_when_no_p_codes(db) -> None:
    # P 系 (^P\d+$) でないコードのみ → P001。
    await _add_patient(db, code="X-001")
    await _add_patient(db, code="P-NEW-001")  # ハイフン入りは ^P\d+$ に一致しない
    assert await generate_next_patient_code(db) == "P001"


@pytest.mark.asyncio
async def test_generate_next_is_max_plus_one(db) -> None:
    await _add_patient(db, code="P002")
    await _add_patient(db, code="P094")
    await _add_patient(db, code="P046")
    assert await generate_next_patient_code(db) == "P095"


@pytest.mark.asyncio
async def test_generate_next_handles_sparse_numbers(db) -> None:
    # 飛び番でも max+1 (最大を見る)。
    await _add_patient(db, code="P001")
    await _add_patient(db, code="P050")
    assert await generate_next_patient_code(db) == "P051"


@pytest.mark.asyncio
async def test_generate_next_includes_deleted_rows(db) -> None:
    # soft-deleted 行のコードも考慮 (UNIQUE 制約が効くため)。
    await _add_patient(db, code="P010")
    await _add_patient(db, code="P099", deleted=True)
    assert await generate_next_patient_code(db) == "P100"


@pytest.mark.asyncio
async def test_generate_next_zero_pads_to_three_digits(db) -> None:
    await _add_patient(db, code="P008")
    assert await generate_next_patient_code(db) == "P009"


@pytest.mark.asyncio
async def test_generate_next_natural_digits_over_999(db) -> None:
    await _add_patient(db, code="P999")
    assert await generate_next_patient_code(db) == "P1000"
