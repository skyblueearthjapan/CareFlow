"""カイポケの材料 (予定・実績の CSV と個別業務) の取り直し — 休みと受け入れ枠の共通の土台.

正典: ``docs/plans/shared-foundation-leave-acceptance-2026-10-20.md`` §0-2〜0-4。

* 取ったものは ``kaipoke_source_snapshots`` に ``(kind, period_start)`` で 1 行ずつ置く
  (``kaipoke_csv_snapshots`` には書かない)。1 本ごとに commit する。
* 空 (RPA が 0 件と明示・ヘッダだけ) は ``empty``、取れなかったら ``failed``。
  ``failed`` は前に取れた行 (``ok`` / ``empty``) を消さない。
* 拠点で行を絞らない (``office_id`` は常に NULL)。
* ジョブは ``KaipokeJob`` (``params.op`` = ``acceptance-refresh`` / ``leave-compare``)。
  始める前に、種類を問わず ``pending`` / ``running`` のジョブがあれば 409。
* 1 本ごとに RPA の空きを確かめ (他のジョブ・RPA の実行中)、塞がっていれば 30 秒おきに
  最大 10 回待つ。それでも空かなければその本を ``failed`` にして次へ。
* 個別業務は同期モード (``async``・``job_id`` なし) で取る。他のジョブが動いている間は
  RPA を呼ばないので、イベント取り込みの非同期の結果の置き場を上書きしない。
  同期の取得が時間切れになったら、RPA が空くのを確かめてから次の本へ進む。
* 待ちとカイポケへの呼び出しの前に DB のトランザクションを閉じる (鍵や行を抱えたまま待たない)。
* 段階 L・A は ``start_source_job`` → ``run_source_job`` の順に使う (``run_source_job`` を参照)。
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update

from app.models.kaipoke_job import KaipokeJob
from app.models.kaipoke_source_snapshot import KaipokeSourceSnapshot
from app.services.kaipoke.credentials import get_credentials_payload
from app.services.kaipoke.events_inbound import EventsFetchError
from app.services.kaipoke.events_inbound import fetch_week_tasks as rpa_fetch_week_tasks
from app.services.kaipoke.inbound import load_staff_name_index
from app.services.kaipoke.name_match import match_name
from app.services.kaipoke.plan_actual_fetch import _export_one
from app.services.kaipoke_client import KaipokeApiError, KaipokeBusyError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.kaipoke_client import KaipokeClient

logger = logging.getLogger(__name__)

#: ``KaipokeJob.params["op"]`` の値。
OP_ACCEPTANCE_REFRESH: Final = "acceptance-refresh"
OP_LEAVE_COMPARE: Final = "leave-compare"
#: 休みをカイポケへ送る (休みの段階 L が使う)。
OP_LEAVE_OUTBOUND: Final = "leave-outbound"
SOURCE_OPS: Final = (OP_ACCEPTANCE_REFRESH, OP_LEAVE_COMPARE, OP_LEAVE_OUTBOUND)
#: op → ``KaipokeJob.job_type``。取り直しは "fetch"、送信は今のイベント送信
#: (op="events-outbound") と同じ "push"。op の名前は job_type に入れない。
_JOB_TYPES: Final = {
    OP_ACCEPTANCE_REFRESH: "fetch",
    OP_LEAVE_COMPARE: "fetch",
    OP_LEAVE_OUTBOUND: "push",
}

#: ``kaipoke_source_snapshots.kind``。
KIND_PLAN_CSV: Final = "plan_csv"
KIND_ACTUAL_CSV: Final = "actual_csv"
KIND_TASKS: Final = "tasks"
#: CSV の種類 → RPA の export の ``division``。
_CSV_DIVISIONS: Final = {KIND_PLAN_CSV: "plan", KIND_ACTUAL_CSV: "actual"}

#: ``kaipoke_source_snapshots.status``。
STATUS_OK: Final = "ok"
STATUS_EMPTY: Final = "empty"
STATUS_FAILED: Final = "failed"

#: RPA が塞がっているときに待つ間隔 (秒) と回数。
RETRY_INTERVAL_SECONDS: float = 30.0
MAX_RETRIES: int = 10
#: これより長く更新の無い pending/running ジョブは残骸とみなして数えない (書き換えない)。
STALE_JOB_MINUTES: Final = 30
#: CSV の職員名の列 (主担当・同行)。
_CSV_STAFF_COLUMNS: Final = ("職員名１", "職員名２", "職員名３")
#: CSV で「担当なし」を表す値。
_CSV_NO_STAFF: Final = "-"
#: 失敗の理由の最大長 (短く)。
_ERROR_MAX_LEN: Final = 300

_ACTIVE_JOB_STATUSES: Final = ("pending", "running")
_RUNNING: Final = "running"
#: 「動いているジョブを数える → 作る」を 1 本ずつにする鍵 (PG の advisory lock のキー)。
_START_LOCK_KEY: Final = 7_202_610_200_098
#: 同じプロセスの中で同時に立てるのを防ぐ (SQLite の試験では advisory lock が無い)。
_START_LOCK = asyncio.Lock()
_JST: Final = ZoneInfo("Asia/Tokyo")
#: 17〜18 時台 (JST) は送信・取り込みと重なりやすい。
_EVENING_HOURS: Final = (17, 18)
EVENING_WARNING: Final = (
    "夕方は送信・取り込みと重なりやすい時間です。急がなければ他の時間に取り直してください"
)

_MSG_JOB_RUNNING: Final = "カイポケの送信・取り込みが動いていたため取れませんでした"
_MSG_RPA_BUSY: Final = "カイポケが空かなかったため取れませんでした"


class RpaJobConflictError(RuntimeError):
    """RPA を使う他のジョブが動いている (API は 409 で返す)。"""

    status_code: Final = 409

    def __init__(self, jobs: list[KaipokeJob]) -> None:
        super().__init__(
            "カイポケの送信・取り込みが動いています。終わってからもう一度お試しください"
        )
        self.job_ids = [job.id for job in jobs]


class RpaUnavailableError(RuntimeError):
    """待っても RPA が空かなかった。"""


class SourceJobCancelledError(RuntimeError):
    """ジョブが途中で取り消された (``running`` でなくなった)。取り直しを止める。"""

    def __init__(self, job_id: uuid.UUID) -> None:
        super().__init__(f"kaipoke source job {job_id} is no longer running")
        self.job_id = job_id


@dataclass(frozen=True)
class SourceSpec:
    """取る 1 本。``kind`` が CSV なら ``period`` はその月の 1 日、``tasks`` なら週の月曜。"""

    kind: str
    period: date


@dataclass
class SourceFetchResult:
    """1 本の取り直しの結果。"""

    kind: str
    period_start: date
    status: str
    row_count: int = 0
    error: str | None = None
    snapshot_id: uuid.UUID | None = None
    #: らく助の職員に結び付かなかった名前 → 件数 (黙って捨てない)。
    unmatched_names: dict[str, int] = field(default_factory=dict)

    def as_summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "period_start": self.period_start.isoformat(),
            "status": self.status,
            "row_count": self.row_count,
            "error": self.error,
            "snapshot_id": str(self.snapshot_id) if self.snapshot_id else None,
            "unmatched_names": dict(self.unmatched_names),
        }


def evening_warning(now: datetime | None = None) -> str | None:
    """17〜18 時台 (JST) なら画面に出す注意。押すのは止めない。"""
    current = (now or datetime.now(UTC)).astimezone(_JST)
    return EVENING_WARNING if current.hour in _EVENING_HOURS else None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def active_rpa_jobs(
    db: AsyncSession, *, exclude_job_id: uuid.UUID | None = None
) -> list[KaipokeJob]:
    """RPA を使う (種類を問わない) ``pending`` / ``running`` のジョブ。残骸は数えない。"""
    cutoff = datetime.now(UTC) - timedelta(minutes=STALE_JOB_MINUTES)
    rows = (
        await db.scalars(
            select(KaipokeJob)
            .where(KaipokeJob.status.in_(_ACTIVE_JOB_STATUSES))
            .order_by(KaipokeJob.created_at.desc())
            .execution_options(populate_existing=True)
        )
    ).all()
    out: list[KaipokeJob] = []
    for job in rows:
        if job.id == exclude_job_id:
            continue
        # 長い取り直しは 1 本ごとに updated_at を書き戻すので、最後の更新で見る。
        touched = _aware(job.updated_at or job.created_at)
        if touched is not None and touched < cutoff:
            continue
        out.append(job)
    return out


async def start_source_job(
    db: AsyncSession,
    *,
    op: str,
    week_start: date,
    user_id: uuid.UUID | None,
    params: dict[str, Any] | None = None,
    total: int | None = None,
) -> KaipokeJob:
    """ジョブを ``running`` で立てて commit する。他のジョブが動いていれば 409。

    ``week_start`` は取る範囲の最初の月曜。``total`` は取る本数 (進み具合の分母)。
    「動いているジョブを数える → 作る」は鍵を取った 1 つのトランザクションで行う
    (同時に 2 本押されても 1 本は 409。PG は advisory lock、同じプロセスは asyncio の鍵)。
    **新しいトランザクションで呼ぶこと** (409 のときの rollback は、呼び出し側の
    未 commit の変更も捨てるため)。
    """
    if op not in SOURCE_OPS:
        raise ValueError(f"unknown source op: {op}")
    if week_start.weekday() != 0:
        raise ValueError(f"week_start must be a Monday: {week_start}")
    async with _START_LOCK:
        if db.get_bind().dialect.name == "postgresql":
            await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _START_LOCK_KEY})
        blocking = await active_rpa_jobs(db)
        if blocking:
            conflict = RpaJobConflictError(blocking)  # rollback で行が期限切れになる前に読む
            await db.rollback()  # 鍵を外す
            raise conflict
        return await _create_job(
            db, op=op, week_start=week_start, user_id=user_id, params=params, total=total
        )


async def _create_job(
    db: AsyncSession,
    *,
    op: str,
    week_start: date,
    user_id: uuid.UUID | None,
    params: dict[str, Any] | None,
    total: int | None,
) -> KaipokeJob:
    job = KaipokeJob(
        job_type=_JOB_TYPES[op],
        status="running",
        week_start=week_start,
        params={**(params or {}), "op": op},
        result_summary={"done": 0, "total": total, "current": None, "results": [], "failed": []},
        started_at=datetime.now(UTC),
        created_by_user_id=user_id,
    )
    db.add(job)
    await db.commit()  # 鍵もここで外れる
    return job


def _failure_entry(result: SourceFetchResult) -> dict[str, Any]:
    return {
        "kind": result.kind,
        "period_start": result.period_start.isoformat(),
        "reason": result.error,
    }


async def _write_if_running(db: AsyncSession, job_id: uuid.UUID, **values: Any) -> bool:
    """``status='running'`` のときだけジョブを書き換えて commit する。書けたら True。"""
    written = await db.execute(
        update(KaipokeJob)
        .where(KaipokeJob.id == job_id, KaipokeJob.status == _RUNNING)
        .values(**values, updated_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return bool(getattr(written, "rowcount", 0))


async def record_progress(db: AsyncSession, job_id: uuid.UUID, result: SourceFetchResult) -> None:
    """1 本終わるごとにジョブの ``updated_at`` と進み具合を書き戻す (途中で閉じられないように)。

    ジョブが ``running`` でなくなっていたら (取り消し) ``SourceJobCancelledError``。
    """
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or job.status != _RUNNING:
        await db.rollback()
        raise SourceJobCancelledError(job_id)
    summary = dict(job.result_summary or {})
    summary["done"] = int(summary.get("done") or 0) + 1
    summary["current"] = {"kind": result.kind, "period_start": result.period_start.isoformat()}
    summary["results"] = [*(summary.get("results") or []), result.as_summary()]
    if result.status == STATUS_FAILED:
        summary["failed"] = [*(summary.get("failed") or []), _failure_entry(result)]
    if not await _write_if_running(db, job_id, result_summary=summary):
        raise SourceJobCancelledError(job_id)


async def finish_source_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    results: list[SourceFetchResult],
    *,
    error: str | None = None,
) -> KaipokeJob | None:
    """ジョブを決着させる。一部の失敗は ``completed`` のまま ``result_summary.failed[]`` に。

    ``error`` を渡したとき (ジョブ自体が壊れた) だけ ``failed``。
    ``running`` でなくなったジョブ (取り消し済みなど) は書き換えずにそのまま返す。
    """
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or job.status != _RUNNING:
        await db.rollback()
        return job
    unmatched: dict[str, int] = {}
    for result in results:
        for name, count in result.unmatched_names.items():
            unmatched[name] = unmatched.get(name, 0) + count
    previous = job.result_summary or {}
    summary: dict[str, Any] = {
        **previous,
        "done": len(results),
        "total": max(int(previous.get("total") or 0), len(results)),
        "current": None,
        "results": [r.as_summary() for r in results],
        "failed": [_failure_entry(r) for r in results if r.status == STATUS_FAILED],
        "unmatched_names": unmatched,
    }
    if error is not None:
        summary["error"] = error
    await _write_if_running(
        db,
        job_id,
        status="failed" if error is not None else "completed",
        completed_at=datetime.now(UTC),
        result_summary=summary,
    )
    return await db.get(KaipokeJob, job_id, populate_existing=True)


async def _rpa_busy_reason(
    db: AsyncSession, client: KaipokeClient, *, job_id: uuid.UUID | None
) -> str | None:
    """RPA が使えなければ理由、使えれば None。読んだらトランザクションを閉じてから RPA を見る。"""
    others = await active_rpa_jobs(db, exclude_job_id=job_id)
    await db.commit()
    if others:
        return _MSG_JOB_RUNNING
    try:
        raw = await client.status()
    except KaipokeApiError as exc:
        return f"カイポケの状態を確かめられませんでした（{exc}）"
    if bool((raw.get("current_task") or {}).get("running")):
        return _MSG_RPA_BUSY
    return None


async def call_when_idle[T](
    db: AsyncSession,
    client: KaipokeClient,
    *,
    job_id: uuid.UUID | None,
    call: Callable[[], Awaitable[T]],
) -> T:
    """RPA が空くのを待ってから ``call`` する (30 秒おき・最大 10 回)。

    待ちの各回で自分のジョブがまだ ``running`` か確かめる (違えば ``SourceJobCancelledError``)。
    """
    reason = _MSG_RPA_BUSY
    for attempt in range(MAX_RETRIES + 1):
        if attempt:
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)
        await _ensure_running(db, job_id)
        busy = await _rpa_busy_reason(db, client, job_id=job_id)
        if busy is not None:
            reason = busy
            continue
        try:
            return await call()
        except KaipokeBusyError:
            reason = _MSG_RPA_BUSY
    raise RpaUnavailableError(reason)


async def _ensure_running(db: AsyncSession, job_id: uuid.UUID | None) -> None:
    """自分のジョブがまだ ``running`` か確かめる。違えば ``SourceJobCancelledError``。"""
    if job_id is None:
        return
    status = await db.scalar(select(KaipokeJob.status).where(KaipokeJob.id == job_id))
    await db.commit()
    if status != _RUNNING:
        raise SourceJobCancelledError(job_id)


async def _wait_rpa_idle(
    db: AsyncSession, client: KaipokeClient, *, job_id: uuid.UUID | None
) -> bool:
    """RPA が実行中でなくなるまで待つ (30 秒おき・最大 10 回)。空いたら True。

    待ちの各回で自分のジョブがまだ ``running`` か確かめる (違えば ``SourceJobCancelledError``)。
    """
    for attempt in range(MAX_RETRIES + 1):
        if attempt:
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)
        await _ensure_running(db, job_id)
        try:
            raw = await client.status()
        except KaipokeApiError:
            continue
        if not bool((raw.get("current_task") or {}).get("running")):
            return True
    return False


def _short(message: object) -> str:
    return str(message)[:_ERROR_MAX_LEN]


async def _save(
    db: AsyncSession,
    *,
    kind: str,
    period_start: date,
    status: str,
    rows: list[Any],
    covered_dates: list[str] | None,
    error: str | None,
    job_id: uuid.UUID | None,
) -> KaipokeSourceSnapshot:
    """``(kind, period_start)`` の行を置き換えて commit する。

    失敗のとき、前に ``ok`` / ``empty`` の行があれば書き換えない (失敗はジョブの
    ``result_summary.failed[]`` にだけ残る)。前の行が無いときだけ ``failed`` の行を書く。
    """
    existing = await db.scalar(
        select(KaipokeSourceSnapshot).where(
            KaipokeSourceSnapshot.kind == kind,
            KaipokeSourceSnapshot.period_start == period_start,
        )
    )
    if status == STATUS_FAILED and existing is not None and existing.status != STATUS_FAILED:
        return existing
    row = existing or KaipokeSourceSnapshot(kind=kind, period_start=period_start)
    row.office_id = None
    row.rows = rows
    row.covered_dates = covered_dates
    row.row_count = len(rows)
    row.status = status
    row.error = error
    row.fetched_at = datetime.now(UTC)
    row.job_id = job_id
    if existing is None:
        db.add(row)
    await db.commit()
    return row


async def _unmatched_names(db: AsyncSession, names: list[str]) -> dict[str, int]:
    """らく助の職員 (``staff.name``) に結び付かない名前 → 件数。"""
    name_index, _ = await load_staff_name_index(db)
    out: dict[str, int] = {}
    for name in names:
        if match_name(name, name_index) is None:
            out[name] = out.get(name, 0) + 1
    return out


def parse_csv_rows(csv_text: str) -> list[dict[str, str]]:
    """カイポケの CSV → 1 行 1 オブジェクト (ヘッダ名で)。空の行は捨てる。"""
    text = (csv_text or "").lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    out: list[dict[str, str]] = []
    for raw in reader:
        row = {(k or "").strip(): (v or "") for k, v in raw.items() if k is not None}
        if any(value.strip() for value in row.values()):
            out.append(row)
    return out


def covered_dates_of(result: dict[str, Any]) -> list[str] | None:
    """RPA の結果の ``week_dates`` (画面に出た日) → ISO 日付の並び。返らなければ None。

    読めない値は捨てる。``[]`` や全部読めないときは空の一覧のまま返す (= どの日も
    表示を確かめられていない。None = RPA が返さなかった とは区別する)。
    """
    raw = result.get("week_dates")
    if not isinstance(raw, list):
        return None
    out: set[date] = set()
    for value in raw:
        try:
            out.add(date.fromisoformat(str(value)))
        except ValueError:
            continue
    return [d.isoformat() for d in sorted(out)]


def _month_start(month: str) -> date:
    year, mon = month.split("-")
    return date(int(year), int(mon), 1)


async def _store(
    db: AsyncSession,
    *,
    kind: str,
    period_start: date,
    job_id: uuid.UUID | None,
    rows: list[Any] | None = None,
    covered_dates: list[str] | None = None,
    error: str | None = None,
    unmatched: dict[str, int] | None = None,
) -> SourceFetchResult:
    """1 本の結果を保存し、ジョブがあれば進み具合を書き戻す。``error`` があれば失敗。"""
    if error is not None:
        status = STATUS_FAILED
    else:
        status = STATUS_OK if rows else STATUS_EMPTY
    snapshot = await _save(
        db,
        kind=kind,
        period_start=period_start,
        status=status,
        rows=rows or [],
        covered_dates=covered_dates,
        error=error,
        job_id=job_id,
    )
    result = SourceFetchResult(
        kind=kind,
        period_start=period_start,
        status=status,
        row_count=len(rows or []),
        error=error,
        snapshot_id=snapshot.id if status != STATUS_FAILED else None,
        unmatched_names=unmatched or {},
    )
    if job_id is not None:
        await record_progress(db, job_id, result)
    return result


async def fetch_month_csv(
    db: AsyncSession,
    client: KaipokeClient,
    kind: str,
    month: str,
    *,
    job_id: uuid.UUID | None = None,
) -> SourceFetchResult:
    """予定 (``plan_csv``) か実績 (``actual_csv``) の CSV を 1 か月 (``YYYY-MM``) 取って置く。"""
    division = _CSV_DIVISIONS.get(kind)
    if division is None:
        raise ValueError(f"not a csv kind: {kind}")
    period_start = _month_start(month)
    credentials = await get_credentials_payload(db)
    await db.commit()
    try:
        csv_text = await call_when_idle(
            db,
            client,
            job_id=job_id,
            call=lambda: _export_one(
                client=client, month=month, division=division, credentials=credentials
            ),
        )
    except (RpaUnavailableError, KaipokeApiError) as exc:
        return await _store(
            db, kind=kind, period_start=period_start, job_id=job_id, error=_short(exc)
        )

    rows = parse_csv_rows(csv_text)
    names = [
        name
        for row in rows
        for column in _CSV_STAFF_COLUMNS
        if (name := (row.get(column) or "").strip()) and name != _CSV_NO_STAFF
    ]
    return await _store(
        db,
        kind=kind,
        period_start=period_start,
        job_id=job_id,
        rows=rows,
        unmatched=await _unmatched_names(db, names),
    )


async def fetch_week_tasks(
    db: AsyncSession,
    client: KaipokeClient,
    week_start: date,
    *,
    job_id: uuid.UUID | None = None,
) -> SourceFetchResult:
    """1 週 (月曜始まり) の個別業務を RPA の同期モードで取って置く (``staff_events`` には書かない)。"""
    if week_start.weekday() != 0:
        raise ValueError(f"week_start must be a Monday: {week_start}")
    credentials = await get_credentials_payload(db)
    await db.commit()
    try:
        result = await call_when_idle(
            db,
            client,
            job_id=job_id,
            call=lambda: rpa_fetch_week_tasks(client, week_start, credentials),
        )
    except (RpaUnavailableError, KaipokeApiError, EventsFetchError) as exc:
        error = _short(exc)
        if isinstance(exc, KaipokeApiError) and exc.status_code == 504:
            # 同期の取得が時間切れ: RPA はまだ動いているかもしれない。空くのを確かめてから
            # 次の本へ (次の呼び出しが動いている取得の結果の置き場を上書きしないように)。
            if not await _wait_rpa_idle(db, client, job_id=job_id):
                error = _short(f"{error}（時間切れのあと{_MSG_RPA_BUSY}）")
        return await _store(
            db, kind=KIND_TASKS, period_start=week_start, job_id=job_id, error=error
        )

    tasks = list(result.get("tasks") or [])
    names = [
        name
        for task in tasks
        if isinstance(task, dict) and (name := str(task.get("staff_name") or "").strip())
    ]
    return await _store(
        db,
        kind=KIND_TASKS,
        period_start=week_start,
        job_id=job_id,
        rows=tasks,
        covered_dates=covered_dates_of(result),
        unmatched=await _unmatched_names(db, names),
    )


async def run_source_job(
    db: AsyncSession,
    client: KaipokeClient,
    job_id: uuid.UUID,
    plan: list[SourceSpec],
) -> KaipokeJob | None:
    """``plan`` の本を 1 本ずつ取り、ジョブを必ず決着させる (段階 L・A の入口)。

    使い方 (API は 202 を返し、実体は BackgroundTasks で自前のセッションから呼ぶ)::

        plan = [SourceSpec(KIND_ACTUAL_CSV, date(2026, 10, 1)),
                SourceSpec(KIND_TASKS, date(2026, 10, 19))]
        try:
            job = await start_source_job(db, op=OP_LEAVE_COMPARE, week_start=monday,
                                         user_id=user.id, total=len(plan))
        except RpaJobConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        background.add_task(_run, job.id, plan)

        async def _run(job_id, plan):
            async with get_session_factory()() as db:
                await run_source_job(db, client, job_id, plan)

    * 1 本ごとに保存・commit し、ジョブの ``updated_at`` と進み具合を書き戻す。
    * 1 本の失敗は ``result_summary.failed[]`` に入れて次へ (最後は ``completed``)。
    * 途中でジョブが取り消されたら、そこで止めて書き換えない。
    * 思わぬ例外のときは rollback して ``finish_source_job(error=...)`` で ``failed`` に閉じる。
    """
    results: list[SourceFetchResult] = []
    try:
        for spec in plan:
            if spec.kind == KIND_TASKS:
                result = await fetch_week_tasks(db, client, spec.period, job_id=job_id)
            else:
                month = f"{spec.period.year:04d}-{spec.period.month:02d}"
                result = await fetch_month_csv(db, client, spec.kind, month, job_id=job_id)
            results.append(result)
    except SourceJobCancelledError:
        await db.rollback()
        return await db.get(KaipokeJob, job_id, populate_existing=True)
    except asyncio.CancelledError:
        # タスクごと止められた (プロセスの終了など)。閉じ終えるまでは止めさせない。
        await asyncio.shield(_close_failed(db, job_id, results, "中断されました"))
        raise
    except Exception as exc:  # noqa: BLE001 — 黙って死なせない (running が残る)
        return await _close_failed(
            db, job_id, results, f"取り直しの途中で失敗しました: {_short(exc)}"
        )
    return await finish_source_job(db, job_id, results)


async def _close_failed(
    db: AsyncSession, job_id: uuid.UUID, results: list[SourceFetchResult], error: str
) -> KaipokeJob | None:
    """rollback してジョブを ``failed`` で閉じる。閉じられなければログに残す。"""
    try:
        await db.rollback()
        return await finish_source_job(db, job_id, results, error=error)
    except Exception:  # noqa: BLE001 — 元の失敗を隠さない (ここはログだけ)
        logger.exception("kaipoke source job %s could not be closed (%s)", job_id, error)
        return None


__all__ = [
    "EVENING_WARNING",
    "KIND_ACTUAL_CSV",
    "KIND_PLAN_CSV",
    "KIND_TASKS",
    "MAX_RETRIES",
    "OP_ACCEPTANCE_REFRESH",
    "OP_LEAVE_COMPARE",
    "OP_LEAVE_OUTBOUND",
    "RETRY_INTERVAL_SECONDS",
    "SOURCE_OPS",
    "STALE_JOB_MINUTES",
    "STATUS_EMPTY",
    "STATUS_FAILED",
    "STATUS_OK",
    "RpaJobConflictError",
    "RpaUnavailableError",
    "SourceJobCancelledError",
    "SourceSpec",
    "SourceFetchResult",
    "active_rpa_jobs",
    "call_when_idle",
    "covered_dates_of",
    "evening_warning",
    "fetch_month_csv",
    "fetch_week_tasks",
    "finish_source_job",
    "parse_csv_rows",
    "record_progress",
    "run_source_job",
    "start_source_job",
]
