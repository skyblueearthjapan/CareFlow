"""連携結果レポート (らく助 ⇄ カイポケ) のデータ組み立て — read-only。

正典 = ``docs/plans/sync-result-report-design.md``。

``build_sync_report(db, job_id)`` は ``kaipoke_jobs`` / ``kaipoke_job_items`` を読んで
純データ (dataclass) を返す。HTML 化は ``sync_report_html.render_sync_report_html``
(純関数) が担当する。DB 書込は一切しない。

明細 (``kaipoke_job_items``) はジョブ完了時に各 op 側が書く。items が無い改修前の
ジョブは ``detail_level='summary_only'`` として、``result_summary`` の件数
(apply は ``result.details[]``) だけで「明細なし版」を出す。

**「全件成功」を軽々しく言わない**のがこのモジュールの一番の責務:
結果が確定していない行 (RPA が結果を返さなかった等) は ``unresolved`` として
必ず要対応に数え、緑 (全件反映) はすべての行が success のときだけにする。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.kaipoke_job import KaipokeJob
from app.models.user import User

# op ラベル / 対象 op / 理由コード辞書の正典は lane ① の ``sync_report_items``。
# ここでフォールバックを持つと二重管理になるため、import できなければ落とす。
from app.services.kaipoke.sync_report_items import (
    OP_LABELS,
    REASON_LABELS,
    REPORTABLE_OPS,
)

# 「要対応」に必ず載せる理由コード (失敗していなくても人が目視すべき型)。
# ``no_rpa_result`` = 送ったのに RPA から結果が返らなかった行 — カイポケに入った
# のか入っていないのか不明なので、成功扱いにしては絶対にいけない。
ATTENTION_REASONS = frozenset(
    {
        "old_row_remains_duplicate",
        "add_may_have_registered",
        "add_failed_row_lost",
        "add_failed_old_row_intact",
        "delete_not_verified",
        "no_rpa_result",
    }
)

# 送信方向の op (それ以外は取込)。``report_meta.direction`` があればそちらが優先。
OUTBOUND_OPS = frozenset({"apply", "events-outbound"})

# 訪問を触らない op — 末尾の突合 (訪問の突合) は対象外にする。
EVENT_OPS = frozenset({"apply-events", "events-outbound"})

# `kaipoke_job_items.content.kind` の許容値。未知/空は無視する (誤集計を防ぐ)。
ITEM_KINDS = frozenset({"row", "event", "day", "skip", "trainee_solo"})

# 結果が確定した扱いにできる outcome。これ以外 (pending・未知の文字列) は
# ``unresolved`` として要対応に積む。
SETTLED_OUTCOMES = frozenset({"success", "excluded", "skipped"})

ACTION_LABELS = {
    "add": "追加",
    "edit": "変更",
    "delete": "削除",
    "date_change": "日付変更",
    "cancel": "取消",
    "update": "更新",
}

OUTCOME_META = {
    "success": ("成功", "ok"),
    "failed": ("失敗", "ng"),
    "skipped": ("スキップ", "warn"),
    "excluded": ("送信対象外", "muted"),
    # 送ったが RPA から結果が返らなかった行。lane ① は "unknown"、古い経路は
    # "pending" を書く。どちらも SETTLED_OUTCOMES に入れない = 成功扱いしない。
    "pending": ("結果不明", "warn"),
    "unknown": ("結果不明", "warn"),
}

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

# 1 ページに収まる日セクションの行数上限 (設計 §4)。
COMPACT_ROW_LIMIT = 14
# 表紙の要対応一覧の打ち切り行数 (設計 §1)。
ATTENTION_COVER_LIMIT = 15

_CHANGE_FIELDS = ("date", "start", "end", "staff1", "staff2", "service")


class SyncReportNotFoundError(Exception):
    """対象ジョブが存在しない (→ 404)。"""


class SyncReportUnsupportedError(Exception):
    """レポート対象外の op / 未完了ジョブ (→ 422)。"""


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


@dataclass
class JobInfo:
    id: str
    op: str
    op_label: str
    direction: str  # outbound | inbound
    status: str
    week_start: str | None = None
    week_end: str | None = None
    month: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_sec: int | None = None
    executor_name: str | None = None
    result_unknown: bool = False


@dataclass
class Summary:
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    excluded: int = 0
    # 結果が確定していない行 (pending / RPA 無応答)。緑を出さない根拠になる。
    unresolved: int = 0
    attention: int = 0


@dataclass
class ExclusionGroup:
    reason: str
    label: str
    count: int


@dataclass
class RowChange:
    date: str | None
    start: str
    end: str
    user_name: str
    action: str
    action_label: str
    outcome: str
    outcome_label: str
    outcome_tag: str
    change_text: str
    reason: str | None = None
    reason_label: str | None = None


@dataclass
class DaySection:
    date: str
    weekday: str
    label: str
    rows: list[RowChange] = field(default_factory=list)

    @property
    def compact(self) -> bool:
        return len(self.rows) <= COMPACT_ROW_LIMIT


@dataclass
class AttentionRow:
    date: str | None
    time: str
    subject: str
    what: str
    outcome_label: str
    outcome_tag: str
    reason_label: str


@dataclass
class ReplaceDay:
    date: str  # 空文字 = 日別内訳が無い改修前ジョブの「合計」行
    weekday: str
    wiped: int
    inserted: int
    sunday_skipped: bool = False


@dataclass
class ReplaceSkip:
    date: str | None
    start: str
    user_name: str
    staff_name: str
    reason: str | None
    reason_label: str


@dataclass
class TraineeSolo:
    staff_name: str
    count: int


@dataclass
class EventRow:
    date: str | None
    start: str
    end: str
    staff_name: str
    title: str
    action: str
    action_label: str
    outcome: str
    outcome_label: str
    outcome_tag: str
    change_text: str
    reason: str | None = None
    reason_label: str | None = None


@dataclass
class Verification:
    available: bool
    counts: dict[str, int] = field(default_factory=dict)
    fetched_at: datetime | None = None
    note: str | None = None


@dataclass
class SyncReport:
    job: JobInfo
    summary: Summary
    conclusion_tone: str  # green | amber | red
    conclusion_text: str
    exclusions: list[ExclusionGroup] = field(default_factory=list)
    attention: list[AttentionRow] = field(default_factory=list)
    days: list[DaySection] = field(default_factory=list)
    excluded_rows: list[RowChange] = field(default_factory=list)
    replace_days: list[ReplaceDay] = field(default_factory=list)
    skips: list[ReplaceSkip] = field(default_factory=list)
    trainee_solo: list[TraineeSolo] = field(default_factory=list)
    events: list[EventRow] = field(default_factory=list)
    verification: Verification = field(default_factory=lambda: Verification(available=False))
    detail_level: str = "full"  # full | summary_only
    reason_codes: list[tuple[str, str]] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": asdict(self.job),
            "summary": asdict(self.summary),
            "conclusion_tone": self.conclusion_tone,
            "conclusion_text": self.conclusion_text,
            "exclusions": [asdict(x) for x in self.exclusions],
            "attention": [asdict(x) for x in self.attention],
            "days": [
                {
                    "date": d.date,
                    "weekday": d.weekday,
                    "label": d.label,
                    "compact": d.compact,
                    "rows": [asdict(r) for r in d.rows],
                }
                for d in self.days
            ],
            "excluded_rows": [asdict(r) for r in self.excluded_rows],
            "replace_days": [asdict(x) for x in self.replace_days],
            "skips": [asdict(x) for x in self.skips],
            "trainee_solo": [asdict(x) for x in self.trainee_solo],
            "events": [asdict(x) for x in self.events],
            "verification": asdict(self.verification),
            "detail_level": self.detail_level,
            "reason_codes": [{"code": c, "label": lb} for c, lb in self.reason_codes],
            "generated_at": self.generated_at,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _int(src: dict[str, Any], *keys: str) -> int:
    """result_summary から件数を安全に読む (bool は数えない・文字列も許容)。"""
    for k in keys:
        v = src.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
        if isinstance(v, list):
            return len(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return 0


def _reason_label(reason: str | None, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if not reason:
        return ""
    return REASON_LABELS.get(reason, reason)


def day_label(iso: str | None) -> tuple[str, str]:
    """ISO 日付 → (曜日, '9/7（月）')。解釈できなければ ('', 元文字列)。"""
    if not iso:
        return "", "日付なし"
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return "", iso
    wd = WEEKDAY_JA[d.weekday()]
    return wd, f"{d.month}/{d.day}（{wd}）"


def _iso_from_day_number(day_num: int, week_start: date | None) -> str | None:
    """RPA details の日番号 → ISO。**対象週の中に一致する日が無ければ None**。

    月跨ぎ週 (例: 8/31〜9/6 の「31」と「1」) を正しく解くのが目的。週外の数字は
    素性が分からないので、週の月で日付をでっち上げず捨てる。
    """
    if week_start is None:
        return None
    for i in range(7):
        d = week_start + timedelta(days=i)
        if d.day == day_num:
            return d.isoformat()
    return None


def _side_parts(side: dict[str, Any], keys: list[str]) -> str:
    """変わった項目だけを「16:00 熊澤」の形に並べる。"""
    parts: list[str] = []
    if "date" in keys and side.get("date"):
        _wd, lb = day_label(_s(side.get("date")))
        parts.append(lb)
    if "start" in keys or "end" in keys:
        t = _s(side.get("start"))
        if "end" in keys and side.get("end"):
            t = f"{t}–{_s(side.get('end'))}" if t else _s(side.get("end"))
        if t:
            parts.append(t)
    for k in ("staff1", "staff2"):
        if k in keys and side.get(k):
            parts.append(_s(side.get(k)))
    if "service" in keys and side.get("service"):
        parts.append(_s(side.get("service")))
    return " ".join(parts)


def _row_desc(side: dict[str, Any]) -> str:
    return _side_parts(side, list(_CHANGE_FIELDS))


def change_text(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    """before → after を「変わった項目だけ」で表現する。"""
    if not before and not after:
        return ""
    if not before:
        return _row_desc(after or {})
    if not after:
        return _row_desc(before)
    keys = [k for k in _CHANGE_FIELDS if _s(before.get(k)) != _s(after.get(k))]
    if not keys:
        return ""
    left = _side_parts(before, keys)
    right = _side_parts(after, keys)
    if not left and not right:
        return ""
    return f"{left} → {right}"


def _outcome_meta(outcome: str) -> tuple[str, str]:
    return OUTCOME_META.get(outcome, (outcome or "結果不明", "warn"))


def _normalize_outcome(raw: str | None, item_status: str | None) -> str:
    v = (raw or item_status or "").strip()
    if v in OUTCOME_META:
        return v
    if v in ("completed", "ok", "done"):
        return "success"
    if v in ("error", "fail"):
        return "failed"
    if v in ("skip",):
        return "skipped"
    return v or "pending"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


async def _resolve_executor(db, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None
    if user.username:
        return user.username
    if user.email:
        return user.email.split("@", 1)[0]
    return None


async def _build_verification(db, week_start: date | None, op: str) -> Verification:
    """末尾の「送信後の確認」= 最新カイポケ控えとの突合 (無ければ available=False)。"""
    if op in EVENT_OPS:
        # イベントは訪問 CSV に出ないので、訪問の突合を回しても意味がない (かつ重い)。
        kind = "イベント送信" if op == "events-outbound" else "イベント取込"
        return Verification(available=False, note=f"訪問の突合は対象外（{kind}）")
    if week_start is None:
        return Verification(available=False, note="対象週が特定できないため突合を省略しました。")
    try:
        from app.services.kaipoke.reconcile_report_html import build_reconcile_report

        rep = await build_reconcile_report(db, week_start=week_start, days=7)
        fetched = [s.fetched_at for s in rep.snapshots if s.fetched_at is not None]
        counts = dict(rep.counts)
    except Exception:  # noqa: BLE001 - 突合が落ちてもレポート本体は出す
        return Verification(
            available=False,
            note="カイポケ控えとの突合を実行できませんでした"
            "（連携ページの🔄突合で取り直してください）。",
        )
    if not fetched:
        return Verification(
            available=False,
            note="カイポケの控え（スナップショット）が保存されていないため突合できません。"
            "連携ページの「差分最新化」を実行してから開き直してください。",
        )
    return Verification(available=True, counts=counts, fetched_at=max(fetched))


def _rows_from_result_details(job: KaipokeJob, week_start: date | None) -> list[RowChange]:
    """改修前の apply ジョブ (items 無し) 用: RPA result.details[] を行に起こす。"""
    result = ((job.result_summary or {}).get("result") or {}) if job.result_summary else {}
    details = result.get("details")
    if not isinstance(details, list):
        return []
    out: list[RowChange] = []
    for d in details:
        if not isinstance(d, dict):
            continue
        raw_date = d.get("date")
        iso: str | None = None
        if isinstance(raw_date, str) and "-" in raw_date:
            iso = raw_date
        elif isinstance(raw_date, str) and raw_date.strip().isdigit():
            iso = _iso_from_day_number(int(raw_date.strip()), week_start)
        elif isinstance(raw_date, int) and not isinstance(raw_date, bool):
            iso = _iso_from_day_number(raw_date, week_start)
        action = _s(d.get("action"))
        outcome = _normalize_outcome(_s(d.get("status")) or None, None)
        label, tag = _outcome_meta(outcome)
        reason = d.get("reason") or None
        out.append(
            RowChange(
                date=iso,
                start=_s(d.get("start")),
                end=_s(d.get("end")),
                user_name=_s(d.get("user") or d.get("user_name")),
                action=action,
                action_label=ACTION_LABELS.get(action, action or "—"),
                outcome=outcome,
                outcome_label=label,
                outcome_tag=tag,
                change_text="",
                reason=reason,
                reason_label=_reason_label(reason),
            )
        )
    return out


def _summary_only_counts(op: str, rs: dict[str, Any]) -> tuple[Summary, list[ReplaceDay]]:
    """items も details も無い改修前ジョブ: result_summary の件数から要約を作る。

    「対象 0 件すべてが成功」と嘘をつかないための経路。日別内訳は復元できないので
    置換系だけ「合計」行 (date='') を 1 本立てて 消した/入れた 件数を見せる。
    """
    # 件数の置き場は op ごとに違う: トップレベル (取込/置換)、"summary"
    # (events-outbound)、"result" (apply / RPA 経由)。全部畳んでから読む。
    src: dict[str, Any] = dict(rs)
    for key in ("summary", "result"):
        nested = rs.get(key)
        if isinstance(nested, dict):
            src.update(nested)

    replace_total: list[ReplaceDay] = []
    failed = _int(src, "failed")
    skipped = _int(src, "skipped")

    if op in ("apply-inbound", "smart-apply"):
        success = _int(src, "cancelled") + _int(src, "updated") + _int(src, "added")
        # smart-apply は差分 + 置換のハイブリッド。置換側の件数があれば足す。
        inserted, wiped = _int(src, "inserted"), _int(src, "wiped")
        success += inserted
        if inserted or wiped:
            replace_total.append(ReplaceDay(date="", weekday="", wiped=wiped, inserted=inserted))
    elif op == "replace-inbound":
        success = _int(src, "inserted")
        replace_total.append(
            ReplaceDay(date="", weekday="", wiped=_int(src, "wiped"), inserted=success)
        )
    elif op == "apply-events":
        success = _int(src, "added") + _int(src, "updated") + _int(src, "deleted")
    elif op == "events-outbound":
        success = _int(src, "ok")
        total = _int(src, "total")
        if not failed:
            failed = max(0, total - success - skipped)
    else:  # apply は details 経路で処理済み
        success = _int(src, "success")

    return (
        Summary(
            total=success + failed + skipped,
            success=success,
            failed=failed,
            skipped=skipped,
        ),
        replace_total,
    )


def _classify_items(
    items: list[Any],
) -> tuple[list[RowChange], list[EventRow], list[ReplaceDay], list[ReplaceSkip], list[TraineeSolo]]:
    rows: list[RowChange] = []
    events: list[EventRow] = []
    days: list[ReplaceDay] = []
    skips: list[ReplaceSkip] = []
    solo: list[TraineeSolo] = []
    for it in items:
        c = it.content if isinstance(it.content, dict) else {}
        kind = _s(c.get("kind"))
        if kind not in ITEM_KINDS:
            # 空 content / 未知の kind は黙って捨てる。既定を "row" にすると
            # 中身の無い行が明細と件数に混ざる (誤集計の温床)。
            continue
        if kind == "row":
            outcome = _normalize_outcome(c.get("outcome"), it.status)
            label, tag = _outcome_meta(outcome)
            action = _s(c.get("action"))
            reason = c.get("reason") or None
            rows.append(
                RowChange(
                    date=c.get("date"),
                    start=_s(c.get("start")),
                    end=_s(c.get("end")),
                    user_name=_s(c.get("user_name")),
                    action=action,
                    action_label=ACTION_LABELS.get(action, action or "—"),
                    outcome=outcome,
                    outcome_label=label,
                    outcome_tag=tag,
                    change_text=change_text(c.get("before"), c.get("after")),
                    reason=reason,
                    reason_label=_reason_label(reason, c.get("reason_label") or it.error_msg),
                )
            )
        elif kind == "event":
            outcome = _normalize_outcome(c.get("outcome"), it.status)
            label, tag = _outcome_meta(outcome)
            action = _s(c.get("action"))
            reason = c.get("reason") or None
            before = c.get("before")
            after = None
            if before:
                after = {"start": c.get("start"), "end": c.get("end"), "service": c.get("title")}
                before = {
                    "start": before.get("start"),
                    "end": before.get("end"),
                    "service": before.get("title"),
                }
            events.append(
                EventRow(
                    date=c.get("date"),
                    start=_s(c.get("start")),
                    end=_s(c.get("end")),
                    staff_name=_s(c.get("staff_name")),
                    title=_s(c.get("title")),
                    action=action,
                    action_label=ACTION_LABELS.get(action, action or "—"),
                    outcome=outcome,
                    outcome_label=label,
                    outcome_tag=tag,
                    change_text=change_text(before, after),
                    reason=reason,
                    reason_label=_reason_label(reason, c.get("reason_label") or it.error_msg),
                )
            )
        elif kind == "day":
            wd, _lb = day_label(c.get("date"))
            days.append(
                ReplaceDay(
                    date=_s(c.get("date")),
                    weekday=wd,
                    wiped=int(c.get("wiped") or 0),
                    inserted=int(c.get("inserted") or 0),
                    sunday_skipped=bool(c.get("sunday_skipped")),
                )
            )
        elif kind == "skip":
            reason = c.get("reason") or None
            skips.append(
                ReplaceSkip(
                    date=c.get("date"),
                    start=_s(c.get("start")),
                    user_name=_s(c.get("user_name")),
                    staff_name=_s(c.get("staff_name")),
                    reason=reason,
                    reason_label=_reason_label(reason, c.get("reason_label") or it.error_msg),
                )
            )
        elif kind == "trainee_solo":
            solo.append(
                TraineeSolo(staff_name=_s(c.get("staff_name")), count=int(c.get("count") or 0))
            )
    return rows, events, days, skips, solo


def _tone(summary: Summary, job_status: str) -> str:
    """緑は「全部 success で、要確認も結果不明も 0」のときだけ。"""
    if job_status == "failed" or summary.failed > 0:
        return "red"
    all_settled = summary.success == summary.total
    if all_settled and summary.attention == 0 and summary.unresolved == 0:
        return "green"
    return "amber"


def _conclusion(tone: str, summary: Summary, direction: str, detail_level: str) -> str:
    verb = "カイポケへの送信" if direction == "outbound" else "らく助への取り込み"
    target = "送信対象" if direction == "outbound" else "取込対象"
    if summary.total == 0 and summary.excluded == 0:
        base = f"{verb}の対象となる行はありませんでした。"
    elif summary.total == 0:
        # 全行が除外 — 「0 件すべて成功」ではなく、除外されたことを言う。
        base = f"{target}の行はなく、{summary.excluded} 件は除外されました。"
    elif tone == "green":
        base = f"{verb}は対象 {summary.total} 件すべてが成功しました。要対応はありません。"
    else:
        bits: list[str] = []
        if summary.failed:
            bits.append(f"失敗 {summary.failed} 件")
        if summary.unresolved:
            bits.append(f"結果不明 {summary.unresolved} 件")
        if summary.skipped:
            bits.append(f"スキップ {summary.skipped} 件")
        if not bits and summary.attention:
            bits.append(f"要確認 {summary.attention} 件")
        detail = "・".join(bits) if bits else "確認が必要な項目"
        head = "で" if tone == "red" else "は完了しましたが、"
        # 要対応一覧が空 (例: outbound のスキップだけで緑を外れた) のに
        # 「要対応一覧を見ろ」と書くと、空の表を探させることになる。
        where = "「要対応一覧」と明細" if summary.attention else "明細"
        base = (
            f"{verb}{head}{detail} があります。"
            f"{where}で対象を確認してください"
            f"（成功 {summary.success} / 対象 {summary.total}）。"
        )
    if detail_level == "summary_only":
        base += "（このジョブは改修前のため、行単位の明細はありません）"
    return base


async def build_sync_report(db, job_id: uuid.UUID | str, *, verify: bool = True) -> SyncReport:
    """ジョブ 1 件の連携結果レポートを組み立てる (read-only)。

    ``verify=False`` で末尾の突合 (``build_reconcile_report``・重い) を省略する。
    """
    try:
        jid = job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise SyncReportNotFoundError(str(job_id)) from exc

    job = await db.scalar(
        select(KaipokeJob).where(KaipokeJob.id == jid).options(selectinload(KaipokeJob.items))
    )
    if job is None:
        raise SyncReportNotFoundError(str(job_id))

    params = job.params or {}
    op = _s(params.get("op"))
    if op not in REPORTABLE_OPS:
        raise SyncReportUnsupportedError(f"op={op or '(なし)'} はレポート対象外です")
    if job.status not in ("completed", "failed"):
        raise SyncReportUnsupportedError(f"ジョブが未完了です (status={job.status})")

    summary_meta = job.result_summary or {}
    meta = summary_meta.get("report_meta") or {}
    direction = meta.get("direction") or (
        "outbound" if (op in OUTBOUND_OPS or job.job_type == "push") else "inbound"
    )
    op_label = meta.get("op_label") or OP_LABELS.get(op, op)
    executor = meta.get("executor_name") or await _resolve_executor(db, job.created_by_user_id)

    # --- 期間 (日番号の解決にも使うので先に決める) --------------------------
    month = params.get("month") if isinstance(params.get("month"), str) else None
    week_start: date | None = job.week_start
    if isinstance(params.get("week_start"), str):
        try:
            week_start = date.fromisoformat(params["week_start"])
        except ValueError:
            pass
    week_end = week_start + timedelta(days=6) if week_start else None

    items = sorted(job.items or [], key=lambda i: i.seq)
    rows, events, replace_days, skips, solo = _classify_items(items)
    detail_level = "full" if items else "summary_only"
    seeded: Summary | None = None
    if not items:
        if op == "apply":
            rows = _rows_from_result_details(job, week_start)
        if not rows:
            seeded, extra_days = _summary_only_counts(op, summary_meta)
            replace_days = replace_days + extra_days

    # --- 集計 -------------------------------------------------------------
    excluded_rows = [r for r in rows if r.outcome == "excluded"]
    live_rows = [r for r in rows if r.outcome != "excluded"]
    live_events = [e for e in events if e.outcome != "excluded"]
    excluded_events = [e for e in events if e.outcome == "excluded"]
    live = [*live_rows, *live_events]

    if seeded is not None:
        summary = seeded
    else:
        summary = Summary(
            total=len(live),
            success=sum(1 for x in live if x.outcome == "success"),
            failed=sum(1 for x in live if x.outcome == "failed"),
            skipped=sum(1 for x in live if x.outcome == "skipped"),
            unresolved=sum(
                1 for x in live if x.outcome not in SETTLED_OUTCOMES and x.outcome != "failed"
            ),
        )
    summary.excluded = len(excluded_rows) + len(excluded_events)

    # --- 要対応 -----------------------------------------------------------
    def _needs_attention(outcome: str, reason: str | None) -> bool:
        # 「確定した無事」以外はすべて要対応 — pending/未知を握り潰さない。
        if outcome not in SETTLED_OUTCOMES:
            return True
        if reason and reason in ATTENTION_REASONS:
            return True
        return direction == "inbound" and outcome == "skipped"

    attention: list[AttentionRow] = []
    for r in live_rows:
        if _needs_attention(r.outcome, r.reason):
            attention.append(
                AttentionRow(
                    date=r.date,
                    time=r.start,
                    subject=r.user_name,
                    what=r.action_label,
                    outcome_label=r.outcome_label,
                    outcome_tag=r.outcome_tag,
                    reason_label=r.reason_label or "",
                )
            )
    for e in live_events:
        if _needs_attention(e.outcome, e.reason):
            attention.append(
                AttentionRow(
                    date=e.date,
                    time=e.start,
                    subject=f"{e.staff_name}／{e.title}".strip("／"),
                    what=e.action_label,
                    outcome_label=e.outcome_label,
                    outcome_tag=e.outcome_tag,
                    reason_label=e.reason_label or "",
                )
            )
    for sk in skips:
        attention.append(
            AttentionRow(
                date=sk.date,
                time=sk.start,
                subject=sk.user_name or sk.staff_name,
                what="取り込めなかった行",
                outcome_label="スキップ",
                outcome_tag="warn",
                reason_label=sk.reason_label or "",
            )
        )
    for ts in solo:
        attention.append(
            AttentionRow(
                date=None,
                time="",
                subject=ts.staff_name,
                what="新人単独",
                outcome_label="要確認",
                outcome_tag="warn",
                reason_label=f"新人が単独で登録されています（{ts.count} 件）",
            )
        )
    attention.sort(key=lambda a: (a.date or "9999-99-99", a.time, a.subject))
    summary.attention = len(attention)

    # --- 除外の内訳 -------------------------------------------------------
    grouped: dict[str, int] = {}
    for reason in [x.reason or "unknown" for x in (*excluded_rows, *excluded_events)]:
        grouped[reason] = grouped.get(reason, 0) + 1
    exclusions = [
        ExclusionGroup(reason=k, label=_reason_label(k) or "理由不明", count=v)
        for k, v in sorted(grouped.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # --- 日ごとの明細 -----------------------------------------------------
    by_day: dict[str, list[RowChange]] = {}
    for r in live_rows:
        by_day.setdefault(r.date or "", []).append(r)
    days: list[DaySection] = []
    for iso in sorted(by_day, key=lambda s: s or "9999-99-99"):
        wd, lb = day_label(iso or None)
        section = DaySection(date=iso, weekday=wd, label=lb)
        section.rows = sorted(by_day[iso], key=lambda r: (r.start, r.user_name))
        days.append(section)

    duration = None
    if job.started_at and job.completed_at:
        st, ct = job.started_at, job.completed_at
        if st.tzinfo is None:
            st = st.replace(tzinfo=UTC)
        if ct.tzinfo is None:
            ct = ct.replace(tzinfo=UTC)
        duration = max(0, int((ct - st).total_seconds()))

    info = JobInfo(
        id=str(job.id),
        op=op,
        op_label=op_label,
        direction=direction,
        status=job.status,
        week_start=week_start.isoformat() if week_start else None,
        week_end=week_end.isoformat() if week_end else None,
        month=month,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_sec=duration,
        executor_name=executor,
        result_unknown=bool(summary_meta.get("result_unknown")),
    )

    verification = (
        await _build_verification(db, week_start, op)
        if verify
        else Verification(available=False, note="突合は省略されました（verify=false）。")
    )

    tone = _tone(summary, job.status)

    # 補足ページの用語集は「実際に出た理由コード」だけを載せる。
    used: dict[str, str] = {}
    for reason, label in (
        [(r.reason, r.reason_label) for r in rows]
        + [(e.reason, e.reason_label) for e in events]
        + [(s.reason, s.reason_label) for s in skips]
    ):
        if reason:
            used.setdefault(reason, label or _reason_label(reason))
    reason_codes = sorted(used.items())

    return SyncReport(
        job=info,
        summary=summary,
        conclusion_tone=tone,
        conclusion_text=_conclusion(tone, summary, direction, detail_level),
        exclusions=exclusions,
        attention=attention,
        days=days,
        excluded_rows=excluded_rows,
        replace_days=replace_days,
        skips=skips,
        trainee_solo=solo,
        events=events,
        verification=verification,
        detail_level=detail_level,
        reason_codes=reason_codes,
        generated_at=datetime.now(UTC),
    )
