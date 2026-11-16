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
- 週を空にする clear-week・型から作る generate-week は下の節（青ピン廃止 2026-10-12）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from rk import RkError
from rk.opsbase import Api, ApiError, Op, register

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


# ---- 週を空にする / 型から週を作る（青ピン廃止 BP-3/BP-4・2026-10-12） ----
# 根拠: backend/app/api/v1/week_clear.py・schedule.py（generate-week-only）。どちらも admin。
#   POST /schedule/v2/weeks/{y}/{w}/clear       {dry_run, office_id?} → {to_delete, kaipoke_linked, kept_checked_in, snapshot_id}
#   POST /schedule/v2/weeks/{y}/{w}/clear/undo  {snapshot_id} → {restored}
#   POST /schedule/generate-week-only           {iso_year, iso_week, office_id?, dry_run} → dry_run は {to_add}・空でない週は 409
# 期間で組む（agent-period-build-design-2026-11-16 §3-2）: どちらも任意の date_from・date_to（YYYY-MM-DD・組で・
# その ISO 週の月〜日の中・from ≦ to）を受ける。付けると、その日だけを空にする／作る（範囲の外の日はそのまま）。
# 付けないときは週全体（今までどおり）。生成の 409 は「範囲の中が空でない」になる。
# dry_run: true は DB を変えない。ただし POST の本文で決まるため rk post-read の許可リストには入れず、
# この操作の中（snapshot）で api.call(dry_run=true) を押す。


def _week_ints(op: dict) -> list[str]:
    errs: list[str] = []
    for k in ("iso_year", "iso_week"):
        v = op.get(k)
        if not isinstance(v, int) or isinstance(v, bool):
            errs.append(f"{k} は整数です（{v!r}）")
    if errs:
        return errs
    try:
        date.fromisocalendar(op["iso_year"], op["iso_week"], 1)
    except ValueError as e:
        errs.append(f"ISO の週が正しくありません: {e}")
    return errs


def _office(op: dict) -> dict:
    return {"office_id": op["office_id"]} if op.get("office_id") else {}


def _range_errors(op: dict) -> list[str]:
    """date_from・date_to（任意）の検査: 組で・日付・その週の中・from ≦ to。"""
    has_f, has_t = op.get("date_from") is not None, op.get("date_to") is not None
    if not has_f and not has_t:
        return []
    if has_f != has_t:
        return ["date_from と date_to は組で書きます（片方だけは不可。週全体なら両方とも書かない）"]
    days = []
    for k in ("date_from", "date_to"):
        v = op[k]
        try:
            if not isinstance(v, str) or len(v) != 10:
                raise ValueError
            days.append(date.fromisoformat(v))
        except ValueError:
            return [f"{k} は YYYY-MM-DD の日付です（{v!r}）"]
    try:
        monday = date.fromisocalendar(op["iso_year"], op["iso_week"], 1)
    except (KeyError, TypeError, ValueError):
        return []  # 週の誤りは _week_ints が知らせる
    sunday = monday + timedelta(days=6)
    errs = [f"{k} {d} は {monday}〜{sunday} の週の外です" for k, d in zip(("date_from", "date_to"), days)
            if not monday <= d <= sunday]
    if days[0] > days[1]:
        errs.append(f"date_from {days[0]} が date_to {days[1]} より後です")
    return errs


def _range(op: dict) -> dict:
    """本文に足す範囲（無ければ空 = 週全体）。"""
    if op.get("date_from") and op.get("date_to"):
        return {"date_from": str(op["date_from"]), "date_to": str(op["date_to"])}
    return {}


def _range_ja(op: dict) -> str:
    r = _range(op)
    return f"{r['date_from']}〜{r['date_to']} " if r else ""


def _clear_path(op: dict) -> str:
    return f"/schedule/v2/weeks/{op['iso_year']}/{op['iso_week']}/clear"


@register
class ClearWeek(Op):
    name = "clear-week"
    summary = "この週を空にする（打刻・実績のある訪問と取消の記録は残る）"
    undoable = True

    def validate(self, op: dict) -> list[str]:
        errs = _week_ints(op)
        if op.get("office_id") is not None and not isinstance(op["office_id"], str):
            errs.append("office_id は拠点 ID（文字列）です")
        return errs + _range_errors(op)

    def request(self, op: dict) -> tuple[str, str, dict | None]:
        return ("POST", _clear_path(op), {"dry_run": False, **_office(op), **_range(op)})

    def snapshot(self, api: Api, op: dict) -> dict:
        pv = api.call("POST", _clear_path(op), {"dry_run": True, **_office(op), **_range(op)}) or {}
        return {"dry_run": {k: pv.get(k) for k in ("to_delete", "kaipoke_linked", "kept_checked_in")}}

    def execute(self, api: Api, op: dict) -> Any:
        res = api.call("POST", _clear_path(op), {"dry_run": False, **_office(op), **_range(op)})
        if isinstance(res, dict):
            op["_snapshot_id"] = res.get("snapshot_id")  # inverse() が読む（応答は inverse に渡らないため）
        return res

    def check_response(self, op: dict, response: Any) -> list[str]:
        if not isinstance(response, dict):
            return ["応答の形が想定外です"]
        return []

    def inverse(self, op: dict, before: dict) -> list[dict] | None:
        sid = op.get("_snapshot_id")
        if not sid:
            return []  # 消すものが無かった（元に戻すものも無い）
        return [{"op": "clear-week-undo", "iso_year": op["iso_year"], "iso_week": op["iso_week"], "snapshot_id": sid}]

    def verify(self, api: Api, op: dict) -> list[str]:
        pv = api.call("POST", _clear_path(op), {"dry_run": True, **_office(op), **_range(op)}) or {}
        left = int(pv.get("to_delete") or 0)
        return [f"{_range_ja(op)}まだ消える訪問が {left} 件あります"] if left else []


@register
class ClearWeekUndo(Op):
    name = "clear-week-undo"
    summary = "この週を空にする前へ戻す（消した訪問だけ書き戻す）"
    undoable = False

    def validate(self, op: dict) -> list[str]:
        errs = _week_ints(op)
        if not isinstance(op.get("snapshot_id"), str) or not op.get("snapshot_id"):
            errs.append("snapshot_id（空にしたときの応答）が要ります")
        return errs

    def request(self, op: dict) -> tuple[str, str, dict | None]:
        return ("POST", _clear_path(op) + "/undo", {"snapshot_id": op["snapshot_id"]})

    def check_response(self, op: dict, response: Any) -> list[str]:
        if not isinstance(response, dict) or "restored" not in response:
            return ["応答の形が想定外です"]
        return []

    def undo_note(self, op: dict, before: dict | None = None) -> str | None:
        return "画面からは元に戻せません"


def _nothing_to_add(op: dict) -> bool:
    """反映の前の確認（dry_run）で、型から作るものが 0 件だった（範囲が日曜だけ等）。"""
    v = op.get("_to_add")
    return v is not None and int(v or 0) == 0


@register
class GenerateWeek(Op):
    name = "generate-week"
    summary = "空の週に、固定訪問の型から訪問を作る（担当は付けない）"
    undoable = False

    def validate(self, op: dict) -> list[str]:
        errs = _week_ints(op)
        if op.get("office_id") is not None and not isinstance(op["office_id"], str):
            errs.append("office_id は拠点 ID（文字列）です")
        return errs + _range_errors(op)

    def request(self, op: dict) -> tuple[str, str, dict | None]:
        return ("POST", "/schedule/generate-week-only",
                {"iso_year": op["iso_year"], "iso_week": op["iso_week"], "dry_run": False, **_office(op), **_range(op)})

    def snapshot(self, api: Api, op: dict) -> dict:
        _, path, body = self.request(op)
        try:
            pv = api.call("POST", path, {**body, "dry_run": True}) or {}
        except ApiError as e:
            if e.status == 409:
                where = f"{_range_ja(op)}の日" if _range(op) else "この週"
                raise RkError(f"{where}にはもう予定があります（取消の記録と打刻・実績のある訪問以外）。"
                              "作り直すなら先に clear-week（同じ範囲）で空にします") from e
            raise
        op["_to_add"] = pv.get("to_add")  # check_response・verify が読む（範囲に作るものが無い日もある）
        return {"to_add": pv.get("to_add")}

    def check_response(self, op: dict, response: Any) -> list[str]:
        if not isinstance(response, dict):
            return ["応答の形が想定外です"]
        out = []
        if int(response.get("visits_created") or 0) == 0 and not _nothing_to_add(op):
            out.append("作った訪問が 0 件です")
        if response.get("unplaced_multi_staff_patients"):
            out.append(f"2 名体制で片側が欠けて作れなかった方が {len(response['unplaced_multi_staff_patients'])} 名います")
        return out

    def inverse(self, op: dict, before: dict) -> list[dict] | None:
        # clear-week で戻すと、同じ週の他の新しい訪問まで消えるため式にしない
        return None

    def undo_note(self, op: dict, before: dict | None = None) -> str | None:
        return "画面からは元に戻せません"

    def verify(self, api: Api, op: dict) -> list[str]:
        if _range(op):
            # 範囲の中に訪問があるか: 同じ範囲で「空にする」の確認（dry_run・DB は変わらない）の件数を読む。
            # 作った訪問は空にすると消える側に数えられる。中継の中でも通る読み方（dry_run の POST）。
            pv = api.call("POST", _clear_path(op), {"dry_run": True, **_office(op), **_range(op)}) or {}
            if int(pv.get("to_delete") or 0) or _nothing_to_add(op):
                return []
            if "_to_add" not in op:  # rk verify で案を読み直したとき（覚えた値が無い）: 型から作るものがあるかを読む
                _, path, body = self.request(op)
                try:
                    if int((api.call("POST", path, {**body, "dry_run": True}) or {}).get("to_add") or 0) == 0:
                        return []  # 範囲に型の枠が無い（日曜だけ等）
                except ApiError as e:
                    if e.status != 409:
                        raise
            return [f"{_range_ja(op)}に作った訪問がありません"]
        monday = date.fromisocalendar(op["iso_year"], op["iso_week"], 1)
        return [] if _week_visits(api, monday) else [f"{monday} の週に訪問がありません"]
