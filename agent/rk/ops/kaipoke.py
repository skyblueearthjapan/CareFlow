"""カイポケとの差分・送信・突き合わせ（S8 rakusuke-kaipoke）の部品。

- 操作（許可リスト）は 1 つだけ: ``kaipoke-send``（POST /integrations/apply・dryRun=false）。
  カイポケへの外部書き込みで、らく助からは**戻せない**（inverse=None）。
- それ以外は読むための手伝いの関数（操作ではない）。スキルは ``rk get`` / ``rk post-read`` で同じ API を呼ぶか、
  Python から ``from rk.ops.kaipoke import ...`` で使う。

約束（R1 §12・R2 B-6/B-7/C-5・session-2026-10-09-HANDOFF §6/§7）:
- 送信は必ず実 HTTP（rk は 127.0.0.1:18001 へ ssh のポート転送で HTTP する）。ASGI 直叩き（admin_call.py）は
  裏の処理が止まるので使わない。
- 送る前に必ず差分を見る（お客様がカイポケで直接手を入れる運用がある・週42 の約 45 件）。
- 日中だけ（09:00〜17:00 JST）。17〜18 時台はロボットが失敗しやすい。1 件 30〜60 秒。
- 当日以前は送られない（サーバーが除外・integrations.py:1868-1893）。担当なし（職員1 が空/'-'）も除外（:1926-1947）。
- 月をまたぐ週は月ごとに分けて送る（2 か月が混ざると送れない・:1949-）。
- 成否はカイポケの export で確かめる（ジョブの completed は「終わった」だけ）。
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from rk import RkError
from rk.opsbase import Api, Op, register

JST = timezone(timedelta(hours=9))
SEND_START = time(9, 0)
SEND_END = time(17, 0)
#: 1 件あたりの見込み（秒）。実績 30〜60 秒（172 件で約 1 時間 50 分 ≒ 38 秒/件）。
SECONDS_PER_ITEM = 45
#: 差分を確かめてから送るまでの許容時間。これより古い確認ではお客様の手直しを見落とす恐れ。
MAX_DIFF_AGE = timedelta(hours=3)

_MONTH = re.compile(r"^\d{4}-\d{2}$")
_UNASSIGNED = {"", "-", "－", None}


def now_jst() -> datetime:
    return datetime.now(JST)


def _date(v: Any) -> date | None:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else None


def months_of_week(week_start: date, week_end: date) -> list[str]:
    out: list[str] = []
    d = week_start
    while d <= week_end:
        m = d.strftime("%Y-%m")
        if m not in out:
            out.append(m)
        d += timedelta(days=1)
    return out


def item_date(item: dict, week_start: date) -> date | None:
    """差分の 1 行の実日付（integrations.py:1447 resolve_item_date と同じ規則）。

    delete/edit/date_change は before（カイポケの今の位置）・add は after。「日」(1〜31) を週の 7 日で一意に解決。
    unsent-summary の行は date_iso を持つのでそれを優先。
    """
    if item.get("date_iso"):
        return _date(item["date_iso"])
    src = item.get("before") if item.get("action") in ("delete", "edit", "date_change") else item.get("after")
    raw = str((src or {}).get("date") or "").strip()
    if not raw.isdigit():
        return None
    hits = [week_start + timedelta(days=i) for i in range(7) if (week_start + timedelta(days=i)).day == int(raw)]
    return hits[0] if len(hits) == 1 else None


def is_unassigned(item: dict) -> bool:
    """add/edit の送り先（after）の職員1 が空/'-'（カイポケの export に出ない行・2026-09-03 の事故）。"""
    if item.get("unassigned"):
        return True
    if item.get("action") not in ("add", "edit", "date_change"):
        return False
    return (item.get("after") or {}).get("staff1") in _UNASSIGNED


def send_window_problems(now: datetime, item_count: int = 0) -> list[str]:
    """送ってよい時間か（09:00〜17:00 JST・見込みの終わりも 17:00 まで）。"""
    t = now.astimezone(JST)
    out = []
    if not (SEND_START <= t.time() < SEND_END):
        out.append(f"いまは {t:%H:%M}（日本時間）。カイポケへの送信は 09:00〜17:00 だけ（17〜18 時台はロボットが失敗しやすい）")
    elif item_count:
        end = t + timedelta(seconds=SECONDS_PER_ITEM * item_count)
        if end.time() >= SEND_END or end.date() != t.date():
            out.append(f"{item_count} 件は約 {SECONDS_PER_ITEM * item_count // 60} 分かかり、終わりが {end:%H:%M} の見込み。"
                       "17:00 までに終わるよう件数を分ける（item_ids で一部ずつ）")
    return out


# ---------------------------------------------------------------------------
# 操作: kaipoke-send
# ---------------------------------------------------------------------------


# `python -m rk.ops.kaipoke` で動かすとこのファイルが 2 回読まれる（rk.ops と __main__）。2 回目は登録しない。
_register = register if __name__ != "__main__" else (lambda cls: cls)


@_register
class KaipokeSend(Op):
    """差分シートの内容をカイポケへ書き込む（RPA）。画面の「この週で本番反映」「⇧カイポケへ送る」と同じ。

    API: POST /integrations/apply body {"sheetId","dryRun":false,"itemIds"?}（backend/app/api/v1/integrations.py:1804・
    IntegrationApplyRequest schemas/integrations.py:244・camelCase）。応答 202 {jobId, kaipokeJobId, status}。
    送る差分は diff-local（:1480・カイポケから export して比べる）か unsent-summary（:3217・保存した写しと比べる）で作る。
    op: {"op":"kaipoke-send","sheet_id":"…","month":"2026-10","week_start":"2026-10-19","week_end":"2026-10-25",
         "expected_item_count":12,"diff_reviewed_at":"2026-10-19T10:05:00+09:00","item_ids":[…](任意・部分送信),
         "label":"週43 をカイポケへ（12 件）"}
    - expected_item_count: 了承した件数。送る直前に読み直して違えば止める（シートが変わった・別の人が送った）。
    - diff_reviewed_at: PO が差分（前→後の表）を確かめて了承した時刻（タイムゾーンつき）。3 時間より古ければ取り直す。
    """

    name = "kaipoke-send"
    summary = "カイポケへ送る（外部への書き込み・らく助からは戻せない）"
    undoable = False

    #: テストで差し替える時計（日本時間の今を返す関数）。
    clock: Callable[[], datetime] = staticmethod(now_jst)

    def validate(self, op):
        errs = []
        for k in ("sheet_id", "month", "week_start", "week_end", "expected_item_count", "diff_reviewed_at"):
            if op.get(k) in (None, ""):
                errs.append(f"{k} がありません")
        if errs:
            return errs
        if not _MONTH.match(str(op["month"])):
            errs.append("month は YYYY-MM")
        ws, we = _date(op["week_start"]), _date(op["week_end"])
        if ws is None or we is None:
            errs.append("week_start / week_end は YYYY-MM-DD")
        else:
            if we < ws or (we - ws).days > 6:
                errs.append("week_end は week_start から 0〜6 日後")
            months = months_of_week(ws, we)
            if op["month"] not in months:
                errs.append(f"month {op['month']} がこの週（{', '.join(months)}）に含まれない")
            if len(months) > 1 and not op.get("item_ids"):
                errs.append(f"月をまたぐ週（{' と '.join(months)}）は月ごとに分けて送る。item_ids に {op['month']} の行だけを入れる")
        n = op["expected_item_count"]
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            errs.append("expected_item_count は 1 以上の整数（了承した件数）")
        elif op.get("item_ids") is not None and len(op["item_ids"]) != n:
            errs.append(f"item_ids の数（{len(op['item_ids'])}）と expected_item_count（{n}）が違う")
        now = self.clock()
        rv = _dt(op["diff_reviewed_at"])
        if rv is None:
            errs.append("diff_reviewed_at はタイムゾーンつきの時刻（例 2026-10-19T10:05:00+09:00）")
        elif rv > now + timedelta(minutes=5):
            errs.append("diff_reviewed_at が未来になっている")
        elif now - rv > MAX_DIFF_AGE:
            errs.append(f"差分の確認（{rv.astimezone(JST):%m/%d %H:%M}）から {MAX_DIFF_AGE.seconds // 3600} 時間を過ぎた。"
                        "お客様がカイポケで手を入れた分を見落とさないよう、差分を取り直して確かめる")
        errs += send_window_problems(now, n if isinstance(n, int) else 0)
        return errs

    def request(self, op):
        body: dict[str, Any] = {"sheetId": op["sheet_id"], "dryRun": False}
        if op.get("item_ids"):
            body["itemIds"] = list(op["item_ids"])
        return ("POST", "/integrations/apply", body)

    def _items(self, api: Api, op: dict) -> list[dict]:
        items = list_items(api, op["sheet_id"])
        if op.get("item_ids"):
            want = {str(i) for i in op["item_ids"]}
            return [it for it in items if str(it.get("id")) in want]
        return [it for it in items if it.get("include")]

    def snapshot(self, api, op):
        """送る行の写し（何を送ったかの記録。カイポケ側を戻す手がかりにもなる）。"""
        items = self._items(api, op)
        ws = _date(op["week_start"])
        return {"sheet_id": op["sheet_id"], "count": len(items), "summary": summarize_items(items, ws, self.clock().date()),
                "items": [{"id": it.get("id"), "action": it.get("action"), "before": it.get("before"),
                           "after": it.get("after")} for it in items]}

    def execute(self, api, op):
        """送る直前にもう一度: 時間・件数・月を確かめる（案を作った時から変わっていたら止める）。"""
        problems = send_window_problems(self.clock(), int(op["expected_item_count"]))
        if problems:
            raise RkError("送信を止めました: " + " / ".join(problems))
        items = self._items(api, op)
        if len(items) != op["expected_item_count"]:
            raise RkError(f"送信を止めました: 送る行が {len(items)} 件（了承は {op['expected_item_count']} 件）。差分を取り直して確かめる")
        ws = _date(op["week_start"])
        months = {d.strftime("%Y-%m") for d in (item_date(it, ws) for it in items) if d}
        if months - {op["month"]}:
            raise RkError(f"送信を止めました: {op['month']} 以外の月の行がある（{sorted(months)}）。月ごとに分ける")
        return super().execute(api, op)

    def check_response(self, op, response):
        if not isinstance(response, dict) or not (response.get("jobId") or response.get("job_id")):
            return ["応答にジョブ ID が無い"]
        if response.get("status") in ("failed", "cancelled"):
            return [f"送信のジョブが {response.get('status')}"]
        return []

    def undo_note(self, op, before: dict | None = None) -> str | None:
        return ("カイポケ側は送った内容を消す/戻すには別の送信が要る（らく助の予定を元に戻してから差分を取り直して送る）。"
                "らく助のバックアップを戻してもカイポケは戻らない")

    def inverse(self, op, before):
        return None

    def verify(self, api, op):
        """送信のジョブがあるか・失敗していないか。成否の確定はカイポケの export（スキルの手順）で。"""
        jobs = find_apply_jobs(api, op["week_start"], op["sheet_id"])
        if not jobs:
            return ["このシートの送信ジョブが見つからない"]
        j = jobs[0]
        if j.get("status") in ("failed", "cancelled"):
            return [f"送信のジョブが {j.get('status')}（{(j.get('result_summary') or {})}）"]
        return []


# ---------------------------------------------------------------------------
# 読むための手伝い（操作ではない）
# ---------------------------------------------------------------------------


def diff_local_request(month: str, week_start: str, week_end: str | None = None,
                       office_id: str | None = None) -> tuple[str, str, dict]:
    """送る前の差分を作る（integrations.py:1480）。**カイポケにログインして export する（1〜2 分）→ PO の了承の後で**。

    応答 {jobId, sheetId, summary}。月をまたぐ週は両月を export する。body は camelCase。
    """
    body: dict[str, Any] = {"month": month, "weekStart": week_start}
    if week_end:
        body["weekEnd"] = week_end
    if office_id:
        body["officeId"] = office_id
    return ("POST", "/integrations/diff-local", body)


def create_diff(api: Api, month: str, week_start: str, week_end: str | None = None) -> dict:
    method, path, body = diff_local_request(month, week_start, week_end)
    return api.call(method, path, body)


def list_items(api: Api, sheet_id: str, include: bool | None = None, action: str | None = None) -> list[dict]:
    """差分シートの行を全部読む（GET /integrations/correction-sheets/{id}/items・integrations.py:3034・limit≦500）。"""
    out: list[dict] = []
    off = 0
    while True:
        params: dict[str, Any] = {"limit": 500, "offset": off}
        if include is not None:
            params["include"] = "true" if include else "false"
        if action:
            params["type"] = action
        page = api.get(f"/integrations/correction-sheets/{sheet_id}/items", params) or {}
        rows = page.get("items") or []
        out.extend(rows)
        off += len(rows)
        if not rows or off >= int(page.get("total") or 0):
            return out


def unsent_summary(api: Api, week_start: str) -> dict:
    """●未送信（POST /integrations/unsent-summary・integrations.py:3217）。RPA を呼ばず、**保存した写し**と比べる。

    答えには snapshot.fetched_at（写しの時刻）を必ず添える。月をまたぐ週は空＋警告。
    応答の sheet_id はそのまま kaipoke-send に使える（送れる件数 = sendable_count）。
    """
    return api.call("POST", "/integrations/unsent-summary", {"weekStart": week_start})


def reconcile_report(api: Api, week_start: str, days: int = 7) -> dict:
    """突き合わせの一覧（GET /integrations/reconcile-report・integrations.py:778・DB 書込なし・保存した写しと比べる）。"""
    return api.get("/integrations/reconcile-report",
                   {"weekStart": week_start, "days": days, "format": "json", "include_html": "false"})


def job_status(api: Api, job_id: str) -> dict:
    """ジョブ 1 件（GET /integrations/kaipoke/jobs/{id}・integrations.py:196）。"""
    return api.get(f"/integrations/kaipoke/jobs/{job_id}")


def job_report(api: Api, job_id: str) -> dict:
    """連携結果レポート（GET /integrations/kaipoke/jobs/{id}/report・sync_report.py:34）。unknown = 要目視。"""
    return api.get(f"/integrations/kaipoke/jobs/{job_id}/report", {"fmt": "json", "include_html": "false"})


def find_apply_jobs(api: Api, week_start: str, sheet_id: str | None = None) -> list[dict]:
    """その週の送信ジョブ（新しい順）。sheet_id を渡すとそのシートの本番送信（dry_run=false）だけ。"""
    page = api.get("/integrations/kaipoke/jobs", {"week_start": week_start, "type": "push", "limit": 50}) or {}
    out = []
    for j in page.get("items") or []:
        p = j.get("params") or {}
        if p.get("op") != "apply" or p.get("dry_run"):
            continue
        if sheet_id and str(p.get("sheet_id")) != str(sheet_id):
            continue
        out.append(j)
    return out


def summarize_items(items: list[dict], week_start: date | None, today: date | None = None) -> dict:
    """差分の行を数える（action ごと・担当なし・過去日・非稼働の患者・請求区分のずれ・月）。"""
    today = today or now_jst().date()
    s: dict[str, Any] = {"total": len(items), "by_action": {}, "unassigned": 0, "past": 0,
                         "inactive_patient": 0, "grade_change": 0, "unresolved_date": 0, "months": {}}
    for it in items:
        a = it.get("action") or "?"
        s["by_action"][a] = s["by_action"].get(a, 0) + 1
        if is_unassigned(it):
            s["unassigned"] += 1
        if it.get("inactive_patient"):
            s["inactive_patient"] += 1
        if it.get("grade_change") or (it.get("after") or {}).get("grade_change"):
            s["grade_change"] += 1
        d = item_date(it, week_start) if week_start else None
        if d is None:
            s["unresolved_date"] += 1
        else:
            if d <= today:
                s["past"] += 1
            m = d.strftime("%Y-%m")
            s["months"][m] = s["months"].get(m, 0) + 1
    return s


ACTION_JA = {"add": "追加", "delete": "削除", "edit": "変更", "update": "変更", "date_change": "日の移動",
             "companion_change": "同行の変更"}


def items_table(items: list[dict], week_start: date | None) -> list[dict]:
    """前→後の表（PO に見せる用）。利用者名を含むので docs/reports/agent/ の外に書かない。"""
    rows = []
    for it in items:
        b, a = it.get("before") or {}, it.get("after") or {}
        d = item_date(it, week_start) if week_start else None
        rows.append({
            "id": it.get("id"),
            "日付": d.isoformat() if d else (a.get("date") or b.get("date")),
            "種類": ACTION_JA.get(it.get("action"), it.get("action")),
            "利用者": a.get("user_name") or b.get("user_name"),
            "時刻（カイポケ→らく助）": f"{b.get('start_time') or '-'} → {a.get('start_time') or '-'}",
            "職員1（カイポケ→らく助）": f"{b.get('staff1') or '-'} → {a.get('staff1') or '-'}",
            "職員2": f"{b.get('staff2') or '-'} → {a.get('staff2') or '-'}",
            "担当なし": is_unassigned(it),
            "送る": bool(it.get("include")),
        })
    return rows


def snapshot_time_text(unsent: dict) -> str:
    """●未送信の写しの時刻を日本時間の文で。写しが無ければそう言う。"""
    snap = (unsent or {}).get("snapshot") or {}
    t = _dt(snap.get("fetched_at"))
    if t is None:
        return "カイポケの写しがありません（差分を取る＝カイポケから export する必要があります）"
    return f"カイポケの写しは {t.astimezone(JST):%m/%d %H:%M}（日本時間）時点（{snap.get('month')}・{snap.get('row_count')} 行）"


# ---------------------------------------------------------------------------
# 小さなコマンド（スキルが使う）: PYTHONPATH=. python -m rk.ops.kaipoke <サブコマンド> ...
#   diff   … diff-local（カイポケから export → 差分シート）。RPA を動かすので --approved が要る
#   items  … 差分シートの行の要点と前→後の表（利用者名を含む → 画面に出すだけ・ファイルは docs/reports/agent/ へ）
#   unsent … ●未送信（保存した写しと比べる）と写しの時刻
#   jobs   … その週の送信ジョブ / job … ジョブ 1 件の状態
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from rk.config import load_config
    from rk.session import open_api

    ap = argparse.ArgumentParser(prog="python -m rk.ops.kaipoke", description="カイポケとの差分・送信の確認")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff", help="差分を作る（カイポケから export・1〜2 分）")
    d.add_argument("--month", required=True)
    d.add_argument("--week-start", required=True)
    d.add_argument("--week-end")
    d.add_argument("--approved", required=True, help="誰が・いつ了承したか（カイポケのロボットを動かすため）")
    i = sub.add_parser("items", help="差分シートの要点と前→後の表")
    i.add_argument("--sheet", required=True)
    i.add_argument("--week-start", required=True)
    i.add_argument("--include-only", action="store_true")
    u = sub.add_parser("unsent", help="●未送信と写しの時刻")
    u.add_argument("--week-start", required=True)
    j = sub.add_parser("jobs", help="その週の送信ジョブ")
    j.add_argument("--week-start", required=True)
    j.add_argument("--sheet")
    k = sub.add_parser("job", help="ジョブ 1 件")
    k.add_argument("--id", required=True)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows の画面でも日本語を崩さない
    except (AttributeError, ValueError):
        pass

    with open_api(load_config()) as api:
        if a.cmd == "diff":
            out: Any = {"approved": a.approved, "result": create_diff(api, a.month, a.week_start, a.week_end)}
        elif a.cmd == "items":
            items = list_items(api, a.sheet, include=True if a.include_only else None)
            ws = _date(a.week_start)
            out = {"summary": summarize_items(items, ws), "table": items_table(items, ws)}
        elif a.cmd == "unsent":
            res = unsent_summary(api, a.week_start)
            out = {"snapshot": snapshot_time_text(res), "sheet_id": res.get("sheet_id"),
                   "sendable_count": res.get("sendable_count"), "past_count": res.get("past_count"),
                   "summary": summarize_items(res.get("items") or [], _date(a.week_start)),
                   "table": items_table(res.get("items") or [], _date(a.week_start))}
        elif a.cmd == "jobs":
            out = [{"id": x.get("id"), "status": x.get("status"), "created_at": x.get("created_at"),
                    "completed_at": x.get("completed_at"), "params": x.get("params"),
                    "result_summary": x.get("result_summary")} for x in find_apply_jobs(api, a.week_start, a.sheet)]
        else:
            x = job_status(api, a.id)
            st: dict[str, int] = {}
            for it in x.get("items") or []:
                st[it.get("status")] = st.get(it.get("status"), 0) + 1
            out = {"id": x.get("id"), "status": x.get("status"), "items_by_status": st,
                   "result_summary": x.get("result_summary"), "started_at": x.get("started_at"),
                   "completed_at": x.get("completed_at")}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


__all__ = [
    "KaipokeSend", "create_diff", "diff_local_request", "find_apply_jobs", "item_date", "items_table",
    "is_unassigned", "job_report", "job_status", "list_items", "months_of_week", "now_jst", "reconcile_report",
    "send_window_problems", "snapshot_time_text", "summarize_items", "unsent_summary",
]

if __name__ == "__main__":
    raise SystemExit(main())
