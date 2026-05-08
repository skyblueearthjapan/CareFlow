"""Tests for scripts/cleanup_visits_duplicate.py (W34 (A)).

カバー範囲:
  1. _source_rank: source 優先度
  2. _DuplicateGroup.select_keeper: manual > kaipoke > auto, created_at で最古を残す
  3. _scan_duplicates: (patient_id, visit_date, start_time) 重複を検出
  4. _scan_duplicates: 1 件しか無いキー / deleted_at NOT NULL は対象外
  5. _apply_soft_delete: 残す 1 件以外を deleted_at = now で論理削除
  6. dry-run vs apply の挙動差
"""

# ruff: noqa: I001
from __future__ import annotations

import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import uuid4

import pytest

# sys.path にスクリプトディレクトリを追加
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from cleanup_visits_duplicate import (  # noqa: E402
    _DuplicateGroup,
    _Report,
    _apply_soft_delete,
    _scan_duplicates,
    _source_rank,
)


# ---------------------------------------------------------------------------
# 1) _source_rank
# ---------------------------------------------------------------------------


def test_source_rank_orders_manual_before_auto() -> None:
    """manual が最優先 (rank 最小), auto が最後尾 (rank 最大)."""
    assert _source_rank("manual") < _source_rank("kaipoke")
    assert _source_rank("kaipoke") < _source_rank("ai")
    assert _source_rank("ai") < _source_rank("auto")
    # 未知 source は auto と同等
    assert _source_rank("unknown") == _source_rank("auto")
    # None も auto と同等
    assert _source_rank(None) == _source_rank("auto")


# ---------------------------------------------------------------------------
# 2) _DuplicateGroup.select_keeper
# ---------------------------------------------------------------------------


class _StubVisit:
    """Visit ORM の最小モックスタブ. select_keeper は属性のみ参照."""

    def __init__(
        self,
        *,
        source: str,
        created_at: datetime,
        id: str | None = None,
    ) -> None:
        self.source = source
        self.created_at = created_at
        self.id = id or str(uuid4())


def test_select_keeper_prefers_manual_over_auto() -> None:
    """manual が混じれば manual を残す (created_at が新しくても)."""
    pid = uuid4()
    visit_auto_old = _StubVisit(source="auto", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    visit_manual_new = _StubVisit(source="manual", created_at=datetime(2026, 5, 1, tzinfo=UTC))
    visit_auto_mid = _StubVisit(source="auto", created_at=datetime(2026, 3, 1, tzinfo=UTC))

    g = _DuplicateGroup(
        patient_id=pid,
        patient_name="石塚 麻衣",
        visit_date=date(2026, 5, 4),
        start_time=time(10, 0),
        visits=[visit_auto_old, visit_manual_new, visit_auto_mid],
    )
    keeper = g.select_keeper()
    assert keeper is visit_manual_new, "manual が最優先で残るはず"


def test_select_keeper_prefers_oldest_when_same_source() -> None:
    """同 source なら created_at の最古を残す."""
    pid = uuid4()
    v1 = _StubVisit(source="auto", created_at=datetime(2026, 5, 1, tzinfo=UTC))
    v2 = _StubVisit(source="auto", created_at=datetime(2026, 5, 2, tzinfo=UTC))
    v3 = _StubVisit(source="auto", created_at=datetime(2026, 5, 3, tzinfo=UTC))

    g = _DuplicateGroup(
        patient_id=pid,
        patient_name="A",
        visit_date=date(2026, 5, 4),
        start_time=time(10, 0),
        visits=[v3, v1, v2],
    )
    keeper = g.select_keeper()
    assert keeper is v1, "最古が残るはず"


def test_select_keeper_manual_oldest_among_manuals() -> None:
    """manual が複数あれば manual の最古を残す."""
    pid = uuid4()
    m_old = _StubVisit(source="manual", created_at=datetime(2026, 5, 1, tzinfo=UTC))
    m_new = _StubVisit(source="manual", created_at=datetime(2026, 5, 5, tzinfo=UTC))
    a_old = _StubVisit(source="auto", created_at=datetime(2026, 4, 1, tzinfo=UTC))

    g = _DuplicateGroup(
        patient_id=pid,
        patient_name="A",
        visit_date=date(2026, 5, 4),
        start_time=time(10, 0),
        visits=[a_old, m_new, m_old],
    )
    keeper = g.select_keeper()
    assert keeper is m_old, "manual の最古が残るはず"


def test_to_delete_returns_all_but_keeper() -> None:
    """to_delete() は keeper 以外の全 visit を返す."""
    pid = uuid4()
    keeper = _StubVisit(source="manual", created_at=datetime(2026, 5, 1, tzinfo=UTC))
    extras = [
        _StubVisit(source="manual", created_at=datetime(2026, 5, 5, tzinfo=UTC)),
        _StubVisit(source="auto", created_at=datetime(2026, 5, 2, tzinfo=UTC)),
        _StubVisit(source="auto", created_at=datetime(2026, 5, 3, tzinfo=UTC)),
    ]
    g = _DuplicateGroup(
        patient_id=pid,
        patient_name="A",
        visit_date=date(2026, 5, 4),
        start_time=time(10, 0),
        visits=[*extras, keeper],
    )
    to_delete = g.to_delete()
    assert len(to_delete) == 3
    assert keeper not in to_delete
    for e in extras:
        assert e in to_delete


# ---------------------------------------------------------------------------
# 3) _scan_duplicates: 重複検出
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_finds_duplicates_only(_engine, db) -> None:
    """重複している (patient,date,start_time) のみ検出される.

    各カテゴリの fixture を入れて scan が想定どおり 2 グループだけ
    返すことを検証する.
    """
    from app.models.patient import Patient
    from app.models.visit import Visit

    # patient
    p1 = Patient(code="W34-001", name="石塚 麻衣", status="active")
    p2 = Patient(code="W34-002", name="単独 患者", status="active")
    db.add_all([p1, p2])
    await db.commit()
    await db.refresh(p1)
    await db.refresh(p2)

    visit_date = date(2026, 5, 4)  # Mon

    # ----- グループ A: 重複 3 件 (auto x2 + manual x1) -----
    a_auto1 = Visit(
        patient_id=p1.id,
        visit_date=visit_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
    )
    a_auto2 = Visit(
        patient_id=p1.id,
        visit_date=visit_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
    )
    a_manual = Visit(
        patient_id=p1.id,
        visit_date=visit_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=1,
    )
    # ----- グループ B: 重複 2 件 (manual x2) -----
    b_manual1 = Visit(
        patient_id=p1.id,
        visit_date=visit_date,
        start_time=time(10, 15),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=1,
    )
    b_manual2 = Visit(
        patient_id=p1.id,
        visit_date=visit_date,
        start_time=time(10, 15),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=1,
    )
    # ----- 単独 (重複しない): スキャン結果に含まれない -----
    solo = Visit(
        patient_id=p1.id,
        visit_date=visit_date,
        start_time=time(11, 30),
        end_time=time(12, 0),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
    )
    # ----- 別 patient で同 (date, start_time): patient が異なれば重複でない -----
    other_patient_visit = Visit(
        patient_id=p2.id,
        visit_date=visit_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
    )
    # ----- soft-deleted: 既に消えている重複は対象外 -----
    soft_deleted_dup = Visit(
        patient_id=p1.id,
        visit_date=visit_date,
        start_time=time(11, 30),
        end_time=time(12, 0),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
        deleted_at=datetime(2026, 5, 5, tzinfo=UTC),
    )
    db.add_all(
        [
            a_auto1,
            a_auto2,
            a_manual,
            b_manual1,
            b_manual2,
            solo,
            other_patient_visit,
            soft_deleted_dup,
        ]
    )
    await db.commit()

    groups = await _scan_duplicates()
    # グループ A (10:00) と B (10:15) の 2 組のみ検出されるはず
    assert len(groups) == 2, (
        f"重複グループは 2 組のはず, got {len(groups)} groups: "
        f"{[(g.start_time, len(g.visits)) for g in groups]}"
    )

    by_time = {g.start_time: g for g in groups}
    assert time(10, 0) in by_time
    assert time(10, 15) in by_time
    # 11:30 (solo + soft_deleted_dup の active は 1 件) は重複でない
    assert time(11, 30) not in by_time

    # グループ A は 3 件 (a_auto1, a_auto2, a_manual)
    g_a = by_time[time(10, 0)]
    assert len(g_a.visits) == 3
    assert g_a.patient_id == p1.id
    assert g_a.patient_name == "石塚 麻衣"

    # グループ A の keeper は manual のはず
    keeper_a = g_a.select_keeper()
    assert keeper_a.source == "manual"
    assert keeper_a.id == a_manual.id

    # グループ B は 2 件 (manual x2), keeper は最古
    g_b = by_time[time(10, 15)]
    assert len(g_b.visits) == 2
    keeper_b = g_b.select_keeper()
    assert keeper_b.source == "manual"


# ---------------------------------------------------------------------------
# 4) _apply_soft_delete: 残す 1 件以外を deleted_at = now で論理削除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_soft_delete_removes_duplicates_keeping_one(_engine, db) -> None:
    """apply 後, 各グループは active 1 件のみ. 残りは deleted_at NOT NULL."""
    from sqlalchemy import select

    from app.models.patient import Patient
    from app.models.visit import Visit

    p = Patient(code="W34-010", name="apply 検証", status="active")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    visit_date = date(2026, 5, 4)

    # 重複: auto x2 + manual x1 (3 件)
    auto1 = Visit(
        patient_id=p.id,
        visit_date=visit_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
    )
    auto2 = Visit(
        patient_id=p.id,
        visit_date=visit_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
    )
    manual1 = Visit(
        patient_id=p.id,
        visit_date=visit_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=1,
    )
    db.add_all([auto1, auto2, manual1])
    await db.commit()

    # scan + apply
    groups = await _scan_duplicates()
    assert len(groups) == 1
    report = _Report()
    report.groups = len(groups)
    await _apply_soft_delete(groups, report)

    # 別セッションで現状を確認 (apply は別 session で commit している)
    await db.rollback()
    rows = list(
        await db.scalars(select(Visit).where(Visit.patient_id == p.id).order_by(Visit.created_at))
    )
    assert len(rows) == 3, "物理削除はされない (3 行残る)"
    active = [v for v in rows if v.deleted_at is None]
    deleted = [v for v in rows if v.deleted_at is not None]
    assert len(active) == 1, f"active は 1 件のみ残るはず, got {len(active)}"
    assert len(deleted) == 2, f"残り 2 件は soft-delete されているはず, got {len(deleted)}"
    # 残った 1 件は manual (manual 優先ルール)
    assert active[0].source == "manual"

    # report 集計
    assert report.kept == 1
    assert report.soft_deleted == 2

    # 再 scan で 0 組 (冪等)
    again = await _scan_duplicates()
    assert len(again) == 0, "apply 後の再 scan で重複が残っているのは異常"


@pytest.mark.asyncio
async def test_apply_soft_delete_idempotent(_engine, db) -> None:
    """apply を 2 回叩いても 2 回目は何も起こらない (冪等)."""
    from sqlalchemy import select

    from app.models.patient import Patient
    from app.models.visit import Visit

    p = Patient(code="W34-011", name="冪等検証", status="active")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    db.add_all(
        [
            Visit(
                patient_id=p.id,
                visit_date=date(2026, 5, 4),
                start_time=time(9, 0),
                end_time=time(10, 0),
                type="regular",
                status="planned",
                source="auto",
                required_staff_count=1,
            ),
            Visit(
                patient_id=p.id,
                visit_date=date(2026, 5, 4),
                start_time=time(9, 0),
                end_time=time(10, 0),
                type="regular",
                status="planned",
                source="auto",
                required_staff_count=1,
            ),
        ]
    )
    await db.commit()

    # 1 回目: 1 件 soft-delete
    groups1 = await _scan_duplicates()
    assert len(groups1) == 1
    rep1 = _Report()
    await _apply_soft_delete(groups1, rep1)
    assert rep1.soft_deleted == 1

    # 2 回目: 何も検出されない
    groups2 = await _scan_duplicates()
    assert len(groups2) == 0

    await db.rollback()
    rows = list(await db.scalars(select(Visit).where(Visit.patient_id == p.id)))
    active = [v for v in rows if v.deleted_at is None]
    assert len(active) == 1
