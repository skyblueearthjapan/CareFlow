"""NG 曜日 (weekly_pattern.ng_weekdays) の純関数・検査・計算経路のテスト (DB を使わない).

正典: docs/plans/biweekly-monthly-design-2026-10-30.md §9 (PO 決定 2026-10-30
「NG 曜日＝画面に出して全経路で守る」)。API の 422 は test_ng_weekdays_api.py。
"""

from __future__ import annotations

import uuid
from datetime import time

import pytest
from pydantic import ValidationError

from app.models import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.schemas.v2.patient import WeeklyPatternV2
from app.services.scheduling.auto_allocator_v2 import (
    _extract_weekly_entries,
    _g93_desired_weekdays,
    build_visits_for_pool,
)
from app.services.scheduling.layer1_expander import entry_staff_count
from app.services.scheduling.ng_weekdays import (
    NG_WEEKDAY_CODE,
    is_ng_weekday,
    ng_weekday_detail,
    ng_weekday_message,
    ng_weekdays_of,
    normalize_ng_weekdays,
    preferred_ng_conflicts,
    weekdays_label,
)
from app.services.scheduling.propose_slots_service import (
    CandidateInput,
    ExcludedReasonSummary,
    _target_weekdays,
    compute_all_proposed_slots,
)

# ---------------------------------------------------------------------------
# 純関数
# ---------------------------------------------------------------------------


def test_normalize_sorts_dedupes_and_accepts_codes_ints_and_jp() -> None:
    assert normalize_ng_weekdays(["Wed", "Mon", "Wed"]) == ["Mon", "Wed"]
    assert normalize_ng_weekdays([6, 0]) == ["Mon", "Sun"]
    assert normalize_ng_weekdays(["土", "日"]) == ["Sat", "Sun"]
    assert normalize_ng_weekdays([]) == []
    assert normalize_ng_weekdays(None) is None


@pytest.mark.parametrize("bad", ["Mon", ["Monday"], [7], [True], [None], 3])
def test_normalize_rejects_non_weekdays(bad) -> None:
    with pytest.raises(ValueError):
        normalize_ng_weekdays(bad)


def test_ng_weekdays_of_is_lenient() -> None:
    assert ng_weekdays_of({"ng_weekdays": ["Mon", "x", 9, "Fri"]}) == frozenset({0, 4})
    assert ng_weekdays_of({"ng_weekdays": "Mon"}) == frozenset()
    assert ng_weekdays_of({}) == frozenset()
    assert ng_weekdays_of(None) == frozenset()
    assert is_ng_weekday({"ng_weekdays": ["Tue"]}, 1)
    assert not is_ng_weekday({"ng_weekdays": ["Tue"]}, 2)


def test_message_and_detail() -> None:
    msg = ng_weekday_message("山田", 2)
    assert "山田様" in msg and "水曜日" in msg and "NG 曜日（訪問しない曜日）" in msg
    pid = uuid.uuid4()
    detail = ng_weekday_detail(patient_id=pid, patient_name="山田", weekday=2)
    assert detail == {
        "code": NG_WEEKDAY_CODE,
        "patient_id": str(pid),
        "weekday": 2,
        "message": msg,
    }
    assert weekdays_label({4, 0}) == "月・金"


def test_preferred_conflicts() -> None:
    wp = {
        "ng_weekdays": ["Mon", "Thu"],
        "preferred_weekdays": ["Mon", "Wed"],
        "entries": [{"weekday": "Thu"}],
    }
    assert preferred_ng_conflicts(wp) == [0, 3]
    assert preferred_ng_conflicts({"preferred_weekdays": ["Mon"]}) == []


# ---------------------------------------------------------------------------
# 検査 (schemas/v2/patient.py)
# ---------------------------------------------------------------------------


def test_schema_normalizes_ng_weekdays() -> None:
    wp = WeeklyPatternV2.model_validate({"ng_weekdays": ["Fri", "Mon", "Fri"]})
    assert wp.ng_weekdays == ["Mon", "Fri"]
    assert WeeklyPatternV2.model_validate({"ng_weekdays": []}).ng_weekdays == []
    assert WeeklyPatternV2.model_validate({}).ng_weekdays is None


def test_schema_rejects_bad_ng_weekdays() -> None:
    with pytest.raises(ValidationError):
        WeeklyPatternV2.model_validate({"ng_weekdays": ["Funday"]})


def test_schema_keeps_other_keys_alongside_ng_weekdays() -> None:
    dumped = WeeklyPatternV2.model_validate(
        {"ng_weekdays": ["Sun"], "preferred_weekdays": ["Mon"], "custom_key": 1}
    ).model_dump(mode="json")
    assert dumped["ng_weekdays"] == ["Sun"]
    assert dumped["preferred_weekdays"] == ["Mon"]
    assert dumped["custom_key"] == 1


# ---------------------------------------------------------------------------
# 空き枠の提案 (propose_slots_service)
# ---------------------------------------------------------------------------


def _candidate(**kw) -> CandidateInput:
    base = dict(
        lat=35.6,
        lng=140.1,
        service_minutes=30,
        time_type=None,
        preferred_start=None,
        preferred_end=None,
        preferred_weekdays=frozenset(),
        requires_multiple_staff=False,
        existing_patient_id=None,
    )
    base.update(kw)
    return CandidateInput(**base)


def test_target_weekdays_drop_ng() -> None:
    assert _target_weekdays(_candidate(ng_weekdays=frozenset({0, 6}))) == frozenset(range(1, 6))
    assert _target_weekdays(
        _candidate(preferred_weekdays=frozenset({0, 2}), ng_weekdays=frozenset({0}))
    ) == frozenset({2})


def test_all_preferred_days_ng_reports_ng_weekday_reason() -> None:
    excluded: list[ExcludedReasonSummary] = []
    out = compute_all_proposed_slots(
        {},
        {},
        _candidate(preferred_weekdays=frozenset({1}), ng_weekdays=frozenset({1})),
        office_ids=[],
        exclusions_out=excluded,
    )
    assert out == []
    assert [(e.reason, e.weekday) for e in excluded] == [("ng_weekday", 1)]


# ---------------------------------------------------------------------------
# 自動割当・保留プール (auto_allocator_v2)
# ---------------------------------------------------------------------------


def _patient(wp: dict, **kw) -> Patient:
    p = Patient(
        id=uuid.uuid4(),
        code="NG-1",
        name="患者NG",
        status="active",
        lat=35.6,
        lng=140.1,
        primary_office_id=uuid.uuid4(),
        weekly_pattern=wp,
        special_week_active=[],
        requires_multiple_staff=kw.pop("requires_multiple_staff", False),
    )
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_extract_weekly_entries_drops_ng_weekdays_summary_form() -> None:
    p = _patient(
        {
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
            "preferred_start": "10:00",
            "ng_weekdays": ["Wed"],
        }
    )
    assert [e[0] for e in _extract_weekly_entries(p)] == [0, 4]


def test_extract_weekly_entries_drops_ng_weekdays_entries_form() -> None:
    p = _patient(
        {
            "entries": [
                {"weekday": "Tue", "preferred_start": "10:00"},
                {"weekday": "Thu", "preferred_start": "10:00"},
            ],
            "ng_weekdays": ["Tue"],
        }
    )
    assert [e[0] for e in _extract_weekly_entries(p)] == [3]


def _pfv(patient: Patient, weekday: int) -> PatientFixedVisit:
    return PatientFixedVisit(
        id=uuid.uuid4(),
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=False,
        movability="unknown",
        visit_week="every",
    )


def test_pool_candidates_skip_ng_weekday_for_pattern_and_fixed_sources() -> None:
    p = _patient(
        {"preferred_weekdays": ["Mon", "Tue"], "preferred_start": "10:00", "ng_weekdays": ["Mon"]}
    )
    from_pattern = build_visits_for_pool([p])
    assert {v.weekday for v in from_pattern} == {1}
    from_fixed = build_visits_for_pool(
        [p], fixed_by_patient={p.id: [_pfv(p, 0), _pfv(p, 2)]}, use_fixed_as_source=True
    )
    assert {v.weekday for v in from_fixed} == {2}


def test_g93_desired_weekdays_ignore_ng_preference() -> None:
    p = _patient({"preferred_weekdays": ["Mon", "Tue"], "ng_weekdays": ["Mon"]})
    assert _g93_desired_weekdays(p, []) == {1}


# ---------------------------------------------------------------------------
# 2 名体制の単一ソース (layer1 の entries の経路)
# ---------------------------------------------------------------------------


def test_entry_staff_count_follows_requires_multiple_staff() -> None:
    two = _patient({}, requires_multiple_staff=True)
    one = _patient({}, requires_multiple_staff=False)
    # 患者が 2 名体制なら entry の staff_count は見ない.
    assert entry_staff_count(two, {"staff_count": 1}) == (2, False)
    assert entry_staff_count(two, {}) == (2, False)
    # 患者が 1 名で entry だけ 2 → 今までどおり 2 名 + 二重の設定として知らせる.
    assert entry_staff_count(one, {"staff_count": 2}) == (2, True)
    assert entry_staff_count(one, {"staff_count": 1}) == (1, False)
    assert entry_staff_count(one, {"staff_count": 5}) == (1, False)
    assert entry_staff_count(one, {}) == (1, False)
