"""週のお知らせ (SMS) の一覧・送信・送る順番の処理.

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-1・§6・§7、
``docs/plans/sms-notify-api-contract-2026-11-16.md`` §12・§13。

* :func:`build_week_preview` — 週の一覧 (送れる方 / 送れない方と理由 / 合計・行の印 ``token``)。
* :func:`create_week_batch` — 送る: batch と 1 通ずつの行と版の記録を作り、
  ``patient_week_notices.version`` (送ろうとした回) を上げる。「お伝えした内容」はまだ替えない。
* :func:`send_due_messages` — 送る時刻の来た ``queued`` を 1 件ずつ順に送る
  (毎秒 1 件まで・1 回で :data:`MAX_SEND_PER_RUN` 件まで)。応答の後の BackgroundTask と
  cron (``/sms/send-due/run``) の両方から呼ぶ。
* :func:`record_accepted` — その版の SMS が 1 通でも受け付けられたら、「お伝えした内容」
  (``visits``・``told_at``・``last_channel``・``told_version``) をその版の中身に替える (§13-1)。

送る先: ``is_notify_target`` かつ同意「確認済み」かつ使える手段が SMS (携帯の番号) の連絡先。
二重送信の防止: ``sms_messages.dedupe_key`` (種類 + 患者 + 連絡先 + 週 + 版) が UNIQUE。
``sending`` へ移すときは「まだ queued なら」の条件付きで書く (同時に 2 つ動いても 1 回だけ)。
版を上げるときは「まだ見たときの版なら」の条件付きで書く (同時の操作は 409)。
送る直前に、期限・版・連絡先・稼働中・送れる時間帯・同じ番号の 600 秒を確かめる (§13-2)。

番号と本文はログに書かない。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import get_settings
from app.models.business_profile import BusinessProfile
from app.models.notification import Notification
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient import Patient
from app.models.patient_contact import PatientContact
from app.models.sms import (
    PatientNoticeEvent,
    PatientWeekNotice,
    SmsBatch,
    SmsMessage,
    SmsStatusEvent,
)
from app.models.user import User
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.services.contacts import RELATION_LABEL, _normalize_phone_input, effective_channel
from app.services.sms import composer, labels
from app.services.sms.provider import (
    SendResult,
    get_provider,
    provider_config_error,
    provider_setting,
)

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
FEATURE_KEY = "sms_notify"

#: 同じ番号へ続けて送るときにあける秒数 (KDDI・au の推奨)。
SAME_NUMBER_GAP_SECONDS = 600
#: 1 件ずつ送るあいだの秒数 (毎秒 1 件まで。KDDI の上限は毎秒 10 件)。試験では 0 にする。
SEND_INTERVAL_SECONDS = 1.0
#: 1 回の send_due_messages で扱う件数の上限 (cron は毎分・lock と接続を長く持たない)。
MAX_SEND_PER_RUN = 30
#: 502/503/504 などで送り直す回数の上限 (これを超えたら failed)。
MAX_ATTEMPTS = 3
#: 送り直すまでの秒数 (回数 × これ)。
RETRY_DELAY_SECONDS = 60
#: 受け付けた後、最初に照会する時刻 (設計 §7-3 の 1 分後)。
FIRST_POLL_SECONDS = 60
#: 送る期限 = max(作った時刻, not_before) + これ (契約 §13-2)。
EXPIRY_MINUTES = 60
#: 試験の状態のため送らなかった (``skip_reason``)。
SKIP_TEST_MODE = "test_mode"

#: 送れない理由 (画面の文言)。
REASON_LABEL: dict[str, str] = {
    "no_contact": "送り先がありません",
    "consent_unconfirmed": "同意が未確認です",
    "consent_ng": "NG 回答です",
    "not_mobile": "携帯の番号ではありません",
    "too_long": "文面が長すぎます",
    "unsupported_char": "お名前などに、SMS で送れない文字があります",
    "patient_inactive": "いまは稼働中でない方です（お電話でお伝えください）",
}

#: 確かめた後に予定や連絡先が変わった (契約 §12)。
STALE_MESSAGE: Final = "予定か連絡先が変わりました。一覧を開き直してください"
#: 同じ方へのほかの操作と重なった (契約 §13 の同時の操作)。
CONFLICT_MESSAGE: Final = "ほかの操作と重なりました。一覧を開き直してください"

#: 一覧の既定で印を付ける状態 (契約 §13-1)。
DEFAULT_SEND_STATES: Final = ("new", "changed_since_sent", "not_delivered")
#: まだ送り終えていない (待ち・送信中)。
IN_FLIGHT_STATUSES: Final = ("queued", "sending")

#: last_attempt.result の言葉。
ATTEMPT_RESULT_LABEL: dict[str, str] = {
    "failed": "受け付けられませんでした",
    "unknown": "送ったか分かりません",
    "delivery_failed": "届きませんでした",
}

# --- ベル (管理者全員・患者様の名前は書かない) ---
#: 画面の「お知らせの記録」(ベルから開く先)。
RECORDS_PATH: Final = "/sms-notices"
#: お知らせ待ちのベルの開く先。送る一覧はスケジュールの画面のダイアログ (変更のお知らせ) にある。
CHANGE_PENDING_PATH: Final = "/schedule?sms=changes"
NOTIFY_CHANGE_PENDING: Final = "sms_change_pending"
NOTIFY_DELIVERY_FAILED: Final = "sms_delivery_failed"
NOTIFY_UNSENT: Final = "sms_unsent"
NOTIFY_CONFIG_MISSING: Final = "sms_config_missing"
NOTIFY_CONFIG_ERROR: Final = "sms_config_error"
#: 設定が足りないベルを出し直すまでの時間。
CONFIG_BELL_INTERVAL_HOURS: Final = 24


def _now() -> datetime:
    """いまの時刻 (UTC)。試験で差し替える。"""
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite は時差を落として返すので、時差の無い値は UTC とみなす."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SmsNoticeError(Exception):
    """API で 4xx にするもの (``status_code`` と日本語の ``message``)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def make_token(*parts: Any) -> str:
    """見た内容と送る内容を合わせる印 (sha256 の先頭 32 桁・契約 §12)."""
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 設定・旗
# ---------------------------------------------------------------------------


async def flag_enabled_anywhere(db: AsyncSession) -> bool:
    """``sms_notify`` が ON の拠点が 1 つでもあるか."""
    row = await db.scalar(
        select(OfficeFeatureFlag.id)
        .where(
            OfficeFeatureFlag.feature_key == FEATURE_KEY,
            OfficeFeatureFlag.enabled_at.is_not(None),
        )
        .limit(1)
    )
    return row is not None


def parse_send_hours(spec: str) -> tuple[time, time]:
    """``"08:00-20:00"`` → (8:00, 20:00). 形が違えば既定の 8:00〜20:00."""
    try:
        start_s, end_s = (part.strip() for part in spec.split("-", 1))
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        return time(sh, sm), time(eh, em)
    except (ValueError, AttributeError):
        return time(8, 0), time(20, 0)


def within_send_hours(now: datetime, spec: str) -> bool:
    start, end = parse_send_hours(spec)
    t = now.astimezone(JST).time()
    return start <= t < end


def send_hours_label(spec: str) -> str:
    start, end = parse_send_hours(spec)
    return f"{start.hour}:{start.minute:02d}〜{end.hour}:{end.minute:02d}"


def phone_digits(raw: str) -> str:
    """連絡先と同じ数え方で数字だけにする (``+81``・ハイフン・全角を合わせる)."""
    return re.sub(r"[^0-9]", "", _normalize_phone_input(raw))


def parse_allowlist(spec: str) -> set[str]:
    out = {phone_digits(part) for part in (spec or "").split(",") if part.strip()}
    out.discard("")
    return out


def is_live_mode(settings: Any) -> bool:
    """``sms_send_mode`` が ``live`` のときだけ全員に送る (それ以外は test = 守りの側)."""
    return str(getattr(settings, "sms_send_mode", "test") or "test").strip().lower() == "live"


def may_really_send(phone: str | None, settings: Any) -> bool:
    """実際に送ってよい番号か.

    live = 全員。test (既定) = ``sms_test_allowlist`` にある番号だけ (一覧が空なら 1 件も送らない)。
    """
    if is_live_mode(settings):
        return True
    return bool(phone) and phone_digits(phone or "") in parse_allowlist(
        settings.sms_test_allowlist or ""
    )


def mask_phone(phone: str | None) -> str | None:
    """末尾 4 桁だけ (一覧に出す形)."""
    if not phone:
        return None
    return phone[-4:]


def is_upcoming(slot: tuple[date, time], now: datetime) -> bool:
    """まだ始まっていない訪問 (今日の分は開始の時刻がいまより後・日本時間)."""
    local = now.astimezone(JST)
    d, t = slot
    today = local.date()
    if d != today:
        return d > today
    return time(t.hour, t.minute) > local.time().replace(second=0, microsecond=0)


# ---------------------------------------------------------------------------
# ベル
# ---------------------------------------------------------------------------


async def admin_users(db: AsyncSession) -> list[User]:
    return list(
        (
            await db.scalars(
                select(User).where(User.role.in_(("admin", "manager")), User.deleted_at.is_(None))
            )
        ).all()
    )


async def add_bell(
    db: AsyncSession,
    *,
    type_: str,
    title: str,
    body: str,
    now: datetime,
    href: str = RECORDS_PATH,
) -> int:
    """管理者全員へ 1 つずつベルを足す (患者様の名前は入れない)。足した行の数を返す.

    href はベルを押したときに開く画面。既定は「お知らせの記録」。
    """
    users = await admin_users(db)
    ref = uuid.uuid4()
    for u in users:
        db.add(
            Notification(
                user_id=u.id,
                type=type_,
                title=title,
                body=body,
                reference_type=type_,
                reference_id=ref,
                data={"href": href},
                created_at=now,
                updated_at=now,
            )
        )
    return len(users)


async def last_bell_at(db: AsyncSession, type_: str, since: datetime) -> datetime | None:
    """``since`` より後に出した ``type_`` のベルの最後の時刻."""
    return aware(
        await db.scalar(
            select(func.max(Notification.created_at)).where(
                Notification.type == type_, Notification.created_at >= since
            )
        )
    )


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------


@dataclass
class Destination:
    contact_id: uuid.UUID
    relation: str
    phone: str

    @property
    def relation_label(self) -> str:
        return RELATION_LABEL.get(self.relation, self.relation)


def destinations_key(dests: Sequence[Destination]) -> list[list[str]]:
    """印に入れる送り先 (連絡先の ID と番号)."""
    return [[str(d.contact_id), d.phone] for d in dests]


@dataclass
class AttemptMsg:
    """一覧の状態を決めるための 1 通 (版・状態・理由・決まった時刻)."""

    version: int
    status: str
    skip_reason: str | None
    at: datetime | None


@dataclass
class PreviewRow:
    patient_id: uuid.UUID
    patient_name: str
    surname: str
    slots: list[tuple[date, time]]
    destinations: list[Destination] = field(default_factory=list)
    body: str = ""
    char_count: int = 0
    segments_est: int = 0
    #: new / changed_since_sent / already_sent_same / sending / not_delivered (§13-1)
    state: str = "new"
    #: 前の回の結果 (null か {"at", "result", "label"})。
    last_attempt: dict[str, Any] | None = None
    #: 送れないときの理由 (None = 送れる)。
    reason: str | None = None
    #: 名前に区切りの空白が無く、姓を分けられない。
    surname_uncertain: bool = False
    #: 一覧を作ったときの ``patient_week_notices.version`` (行が無ければ None)。
    notice_version_seen: int | None = None
    #: 一覧を作ったときの ``patient_week_notices.told_version`` (行が無ければ None)。
    told_version_seen: int | None = None

    @property
    def sendable(self) -> bool:
        return self.reason is None

    @property
    def message_count(self) -> int:
        return len(self.destinations)

    @property
    def est_yen(self) -> int:
        return self.segments_est * composer.YEN_PER_SEGMENT * self.message_count

    @property
    def visits_json(self) -> list[dict[str, str]]:
        return composer.slots_to_json(self.slots)

    @property
    def token(self) -> str:
        """本文・送り先・訪問の並び・状態・版の印 (契約 §12).

        状態と版も入れる: 一覧を開いているあいだに、ほかの管理者が同じ方へ送った
        (状態や版が替わった) ら印が合わず 409 になり、``resend`` でも 2 通目を送らない。
        """
        return make_token(
            "week",
            self.body,
            destinations_key(self.destinations),
            self.visits_json,
            self.state,
            self.notice_version_seen,
            self.told_version_seen,
        )


@dataclass
class WeekPreview:
    week_start: date
    today: date
    week_label: str
    rows: list[PreviewRow]

    @property
    def sendable(self) -> list[PreviewRow]:
        return [r for r in self.rows if r.sendable]

    @property
    def not_sendable(self) -> list[PreviewRow]:
        return [r for r in self.rows if not r.sendable]

    def totals(self) -> dict[str, int]:
        """既定で送る方 (送れる・印を付ける状態) の合計."""
        rows = [r for r in self.sendable if r.state in DEFAULT_SEND_STATES]
        return {
            "people": len(rows),
            "messages": sum(r.message_count for r in rows),
            "segments": sum(r.segments_est * r.message_count for r in rows),
            "yen": sum(r.est_yen for r in rows),
        }


def classify_destinations(
    contacts: Sequence[PatientContact],
) -> tuple[list[Destination], str | None]:
    """送る先と、送れないときの理由.

    送る先 = ``is_notify_target`` かつ同意「確認済み」かつ使える手段が SMS。
    同じ番号の連絡先が 2 つあっても 1 通にする。理由は次の順で決める:
    送る先の印が無い (NG 回答の連絡先があれば consent_ng・無ければ no_contact) →
    同意が確認済みでない (consent_unconfirmed) → SMS が使えない (not_mobile)。
    """
    targets = [c for c in contacts if c.is_notify_target]
    if not targets:
        if any(c.consent_status == "ng" for c in contacts):
            return [], "consent_ng"
        return [], "no_contact"
    confirmed = [c for c in targets if c.consent_status == "confirmed"]
    if not confirmed:
        if any(c.consent_status == "ng" for c in targets):
            return [], "consent_ng"
        return [], "consent_unconfirmed"
    out: list[Destination] = []
    seen: set[str] = set()
    for c in confirmed:
        if effective_channel(c.notify_channel, c.phone_kind, c.email) != "sms" or not c.phone:
            continue
        if c.phone in seen:
            continue
        seen.add(c.phone)
        out.append(Destination(contact_id=c.id, relation=c.relation, phone=c.phone))
    if not out:
        return [], "not_mobile"
    return out, None


def body_reason(body: str) -> str | None:
    """文面だけで決まる送れない理由 (送れない文字 / 長すぎる)."""
    if composer.has_unsupported_chars(body):
        return "unsupported_char"
    if composer.text_length(body) > composer.MAX_SMS_CHARS:
        return "too_long"
    return None


async def load_profile(db: AsyncSession) -> tuple[str, str]:
    """事業所名と電話 (``business_profile``)。どちらかが無ければ 422."""
    row = await db.scalar(select(BusinessProfile).where(BusinessProfile.is_singleton.is_(True)))
    station = (row.station_name or "").strip() if row else ""
    tel = (row.contact_tel or "").strip() if row else ""
    if not station or not tel:
        raise SmsNoticeError(
            422, "事業所の情報（事業所名と電話）を設定してから、お知らせを使ってください"
        )
    return station, tel


def validate_week_start(week_start: date) -> None:
    if week_start.weekday() != 0:
        raise SmsNoticeError(422, "週の始まり（月曜日）の日付を指定してください")


async def _week_slots(
    db: AsyncSession, week_start: date, now: datetime
) -> dict[uuid.UUID, list[tuple[date, time]]]:
    """週の中でまだ始まっていない、取消でない・削除されていない訪問 (打刻だけの予定外は除く)."""
    today = now.astimezone(JST).date()
    first = max(week_start, today)
    last = week_start + timedelta(days=6)
    if first > last:
        return {}
    rows = (
        await db.execute(
            select(Visit.patient_id, Visit.visit_date, Visit.start_time).where(
                Visit.visit_date >= first,
                Visit.visit_date <= last,
                Visit.status != VISIT_STATUS_CANCELLED,
                Visit.deleted_at.is_(None),
                Visit.is_unplanned.is_(False),
            )
        )
    ).all()
    out: dict[uuid.UUID, list[tuple[date, time]]] = defaultdict(list)
    for patient_id, d, t in rows:
        if is_upcoming((d, t), now):
            out[patient_id].append((d, t))
    return {pid: composer.unique_slots(slots) for pid, slots in out.items()}


async def load_attempts(
    db: AsyncSession, week_start: date, patient_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[AttemptMsg]]:
    """その週の、患者様ごとの送ろうとした 1 通 (週・変更の両方)."""
    if not patient_ids:
        return {}
    out: dict[uuid.UUID, list[AttemptMsg]] = defaultdict(list)
    for pid, version, st, reason, decided, updated in (
        await db.execute(
            select(
                SmsMessage.patient_id,
                SmsMessage.notice_version,
                SmsMessage.status,
                SmsMessage.skip_reason,
                SmsMessage.status_decided_at,
                SmsMessage.updated_at,
            ).where(
                SmsMessage.week_start == week_start,
                SmsMessage.patient_id.in_(list(patient_ids)),
            )
        )
    ).all():
        out[pid].append(AttemptMsg(version, st, reason, aware(decided) or aware(updated)))
    return out


def _attempt(result: str, msgs: Sequence[AttemptMsg]) -> dict[str, Any]:
    ats = [m.at for m in msgs if m.at is not None]
    if result == "skipped":
        reason = next((m.skip_reason for m in msgs if m.skip_reason), None)
        label = labels.status_label("skipped", reason)
    else:
        label = ATTEMPT_RESULT_LABEL[result]
    return {"at": max(ats) if ats else None, "result": result, "label": label}


def effective_told_version(notice: PatientWeekNotice | None, settings: Any = None) -> int | None:
    """お伝えしたとみなす版.

    mock (模擬) で受け付けた版は実際には届いていないので、窓口が mock でなくなったら
    「まだお伝えしていない」(None) とみなす。窓口が mock のあいだは今のまま (動きを見せられる)。
    """
    if notice is None or notice.told_version is None:
        return None
    if notice.told_via == "mock":
        if provider_setting(settings if settings is not None else get_settings()) != "mock":
            return None
    return notice.told_version


def delivery_failed(told_version: int | None, msgs: Sequence[AttemptMsg]) -> bool:
    """お伝えした版の SMS が、受け付けられた後にすべて届かなかった."""
    if told_version is None:
        return False
    told = [m for m in msgs if m.version == told_version]
    return bool(told) and all(m.status == "failed" for m in told)


def attempt_state(
    notice: PatientWeekNotice | None,
    msgs: Sequence[AttemptMsg],
    current_visits: list[dict[str, str]],
    now: datetime,
) -> tuple[str, dict[str, Any] | None]:
    """一覧の ``state`` と ``last_attempt`` (契約 §13-1)."""
    last_attempt: dict[str, Any] | None = None
    told_version = effective_told_version(notice)
    if notice is not None:
        latest = [m for m in msgs if m.version == notice.version]
        if (
            latest
            and told_version != notice.version
            and all(m.status in ("skipped", "failed", "unknown") for m in latest)
        ):
            statuses = {m.status for m in latest}
            result = (
                "unknown"
                if "unknown" in statuses
                else "failed"
                if "failed" in statuses
                else "skipped"
            )
            last_attempt = _attempt(result, latest)
    if any(m.status in IN_FLIGHT_STATUSES for m in msgs):
        return "sending", last_attempt
    if delivery_failed(told_version, msgs):
        told = [m for m in msgs if m.version == told_version]
        return "not_delivered", _attempt("delivery_failed", told)
    if notice is not None and told_version is not None:
        told_now = composer.slots_to_json(
            [s for s in composer.slots_from_json(notice.visits) if is_upcoming(s, now)]
        )
        same = told_now == current_visits
        return ("already_sent_same" if same else "changed_since_sent"), last_attempt
    if last_attempt is not None:
        return "not_delivered", last_attempt
    return "new", None


async def build_week_preview(db: AsyncSession, week_start: date, now: datetime) -> WeekPreview:
    """週のお知らせの一覧 (まだ始まっていない訪問のある、稼働中の患者様だけ)."""
    validate_week_start(week_start)
    station, tel = await load_profile(db)
    today = now.astimezone(JST).date()
    slots_by_patient = await _week_slots(db, week_start, now)
    if not slots_by_patient:
        return WeekPreview(week_start, today, composer.week_label(week_start, today), [])

    patients = (
        await db.scalars(
            select(Patient).where(
                Patient.id.in_(list(slots_by_patient)),
                Patient.status == "active",
                Patient.deleted_at.is_(None),
            )
        )
    ).all()
    ids = [p.id for p in patients]
    contacts_by_patient: dict[uuid.UUID, list[PatientContact]] = defaultdict(list)
    for c in (
        await db.scalars(
            select(PatientContact)
            .where(PatientContact.patient_id.in_(ids))
            .order_by(PatientContact.patient_id, PatientContact.sort_order)
        )
    ).all():
        contacts_by_patient[c.patient_id].append(c)
    notices = {
        n.patient_id: n
        for n in (
            await db.scalars(
                select(PatientWeekNotice).where(
                    PatientWeekNotice.patient_id.in_(ids),
                    PatientWeekNotice.week_start == week_start,
                )
            )
        ).all()
    }
    attempts = await load_attempts(db, week_start, ids)

    rows: list[PreviewRow] = []
    for p in sorted(patients, key=lambda x: ((x.kana or x.name or ""), x.name or "")):
        slots = slots_by_patient[p.id]
        surname = composer.surname_of(p.name)
        row = PreviewRow(
            patient_id=p.id,
            patient_name=p.name,
            surname=surname,
            slots=slots,
            surname_uncertain=composer.surname_uncertain(p.name),
        )
        row.body = composer.compose_week_notice(
            station_name=station,
            contact_tel=tel,
            surname=surname,
            week_start=week_start,
            today=today,
            slots=slots,
        )
        row.char_count = composer.count_chars(row.body)
        row.segments_est = composer.estimate_segments(row.char_count)
        row.destinations, row.reason = classify_destinations(contacts_by_patient.get(p.id, []))
        if row.reason is None:
            row.reason = body_reason(row.body)
            if row.reason is not None:
                row.destinations = []
        notice = notices.get(p.id)
        row.notice_version_seen = notice.version if notice is not None else None
        row.told_version_seen = notice.told_version if notice is not None else None
        row.state, row.last_attempt = attempt_state(
            notice, attempts.get(p.id, []), row.visits_json, now
        )
        rows.append(row)
    return WeekPreview(week_start, today, composer.week_label(week_start, today), rows)


# ---------------------------------------------------------------------------
# 送る (batch を作る)
# ---------------------------------------------------------------------------


def dedupe_key(
    kind: str, patient_id: uuid.UUID, contact_id: uuid.UUID, week_start: date, version: int
) -> str:
    return f"{kind}:{patient_id}:{contact_id}:{week_start.isoformat()}:v{version}"


def select_rows_to_send(
    preview: WeekPreview,
    patient_ids: Sequence[uuid.UUID],
    tokens: dict[uuid.UUID, str],
    *,
    resend: bool,
) -> list[PreviewRow]:
    """選ばれた方の行 (1 名でも合わなければ 409 で、だれにも送らない・契約 §12).

    * 行が無い・送れない・印が違う → 409 (予定か連絡先が変わりました)。
    * いま送っている途中 (``sending``) → 409 (ほかの操作と重なりました)。
    * 同じ内容をお伝え済み (``already_sent_same``) は ``resend`` のときだけ。
    """
    by_id = {r.patient_id: r for r in preview.rows}
    out: list[PreviewRow] = []
    for pid in dict.fromkeys(patient_ids):
        r = by_id.get(pid)
        if r is None or not r.sendable or tokens.get(pid) != r.token:
            raise SmsNoticeError(409, STALE_MESSAGE)
        if r.state == "sending":
            raise SmsNoticeError(409, CONFLICT_MESSAGE)
        if r.state == "already_sent_same" and not resend:
            raise SmsNoticeError(409, STALE_MESSAGE)
        out.append(r)
    return out


async def bump_notice_version(db: AsyncSession, notice: PatientWeekNotice, seen: int) -> int:
    """版を 1 つ上げる (見たときの版のままなら)。違えば 409 (同時の操作)."""
    new = seen + 1
    res = await db.execute(
        update(PatientWeekNotice)
        .where(PatientWeekNotice.id == notice.id, PatientWeekNotice.version == seen)
        .values(version=new)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        raise SmsNoticeError(409, CONFLICT_MESSAGE)
    set_committed_value(notice, "version", new)
    return new


async def next_free_by_phone(
    db: AsyncSession, phones: set[str], now: datetime
) -> dict[str, datetime]:
    """番号ごとに、次に送ってよい時刻 (直近に送った・送る予定の行から)."""
    if not phones:
        return {}
    window = now - timedelta(seconds=SAME_NUMBER_GAP_SECONDS)
    out: dict[str, datetime] = {}
    rows = (
        await db.execute(
            select(
                SmsMessage.phone, SmsMessage.sent_at, SmsMessage.not_before, SmsMessage.created_at
            ).where(
                SmsMessage.phone.in_(list(phones)),
                SmsMessage.status.in_(("queued", "sending", "accepted", "delivered", "unknown")),
                # 期限 (60 分) と間隔 (600 秒) より前の行は見ない。
                SmsMessage.created_at >= now - timedelta(hours=3),
            )
        )
    ).all()
    for phone, sent_at, not_before, created_at in rows:
        at = aware(sent_at) or aware(not_before) or aware(created_at)
        if at is None or at <= window:
            continue
        nxt = at + timedelta(seconds=SAME_NUMBER_GAP_SECONDS)
        if phone not in out or nxt > out[phone]:
            out[phone] = nxt
    return out


@dataclass
class BatchResult:
    batch: SmsBatch
    messages: list[SmsMessage]
    patient_ids: list[uuid.UUID]


async def create_week_batch(
    db: AsyncSession,
    *,
    preview: WeekPreview,
    rows: Sequence[PreviewRow],
    user_id: uuid.UUID | None,
    now: datetime,
) -> BatchResult:
    """batch・1 通ずつの行・版の記録を作り、版を上げる (commit しない).

    「お伝えした内容」は替えない (受け付けられたときに :func:`record_accepted` が替える)。
    同時の操作と重なったら :class:`SmsNoticeError` 409 (呼ぶ側で rollback)。
    """
    week_start = preview.week_start
    batch = SmsBatch(
        id=uuid.uuid4(),
        kind="week",
        week_start=week_start,
        created_by_user_id=user_id,
        patient_count=len(rows),
        message_count=sum(r.message_count for r in rows),
        segments_est_total=sum(r.segments_est * r.message_count for r in rows),
        created_at=now,
    )
    db.add(batch)
    await db.flush()

    ids = [r.patient_id for r in rows]
    notices = {
        n.patient_id: n
        for n in (
            await db.scalars(
                select(PatientWeekNotice).where(
                    PatientWeekNotice.patient_id.in_(ids),
                    PatientWeekNotice.week_start == week_start,
                )
            )
        ).all()
    }
    next_free = await next_free_by_phone(db, {d.phone for r in rows for d in r.destinations}, now)

    messages: list[SmsMessage] = []
    for r in rows:
        notice = notices.get(r.patient_id)
        if notice is None:
            if r.notice_version_seen is not None:
                raise SmsNoticeError(409, CONFLICT_MESSAGE)
            notice = PatientWeekNotice(
                id=uuid.uuid4(),
                patient_id=r.patient_id,
                week_start=week_start,
                version=1,
                visits=[],
            )
            db.add(notice)
            # 先に書き出す: 下の記録 (patient_notice_events) が notice_id を外部キーで指す。
            # relationship を張っていないので、まとめて flush すると順番が保証されない
            # (PostgreSQL で外部キー違反になる・SQLite の試験では見えない)。
            await db.flush()
            version = 1
        else:
            if r.notice_version_seen is None:
                raise SmsNoticeError(409, CONFLICT_MESSAGE)
            version = await bump_notice_version(db, notice, r.notice_version_seen)
        db.add(
            PatientNoticeEvent(
                notice_id=notice.id,
                patient_id=r.patient_id,
                week_start=week_start,
                version=version,
                kind="week",
                channel="sms",
                visits=r.visits_json,
                told_at=None,
                told_by_user_id=user_id,
                batch_id=batch.id,
            )
        )
        for d in r.destinations:
            messages.append(
                add_queued_message(
                    db,
                    batch=batch,
                    week_start=week_start,
                    patient_id=r.patient_id,
                    destination=d,
                    body=r.body,
                    char_count=r.char_count,
                    segments_est=r.segments_est,
                    notice_version=version,
                    user_id=user_id,
                    now=now,
                    next_free=next_free,
                )
            )
    await db.flush()
    return BatchResult(batch=batch, messages=messages, patient_ids=ids)


def add_queued_message(
    db: AsyncSession,
    *,
    batch: SmsBatch,
    week_start: date,
    patient_id: uuid.UUID,
    destination: Destination,
    body: str,
    char_count: int,
    segments_est: int,
    notice_version: int,
    user_id: uuid.UUID | None,
    now: datetime,
    next_free: dict[str, datetime],
) -> SmsMessage:
    """送る順番を待つ 1 通を足す (週のお知らせと変更のお知らせで共通).

    同じ番号へ続けて送るときは ``not_before`` で 600 秒あける (``next_free`` を書き換える)。
    期限は max(作った時刻, not_before) + 60 分。
    """
    d = destination
    free = next_free.get(d.phone)
    not_before = free if free is not None and free > now else None
    next_free[d.phone] = (not_before or now) + timedelta(seconds=SAME_NUMBER_GAP_SECONDS)
    msg = SmsMessage(
        id=uuid.uuid4(),
        batch_id=batch.id,
        kind=batch.kind,
        week_start=week_start,
        patient_id=patient_id,
        contact_id=d.contact_id,
        relation=d.relation,
        phone=d.phone,
        body=body,
        char_count=char_count,
        segments_est=segments_est,
        notice_version=notice_version,
        status="queued",
        dedupe_key=dedupe_key(batch.kind, patient_id, d.contact_id, week_start, notice_version),
        not_before=not_before,
        expires_at=(not_before or now) + timedelta(minutes=EXPIRY_MINUTES),
        created_by_user_id=user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(msg)
    db.add(SmsStatusEvent(message_id=msg.id, at=now, status="queued"))
    return msg


# ---------------------------------------------------------------------------
# 受け付けられた → お伝えした内容を替える (§13-1)
# ---------------------------------------------------------------------------


def clear_pending(notice: PatientWeekNotice) -> None:
    """お知らせ待ちの印を消す (お伝えした内容が替わったとき)."""
    notice.pending_since = None
    notice.pending_signature = None
    notice.pending_signature_at = None
    notice.pending_cleared_at = None
    notice.pending_urgent = False
    notice.last_bell_at = None
    notice.bell_count = 0


async def record_accepted(db: AsyncSession, msg: SmsMessage, now: datetime) -> bool:
    """その版の 1 通が受け付けられた → お伝えした内容をその版の中身に替える (commit しない).

    すでに同じか新しい版をお伝え済みなら何もしない。替えたら True。
    """
    if msg.patient_id is None:
        return False
    notice = await db.scalar(
        select(PatientWeekNotice).where(
            PatientWeekNotice.patient_id == msg.patient_id,
            PatientWeekNotice.week_start == msg.week_start,
        )
    )
    if notice is None:
        return False
    if notice.told_version is not None and notice.told_version >= msg.notice_version:
        return False
    event = await db.scalar(
        select(PatientNoticeEvent).where(
            PatientNoticeEvent.notice_id == notice.id,
            PatientNoticeEvent.version == msg.notice_version,
        )
    )
    if event is None:
        return False
    v = msg.notice_version
    # 版が後戻りしないよう、条件付きで書く (遅れて届いた古い版の受付で上書きしない)。
    res = await db.execute(
        update(PatientWeekNotice)
        .where(
            PatientWeekNotice.id == notice.id,
            or_(PatientWeekNotice.told_version.is_(None), PatientWeekNotice.told_version < v),
        )
        .values(
            visits=list(event.visits or []),
            told_version=v,
            told_at=now,
            last_channel="sms",
            told_via="kddi" if msg.provider == "kddi" else "mock",
            told_by_user_id=event.told_by_user_id,
            pending_since=None,
            pending_signature=None,
            pending_signature_at=None,
            pending_cleared_at=None,
            pending_urgent=False,
            last_bell_at=None,
            bell_count=0,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await db.refresh(notice)
    if res.rowcount != 1:
        return False
    event.told_at = now
    return True


# ---------------------------------------------------------------------------
# 送る順番の処理 (1 件ずつ)
# ---------------------------------------------------------------------------


def _event(
    msg_id: uuid.UUID, at: datetime, status: str, raw: str | None, detail: str | None
) -> SmsStatusEvent:
    return SmsStatusEvent(
        message_id=msg_id,
        at=at,
        status=status,
        raw_status=raw,
        detail=detail[:255] if detail else None,
    )


def _apply_result(
    msg: SmsMessage, result: SendResult, provider_name: str, now: datetime
) -> SmsStatusEvent:
    msg.provider = provider_name
    msg.updated_at = now
    if result.transaction_id:
        msg.transaction_id = result.transaction_id
    detail = result.error_code
    if result.needs_human and detail:
        detail = f"{detail} needs_human"
    if result.outcome == "accepted":
        msg.status = "accepted"
        msg.status_raw = result.status_raw
        msg.provider_msg_id = result.msg_id
        msg.accepted_at = now
        msg.next_poll_at = now + timedelta(seconds=FIRST_POLL_SECONDS)
        msg.error_code = None
        msg.error_message = None
        return _event(msg.id, now, "accepted", result.status_raw, None)
    msg.error_code = result.error_code
    msg.error_message = result.error_message
    if result.outcome == "retry" and (msg.attempt_count or 0) < MAX_ATTEMPTS:
        msg.status = "queued"
        # 待ちに戻す (送っていない)。前の sent_at・claimed_at を残すと、次に取ったときに
        # 「止まった行」と見誤る。
        msg.sent_at = None
        msg.claimed_at = None
        msg.not_before = now + timedelta(
            seconds=RETRY_DELAY_SECONDS * max(1, msg.attempt_count or 1)
        )
        return _event(msg.id, now, "queued", None, f"retry {detail}")
    # unknown も failed も、ここで決まった (ベルと記録の「決まった時刻」に使う)。
    msg.status = "unknown" if result.outcome == "unknown" else "failed"
    msg.status_decided_at = now
    return _event(msg.id, now, msg.status, None, detail)


async def skip_reason_before_send(db: AsyncSession, msg: SmsMessage, now: datetime) -> str | None:
    """送る直前の確かめ (契約 §13-2)。送らないときは ``skip_reason`` を返す."""
    expires = aware(msg.expires_at)
    created = aware(msg.created_at)
    if (expires is not None and now > expires) or (
        created is not None and created.astimezone(JST).date() != now.astimezone(JST).date()
    ):
        return "expired"
    if msg.patient_id is None:
        return "patient_inactive"
    current_version = await db.scalar(
        select(PatientWeekNotice.version).where(
            PatientWeekNotice.patient_id == msg.patient_id,
            PatientWeekNotice.week_start == msg.week_start,
        )
    )
    if current_version is not None and current_version > msg.notice_version:
        return "superseded"
    patient = await db.get(Patient, msg.patient_id)
    if patient is None or patient.deleted_at is not None or patient.status != "active":
        return "patient_inactive"
    contacts = (
        await db.scalars(select(PatientContact).where(PatientContact.patient_id == msg.patient_id))
    ).all()
    dests, _ = classify_destinations(contacts)
    if not msg.phone or msg.phone not in {d.phone for d in dests}:
        return "contact_changed"
    return None


async def next_send_time(
    db: AsyncSession, phone: str, message_id: uuid.UUID, now: datetime
) -> datetime | None:
    """同じ番号へ送ってよい次の時刻 (DB の ``sent_at`` から・いま送ってよければ None)."""
    gap = timedelta(seconds=SAME_NUMBER_GAP_SECONDS)
    others = (SmsMessage.phone == phone, SmsMessage.id != message_id)
    in_flight = await db.scalar(
        select(SmsMessage.id).where(*others, SmsMessage.status == "sending").limit(1)
    )
    if in_flight is not None:
        return now + gap
    last = aware(
        await db.scalar(
            select(func.max(SmsMessage.sent_at)).where(*others, SmsMessage.sent_at >= now - gap)
        )
    )
    if last is not None and last + gap > now:
        return last + gap
    return None


async def claim_message(db: AsyncSession, message_id: uuid.UUID, now: datetime) -> bool:
    """``queued`` を ``sending`` へ取る (まだ queued なら)。取れたら True (commit する)."""
    res = await db.execute(
        update(SmsMessage)
        .where(SmsMessage.id == message_id, SmsMessage.status == "queued")
        .values(status="sending", claimed_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return res.rowcount == 1


async def _close(
    db: AsyncSession,
    msg: SmsMessage,
    now: datetime,
    *,
    status: str,
    provider_name: str,
    skip_reason: str | None = None,
    error_code: str | None = None,
) -> None:
    """送らずに終える (skipped / failed)。"""
    msg.status = status
    msg.skip_reason = skip_reason
    msg.error_code = error_code
    msg.provider = provider_name
    msg.status_decided_at = now
    msg.updated_at = now
    db.add(_event(msg.id, now, status, None, skip_reason or error_code))
    await db.commit()


async def _release(
    db: AsyncSession, msg: SmsMessage, now: datetime, not_before: datetime | None = None
) -> None:
    """待ちに戻す (時間の外・設定が足りない・同じ番号の間隔)。"""
    msg.status = "queued"
    msg.claimed_at = None
    if not_before is not None:
        msg.not_before = not_before
    msg.updated_at = now
    await db.commit()


async def send_due_messages(
    *,
    limit: int = MAX_SEND_PER_RUN,
    interval: float | None = None,
    session_factory: Any = None,
) -> int:
    """送る時刻の来た ``queued`` を 1 件ずつ順に送る. 送った・送らずに終えた件数を返す.

    1 通ずつ、送る直前に確かめる (契約 §13-2):
    期限・作った日 (expired) / 版 (superseded) / 稼働中 (patient_inactive) /
    連絡先 (contact_changed) → ``skipped``。送れる時間帯の外 → 待ちのまま (期限で expired)。
    試験の守り: ``sms_send_mode`` が ``live`` でないあいだは、``sms_test_allowlist`` に無い番号は
    ``skipped`` (``test_mode``)。窓口の設定に誤りがある (``live`` なのに kddi でない・知らない値)
    なら ``failed`` (``config``) にし、「設定に誤りがある」ベルを出す (届かなかったベルにはしない)。
    kddi の接続の値が足りなければ待ちのまま、ベルを 1 回。同じ番号は DB の ``sent_at`` から
    600 秒あける (早ければ ``not_before`` を延ばして待ちのまま)。
    窓口へ送るあいだは transaction を持たない (送る前に commit する)。
    """
    from app.db.session import get_session_factory

    settings = get_settings()
    gap = SEND_INTERVAL_SECONDS if interval is None else interval
    factory = session_factory or get_session_factory()
    start = _now()
    provider = get_provider(settings)
    config_error = provider_config_error(settings)
    config_missing = provider.name == "kddi" and not getattr(provider, "configured", True)

    async with factory() as db:
        due = list(
            (
                await db.scalars(
                    select(SmsMessage.id)
                    .where(
                        SmsMessage.status == "queued",
                        or_(SmsMessage.not_before.is_(None), SmsMessage.not_before <= start),
                    )
                    .order_by(SmsMessage.created_at, SmsMessage.id)
                    .limit(limit)
                )
            ).all()
        )
        await db.commit()

        done = 0
        unsent = 0
        held_for_config = 0
        config_failed = 0
        last_sent = False
        for mid in due:
            if last_sent and gap > 0:
                await asyncio.sleep(gap)
            last_sent = False
            now = _now()
            if not await claim_message(db, mid, now):
                continue
            msg = await db.get(SmsMessage, mid, populate_existing=True)
            if msg is None:
                continue
            reason = await skip_reason_before_send(db, msg, now)
            if reason is not None:
                await _close(
                    db, msg, now, status="skipped", skip_reason=reason, provider_name=provider.name
                )
                done += 1
                unsent += 1
                continue
            if not within_send_hours(now, settings.sms_send_hours):
                await _release(db, msg, now)
                continue
            if not may_really_send(msg.phone, settings):
                await _close(
                    db,
                    msg,
                    now,
                    status="skipped",
                    skip_reason=SKIP_TEST_MODE,
                    provider_name=provider.name,
                )
                done += 1
                continue
            if config_error:
                # 設定の誤りのベルを別に出すので、届かなかったベルには数えない。
                msg.fail_notified_at = now
                await _close(
                    db, msg, now, status="failed", error_code="config", provider_name=provider.name
                )
                done += 1
                config_failed += 1
                continue
            if config_missing:
                await _release(db, msg, now)
                held_for_config += 1
                continue
            free_at = await next_send_time(db, msg.phone or "", msg.id, now)
            if free_at is not None:
                await _release(db, msg, now, not_before=free_at)
                continue
            msg.sent_at = now
            msg.attempt_count = (msg.attempt_count or 0) + 1
            msg.provider = provider.name
            await db.commit()  # 窓口へ送るあいだは transaction を持たない。
            try:
                result = await provider.send(msg.phone or "", msg.body or "")
            except Exception as exc:  # noqa: BLE001 — 窓口の思わぬ失敗でも順番の処理は止めない
                logger.warning("sms send failed unexpectedly: %s", type(exc).__name__)
                result = SendResult(outcome="unknown", error_code="exception", needs_human=True)
            at = _now()
            db.add(_apply_result(msg, result, provider.name, at))
            if msg.status == "accepted":
                await record_accepted(db, msg, at)
            await db.commit()
            done += 1
            last_sent = True

        bell_at = _now()
        if unsent:
            await add_bell(
                db,
                type_=NOTIFY_UNSENT,
                title=f"送れなかったお知らせが {unsent} 件あります",
                body="お知らせの記録で理由を確かめ、必要ならお電話でお伝えください。",
                now=bell_at,
            )
            await db.commit()
        if held_for_config:
            since = bell_at - timedelta(hours=CONFIG_BELL_INTERVAL_HOURS)
            if await last_bell_at(db, NOTIFY_CONFIG_MISSING, since) is None:
                await add_bell(
                    db,
                    type_=NOTIFY_CONFIG_MISSING,
                    title="SMS の設定が足りないため、送れていません",
                    body="管理者が SMS の接続の設定を確かめてください。",
                    now=bell_at,
                )
            await db.commit()
        if config_failed:
            since = bell_at - timedelta(hours=CONFIG_BELL_INTERVAL_HOURS)
            if await last_bell_at(db, NOTIFY_CONFIG_ERROR, since) is None:
                await add_bell(
                    db,
                    type_=NOTIFY_CONFIG_ERROR,
                    title="SMS の設定に誤りがあるため、送れていません",
                    body="管理者が SMS の設定（送る窓口と送り方）を確かめてください。",
                    now=bell_at,
                )
            await db.commit()
        return done
