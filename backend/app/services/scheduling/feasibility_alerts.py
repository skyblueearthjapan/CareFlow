"""実現性の警告 — カードの印と、カイポケ取り込み後の確認 (読み取り + 通知).

設計 = ``docs/plans/schedule-feasibility-alerts-design-2026-11-01.md`` (第 1 段)。
判定そのものは ``feasibility_check`` (純粋関数 + SELECT のみ) をそのまま使い、ここでは

  * 指摘を「赤 (hard) / 黄 (soft)」だけに絞る (参考 = info は帯・印・ベルに出さない)
  * 人が読む理由の文を作る (画面ごとに言い方がずれないよう backend で 1 か所)
  * 取り込みの前後を比べて新しく出た指摘を求め、取り込みの記録に残しベルで知らせる

を受け持つ。カードの印は保存せず毎回計算する (§3-2)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scheduling.feasibility_check import (
    KIND_IMPOSSIBLE,
    KIND_NG_WEEKDAY,
    KIND_ON_LEAVE,
    KIND_OVERLAP,
    KIND_PAIR_OVER,
    KIND_PAIR_SHORT,
    KIND_STAFF_NOT_MIRRORED,
    KIND_TIGHT,
    ROLE_ACCOMPANY,
    SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
    FeasibilityReport,
    Finding,
    build_feasibility_report_for_dates,
)

logger = logging.getLogger(__name__)

# 帯・印・ベルに出す区分 (参考 = info は出さない・PO 決定 2026-11-01 夕)。
ALERT_TIERS: frozenset[str] = frozenset({"hard", "soft"})
# ベル (notifications) の種類と参照。既存の kaipoke_import_result とは別の参照キーにする
# (同じキーだと部分 UNIQUE で片方が出なくなるため・設計 §3-3)。
IMPORT_NOTIFY_TYPE = "feasibility_after_import"
IMPORT_REFERENCE_TYPE = "feasibility_import"
# ベルの本文に出す明細の上限 (欄が 3 行で切れるため)。
BELL_DETAIL_LIMIT = 3
# 印の API で一度に見る期間の上限 (日)。
MAX_MARK_DAYS = 7

_TIER_ORDER = {"hard": 0, "soft": 1, "info": 2}


def fingerprint(f: Finding) -> str:
    """同じ指摘かどうかのキー (設計 §3-3)。

    訪問 ID は使わない (置換の取り込みで入れ直すと ID が変わり、毎回「新しい」になるため)。
    """
    return "|".join(
        [f.kind, f.staff_key, f.day.isoformat(), f.at, f.to_patient_id, f.from_patient_id]
    )


def _to_min(hm: str) -> int | None:
    try:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _who(name: str, kind: str) -> str:
    """直前・該当の予定の呼び方 (訪問は「〜様」・イベントはそのまま)。"""
    if not name:
        return ""
    return f"{name}様" if kind == "visit" else name


def _km(km: float | None) -> str:
    return f"（直線 {km:.1f} km）" if km is not None else ""


def reason_text(f: Finding, *, buffer_min: int) -> str:
    """印・帯・ベルに出す理由の文 (例「前の訪問（〜11:50）から 10 分・移動に 11 分（3.6 km）」)。"""
    frm = _who(f.frm, f.frm_kind)
    frm_label = f"{frm} " if frm else ""
    if f.kind == KIND_IMPOSSIBLE:
        text = (
            f"前の予定（{frm_label}〜{f.frm_end}）から {f.gap_min} 分しかありません。"
            f"移動に約 {f.need_min} 分かかります{_km(f.km)}。"
        )
    elif f.kind == KIND_OVERLAP:
        text = (
            f"前の予定（{frm_label}{f.frm_start}〜{f.frm_end}）と "
            f"{abs(f.gap_min or 0)} 分重なっています。"
        )
    elif f.kind == KIND_PAIR_OVER:
        text = "同じ建物で、同じ時刻に 3 名以上を担当しています。"
    elif f.kind == KIND_TIGHT:
        need = f.need_min or 0
        gap = f.gap_min or 0
        short = need + buffer_min - gap
        base = (
            f"同じ建物の 2 名の訪問の占有（〜{f.occupied_until}）から {gap} 分です。"
            if f.occupied_until
            else f"前の予定（{frm_label}〜{f.frm_end}）から {gap} 分です。"
        )
        text = f"{base}移動 {need} 分＋余裕 {buffer_min} 分に {short} 分足りません{_km(f.km)}。"
    elif f.kind == KIND_PAIR_SHORT:
        text = (
            f"同じ建物の 2 名（{f.frm}）は {SAME_ADDRESS_PAIR_MIN_OCCUPANCY} 分みておく決まりです"
            f"（〜{f.occupied_until}）。{f.at} に次が始まります。"
        )
        real_end = _to_min(f.frm_end)
        start = _to_min(f.at)
        if real_end is not None and start is not None:
            text += f"実際の終わりは {f.frm_end} で、間は {start - real_end} 分です"
            text += f"{_km(f.km)}。" if f.km is not None else "。"
    elif f.kind == KIND_ON_LEAVE:
        # frm = 「〇〇さんは午前休です（…）」。括弧の中は A4 向けの案内なので外す。
        text = f"{f.frm.split('（', 1)[0]}。職員スケジュールで担当を合わせてください。"
    elif f.kind in (KIND_NG_WEEKDAY, KIND_STAFF_NOT_MIRRORED):
        text = f.frm if f.frm.endswith("。") else f"{f.frm}。"
    else:
        text = f"{f.kind}：{f.frm}" if f.frm else f.kind
    if f.role == ROLE_ACCOMPANY:
        text = f"同行の{f.staff}さん：{text}"
    return text


def alert_findings(report: FeasibilityReport) -> list[Finding]:
    """帯・印・ベルに出す指摘 (赤・黄) を、赤→黄・日・時刻の順で返す。"""
    fs = [f for f in report.findings if f.tier in ALERT_TIERS]
    fs.sort(key=lambda f: (_TIER_ORDER.get(f.tier, 9), f.day, f.at, f.staff, f.to))
    return fs


def alert_item(f: Finding, *, buffer_min: int) -> dict[str, Any]:
    """指摘 1 件 → API / 取り込みの記録に載せる素朴な辞書。"""
    return {
        "tier": f.tier,
        "kind": f.kind,
        "day": f.day.isoformat(),
        "at": f.at,
        "staff": f.staff,
        "patient_name": f.to,
        "reason": reason_text(f, buffer_min=buffer_min),
        "visit_ids": list(f.to_visit_ids),
        "fingerprint": fingerprint(f),
    }


def _week_chunks(date_from: date, date_to: date) -> list[tuple[date, date]]:
    """期間を ISO 週 (月〜日) ごとに分ける。"""
    chunks: list[tuple[date, date]] = []
    cur = date_from
    while cur <= date_to:
        week_end = cur + timedelta(days=6 - cur.weekday())
        end = min(week_end, date_to)
        chunks.append((cur, end))
        cur = end + timedelta(days=1)
    return chunks


async def build_marks(
    db: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    office_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """期間の印 (訪問 ID ごと) と一覧・件数を作る (read-only)。"""
    items: list[dict[str, Any]] = []
    unassigned = 0
    for start, end in _week_chunks(date_from, date_to):
        report = await build_feasibility_report_for_dates(
            db, date_from=start, date_to=end, office_id=office_id
        )
        unassigned += report.unassigned_visit_count
        buffer_min = report.config.visit_buffer_min
        items.extend(alert_item(f, buffer_min=buffer_min) for f in alert_findings(report))
    items.sort(key=lambda x: (_TIER_ORDER.get(x["tier"], 9), x["day"], x["at"], x["staff"]))
    marks: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        for vid in it["visit_ids"]:
            marks.setdefault(vid, []).append(
                {"tier": it["tier"], "kind": it["kind"], "reason": it["reason"]}
            )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "hard_count": sum(1 for x in items if x["tier"] == "hard"),
        "soft_count": sum(1 for x in items if x["tier"] == "soft"),
        "unassigned_count": unassigned,
        "items": items,
        "marks": marks,
    }


# ---------------------------------------------------------------------------
# カイポケ取り込みの前後比較 (設計 §3-1 (A))
# ---------------------------------------------------------------------------


async def _week_alerts(
    db: AsyncSession, week_start: date
) -> tuple[FeasibilityReport, list[Finding]]:
    report = await build_feasibility_report_for_dates(
        db, date_from=week_start, date_to=week_start + timedelta(days=6)
    )
    return report, alert_findings(report)


async def collect_import_baseline(db: AsyncSession, week_start: date) -> set[str] | None:
    """取り込み前の指紋 (赤・黄) を集める。失敗しても取り込みは止めない (None を返す)。"""
    try:
        async with db.begin_nested():  # 失敗しても取り込みのトランザクションを壊さない
            _report, fs = await _week_alerts(db, week_start)
        return {fingerprint(f) for f in fs}
    except Exception:  # noqa: BLE001 - 判定の失敗で取り込みを止めない (設計 §3-1)
        logger.exception("feasibility baseline failed (week_start=%s)", week_start)
        return None


async def _already_notified(db: AsyncSession, week_start: date, job_id: uuid.UUID) -> set[str]:
    """同じ週のこれまでの取り込みで知らせた指紋 (同じ指摘を何度も知らせない)。"""
    from app.models.kaipoke_job import KaipokeJob

    rows = (
        await db.scalars(
            select(KaipokeJob.result_summary).where(
                KaipokeJob.week_start == week_start,
                KaipokeJob.job_type == "fetch",
                KaipokeJob.status == "completed",
                KaipokeJob.id != job_id,
            )
        )
    ).all()
    out: set[str] = set()
    for summary in rows:
        feas = (summary or {}).get("feasibility") if isinstance(summary, dict) else None
        if isinstance(feas, dict):
            out.update(str(x) for x in feas.get("notified") or [])
    return out


def _md(d: str) -> str:
    y, m, dd = d.split("-")
    return f"{int(m)}/{int(dd)}"


def _bell_text(week_start: date, new_items: list[dict[str, Any]]) -> tuple[str, str]:
    hard = sum(1 for x in new_items if x["tier"] == "hard")
    soft = sum(1 for x in new_items if x["tier"] == "soft")
    days = sorted({x["day"] for x in new_items})
    title = f"取り込み後の確認：無理 {hard} 件・注意 {soft} 件（{'・'.join(_md(d) for d in days)}）"
    lines = [
        f"週 {_md(week_start.isoformat())} の取り込みで、間に合わない訪問などが新しく見つかりました。"
    ]
    for x in new_items[:BELL_DETAIL_LIMIT]:
        who = f"{x['patient_name']}様" if x["visit_ids"] else x["patient_name"]
        lines.append(f"{_md(x['day'])} {x['staff']} {x['at']} {who} … {x['reason']}")
    rest = len(new_items) - BELL_DETAIL_LIMIT
    if rest > 0:
        lines.append(f"ほか {rest} 件はスケジュール画面の印で確かめてください。")
    return title[:200], "\n".join(lines)


async def record_import_alerts(
    db: AsyncSession,
    *,
    week_start: date,
    job: Any,
    before: set[str] | None,
) -> dict[str, Any]:
    """取り込み後に判定し、新しく出た赤・黄を記録してベルで知らせる (commit は呼び出し側)。

    ``job`` は flush 済みの ``KaipokeJob``。結果は ``job.result_summary["feasibility"]`` にも
    残す。判定が失敗しても例外は外へ出さない (取り込みを止めない・ログだけ)。
    """
    from app.models.notification import Notification
    from app.services.checkin.notify import _active_admin_manager_users

    failed = {
        "ok": False,
        "hard_count": 0,
        "soft_count": 0,
        "unassigned_count": 0,
        "items": [],
        "notified": [],
        "message": "取り込み後の確認ができませんでした（取り込みは終わっています）",
    }
    result: dict[str, Any]
    if before is None:
        result = failed
    else:
        try:
            async with db.begin_nested():
                report, fs = await _week_alerts(db, week_start)
                buffer_min = report.config.visit_buffer_min
                notified_before = await _already_notified(db, week_start, job.id)
                new_items = [
                    alert_item(f, buffer_min=buffer_min) for f in fs if fingerprint(f) not in before
                ]
                to_notify = [x for x in new_items if x["fingerprint"] not in notified_before]
                if to_notify:
                    title, body = _bell_text(week_start, to_notify)
                    data = {
                        "week_start": week_start.isoformat(),
                        "dates": sorted({x["day"] for x in to_notify}),
                        "hard": sum(1 for x in to_notify if x["tier"] == "hard"),
                        "soft": sum(1 for x in to_notify if x["tier"] == "soft"),
                        "job_id": str(job.id),
                    }
                    for u in await _active_admin_manager_users(db):
                        db.add(
                            Notification(
                                user_id=u.id,
                                type=IMPORT_NOTIFY_TYPE,
                                title=title,
                                body=body,
                                reference_type=IMPORT_REFERENCE_TYPE,
                                reference_id=job.id,
                                data=data,
                            )
                        )
                    await db.flush()
            result = {
                "ok": True,
                "hard_count": sum(1 for x in new_items if x["tier"] == "hard"),
                "soft_count": sum(1 for x in new_items if x["tier"] == "soft"),
                "unassigned_count": report.unassigned_visit_count,
                "items": new_items,
                "notified": [x["fingerprint"] for x in to_notify],
                "message": None,
            }
        except Exception:  # noqa: BLE001 - 判定の失敗で取り込みを止めない (設計 §3-1)
            logger.exception("feasibility after import failed (week_start=%s)", week_start)
            result = failed
    # JSONB の変更検知のため辞書ごと入れ替える。
    job.result_summary = {**(job.result_summary or {}), "feasibility": result}
    return result


__all__ = [
    "ALERT_TIERS",
    "IMPORT_NOTIFY_TYPE",
    "IMPORT_REFERENCE_TYPE",
    "alert_findings",
    "alert_item",
    "build_marks",
    "collect_import_baseline",
    "fingerprint",
    "reason_text",
    "record_import_alerts",
]
