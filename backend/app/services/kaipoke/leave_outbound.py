"""休みをらく助からカイポケへ送る (段階 L #10・§7-4-4).

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §7-4-4・§2-1・§2-5。

* 送るのは状態が「未送信」の休みだけ (R = らく助の休み・S = ``sent_type``):
  R がありで S が NULL・照合前 (``baseline_pending``) でない。さらに、その月の照合が一度
  済んでいる (``leave_months.last_compared_at`` がある) ときだけ。
* 送ったあとの取り消し (R 無・S あり)・種類の変更 (R ≠ S) は、RPA が個別業務の **作成しか
  できない** ので送らず「カイポケで要対応」として返す。
* 件名と時刻は §2-1 の表 (公休「休み」・午前休「AM休み」・午後休「PM休み」・有給「有給」・
  時刻は ``kaipoke_send_window``。型で勤務しない曜日は営業時間)。日曜とカイポケの職員 ID が
  分からない職員は送れない (週の個別業務の送信 ``events_outbound`` と同じ理由)。
* 週の個別業務の送信 (``events_outbound``・朝会などの予定) とは別の経路。ジョブは段階 0 の
  ``KaipokeJob`` (``op='leave-outbound'``・``job_type='push'``・``start_source_job`` で 409)。
* 背景で週ごとに順番に RPA の個別業務の作成 (``/api/individual-tasks-apply``・
  ``external_ref = leave:{staff_leave_kaipoke.id}``) を呼び、送れた分 (``added`` /
  ``skipped_duplicate``) は同じトランザクションで ``sent_type``・``sent_at``・``sent_by``・
  ``kaipoke_external_id`` を書き、``staff_events`` に ``source='kaipoke'``・
  ``external_id`` = 返ってきたキーの行を作る (同じキーの行があれば作らない)。これで次の
  イベント取込・照合で二重にならず、``events_outbound`` の送信対象 (``manual``/``fixed``)
  にも入らない (昇格は ``events_outbound.promote_sent_events`` の仕事で、ここでは呼ばない)。
* 失敗した件は未送信のまま (結果に理由)。カイポケへの送信は日中に (17〜18 時台は注意)。

#13 (月の確定) からは ``start_leave_outbound`` → ``BackgroundTasks`` で
``run_leave_outbound_job`` の順に呼ぶ (確定の API の中で同期で送らない)。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.leave import LEAVE_TYPES, kaipoke_send_window
from app.db.session import get_session_factory
from app.models.kaipoke_job import KaipokeJob
from app.models.leave import LeaveCompareRun, LeaveMonth, StaffLeaveKaipoke
from app.models.staff import Staff, StaffEvent, StaffShift
from app.services.kaipoke import sources
from app.services.kaipoke.credentials import get_credentials_payload
from app.services.kaipoke.events_inbound import EVENT_TYPE_IMPORTED, RE_EXTERNAL_ID
from app.services.kaipoke.events_outbound import EVENT_SOURCE_KAIPOKE, build_staff_internal_map
from app.services.kaipoke.sources import (
    OP_LEAVE_OUTBOUND,
    STATUS_FAILED,
    STATUS_OK,
    RpaJobConflictError,
    RpaUnavailableError,
    SourceFetchResult,
    SourceJobCancelledError,
)
from app.services.kaipoke_client import KaipokeApiError
from app.services.leave_calendar import leave_type_on, load_overrides_between
from app.services.leave_compare import (
    DIFF_STAFF_NOT_FOUND,
    DIFF_UNCONFIRMED,
    diff_needs_recompare,
)
from app.services.scheduling.config import load_scheduling_config

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.kaipoke_client import KaipokeClient

logger = logging.getLogger(__name__)

#: 休みの種類 → カイポケの個別業務の件名 (§2-1)。
LEAVE_SUBJECTS: Final = {
    "off": "休み",
    "am_off": "AM休み",
    "pm_off": "PM休み",
    "paid_off": "有給",
}
_KIND_LABELS: Final = {"off": "公休", "am_off": "午前休", "pm_off": "午後休", "paid_off": "有給"}

#: 進み具合の 1 本 (1 週) の種類 (``result_summary.results[].kind``)。
KIND_LEAVE_WEEK: Final = "leave_week"
#: 送った 1 件の結果 (``result_summary.items[].outcome``)。
OUTCOME_SENT: Final = "sent"
OUTCOME_FAILED: Final = "failed"
OUTCOME_SKIPPED: Final = "skipped_changed"
#: RPA が「送れた」とする結果 (作った・同じ予定が既にあった)。
_RPA_OK: Final = ("added", "skipped_duplicate")

#: RPA の書き込みの結果を読みに行く間隔 (秒)。待つのはジョブ全体の締め切りまで。
APPLY_POLL_SECONDS: float = 5.0
#: RPA の書き込みを始めるときの待ち時間 (秒・async で即応答する)。
_START_TIMEOUT: Final = 30.0
_SUNDAY: Final = 6
_ERROR_MAX_LEN: Final = 300
_RUNNING: Final = "running"
_EVENT_NOTE: Final = "らく助から送った休み"

MSG_NOT_COMPARED: Final = "まだカイポケと比べていないので、休みは送りません"
MSG_NOTHING_TO_SEND: Final = "送る休みがありません"
MSG_SUNDAY: Final = "日曜日はカイポケ取込対象外のため送信しません"
MSG_NO_INTERNAL_ID: Final = "カイポケの職員IDが未対応です（この職員の取込実績がありません）"
MSG_CHANGED: Final = "送る前に休みが変わったため送りませんでした"
MSG_NO_RESULT: Final = "カイポケから結果が返りませんでした（カイポケで確かめてください）"
MSG_BAD_KEY: Final = "カイポケから予定の番号が返りませんでした（カイポケで確かめてください）"
MSG_DIFF_PENDING: Final = "カイポケとの差分が残っています。先に合わせてください"
MSG_RECOMPARE_AFTER_CHOOSE: Final = "種類を選んだので比べ直してください"
MSG_UNKNOWN_STAFF_DAY: Final = (
    "この日はカイポケの予定にらく助に見つからない職員がいます。"
    "らく助の職員の名前をそろえて比べ直してください"
)
MSG_UNCONFIRMED: Final = "この日はカイポケと確かめられていません。取り直してから比べ直してください"
MSG_DEADLINE: Final = (
    "送信の締め切り（ジョブの開始から {minutes} 分）を過ぎたため送りませんでした"
    "（カイポケで確かめてから送り直してください）"
)

#: ジョブ全体の締め切り (分)。段階 0 の自動クローズ (120 分) より手前で必ず終える。
JOB_DEADLINE_MINUTES: float = 110


def _label(kind: str | None) -> str:
    return _KIND_LABELS.get(kind or "", kind or "")


def _hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _short(message: object) -> str:
    return str(message)[:_ERROR_MAX_LEN]


def _month_first(month: date | str) -> date:
    if isinstance(month, str):
        year, mon = month.split("-")
        return date(int(year), int(mon), 1)
    return month.replace(day=1)


def _month_last(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


# --- 送る予定 ---------------------------------------------------------------------


@dataclass
class LeaveSendItem:
    """送る予定の 1 件 (未送信の休み)。送れないときは ``sendable=False`` と理由。"""

    staff_id: uuid.UUID
    staff_name: str
    day: date
    leave_type: str
    title: str
    start: str
    end: str
    sendable: bool
    reason: str | None = None
    staff_internal_id: str | None = None
    #: ``staff_leave_kaipoke.id`` (無ければ送り始めるときに作る)。
    record_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "staff_id": str(self.staff_id),
            "staff_name": self.staff_name,
            "date": self.day.isoformat(),
            "leave_type": self.leave_type,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "sendable": self.sendable,
            "reason": self.reason,
            "record_id": str(self.record_id) if self.record_id else None,
        }


@dataclass(frozen=True)
class LeaveNeedsAction:
    """送ったあとに変わった休み (カイポケで要対応・RPA は作成しかできないので送らない)。"""

    staff_id: uuid.UUID
    staff_name: str
    day: date
    rakusuke: str | None
    sent: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "staff_id": str(self.staff_id),
            "staff_name": self.staff_name,
            "date": self.day.isoformat(),
            "rakusuke": self.rakusuke,
            "sent": self.sent,
            "message": self.message,
        }


@dataclass
class LeaveSendPlan:
    """その月の送る予定 (``build_leave_send_plan`` の結果)。"""

    month: date
    #: その月の照合 (``leave_months.last_compared_at``)。None = まだ比べていない。
    last_compared_at: datetime | None = None
    #: 送れない理由 (照合がまだ)。None なら送ってよい。
    blocked_reason: str | None = None
    items: list[LeaveSendItem] = field(default_factory=list)
    needs_action: list[LeaveNeedsAction] = field(default_factory=list)
    #: 照合前で送らない休みの数。
    baseline_count: int = 0
    #: 送信済み (S = R) の休みの数。
    sent_count: int = 0

    @property
    def sendable_items(self) -> list[LeaveSendItem]:
        if self.blocked_reason is not None:
            return []
        return [i for i in self.items if i.sendable and i.staff_internal_id]

    def as_dict(self) -> dict[str, Any]:
        return {
            "month": f"{self.month.year:04d}-{self.month.month:02d}",
            "last_compared_at": (
                self.last_compared_at.isoformat() if self.last_compared_at else None
            ),
            "blocked_reason": self.blocked_reason,
            "items": [i.as_dict() for i in self.items],
            "sendable_count": len(self.sendable_items),
            "needs_action": [n.as_dict() for n in self.needs_action],
            "baseline_count": self.baseline_count,
            "sent_count": self.sent_count,
        }


async def build_leave_send_plan(
    db: AsyncSession, month: date | str, *, staff_ids: Iterable[uuid.UUID] | None = None
) -> LeaveSendPlan:
    """その月の休みの送信の状態から、送る予定・要対応・照合前・送信済みを作る (書かない)。

    ``staff_ids`` を渡すとその職員の分だけ (一部の職員だけを確定したとき・ディレクター決定)。

    状態は R・S・照合前からその場で決める (§2-5 の表):
    R あり・S NULL・照合前 → 照合前 (送らない)／R あり・S NULL → 未送信 (送る予定)／
    S = R → 送信済み／R あり・S ≠ R → 要対応 (Y を消す・X は送り直す)／
    R 無・S あり → 要対応 (カイポケの X を手で消す)。
    """
    first = _month_first(month)
    last = _month_last(first)
    leave_month = await db.get(LeaveMonth, first)
    compared_at = leave_month.last_compared_at if leave_month is not None else None
    plan = LeaveSendPlan(
        month=first,
        last_compared_at=compared_at,
        blocked_reason=None if compared_at is not None else MSG_NOT_COMPARED,
    )
    # 最新の照合でまだ合わせていない差分 (種類がわからない・同じ日に 2 件を含む) がある
    # (職員, 日) と、未確認の日は送らない (カイポケが正・確かめてから送る)。
    run_id = leave_month.last_compare_run_id if leave_month is not None else None
    run = await db.get(LeaveCompareRun, run_id) if run_id is not None else None
    pending: set[tuple[uuid.UUID, date]] = set()
    #: 種類を選んだ (判定の記録をした) が、まだ比べ直していない (職員, 日)。
    rechoose: set[tuple[uuid.UUID, date]] = set()
    unconfirmed_days: set[date] = set()
    #: カイポケの予定の職員がらく助に見つからない日 (誰の休みか確かめられない・全員止める)。
    unknown_staff_days: set[date] = set()
    for raw in (run.diffs if run is not None else None) or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("kind") == DIFF_STAFF_NOT_FOUND:
            for value in (raw.get("detail") or {}).get("dates") or []:
                try:
                    unknown_staff_days.add(date.fromisoformat(str(value)))
                except ValueError:
                    continue
            continue
        if not raw.get("date"):
            continue
        try:
            diff_day = date.fromisoformat(str(raw["date"]))
        except ValueError:
            continue
        resolved = raw.get("resolved")
        if raw.get("staff_id"):
            key = (uuid.UUID(str(raw["staff_id"])), diff_day)
            if not resolved:
                pending.add(key)
            elif diff_needs_recompare(raw):
                rechoose.add(key)
        elif raw.get("kind") == DIFF_UNCONFIRMED and not resolved:
            unconfirmed_days.add(diff_day)

    staff_names = {
        s.id: s.name
        for s in (await db.scalars(select(Staff).where(Staff.deleted_at.is_(None)))).all()
    }
    rakusuke: dict[tuple[uuid.UUID, date], str] = {
        (sid, d): row.override_type
        for sid, per_day in (await load_overrides_between(db, first, last)).items()
        for d, row in per_day.items()
        if row.override_type in LEAVE_TYPES
    }
    records = {
        (row.staff_id, row.leave_date): row
        for row in (
            await db.scalars(
                select(StaffLeaveKaipoke).where(
                    StaffLeaveKaipoke.leave_date >= first, StaffLeaveKaipoke.leave_date <= last
                )
            )
        ).all()
    }
    config = await load_scheduling_config(db)
    business = (config.business_start, config.business_end)
    shifts = {
        (row.staff_id, row.weekday): row for row in (await db.scalars(select(StaffShift))).all()
    }
    internal_map = await build_staff_internal_map(db)
    wanted = set(staff_ids) if staff_ids is not None else None

    for staff_id, day in sorted(
        {*rakusuke, *records}, key=lambda k: (k[1], staff_names.get(k[0], ""), str(k[0]))
    ):
        if staff_id not in staff_names:
            continue
        if wanted is not None and staff_id not in wanted:
            continue
        name = staff_names[staff_id]
        r = rakusuke.get((staff_id, day))
        record = records.get((staff_id, day))
        s = record.sent_type if record is not None else None
        if r is None:
            if s is not None:
                plan.needs_action.append(
                    LeaveNeedsAction(
                        staff_id, name, day, None, s, f"カイポケの{_label(s)}を手で消してください"
                    )
                )
            continue
        if s is None:
            if record is not None and record.baseline_pending:
                plan.baseline_count += 1
                continue
            shift = shifts.get((staff_id, day.weekday()))
            start, end = kaipoke_send_window(
                r,
                shift.start_time if shift is not None else None,
                shift.end_time if shift is not None else None,
                bool(shift is not None and shift.is_on),
                business_hours=business,
            )
            internal = internal_map.get(staff_id)
            reason: str | None = None
            if day.weekday() == _SUNDAY:
                reason = MSG_SUNDAY
            elif day in unconfirmed_days:
                reason = MSG_UNCONFIRMED
            elif day in unknown_staff_days:
                reason = MSG_UNKNOWN_STAFF_DAY
            elif (staff_id, day) in pending:
                reason = MSG_DIFF_PENDING
            elif (staff_id, day) in rechoose:
                reason = MSG_RECOMPARE_AFTER_CHOOSE
            elif internal is None:
                reason = MSG_NO_INTERNAL_ID
            plan.items.append(
                LeaveSendItem(
                    staff_id=staff_id,
                    staff_name=name,
                    day=day,
                    leave_type=r,
                    title=LEAVE_SUBJECTS[r],
                    start=_hhmm(start),
                    end=_hhmm(end),
                    sendable=reason is None,
                    reason=reason,
                    staff_internal_id=internal,
                    record_id=record.id if record is not None else None,
                )
            )
        elif s == r:
            plan.sent_count += 1
        else:
            plan.needs_action.append(
                LeaveNeedsAction(
                    staff_id,
                    name,
                    day,
                    r,
                    s,
                    f"カイポケの{_label(s)}を消してください（{_label(r)}は送り直します）",
                )
            )
    return plan


# --- 始める -------------------------------------------------------------------------


@dataclass
class LeaveOutboundStart:
    """``start_leave_outbound`` の結果。``job`` が None なら送らない (``reason`` に理由)。"""

    plan: LeaveSendPlan
    job: KaipokeJob | None = None
    reason: str | None = None
    #: 送る件数 (ジョブに載せた件数)。
    count: int = 0
    #: 週の数 (進み具合の分母)。
    weeks: int = 0


async def start_leave_outbound(
    db: AsyncSession,
    *,
    month: date | str,
    user_id: uuid.UUID | None,
    staff_ids: Iterable[uuid.UUID] | None = None,
) -> LeaveOutboundStart:
    """送る予定を作り、送る休みがあれば段階 0 のジョブ (``leave-outbound``) を立てる。

    * 照合がまだの月・送る休みが無いときはジョブを立てず ``reason`` を返す。
    * ほかの RPA のジョブが動いていれば ``RpaJobConflictError`` (API は 409)。同時に押されて
      行がぶつかった (IntegrityError) ときも ``RpaJobConflictError``。
    * 送る休みの ``staff_leave_kaipoke`` の行 (``external_ref`` の id) が無ければここで作る
      (S = NULL・照合前でない = 未送信のまま。409 なら rollback で消える)。

    **新しいトランザクションで呼ぶこと** (月の確定から呼ぶときは確定の記録を commit して
    から)。実体は ``run_leave_outbound_job`` を応答の後に背景で呼ぶ。
    """
    plan = await build_leave_send_plan(db, month, staff_ids=staff_ids)
    if plan.blocked_reason is not None:
        return LeaveOutboundStart(plan=plan, reason=plan.blocked_reason)
    items = plan.sendable_items
    if not items:
        return LeaveOutboundStart(plan=plan, reason=MSG_NOTHING_TO_SEND)

    for item in items:
        if item.record_id is None:
            record = StaffLeaveKaipoke(
                id=uuid.uuid4(), staff_id=item.staff_id, leave_date=item.day, sent_type=None
            )
            db.add(record)
            item.record_id = record.id
    try:
        await db.flush()
    except IntegrityError as exc:
        # 同時に押された別の送信の開始が同じ (職員, 日) の行を先に作った → 409。
        await db.rollback()
        raise RpaJobConflictError([]) from exc

    weeks: dict[date, int] = defaultdict(int)
    for item in items:
        weeks[_monday(item.day)] += 1
    mondays = sorted(weeks)
    job = await sources.start_source_job(
        db,
        op=OP_LEAVE_OUTBOUND,
        week_start=mondays[0],
        user_id=user_id,
        params={
            "month": f"{plan.month.year:04d}-{plan.month.month:02d}",
            "targets": [
                {"week_start": monday.isoformat(), "count": weeks[monday]} for monday in mondays
            ],
            "items": [{**i.as_dict(), "staff_internal_id": i.staff_internal_id} for i in items],
            "needs_action": [n.as_dict() for n in plan.needs_action],
        },
        total=len(mondays),
    )
    return LeaveOutboundStart(plan=plan, job=job, count=len(items), weeks=len(mondays))


# --- 背景で送る ---------------------------------------------------------------------


async def _touch(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """待っている間もジョブの ``updated_at`` を書き戻す (残骸と見なされないように)。

    ``running`` でなくなっていれば (取り消し) False。
    """
    written = await db.execute(
        update(KaipokeJob)
        .where(KaipokeJob.id == job_id, KaipokeJob.status == _RUNNING)
        .values(updated_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return bool(getattr(written, "rowcount", 0))


async def _still_unsent(db: AsyncSession, item: dict[str, Any]) -> bool:
    """送る直前にも未送信か (R が予定の種類のまま・S が NULL・照合前でない)。"""
    staff_id = uuid.UUID(item["staff_id"])
    day = date.fromisoformat(item["date"])
    record = await db.get(StaffLeaveKaipoke, uuid.UUID(item["record_id"]), populate_existing=True)
    if record is None or record.sent_type is not None or record.baseline_pending:
        return False
    return await leave_type_on(db, staff_id, day) == item["leave_type"]


async def _apply_and_wait(
    db: AsyncSession,
    client: KaipokeClient,
    *,
    job_id: uuid.UUID,
    monday: date,
    rpa_items: list[dict[str, Any]],
    deadline: datetime,
) -> list[dict[str, Any]]:
    """RPA の個別業務の作成を 1 週ぶん呼び、結果 (``results[]``) を受け取るまで待つ。

    RPA の空き確認・待ち・再試行は段階 0 の ``call_when_idle``。待つのはジョブ全体の
    締め切り (``deadline``) まで。取れなければ ``RuntimeError`` (理由つき)。
    """
    credentials = await get_credentials_payload(db)
    await db.commit()
    rpa_job_id = f"{job_id}:{monday.isoformat()}"
    body: dict[str, Any] = {"items": rpa_items, "async": True, "job_id": rpa_job_id}
    if credentials:
        body["credentials"] = credentials

    async def _apply() -> dict[str, Any]:
        # 空き待ちのあいだに締め切りを過ぎたら、RPA へ頼まない。
        if datetime.now(UTC) >= deadline:
            raise RuntimeError(_deadline_message())
        return await client.individual_tasks_apply(body, timeout=_START_TIMEOUT)

    resp = await sources.call_when_idle(db, client, job_id=job_id, call=_apply)
    if not resp.get("success"):
        raise RuntimeError(str(resp.get("error") or "カイポケへの送信を始められませんでした"))

    while True:
        if datetime.now(UTC) >= deadline:
            raise RuntimeError(_deadline_message())
        await asyncio.sleep(APPLY_POLL_SECONDS)
        # 取り消されても、RPA は作り続けているので結果は受け取って記録する。
        await _touch(db, job_id)
        try:
            rpa = await client.individual_tasks_apply_result()
        except KaipokeApiError:
            continue  # 読めないときは締め切りまで読み直す
        status = str(rpa.get("status") or "")
        ours = str(rpa.get("job_id") or "") == rpa_job_id
        if status == "running":
            if rpa.get("job_id") and not ours:
                raise RuntimeError("カイポケ側で別の送信が動いていて結果を受け取れませんでした")
            continue
        if not ours:
            raise RuntimeError("カイポケ側で結果が失われました（カイポケで確かめてください）")
        if status == "error":
            raise RuntimeError(str(rpa.get("error") or "カイポケへの送信に失敗しました"))
        if status != "completed":
            raise RuntimeError(f"カイポケが不明な状態を返しました: {status}")
        results = (rpa.get("result") or {}).get("results")
        if not isinstance(results, list):
            raise RuntimeError("カイポケの送信の結果の形が不正です")
        return [r for r in results if isinstance(r, dict)]


def _deadline_message() -> str:
    return MSG_DEADLINE.format(minutes=f"{JOB_DEADLINE_MINUTES:g}")


def job_deadline(job: KaipokeJob) -> datetime:
    """ジョブ全体の締め切り = 開始 + ``JOB_DEADLINE_MINUTES`` (自動クローズの 120 分より手前)。"""
    started = job.started_at or job.created_at or datetime.now(UTC)
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started + timedelta(minutes=JOB_DEADLINE_MINUTES)


async def _record_sent(
    db: AsyncSession,
    *,
    item: dict[str, Any],
    key: str,
    sent_by: uuid.UUID | None,
) -> None:
    """送れた 1 件: ``sent_type``・``sent_at``・``sent_by``・``kaipoke_external_id`` と
    ``staff_events`` の ``source='kaipoke'`` の行 (同じキーの行があれば作らない)。"""
    record = await db.get(StaffLeaveKaipoke, uuid.UUID(item["record_id"]))
    if record is None:
        record = StaffLeaveKaipoke(
            id=uuid.UUID(item["record_id"]),
            staff_id=uuid.UUID(item["staff_id"]),
            leave_date=date.fromisoformat(item["date"]),
        )
        db.add(record)
    record.sent_type = item["leave_type"]
    record.baseline_pending = False
    record.sent_at = datetime.now(UTC)
    record.sent_by = sent_by
    record.kaipoke_external_id = key
    existing = await db.scalar(
        select(StaffEvent.id).where(
            StaffEvent.source == EVENT_SOURCE_KAIPOKE, StaffEvent.external_id == key
        )
    )
    if existing is None:
        day = date.fromisoformat(item["date"])
        # 予定の時刻は JST の壁時計をそのまま入れる決まり (取込と同じ)。
        db.add(
            StaffEvent(
                staff_id=uuid.UUID(item["staff_id"]),
                event_type=EVENT_TYPE_IMPORTED,
                starts_at=datetime.combine(day, time.fromisoformat(item["start"])),
                ends_at=datetime.combine(day, time.fromisoformat(item["end"])),
                title=item["title"],
                note=_EVENT_NOTE,
                source=EVENT_SOURCE_KAIPOKE,
                external_id=key,
            )
        )


async def _save_week_outcomes(
    db: AsyncSession, job_id: uuid.UUID, outcomes: list[dict[str, Any]]
) -> None:
    """1 週ぶんの結果をジョブの ``result_summary.items`` に足す (取り消し後も記録する)。"""
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None:
        return
    summary = dict(job.result_summary or {})
    summary["items"] = [*(summary.get("items") or []), *outcomes]
    await db.execute(
        update(KaipokeJob)
        .where(KaipokeJob.id == job_id)
        .values(result_summary=summary, updated_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )


def _outcome(item: dict[str, Any], outcome: str, **extra: Any) -> dict[str, Any]:
    return {
        "staff_id": item["staff_id"],
        "staff_name": item.get("staff_name"),
        "date": item["date"],
        "leave_type": item["leave_type"],
        "title": item["title"],
        "start": item["start"],
        "end": item["end"],
        "outcome": outcome,
        "external_key": extra.get("external_key"),
        "reason": extra.get("reason"),
    }


async def send_leave_week(
    db: AsyncSession,
    client: KaipokeClient,
    *,
    job_id: uuid.UUID,
    monday: date,
    items: list[dict[str, Any]],
    sent_by: uuid.UUID | None,
    deadline: datetime,
) -> SourceFetchResult:
    """1 週ぶんの休みを送り、送れた分を書く。進み具合の 1 本 (週) の結果を返す。

    送る直前に未送信のままか確かめ、変わっていたものは送らない。ジョブ全体の締め切り
    (``deadline``) を過ぎていれば RPA を呼ばず、未送信のまま失敗として理由を残す。
    送れた分の ``sent_type`` などと ``staff_events`` の行、ジョブの ``result_summary.items``
    を 1 つのトランザクションで書いて commit する。
    """
    outcomes: list[dict[str, Any]] = []
    to_send: list[dict[str, Any]] = []
    for item in items:
        if await _still_unsent(db, item):
            to_send.append(item)
        else:
            outcomes.append(_outcome(item, OUTCOME_SKIPPED, reason=MSG_CHANGED))
    await db.commit()

    error: str | None = None
    if to_send:
        rpa_items = [
            {
                "external_ref": f"leave:{item['record_id']}",
                "staff_internal_id": item["staff_internal_id"],
                "date": item["date"],
                "start": item["start"],
                "end": item["end"],
                "title": item["title"],
            }
            for item in to_send
        ]
        results: list[dict[str, Any]] = []
        if datetime.now(UTC) >= deadline:
            error = _deadline_message()
        else:
            try:
                results = await _apply_and_wait(
                    db,
                    client,
                    job_id=job_id,
                    monday=monday,
                    rpa_items=rpa_items,
                    deadline=deadline,
                )
            except (RpaUnavailableError, KaipokeApiError, RuntimeError) as exc:
                error = _short(exc)
        by_ref = {str(r.get("external_ref")): r for r in results}
        for item in to_send:
            got = by_ref.get(f"leave:{item['record_id']}")
            key = str((got or {}).get("external_key") or "")
            if got is not None and got.get("outcome") in _RPA_OK and RE_EXTERNAL_ID.match(key):
                await _record_sent(db, item=item, key=key, sent_by=sent_by)
                outcomes.append(_outcome(item, OUTCOME_SENT, external_key=key))
                continue
            if got is None:
                reason = error or MSG_NO_RESULT
            elif got.get("outcome") in _RPA_OK:
                reason = MSG_BAD_KEY
            else:
                reason = _short(
                    got.get("error") or got.get("detail") or got.get("reason") or "送れませんでした"
                )
            outcomes.append(_outcome(item, OUTCOME_FAILED, reason=reason))

    await _save_week_outcomes(db, job_id, outcomes)
    await db.commit()
    sent = sum(1 for o in outcomes if o["outcome"] == OUTCOME_SENT)
    failed = sum(1 for o in outcomes if o["outcome"] == OUTCOME_FAILED)
    if failed and error is None:
        error = f"{failed}件送れませんでした"
    return SourceFetchResult(
        kind=KIND_LEAVE_WEEK,
        period_start=monday,
        status=STATUS_FAILED if failed else STATUS_OK,
        row_count=sent,
        error=error if failed else None,
    )


async def _close_failed(
    db: AsyncSession, job_id: uuid.UUID, results: list[SourceFetchResult], error: str
) -> KaipokeJob | None:
    try:
        await db.rollback()
        return await sources.finish_source_job(db, job_id, results, error=error)
    except Exception:  # noqa: BLE001 — 元の失敗を隠さない (ここはログだけ)
        logger.exception("leave outbound job %s could not be closed (%s)", job_id, error)
        return None


async def run_leave_outbound(
    db: AsyncSession, client: KaipokeClient, job_id: uuid.UUID
) -> KaipokeJob | None:
    """ジョブに載せた休みを週ごとに順番に送り、ジョブを必ず決着させる。

    * 1 週ごとに進み具合 (段階 0 の ``record_progress``) を書き戻す。
    * 一部の失敗は ``result_summary.failed[]`` (週) と ``items[]`` (1 件ずつ) に残して次へ。
    * 途中で取り消されたら次の週へ進まない (送った週の記録は残す)。
    """
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or job.status != _RUNNING:
        await db.rollback()
        return job
    params = job.params or {}
    sent_by = job.created_by_user_id
    deadline = job_deadline(job)
    by_week: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for item in params.get("items") or []:
        by_week[_monday(date.fromisoformat(item["date"]))].append(item)
    await db.commit()

    results: list[SourceFetchResult] = []
    try:
        for monday in sorted(by_week):
            result = await send_leave_week(
                db,
                client,
                job_id=job_id,
                monday=monday,
                items=by_week[monday],
                sent_by=sent_by,
                deadline=deadline,
            )
            results.append(result)
            await sources.record_progress(db, job_id, result)
    except SourceJobCancelledError:
        await db.rollback()
        return await db.get(KaipokeJob, job_id, populate_existing=True)
    except asyncio.CancelledError:
        await asyncio.shield(_close_failed(db, job_id, results, "中断されました"))
        raise
    except Exception as exc:  # noqa: BLE001 — 黙って死なせない (running が残る)
        return await _close_failed(
            db, job_id, results, f"休みの送信の途中で失敗しました: {_short(exc)}"
        )
    return await sources.finish_source_job(db, job_id, results)


async def run_leave_outbound_job(*, job_id: uuid.UUID, client: KaipokeClient) -> KaipokeJob | None:
    """応答の後に自前のセッションで ``run_leave_outbound`` を呼ぶ (``BackgroundTasks`` 用)。"""
    factory = get_session_factory()
    async with factory() as db:
        return await run_leave_outbound(db, client, job_id)


__all__ = [
    "APPLY_POLL_SECONDS",
    "JOB_DEADLINE_MINUTES",
    "KIND_LEAVE_WEEK",
    "LEAVE_SUBJECTS",
    "MSG_DEADLINE",
    "MSG_DIFF_PENDING",
    "MSG_NOTHING_TO_SEND",
    "MSG_NOT_COMPARED",
    "MSG_RECOMPARE_AFTER_CHOOSE",
    "MSG_UNCONFIRMED",
    "MSG_UNKNOWN_STAFF_DAY",
    "OUTCOME_FAILED",
    "OUTCOME_SENT",
    "OUTCOME_SKIPPED",
    "LeaveNeedsAction",
    "LeaveOutboundStart",
    "LeaveSendItem",
    "LeaveSendPlan",
    "build_leave_send_plan",
    "job_deadline",
    "run_leave_outbound",
    "run_leave_outbound_job",
    "send_leave_week",
    "start_leave_outbound",
]
