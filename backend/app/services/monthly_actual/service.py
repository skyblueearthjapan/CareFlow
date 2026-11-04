"""月の実績 — DB の読み出し・判定の保存・RPA で反映するジョブ・予約・通知.

正典: ``docs/plans/kaipoke-actual-write-design-2026-11-01.md`` §5・§5-2・§5-3。判定の決まりは
``judge``。RPA の口は PlaywrightTest1 ``ACTUAL_TIME_EDIT_SPEC.md``
(``POST /api/actual-time/apply``・``GET /api/actual-time/result``)。

* らく助の側: ``load_actuals`` (後から合わせた時刻を含む) が正。取消・削除・未訪問は除く。
* カイポケの側: ``kaipoke_csv_snapshots`` (division='actual') の最新。取り直しは
  ``start_refresh`` (実績の export だけ・予実比較の ``_export_one`` を使う)。連携の
  「予実比較（月）」で取り直した実績も、そのまま使う (GET のときに新しければ判定し直す)。
* ジョブは段階 0 の ``KaipokeJob`` (``sources.start_source_job``・advisory lock・他のジョブが
  動いていれば 409)。実体は応答の後に自前のセッションで走る (``BackgroundTasks``)。
* 反映は過去の月だけ。選んだ行だけを送る。本番で成功した行は二度送らない。送る項目の鍵
  (利用者名を含む) が重なれば送らない。反映 (本番) の後は実績を読み取り直して確かめる。
* 記録書Ⅱ (PO 決定 2026-11-04・``docs/plans/kaipoke-kiroku2-time-design-2026-11-04.md`` §2-2):
  実績も書き換える行は ``/api/actual-time/apply`` の項目に ``care_record`` (いまの記録書Ⅱの
  時刻) を付けて続けてそろえる。実績はもう QR どおりで記録書Ⅱだけ違う行は
  ``/api/care-record-time/apply`` で送る。RPA は 1 つずつなので、実績 → 記録書Ⅱだけ の順に
  続けて流す。結果は実績 (``apply_*``) と記録書Ⅱ (``care_record_*``) を別に持つ。
* 終わったら管理者全員にアプリ内通知 (成功・失敗の件数)。
* 夜の予約は ``scheduled_at`` に置き、VPS の cron が ``run_due_schedules`` を叩いて始める。
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update

from app.db.session import get_session_factory
from app.models.kaipoke_job import KaipokeJob
from app.models.monthly_actual import MonthlyActualRow, MonthlyActualRun
from app.models.notification import Notification
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import Visit
from app.services.checkin.actuals import as_utc, load_actuals
from app.services.kaipoke import sources
from app.services.kaipoke.credentials import get_credentials_payload
from app.services.kaipoke.csv_snapshot import get_latest, save_snapshot
from app.services.kaipoke.plan_actual_compare import VISIT_BUSINESS_TYPES, norm_time
from app.services.kaipoke.plan_actual_fetch import _export_one
from app.services.kaipoke.sources import (
    OP_MONTHLY_ACTUAL_APPLY,
    OP_MONTHLY_ACTUAL_REFRESH,
    RpaJobConflictError,
    SourceJobCancelledError,
)
from app.services.kaipoke_client import KaipokeApiError
from app.services.monthly_actual import judge as rules

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot
    from app.services.kaipoke_client import KaipokeClient

logger = logging.getLogger(__name__)

JST: Final = ZoneInfo("Asia/Tokyo")
#: 取り直した実績のスナップショットの ``source_op``。
SNAPSHOT_SOURCE_OP: Final = "monthly-actual"
#: RPA の結果を読み直す間隔 (秒)。試験で小さくする。
APPLY_POLL_SECONDS: float = 30.0
#: 反映のジョブの締め切り (分)。連携の自動クローズ (360 分) より手前。
APPLY_DEADLINE_MINUTES: Final = 330
#: 背景の読み直しがこの分より長く止まっていたら、状態の API がジョブを決着させてよい。
STALE_POLLER_MINUTES: Final = 5
#: 続けて ``no_result`` が返ったら結果が失われたとみなす回数。
MAX_NO_RESULT: Final = 3
#: RPA の呼び出しの時間切れ (秒)。受け付けたらすぐ 202 が返る。
_START_TIMEOUT: Final = 60.0
#: 予約の既定の時刻 (JST)。夜はカイポケの事務所のログインとぶつからない (§5-3)。
DEFAULT_SCHEDULE_TIME: Final = time(22, 0)
#: 予約の時刻からこれより遅れても始められなければ、予約を取り下げる。
SCHEDULE_GIVE_UP: Final = timedelta(hours=3)

#: 通知の種類と冪等キー。
NOTIFY_TYPE: Final = "monthly_actual_applied"
NOTIFY_REFERENCE: Final = "monthly_actual_apply"

#: RPA の details[].status。
RPA_SUCCESS: Final = "success"
RPA_DRY_RUN_OK: Final = "dry_run_ok"
RPA_SKIPPED: Final = "skipped"
RPA_FAILED: Final = "failed"
RPA_ERROR: Final = "error"
#: 送り待ち (らく助だけの状態)。
APPLY_QUEUED: Final = "queued"
#: RPA を始めた後に止まり、書き換えたか分からない (らく助だけの状態・カイポケで要目視)。
APPLY_UNKNOWN: Final = "unknown"
#: 成功と同じに扱う理由 (再実行で二重に触らない)。
REASON_ALREADY_APPLIED: Final = "already_applied"
#: 書き換えたか分からない結果 (カイポケで確かめてから。流し直しの対象にしない)。
CHECK_STATUSES: Final = frozenset({RPA_ERROR, APPLY_UNKNOWN})
CHECK_REASONS: Final = frozenset({"verify_failed", "dialog_left_open", "no_detail"})
#: 記録書Ⅱで書き換えたか分からない理由 (保存済みかも・編集画面が開いたまま)。
CARE_CHECK_REASONS: Final = frozenset({"verify_failed", "edit_screen_left_open", "no_detail"})
#: RPA の 2 つの口 (同時には走れない・実績 → 記録書Ⅱだけ の順に流す)。
PHASE_ACTUAL: Final = "actual"
PHASE_CARE_RECORD: Final = "care_record"
#: 段階ごとの ``result_summary`` の印 (始めた・RPA が終わった)。
_PHASE_KEYS: Final = {
    PHASE_ACTUAL: ("rpa_started", "rpa_completed"),
    PHASE_CARE_RECORD: ("k2_started", "k2_completed"),
}
#: 本番が試しを通っていないときの理由 (画面にそのまま出す)。
MSG_NEED_DRY_RUN: Final = (
    "試しを通っていない行があります。先に「試しに流す」を押して、試しに流してから反映してください"
)

_ACTIVE: Final = ("pending", "running")
_RUNNING: Final = "running"


class MonthlyActualError(RuntimeError):
    """API がそのまま返す失敗 (状態コードと日本語の理由)。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- 月 -------------------------------------------------------------------------------


def month_bounds(month: str) -> tuple[date, date]:
    """``YYYY-MM`` → (1 日, 翌月 1 日)。"""
    year, mon = (int(x) for x in month.split("-"))
    first = date(year, mon, 1)
    nxt = date(year + (mon == 12), mon % 12 + 1, 1)
    return first, nxt


def month_monday(month: str) -> date:
    """ジョブの ``week_start`` (その月の 1 日を含む週の月曜)。"""
    first, _ = month_bounds(month)
    return first - timedelta(days=first.weekday())


def is_past_month(month: str, now: datetime | None = None) -> bool:
    """今月 (JST) より前の月か。反映は過去の月だけ (予定とは逆の止め・§5)。"""
    today = (now or datetime.now(UTC)).astimezone(JST).date()
    _, nxt = month_bounds(month)
    return nxt <= today.replace(day=1)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware is not None else None


def _hm(value: datetime) -> str:
    """実績の時刻 → JST の ``HH:MM`` (秒は切り捨て・カイポケと同じ)。"""
    return as_utc(value).astimezone(JST).strftime("%H:%M")


def _short(message: object, limit: int = 300) -> str:
    return str(message)[:limit]


# --- 読み出し -------------------------------------------------------------------------


async def load_rk_visits(db: AsyncSession, month: str) -> list[rules.RkVisit]:
    """その月のらく助の訪問 (取消・削除・未訪問を除く) と QR の実績。"""
    first, nxt = month_bounds(month)
    visits = (
        await db.scalars(
            select(Visit).where(
                Visit.deleted_at.is_(None),
                Visit.visit_date >= first,
                Visit.visit_date < nxt,
                Visit.status != "cancelled",
            )
        )
    ).all()
    if not visits:
        return []
    staff_names = {s.id: s.name for s in (await db.scalars(select(Staff))).all()}
    patient_ids = {v.patient_id for v in visits}
    patients = {
        p.id: p
        for p in (await db.scalars(select(Patient).where(Patient.id.in_(patient_ids)))).all()
    }
    actuals = await load_actuals(db, [v.id for v in visits])
    out: list[rules.RkVisit] = []
    for v in visits:
        a = actuals.get(v.id)
        if a is not None and a.no_show is not None and a.arrival is None:
            continue  # 未訪問 (行かなかった) はカイポケの実績の対象ではない
        patient = patients.get(v.patient_id)
        staff = tuple(
            staff_names[sid]
            for sid in (v.primary_staff_id, v.secondary_staff_id, v.mentor_staff_id)
            if sid is not None and staff_names.get(sid)
        )
        out.append(
            rules.RkVisit(
                visit_id=str(v.id),
                date=v.visit_date,
                patient=patient.name if patient else "",
                patient_code=patient.code if patient else "",
                staff=tuple(dict.fromkeys(staff)),
                plan_start=v.start_time.strftime("%H:%M") if v.start_time else None,
                plan_end=v.end_time.strftime("%H:%M") if v.end_time else None,
                arrival=_hm(a.arrival.at) if a and a.arrival else None,
                departure=_hm(a.departure.at) if a and a.departure else None,
                arrival_adjusted=bool(a and a.arrival and a.arrival.adjusted),
                departure_adjusted=bool(a and a.departure and a.departure.adjusted),
            )
        )
    return out


def parse_kp_csv(csv_text: str, month: str) -> list[rules.KpRow]:
    """カイポケの実績 CSV → ``KpRow`` (訪問の行だけ・日付が読めない行は捨てる)。"""
    first, _ = month_bounds(month)
    reader = csv.DictReader(io.StringIO(csv_text or ""))
    out: list[rules.KpRow] = []
    for raw in reader:
        row = {(k or "").strip().lstrip("﻿"): (v or "").strip() for k, v in raw.items()}
        day = row.get("日付", "")
        if not day.isdigit():
            continue
        business = row.get("業務種別", "")
        if business and business not in VISIT_BUSINESS_TYPES:
            continue
        try:
            visit_date = first.replace(day=int(day))
        except ValueError:
            continue
        minutes = row.get("提供時間（分）", "")
        out.append(
            rules.KpRow(
                date=visit_date,
                patient=row.get("利用者", "").replace("　", " ").strip(),
                staff=rules.kp_staff_tuple(
                    row.get(c, "").replace("　", " ") for c in ("職員名１", "職員名２", "職員名３")
                ),
                start=norm_time(row.get("開始時間", "")),
                end=norm_time(row.get("終了時間", "")),
                minutes=int(minutes) if minutes.isdigit() else None,
                k2_start=norm_time(row.get("記録Ⅱ開始時間", "")) or None,
                k2_end=norm_time(row.get("記録Ⅱ終了時間", "")) or None,
                service=row.get("サービス内容", ""),
            )
        )
    return out


async def latest_actual_snapshot(db: AsyncSession, month: str) -> KaipokeCsvSnapshot | None:
    """その月のカイポケの実績の最新 (月まるごと)。"""
    return await get_latest(db, month=month, division="actual", month_only=True)


async def get_run(db: AsyncSession, month: str) -> MonthlyActualRun | None:
    return await db.scalar(
        select(MonthlyActualRun)
        .where(MonthlyActualRun.month == month)
        .execution_options(populate_existing=True)
    )


async def load_rows(db: AsyncSession, run_id: uuid.UUID) -> list[MonthlyActualRow]:
    return list(
        (
            await db.scalars(
                select(MonthlyActualRow)
                .where(MonthlyActualRow.run_id == run_id)
                .order_by(MonthlyActualRow.seq)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def active_job(db: AsyncSession, job_id: uuid.UUID | None) -> KaipokeJob | None:
    """``job_id`` のジョブが動いていれば返す。"""
    if job_id is None:
        return None
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    return job if job is not None and job.status in _ACTIVE else None


# --- 判定の保存 -----------------------------------------------------------------------


def _selection_key(visit_id: uuid.UUID | str | None, item_key: str | None) -> str | None:
    """選び直しを引き継ぐ鍵 = 訪問＋カイポケの行の鍵 (別のカイポケの行へ引き継がない)。"""
    if visit_id is None or not item_key:
        return None
    return f"v:{visit_id}|k:{item_key}"


def needs_actual(row: MonthlyActualRow) -> bool:
    """カイポケの実績の時刻が新しい時刻と違う (実績を書き換える)。"""
    return bool(row.new_start and row.new_end) and (
        norm_time(row.kp_start or ""),
        norm_time(row.kp_end or ""),
    ) != (row.new_start, row.new_end)


def needs_care_record(row: MonthlyActualRow) -> bool:
    """記録書Ⅱがあり、時刻が新しい時刻と違う (記録書Ⅱもそろえる)。記録書Ⅱが無い行は扱わない。"""
    return bool(row.k2_start and row.k2_end and row.new_start and row.new_end) and (
        norm_time(row.k2_start or ""),
        norm_time(row.k2_end or ""),
    ) != (row.new_start, row.new_end)


def dry_run_sig(row: MonthlyActualRow) -> str:
    """試しを通ったかを見る印 = 送る鍵と新しい時刻 (記録書Ⅱもそろえる行は記録書Ⅱの旧の時刻も)。"""
    sig = f"{row.item_key}|{row.new_start}-{row.new_end}"
    if needs_care_record(row):
        sig += f"|k2:{row.k2_start}-{row.k2_end}"
    return sig


def _actual_needs_check(row: MonthlyActualRow) -> bool:
    return row.apply_status in CHECK_STATUSES or row.apply_reason in CHECK_REASONS


def care_record_needs_check(row: MonthlyActualRow) -> bool:
    """記録書Ⅱを書き換えたか分からない結果 (カイポケで確かめてから)。"""
    return row.care_record_status in CHECK_STATUSES or row.care_record_reason in CARE_CHECK_REASONS


def needs_kaipoke_check(row: MonthlyActualRow) -> bool:
    """書き換えたか分からない結果 (実績か記録書Ⅱ・カイポケで確かめてから)。"""
    return _actual_needs_check(row) or care_record_needs_check(row)


def _live_check(row: MonthlyActualRow) -> bool:
    """本番で「カイポケで確かめてから」になった (流し直さない)。"""
    return (row.apply_dry_run is False and _actual_needs_check(row)) or (
        row.care_record_dry_run is False and care_record_needs_check(row)
    )


def _row_from_judged(run_id: uuid.UUID, seq: int, j: rules.JudgedRow) -> MonthlyActualRow:
    rk, kp = j.rk, j.kp
    return MonthlyActualRow(
        id=uuid.uuid4(),
        run_id=run_id,
        seq=seq,
        status=j.status,
        flags=[f.as_dict() for f in j.flags],
        item_key=j.item_key,
        visit_date=j.date,
        patient=(j.patient or "")[:100],
        visit_id=uuid.UUID(rk.visit_id) if rk else None,
        patient_code=rk.patient_code if rk else None,
        rk_staff="・".join(rk.staff)[:200] if rk else None,
        rk_arrival=rk.arrival if rk else None,
        rk_departure=rk.departure if rk else None,
        rk_adjusted=j.rk_adjusted or None,
        rk_stay=j.rk_stay,
        new_start=j.new_start,
        new_end=j.new_end,
        kp_patient=kp.patient[:100] if kp else None,
        kp_start=kp.start if kp else None,
        kp_end=kp.end if kp else None,
        kp_staff1=kp.staff1[:100] if kp else None,
        kp_staff="・".join(kp.staff)[:200] if kp else None,
        kp_service=kp.service[:100] if kp else None,
        kp_minutes=kp.minutes if kp else None,
        k2_start=kp.k2_start if kp else None,
        k2_end=kp.k2_end if kp else None,
        selected=j.default_selected,
    )


def _checked_since(snap_at: datetime | None, done_at: datetime | None, keep_checks: bool) -> bool:
    """結果の後に管理者がカイポケの実績を読み取り直した (読み取り直しが確かめになる)。"""
    at = _aware(done_at)
    return not keep_checks and snap_at is not None and at is not None and snap_at > at


async def judge_month(
    db: AsyncSession,
    month: str,
    *,
    snapshot: KaipokeCsvSnapshot | None = None,
    keep_checks: bool = False,
) -> MonthlyActualRun:
    """らく助とカイポケの実績を突き合わせて判定し、作業の行を作り直す (commit は呼び出し側)。

    管理者が変えた「反映する／しない」(``selected_by`` あり) は、同じ訪問かつ同じ鍵の行へ
    引き継ぐ (前は人が見るでなく、今回「人が見る」になった行は引き継がない)。送った結果と試しを通った印は同じ鍵 (カイポケの旧時刻が同じ) の行へ引き継ぐ。
    ただし「カイポケで確かめてから」の結果は、その後に管理者がカイポケの実績を読み取り直して
    いれば引き継がない (読み取り直しが確かめになる)。反映の直後の確かめ (``keep_checks``・
    その読み取りの実績) では引き継ぐ。カイポケの実績がまだ無ければ 422。
    """
    snap = snapshot or await latest_actual_snapshot(db, month)
    if snap is None:
        raise MonthlyActualError(
            422,
            "カイポケの実績をまだ読み取っていません。先に「カイポケの実績を読み取る」を押してください",
        )
    rk = await load_rk_visits(db, month)
    kp = parse_kp_csv(snap.csv_text, month)
    judged = rules.judge(rk, kp)

    run = await get_run(db, month)
    previous: list[MonthlyActualRow] = []
    if run is None:
        run = MonthlyActualRun(id=uuid.uuid4(), month=month, state="judged")
        db.add(run)
        await db.flush()
    else:
        previous = await load_rows(db, run.id)
    chosen = {
        key: r
        for r in previous
        if r.selected_by is not None and (key := _selection_key(r.visit_id, r.item_key)) is not None
    }
    applied = {
        r.item_key: r for r in previous if r.item_key and (r.apply_status or r.care_record_status)
    }
    passed = {r.item_key: r.dry_run_ok_sig for r in previous if r.item_key and r.dry_run_ok_sig}
    snap_at = _aware(snap.fetched_at)
    keep_checks = keep_checks or (
        str(snap.id) == str((run.verify_result or {}).get("snapshot_id") or "")
    )
    await db.execute(delete(MonthlyActualRow).where(MonthlyActualRow.run_id == run.id))

    rows: list[MonthlyActualRow] = []
    for seq, j in enumerate(judged):
        row = _row_from_judged(run.id, seq, j)
        prev = chosen.get(_selection_key(row.visit_id, row.item_key) or "")
        # 今回「人が見る」になった行は、前の選択を引き継がず既定の「しない」に戻す。
        if (
            prev is not None
            and row.status in rules.SELECTABLE_STATUSES
            and not (row.status == rules.STATUS_REVIEW and prev.status != rules.STATUS_REVIEW)
        ):
            row.selected = prev.selected
            row.selected_by = prev.selected_by
            row.selected_at = prev.selected_at
        row.dry_run_ok_sig = passed.get(row.item_key or "")
        done = applied.get(row.item_key or "")
        if (
            done is not None
            and done.apply_status not in (None, APPLY_QUEUED)
            and not (
                _actual_needs_check(done) and _checked_since(snap_at, done.applied_at, keep_checks)
            )
        ):
            row.apply_status = done.apply_status
            row.apply_reason = done.apply_reason
            row.apply_message = done.apply_message
            row.apply_dry_run = done.apply_dry_run
            row.applied_at = done.applied_at
        if (
            done is not None
            and done.care_record_status not in (None, APPLY_QUEUED)
            and not (
                care_record_needs_check(done)
                and _checked_since(snap_at, done.care_record_at, keep_checks)
            )
        ):
            row.care_record_status = done.care_record_status
            row.care_record_reason = done.care_record_reason
            row.care_record_message = done.care_record_message
            row.care_record_dry_run = done.care_record_dry_run
            row.care_record_at = done.care_record_at
        rows.append(row)
    db.add_all(rows)

    run.snapshot_id = snap.id
    run.kaipoke_fetched_at = snap.fetched_at
    run.judged_at = datetime.now(UTC)
    run.counts = {**rules.count_statuses(judged), "care_record": rules.count_k2(judged)}
    if run.state in ("failed", "applying"):
        run.state = "judged"
    await db.flush()
    return run


# --- 画面へ出す形 ---------------------------------------------------------------------


def is_sendable(row: MonthlyActualRow) -> bool:
    """RPA へ送れる行か (反映する／人が見る・カイポケの行と新しい時刻がある)。"""
    return bool(
        row.status in rules.SELECTABLE_STATUSES
        and row.item_key
        and row.kp_start
        and row.kp_staff1
        and row.new_start
        and row.new_end
    )


def is_applied(row: MonthlyActualRow) -> bool:
    """書き換え済み (本番の成功・すでに反映済み)。二度送らない。

    ``already_applied`` はカイポケがもう新しい時刻なので、試しで返っても済みとする。
    """
    if row.apply_reason == REASON_ALREADY_APPLIED:
        return True
    return row.apply_dry_run is False and row.apply_status == RPA_SUCCESS


def is_care_record_applied(row: MonthlyActualRow) -> bool:
    """記録書Ⅱをそろえ済み (本番の成功・すでにそろっていた)。"""
    if row.care_record_reason == REASON_ALREADY_APPLIED:
        return True
    return row.care_record_dry_run is False and row.care_record_status == RPA_SUCCESS


def send_actual(row: MonthlyActualRow) -> bool:
    """実績の口へ送る (実績の時刻が違い、まだ書き換えていない)。"""
    return needs_actual(row) and not is_applied(row)


def send_care_record(row: MonthlyActualRow) -> bool:
    """記録書Ⅱをそろえる (記録書Ⅱの時刻が違い、まだそろえていない)。"""
    return needs_care_record(row) and not is_care_record_applied(row)


def row_view(row: MonthlyActualRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "seq": row.seq,
        "status": row.status,
        "status_label": rules.STATUS_LABELS.get(row.status, row.status),
        "flags": list(row.flags or []),
        "item_key": row.item_key,
        "date": row.visit_date.isoformat(),
        "patient": row.patient,
        "patient_code": row.patient_code,
        "visit_id": str(row.visit_id) if row.visit_id else None,
        "rk_staff": row.rk_staff,
        "rk_arrival": row.rk_arrival,
        "rk_departure": row.rk_departure,
        "rk_adjusted": row.rk_adjusted,
        "rk_stay": row.rk_stay,
        "new_start": row.new_start,
        "new_end": row.new_end,
        "kp_patient": row.kp_patient,
        "kp_start": row.kp_start,
        "kp_end": row.kp_end,
        "kp_staff1": row.kp_staff1,
        "kp_staff": row.kp_staff,
        "kp_service": row.kp_service,
        "kp_minutes": row.kp_minutes,
        "k2_start": row.k2_start,
        "k2_end": row.k2_end,
        "needs_actual": needs_actual(row),
        "needs_care_record": needs_care_record(row),
        "care_record_status": row.care_record_status,
        "care_record_reason": row.care_record_reason,
        "care_record_message": row.care_record_message,
        "care_record_dry_run": row.care_record_dry_run,
        "care_record_at": _iso(row.care_record_at),
        "diff_start": _diff(row.new_start, row.kp_start),
        "diff_end": _diff(row.new_end, row.kp_end),
        "selectable": is_sendable(row),
        "selected": row.selected,
        "selected_by_user": row.selected_by is not None,
        "apply_status": row.apply_status,
        "apply_reason": row.apply_reason,
        "apply_message": row.apply_message,
        "apply_dry_run": row.apply_dry_run,
        "applied_at": _iso(row.applied_at),
        "needs_kaipoke_check": needs_kaipoke_check(row),
        "dry_run_passed": row.dry_run_ok_sig is not None and row.dry_run_ok_sig == dry_run_sig(row),
    }


def _diff(rk: str | None, kp: str | None) -> int | None:
    a, b = rules.to_minutes(rk), rules.to_minutes(kp)
    return a - b if a is not None and b is not None else None


def job_view(job: KaipokeJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    summary = job.result_summary or {}
    return {
        "id": str(job.id),
        "op": (job.params or {}).get("op"),
        "status": job.status,
        "dry_run": (job.params or {}).get("dry_run"),
        "total": summary.get("total"),
        "progress": summary.get("progress"),
        "counts": summary.get("counts"),
        "care_record_counts": summary.get("care_record_counts"),
        #: いま RPA で動いている口 (実績 → 記録書Ⅱだけ)。
        "phase": (
            PHASE_CARE_RECORD
            if summary.get("k2_started")
            else PHASE_ACTUAL
            if summary.get("rpa_started")
            else None
        ),
        "error": summary.get("error"),
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
        "updated_at": _iso(job.updated_at),
    }


def apply_counts(rows: list[MonthlyActualRow]) -> dict[str, int]:
    """送った行の結果の件数 (``already_applied`` は成功に数える)。"""
    counts = {
        "success": 0,
        "dry_run_ok": 0,
        "skipped": 0,
        "failed": 0,
        "error": 0,
        "queued": 0,
        "unknown": 0,
    }
    for row in rows:
        if row.apply_status is None:
            continue
        if row.apply_reason == REASON_ALREADY_APPLIED or row.apply_status == RPA_SUCCESS:
            counts["success"] += 1
        elif row.apply_status in counts:
            counts[row.apply_status] += 1
    return counts


def care_record_counts(rows: list[MonthlyActualRow]) -> dict[str, int]:
    """記録書Ⅱの結果の件数 (``already_applied`` は成功に数える)。"""
    counts = dict.fromkeys(
        ("success", "dry_run_ok", "skipped", "failed", "error", "queued", "unknown"), 0
    )
    for row in rows:
        status = row.care_record_status
        if status is None:
            continue
        if row.care_record_reason == REASON_ALREADY_APPLIED or status == RPA_SUCCESS:
            counts["success"] += 1
        elif status in counts:
            counts[status] += 1
    return counts


def job_row_ids(job: KaipokeJob | None) -> tuple[set[str], set[str], set[str]]:
    """ジョブの行の分け方 = (実績の口へ送る行, 記録書Ⅱもそろえる行, 記録書Ⅱだけの口へ送る行)。

    記録書Ⅱもそろえる行は、実績と続けての行と記録書Ⅱだけの行の両方を含む。記録書Ⅱを
    扱う前のジョブ (分け方を持たない) は、全部を実績の口へ送った扱い。
    """
    params = (job.params or {}) if job is not None else {}
    actual = params.get("actual_row_ids")
    if actual is None:
        actual = params.get("row_ids") or []
    return (
        {str(i) for i in actual},
        {str(i) for i in params.get("care_record_row_ids") or []},
        {str(i) for i in params.get("k2_row_ids") or []},
    )


def job_counts(
    job: KaipokeJob | None, rows: list[MonthlyActualRow]
) -> tuple[dict[str, int], dict[str, int]]:
    """ジョブの行の結果の件数 = (実績の口へ送った行, 記録書Ⅱもそろえた行)。"""
    actual_ids, care_ids, _ = job_row_ids(job)
    return (
        apply_counts([r for r in rows if str(r.id) in actual_ids]),
        care_record_counts([r for r in rows if str(r.id) in care_ids]),
    )


async def run_view(db: AsyncSession, run: MonthlyActualRun) -> dict[str, Any]:
    refresh_job = await db.get(KaipokeJob, run.refresh_job_id) if run.refresh_job_id else None
    apply_job = await db.get(KaipokeJob, run.apply_job_id) if run.apply_job_id else None
    return {
        "id": str(run.id),
        "month": run.month,
        "state": run.state,
        "snapshot_id": str(run.snapshot_id) if run.snapshot_id else None,
        "kaipoke_fetched_at": _iso(run.kaipoke_fetched_at),
        "judged_at": _iso(run.judged_at),
        "counts": run.counts or rules.count_statuses([]),
        "last_apply": run.last_apply,
        "verify_result": run.verify_result,
        "scheduled_at": _iso(run.scheduled_at),
        "scheduled_dry_run": run.scheduled_dry_run,
        "refresh_job": job_view(refresh_job),
        "apply_job": job_view(apply_job),
    }


# --- 反映する／しない -----------------------------------------------------------------


async def set_selected(
    db: AsyncSession,
    *,
    month: str | None,
    row_id: uuid.UUID,
    selected: bool,
    user_id: uuid.UUID,
) -> MonthlyActualRow:
    """1 行の「反映する／しない」を変える (commit は呼び出し側)。

    ``month`` を渡したら、その月の行でなければ 404。None なら行の作業から月を決める。
    """
    row = await db.get(MonthlyActualRow, row_id, populate_existing=True)
    run = (
        await db.get(MonthlyActualRun, row.run_id, populate_existing=True)
        if row is not None
        else None
    )
    if run is None or row is None or (month is not None and run.month != month):
        raise MonthlyActualError(404, "行が見つかりません")
    if await active_job(db, run.apply_job_id) is not None:
        raise MonthlyActualError(409, "反映の途中です。終わってから選び直してください")
    if not is_sendable(row):
        raise MonthlyActualError(
            422, "この行は反映できません（カイポケの行か QR の時刻がありません）"
        )
    row.selected = selected
    row.selected_by = user_id
    row.selected_at = datetime.now(UTC)
    await db.flush()
    return row


# --- 反映 (RPA) -----------------------------------------------------------------------


def choose_rows(
    rows: list[MonthlyActualRow],
    *,
    row_ids: list[uuid.UUID] | None = None,
    only_failed: bool = False,
) -> list[MonthlyActualRow]:
    """送る行 = 選んだ (反映する) 行のうち、送れて、まだ書き換えていないもの。

    ``row_ids`` を渡したらその行だけ (選んでいない行・送れない行は 422)。
    ``only_failed`` なら前に失敗した行だけ (流し直し)。本番で「カイポケで確かめてから」に
    なった行は送らない (書き換えたか分からないため・読み取り直すと戻る)。
    """
    if row_ids is not None:
        wanted = set(row_ids)
        picked = [r for r in rows if r.id in wanted]
        if len(picked) != len(wanted):
            raise MonthlyActualError(
                404, "選んだ行の一部が見つかりません（判定し直された可能性があります）"
            )
        bad = [r for r in picked if not (r.selected and is_sendable(r))]
        if bad:
            raise MonthlyActualError(422, f"反映しない行・反映できない行が {len(bad)} 件あります")
    else:
        picked = [r for r in rows if r.selected and is_sendable(r)]
    picked = [r for r in picked if (send_actual(r) or send_care_record(r)) and not _live_check(r)]
    if only_failed:
        picked = [
            r
            for r in picked
            if RPA_FAILED in (r.apply_status, r.care_record_status) and not needs_kaipoke_check(r)
        ]
    return picked


def ensure_dry_run_passed(rows: list[MonthlyActualRow]) -> None:
    """本番は試しを通った行だけ: 送る行すべてに、今の鍵と新しい時刻で ``dry_run_ok`` がある。"""
    bad = [r for r in rows if r.dry_run_ok_sig is None or r.dry_run_ok_sig != dry_run_sig(r)]
    if bad:
        raise MonthlyActualError(422, f"{MSG_NEED_DRY_RUN}（{len(bad)} 件）")


def duplicate_keys(rows: list[MonthlyActualRow]) -> list[str]:
    """同じカイポケの行 (鍵) を 2 件以上が指していれば、その鍵 (11/1 の事故の止め)。"""
    seen: dict[str, int] = {}
    for r in rows:
        if r.item_key:
            seen[r.item_key] = seen.get(r.item_key, 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


def _ensure_no_duplicates(rows: list[MonthlyActualRow]) -> None:
    dup = duplicate_keys(rows)
    if dup:
        raise MonthlyActualError(422, f"同じカイポケの行を 2 件が指しています（{dup[0]}）")


def build_rpa_items(
    rows: list[MonthlyActualRow], *, care_record_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """実績の口の ``items[]`` (ACTUAL_TIME_EDIT_SPEC.md)。``item_id`` は行の id。

    記録書Ⅱもそろえる行 (``care_record_ids``・省略なら行の状態から決める) には
    ``care_record: {old_start, old_end}`` (いまの記録書Ⅱの時刻) を付ける。
    """
    _ensure_no_duplicates(rows)
    items: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {
            "item_id": str(r.id),
            "user_name": r.kp_patient or r.patient,
            "date": r.visit_date.isoformat(),
            "old_start": r.kp_start,
            "old_end": r.kp_end,
            "staff1": r.kp_staff1,
            "new_start": r.new_start,
            "new_end": r.new_end,
        }
        with_care = send_care_record(r) if care_record_ids is None else str(r.id) in care_record_ids
        if with_care:
            item["care_record"] = {"old_start": r.k2_start, "old_end": r.k2_end}
        items.append(item)
    return items


def build_care_record_items(rows: list[MonthlyActualRow]) -> list[dict[str, Any]]:
    """記録書Ⅱだけの口の ``items[]``。

    ``actual_*`` はいまのカイポケの実績 (行の鍵)。この口へ送る行は、実績がもう新しい時刻
    (一致していたか、前に書き換えた) なので新しい時刻を使う。``old_*`` はいまの記録書Ⅱ。
    """
    _ensure_no_duplicates(rows)
    return [
        {
            "item_id": str(r.id),
            "date": r.visit_date.isoformat(),
            "patient": r.kp_patient or r.patient,
            "staff1": r.kp_staff1,
            "actual_start": r.new_start,
            "actual_end": r.new_end,
            "old_start": r.k2_start,
            "old_end": r.k2_end,
            "new_start": r.new_start,
            "new_end": r.new_end,
        }
        for r in rows
    ]


def rpa_job_id(job_id: uuid.UUID, phase: str = PHASE_ACTUAL) -> str:
    if phase == PHASE_CARE_RECORD:
        return f"careflow-k2-{job_id}"
    return f"careflow-actual-{job_id}"


async def start_apply(
    db: AsyncSession,
    *,
    month: str,
    user_id: uuid.UUID | None,
    dry_run: bool,
    row_ids: list[uuid.UUID] | None = None,
    only_failed: bool = False,
) -> KaipokeJob:
    """送る行を決めて反映のジョブを立てる (commit する)。実体は ``run_apply_job``。

    過去の月でない・判定がまだ・反映の途中・送る行が無い・鍵が重なる → ``MonthlyActualError``。
    ほかの RPA のジョブが動いていれば ``RpaJobConflictError`` (409)。
    """
    if not is_past_month(month):
        raise MonthlyActualError(422, "カイポケへ反映できるのは、終わった月だけです")
    run = await get_run(db, month)
    if run is None:
        raise MonthlyActualError(
            422, "まだ判定していません。先にカイポケの実績を読み取ってください"
        )
    if await active_job(db, run.apply_job_id) is not None:
        raise MonthlyActualError(409, "反映がすでに動いています（二重には送りません）")
    rows = choose_rows(await load_rows(db, run.id), row_ids=row_ids, only_failed=only_failed)
    if not rows:
        raise MonthlyActualError(422, "反映する行がありません")
    _ensure_no_duplicates(rows)  # 鍵の重なりをここで止める
    if not dry_run:
        ensure_dry_run_passed(rows)
    # 実績も書き換える行は実績の口へ (記録書Ⅱも違えば続けてそろえる)。実績はもう新しい時刻で
    # 記録書Ⅱだけ違う行は記録書Ⅱだけの口へ。
    actual_ids = [r.id for r in rows if send_actual(r)]
    care_ids = [r.id for r in rows if send_care_record(r)]
    k2_ids = [r.id for r in rows if not send_actual(r) and send_care_record(r)]
    ids = [r.id for r in rows]
    run_id = run.id
    params: dict[str, Any] = {
        "month": month,
        "run_id": str(run_id),
        "dry_run": dry_run,
        "row_ids": [str(i) for i in ids],
        "actual_row_ids": [str(i) for i in actual_ids],
        "care_record_row_ids": [str(i) for i in care_ids],
        "k2_row_ids": [str(i) for i in k2_ids],
    }
    if not dry_run:
        # 送った記録 (判定し直しで行が作り直されても消えない)。結果は result_summary.sent_results。
        params["sent_by"] = str(user_id) if user_id else None
        params["sent_at"] = datetime.now(UTC).isoformat()
        params["sent"] = [
            {
                "row_id": str(r.id),
                "visit_id": str(r.visit_id) if r.visit_id else None,
                "patient": r.kp_patient or r.patient,
                "date": r.visit_date.isoformat(),
                "old_start": r.kp_start,
                "old_end": r.kp_end,
                "staff1": r.kp_staff1,
                "new_start": r.new_start,
                "new_end": r.new_end,
                "actual": r.id in actual_ids,
                "care_record": (
                    {"old_start": r.k2_start, "old_end": r.k2_end} if r.id in care_ids else None
                ),
            }
            for r in rows
        ]
    await db.commit()  # 読んだだけのトランザクションを閉じてから鍵を取る

    job = await sources.start_source_job(
        db,
        op=OP_MONTHLY_ACTUAL_APPLY,
        week_start=month_monday(month),
        user_id=user_id,
        params=params,
        total=len(ids),
    )
    run = await get_run(db, month)
    assert run is not None
    run.apply_job_id = job.id
    run.state = "applying"
    if actual_ids:
        await db.execute(
            update(MonthlyActualRow)
            .where(MonthlyActualRow.id.in_(actual_ids))
            .values(
                apply_status=APPLY_QUEUED,
                apply_reason=None,
                apply_message=None,
                apply_dry_run=dry_run,
                applied_at=None,
            )
            .execution_options(synchronize_session=False)
        )
    if care_ids:
        await db.execute(
            update(MonthlyActualRow)
            .where(MonthlyActualRow.id.in_(care_ids))
            .values(
                care_record_status=APPLY_QUEUED,
                care_record_reason=None,
                care_record_message=None,
                care_record_dry_run=dry_run,
                care_record_at=None,
            )
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    return job


async def _touch(db: AsyncSession, job_id: uuid.UUID, **summary: Any) -> bool:
    """ジョブの ``updated_at`` (と ``result_summary`` の一部) を書き戻す。動いていなければ False。"""
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or job.status != _RUNNING:
        await db.rollback()
        return False
    values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
    if summary:
        values["result_summary"] = {**(job.result_summary or {}), **summary}
    written = await db.execute(
        update(KaipokeJob)
        .where(KaipokeJob.id == job_id, KaipokeJob.status == _RUNNING)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return bool(getattr(written, "rowcount", 0))


def _detail_message(detail: dict[str, Any]) -> str | None:
    parts: list[str] = []
    if detail.get("message"):
        parts.append(str(detail["message"]))
    for key in ("messages", "observed", "fields", "back_messages"):
        value = detail.get(key)
        if isinstance(value, list) and value:
            parts.append(f"{key}: " + " / ".join(str(v) for v in value))
    for key in ("observed_old", "observed_record", "observed_after", "screen", "back_reason"):
        if detail.get(key):
            parts.append(f"{key}: {detail[key]}")
    return _short("\n".join(parts), 2000) if parts else None


def _care_record_message(detail: dict[str, Any]) -> str | None:
    """実績と続けての結果の記録書Ⅱの補足 (``care_record_detail`` は dict か文字)。"""
    extra = detail.get("care_record_detail")
    if isinstance(extra, dict):
        return _detail_message(extra)
    return _short(extra, 2000) if extra else None


def _care_record_ok(row: MonthlyActualRow) -> bool:
    """試しで記録書Ⅱが通った (登録の直前まで・すでにそろっていた)。"""
    return (
        row.care_record_status == RPA_DRY_RUN_OK or row.care_record_reason == REASON_ALREADY_APPLIED
    )


async def ingest_rpa_result(
    db: AsyncSession, job: KaipokeJob, rpa: dict[str, Any], *, phase: str = PHASE_ACTUAL
) -> str:
    """RPA の結果を 1 回読み込む (冪等)。``running`` / ``completed`` / ``no_result`` を返す。

    ``phase`` は実績の口 (``PHASE_ACTUAL``) か記録書Ⅱだけの口 (``PHASE_CARE_RECORD``)。
    完了なら行ごとに、実績の口は ``apply_*`` と (続けてそろえた行は) ``care_record_*`` を、
    記録書Ⅱだけの口は ``care_record_*`` を書く。別のジョブの結果・エラーは ``RuntimeError``
    (理由つき)。commit する。
    """
    care_phase = phase == PHASE_CARE_RECORD
    ours = rpa_job_id(job.id, phase)
    status = str(rpa.get("status") or "")
    theirs = str(rpa.get("job_id") or "")
    if status == "no_result":
        return "no_result"
    if status == "running":
        if theirs and theirs != ours:
            raise RuntimeError("カイポケ側で別の処理が動いていて、結果を受け取れませんでした")
        await _touch(db, job.id, progress=rpa.get("progress"))
        return "running"
    if theirs != ours:
        raise RuntimeError("カイポケ側で結果が失われました（カイポケで確かめてください）")
    if status == "error":
        fallback = (
            "カイポケの記録書Ⅱのそろえに失敗しました"
            if care_phase
            else "カイポケの実績の書き換えに失敗しました"
        )
        raise RuntimeError(str(rpa.get("error") or fallback))
    if status != "completed":
        raise RuntimeError(f"カイポケが不明な状態を返しました: {status}")
    result = rpa.get("result") or {}
    details = result.get("details")
    if not isinstance(details, list):
        raise RuntimeError("カイポケの結果の形が不正です")
    dry_run = bool((job.params or {}).get("dry_run"))
    now = datetime.now(UTC)
    by_id: dict[uuid.UUID, dict[str, Any]] = {}
    for d in details:
        if not isinstance(d, dict):
            continue
        try:
            by_id[uuid.UUID(str(d.get("item_id")))] = d
        except ValueError:
            continue
    actual_ids, care_ids, k2_ids = job_row_ids(job)
    phase_ids = k2_ids if care_phase else actual_ids
    rows = (
        await db.scalars(
            select(MonthlyActualRow)
            .where(MonthlyActualRow.id.in_([uuid.UUID(i) for i in phase_ids]))
            .execution_options(populate_existing=True)
        )
    ).all()
    no_detail = "カイポケから結果が返りませんでした（カイポケで確かめてください）"
    for row in rows:
        d = by_id.get(row.id)
        with_care = str(row.id) in care_ids
        if d is None:
            # 結果に載らなかった (非常停止など)。送っていない扱いで、目視を促す。
            if not care_phase and row.apply_status == APPLY_QUEUED:
                row.apply_status = RPA_ERROR
                row.apply_reason = "no_detail"
                row.apply_message = no_detail
                row.applied_at = now
            if with_care and row.care_record_status == APPLY_QUEUED:
                row.care_record_status = RPA_ERROR
                row.care_record_reason = "no_detail"
                row.care_record_message = no_detail
                row.care_record_at = now
            continue
        if care_phase:
            row.care_record_status = str(d.get("status") or RPA_ERROR)[:16]
            row.care_record_reason = (str(d["reason"])[:40]) if d.get("reason") else None
            row.care_record_message = _detail_message(d)
            row.care_record_dry_run = dry_run
            row.care_record_at = now
            if dry_run:
                row.dry_run_ok_sig = dry_run_sig(row) if _care_record_ok(row) else None
            continue
        row.apply_status = str(d.get("status") or RPA_ERROR)[:16]
        row.apply_reason = (str(d["reason"])[:40]) if d.get("reason") else None
        row.apply_message = _detail_message(d)
        row.apply_dry_run = dry_run
        row.applied_at = now
        if with_care:
            care_status = d.get("care_record_status")
            # 記録書Ⅱの結果が無い (記録書Ⅱを扱わない RPA など) ときは、送っていない扱いに戻す。
            row.care_record_status = str(care_status)[:16] if care_status else None
            row.care_record_reason = (
                str(d["care_record_reason"])[:40] if d.get("care_record_reason") else None
            )
            row.care_record_message = _care_record_message(d)
            row.care_record_dry_run = dry_run if care_status else None
            row.care_record_at = now if care_status else None
        if dry_run:
            ok = row.apply_status == RPA_DRY_RUN_OK and (not with_care or _care_record_ok(row))
            row.dry_run_ok_sig = dry_run_sig(row) if ok else None
    keys = ("total", "success", "dry_run_ok", "skipped", "failed", "error")
    counts = {k: int(result.get(k) or 0) for k in keys}
    await db.flush()
    job = await db.get(KaipokeJob, job.id, populate_existing=True) or job
    if job.status == _RUNNING:
        summary = dict(job.result_summary or {})
        if not dry_run:
            all_rows = await _rows_of_job(db, job)
            summary["sent_results"] = _sent_results(all_rows, now)
        _, completed_key = _PHASE_KEYS[phase]
        summary[completed_key] = True
        if care_phase:
            summary["k2_counts"] = counts
        else:
            summary["rpa_counts"] = counts
            if isinstance(result.get("care_record"), dict):
                summary["care_record_rpa_counts"] = result["care_record"]
        summary["stopped"] = bool(summary.get("stopped")) or bool(result.get("stopped"))
        summary["done"] = int(summary.get("done") or 0) + len(rows)
        job.result_summary = summary
        job.updated_at = now
    await db.commit()
    return "completed"


def _sent_results(rows: Any, now: datetime) -> dict[str, dict[str, Any]]:
    """送った記録の結果 (行の id → 結果)。``params.sent`` と合わせて読む。"""
    return {
        str(r.id): {
            "status": r.apply_status,
            "reason": r.apply_reason,
            "care_record_status": r.care_record_status,
            "care_record_reason": r.care_record_reason,
            "at": _iso(r.applied_at or r.care_record_at) or now.isoformat(),
        }
        for r in rows
    }


async def _admin_users(db: AsyncSession) -> list[User]:
    return list(
        (
            await db.scalars(
                select(User).where(User.role.in_(("admin", "manager")), User.deleted_at.is_(None))
            )
        ).all()
    )


async def notify_admins(
    db: AsyncSession, *, job_id: uuid.UUID, month: str, title: str, body: str
) -> int:
    """管理者全員へ 1 通ずつ (同じジョブは 1 回だけ)。commit は呼び出し側。"""
    existing = set(
        (
            await db.scalars(
                select(Notification.user_id).where(
                    Notification.reference_type == NOTIFY_REFERENCE,
                    Notification.reference_id == job_id,
                )
            )
        ).all()
    )
    added = 0
    for user in await _admin_users(db):
        if user.id in existing:
            continue
        db.add(
            Notification(
                user_id=user.id,
                type=NOTIFY_TYPE,
                title=title[:200],
                body=body,
                reference_type=NOTIFY_REFERENCE,
                reference_id=job_id,
                data={"month": month, "job_id": str(job_id)},
            )
        )
        added += 1
    return added


def _month_label(month: str) -> str:
    year, mon = month.split("-")
    return f"{int(year)}年{int(mon)}月"


async def _rows_of_job(db: AsyncSession, job: KaipokeJob) -> list[MonthlyActualRow]:
    row_ids = [uuid.UUID(i) for i in (job.params or {}).get("row_ids") or []]
    if not row_ids:
        return []
    return list(
        (
            await db.scalars(
                select(MonthlyActualRow)
                .where(MonthlyActualRow.id.in_(row_ids))
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def finish_apply(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    error: str | None = None,
    verify: dict[str, Any] | None = None,
    counts: dict[str, int] | None = None,
    care_counts: dict[str, int] | None = None,
) -> KaipokeJob | None:
    """反映のジョブを決着させ、作業の状態を書き、管理者へ知らせる (commit する)。

    ``running`` でなくなったジョブ (取り消し・すでに決着) は書き換えない。``counts`` (実績)・
    ``care_counts`` (記録書Ⅱ) は確かめる前に数えた件数 (確かめると行が作り直されるため)。
    """
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or job.status != _RUNNING:
        await db.rollback()
        return job
    params = job.params or {}
    month = str(params.get("month"))
    dry_run = bool(params.get("dry_run"))
    now = datetime.now(UTC)
    summary = {
        **(job.result_summary or {}),
        "counts": counts,
        "care_record_counts": care_counts,
        "progress": None,
    }
    if counts is None or care_counts is None:
        rows = await _rows_of_job(db, job)
        if error is not None:
            # RPA を始めた後なら書き換えたか分からない (カイポケで要目視)。始める前なら送っていない。
            prev = job.result_summary or {}
            actual_started = bool(prev.get("rpa_started"))
            k2_started = bool(prev.get("k2_started"))
            _, _, k2_ids = job_row_ids(job)
            stopped = _short(f"途中で止まりました（カイポケで確かめてください）: {error}", 2000)
            for row in rows:
                if row.apply_status == APPLY_QUEUED:
                    if actual_started:
                        row.apply_status = APPLY_UNKNOWN
                        row.apply_reason = None
                        row.apply_message = stopped
                        row.applied_at = now
                    else:
                        row.apply_status = None
                if row.care_record_status == APPLY_QUEUED:
                    if k2_started if str(row.id) in k2_ids else actual_started:
                        row.care_record_status = APPLY_UNKNOWN
                        row.care_record_reason = None
                        row.care_record_message = stopped
                        row.care_record_at = now
                    else:
                        row.care_record_status = None
            if (actual_started or k2_started) and not dry_run and "sent_results" not in summary:
                summary["sent_results"] = _sent_results(rows, now)
        counts, care_counts = job_counts(job, rows)
        summary["counts"] = counts
        summary["care_record_counts"] = care_counts
    if error is not None:
        summary["error"] = error
    written = await db.execute(
        update(KaipokeJob)
        .where(KaipokeJob.id == job_id, KaipokeJob.status == _RUNNING)
        .values(
            status="failed" if error is not None else "completed",
            completed_at=now,
            updated_at=now,
            result_summary=summary,
        )
        .execution_options(synchronize_session=False)
    )
    if not getattr(written, "rowcount", 0):
        await db.rollback()
        return await db.get(KaipokeJob, job_id, populate_existing=True)

    run = await get_run(db, month)
    if run is not None:
        run.last_apply = {
            "job_id": str(job_id),
            "dry_run": dry_run,
            "finished_at": now.isoformat(),
            "counts": counts,
            "care_record_counts": care_counts,
            "error": error,
        }
        if verify is not None:
            run.verify_result = verify
        if error is not None:
            run.state = "failed"
        elif dry_run:
            run.state = "judged"
        elif verify is not None and not verify.get("error") and not verify.get("not_matched"):
            run.state = "verified"
        else:
            run.state = "applied"

    label = _month_label(month)
    kind = "試しに流しました" if dry_run else "カイポケへの反映が終わりました"
    if error is not None:
        title = f"{label}の実績の{'試し' if dry_run else '反映'}が止まりました"
        body = f"理由: {error}\n" + _counts_text(counts, dry_run)
    else:
        title = f"{label}の実績を{kind}" if dry_run else f"{label}の実績の{kind}"
        body = _counts_text(counts, dry_run)
    if any(care_counts.values()):
        body += "\n記録書Ⅱ: " + _counts_text(care_counts, dry_run)
    if error is None and verify is not None:
        if verify.get("error"):
            body += f"\n確かめ: 実績を読み取り直せませんでした（{verify['error']}）"
        else:
            body += (
                f"\n確かめ: 反映した {verify.get('applied', 0)} 件のうち "
                f"{verify.get('matched', 0)} 件が実績も記録書Ⅱも QR と同じになりました"
            )
    body += "\n訪問記録の「月の実績」で 1 件ずつ見られます。"
    await notify_admins(db, job_id=job_id, month=month, title=title, body=body)
    await db.commit()
    return await db.get(KaipokeJob, job_id, populate_existing=True)


def _counts_text(counts: dict[str, int], dry_run: bool) -> str:
    ok = counts.get("dry_run_ok", 0) if dry_run else counts.get("success", 0)
    failed = counts.get("failed", 0) + counts.get("error", 0)
    head = "登録の直前まで進めた" if dry_run else "成功"
    text = f"{head} {ok} 件・失敗 {failed} 件・スキップ {counts.get('skipped', 0)} 件"
    if counts.get("unknown"):
        text += f"・カイポケで確かめる {counts['unknown']} 件"
    return text


async def _export_actual(
    db: AsyncSession, client: KaipokeClient, *, job_id: uuid.UUID, month: str
) -> KaipokeCsvSnapshot:
    """RPA が空くのを待って実績を export し、スナップショットに保存する (commit する)。"""
    credentials = await get_credentials_payload(db)
    await db.commit()
    csv_text = await sources.call_when_idle(
        db,
        client,
        job_id=job_id,
        call=lambda: _export_one(
            client=client, month=month, division="actual", credentials=credentials
        ),
    )
    snap = await save_snapshot(
        db,
        office_id=None,
        month=month,
        week_start=None,
        csv_text=csv_text,
        source_op=SNAPSHOT_SOURCE_OP,
        division="actual",
    )
    if snap is None:
        raise RuntimeError("カイポケにこの月の実績がありません")
    await db.commit()
    return snap


async def verify_after_apply(
    db: AsyncSession, client: KaipokeClient, *, job: KaipokeJob, month: str
) -> dict[str, Any]:
    """反映の後に実績を読み取り直して判定し直し、反映した訪問が QR と同じになったかを見る。

    実績 (カイポケの実績の時刻) と記録書Ⅱ (CSV の記録Ⅱの列) の両方を数える。記録書Ⅱが無い
    行は、記録書Ⅱは合っている扱い。
    """
    job_rows = await _rows_of_job(db, job)
    actual_visits = {r.visit_id for r in job_rows if is_applied(r) and r.visit_id is not None}
    care_visits = {
        r.visit_id for r in job_rows if is_care_record_applied(r) and r.visit_id is not None
    }
    applied_visits = actual_visits | care_visits
    await db.commit()
    try:
        # 確かめの間も動いている印を書く (状態の API が古いジョブとみなして閉じないように)。
        await _touch(db, job.id, verifying=True)
        snap = await _export_actual(db, client, job_id=job.id, month=month)
        await _touch(db, job.id)
        run = await judge_month(db, month, snapshot=snap, keep_checks=True)
        rows = await load_rows(db, run.id)
        await db.commit()
    except SourceJobCancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 確かめられなくても反映の結果は残す
        await db.rollback()
        logger.warning("monthly actual verify failed for %s: %s", month, exc)
        return {"checked_at": datetime.now(UTC).isoformat(), "error": _short(exc)}
    by_visit = {r.visit_id: r for r in rows if r.visit_id in applied_visits}
    actual_ok = {v for v, r in by_visit.items() if not needs_actual(r)}
    care_ok = {v for v, r in by_visit.items() if not needs_care_record(r)}
    not_matched = [
        {
            "date": r.visit_date.isoformat(),
            "patient": r.patient,
            "status": r.status,
            "new_start": r.new_start,
            "new_end": r.new_end,
            "kp_start": r.kp_start,
            "kp_end": r.kp_end,
            "k2_start": r.k2_start,
            "k2_end": r.k2_end,
            "actual_matched": v in actual_ok,
            "care_record_matched": v in care_ok,
        }
        for v, r in by_visit.items()
        if v not in actual_ok or v not in care_ok
    ]
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "kaipoke_fetched_at": _iso(snap.fetched_at),
        "snapshot_id": str(snap.id),
        "applied": len(applied_visits),
        "matched": len(applied_visits) - len(not_matched),
        "actual_applied": len(actual_visits),
        "actual_matched": len(actual_visits & actual_ok),
        "care_record_applied": len(care_visits),
        "care_record_matched": len(care_visits & care_ok),
        "not_matched": not_matched,
        "counts": run.counts,
    }


async def _run_phase(
    db: AsyncSession,
    client: KaipokeClient,
    job_id: uuid.UUID,
    *,
    phase: str,
    body: dict[str, Any],
    deadline: datetime,
) -> bool:
    """RPA の 1 つの口へ送り、結果を待って行ごとに書く。ジョブが動いていなければ False。"""
    care_phase = phase == PHASE_CARE_RECORD
    started_key, _ = _PHASE_KEYS[phase]
    body = {**body, "job_id": rpa_job_id(job_id, phase)}

    async def _apply() -> dict[str, Any]:
        if datetime.now(UTC) >= deadline:
            raise RuntimeError("締め切りを過ぎたため送りませんでした")
        if care_phase:
            return await client.care_record_time_apply(body, timeout=_START_TIMEOUT)
        return await client.actual_time_apply(body, timeout=_START_TIMEOUT)

    resp = await sources.call_when_idle(db, client, job_id=job_id, call=_apply)
    if not resp.get("success"):
        what = "記録書Ⅱのそろえ" if care_phase else "カイポケへの反映"
        raise RuntimeError(str(resp.get("error") or f"{what}を始められませんでした"))
    if not await _touch(db, job_id, **{started_key: True}):
        return False

    no_result = 0
    while True:
        if datetime.now(UTC) >= deadline:
            raise RuntimeError(
                f"{APPLY_DEADLINE_MINUTES} 分たっても終わらなかったため、結果の受け取りをやめました"
                "（カイポケで確かめてください）"
            )
        await asyncio.sleep(APPLY_POLL_SECONDS)
        if not await _touch(db, job_id):
            return False
        try:
            if care_phase:
                rpa = await client.care_record_time_result()
            else:
                rpa = await client.actual_time_result()
        except KaipokeApiError:
            continue
        job = await db.get(KaipokeJob, job_id, populate_existing=True)
        if job is None or job.status != _RUNNING:
            await db.rollback()
            return False
        outcome = await ingest_rpa_result(db, job, rpa, phase=phase)
        if outcome == "completed":
            return True
        if outcome == "no_result":
            no_result += 1
            if no_result >= MAX_NO_RESULT:
                raise RuntimeError("カイポケ側で結果が失われました（カイポケで確かめてください）")
        else:
            no_result = 0


async def run_apply(
    db: AsyncSession, client: KaipokeClient, job_id: uuid.UUID
) -> KaipokeJob | None:
    """反映のジョブの本体: RPA へ送り、結果を待って行ごとに書き、本番なら確かめ、決着させる。

    RPA は 1 つずつしか動けないので、実績の口 (記録書Ⅱも続けて) → 記録書Ⅱだけの口 の順に流す。
    """
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or job.status != _RUNNING:
        await db.rollback()
        return job
    params = dict(job.params or {})
    month = str(params["month"])
    dry_run = bool(params.get("dry_run"))
    started = _aware(job.started_at) or datetime.now(UTC)
    deadline = started + timedelta(minutes=APPLY_DEADLINE_MINUTES)
    try:
        rows = await _rows_of_job(db, job)
        actual_ids, care_ids, k2_ids = job_row_ids(job)
        actual_items = build_rpa_items(
            [r for r in rows if str(r.id) in actual_ids], care_record_ids=care_ids
        )
        care_items = build_care_record_items([r for r in rows if str(r.id) in k2_ids])
        credentials = await get_credentials_payload(db)
        await db.commit()
        base: dict[str, Any] = {"month": month, "dry_run": dry_run, "headed": True}
        if credentials:
            base["credentials"] = credentials

        for phase, items in ((PHASE_ACTUAL, actual_items), (PHASE_CARE_RECORD, care_items)):
            if not items:
                continue
            ok = await _run_phase(
                db, client, job_id, phase=phase, body={**base, "items": items}, deadline=deadline
            )
            if not ok:
                return await db.get(KaipokeJob, job_id, populate_existing=True)

        job = await db.get(KaipokeJob, job_id, populate_existing=True)
        assert job is not None
        counts, care_counts = job_counts(job, await _rows_of_job(db, job))
        verify = None
        if not dry_run:
            verify = await verify_after_apply(db, client, job=job, month=month)
        return await finish_apply(db, job_id, verify=verify, counts=counts, care_counts=care_counts)
    except SourceJobCancelledError:
        await db.rollback()
        return await db.get(KaipokeJob, job_id, populate_existing=True)
    except asyncio.CancelledError:
        await asyncio.shield(_close_failed(db, job_id, "中断されました"))
        raise
    except Exception as exc:  # noqa: BLE001 — 黙って死なせない (running が残る)
        logger.warning("monthly actual apply job %s failed: %s", job_id, exc)
        return await _close_failed(db, job_id, _short(exc))


async def _close_failed(db: AsyncSession, job_id: uuid.UUID, error: str) -> KaipokeJob | None:
    try:
        await db.rollback()
        return await finish_apply(db, job_id, error=error)
    except Exception:  # noqa: BLE001 — 元の失敗を隠さない (ここはログだけ)
        logger.exception("monthly actual job %s could not be closed (%s)", job_id, error)
        return None


async def run_apply_job(*, job_id: uuid.UUID, client: KaipokeClient) -> KaipokeJob | None:
    """応答の後に自前のセッションで ``run_apply`` を呼ぶ (``BackgroundTasks`` 用)。"""
    async with get_session_factory()() as db:
        return await run_apply(db, client, job_id)


async def poll_apply_status(db: AsyncSession, client: KaipokeClient, run: MonthlyActualRun) -> None:
    """状態の API から: 反映が動いていれば RPA の結果を 1 回読み、行ごとに書く。

    背景の読み直しが止まっている (プロセスの再起動など) ときだけ、ここでジョブを決着させる。
    RPA に届かないときは何もしない (次の読み直しで)。
    """
    job = await active_job(db, run.apply_job_id)
    if job is None:
        return
    summary = job.result_summary or {}
    # いま RPA で動いている口 (記録書Ⅱだけの口は実績の口の後)。どちらも終わっていれば、
    # 背景が次の口へ送る前か確かめ (読み取り直し) の途中なので、件数なしで閉じない。
    if summary.get("k2_started") and not summary.get("k2_completed"):
        phase = PHASE_CARE_RECORD
    elif summary.get("rpa_started") and not summary.get("rpa_completed"):
        phase = PHASE_ACTUAL
    else:
        return
    try:
        if phase == PHASE_CARE_RECORD:
            rpa = await client.care_record_time_result()
        else:
            rpa = await client.actual_time_result()
    except KaipokeApiError:
        return
    touched = _aware(job.updated_at) or datetime.now(UTC)
    stale = touched < datetime.now(UTC) - timedelta(minutes=STALE_POLLER_MINUTES)
    try:
        outcome = await ingest_rpa_result(db, job, rpa, phase=phase)
    except RuntimeError as exc:
        if stale:
            await finish_apply(db, job.id, error=_short(exc))
        else:
            await db.rollback()
        return
    if outcome == "completed" and stale:
        await finish_apply(db, job.id)


# --- 取り直し -------------------------------------------------------------------------


async def start_refresh(db: AsyncSession, *, month: str, user_id: uuid.UUID | None) -> KaipokeJob:
    """カイポケの実績を取り直して判定し直すジョブを立てる (commit する)。"""
    run = await get_run(db, month)
    if run is not None and await active_job(db, run.apply_job_id) is not None:
        raise MonthlyActualError(409, "反映の途中です。終わってから読み取り直してください")
    await db.commit()
    job = await sources.start_source_job(
        db,
        op=OP_MONTHLY_ACTUAL_REFRESH,
        week_start=month_monday(month),
        user_id=user_id,
        params={"month": month},
        total=1,
    )
    run = await get_run(db, month)
    if run is not None:
        run.refresh_job_id = job.id
        await db.commit()
    return job


async def run_refresh(
    db: AsyncSession, client: KaipokeClient, job_id: uuid.UUID
) -> KaipokeJob | None:
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or job.status != _RUNNING:
        await db.rollback()
        return job
    month = str((job.params or {})["month"])
    error: str | None = None
    counts: dict[str, int] | None = None
    try:
        snap = await _export_actual(db, client, job_id=job_id, month=month)
        run = await judge_month(db, month, snapshot=snap)
        run.refresh_job_id = job_id
        counts = dict(run.counts or {})
        await db.commit()
    except SourceJobCancelledError:
        await db.rollback()
        return await db.get(KaipokeJob, job_id, populate_existing=True)
    except asyncio.CancelledError:
        error = "中断されました"
        raise
    except Exception as exc:  # noqa: BLE001 — 黙って死なせない
        await db.rollback()
        logger.warning("monthly actual refresh job %s failed: %s", job_id, exc)
        error = _short(getattr(exc, "detail", None) or exc)
    finally:
        await asyncio.shield(_finish_refresh(db, job_id, error=error, counts=counts))
    return await db.get(KaipokeJob, job_id, populate_existing=True)


async def _finish_refresh(
    db: AsyncSession, job_id: uuid.UUID, *, error: str | None, counts: dict[str, int] | None
) -> None:
    try:
        job = await db.get(KaipokeJob, job_id, populate_existing=True)
        if job is None or job.status != _RUNNING:
            await db.rollback()
            return
        now = datetime.now(UTC)
        summary = {**(job.result_summary or {}), "done": 1, "counts": counts}
        if error is not None:
            summary["error"] = error
        await db.execute(
            update(KaipokeJob)
            .where(KaipokeJob.id == job_id, KaipokeJob.status == _RUNNING)
            .values(
                status="failed" if error is not None else "completed",
                completed_at=now,
                updated_at=now,
                result_summary=summary,
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — ここはログだけ
        logger.exception("monthly actual refresh job %s could not be closed", job_id)


async def run_refresh_job(*, job_id: uuid.UUID, client: KaipokeClient) -> KaipokeJob | None:
    async with get_session_factory()() as db:
        return await run_refresh(db, client, job_id)


# --- 夜の予約 -------------------------------------------------------------------------


def next_default_schedule(now: datetime | None = None) -> datetime:
    """次の 22:00 (JST)。もう過ぎていれば翌日の 22:00。"""
    current = (now or datetime.now(UTC)).astimezone(JST)
    at = datetime.combine(current.date(), DEFAULT_SCHEDULE_TIME, tzinfo=JST)
    if at <= current:
        at += timedelta(days=1)
    return at.astimezone(UTC)


async def schedule_apply(
    db: AsyncSession,
    *,
    month: str,
    user_id: uuid.UUID,
    dry_run: bool,
    at: datetime | None,
) -> MonthlyActualRun:
    """反映を予約する (既定は今夜 22:00 JST)。commit は呼び出し側。"""
    if not is_past_month(month):
        raise MonthlyActualError(422, "カイポケへ反映できるのは、終わった月だけです")
    run = await get_run(db, month)
    if run is None:
        raise MonthlyActualError(
            422, "まだ判定していません。先にカイポケの実績を読み取ってください"
        )
    if await active_job(db, run.apply_job_id) is not None:
        raise MonthlyActualError(409, "反映がすでに動いています")
    when = _aware(at) if at is not None else next_default_schedule()
    assert when is not None
    if when <= datetime.now(UTC):
        raise MonthlyActualError(422, "予約の時刻は、これから先の時刻にしてください")
    rows = choose_rows(await load_rows(db, run.id))
    if not rows:
        raise MonthlyActualError(422, "反映する行がありません")
    if not dry_run:
        ensure_dry_run_passed(rows)
    run.scheduled_at = when
    run.scheduled_dry_run = dry_run
    run.scheduled_by = user_id
    await db.flush()
    return run


async def cancel_schedule(db: AsyncSession, *, month: str) -> MonthlyActualRun:
    run = await get_run(db, month)
    if run is None:
        raise MonthlyActualError(404, "この月の作業がありません")
    run.scheduled_at = None
    await db.flush()
    return run


@dataclass
class ScheduleTick:
    started: list[dict[str, Any]]
    waiting: list[dict[str, Any]]
    dropped: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"started": self.started, "waiting": self.waiting, "dropped": self.dropped}


async def run_due_schedules(db: AsyncSession, now: datetime | None = None) -> ScheduleTick:
    """予約の時刻を過ぎた月の反映を始める (cron から)。始めたジョブは ``started[].job_id``。

    ほかの RPA のジョブが動いていれば次の回まで待つ (予約を残す)。予約の時刻から 3 時間を
    過ぎていれば、理由を問わず (cron が止まっていた・ずっと塞がっていた) 予約を取り下げて
    管理者に知らせる。送る行が無い・試しを通っていなければ取り下げる。
    呼び出し側は ``started`` のジョブを ``run_apply_job`` で背景に流す。
    """
    current = now or datetime.now(UTC)
    due = (
        await db.scalars(
            select(MonthlyActualRun).where(
                MonthlyActualRun.scheduled_at.is_not(None),
                MonthlyActualRun.scheduled_at <= current,
            )
        )
    ).all()
    plans = [
        (r.month, bool(r.scheduled_dry_run), r.scheduled_by, _aware(r.scheduled_at)) for r in due
    ]
    await db.commit()
    tick = ScheduleTick(started=[], waiting=[], dropped=[])
    for month, dry_run, user_id, at in plans:
        if at is not None and current - at > SCHEDULE_GIVE_UP:
            await _drop_schedule(
                db, month, "予約の時刻から 3 時間を過ぎても始められませんでした（流していません）"
            )
            tick.dropped.append({"month": month, "reason": "late"})
            continue
        try:
            job = await start_apply(db, month=month, user_id=user_id, dry_run=dry_run)
        except RpaJobConflictError:
            await db.rollback()
            tick.waiting.append({"month": month})
            continue
        except MonthlyActualError as exc:
            await db.rollback()
            await _drop_schedule(db, month, exc.detail)
            tick.dropped.append({"month": month, "reason": exc.detail})
            continue
        run = await get_run(db, month)
        if run is not None:
            run.scheduled_at = None
            await db.commit()
        tick.started.append({"month": month, "job_id": str(job.id), "dry_run": dry_run})
    return tick


async def _drop_schedule(db: AsyncSession, month: str, reason: str) -> None:
    run = await get_run(db, month)
    if run is None:
        return
    run.scheduled_at = None
    ref = uuid.uuid5(
        uuid.NAMESPACE_URL, f"monthly-actual-schedule:{run.id}:{datetime.now(UTC).date()}"
    )
    await notify_admins(
        db,
        job_id=ref,
        month=month,
        title=f"{_month_label(month)}の実績の予約を取り下げました",
        body=f"理由: {reason}\n訪問記録の「月の実績」から、もう一度流してください。",
    )
    await db.commit()


__all__ = [
    "APPLY_POLL_SECONDS",
    "MonthlyActualError",
    "apply_counts",
    "build_care_record_items",
    "build_rpa_items",
    "care_record_counts",
    "care_record_needs_check",
    "is_care_record_applied",
    "job_counts",
    "job_row_ids",
    "needs_actual",
    "needs_care_record",
    "ensure_dry_run_passed",
    "needs_kaipoke_check",
    "cancel_schedule",
    "choose_rows",
    "finish_apply",
    "ingest_rpa_result",
    "is_past_month",
    "judge_month",
    "load_rk_visits",
    "load_rows",
    "next_default_schedule",
    "parse_kp_csv",
    "poll_apply_status",
    "row_view",
    "run_apply",
    "run_apply_job",
    "run_due_schedules",
    "run_refresh",
    "run_refresh_job",
    "run_view",
    "schedule_apply",
    "set_selected",
    "start_apply",
    "start_refresh",
]
