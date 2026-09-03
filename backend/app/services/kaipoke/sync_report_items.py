"""連携結果レポートの明細 (``kaipoke_job_items``) を組み立てて保存する。

設計書: ``docs/plans/sync-result-report-design.md`` §2。

今日の連携ジョブは ``KaipokeJob.result_summary`` に**件数しか**残さないため、
後日「どの行が失敗したのか」を再現できない。本モジュールは実書込を伴う 6 op
(``REPORTABLE_OPS``) の**行単位の結果**を ``kaipoke_job_items`` へ書き、レポート
生成 (lane ②: ``sync_report.py``) が後から読めるようにする。

``content`` (JSONB) の形はレポート側と共有する契約。共通キー::

    {"kind": "row"|"day"|"skip"|"trainee_solo"|"event",
     "direction": "outbound"|"inbound"}

``KaipokeJobItem.status`` = outcome 文字列、``error_msg`` = 人間向けの理由
(``reason_label``)、``seq`` = (日付, 開始時刻, 利用者) 順の 1..n。

本モジュールは **commit しない** (呼び出し側のトランザクションに乗る)。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select

from app.models.kaipoke_job import KaipokeJob, KaipokeJobItem
from app.services.kaipoke.master_reconcile import normalize_person_name

# --- 辞書 (lane ②/③ が import する。名前を変えないこと) --------------------

#: 機械コード → 人間向けの日本語。未知のコードは原文をそのままラベルにする。
REASON_LABELS: dict[str, str] = {
    # 送信前に除外した理由 (らく助側の判断)
    "past": "過去日（実績保護のため送信対象外）",
    "unassigned": "担当なし（先に担当を付ける）",
    "rpa_unsupported": "RPA が准看/一般の登録に未対応",
    # RPA が返す失敗理由 (GAS_APPLY_COMPLETION_SPEC.md)
    "user_not_found": "カイポケに利用者が見つからない（氏名表記の違い等）",
    "entry_not_found": "カイポケ上で対象の予定が見つからない",
    "delete_button_not_found": "削除ボタンが表示されない",
    "delete_not_accepted": "削除がカイポケに受け付けられない",
    "delete_not_verified": "削除の反映を確認できない",
    "staff_select_not_shown": "職員欄が表示されない",
    "register_failed": "登録が完了しない",
    "add_failed_nothing_deleted": "追加に失敗（旧行は無傷）",
    "old_row_remains_duplicate": "追加成功・旧行の削除失敗（二重・要手動削除）",
    "add_failed_rolled_back": "追加失敗・元の予定を再追加済み",
    "add_failed_row_lost": "追加失敗・元の予定も消失（要手動復元）",
    "add_may_have_registered": "追加が登録済みの可能性（要目視）",
    "add_failed_old_row_intact": "追加失敗・旧行は残存",
    "invalid_date_from": "変更前日付が不正",
    "unknown": "不明",
    # 本モジュールが付ける理由
    "no_rpa_result": "RPA から結果が返らなかった（要目視）",
}

#: op → 画面/レポートの見出し。FE の 2 箇所の辞書もここへ寄せる (設計 §2)。
OP_LABELS: dict[str, str] = {
    "apply": "訪問をカイポケへ送信",
    "events-outbound": "イベントをカイポケへ送信",
    "apply-inbound": "カイポケの差分を取込",
    "smart-apply": "カイポケから取込（自動判別）",
    "replace-inbound": "カイポケから置換取込",
    "apply-events": "イベントを取込",
    "diff-local": "差分計算",
    "diff-inbound": "取込差分計算",
    "events-preview": "イベント取込プレビュー",
    "smart-preview": "取込プレビュー",
    "export": "カイポケ現況の取得",
    "expand": "月間展開",
    "diff": "差分計算(RPA)",
    "login-test": "接続テスト",
}

#: 明細を保存しレポートボタンを出す op (= 実書込の 6 op)。
REPORTABLE_OPS: set[str] = {
    "apply",
    "apply-inbound",
    "smart-apply",
    "replace-inbound",
    "apply-events",
    "events-outbound",
}

#: らく助 → カイポケ の向き。それ以外は取込 (inbound) 扱い。
OUTBOUND_OPS: set[str] = {"apply", "events-outbound"}


def reason_label(code: str | None) -> str | None:
    """機械コード → 日本語ラベル。未知コードは原文を返す (情報を捨てない)。"""
    if not code:
        return None
    return REASON_LABELS.get(code, code)


def op_label(op: str | None) -> str:
    return OP_LABELS.get(op or "", op or "")


def op_direction(op: str | None) -> str:
    return "outbound" if op in OUTBOUND_OPS else "inbound"


def executor_name(user: Any) -> str:
    """実行者の表示名: username → スタッフ名 → メールのローカル部 → ''。"""
    if user is None:
        return ""
    username = (getattr(user, "username", None) or "").strip()
    if username:
        return username
    staff = getattr(user, "staff", None)
    staff_name = (getattr(staff, "name", None) or "").strip() if staff is not None else ""
    if staff_name:
        return staff_name
    email = (getattr(user, "email", None) or "").strip()
    return email.split("@", 1)[0] if email else ""


def build_report_meta(op: str | None, *, user: Any) -> dict[str, str]:
    """``result_summary["report_meta"]`` の中身 (レポートの表紙が使う)。"""
    return {
        "direction": op_direction(op),
        "op_label": op_label(op),
        "executor_name": executor_name(user),
    }


# --- content ビルダー (純関数) ----------------------------------------------


def _side(raw: dict[str, Any] | None) -> dict[str, str] | None:
    """CorrectionSheetItem の before/after dict → レポート用の平坦な 6 キー。"""
    if not raw:
        return None
    return {
        "date": str(raw.get("date") or ""),
        "start": str(raw.get("start_time") or ""),
        "end": str(raw.get("end_time") or ""),
        "staff1": str(raw.get("staff1") or ""),
        "staff2": str(raw.get("staff2") or ""),
        "service": str(raw.get("service_type") or ""),
    }


def _prefer(after: dict[str, str] | None, before: dict[str, str] | None, key: str) -> str:
    """after 優先 (空なら before)。行の見出し時刻・利用者の決め方。"""
    for side in (after, before):
        if side:
            val = side.get(key) or ""
            if val:
                return val
    return ""


def row_content(
    *,
    direction: str,
    action: str,
    user_name: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    resolved_date: date | None,
    outcome: str,
    reason: str | None = None,
    sheet_item_id: str | None = None,
    visit_id: str | None = None,
) -> dict[str, Any]:
    """訪問 1 行分の ``content`` (kind='row'・送信/取込 共通)。"""
    b = _side(before)
    a = _side(after)
    return {
        "kind": "row",
        "direction": direction,
        "date": resolved_date.isoformat() if resolved_date is not None else None,
        "start": _prefer(a, b, "start"),
        "end": _prefer(a, b, "end"),
        "user_name": user_name,
        "action": action,
        "before": b,
        "after": a,
        "outcome": outcome,
        "reason": reason,
        "reason_label": reason_label(reason),
        "ref": {"sheet_item_id": sheet_item_id, "visit_id": visit_id},
    }


def row_content_from_sheet_item(
    item: Any,
    *,
    direction: str,
    resolved_date: date | None,
    outcome: str,
    reason: str | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    """CorrectionSheetItem → kind='row' の content。"""
    before = item.before or {}
    after = item.after or {}
    user_name = str(after.get("user_name") or before.get("user_name") or "")
    return row_content(
        direction=direction,
        action=action or str(item.action),
        user_name=user_name,
        before=item.before,
        after=item.after,
        resolved_date=resolved_date,
        outcome=outcome,
        reason=reason,
        sheet_item_id=str(item.id),
    )


#: InboundItemResult.outcome → (レポートの outcome, 表示 action)
_INBOUND_OUTCOME: dict[str, tuple[str, str]] = {
    "cancelled": ("success", "cancel"),
    "updated": ("success", "update"),
    "added": ("success", "add"),
    "skipped": ("skipped", ""),
    "failed": ("failed", ""),
}

#: sheet item の action → 取込側の表示 action (skipped/failed で使う)
_INBOUND_ACTION_FALLBACK: dict[str, str] = {
    "delete": "cancel",
    "edit": "update",
    "date_change": "date_change",
    "add": "add",
}


def row_content_from_inbound_result(result: Any, item: Any | None) -> dict[str, Any]:
    """InboundItemResult (+ 元の CorrectionSheetItem) → kind='row' の content。"""
    outcome, action = _INBOUND_OUTCOME.get(str(result.outcome), ("failed", ""))
    if not action:
        action = _INBOUND_ACTION_FALLBACK.get(str(result.action), str(result.action))
    resolved: date | None = None
    raw_date = str(getattr(result, "date", "") or "")
    if raw_date:
        try:
            resolved = date.fromisoformat(raw_date)
        except ValueError:
            resolved = None
    before = item.before if item is not None else None
    after = item.after if item is not None else None
    user_name = str(getattr(result, "patient_name", "") or "")
    if not user_name:
        user_name = str((after or {}).get("user_name") or (before or {}).get("user_name") or "")
    reason = str(getattr(result, "detail", "") or "") or None
    return row_content(
        direction="inbound",
        action=action,
        user_name=user_name,
        before=before,
        after=after,
        resolved_date=resolved,
        outcome=outcome,
        reason=reason,
        sheet_item_id=str(result.item_id),
    )


def day_content(
    *, target_date: date | str, wiped: int, inserted: int, sunday_skipped: bool = False
) -> dict[str, Any]:
    """置換取込の「日」1 件 (kind='day')。"""
    return {
        "kind": "day",
        "direction": "inbound",
        "date": target_date.isoformat() if isinstance(target_date, date) else str(target_date),
        "wiped": int(wiped),
        "inserted": int(inserted),
        "sunday_skipped": bool(sunday_skipped),
    }


def skip_content(skip: Any) -> dict[str, Any]:
    """ReplaceSkip → kind='skip' の content。"""
    reason = str(getattr(skip, "reason", "") or "") or None
    return {
        "kind": "skip",
        "direction": "inbound",
        "date": str(getattr(skip, "date", "") or ""),
        "start": str(getattr(skip, "start", "") or ""),
        "user_name": str(getattr(skip, "user_name", "") or ""),
        "staff_name": str(getattr(skip, "staff_name", "") or ""),
        "reason": reason,
        "reason_label": reason_label(reason),
    }


def trainee_solo_content(staff_name: str, count: int) -> dict[str, Any]:
    return {
        "kind": "trainee_solo",
        "direction": "inbound",
        "staff_name": str(staff_name),
        "count": int(count),
    }


def replace_contents(result: Any) -> list[dict[str, Any]]:
    """ReplaceResult → 日 / スキップ / 新人単独 の content 一式 (置換取込)。

    日単位の内訳は ``ReplaceResult.per_day`` (置換で触った日だけ) を使う。
    日曜行を落とした週は、日曜の日付で ``sunday_skipped=True`` の 1 件を足す。
    """
    contents: list[dict[str, Any]] = []
    per_day: dict[date, dict[str, int]] = getattr(result, "per_day", None) or {}
    for d in sorted(per_day):
        counts = per_day[d]
        contents.append(
            day_content(
                target_date=d,
                wiped=int(counts.get("wiped") or 0),
                inserted=int(counts.get("inserted") or 0),
            )
        )
    if int(getattr(result, "sunday_skipped", 0) or 0) > 0:
        contents.append(
            day_content(
                target_date=result.week_start + timedelta(days=6),
                wiped=0,
                inserted=0,
                sunday_skipped=True,
            )
        )
    contents.extend(skip_content(s) for s in getattr(result, "skipped", []) or [])
    contents.extend(
        trainee_solo_content(name, count)
        for name, count in sorted((getattr(result, "trainee_solo", None) or {}).items())
    )
    return contents


#: EventApplyResult.outcome → レポートの outcome
_EVENT_OUTCOME: dict[str, str] = {
    "added": "success",
    "updated": "success",
    "deleted": "success",
    "skipped": "skipped",
    "failed": "failed",
    # events-outbound (RPA) 側
    "skipped_duplicate": "skipped",
    "pending": "pending",
}


def event_content(
    *,
    direction: str,
    target_date: date | str,
    start: str = "",
    end: str = "",
    staff_name: str = "",
    title: str = "",
    action: str = "",
    external_id: str = "",
    outcome: str = "pending",
    reason: str | None = None,
    before: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """イベント 1 件の content (kind='event')。"""
    return {
        "kind": "event",
        "direction": direction,
        "date": target_date.isoformat() if isinstance(target_date, date) else str(target_date),
        "start": str(start or ""),
        "end": str(end or ""),
        "staff_name": str(staff_name or ""),
        "title": str(title or ""),
        "action": str(action or ""),
        "external_id": str(external_id or ""),
        "outcome": outcome,
        "reason": reason,
        "reason_label": reason_label(reason),
        "before": before,
    }


def event_content_from_apply_result(result: Any, change: Any | None = None) -> dict[str, Any]:
    """EventApplyResult (+ 入力の change) → kind='event' の content (取込)。

    EventApplyResult は時刻と変更前の値を持たないため、エコーバックされた
    ``EventsInboundChange`` (start/end/before_*) から補う。
    """
    reason = str(getattr(result, "detail", "") or "") or None
    before: dict[str, Any] | None = None
    if change is not None:
        _b = {
            "start": getattr(change, "before_start", None),
            "end": getattr(change, "before_end", None),
            "title": getattr(change, "before_title", None),
        }
        if any(v for v in _b.values()):
            before = {k: str(v or "") for k, v in _b.items()}
    return event_content(
        direction="inbound",
        target_date=str(getattr(result, "date", "") or ""),
        start=str(getattr(change, "start", "") or "") if change is not None else "",
        end=str(getattr(change, "end", "") or "") if change is not None else "",
        staff_name=str(getattr(result, "staff_name", "") or ""),
        title=str(getattr(result, "title", "") or ""),
        action=str(getattr(result, "action", "") or ""),
        external_id=str(getattr(result, "external_id", "") or ""),
        outcome=_EVENT_OUTCOME.get(str(getattr(result, "outcome", "")), "failed"),
        reason=reason,
        before=before,
    )


def event_content_from_outbound_item(item: Any) -> dict[str, Any]:
    """OutboundItem (送信予定のイベント) → kind='event' の content (送信・pending)。

    RPA の結果は非同期で後から届くため、起動時は ``outcome='pending'`` で置き、
    ``finalize_event_items`` が確定させる。
    """
    return event_content(
        direction="outbound",
        target_date=item.target_date,
        start=str(getattr(item, "start", "") or ""),
        end=str(getattr(item, "end", "") or ""),
        staff_name=str(getattr(item, "staff_name", "") or ""),
        title=str(getattr(item, "title", "") or ""),
        action="add",
        external_id=str(getattr(item, "event_id", "") or ""),
        outcome="pending",
    )


# --- 並び (seq) --------------------------------------------------------------

#: 同じ日でも「日サマリ → スキップ → 新人単独」の順に出す (レポートの読み順)。
_KIND_RANK = {"row": 0, "day": 0, "event": 0, "skip": 1, "trainee_solo": 2}
_LAST_DATE = "9999-99-99"


def _sort_key(content: dict[str, Any]) -> tuple[int, str, str, str]:
    kind = str(content.get("kind") or "")
    d = content.get("date")
    name = str(content.get("user_name") or content.get("staff_name") or "")
    return (
        _KIND_RANK.get(kind, 9),
        str(d) if d else _LAST_DATE,
        str(content.get("start") or ""),
        name,
    )


def sort_contents(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """(日付, 開始時刻, 利用者) 順。日付なしは末尾 (安定ソート)。"""
    return sorted(contents, key=_sort_key)


# --- 保存 --------------------------------------------------------------------

#: content の kind → KaipokeJobItem.status の既定値 (row/event は outcome を使う)
_STATUS_FOR_KIND = {"day": "success", "skip": "skipped", "trainee_solo": "success"}


def _status_of(content: dict[str, Any]) -> str:
    outcome = content.get("outcome")
    if isinstance(outcome, str) and outcome:
        return outcome[:16]
    return _STATUS_FOR_KIND.get(str(content.get("kind") or ""), "completed")


async def write_job_items(db, job_id, contents: list[dict[str, Any]]) -> int:
    """ジョブの明細を丸ごと書き直す (冪等: 既存 items を消してから入れる)。

    commit はしない。戻り値 = 書いた件数。
    """
    await db.execute(delete(KaipokeJobItem).where(KaipokeJobItem.job_id == job_id))
    await db.flush()
    rows = sort_contents(contents)
    for seq, content in enumerate(rows, start=1):
        db.add(
            KaipokeJobItem(
                job_id=job_id,
                seq=seq,
                status=_status_of(content),
                content=content,
                error_msg=content.get("reason_label") or None,
            )
        )
    await db.flush()
    return len(rows)


async def _load_items(db, job_id) -> list[KaipokeJobItem]:
    return list(
        (
            await db.scalars(
                select(KaipokeJobItem)
                .where(KaipokeJobItem.job_id == job_id)
                .order_by(KaipokeJobItem.seq)
            )
        ).all()
    )


def _day_of(value: Any) -> int | None:
    """'9'/'09'/'2026-09-09' のいずれからも「日」を取り出す。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    if "-" in raw:
        raw = raw.rsplit("-", 1)[-1]
    try:
        return int(raw)
    except ValueError:
        return None


def _content_day(content: dict[str, Any]) -> int | None:
    day = _day_of(content.get("date"))
    if day is not None:
        return day
    action = str(content.get("action") or "")
    primary = "before" if action in ("delete", "edit", "date_change") else "after"
    for key in (primary, "after", "before"):
        side = content.get(key)
        if isinstance(side, dict):
            day = _day_of(side.get("date"))
            if day is not None:
                return day
    return None


#: RPA details[].status → レポートの outcome
_RPA_STATUS = {"success": "success", "skipped": "skipped"}


async def finalize_apply_items(db, job: KaipokeJob, details: list[dict[str, Any]]) -> int:
    """apply(送信) の pending 明細を RPA の ``result.details[]`` で確定させる。

    突合キーは (日, 正規化した利用者名, action) — RPA は日を「日番号の文字列」で
    返すため ISO の日付とは直接比較できない。

    突合できなかった pending 行は ``outcome='unknown'`` + ``reason='no_rpa_result'``
    にする。``pending`` のまま残すと「まだ実行中」と区別が付かず、レポートで
    黙って成功扱いに見えてしまう — 「結果が分からない = 要目視」と言い切る。
    ``details`` が空 (result_unknown のジョブ) のときも全 pending 行が unknown に
    倒れる = 呼び出し側は結果の有無に関わらず本関数を呼べばよい。

    commit はしない。戻り値 = 確定できた件数。
    """
    items = await _load_items(db, job.id)
    pending: dict[tuple[int | None, str, str], list[KaipokeJobItem]] = {}
    for it in items:
        content = it.content or {}
        if content.get("kind") != "row" or content.get("outcome") != "pending":
            continue
        key = (
            _content_day(content),
            normalize_person_name(str(content.get("user_name") or "")),
            str(content.get("action") or ""),
        )
        pending.setdefault(key, []).append(it)

    settled = 0
    for detail in details or []:
        if not isinstance(detail, dict):
            continue
        key = (
            _day_of(detail.get("date")),
            normalize_person_name(str(detail.get("user") or "")),
            str(detail.get("action") or ""),
        )
        bucket = pending.get(key)
        if not bucket:
            continue
        item = bucket.pop(0)
        outcome = _RPA_STATUS.get(str(detail.get("status") or ""), "failed")
        reason = str(detail.get("reason") or "") or None
        item.content = {
            **(item.content or {}),
            "outcome": outcome,
            "reason": reason,
            "reason_label": reason_label(reason),
        }
        item.status = outcome
        item.error_msg = reason_label(reason)
        settled += 1

    for bucket in pending.values():
        for item in bucket:
            item.content = {
                **(item.content or {}),
                "outcome": "unknown",
                "reason": "no_rpa_result",
                "reason_label": REASON_LABELS["no_rpa_result"],
            }
            item.status = "unknown"
            item.error_msg = REASON_LABELS["no_rpa_result"]
    await db.flush()
    return settled


async def finalize_event_items(db, job: KaipokeJob, results: list[dict[str, Any]]) -> int:
    """events-outbound の pending 明細を RPA の ``results[]`` で確定させる。

    突合キーは ``external_ref`` (= らく助の staff_event.id)。commit はしない。
    """
    items = await _load_items(db, job.id)
    by_ref: dict[str, KaipokeJobItem] = {}
    for it in items:
        content = it.content or {}
        if content.get("kind") != "event":
            continue
        by_ref[str(content.get("external_id") or "")] = it

    settled = 0
    for result in results or []:
        if not isinstance(result, dict):
            continue
        item = by_ref.get(str(result.get("external_ref") or ""))
        if item is None:
            continue
        raw = str(result.get("outcome") or "")
        outcome = _EVENT_OUTCOME.get(raw, "failed")
        reason = str(result.get("reason") or result.get("error") or "") or None
        if outcome == "skipped" and reason is None:
            reason = raw or None
        item.content = {
            **(item.content or {}),
            "outcome": outcome,
            "reason": reason,
            "reason_label": reason_label(reason),
        }
        item.status = outcome
        item.error_msg = reason_label(reason)
        settled += 1

    for item in by_ref.values():
        content = item.content or {}
        if content.get("outcome") != "pending":
            continue
        # 送ったのに結果が返らなかった行 — pending のままにせず「要目視」にする
        # (apply の finalize_apply_items と同じ扱い)。
        item.content = {
            **content,
            "outcome": "unknown",
            "reason": "no_rpa_result",
            "reason_label": REASON_LABELS["no_rpa_result"],
        }
        item.status = "unknown"
        item.error_msg = REASON_LABELS["no_rpa_result"]
    await db.flush()
    return settled
