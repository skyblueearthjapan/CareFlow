"""訪問の実績の時刻（到着・退出）を合わせる部品（/records の「実績時刻を合わせる」と同じ）。

- ``actual-time-adjust`` … PUT /visits/{id}/actual-time body {kind, time, reason_code?, reason_text}
  （backend/app/api/v1/visits.py:2049・検証は services/checkin/adjust.py:234）。
- ``actual-time-reset``  … DELETE /visits/{id}/actual-time?kind=（visits.py:2094）= 読み取った時刻に戻す。
  合わせていない時刻には何もしない（冪等）。actual-time-adjust の戻し方に使う。

約束: 予定（start_time/end_time）は動かない。打刻（visit_checkins）にも触れない（調整の行を足すだけ）。
今の実績は GET /visits/{id}（visits.py:780）の actual_*（調整後・無ければ読取時刻・UTC の時刻）で読む。
サーバーの決まり（管理者として）: 到着は読み取った時刻より後にできない・退出より前。退出は到着より後・
読み取った時刻の 30 分後まで・今より後は不可。読み取りの無い到着は管理者だけ手で入れられる（manual）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from rk import RkError
from rk.opsbase import Api, Op, register
from rk.ops.visits import check_hhmm, check_uuid, visit_or_none

JST = timezone(timedelta(hours=9))
KINDS = ("arrival", "departure")
KIND_JA = {"arrival": "到着", "departure": "退出"}
#: services/checkin/adjust の ADJUST_REASON_LABELS（models/visit_time_adjustment.py:36）。
REASON_CODES = ("intercom_wait", "read_later", "no_read", "other")
REASON_TEXT_MAX = 200


def jst_hhmm(v: Any) -> str | None:
    """UTC/タイムゾーンつきの時刻 → 日本時間の HH:MM。"""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(JST).strftime("%H:%M")


def actual_state(v: dict, kind: str) -> dict:
    return {"at": jst_hhmm(v.get(f"actual_{kind}_at")), "read_at": jst_hhmm(v.get(f"actual_{kind}_read_at")),
            "adjusted": bool(v.get(f"actual_{kind}_adjusted")), "manual": bool(v.get(f"actual_{kind}_manual"))}


def _path(op: dict) -> str:
    return f"/visits/{op['visit_id']}/actual-time"


def _snapshot(api: Api, op: dict) -> dict:
    v = visit_or_none(api, op["visit_id"])
    if v is None:
        return {"visit_id": op["visit_id"], "kind": op.get("kind"), "missing": True}
    return {"visit_id": op["visit_id"], "kind": op.get("kind"), "visit_date": v.get("visit_date"),
            "status": v.get("status"), **actual_state(v, op["kind"])}


def _back_to(op: dict, before: dict) -> list[dict]:
    """反映前の状態へ戻す操作（合わせてあった→その時刻へ・合わせていなかった→読み取った時刻へ）。"""
    if before.get("missing"):
        return []
    if before.get("adjusted") and before.get("at"):
        return [{"op": "actual-time-adjust", "visit_id": op["visit_id"], "kind": op["kind"], "time": before["at"],
                 "reason": "元に戻す（前に合わせた時刻へ）", "reason_code": "other",
                 **({"manual": True} if before.get("manual") else {})}]
    return [{"op": "actual-time-reset", "visit_id": op["visit_id"], "kind": op["kind"]}]


def _hm(s: str) -> tuple[int, int]:
    return int(s[0:2]), int(s[3:5])


@register
class ActualTimeAdjust(Op):
    """実績の到着・退出の時刻を合わせる（予定は動かない）。

    op: {"op":"actual-time-adjust","visit_id":"…","kind":"arrival|departure","time":"09:00",
         "reason":"ご本人から 9:00 に着いたと連絡（インターホン待ち）","reason_code":"intercom_wait"（任意）,
         "manual": true（任意・到着の読み取りが無い訪問へ手で入れるときだけ）}
    """

    name = "actual-time-adjust"
    summary = "実績の時刻を合わせる（到着・退出）"
    undoable = True

    def validate(self, op):
        errs: list[str] = []
        check_uuid("visit_id", op.get("visit_id"), errs)
        if op.get("kind") not in KINDS:
            errs.append("kind は arrival（到着）か departure（退出）")
        check_hhmm("time", op.get("time"), errs)
        reason = str(op.get("reason") or "").strip()
        if not reason:
            errs.append("reason（合わせる理由）がありません")
        elif len(reason) > REASON_TEXT_MAX:
            errs.append(f"reason は {REASON_TEXT_MAX} 字まで")
        if op.get("reason_code") is not None and op.get("reason_code") not in REASON_CODES:
            errs.append(f"reason_code は {' / '.join(REASON_CODES)} のどれか")
        return errs

    def request(self, op):
        body: dict[str, Any] = {"kind": op["kind"], "time": str(op["time"])[:5],
                                "reason_text": str(op.get("reason") or "").strip()}
        if op.get("reason_code"):
            body["reason_code"] = op["reason_code"]
        return ("PUT", _path(op), body)

    def snapshot(self, api, op):
        return _snapshot(api, op)

    def execute(self, api, op):
        """合わせる直前に読み直す: 訪問がある・実績の前提がある・時刻の前後がおかしくない。"""
        v = visit_or_none(api, op["visit_id"])
        if v is None or v.get("deleted_at"):
            raise RkError("合わせるのを止めました: 訪問が見つかりません（消された可能性）")
        kind = op["kind"]
        t = str(op["time"])[:5]
        arr, dep = actual_state(v, "arrival"), actual_state(v, "departure")
        if kind == "arrival":
            if not arr["read_at"] and not arr["manual"] and not op.get("manual"):
                raise RkError("合わせるのを止めました: 到着の読み取りがありません。手で到着を入れるなら、"
                              "管理者に確かめてから manual: true にする")
            if arr["read_at"] and _hm(t) > _hm(arr["read_at"]):
                raise RkError(f"合わせるのを止めました: 到着は読み取った時刻（{arr['read_at']}）より後にはできません")
            if dep["at"] and _hm(t) >= _hm(dep["at"]):
                raise RkError(f"合わせるのを止めました: 到着は退出（{dep['at']}）より前の時刻にしてください")
        else:
            if not arr["at"]:
                raise RkError("合わせるのを止めました: 到着の記録がありません（先に到着を合わせる）")
            if _hm(t) <= _hm(arr["at"]):
                raise RkError(f"合わせるのを止めました: 退出は到着（{arr['at']}）より後の時刻にしてください")
        return super().execute(api, op)

    def check_response(self, op, response):
        if not isinstance(response, dict):
            return ["応答が訪問の形ではない"]
        got = jst_hhmm(response.get(f"actual_{op['kind']}_at"))
        if got and got != str(op["time"])[:5]:
            return [f"{KIND_JA[op['kind']]}が {got} のまま（{op['time']} になっていない）"]
        return []

    def inverse(self, op, before):
        return _back_to(op, before)

    def verify(self, api, op):
        v = visit_or_none(api, op["visit_id"])
        if v is None:
            return ["訪問が見つからない"]
        st = actual_state(v, op["kind"])
        want = str(op["time"])[:5]
        out = []
        if st["at"] != want:
            out.append(f"{KIND_JA[op['kind']]}の実績が {st['at']}（案は {want}）")
        if not st["adjusted"]:
            out.append(f"{KIND_JA[op['kind']]}が合わせた時刻になっていない")
        return out


@register
class ActualTimeReset(Op):
    """合わせた実績の時刻をやめて、読み取った時刻に戻す（actual-time-adjust の戻し方）。

    op: {"op":"actual-time-reset","visit_id":"…","kind":"arrival|departure"}
    """

    name = "actual-time-reset"
    summary = "実績の時刻を読み取った時刻に戻す"
    undoable = True

    def validate(self, op):
        errs: list[str] = []
        check_uuid("visit_id", op.get("visit_id"), errs)
        if op.get("kind") not in KINDS:
            errs.append("kind は arrival（到着）か departure（退出）")
        return errs

    def request(self, op):
        return ("DELETE", f"{_path(op)}?kind={op['kind']}", None)

    def snapshot(self, api, op):
        return _snapshot(api, op)

    def inverse(self, op, before):
        if before.get("adjusted") and before.get("at"):
            return _back_to(op, before)
        return []

    def verify(self, api, op):
        v = visit_or_none(api, op["visit_id"])
        if v is None:
            return ["訪問が見つからない"]
        if actual_state(v, op["kind"])["adjusted"]:
            return [f"{KIND_JA[op['kind']]}がまだ合わせた時刻のまま"]
        return []
