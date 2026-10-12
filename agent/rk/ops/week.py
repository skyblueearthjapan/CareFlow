"""週を作る（前の週をコピー）の操作。画面の「週を作る」ダイアログ（MakeWeekDialog.tsx）と同じ API を使う。

    GET  /schedule/v2/copy-week/sources?target_week_start=YYYY-MM-DD   写す元の候補週と件数（読むだけ）
    POST /schedule/v2/copy-week/preview                                 確認画面の内容（読むだけ・rk post-read 可）
    POST /schedule/v2/copy-week                                         実行（confirm: true・expected_counts 付き）

根拠: backend/app/api/v1/copy_week.py・app/schemas/v2/copy_week.py・services/scheduling/copy_week.py（develop c17aafe）。
- 写す先は今週以降・写す元は写す先の 1〜8 週前（サーバーが 422 で断る）。
- 写す先に予定がある週は「置き換え（replace）」または打刻があれば「足すだけ（add_only）」になる。
  置き換えでは同じコードの仮コースと確定コースが並ぶ経路が残っている（session-2026-10-09-HANDOFF §4-4）
  → この操作は **写す先が空の週だけ** 動かす。上書きは "allow_overwrite": true を書いたときだけ。
- 画面の既定は「自動スタッフ割当 = する」だが、ならしの手順では **しない**（勤務時刻・予定・半休を見ないため）。
  この操作の既定は assign_staff: false・fill_from_fixed: false（画面の「固定訪問から補う」の既定と同じ）。
- 戻すのは API ではできない（バックアップから。画面の「コピー前に戻す」は置き換えの週だけ）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from rk import RkError
from rk.opsbase import Api, Op, register

MAX_WEEKS_BACK = 8  # services/scheduling/copy_week.py と同じ
BIG_DIFF_RATIO = 0.15  # 写す元と写す先の件数がこれより離れたら照合で知らせる


def _monday(op: dict, side: str) -> date:
    return date.fromisocalendar(int(op[f"{side}_iso_year"]), int(op[f"{side}_iso_week"]), 1)


def _today() -> date:
    return date.today()


def _body(op: dict) -> dict:
    body = {
        "source_week_start": _monday(op, "source").isoformat(),
        "target_week_start": _monday(op, "target").isoformat(),
        "exclude_visit_ids": list(op.get("exclude_visit_ids") or []),
        "fill_from_fixed": bool(op.get("fill_from_fixed", False)),
    }
    return body


def _week_visits(api: Api, monday: date) -> int | None:
    """その週の訪問件数（取消・予定外を除く）。copy-week/sources は「target_week_start の前 8 週」を返すので、
    1 週後を target にして読む（読むだけ）。訪問の無い週は一覧に出ない → 0。"""
    res = api.get("/schedule/v2/copy-week/sources",
                  {"target_week_start": (monday + timedelta(days=7)).isoformat()}) or {}
    for it in res.get("items") or []:
        if str(it.get("week_start"))[:10] == monday.isoformat():
            return int(it.get("visits") or 0)
    return 0


@register
class CopyWeek(Op):
    name = "copy-week"
    summary = "週を作る（前の週をコピー）"
    undoable = False

    def validate(self, op: dict) -> list[str]:
        errs: list[str] = []
        for side in ("source", "target"):
            for k in ("iso_year", "iso_week"):
                v = op.get(f"{side}_{k}")
                if not isinstance(v, int) or isinstance(v, bool):
                    errs.append(f"{side}_{k} は整数です（{v!r}）")
        if errs:
            return errs
        try:
            src, dst = _monday(op, "source"), _monday(op, "target")
        except ValueError as e:
            return [f"ISO の週が正しくありません: {e}"]
        diff = (dst - src).days // 7
        if diff < 1 or diff > MAX_WEEKS_BACK:
            errs.append(f"写す元は写す先より前の {MAX_WEEKS_BACK} 週の中から選びます（今は {diff} 週の差）")
        this_monday = _today() - timedelta(days=_today().weekday())
        if dst < this_monday:
            errs.append(f"写す先は今週以降の週だけです（{dst} の週は過ぎています）")
        for k in ("fill_from_fixed", "assign_staff", "allow_overwrite"):
            if k in op and not isinstance(op[k], bool):
                errs.append(f"{k} は true / false です")
        ex = op.get("exclude_visit_ids")
        if ex is not None and (not isinstance(ex, list) or not all(isinstance(x, str) for x in ex)):
            errs.append("exclude_visit_ids は訪問 ID（文字列）のリストです")
        if op.get("assign_staff"):
            errs.append("assign_staff: true（自動スタッフ割当）は使わない約束です（勤務時刻・予定・半休を見ない）。"
                        "担当はならし案で付けます")
        return errs

    def request(self, op: dict) -> tuple[str, str, dict | None]:
        return ("POST", "/schedule/v2/copy-week",
                {**_body(op), "assign_staff": bool(op.get("assign_staff", False)), "confirm": True})

    def _preview(self, api: Api, op: dict) -> dict:
        return api.call("POST", "/schedule/v2/copy-week/preview", _body(op)) or {}

    def snapshot(self, api: Api, op: dict) -> dict:
        pv = self._preview(api, op)
        existing = pv.get("existing") or {}
        total = int(existing.get("total") or 0)
        if total and not op.get("allow_overwrite"):
            raise RkError(f"写す先の週にもう訪問が {total} 件あります（{pv.get('mode')}）。週のコピーは空の週だけに使います。"
                          "上書きするなら PO の了承のうえで案に \"allow_overwrite\": true")
        return {"target_visits": total, "mode": pv.get("mode"), "existing": existing,
                "expected": {"copy_count": pv.get("copy_count"), "fill_count": pv.get("fill_count"),
                             "replace_count": existing.get("replace", 0),
                             "needs_manual_count": len(pv.get("needs_manual_staff") or [])}}

    def execute(self, api: Api, op: dict) -> Any:
        # 押す直前に確認画面と同じ内容を読み直し、その件数を expected_counts に付ける（画面と同じ）
        before = self.snapshot(api, op)
        _, path, body = self.request(op)
        body["expected_counts"] = before["expected"]
        return api.call("POST", path, body)

    def check_response(self, op: dict, response: Any) -> list[str]:
        if not isinstance(response, dict):
            return ["応答の形が想定外です"]
        out = []
        if int(response.get("created") or 0) + int(response.get("filled") or 0) == 0:
            out.append("写した訪問が 0 件です")
        if response.get("differs_from_preview"):
            out.append("確認の件数と実行時の件数が違いました（書き込みは済み・rk verify で確かめる）")
        return out

    def inverse(self, op: dict, before: dict) -> list[dict] | None:
        return None

    def undo_note(self, op: dict, before: dict | None = None) -> str | None:
        return "週を消すにはバックアップから"

    def verify(self, api: Api, op: dict) -> list[str]:
        src, dst = _monday(op, "source"), _monday(op, "target")
        got = _week_visits(api, dst)
        if not got:
            return [f"{dst} の週に訪問がありません"]
        want = _week_visits(api, src) or 0
        if want and abs(got - want) > max(3, want * BIG_DIFF_RATIO):
            return [f"写す先 {got} 件・写す元 {want} 件と大きく違います（取消・予定外・特別訪問の○・"
                    "終了した利用者は写さないので、差の理由を確かめてください）"]
        return []
