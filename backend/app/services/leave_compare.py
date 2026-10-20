"""休みの「カイポケと比べる」 — 材料の取り直しの計画・差分づくり・結果の保存 (段階 L #9).

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §7-4 (特に 7-4-3)・§2-5。
材料の取り直し (ジョブ・RPA の空き確認・待ち・保存) は段階 0 の
``app.services.kaipoke.sources``。ここは自分で RPA を呼ばない。

流れ:

1. ``start_leave_compare`` — その月の週の個別業務と実績 CSV の計画を作り、段階 0 の
   ``start_source_job`` (``op='leave-compare'``・``job_type='fetch'``) でジョブを立てる。
   他のジョブが動いていれば ``RpaJobConflictError`` (API は 409)。
2. ``run_leave_compare_job`` — 応答の後に自前のセッションで ``run_source_job`` を呼び、
   ジョブが ``completed`` で閉じたら ``compare_month`` で差分を作って保存する。
3. ``compare_month`` — ``kaipoke_source_snapshots`` などを読み、純粋な関数
   ``build_leave_diffs`` で差分を作り、``save_compare_run`` で保存する。

**材料が新しいと認める条件** = ``fetched_at`` が今回の leave-compare ジョブの開始
(``KaipokeJob.started_at``) 以降。新しくない週・無い週・``failed`` の週の日と、
``covered_dates`` に無い日 (``covered_dates`` が NULL・空の週は全部の日) は「未確認」
(差分に数える)。

「合わせる」ボタンの処理 (``apply_compare_actions``。``staff_leave.apply_staff_leave`` /
``cancel_staff_leave`` と同じトランザクションで ``sent_type`` を書く・§7-4-3 H-c):

=======================  ===============================  ======================================
差分の ``action``         ボタン                            処理
=======================  ===============================  ======================================
``add_to_rakusuke``       らく助に入れる (#2・#5)            R = K (``apply_staff_leave``)・S = K
``match_kaipoke_kind``    カイポケの種類に合わせる (#8・#11・#15)  R = K (``apply_staff_leave``)・S = K
``cancel_rakusuke``       らく助の休みを取り消す (#6 照合前・#9)  R = 無 (``cancel_staff_leave``)・
                                                           ``staff_leave_kaipoke`` の行を消す
``choose_kind``           種類を選ぶ (種類がわからない)        ``kaipoke_leave_judgements`` に
                                                           ``content_hash`` ごとに記録 → 比べ直す
``refetch_week``          この週を取り直す (未確認)            もう一度「カイポケと比べる」
None                      (ボタンなし) #4・#14・実績との       カイポケで直してから比べ直す
                          食い違い・職員が見つからない・
                          同じ日に 2 件
=======================  ===============================  ======================================
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.leave import (
    AM_OFF_UNTIL,
    DEFAULT_BUSINESS_END,
    DEFAULT_BUSINESS_START,
    FULL_DAY_OFF,
    LEAVE_TYPES,
    PM_OFF_FROM,
    classify_kaipoke_leave,
)
from app.db.session import get_session_factory
from app.models.kaipoke_job import KaipokeJob
from app.models.kaipoke_source_snapshot import KaipokeSourceSnapshot
from app.models.leave import (
    KaipokeLeaveJudgement,
    LeaveCompareRun,
    LeaveMonth,
    StaffLeaveKaipoke,
)
from app.models.staff import StaffShift, StaffWeeklyOverride
from app.services.kaipoke import sources
from app.services.kaipoke.inbound import load_staff_name_index
from app.services.kaipoke.name_match import match_name
from app.services.kaipoke.plan_actual_compare import (
    NO_STAFF_TOKENS,
    VISIT_BUSINESS_TYPES,
    norm_time,
)
from app.services.kaipoke.sources import (
    KIND_ACTUAL_CSV,
    KIND_TASKS,
    OP_LEAVE_COMPARE,
    OP_LEAVE_OUTBOUND,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    SourceSpec,
)
from app.services.leave_calendar import iso_key, leave_type_on
from app.services.scheduling.config import load_scheduling_config
from app.services.staff_leave import (
    LeaveApplyResult,
    StaffLeaveError,
    apply_staff_leave,
    cancel_staff_leave,
    notify_leave_results,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.user import User
    from app.services.kaipoke_client import KaipokeClient

logger = logging.getLogger(__name__)

# --- 差分の種類 (``LeaveDiff.kind``) ------------------------------------------------
#: #2 カイポケだけ休み。
DIFF_KAIPOKE_ONLY: Final = "kaipoke_only"
#: #4 カイポケで要対応 (らく助で取り消したのにカイポケに X が残る)。
DIFF_KAIPOKE_DELETE_NEEDED: Final = "kaipoke_delete_needed"
#: #5 カイポケは Y (らく助は取り消し済み)。
DIFF_KAIPOKE_OTHER_AFTER_CANCEL: Final = "kaipoke_other_after_cancel"
#: #6 照合前 = らく助だけの休み (以前から)。
DIFF_RAKUSUKE_ONLY_BASELINE: Final = "rakusuke_only_baseline"
#: #8 らく助は X・カイポケは Y (未送信)。
DIFF_TYPE_MISMATCH: Final = "type_mismatch"
#: #9 送ったあとカイポケで消されている。
DIFF_DELETED_IN_KAIPOKE: Final = "deleted_in_kaipoke"
#: #11 送ったあとカイポケで Y に変わっている。
DIFF_CHANGED_IN_KAIPOKE: Final = "changed_in_kaipoke"
#: #14 カイポケで要対応 (カイポケの Y を消す・X は送り直す)。
DIFF_KAIPOKE_RESEND_NEEDED: Final = "kaipoke_resend_needed"
#: #15 カイポケは Z。
DIFF_KAIPOKE_OTHER: Final = "kaipoke_other"
#: 件名で種類がわからない (管理者が選ぶ)。
DIFF_UNKNOWN_KIND: Final = "unknown_kind"
#: 未確認 (材料が新しくない・無い・失敗・表示されなかった日・実績 CSV が使えない)。
DIFF_UNCONFIRMED: Final = "unconfirmed"
#: 休みの日・半休の時間帯にカイポケの実績の訪問がある。
DIFF_ACTUAL_CONFLICT: Final = "actual_conflict"
#: カイポケの職員がらく助に見つからない。
DIFF_STAFF_NOT_FOUND: Final = "staff_not_found"
#: 同じ職員・同じ日に休みの予定が 2 件以上。
DIFF_DUPLICATE_LEAVE: Final = "duplicate_leave"

# --- 「合わせる」ボタン (``LeaveDiff.action``) --------------------------------------
ACTION_ADD_TO_RAKUSUKE: Final = "add_to_rakusuke"
ACTION_MATCH_KAIPOKE_KIND: Final = "match_kaipoke_kind"
ACTION_CANCEL_RAKUSUKE: Final = "cancel_rakusuke"
ACTION_CHOOSE_KIND: Final = "choose_kind"
ACTION_REFETCH_WEEK: Final = "refetch_week"
#: 実績の職員名が合わない差分の案内 (ボタンは無い・``action`` は None)。
GUIDE_FIX_STAFF_NAME: Final = "らく助の職員の名前をそろえて比べ直してください"

# --- 未確認の理由 -------------------------------------------------------------------
UNCONFIRMED_NO_SNAPSHOT: Final = "no_snapshot"
UNCONFIRMED_FAILED: Final = "failed"
UNCONFIRMED_STALE: Final = "stale"
UNCONFIRMED_NOT_COVERED: Final = "not_covered"
UNCONFIRMED_ACTUAL: Final = "actual_csv"
#: 実績 CSV の職員名がらく助に見つからない (その日の実績を確かめきれない)。
UNCONFIRMED_ACTUAL_STAFF: Final = "actual_staff"

#: 管理者が選んだ「休みではない」。
JUDGED_NOT_LEAVE: Final = "not_leave"

_USABLE: Final = (STATUS_OK, STATUS_EMPTY)
#: 実績 CSV の職員名の列 (主担当・同行)。
_CSV_STAFF_COLUMNS: Final = ("職員名１", "職員名２", "職員名３")
_SUNDAY: Final = 6
_HASH_SEP: Final = "\x1f"

_KIND_LABELS: Final = {
    "off": "公休",
    "am_off": "午前休",
    "pm_off": "午後休",
    "paid_off": "有給",
}
_UNCONFIRMED_LABELS: Final = {
    UNCONFIRMED_NO_SNAPSHOT: "この週のカイポケの予定を取れていません",
    UNCONFIRMED_FAILED: "この週のカイポケの予定を取れませんでした",
    UNCONFIRMED_STALE: "この週のカイポケの予定が今回の取り直しより前のものです",
    UNCONFIRMED_NOT_COVERED: "カイポケの画面にこの日が出ませんでした（月をまたぐ週）",
    UNCONFIRMED_ACTUAL: "この月のカイポケの実績を今回取れていません",
    UNCONFIRMED_ACTUAL_STAFF: "カイポケの実績にらく助に見つからない職員がいます",
}


def _label(kind: str | None) -> str:
    return _KIND_LABELS.get(kind or "", kind or "")


# --- 入力と結果の型 -----------------------------------------------------------------


@dataclass(frozen=True)
class SourceRows:
    """段階 0 の ``kaipoke_source_snapshots`` の 1 行 (純粋な関数の入力)。"""

    status: str
    fetched_at: datetime
    rows: list[Any] = field(default_factory=list)
    #: ``tasks`` のときの画面に出た日 (ISO)。None = RPA が返さなかった (週の全部の日とみなす)。
    covered_dates: list[str] | None = None


@dataclass(frozen=True)
class SentRecord:
    """``staff_leave_kaipoke`` の 1 行 (S と照合前)。"""

    sent_type: str | None
    baseline_pending: bool = False


@dataclass(frozen=True)
class LeaveDiff:
    """差分の 1 件 (API と画面がそのまま使う。``as_dict`` が JSON の形)。

    * ``case``: §7-4-3 の全通りの表の行 (1〜15)。付帯の行 (種類がわからない・未確認・
      実績との食い違い・職員が見つからない・同じ日に 2 件) は None。
    * ``rakusuke`` / ``sent`` / ``kaipoke``: R・S・K (``off`` / ``am_off`` / ``pm_off`` /
      ``paid_off`` / None)。
    * ``detail``: 種類ごとの付け足し。``unknown_kind`` は ``subject``・``start``・``end``・
      ``content_hash``、``unconfirmed`` は ``reason``・``week_start``、``staff_not_found`` は
      ``name``・``dates``、``duplicate_leave`` は ``items``、``actual_conflict`` は ``visits``。
    """

    kind: str
    message: str
    case: int | None = None
    staff_id: uuid.UUID | None = None
    staff_name: str = ""
    day: date | None = None
    rakusuke: str | None = None
    sent: str | None = None
    kaipoke: str | None = None
    baseline_pending: bool = False
    action: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "case": self.case,
            "staff_id": str(self.staff_id) if self.staff_id else None,
            "staff_name": self.staff_name,
            "date": self.day.isoformat() if self.day else None,
            "rakusuke": self.rakusuke,
            "sent": self.sent,
            "kaipoke": self.kaipoke,
            "baseline_pending": self.baseline_pending,
            "action": self.action,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PlannedSend:
    """送る予定 (未送信・差分に数えない)。#6 (照合前でない)・#12。"""

    staff_id: uuid.UUID
    staff_name: str
    day: date
    leave_type: str
    case: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "staff_id": str(self.staff_id),
            "staff_name": self.staff_name,
            "date": self.day.isoformat(),
            "leave_type": self.leave_type,
            "case": self.case,
        }


@dataclass(frozen=True)
class SentTypeUpdate:
    """差分を作るときに自動でそろえる ``sent_type`` (#3・#7・#12・#13 と行の片付け)。

    ``delete_row`` なら行を消す。そうでなければ ``sent_type`` を書き、照合前を外す。
    """

    staff_id: uuid.UUID
    day: date
    sent_type: str | None
    delete_row: bool
    case: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "staff_id": str(self.staff_id),
            "date": self.day.isoformat(),
            "sent_type": self.sent_type,
            "delete_row": self.delete_row,
            "case": self.case,
        }


@dataclass
class LeaveCompareResult:
    """``build_leave_diffs`` の結果。"""

    month: date
    #: らく助の休みを読む前に取った時刻 (``leave_months.last_compared_at`` に書く)。
    #: 読んだ後に休みが変わったら「照合の後に変えた」と分かるように、読む前の時刻にする。
    compared_at: datetime | None = None
    diffs: list[LeaveDiff] = field(default_factory=list)
    planned_sends: list[PlannedSend] = field(default_factory=list)
    sent_type_updates: list[SentTypeUpdate] = field(default_factory=list)
    #: 未確認の日 (重なりなし・古い順)。
    unconfirmed_dates: list[date] = field(default_factory=list)
    #: 使った材料の ``fetched_at`` (``leave_compare_runs.fetched_at`` に置く形)。
    fetched_at: dict[str, Any] = field(default_factory=dict)
    #: 実績 CSV の職員名でらく助に結び付かなかったもの → 行の数 (黙って捨てない)。
    actual_unmatched_names: dict[str, int] = field(default_factory=dict)

    @property
    def diff_count(self) -> int:
        return len(self.diffs)

    @property
    def planned_send_count(self) -> int:
        return len(self.planned_sends)

    @property
    def unconfirmed_days(self) -> int:
        return len(self.unconfirmed_dates)


# --- 月と週 -------------------------------------------------------------------------


def month_first_day(month: date | str) -> date:
    """``date`` か ``YYYY-MM`` → 月の 1 日。"""
    if isinstance(month, str):
        year, mon = month.split("-")
        return date(int(year), int(mon), 1)
    return month.replace(day=1)


def _month_last_day(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def month_days(month: date) -> list[date]:
    """その月の照合の対象の日 (日曜を除く)。"""
    first = month_first_day(month)
    last = _month_last_day(first)
    return [
        first + timedelta(days=i)
        for i in range((last - first).days + 1)
        if (first + timedelta(days=i)).weekday() != _SUNDAY
    ]


def month_mondays(month: date) -> list[date]:
    """その月の日 (月〜土) を含む ISO 週の月曜 (古い順)。"""
    return sorted({d - timedelta(days=d.weekday()) for d in month_days(month)})


def compare_source_plan(month: date) -> list[SourceSpec]:
    """照合の材料の計画: その月の週の個別業務 (``tasks``) → その月の実績 CSV。"""
    first = month_first_day(month)
    return [
        *(SourceSpec(kind=KIND_TASKS, period=monday) for monday in month_mondays(first)),
        SourceSpec(kind=KIND_ACTUAL_CSV, period=first),
    ]


def leave_content_hash(
    staff_id: uuid.UUID, day: date, subject: str | None, start: time | None, end: time | None
) -> str:
    """判定の記録のキー = sha256(職員 id ‖ 日付 ‖ 件名 ‖ 開始 HH:MM ‖ 終了 HH:MM) (§7-4-3)。

    区切りは ``\\x1f``・件名は前後の空白を除く・時刻が無ければ空。
    """
    parts = [
        str(staff_id),
        day.isoformat(),
        (subject or "").strip(),
        start.strftime("%H:%M") if start else "",
        end.strftime("%H:%M") if end else "",
    ]
    return hashlib.sha256(_HASH_SEP.join(parts).encode("utf-8")).hexdigest()


# --- 純粋な関数 ---------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_fresh(source: SourceRows | None, job_started_at: datetime) -> bool:
    return (
        source is not None
        and source.status in _USABLE
        and _aware(source.fetched_at) >= _aware(job_started_at)
    )


def _unconfirmed_reason(source: SourceRows | None, job_started_at: datetime) -> str | None:
    if source is None:
        return UNCONFIRMED_NO_SNAPSHOT
    if source.status == STATUS_FAILED or source.status not in _USABLE:
        return UNCONFIRMED_FAILED
    if _aware(source.fetched_at) < _aware(job_started_at):
        return UNCONFIRMED_STALE
    return None


def _parse_hhmm(value: Any) -> time | None:
    text = str(value or "").strip()
    head, sep, tail = text.partition(":")
    if not sep:
        return None
    try:
        hour, minute = int(head), int(tail[:2])
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return time(hour, minute)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _visit_hits_leave(leave_type: str, start_min: int, end_min: int | None) -> bool:
    """訪問 (分) が休みの時間帯にかかるか。終日は常に・午前休は開始が勤務の始まりより前・
    午後休は終了が休みの始まりより後 (境目は ``app/core/leave.py`` の定数)。

    照合では見落とさない向きに倒す: 終了が空 (読めない) の訪問は、午後休なら当たりとする。
    """
    if leave_type in FULL_DAY_OFF:
        return True
    if leave_type == "am_off":
        return start_min < _minutes(AM_OFF_UNTIL)
    if leave_type == "pm_off":
        return end_min is None or end_min > _minutes(PM_OFF_FROM)
    return False


def _hhmm(minutes: int | None) -> str | None:
    return None if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"


def _actual_visits_by_staff(
    rows: list[Any],
    first: date,
    day_set: set[date],
    name_index: dict[str, list[str]],
    staff_ids: Mapping[uuid.UUID, str] | None = None,
) -> tuple[
    dict[tuple[uuid.UUID, date], list[dict[str, Any]]],
    dict[str, dict[date, int]],
]:
    """実績 CSV → ((職員, 日) ごとの訪問, らく助に結び付かない職員名 → {日: 行の数})。

    * 開始だけで数える (終了が空・読めなくても落とさない。``end`` は None)。
    * 行の重複のまとめはしない。職員名１〜３ のそれぞれへ配り、(職員, 日) の中で
      同じ利用者・開始・終了の行だけを 1 件にする (職員名２・３ の食い違いを見落とさない)。
    * 名前がらく助の職員 (``staff_ids`` を渡したときはその中) に結び付かない行は黙って
      捨てず、名前と日ごとの行の数を返す (その日は実績を確かめきれない)。
    """
    out: dict[tuple[uuid.UUID, date], list[dict[str, Any]]] = defaultdict(list)
    unmatched: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    seen: set[tuple[uuid.UUID, date, str, int, int | None]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("業務種別") or "").strip() not in VISIT_BUSINESS_TYPES:
            continue
        try:
            day = first.replace(day=int(str(raw.get("日付") or "").strip()))
        except ValueError:
            continue
        if day not in day_set:
            continue
        start = _parse_hhmm(norm_time(str(raw.get("開始時間") or "")))
        if start is None:
            continue
        end = _parse_hhmm(norm_time(str(raw.get("終了時間") or "")))
        start_min = _minutes(start)
        end_min = _minutes(end) if end is not None else None
        patient = str(raw.get("利用者") or "").strip()
        for column in _CSV_STAFF_COLUMNS:
            name = str(raw.get(column) or "").strip()
            if name in NO_STAFF_TOKENS:
                continue
            hit = match_name(name, name_index)
            staff_id = uuid.UUID(hit) if hit is not None else None
            if staff_id is None or (staff_ids is not None and staff_id not in staff_ids):
                unmatched[name][day] += 1
                continue
            key = (staff_id, day, patient, start_min, end_min)
            if key in seen:
                continue
            seen.add(key)
            out[(staff_id, day)].append({"patient": patient, "start": start_min, "end": end_min})
    return out, {name: dict(per_day) for name, per_day in unmatched.items()}


@dataclass
class _Item:
    """カイポケの個別業務のうち休みの語を含むもの (1 職員 × 1 日)。"""

    subject: str
    start: time | None
    end: time | None
    content_hash: str
    #: 件名＋時刻の判定 (``unknown`` を含む)。
    classified: str
    #: 判定の記録まで当てた種類 (None = 休みではない・``unknown`` = まだ選ばれていない)。
    resolved: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "start": self.start.strftime("%H:%M") if self.start else None,
            "end": self.end.strftime("%H:%M") if self.end else None,
            "content_hash": self.content_hash,
            "classified": self.classified,
            "resolved": self.resolved,
        }


def _row_outcome(
    r: str | None, s: str | None, k: str | None, baseline: bool
) -> tuple[int, str | None, str | None]:
    """R・S・K → (表の行, 差分の種類 or 特別な値, ボタン)。

    特別な値: ``"ok"`` (何もしない)・``"auto_set"`` (S=K・照合前を外す)・
    ``"auto_delete"`` (行を消す)・``"auto_clear_send"`` (S=NULL＝送る予定)・
    ``"send"`` (送る予定)。
    """
    if r is None:
        if s is None:
            return (2, DIFF_KAIPOKE_ONLY, ACTION_ADD_TO_RAKUSUKE) if k else (1, "ok", None)
        if k is None:
            return 3, "auto_delete", None
        if k == s:
            return 4, DIFF_KAIPOKE_DELETE_NEEDED, None
        return 5, DIFF_KAIPOKE_OTHER_AFTER_CANCEL, ACTION_ADD_TO_RAKUSUKE
    if s is None:
        if k is None:
            if baseline:
                return 6, DIFF_RAKUSUKE_ONLY_BASELINE, ACTION_CANCEL_RAKUSUKE
            return 6, "send", None
        if k == r:
            return 7, "auto_set", None
        return 8, DIFF_TYPE_MISMATCH, ACTION_MATCH_KAIPOKE_KIND
    if s == r:
        if k is None:
            return 9, DIFF_DELETED_IN_KAIPOKE, ACTION_CANCEL_RAKUSUKE
        if k == r:
            return 10, "ok", None
        return 11, DIFF_CHANGED_IN_KAIPOKE, ACTION_MATCH_KAIPOKE_KIND
    if k is None:
        return 12, "auto_clear_send", None
    if k == r:
        return 13, "auto_set", None
    if k == s:
        return 14, DIFF_KAIPOKE_RESEND_NEEDED, None
    return 15, DIFF_KAIPOKE_OTHER, ACTION_MATCH_KAIPOKE_KIND


def _row_message(kind: str, r: str | None, s: str | None, k: str | None) -> str:
    messages = {
        DIFF_KAIPOKE_ONLY: f"カイポケだけ休み（{_label(k)}）",
        DIFF_KAIPOKE_DELETE_NEEDED: f"カイポケで要対応：カイポケの{_label(k)}を消してください",
        DIFF_KAIPOKE_OTHER_AFTER_CANCEL: f"カイポケは{_label(k)}（らく助は取り消し済み）",
        DIFF_RAKUSUKE_ONLY_BASELINE: f"らく助だけの休み（以前から・{_label(r)}）",
        DIFF_TYPE_MISMATCH: f"らく助は{_label(r)}・カイポケは{_label(k)}",
        DIFF_DELETED_IN_KAIPOKE: f"カイポケで消されています（らく助は{_label(r)}）",
        DIFF_CHANGED_IN_KAIPOKE: f"送ったあとカイポケで{_label(k)}に変わっています",
        DIFF_KAIPOKE_RESEND_NEEDED: (
            f"カイポケで要対応：カイポケの{_label(s)}を消してください（{_label(r)}は送り直します）"
        ),
        DIFF_KAIPOKE_OTHER: f"カイポケは{_label(k)}（らく助は{_label(r)}）",
    }
    return messages[kind]


def build_leave_diffs(
    *,
    month: date,
    job_started_at: datetime,
    staff_names: Mapping[uuid.UUID, str],
    name_index: dict[str, list[str]],
    rakusuke: Mapping[tuple[uuid.UUID, date], str],
    sent: Mapping[tuple[uuid.UUID, date], SentRecord],
    tasks: Mapping[date, SourceRows],
    actual: SourceRows | None,
    judgements: Mapping[str, str],
    shifts: Mapping[tuple[uuid.UUID, int], tuple[time, time]],
    business_hours: tuple[time, time] = (DEFAULT_BUSINESS_START, DEFAULT_BUSINESS_END),
) -> LeaveCompareResult:
    """材料 → 職員 × 日の差分 (§7-4-3 の全通りの表)・送る予定・自動でそろえる S・未確認の日。

    DB に触らない。入力:

    * ``rakusuke``: (職員, 日) → らく助の休み R (``LEAVE_TYPES`` だけ)。
    * ``sent``: (職員, 日) → ``staff_leave_kaipoke`` の S と照合前。
    * ``tasks``: 週の月曜 → 個別業務の材料。``actual``: その月の実績 CSV の材料。
    * ``judgements``: ``content_hash`` → 管理者が選んだ種類 (``not_leave`` を含む)。
    * ``shifts``: (職員, 曜日 0=月) → 勤務時間 (勤務する曜日だけ)。無い曜日は
      ``business_hours`` で種類を決める。
    * ``staff_names``: 照合の対象の職員 (削除済みを除く)。``name_index`` は
      ``load_staff_name_index`` の索引 (カイポケの名前の結び付け)。

    未確認の日・``covered_dates`` に無い日は表の判定をしない (S も動かさない・送る予定に
    しない)。日曜は対象外。祝日は勤務日なのでほかの日と同じに扱う。
    """
    first = month_first_day(month)
    days = month_days(first)
    day_set = set(days)
    result = LeaveCompareResult(month=first)

    # --- 個別業務: 週ごとの新しさと画面に出た日 ---
    confirmed: set[date] = set()
    unconfirmed: dict[date, tuple[str, date]] = {}
    task_items: dict[tuple[uuid.UUID, date], list[_Item]] = defaultdict(list)
    unmatched: dict[str, set[date]] = defaultdict(set)
    fetched: dict[str, Any] = {"tasks": {}, "actual_csv": None}
    for monday in month_mondays(first):
        source = tasks.get(monday)
        fetched["tasks"][monday.isoformat()] = (
            _aware(source.fetched_at).isoformat() if source is not None else None
        )
        week = [d for d in (monday + timedelta(days=i) for i in range(6)) if d in day_set]
        reason = _unconfirmed_reason(source, job_started_at)
        if reason is not None or source is None:
            for d in week:
                unconfirmed[d] = (reason or UNCONFIRMED_NO_SNAPSHOT, monday)
            continue
        # covered_dates が NULL (RPA が画面に出た日を返さなかった) の週は、どの日も表示を
        # 確かめられないので全部の日を未確認にする (保守的に・空の一覧も同じ)。
        shown = {str(v) for v in (source.covered_dates or [])}
        covered = {d for d in week if d.isoformat() in shown}
        for d in week:
            if d in covered:
                confirmed.add(d)
            else:
                unconfirmed[d] = (UNCONFIRMED_NOT_COVERED, monday)
        for task in source.rows:
            if not isinstance(task, dict):
                continue
            try:
                d = date.fromisoformat(str(task.get("date") or ""))
            except ValueError:
                continue
            if d not in covered:
                continue
            subject = str(task.get("title") or "").strip()
            start, end = _parse_hhmm(task.get("start")), _parse_hhmm(task.get("end"))
            staff_name = str(task.get("staff_name") or "").strip()
            hit = match_name(staff_name, name_index) if staff_name else None
            staff_id = uuid.UUID(hit) if hit is not None else None
            if staff_id is None or staff_id not in staff_names:
                if classify_kaipoke_leave(subject, start, end, business_hours=business_hours):
                    unmatched[staff_name or "(名前なし)"].add(d)
                continue
            shift = shifts.get((staff_id, d.weekday()))
            classified = classify_kaipoke_leave(
                subject,
                start,
                end,
                shift[0] if shift else None,
                shift[1] if shift else None,
                business_hours=business_hours,
            )
            if classified is None:
                continue
            content_hash = leave_content_hash(staff_id, d, subject, start, end)
            resolved: str | None = classified
            if classified == "unknown":
                chosen = judgements.get(content_hash)
                if chosen == JUDGED_NOT_LEAVE:
                    resolved = None
                elif chosen in LEAVE_TYPES:
                    resolved = chosen
            task_items[(staff_id, d)].append(
                _Item(subject, start, end, content_hash, classified, resolved)
            )

    # --- 実績 CSV ---
    actual_fresh = _is_fresh(actual, job_started_at)
    if actual is not None:
        fetched["actual_csv"] = _aware(actual.fetched_at).isoformat()
    actual_visits: dict[tuple[uuid.UUID, date], list[dict[str, Any]]] = {}
    actual_unmatched: dict[str, dict[date, int]] = {}
    if actual_fresh and actual is not None:
        actual_visits, actual_unmatched = _actual_visits_by_staff(
            actual.rows, first, day_set, name_index, staff_names
        )
    result.fetched_at = fetched
    result.actual_unmatched_names = {
        name: sum(per_day.values()) for name, per_day in sorted(actual_unmatched.items())
    }

    # --- 未確認の日 (日ごとに 1 件) ---
    for d in sorted(unconfirmed):
        reason, monday = unconfirmed[d]
        result.diffs.append(
            LeaveDiff(
                kind=DIFF_UNCONFIRMED,
                message=f"未確認：{_UNCONFIRMED_LABELS[reason]}",
                day=d,
                action=ACTION_REFETCH_WEEK,
                detail={"reason": reason, "week_start": monday.isoformat()},
            )
        )

    # --- 実績の職員名がらく助に見つからない日 (個別業務を確かめられた日だけ・日ごとに 1 件) ---
    unconfirmed_actual: set[date] = set()
    names_by_day: dict[date, list[str]] = defaultdict(list)
    for name, per_day in sorted(actual_unmatched.items()):
        for d in per_day:
            names_by_day[d].append(name)
    for d in sorted(names_by_day):
        unconfirmed_actual.add(d)
        if d not in confirmed:
            continue  # 個別業務で未確認の日はもう 1 件出している
        result.diffs.append(
            LeaveDiff(
                kind=DIFF_UNCONFIRMED,
                message=(
                    f"未確認：{_UNCONFIRMED_LABELS[UNCONFIRMED_ACTUAL_STAFF]}"
                    f"（{GUIDE_FIX_STAFF_NAME}）"
                ),
                day=d,
                # ボタンは無い (取り直しでは直らない)。名前をそろえてから比べ直す。
                action=None,
                detail={
                    "reason": UNCONFIRMED_ACTUAL_STAFF,
                    "names": names_by_day[d],
                    "guide": GUIDE_FIX_STAFF_NAME,
                },
            )
        )

    # --- 職員 × 日 ---
    keys = {k for k in (*rakusuke, *sent, *task_items) if k[1] in day_set}
    for staff_id, d in sorted(keys, key=lambda k: (staff_names.get(k[0], ""), str(k[0]), k[1])):
        if staff_id not in staff_names:
            continue
        name = staff_names[staff_id]
        r = rakusuke.get((staff_id, d))
        r = r if r in LEAVE_TYPES else None
        record = sent.get((staff_id, d))
        s = record.sent_type if record is not None else None
        baseline = bool(record.baseline_pending) if record is not None else False
        if d not in confirmed:
            continue

        items = task_items.get((staff_id, d), [])
        base = LeaveDiff(
            kind="",
            message="",
            staff_id=staff_id,
            staff_name=name,
            day=d,
            rakusuke=r,
            sent=s,
            baseline_pending=baseline,
        )
        pending = [i for i in items if i.resolved == "unknown"]
        if pending:
            for item in pending:
                result.diffs.append(
                    replace(
                        base,
                        kind=DIFF_UNKNOWN_KIND,
                        message=f"「{item.subject}」の種類を選んでください",
                        action=ACTION_CHOOSE_KIND,
                        detail=item.as_dict(),
                    )
                )
            continue
        leaves = [i for i in items if i.resolved in LEAVE_TYPES]
        if len(leaves) >= 2:
            result.diffs.append(
                replace(
                    base,
                    kind=DIFF_DUPLICATE_LEAVE,
                    message="カイポケで要対応：同じ日に休みの予定が 2 件あります",
                    detail={"items": [i.as_dict() for i in leaves]},
                )
            )
            continue
        k = leaves[0].resolved if leaves else None

        case, outcome, action = _row_outcome(r, s, k, baseline)
        if outcome == "ok":
            if case == 1 and record is not None:
                # R も S も無い行 (照合前のまま休みが消えたものなど) は片付ける。
                result.sent_type_updates.append(SentTypeUpdate(staff_id, d, None, True, case))
        elif outcome == "auto_set":
            result.sent_type_updates.append(SentTypeUpdate(staff_id, d, k, False, case))
        elif outcome == "auto_delete":
            result.sent_type_updates.append(SentTypeUpdate(staff_id, d, None, True, case))
        elif outcome in ("auto_clear_send", "send") and r is not None:
            if outcome == "auto_clear_send":
                result.sent_type_updates.append(SentTypeUpdate(staff_id, d, None, False, case))
            result.planned_sends.append(PlannedSend(staff_id, name, d, r, case))
        elif outcome is not None:
            if case == 2 and record is not None:
                # R も S も無い行は片付ける (差分「カイポケだけ休み」はそのまま出す)。
                result.sent_type_updates.append(SentTypeUpdate(staff_id, d, None, True, case))
            result.diffs.append(
                replace(
                    base,
                    kind=outcome,
                    message=_row_message(outcome, r, s, k),
                    case=case,
                    kaipoke=k,
                    action=action,
                )
            )

        # 休みの日・半休の時間帯にカイポケの実績の訪問がある (R と K がどちらも休み)。
        if r is not None and k is not None:
            if not actual_fresh:
                unconfirmed_actual.add(d)
                result.diffs.append(
                    replace(
                        base,
                        kind=DIFF_UNCONFIRMED,
                        message=f"未確認：{_UNCONFIRMED_LABELS[UNCONFIRMED_ACTUAL]}",
                        kaipoke=k,
                        action=ACTION_REFETCH_WEEK,
                        detail={"reason": UNCONFIRMED_ACTUAL, "month": first.isoformat()},
                    )
                )
                continue
            hits = [
                v
                for v in actual_visits.get((staff_id, d), [])
                if _visit_hits_leave(k, v["start"], v["end"])
            ]
            if hits:
                result.diffs.append(
                    replace(
                        base,
                        kind=DIFF_ACTUAL_CONFLICT,
                        message="カイポケで要対応：休みと実績が食い違っています",
                        kaipoke=k,
                        detail={
                            "visits": [
                                {
                                    "patient": v["patient"],
                                    "start": _hhmm(v["start"]),
                                    "end": _hhmm(v["end"]),
                                }
                                for v in hits
                            ]
                        },
                    )
                )

    # --- カイポケの実績の職員がらく助に見つからない (行の数を出す) ---
    for staff_name, per_day in sorted(actual_unmatched.items()):
        count = sum(per_day.values())
        result.diffs.append(
            LeaveDiff(
                kind=DIFF_STAFF_NOT_FOUND,
                message=(
                    f"カイポケの実績の職員「{staff_name}」がらく助に見つかりません"
                    f"（{count}件・{GUIDE_FIX_STAFF_NAME}）"
                ),
                action=None,
                detail={
                    "name": staff_name,
                    "dates": [d.isoformat() for d in sorted(per_day)],
                    "count": count,
                    "source": KIND_ACTUAL_CSV,
                    "guide": GUIDE_FIX_STAFF_NAME,
                },
            )
        )

    # --- カイポケの職員がらく助に見つからない (休みの予定だけ) ---
    for staff_name in sorted(unmatched):
        result.diffs.append(
            LeaveDiff(
                kind=DIFF_STAFF_NOT_FOUND,
                message=f"カイポケの職員「{staff_name}」がらく助に見つかりません",
                detail={
                    "name": staff_name,
                    "dates": [d.isoformat() for d in sorted(unmatched[staff_name])],
                },
            )
        )

    result.unconfirmed_dates = sorted(set(unconfirmed) | unconfirmed_actual)
    return result


# --- DB から読む・保存する ------------------------------------------------------------


def _to_source(row: KaipokeSourceSnapshot) -> SourceRows:
    return SourceRows(
        status=row.status,
        fetched_at=row.fetched_at,
        rows=list(row.rows or []),
        covered_dates=list(row.covered_dates) if row.covered_dates is not None else None,
    )


async def build_month_diffs(
    db: AsyncSession, *, month: date, job_started_at: datetime
) -> LeaveCompareResult:
    """DB から材料を読み、``build_leave_diffs`` で差分を作る (書かない)。

    らく助の休みを読む前の時刻を ``compared_at`` に持たせる (読んだ後の変更は
    ``last_leave_changed_at`` がこれより後になり、比べ直しが要ると分かる)。
    """
    compared_at = datetime.now(UTC)
    first = month_first_day(month)
    days = month_days(first)
    mondays = month_mondays(first)
    name_index, staff_map = await load_staff_name_index(db)
    staff_names = {sid: s.name for sid, s in staff_map.items()}

    iso_years = {d.isocalendar().year for d in days}
    rakusuke: dict[tuple[uuid.UUID, date], str] = {}
    day_set = set(days)
    for ov in (
        await db.scalars(
            select(StaffWeeklyOverride).where(
                StaffWeeklyOverride.iso_year.in_(iso_years),
                StaffWeeklyOverride.override_type.in_(sorted(LEAVE_TYPES)),
            )
        )
    ).all():
        try:
            d = date.fromisocalendar(ov.iso_year, ov.iso_week, ov.weekday + 1)
        except ValueError:
            continue
        if d in day_set:
            rakusuke[(ov.staff_id, d)] = ov.override_type

    sent = {
        (row.staff_id, row.leave_date): SentRecord(row.sent_type, bool(row.baseline_pending))
        for row in (
            await db.scalars(
                select(StaffLeaveKaipoke).where(
                    StaffLeaveKaipoke.leave_date >= first,
                    StaffLeaveKaipoke.leave_date <= _month_last_day(first),
                )
            )
        ).all()
    }

    snapshots = (
        await db.scalars(
            select(KaipokeSourceSnapshot).where(
                (
                    (KaipokeSourceSnapshot.kind == KIND_TASKS)
                    & KaipokeSourceSnapshot.period_start.in_(mondays)
                )
                | (
                    (KaipokeSourceSnapshot.kind == KIND_ACTUAL_CSV)
                    & (KaipokeSourceSnapshot.period_start == first)
                )
            )
        )
    ).all()
    tasks = {s.period_start: _to_source(s) for s in snapshots if s.kind == KIND_TASKS}
    actual = next((_to_source(s) for s in snapshots if s.kind == KIND_ACTUAL_CSV), None)

    judgements = {
        row.content_hash: row.chosen_kind
        for row in (
            await db.scalars(
                select(KaipokeLeaveJudgement).where(
                    KaipokeLeaveJudgement.event_date >= first,
                    KaipokeLeaveJudgement.event_date <= _month_last_day(first),
                )
            )
        ).all()
    }

    config = await load_scheduling_config(db)
    biz = (config.business_start, config.business_end)
    shifts = {
        (row.staff_id, row.weekday): (row.start_time or biz[0], row.end_time or biz[1])
        for row in (await db.scalars(select(StaffShift))).all()
        if row.is_on
    }
    result = build_leave_diffs(
        month=first,
        job_started_at=job_started_at,
        staff_names=staff_names,
        name_index=name_index,
        rakusuke=rakusuke,
        sent=sent,
        tasks=tasks,
        actual=actual,
        judgements=judgements,
        shifts=shifts,
        business_hours=biz,
    )
    result.compared_at = compared_at
    return result


async def save_compare_run(
    db: AsyncSession,
    *,
    result: LeaveCompareResult,
    job_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> LeaveCompareRun:
    """差分の結果を ``leave_compare_runs`` に置き、自動の行 (#3・#7・#12・#13) の S をそろえ、
    ``leave_months`` の照合の記録を更新する。1 つのトランザクションで commit する。"""
    for change in result.sent_type_updates:
        if change.delete_row:
            await db.execute(
                delete(StaffLeaveKaipoke).where(
                    StaffLeaveKaipoke.staff_id == change.staff_id,
                    StaffLeaveKaipoke.leave_date == change.day,
                )
            )
            continue
        row = await db.scalar(
            select(StaffLeaveKaipoke).where(
                StaffLeaveKaipoke.staff_id == change.staff_id,
                StaffLeaveKaipoke.leave_date == change.day,
            )
        )
        if row is None:
            row = StaffLeaveKaipoke(staff_id=change.staff_id, leave_date=change.day)
            db.add(row)
        row.sent_type = change.sent_type
        row.baseline_pending = False

    run = LeaveCompareRun(
        month=result.month,
        kaipoke_job_id=job_id,
        fetched_at=result.fetched_at,
        # 自動でそろえた sent_type (#3・#7・#12・#13・行の片付け)。
        auto_updates=[u.as_dict() for u in result.sent_type_updates],
        diffs=[diff.as_dict() for diff in result.diffs],
        diff_count=result.diff_count,
        planned_send_count=result.planned_send_count,
        unconfirmed_days=result.unconfirmed_days,
        created_by=user_id,
    )
    db.add(run)
    await db.flush()

    leave_month = await db.get(LeaveMonth, result.month)
    if leave_month is None:
        leave_month = LeaveMonth(month=result.month)
        db.add(leave_month)
    leave_month.last_compared_at = result.compared_at or datetime.now(UTC)
    leave_month.last_compare_run_id = run.id
    await db.commit()
    return run


async def compare_month(db: AsyncSession, job_id: uuid.UUID) -> LeaveCompareRun | None:
    """ジョブ (``completed``) の材料から差分を作って保存する。決着していなければ None。

    差分づくり・保存が失敗したら rollback し、ログとジョブの
    ``result_summary.compare_error`` に残して None を返す (ジョブは ``completed`` のまま)。
    結果を返す側 (API) は ``leave_compare_runs.kaipoke_job_id == job.id`` の行で
    このジョブの照合の結果を突き合わせる (行が無く ``compare_error`` があれば失敗)。
    """
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or job.status != "completed" or job.started_at is None:
        return None
    params = job.params or {}
    if params.get("op") != OP_LEAVE_COMPARE or not params.get("month"):
        return None
    started_at = job.started_at
    user_id = job.created_by_user_id
    try:
        result = await build_month_diffs(
            db, month=month_first_day(str(params["month"])), job_started_at=started_at
        )
        return await save_compare_run(db, result=result, job_id=job_id, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 — 黙って失わない (ジョブは completed で残る)
        logger.exception("leave compare failed for job %s", job_id)
        await db.rollback()
        await record_compare_error(job_id, f"照合の差分を作れませんでした: {str(exc)[:300]}")
        return None


async def record_compare_error(job_id: uuid.UUID, message: str) -> None:
    """ジョブの ``result_summary.compare_error`` を **新しいセッションで** 書く.

    比べる処理の例外で元のセッションが使えなくなっていても必ず書く (画面が「比べています」
    のまま止まらないように)。書けなかったときはログだけ残す。
    """
    try:
        factory = get_session_factory()
        async with factory() as fresh:
            summary = dict(
                (
                    await fresh.scalar(
                        select(KaipokeJob.result_summary).where(KaipokeJob.id == job_id)
                    )
                )
                or {}
            )
            summary["compare_error"] = message
            await fresh.execute(
                update(KaipokeJob)
                .where(KaipokeJob.id == job_id)
                .values(result_summary=summary, updated_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
            await fresh.commit()
    except Exception:  # noqa: BLE001 — ここで落とすと元の失敗が見えなくなる
        logger.exception("could not record compare_error for job %s", job_id)


async def start_leave_compare(
    db: AsyncSession, *, month: date | str, user_id: uuid.UUID | None
) -> tuple[KaipokeJob, list[SourceSpec]]:
    """照合の材料の取り直しのジョブを ``running`` で立てる (他のジョブが動いていれば 409)。

    **新しいトランザクションで呼ぶこと** (段階 0 の ``start_source_job`` と同じ)。
    """
    first = month_first_day(month)
    plan = compare_source_plan(first)
    job = await sources.start_source_job(
        db,
        op=OP_LEAVE_COMPARE,
        week_start=month_mondays(first)[0],
        user_id=user_id,
        params={
            "month": f"{first.year:04d}-{first.month:02d}",
            "targets": [
                {"kind": spec.kind, "period_start": spec.period.isoformat()} for spec in plan
            ],
        },
        total=len(plan),
    )
    return job, plan


async def run_leave_compare_job(
    *, job_id: uuid.UUID, plan: list[SourceSpec], client: KaipokeClient
) -> LeaveCompareRun | None:
    """応答の後に自前のセッションで材料を取り直し、``completed`` なら差分を作って保存する。

    取り直し (1 本ずつ・進み具合・必ずジョブを閉じる) は段階 0 の ``run_source_job``。
    """
    factory = get_session_factory()
    async with factory() as db:
        job = await sources.run_source_job(db, client, job_id, plan)
        if job is None or job.status != "completed":
            return None
        try:
            run = await compare_month(db, job_id)
        except BaseException as exc:
            # compare_month の外側で落ちた (取り消し・セッションの破損など): completed のまま
            # 結果も compare_error も無いと画面が「比べています」で止まるので必ず書く。
            await record_compare_error(job_id, f"照合の差分を作れませんでした: {str(exc)[:300]}")
            raise
        if run is None:
            # 材料は取れたのに結果が無い (ジョブの記録が足りないなど): 理由が無ければ書く。
            fresh = await db.get(KaipokeJob, job_id, populate_existing=True)
            if fresh is not None and not (fresh.result_summary or {}).get("compare_error"):
                await record_compare_error(job_id, "照合の差分を作れませんでした")
        return run


# --- 「合わせる」ボタン (§7-4-3・H-c) -------------------------------------------------

#: 「合わせる」で受ける action (``refetch_week`` は「カイポケと比べる」をもう一度押す)。
APPLY_ACTIONS: Final = frozenset(
    {ACTION_ADD_TO_RAKUSUKE, ACTION_MATCH_KAIPOKE_KIND, ACTION_CANCEL_RAKUSUKE, ACTION_CHOOSE_KIND}
)
#: 判定の記録で選べる種類。
JUDGEMENT_KINDS: Final = frozenset({JUDGED_NOT_LEAVE, *LEAVE_TYPES})
#: 照合の「合わせる」の通知の頭 (まとめて押したとき・§3-3 の 7)。
APPLY_NOTIFY_PREFIX: Final = "カイポケに合わせて"
_ORIGIN: Final = "kaipoke_compare"
#: この月のジョブが動いている間は「合わせる」を止める op。
_MONTH_JOB_OPS: Final = (OP_LEAVE_COMPARE, OP_LEAVE_OUTBOUND)
MSG_APPLY_JOB_RUNNING: Final = (
    "この月のカイポケとの照合・休みの送信が動いています。終わってから合わせてください"
)
MSG_APPLY_CONFLICT: Final = "ほかの方が同時に合わせました。画面を読み直してください"


@dataclass(frozen=True)
class CompareAction:
    """「合わせる」の 1 件 (差分を職員・日・action で指す)。

    ``choose_kind`` は ``content_hash`` (差分の ``detail.content_hash``) と ``chosen_kind``
    (``not_leave`` / ``off`` / ``am_off`` / ``pm_off`` / ``paid_off``) が要る。
    """

    staff_id: uuid.UUID
    day: date
    action: str
    content_hash: str | None = None
    chosen_kind: str | None = None


@dataclass
class CompareApplyResult:
    """``apply_compare_actions`` の結果。"""

    #: 合わせた差分 (職員・日・action・前と後の R と S・移した訪問の数)。
    applied: list[dict[str, Any]] = field(default_factory=list)
    #: 判定の記録をした・休みを変えた → もう一度「カイポケと比べる」が要る。
    recompare_needed: bool = False
    #: 担当なしへ移した訪問の数 (今日以降の日だけ)。
    moved_visit_count: int = 0
    #: 管理者への通知の数 (まとめて 1 通 × 管理者の人数)。
    notified: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "recompare_needed": self.recompare_needed,
            "moved_visit_count": self.moved_visit_count,
            "notified": self.notified,
        }


def _find_diff(diffs: list[Any], item: CompareAction) -> int | None:
    """最新の照合の差分のうち ``item`` が指すもの (まだ合わせていないもの) の位置。"""
    for index, raw in enumerate(diffs):
        if not isinstance(raw, dict) or raw.get("resolved"):
            continue
        if (
            raw.get("staff_id") != str(item.staff_id)
            or raw.get("date") != item.day.isoformat()
            or raw.get("action") != item.action
        ):
            continue
        if item.action == ACTION_CHOOSE_KIND:
            if (raw.get("detail") or {}).get("content_hash") != item.content_hash:
                continue
        return index
    return None


async def _sent_row(db: AsyncSession, staff_id: uuid.UUID, day: date) -> StaffLeaveKaipoke | None:
    return await db.scalar(
        select(StaffLeaveKaipoke).where(
            StaffLeaveKaipoke.staff_id == staff_id, StaffLeaveKaipoke.leave_date == day
        )
    )


async def _ensure_no_month_job(db: AsyncSession, month: date) -> None:
    """その月の照合 (取り直し)・送信のジョブが動いていれば 409 (結果と食い違わないように)。"""
    label = f"{month.year:04d}-{month.month:02d}"
    for job in await sources.active_rpa_jobs(db):
        params = job.params or {}
        if params.get("op") in _MONTH_JOB_OPS and params.get("month") == label:
            raise StaffLeaveError(409, MSG_APPLY_JOB_RUNNING)


def _validate_actions(items: list[CompareAction]) -> None:
    if not items:
        raise StaffLeaveError(422, "合わせる差分を選んでください")
    seen: set[tuple[uuid.UUID, date]] = set()
    for item in items:
        if item.action not in APPLY_ACTIONS:
            raise StaffLeaveError(422, f"合わせ方が不正です: {item.action}")
        if item.action == ACTION_CHOOSE_KIND:
            if not item.content_hash or item.chosen_kind not in JUDGEMENT_KINDS:
                raise StaffLeaveError(422, "種類を選んでください")
            continue
        key = (item.staff_id, item.day)
        if key in seen:
            raise StaffLeaveError(422, "同じ職員・同じ日の差分が 2 回選ばれています")
        seen.add(key)


async def _record_judgement(
    db: AsyncSession, item: CompareAction, diff: dict[str, Any], actor: User | None
) -> None:
    """種類を選んだ記録 (``content_hash`` ごとに 1 行・選び直したら書き換える)。"""
    detail = diff.get("detail") or {}
    row = await db.scalar(
        select(KaipokeLeaveJudgement).where(KaipokeLeaveJudgement.content_hash == item.content_hash)
    )
    if row is None:
        row = KaipokeLeaveJudgement(
            staff_id=item.staff_id,
            event_date=item.day,
            content_hash=str(item.content_hash),
            subject=str(detail.get("subject") or ""),
            starts=_parse_hhmm(detail.get("start")),
            ends=_parse_hhmm(detail.get("end")),
        )
        db.add(row)
    row.chosen_kind = str(item.chosen_kind)
    row.decided_by = actor.id if actor is not None else None
    row.decided_at = datetime.now(UTC)


async def apply_compare_actions(
    db: AsyncSession, *, items: list[CompareAction], actor: User | None
) -> CompareApplyResult:
    """照合の差分を「合わせる」(1 件でも、同じ職員・同じ月のまとめてでも)。

    すべてを **1 つのトランザクション** で行い、最後に commit する (途中で失敗したら
    rollback して何も残さない)。差分は各月の最新の照合 (``leave_months.last_compare_run_id``)
    から探し、照合のときの R・S が今と違えば 409 (比べ直す)。

    * ``add_to_rakusuke`` (#2・#5)・``match_kaipoke_kind`` (#8・#11・#15):
      ``apply_staff_leave(source='kaipoke', notify=False)`` で R = K、同じトランザクションで
      ``sent_type`` = K・照合前を外す。過去の日は休みの記録だけ (``apply_staff_leave`` が守る)。
    * ``cancel_rakusuke`` (#6 照合前・#9): ``cancel_staff_leave(notify=False)`` で R = 無、
      ``staff_leave_kaipoke`` の行を消す (S = NULL)。
    * ``choose_kind`` (種類がわからない): ``kaipoke_leave_judgements`` に記録する。
      R・S は動かさない (比べ直すと K が決まる)。

    合わせた差分は照合の結果 (``leave_compare_runs.diffs``) に ``resolved`` (誰が・いつ・
    どう合わせたか) を書く。担当なしへ移した訪問があれば、管理者への通知を 1 回だけ出す
    (「カイポケに合わせて…」・本人へは送らない)。

    Raises:
        StaffLeaveError: 422 (入力)・409 (照合が無い・差分が見つからない・照合の後に変わった)
            ・404 (職員が見つからない)。
    """
    _validate_actions(items)
    result = CompareApplyResult()
    leave_results: list[LeaveApplyResult] = []
    runs: dict[date, LeaveCompareRun] = {}
    #: 照合の結果に書き戻す印 (run → 差分の位置 → resolved)。
    marks: dict[uuid.UUID, dict[int, dict[str, Any]]] = defaultdict(dict)
    #: 運転席の「戻る」は ユーザー×ISO 週 で戻すので、op_group は週ごとに分ける。
    groups: dict[tuple[int, int], uuid.UUID] = {}
    now = datetime.now(UTC)
    try:
        # 月をまたいでまとめて合わせるときも、月を古い順に並べてから鍵を取る (deadlock を防ぐ)。
        for first in sorted({month_first_day(item.day) for item in items}):
            await _ensure_no_month_job(db, first)
            # 同時に押されたら 1 本ずつ (月の行の鍵・PG だけ効く)。鍵を取ってから最新の
            # 照合を読み直すので、先に合わせた側の resolved が見える。
            leave_month = await db.scalar(
                select(LeaveMonth)
                .where(LeaveMonth.month == first)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            run_id = leave_month.last_compare_run_id if leave_month is not None else None
            locked = (
                await db.get(LeaveCompareRun, run_id, populate_existing=True)
                if run_id is not None
                else None
            )
            if locked is None:
                raise StaffLeaveError(409, "この月はまだカイポケと比べていません")
            runs[first] = locked

        for item in items:
            run = runs[month_first_day(item.day)]
            diffs = list(run.diffs or [])
            index = _find_diff(diffs, item)
            if index is None or index in marks[run.id]:
                raise StaffLeaveError(
                    409, "この差分は最新の照合にありません。カイポケと比べ直してください"
                )
            diff = diffs[index]
            mark: dict[str, Any] = {
                "action": item.action,
                "by": str(actor.id) if actor is not None else None,
                "at": now.isoformat(),
            }
            if item.action == ACTION_CHOOSE_KIND:
                await _record_judgement(db, item, diff, actor)
                mark["chosen_kind"] = item.chosen_kind
                marks[run.id][index] = mark
                result.recompare_needed = True
                result.applied.append(
                    {
                        "staff_id": str(item.staff_id),
                        "date": item.day.isoformat(),
                        "action": item.action,
                        "chosen_kind": item.chosen_kind,
                    }
                )
                continue

            r_now = await leave_type_on(db, item.staff_id, item.day)
            row = await _sent_row(db, item.staff_id, item.day)
            s_now = row.sent_type if row is not None else None
            if r_now != diff.get("rakusuke") or s_now != diff.get("sent"):
                raise StaffLeaveError(
                    409, "比べた後に休みが変わっています。カイポケと比べ直してください"
                )
            y, w, _wd = iso_key(item.day)
            group = groups.setdefault((y, w), uuid.uuid4())
            moved = 0
            after: str | None = None
            if item.action == ACTION_CANCEL_RAKUSUKE:
                if r_now is not None:
                    await cancel_staff_leave(
                        db,
                        staff_id=item.staff_id,
                        day=item.day,
                        actor=actor,
                        op_group_id=group,
                        origin=_ORIGIN,
                        notify=False,
                    )
                if row is not None:
                    await db.delete(row)
            else:
                kind = diff.get("kaipoke")
                if kind not in LEAVE_TYPES:
                    raise StaffLeaveError(409, "カイポケの休みの種類が分かりません")
                applied = await apply_staff_leave(
                    db,
                    staff_id=item.staff_id,
                    day=item.day,
                    leave_type=str(kind),
                    source="kaipoke",
                    note=None,
                    actor=actor,
                    op_group_id=group,
                    origin=_ORIGIN,
                    notify=False,
                )
                leave_results.append(applied)
                moved = len(applied.moved_visit_ids)
                if row is None:
                    row = StaffLeaveKaipoke(staff_id=item.staff_id, leave_date=item.day)
                    db.add(row)
                row.sent_type = str(kind)
                row.baseline_pending = False
                after = str(kind)
            await db.flush()
            result.moved_visit_count += moved
            result.recompare_needed = True
            marks[run.id][index] = mark
            result.applied.append(
                {
                    "staff_id": str(item.staff_id),
                    "date": item.day.isoformat(),
                    "action": item.action,
                    "case": diff.get("case"),
                    "rakusuke_before": r_now,
                    "sent_before": s_now,
                    "rakusuke_after": after,
                    "sent_after": after,
                    "moved_visit_count": moved,
                }
            )

        for run in runs.values():
            run_marks = marks.get(run.id) or {}
            if run_marks:
                run.diffs = [
                    {**raw, "resolved": run_marks[i]} if i in run_marks else raw
                    for i, raw in enumerate(run.diffs or [])
                ]
        result.notified = await notify_leave_results(
            db, leave_results, summary_prefix=APPLY_NOTIFY_PREFIX
        )
        await db.commit()
    except IntegrityError as exc:
        # 同時に押された別の「合わせる」と行がぶつかった (UNIQUE)。
        await db.rollback()
        raise StaffLeaveError(409, MSG_APPLY_CONFLICT) from exc
    except BaseException:
        await db.rollback()
        raise
    return result


__all__ = [
    "ACTION_ADD_TO_RAKUSUKE",
    "ACTION_CANCEL_RAKUSUKE",
    "ACTION_CHOOSE_KIND",
    "ACTION_MATCH_KAIPOKE_KIND",
    "ACTION_REFETCH_WEEK",
    "APPLY_ACTIONS",
    "APPLY_NOTIFY_PREFIX",
    "GUIDE_FIX_STAFF_NAME",
    "JUDGEMENT_KINDS",
    "MSG_APPLY_CONFLICT",
    "MSG_APPLY_JOB_RUNNING",
    "DIFF_ACTUAL_CONFLICT",
    "DIFF_CHANGED_IN_KAIPOKE",
    "DIFF_DELETED_IN_KAIPOKE",
    "DIFF_DUPLICATE_LEAVE",
    "DIFF_KAIPOKE_DELETE_NEEDED",
    "DIFF_KAIPOKE_ONLY",
    "DIFF_KAIPOKE_OTHER",
    "DIFF_KAIPOKE_OTHER_AFTER_CANCEL",
    "DIFF_KAIPOKE_RESEND_NEEDED",
    "DIFF_RAKUSUKE_ONLY_BASELINE",
    "DIFF_STAFF_NOT_FOUND",
    "DIFF_TYPE_MISMATCH",
    "DIFF_UNCONFIRMED",
    "DIFF_UNKNOWN_KIND",
    "JUDGED_NOT_LEAVE",
    "UNCONFIRMED_ACTUAL",
    "UNCONFIRMED_ACTUAL_STAFF",
    "UNCONFIRMED_FAILED",
    "UNCONFIRMED_NOT_COVERED",
    "UNCONFIRMED_NO_SNAPSHOT",
    "UNCONFIRMED_STALE",
    "CompareAction",
    "CompareApplyResult",
    "LeaveCompareResult",
    "LeaveDiff",
    "PlannedSend",
    "SentRecord",
    "SentTypeUpdate",
    "SourceRows",
    "apply_compare_actions",
    "build_leave_diffs",
    "build_month_diffs",
    "compare_month",
    "compare_source_plan",
    "leave_content_hash",
    "month_days",
    "month_first_day",
    "month_mondays",
    "run_leave_compare_job",
    "save_compare_run",
    "start_leave_compare",
]
