"""Data models for the careflow allocation engine.

Ported from PlaywrightTest1/lib/allocation_models.py (Apache License 2.0).
"""

from dataclasses import dataclass, field


@dataclass
class Patient:
    pid: str
    name: str
    area: str = ""
    lat: float | None = None
    lng: float | None = None
    service_minutes: int = 60
    weekly_count: int = 0
    need_staff: int = 1
    sex_limit: str = ""  # "女性のみ", "男性のみ", ""
    cont_pref: str = ""  # "同じ人希望", "ローテーション優先", "どちらでも", ""
    fixed_staff_ids: list[str] = field(default_factory=list)
    fixed_type: str = ""  # "必須", "優先", ""
    ng_staff_ids: list[str] = field(default_factory=list)
    pref_days: list[str] = field(default_factory=list)  # ["Mon","Wed","Fri"]
    ng_days: list[str] = field(default_factory=list)  # ["Sat","Sun"]
    time_type: str = ""  # "固定","午前","午後","終日","時間帯"
    start_pref_min: int | None = None  # minutes from midnight
    end_pref_min: int | None = None


@dataclass
class Staff:
    sid: str
    name: str
    gender: str = ""  # "男性", "女性"
    lat: float | None = None
    lng: float | None = None
    shift_start_min: int = 540  # 09:00
    shift_end_min: int = 1080  # 18:00
    work_days: list[str] = field(default_factory=list)  # ["Mon","Tue",...]
    areas: list[str] = field(default_factory=list)
    max_per_day: int = 999
    alloc_pref: str = "均等"  # "均等","多め","少なめ"

    def soft_cap(self) -> int:
        """maxPerDay adjusted by allocation preference."""
        if self.alloc_pref == "多め":
            return self.max_per_day
        elif self.alloc_pref == "少なめ":
            return max(1, self.max_per_day - 2)
        else:  # 均等 (default)
            return max(1, self.max_per_day - 1)


@dataclass
class VisitRequest:
    """A single visit request from the weekly request sheet."""

    request_id: str = ""
    date_str: str = ""  # "yyyy/MM/dd"
    weekday: str = ""  # "Mon","Tue",...
    pid: str = ""
    pname: str = ""
    area: str = ""
    start_min: int | None = None
    end_min: int | None = None
    service_min: int = 60
    need_staff: int = 1
    specified_staff_ids: list[str] = field(default_factory=list)
    specified_type: str = ""  # "必須","優先"
    ng_staff_ids: list[str] = field(default_factory=list)
    sex_limit: str = ""
    cont_pref: str = ""
    time_type: str = ""
    earliest_min: int | None = None
    latest_min: int | None = None
    prev_staff_id: str = ""
    prev_staff_name: str = ""
    change_type: str = "通常"  # "通常","変更","追加","キャンセル","特別"
    note: str = ""


@dataclass
class Event:
    """Staff event (meeting, training, etc.)"""

    event_id: str
    staff_id: str
    date_str: str
    event_type: str = ""
    title: str = ""
    start_min: int | None = None
    end_min: int | None = None
    duration_min: int = 60
    fixed_slot: bool = True


@dataclass
class StaffChange:
    """Staff availability restriction for a specific date."""

    staff_id: str
    date_str: str
    restriction_type: str  # "休み","終日不可","午前休","午後休","遅刻","早退","時間指定"
    start_min: int | None = None
    end_min: int | None = None


@dataclass
class WeeklyPattern:
    """Weekly visit pattern for a patient (from the new pattern editor)."""

    pid: str
    pname: str = ""
    day_code: str = ""  # "Mon","Tue",...
    start_min: int | None = None
    end_min: int | None = None
    service_min: int = 60
    need_staff: int = 1
    note: str = ""


@dataclass
class ConfirmedHistory:
    """Confirmed schedule history record for rotation lookup."""

    week_start: str  # "yyyy/MM/dd"
    date_str: str
    pid: str
    staff_id: str
    staff_name: str = ""


@dataclass
class AssignmentResult:
    """A single assignment result row."""

    visit_id: str = ""
    date_str: str = ""
    weekday: str = ""
    staff_id: str = ""
    staff_name: str = ""
    pid: str = ""
    pname: str = ""
    area: str = ""
    start_min: int | None = None
    end_min: int | None = None
    service_min: int = 60
    time_type: str = ""
    earliest_min: int | None = None
    latest_min: int | None = None
    note: str = ""
    is_event: bool = False
    is_coupled: bool = False
    movement_km: float | None = None


@dataclass
class Interval:
    """Time interval in minutes."""

    start: int
    end: int


@dataclass
class PatientChange:
    """Patient-specific visit change for a specific date."""

    pid: str
    date_str: str
    operation: str = ""  # "追加","キャンセル","時間変更","スタッフ変更"
    time_type: str = ""
    start_min: int | None = None
    end_min: int | None = None
    earliest_min: int | None = None
    latest_min: int | None = None
    service_min: int | None = None
    need_staff: int | None = None
    specified_staff_ids: list[str] = field(default_factory=list)
    specified_type: str = ""
    ng_staff_ids: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class SpecialWeekHeader:
    """Special week header (one per patient per week)."""

    special_week_id: str
    pid: str
    pname: str = ""
    week_start: str = ""
    mode: str = "ADD"  # "ADD" or "REPLACE"
    reason: str = ""


@dataclass
class SpecialWeekDetail:
    """Special week detail row (specific visit override)."""

    special_week_id: str
    pid: str = ""
    date_str: str = ""
    time_type: str = ""
    start_min: int | None = None
    end_min: int | None = None
    earliest_min: int | None = None
    latest_min: int | None = None
    service_min: int = 60
    need_staff: int = 1
    note: str = ""
    change_policy: str = ""


@dataclass
class MentorPair:
    """Staff accompaniment (trainee-mentor pair)."""

    trainee_staff_id: str
    mentor_staff_id: str = ""
    start_date: str = ""
    end_date: str = ""
    band: str = ""  # "午前","午後","終日"
    start_min: int | None = None
    end_min: int | None = None
    day_condition: str = ""
    priority: str = ""
