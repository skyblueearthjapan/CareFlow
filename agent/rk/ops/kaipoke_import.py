"""カイポケ → らく助 の取り込み（画面の「⇩カイポケから取り込む」= smart-inbound）の部品。

- 操作（許可リスト）は 2 つ:
  ``kaipoke-import``      … POST /integrations/smart-inbound-apply（dryRun=false）。打刻のある日=差分・無い日=置換。
  ``kaipoke-import-undo`` … POST /integrations/inbound-snapshots/{id}/restore（「取り込み前に戻す」）。
  ``kaipoke-import`` の戻し方はこれ（取り込みの直前にサーバーが保存した盤面へ戻す）。
- 取り込む前に必ずプレビューを取る（``run_preview``。カイポケのロボットを動かす = 了承の後・1〜2 分）。
  プレビューの件数と「らく助で調整した訪問で置き換わるもの」（``rakusuke_adjusted_replaced``）を案に書き写す。

根拠（backend/app/api/v1/integrations.py）:
- プレビュー開始 POST /integrations/smart-inbound-preview/start（:5190・202 {jobId}）→
  GET /integrations/smart-inbound-preview/status/{jobId}（:5342）を 3 秒ごと（画面 frontend/lib/queries/integrations.ts:907 と同じ）。
- 適用 POST /integrations/smart-inbound-apply（:5492）。置換の日があるとカイポケから export し直す（ロボット・:5547）。
  取り込みの直前に週の盤面を保存する（snapshot_week kind="smart"・:5544・週ごとに 5 世代まで）。
- 一覧 GET /integrations/inbound-snapshots?weekStart=（:2477・hasCheckins つき）・
  戻す POST /integrations/inbound-snapshots/{id}/restore（:2522）。**週の有効な訪問に打刻が 1 件でもあると 422**
  （services/kaipoke/inbound_snapshot.py:278）。打刻のある日がある週（protected_days が空でない）は取り込んだ後に戻せない。
"""

from __future__ import annotations

import sys
import time as _time
from datetime import datetime, timedelta
from typing import Any, Callable

from rk import RkError
from rk.opsbase import Api, Op, register
from rk.ops.kaipoke import JST, MAX_DIFF_AGE, _date, _dt, now_jst, send_window_problems

PREVIEW_POLL_SECONDS = 3
#: サーバーは 10 分を超えた running を残骸として failed にする。画面と同じく少し長く待つ。
PREVIEW_DEADLINE_SECONDS = 12 * 60
PREVIEW_MAX_CONSECUTIVE_ERRORS = 3
INBOUND_APPLY_OPS = ("smart-apply", "replace-inbound", "apply-inbound")

NO_UNDO_CHECKINS = "この週には打刻のある日があるため、取り込んだ後は画面からも元に戻せません"


def window_problems(now: datetime) -> list[str]:
    """ロボットを使う取り込みの時間（送信と同じ 09:00〜17:00 JST）。"""
    return [p.replace("カイポケへの送信は", "カイポケのロボットを使う取り込みは") for p in send_window_problems(now)]


# ---------------------------------------------------------------------------
# プレビュー（読むための手伝い・ロボットを動かす）
# ---------------------------------------------------------------------------


def start_preview(api: Api, week_start: str) -> str:
    res = api.call("POST", "/integrations/smart-inbound-preview/start", {"weekStart": week_start}) or {}
    job_id = res.get("jobId") or res.get("job_id")
    if not job_id:
        raise RkError("プレビューを始められませんでした（受付番号がありません）")
    return str(job_id)


def preview_status(api: Api, job_id: str) -> dict:
    return api.get(f"/integrations/smart-inbound-preview/status/{job_id}") or {}


def wait_preview(api: Api, job_id: str, sleep: Callable[[float], None] = _time.sleep,
                 clock: Callable[[], float] = _time.monotonic,
                 deadline_s: float = PREVIEW_DEADLINE_SECONDS) -> dict:
    """status を 3 秒ごとに読み、completed なら {job_id, preview, completed_at} を返す。"""
    end = clock() + deadline_s
    errors = 0
    while True:
        try:
            st = preview_status(api, job_id)
            errors = 0
        except Exception:
            errors += 1
            if errors >= PREVIEW_MAX_CONSECUTIVE_ERRORS:
                raise
            sleep(PREVIEW_POLL_SECONDS)
            continue
        if st.get("status") == "completed" and st.get("preview"):
            return {"job_id": job_id, "preview": st["preview"], "completed_at": st.get("completedAt")}
        if st.get("status") in ("failed", "completed"):
            raise RkError(st.get("error") or "カイポケの読み込みに失敗しました")
        if clock() > end:
            raise RkError("カイポケの読み込みが時間内に終わりませんでした（12 分）")
        sleep(PREVIEW_POLL_SECONDS)


def run_preview(api: Api, week_start: str, **kw: Any) -> dict:
    return wait_preview(api, start_preview(api, week_start), **kw)


def summarize_preview(preview: dict) -> dict:
    """プレビューの要点（案に書き写す数と、置き換わる「らく助で調整した訪問」）。"""
    rep = preview.get("replace") or {}
    adjusted = [{"visit_id": str(a.get("visit_id")), "date": str(a.get("date")), "start_time": a.get("start_time"),
                 "patient_label": a.get("patient_label"), "change": a.get("change")}
                for a in preview.get("rakusuke_adjusted_replaced") or []]
    return {
        "protected_days": [str(d) for d in preview.get("protectedDays") or []],
        "replace_days": [str(d) for d in preview.get("replaceDays") or []],
        "sheet_id": str(preview["sheetId"]) if preview.get("sheetId") else None,
        "diff": dict(preview.get("diffSummary") or {}),
        "replace_wiped": int(rep.get("wiped") or 0),
        "replace_inserted": int(rep.get("inserted") or 0),
        "replace_skipped": len(rep.get("skipped") or []),
        "trainee_solo": len(rep.get("traineeSolo") or []),
        "ng_conflicts": len(rep.get("ngConflicts") or []),
        "adjusted_replaced": adjusted,
        "adjusted_removed": sum(1 for a in adjusted if "消え" in str(a.get("change") or "")),
    }


def build_import_op(week_start: str, preview_result: dict, reviewed_at: str, label: str | None = None) -> dict:
    """プレビューの結果から kaipoke-import の op を作る（reviewed_at = 管理者が要点を見て了承した時刻）。"""
    s = summarize_preview(preview_result["preview"])
    return {
        "op": "kaipoke-import", "week_start": week_start, "preview_job_id": preview_result["job_id"],
        "sheet_id": s["sheet_id"], "preview_reviewed_at": reviewed_at,
        "protected_days": s["protected_days"], "replace_days": s["replace_days"],
        "expected": {"diff": s["diff"], "replace_wiped": s["replace_wiped"],
                     "replace_inserted": s["replace_inserted"], "replace_skipped": s["replace_skipped"]},
        "adjusted_replaced": s["adjusted_replaced"],
        "label": label or f"{week_start} の週をカイポケから取り込む",
    }


def list_snapshots(api: Api, week_start: str) -> dict:
    return api.get("/integrations/inbound-snapshots", {"weekStart": week_start}) or {}


def inbound_jobs(api: Api, week_start: str) -> list[dict]:
    page = api.get("/integrations/kaipoke/jobs", {"week_start": week_start, "limit": 50}) or {}
    items = page.get("items") if isinstance(page, dict) else page
    return [j for j in items or [] if (j.get("params") or {}).get("op") in INBOUND_APPLY_OPS]


def _after(v: Any, ref: datetime) -> bool:
    t = _dt(v)
    return t is not None and t >= ref


# ---------------------------------------------------------------------------
# 操作
# ---------------------------------------------------------------------------

_register = register if __name__ != "__main__" else (lambda cls: cls)


@_register
class KaipokeImport(Op):
    """カイポケの今を、らく助の週へ取り込む（打刻のある日=差分・無い日=置換）。

    API: POST /integrations/smart-inbound-apply body {"weekStart","sheetId","dryRun":false}（integrations.py:5492）。
    op: {"op":"kaipoke-import","week_start":"2026-10-19","preview_job_id":"…","sheet_id":"…"|null,
         "preview_reviewed_at":"2026-10-19T10:05:00+09:00","protected_days":[…],"replace_days":[…],
         "expected":{"diff":{…},"replace_wiped":n,"replace_inserted":n},"adjusted_replaced":[…]}
    （``build_import_op`` がプレビューから作る）。
    戻し方: kaipoke-import-undo（取り込みの直前の盤面へ）。打刻のある日がある週は戻せない。
    """

    name = "kaipoke-import"
    summary = "カイポケから取り込む（打刻のある日=差分・無い日=置き換え）"
    undoable = True

    clock: Callable[[], datetime] = staticmethod(now_jst)

    def validate(self, op):
        errs = []
        for k in ("week_start", "preview_job_id", "preview_reviewed_at", "expected"):
            if op.get(k) in (None, ""):
                errs.append(f"{k} がありません")
        for k in ("protected_days", "replace_days", "adjusted_replaced"):
            if not isinstance(op.get(k), list):
                errs.append(f"{k} がありません（プレビューから書き写す・無ければ空の一覧）")
        if errs:
            return errs
        ws = _date(op["week_start"])
        if ws is None or ws.weekday() != 0:
            errs.append("week_start は月曜日（YYYY-MM-DD）")
        if op["protected_days"] and not op.get("sheet_id"):
            errs.append("打刻のある日があるのに sheet_id がありません（プレビューを取り直す）")
        if not op["protected_days"] and not op["replace_days"]:
            errs.append("取り込む日がありません")
        now = self.clock()
        rv = _dt(op["preview_reviewed_at"])
        if rv is None:
            errs.append("preview_reviewed_at はタイムゾーンつきの時刻（例 2026-10-19T10:05:00+09:00）")
        elif rv > now + timedelta(minutes=5):
            errs.append("preview_reviewed_at が未来になっている")
        elif now - rv > MAX_DIFF_AGE:
            errs.append(f"プレビューの確認（{rv.astimezone(JST):%m/%d %H:%M}）から {MAX_DIFF_AGE.seconds // 3600} 時間を過ぎた。"
                        "お客様がカイポケで手を入れた分を見落とさないよう、プレビューを取り直して確かめる")
        if op["replace_days"]:
            errs += window_problems(now)
        return errs

    def request(self, op):
        return ("POST", "/integrations/smart-inbound-apply",
                {"weekStart": op["week_start"], "sheetId": op.get("sheet_id"), "dryRun": False})

    def snapshot(self, api, op):
        snaps = list_snapshots(api, op["week_start"])
        return {"week_start": op["week_start"], "has_checkins": bool(snaps.get("hasCheckins")),
                "snapshot_ids": [str(s.get("id")) for s in snaps.get("snapshots") or []],
                "taken_at": self.clock().isoformat()}

    def execute(self, api, op):
        """取り込む直前にもう一度: 時間・プレビューがまだ有効か・その後に取り込みが走っていないか。"""
        if op.get("replace_days"):
            problems = window_problems(self.clock())
            if problems:
                raise RkError("取り込みを止めました: " + " / ".join(problems))
        st = preview_status(api, op["preview_job_id"])
        pv = st.get("preview") or {}
        if st.get("status") != "completed" or not pv:
            raise RkError("取り込みを止めました: プレビューの結果が残っていません。プレビューを取り直して確かめる")
        if str(pv.get("sheetId") or "") != str(op.get("sheet_id") or ""):
            raise RkError("取り込みを止めました: プレビューと案の差分シートが違います。プレビューを取り直して確かめる")
        done = _dt(st.get("completedAt"))
        if done is not None:
            later = [j for j in inbound_jobs(api, op["week_start"])
                     if j.get("status") == "completed" and _after(j.get("completed_at") or j.get("created_at"), done)]
            if later:
                raise RkError("取り込みを止めました: プレビューの後に、この週はもう取り込まれています。プレビューを取り直して確かめる")
        return super().execute(api, op)

    def check_response(self, op, response):
        if not isinstance(response, dict) or response.get("dryRun") is not False:
            return ["応答が取り込みの結果ではない"]
        out = []
        failed = int(((response.get("diff") or {}).get("failed")) or 0)
        if failed:
            out.append(f"打刻のある日の差分のうち {failed} 件を取り込めなかった（連携結果レポートで確かめる）")
        return out

    def _blocked(self, op, before) -> bool:
        return bool(op.get("protected_days")) or bool((before or {}).get("has_checkins"))

    def undo_note(self, op, before: dict | None = None) -> str | None:
        return NO_UNDO_CHECKINS if self._blocked(op, before) else None

    def inverse(self, op, before):
        if self._blocked(op, before):
            return None
        return [{"op": "kaipoke-import-undo", "week_start": op["week_start"],
                 "known_snapshot_ids": list((before or {}).get("snapshot_ids") or [])}]

    def verify(self, api, op):
        ref = _dt(op["preview_reviewed_at"])
        problems = []
        jobs = [j for j in inbound_jobs(api, op["week_start"])
                if (j.get("params") or {}).get("op") == "smart-apply" and j.get("status") == "completed"
                and ref is not None and _after(j.get("created_at"), ref)]
        if not jobs:
            problems.append("この週の取り込みの記録が見つからない")
        snaps = list_snapshots(api, op["week_start"]).get("snapshots") or []
        if not any(s.get("kind") == "smart" and ref is not None and _after(s.get("createdAt"), ref) for s in snaps):
            problems.append("取り込みの直前の盤面の保存が見つからない")
        return problems


@_register
class KaipokeImportUndo(Op):
    """取り込みを元に戻す（画面の「取り込み前に戻す」と同じ）。kaipoke-import の戻し方。

    API: POST /integrations/inbound-snapshots/{id}/restore（integrations.py:2522）。週を白紙にして保存した盤面を書き戻す。
    op: {"op":"kaipoke-import-undo","week_start":"2026-10-19","known_snapshot_ids":[取り込みの前からあった保存]}
        または {"snapshot_id": "…"}（直接指定）。known_snapshot_ids のときは、その後にできた kind=smart の保存のうち
        いちばん古いもの（= その取り込みの直前の盤面）を選ぶ。
    打刻のある週は 422（サーバーが止める）。保存は週ごとに 5 世代まで（古いものは消える）。
    """

    name = "kaipoke-import-undo"
    summary = "取り込み前に戻す（その週の予定を取り込みの直前の状態へ）"
    undoable = False

    def validate(self, op):
        errs = []
        ws = _date(op.get("week_start"))
        if ws is None or ws.weekday() != 0:
            errs.append("week_start は月曜日（YYYY-MM-DD）")
        if not op.get("snapshot_id") and not isinstance(op.get("known_snapshot_ids"), list):
            errs.append("snapshot_id か known_snapshot_ids がありません")
        return errs

    def request(self, op):
        sid = op.get("snapshot_id") or "<取り込みの直前の保存>"
        return ("POST", f"/integrations/inbound-snapshots/{sid}/restore", None)

    def _pick(self, snaps: dict, op: dict) -> dict | None:
        rows = snaps.get("snapshots") or []
        if op.get("snapshot_id"):
            hits = [s for s in rows if str(s.get("id")) == str(op["snapshot_id"])]
        else:
            known = {str(x) for x in op.get("known_snapshot_ids") or []}
            hits = sorted((s for s in rows if s.get("kind") == "smart" and str(s.get("id")) not in known),
                          key=lambda s: str(s.get("createdAt") or ""))
        return hits[0] if hits else None

    def snapshot(self, api, op):
        snaps = list_snapshots(api, op["week_start"])
        s = self._pick(snaps, op)
        return {"snapshot_id": s.get("id") if s else None, "has_checkins": bool(snaps.get("hasCheckins"))}

    def execute(self, api, op):
        snaps = list_snapshots(api, op["week_start"])
        if snaps.get("hasCheckins"):
            raise RkError("戻すのを止めました: この週には打刻（訪問の実績）があるため、取り込み前には戻せません（画面からも元に戻せません）")
        s = self._pick(snaps, op)
        if s is None:
            raise RkError("戻すのを止めました: 取り込みの直前の保存が見つかりません（5 世代を過ぎて消えた可能性）")
        return api.call("POST", f"/integrations/inbound-snapshots/{s['id']}/restore", None)

    def check_response(self, op, response):
        if not isinstance(response, dict) or "restored" not in response:
            return ["応答が戻した結果ではない"]
        return []

    def undo_note(self, op, before: dict | None = None) -> str | None:
        return "取り込み前に戻した後は、もう一度取り込むことでしか今の状態に戻らない"

    def verify(self, api, op):
        snaps = list_snapshots(api, op["week_start"])
        rows = snaps.get("snapshots") or []
        if op.get("snapshot_id"):
            hits = [s for s in rows if str(s.get("id")) == str(op["snapshot_id"])]
        else:
            known = {str(x) for x in op.get("known_snapshot_ids") or []}
            hits = [s for s in rows if s.get("kind") == "smart" and str(s.get("id")) not in known]
        if not any(s.get("restoredAt") for s in hits):
            return ["取り込み前に戻した印が見つからない"]
        return []


# ---------------------------------------------------------------------------
# 小さなコマンド: PYTHONPATH=. python -m rk.ops.kaipoke_import preview --week-start 2026-10-19 --approved "…"
#   カイポケのロボットを動かす（1〜2 分）→ 了承の後で。出力に利用者名が入る（画面に見せるだけ）。
#   --op-out <file> で kaipoke-import の op（JSON）を書く（reviewed_at は今の時刻・了承を取ってから案に入れる）。
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from rk.config import load_config
    from rk.session import open_api

    ap = argparse.ArgumentParser(prog="python -m rk.ops.kaipoke_import", description="カイポケからの取り込みのプレビュー")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preview", help="取り込みのプレビュー（カイポケから読む・1〜2 分）")
    p.add_argument("--week-start", required=True)
    p.add_argument("--approved", required=True, help="誰が・いつ了承したか（カイポケのロボットを動かすため）")
    p.add_argument("--op-out", help="kaipoke-import の op を書く先（../docs/reports/agent/ か runs/）")
    s = sub.add_parser("snapshots", help="取り込み前の保存の一覧（戻せるか）")
    s.add_argument("--week-start", required=True)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ws = _date(a.week_start)
    if ws is None or ws.weekday() != 0:
        raise SystemExit("--week-start は月曜日（YYYY-MM-DD）")

    with open_api(load_config()) as api:
        if a.cmd == "preview":
            res = run_preview(api, a.week_start)
            out: Any = {"approved": a.approved, "job_id": res["job_id"], "completed_at": res["completed_at"],
                        "summary": summarize_preview(res["preview"])}
            if a.op_out:
                op = build_import_op(a.week_start, res, now_jst().isoformat(timespec="seconds"))
                with open(a.op_out, "w", encoding="utf-8") as f:
                    json.dump(op, f, ensure_ascii=False, indent=2)
                out["op_out"] = a.op_out
        else:
            out = list_snapshots(api, a.week_start)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


__all__ = ["KaipokeImport", "KaipokeImportUndo", "build_import_op", "list_snapshots", "preview_status",
           "run_preview", "start_preview", "summarize_preview", "wait_preview", "window_problems"]

if __name__ == "__main__":
    raise SystemExit(main())
