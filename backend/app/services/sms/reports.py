"""お知らせの記録: 月のまとめ・患者様ごと・気になる番号・Excel.

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-4・§5-5、
``docs/plans/sms-notify-api-contract-2026-11-16.md`` §6〜§10。

集計用の表は作らず、その場で数える (設計 §6)。
* 月は「作った日 (日本時間)」で数える。
* 通数 = 照会の ``split_num`` があればそれ、無ければ見込みの ``segments_est``。
  送っていない行 (``skipped``) は通数・キャリアに数えない。
* 見込みの金額 = 届いた分の通数 × 11 円。
* 届いた割合 = 届いた ÷ (届いた + 届かなかった + 分からない)。分母が 0 なら None。
"""

from __future__ import annotations

import io
import re
import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_contact import PatientContact
from app.models.sms import PatientNoticeEvent, SmsMessage
from app.models.user import User
from app.services.contacts import RELATION_LABEL
from app.services.sms import composer, labels
from app.services.sms.notices import JST, SmsNoticeError, aware, mask_phone

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PENDING_STATUSES = ("queued", "sending", "accepted")
#: 届かなかった理由が記録されていないときの印と言葉 (月のまとめ)。
UNKNOWN_REASON: str = "unknown"
UNKNOWN_REASON_LABEL: str = "理由は分かりません"
#: 患者様の行が見つからないとき (消された方) の名前。
MISSING_PATIENT_NAME: str = "（削除された方）"


@dataclass(frozen=True)
class MonthRange:
    label: str
    start: datetime
    end: datetime


def month_range(month: str | None, now: datetime) -> MonthRange:
    """``YYYY-MM`` (日本時間の月) → UTC の [始まり, 終わり)。省略はいまの月."""
    if month is None:
        jst = now.astimezone(JST)
        y, m = jst.year, jst.month
    else:
        if not _MONTH_RE.match(month):
            raise SmsNoticeError(422, "月は YYYY-MM の形で指定してください")
        y, m = int(month[:4]), int(month[5:])
    start = datetime.combine(date(y, m, 1), time(0), tzinfo=JST)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = datetime.combine(date(ny, nm, 1), time(0), tzinfo=JST)
    return MonthRange(f"{y:04d}-{m:02d}", start.astimezone(UTC), end.astimezone(UTC))


def segments_of(m: SmsMessage) -> int:
    return int(m.split_num) if m.split_num else int(m.segments_est or 0)


def delivered_rate(delivered: int, failed: int, unknown: int) -> float | None:
    denom = delivered + failed + unknown
    return None if denom == 0 else delivered / denom


async def month_messages(db: AsyncSession, rng: MonthRange) -> list[SmsMessage]:
    return list(
        (
            await db.scalars(
                select(SmsMessage)
                .where(SmsMessage.created_at >= rng.start, SmsMessage.created_at < rng.end)
                .order_by(SmsMessage.created_at, SmsMessage.id)
            )
        ).all()
    )


async def month_change_events(db: AsyncSession, rng: MonthRange) -> list[PatientNoticeEvent]:
    return list(
        (
            await db.scalars(
                select(PatientNoticeEvent).where(
                    PatientNoticeEvent.kind == "change",
                    PatientNoticeEvent.told_at >= rng.start,
                    PatientNoticeEvent.told_at < rng.end,
                )
            )
        ).all()
    )


def _fail_key(m: SmsMessage) -> str | None:
    return m.fail_reason_raw or m.error_code


async def build_summary(db: AsyncSession, rng: MonthRange) -> dict[str, Any]:
    msgs = await month_messages(db, rng)
    status = Counter(m.status for m in msgs)
    sent = [m for m in msgs if m.status != "skipped"]
    delivered = [m for m in msgs if m.status == "delivered"]

    by_kind = []
    for kind in ("week", "change"):
        mine = [m for m in msgs if m.kind == kind]
        by_kind.append(
            {
                "kind": kind,
                "messages": len(mine),
                "segments": sum(segments_of(m) for m in mine if m.status != "skipped"),
                "est_yen": sum(segments_of(m) for m in mine if m.status == "delivered")
                * composer.YEN_PER_SEGMENT,
            }
        )

    carriers: dict[str, Counter[str]] = defaultdict(Counter)
    for m in sent:
        c = carriers[(m.carrier or "unknown").strip() or "unknown"]
        c["messages"] += 1
        c[m.status] += 1
    by_carrier = [
        {
            "carrier": k,
            "messages": v["messages"],
            "delivered": v["delivered"],
            "failed": v["failed"],
        }
        for k, v in sorted(carriers.items(), key=lambda kv: (-kv[1]["messages"], kv[0]))
    ]

    reasons = Counter(_fail_key(m) or UNKNOWN_REASON for m in msgs if m.status == "failed")
    by_fail_reason = [
        {
            "reason": r,
            "label": UNKNOWN_REASON_LABEL if r == UNKNOWN_REASON else labels.fail_reason_label(r),
            "count": n,
        }
        for r, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    seg_buckets = Counter(
        "3+" if segments_of(m) >= 3 else str(max(1, segments_of(m))) for m in sent
    )
    by_segments = [{"segments": k, "messages": seg_buckets.get(k, 0)} for k in ("1", "2", "3+")]

    events = await month_change_events(db, rng)
    minutes = [
        (aware(e.told_at) - aware(e.detected_at)).total_seconds() / 60  # type: ignore[operator]
        for e in events
        if e.detected_at is not None
    ]
    channels = Counter(e.channel for e in events)
    changes = {
        "count": len(events),
        "urgent_count": sum(1 for e in events if e.urgent),
        "median_minutes_to_tell": round(statistics.median(minutes)) if minutes else None,
        "by_channel": [
            {"channel": ch, "count": channels.get(ch, 0)} for ch in ("sms", "phone", "none")
        ],
    }

    weeks: dict[date, Counter[str]] = defaultdict(Counter)
    for m in msgs:
        weeks[m.week_start]["messages"] += 1
        weeks[m.week_start][m.status] += 1
    by_week = [
        {
            "week_start": w.isoformat(),
            "messages": c["messages"],
            "delivered": c["delivered"],
            "failed": c["failed"],
        }
        for w, c in sorted(weeks.items())
    ]

    return {
        "month": rng.label,
        "totals": {
            "messages": len(msgs),
            "delivered": status["delivered"],
            "failed": status["failed"],
            "unknown": status["unknown"],
            "pending": sum(status[s] for s in PENDING_STATUSES),
            "skipped": status["skipped"],
            "delivered_rate": delivered_rate(
                status["delivered"], status["failed"], status["unknown"]
            ),
            "segments": sum(segments_of(m) for m in sent),
            "est_yen": sum(segments_of(m) for m in delivered) * composer.YEN_PER_SEGMENT,
        },
        "by_kind": by_kind,
        "by_carrier": by_carrier,
        "by_fail_reason": by_fail_reason,
        "by_segments": by_segments,
        "changes": changes,
        "by_week": by_week,
    }


async def _patient_names(db: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, Patient]:
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    return {
        p.id: p for p in (await db.scalars(select(Patient).where(Patient.id.in_(wanted)))).all()
    }


async def build_patients_summary(db: AsyncSession, rng: MonthRange) -> dict[str, Any]:
    msgs = await month_messages(db, rng)
    events = [e for e in await month_change_events(db, rng) if e.channel == "phone"]
    per: dict[uuid.UUID, dict[str, Any]] = {}

    def row(pid: uuid.UUID) -> dict[str, Any]:
        return per.setdefault(
            pid,
            {
                "messages": 0,
                "segments": 0,
                "delivered": 0,
                "failed": 0,
                "last_delivered_at": None,
                "told_by_phone": 0,
            },
        )

    for m in msgs:
        if m.patient_id is None:
            continue
        r = row(m.patient_id)
        r["messages"] += 1
        if m.status != "skipped":
            r["segments"] += segments_of(m)
        if m.status == "delivered":
            r["delivered"] += 1
            at = aware(m.status_decided_at) or aware(m.created_at)
            if at is not None and (r["last_delivered_at"] is None or at > r["last_delivered_at"]):
                r["last_delivered_at"] = at
        elif m.status == "failed":
            r["failed"] += 1
    for e in events:
        row(e.patient_id)["told_by_phone"] += 1

    patients = await _patient_names(db, set(per))
    items = [
        {
            "patient_id": pid,
            "patient_name": patients[pid].name if pid in patients else MISSING_PATIENT_NAME,
            **vals,
        }
        for pid, vals in per.items()
    ]
    items.sort(
        key=lambda x: (
            (patients[x["patient_id"]].kana or patients[x["patient_id"]].name or "")
            if x["patient_id"] in patients
            else "",
        )
    )
    return {"month": rng.label, "items": items}


async def build_attention(db: AsyncSession) -> dict[str, Any]:
    """気になる番号 (今の連絡先にある番号だけ・最後に届いた後の分だけを見る)."""
    contacts = (
        await db.execute(
            select(PatientContact.patient_id, PatientContact.phone).where(
                PatientContact.phone.is_not(None)
            )
        )
    ).all()
    current = {(pid, phone) for pid, phone in contacts}
    if not current:
        return {"items": []}
    msgs = (
        await db.scalars(
            select(SmsMessage)
            .where(
                SmsMessage.patient_id.in_(list({pid for pid, _ in current})),
                SmsMessage.phone.is_not(None),
                SmsMessage.status.in_(("delivered", "failed", "unknown", "accepted")),
            )
            .order_by(SmsMessage.created_at, SmsMessage.id)
        )
    ).all()
    groups: dict[tuple[uuid.UUID, str], list[SmsMessage]] = defaultdict(list)
    for m in msgs:
        key = (m.patient_id, m.phone)
        if key in current:
            groups[key].append(m)  # type: ignore[index]

    def when(m: SmsMessage) -> datetime | None:
        return aware(m.status_decided_at) or aware(m.created_at)

    found: list[tuple[uuid.UUID, str, str, str | None, int, datetime | None]] = []
    for (pid, phone), rows in groups.items():
        last_ok = max((i for i, m in enumerate(rows) if m.status == "delivered"), default=-1)
        after = rows[last_ok + 1 :]
        if not after:
            continue
        relation = rows[-1].relation
        decided = [m for m in after if m.status in ("delivered", "failed")]
        streak = 0
        for m in reversed(decided):
            if m.status != "failed":
                break
            streak += 1
        if streak >= 2:
            last = next(m for m in reversed(decided) if m.status == "failed")
            found.append((pid, phone, "repeated_failure", relation, streak, when(last)))
        unused = [m for m in after if (m.carrier or "").lower() == labels.NOT_IN_USE_CARRIER]
        if unused:
            found.append((pid, phone, "not_in_use", relation, len(unused), when(unused[-1])))
        blocked = [
            m
            for m in after
            if m.status == "failed" and (m.fail_reason_raw or "").upper() in labels.BLOCKED_REASONS
        ]
        if blocked:
            found.append((pid, phone, "blocked", relation, len(blocked), when(blocked[-1])))

    patients = await _patient_names(db, {f[0] for f in found})
    found.sort(key=lambda f: f[5] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return {
        "items": [
            {
                "patient_id": pid,
                "patient_name": (patients[pid].name if pid in patients else MISSING_PATIENT_NAME),
                "relation_label": RELATION_LABEL.get(rel, rel) if rel else None,
                "phone_last4": mask_phone(phone),
                "kind": kind,
                "kind_label": labels.ATTENTION_KIND_LABEL[kind],
                "count": count,
                "last_at": last_at,
            }
            for pid, phone, kind, rel, count, last_at in found
        ]
    }


EXPORT_HEADERS: Sequence[str] = (
    "日時（日本時間）",
    "患者様",
    "続柄",
    "番号（末尾4桁）",
    "種類",
    "状態",
    "通数",
    "キャリア",
    "届かなかった理由",
    "送った人",
)


async def build_export(db: AsyncSession, rng: MonthRange) -> bytes:
    """その月の 1 通ごとの一覧 (本文は入れない)."""
    msgs = await month_messages(db, rng)
    patients = await _patient_names(db, {m.patient_id for m in msgs})
    user_ids = [u for u in {m.created_by_user_id for m in msgs} if u is not None]
    users = (
        {
            u.id: (u.display_name or u.username or u.email)
            for u in (await db.scalars(select(User).where(User.id.in_(user_ids)))).all()
        }
        if user_ids
        else {}
    )
    wb = Workbook()
    ws = wb.active
    ws.title = f"お知らせ {rng.label}"
    ws.append(list(EXPORT_HEADERS))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for m in msgs:
        created = aware(m.created_at)
        ws.append(
            [
                created.astimezone(JST).strftime("%Y-%m-%d %H:%M") if created else "",
                patients[m.patient_id].name if m.patient_id in patients else "",
                RELATION_LABEL.get(m.relation, m.relation) if m.relation else "",
                mask_phone(m.phone) or "",
                labels.KIND_LABEL.get(m.kind, m.kind),
                labels.status_label(m.status, m.skip_reason),
                segments_of(m) if m.status != "skipped" else 0,
                m.carrier or "",
                labels.fail_reason_label(_fail_key(m)) if m.status == "failed" else "",
                users.get(m.created_by_user_id, "") if m.created_by_user_id else "",
            ]
        )
    widths = (18, 16, 10, 12, 16, 30, 6, 10, 36, 20)
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
