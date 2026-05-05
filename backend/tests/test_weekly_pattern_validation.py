"""Validation tests for the WeeklyPatternSchema (Pydantic v2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.patient import PatientCreate, WeeklyPatternSchema


def test_weekly_pattern_valid_payload_parses() -> None:
    schema = WeeklyPatternSchema.model_validate(
        {
            "frequency_per_week": 3,
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
            "weekday_priority": "高",
            "service_minutes": 60,
            "time_type": "時間帯",
            "preferred_start": "09:00",
            "preferred_end": "10:30",
            "ng_weekdays": ["Sat", "Sun"],
        }
    )
    assert schema.frequency_per_week == 3
    assert schema.preferred_weekdays == ["Mon", "Wed", "Fri"]
    assert schema.time_type == "時間帯"
    assert schema.preferred_start == "09:00"


def test_weekly_pattern_rejects_invalid_time_type() -> None:
    with pytest.raises(ValidationError):
        WeeklyPatternSchema.model_validate(
            {
                "frequency_per_week": 2,
                "preferred_weekdays": ["Mon"],
                "weekday_priority": "中",
                "service_minutes": 30,
                "time_type": "夜間",  # not in Literal[...]
                "preferred_start": None,
                "preferred_end": None,
                "ng_weekdays": [],
            }
        )


def test_weekly_pattern_rejects_invalid_hhmm() -> None:
    with pytest.raises(ValidationError):
        WeeklyPatternSchema.model_validate(
            {
                "frequency_per_week": 1,
                "preferred_weekdays": ["Mon"],
                "weekday_priority": "低",
                "service_minutes": 30,
                "time_type": "固定",
                "preferred_start": "9:00",  # missing leading zero -> bad HH:MM
                "preferred_end": "10:00",
                "ng_weekdays": [],
            }
        )


def test_weekly_pattern_rejects_unknown_weekday() -> None:
    with pytest.raises(ValidationError):
        WeeklyPatternSchema.model_validate(
            {
                "frequency_per_week": 1,
                "preferred_weekdays": ["Monday"],  # full names not allowed
                "weekday_priority": "中",
                "service_minutes": 30,
                "time_type": "固定",
                "ng_weekdays": [],
            }
        )


def test_weekly_pattern_allows_extra_keys_for_forward_compat() -> None:
    # extra='allow' keeps clients sending future fields working.
    schema = WeeklyPatternSchema.model_validate(
        {
            "frequency_per_week": 1,
            "preferred_weekdays": ["Mon"],
            "weekday_priority": "中",
            "service_minutes": 30,
            "time_type": "固定",
            "preferred_start": "09:00",
            "preferred_end": "09:30",
            "ng_weekdays": [],
            "future_field": "ok",
        }
    )
    assert schema.frequency_per_week == 1


def test_patient_create_embeds_weekly_pattern() -> None:
    patient = PatientCreate.model_validate(
        {
            "code": "P999",
            "name": "テスト太郎",
            "weekly_pattern": {
                "frequency_per_week": 2,
                "preferred_weekdays": ["Tue", "Thu"],
                "weekday_priority": "中",
                "service_minutes": 45,
                "time_type": "午前",
                "preferred_start": None,
                "preferred_end": None,
                "ng_weekdays": [],
            },
        }
    )
    assert patient.weekly_pattern is not None
    assert patient.weekly_pattern.frequency_per_week == 2
    assert patient.weekly_pattern.time_type == "午前"


def test_patient_create_rejects_garbage_weekly_pattern() -> None:
    with pytest.raises(ValidationError):
        PatientCreate.model_validate(
            {
                "code": "P998",
                "name": "テスト花子",
                "weekly_pattern": {
                    "frequency_per_week": 99,  # > 7
                    "preferred_weekdays": ["Mon"],
                    "weekday_priority": "中",
                    "service_minutes": 30,
                    "time_type": "固定",
                    "ng_weekdays": [],
                },
            }
        )
