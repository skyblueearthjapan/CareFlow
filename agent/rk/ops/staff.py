"""職員の休みと急休代替の操作（設計書 §3-4・S4）。

- 休みを登録しても**訪問はそのまま元の担当に残る**（プールにも行かない）。振り分けは別の操作
  （ならし案の assign-staff-week / move-visit-week / return-to-pool、または staff-off-week）。
- 時刻・日付の書き方は rk/ops/visits.py の先頭を参照。
"""

from __future__ import annotations

from typing import Any

from rk.opsbase import Api, Op, register
from rk.ops.visits import (
    check_hhmm,
    check_staff_ref,
    fields,
    hhmm,
    resolve_staff,
    staff_param,
    to_date,
    visit_brief,
)

TYPES = {"休み": "休み", "off": "休み", "午前休": "午前休", "am_off": "午前休", "午後休": "午後休",
         "pm_off": "午後休", "有給": "有給", "paid_off": "有給",
         "時間変更": "時間変更", "custom_time": "時間変更"}


def overrides_on(api: Api, staff_id: str, day: str) -> list[dict]:
    """その職員のその日の休み/時間変更（GET /staff/{id}/overrides staff_overrides.py:131）。"""
    rows = api.get(f"/staff/{staff_id}/overrides", {"from": day, "to": day, "limit": 500}) or []
    return [r for r in rows if str(r.get("date"))[:10] == day]


def override_brief(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "type": r.get("type"), "start_time": hhmm(r.get("start_time")),
            "end_time": hhmm(r.get("end_time")), "note": r.get("note")}


@register
class StaffDayOff(Op):
    """職員のその日の休み・半休・時間変更を登録する（職員スケジュールの休み・申請の承認と同じ記録）。

    API（staff_overrides.py）:
    - 新しく登録 POST /staff/{staff_id}/overrides（:181）body ``{date, type, start_time, end_time, note}``
    - すでにある日を変える PATCH /staff/{staff_id}/overrides/{override_id}（:212）
    - 取り消す DELETE /staff/{staff_id}/overrides/{override_id}（:265・**本人に取消のお知らせが届く**）
    どれにするかは反映の時に GET /staff/{id}/overrides（:131）で決める（同じ日の行は 1 つだけ）。
    受け取る値: ``staff_id``*（UUID/職員コード）・``date``*・``action``（"set" 既定 / "remove"）・
    ``type``（休み 既定 / 午前休 / 午後休 / 有給 / 時間変更。英語の off・am_off・pm_off・paid_off・
    custom_time も可）・
    ``start_time``・``end_time``（時間変更のとき HH:MM）・``note``（500 字まで）。
    注意: 休み（休み・午前休・午後休・有給）を登録すると、今日以降の日なら**休みの時間帯の本人の訪問は
    「担当なし」へ移る**（半休は時間帯の訪問だけ・コースは本人のまま／終日はコースごと担当なし）。
    休みの時間帯の固定イベントにも取消印が付き、管理者へ「未割り当てとしました」のお知らせが届く。
    今日より前の日は休みの記録だけ。時間変更は今までどおり行だけ（訪問は動かない）。
    取り消しても担当なしへ移した訪問は戻らない（職員スケジュールで割り当て直す）。
    戻し方: 反映前に行が無ければ remove、あれば元の type・時刻・メモで set（remove は本人へお知らせが飛ぶ）。
    """

    name = "staff-day-off"
    summary = "職員の休み・時間変更を登録する（休みの時間帯の訪問は担当なしへ移る）"

    def validate(self, op):
        f, errs = fields(op), []
        check_staff_ref("staff_id", f.get("staff_id"), errs, allow_none=False)
        if to_date(f.get("date")) is None:
            errs.append("date（YYYY-MM-DD）がありません")
        action = f.get("action", "set")
        if action not in ("set", "remove"):
            errs.append("action は set か remove です")
        if action == "set":
            t = f.get("type", "休み")
            if t not in TYPES:
                errs.append(f"type は 休み/午前休/午後休/有給/時間変更 のどれかです（{t}）")
            elif TYPES[t] == "時間変更":
                check_hhmm("start_time", f.get("start_time"), errs)
                check_hhmm("end_time", f.get("end_time"), errs)
            if f.get("note") is not None and len(str(f["note"])) > 500:
                errs.append("note は 500 字までです")
        return errs

    @staticmethod
    def _body(f: dict) -> dict:
        body: dict = {"date": str(to_date(f["date"])), "type": TYPES[f.get("type", "休み")]}
        for k in ("start_time", "end_time"):
            if f.get(k):
                body[k] = hhmm(f[k])
        if f.get("note"):
            body["note"] = str(f["note"])
        return body

    def request(self, op):
        f = fields(op)
        sid = staff_param(f.get("staff_id"))
        if f.get("action", "set") == "remove":
            return ("DELETE", f"/staff/{sid}/overrides/<{f.get('date')} の行>", None)
        return ("POST", f"/staff/{sid}/overrides", self._body(f))

    def execute(self, api, op):
        f = fields(op)
        sid = resolve_staff(api, f["staff_id"])
        day = str(to_date(f["date"]))
        rows = overrides_on(api, sid, day)
        if f.get("action", "set") == "remove":
            if not rows:
                return None  # もう無い（同じ結果）
            return api.call("DELETE", f"/staff/{sid}/overrides/{rows[0]['id']}", None)
        body = self._body(f)
        if rows:
            patch = {k: v for k, v in body.items() if k != "date"}
            for k in ("start_time", "end_time", "note"):
                patch.setdefault(k, None)
            return api.call("PATCH", f"/staff/{sid}/overrides/{rows[0]['id']}", patch)
        return api.call("POST", f"/staff/{sid}/overrides", body)

    def snapshot(self, api, op):
        f = fields(op)
        sid = resolve_staff(api, f["staff_id"])
        day = str(to_date(f["date"]))
        rows = overrides_on(api, sid, day)
        return {"staff_id": sid, "date": day, "override": override_brief(rows[0] if rows else None)}

    def inverse(self, op, before):
        if not before or not before.get("staff_id"):
            return None
        o = before.get("override")
        base = {"op": self.name, "staff_id": before["staff_id"], "date": before["date"],
                "label": f"元に戻す: {op.get('label') or '休みの登録'}"}
        if o is None:
            return [{**base, "action": "remove"}]
        inv = {**base, "action": "set", "type": o.get("type") or "休み"}
        for k in ("start_time", "end_time", "note"):
            if o.get(k):
                inv[k] = o[k]
        return [inv]

    def verify(self, api, op):
        f = fields(op)
        sid = resolve_staff(api, f["staff_id"])
        day = str(to_date(f["date"]))
        rows = overrides_on(api, sid, day)
        if f.get("action", "set") == "remove":
            return [f"{day} の休みが残っています"] if rows else []
        if not rows:
            return [f"{day} の休みが登録されていません"]
        want = TYPES[f.get("type", "休み")]
        return [] if rows[0].get("type") == want else [f"{day} の種類が案と違います（案 {want}・今 {rows[0].get('type')}）"]


@register
class StaffOffWeek(Op):
    """急休代替：その日を休みにして、その日の担当を **1 人** に任せる（今週の運転席「🛌 休みにする」と同じ）。

    API: POST /schedule/v2/staff-off-week（schedule_v2.py:1818、要求 auto_schedule_v2.py:699）
    body ``{staff_id, date, to_staff_id|null, acknowledge_constraint_warnings, op_group_id}``。1 回で:
    休み（override=off）の登録・その日その人が載っている planned の訪問の「本人の枠」だけを引き受け先へ
    （2 名体制の相方は残す）・その日のコースの担当を引き受け先へ・その日の固定イベントに取消印。
    受け取る値: ``staff_id``*（休む人）・``date``*・``to_staff_id``（引き受け先。null=全部「担当なし」）※別名 ``to_staff``・
    ``acknowledge_constraint_warnings``・``op_group_id``。
    前提: 過去日 422（当日は可）・引き受け先が新人/退職/本人/その日休みなら 422・
    NG・女性限定は 422（了承で再送）。打刻済み等は据え置き（応答の skipped_visit_ids）。
    複数人へ振り分けたいときはこれではなく、ならし（rk level day）→ staff-day-off＋assign-staff-week など。
    戻し方: **rk undo では戻せない（None）** → 戻すにはバックアップから。理由: 2 名体制の「本人の枠」だけの差し替え・
    固定イベントの取消印・手の上書き印（API に出ない）を、許可リストの操作では元どおりにできない。
    アプリの「戻る」（op_log・同じ op_group）なら 1 回で全部戻るが、それは**同じアカウントがその週で最後に
    した操作**のときだけ（エージェントのアカウントで画面を開いて「戻る」）。
    """

    name = "staff-off-week"
    summary = "急休代替（休み＋その日の担当を 1 人へ）"
    undoable = False

    @staticmethod
    def _to(f: dict) -> Any:
        return f["to_staff_id"] if "to_staff_id" in f else f.get("to_staff")

    def validate(self, op):
        f, errs = fields(op), []
        check_staff_ref("staff_id", f.get("staff_id"), errs, allow_none=False)
        if to_date(f.get("date")) is None:
            errs.append("date（YYYY-MM-DD）がありません")
        if "to_staff_id" not in f and "to_staff" not in f:
            errs.append("to_staff_id（全部担当なしにするなら null）がありません")
        else:
            check_staff_ref("to_staff_id", self._to(f), errs, allow_none=True)
            if self._to(f) is not None and self._to(f) == f.get("staff_id"):
                errs.append("休む本人には引き受けられません")
        return errs

    def _body(self, f: dict, staff_id: str, to_staff_id: Any) -> dict:
        body: dict = {"staff_id": staff_id, "date": str(to_date(f["date"])), "to_staff_id": to_staff_id}
        if f.get("acknowledge_constraint_warnings"):
            body["acknowledge_constraint_warnings"] = True
        if f.get("op_group_id"):
            body["op_group_id"] = str(f["op_group_id"])
        return body

    def request(self, op):
        f = fields(op)
        return ("POST", "/schedule/v2/staff-off-week",
                self._body(f, staff_param(f.get("staff_id")), staff_param(self._to(f))))

    def execute(self, api, op):
        f = fields(op)
        return api.call("POST", "/schedule/v2/staff-off-week",
                        self._body(f, resolve_staff(api, f["staff_id"]), resolve_staff(api, self._to(f))))

    @staticmethod
    def _day(api: Api, sid: str, day: str) -> tuple[list[dict], list[dict]]:
        d = to_date(day)
        iso = d.isocalendar()
        visits = api.get("/visits", {"staff_id": sid, "week_start": day, "week_end": day, "limit": 500}) or []
        courses = api.get("/courses", {"iso_year": iso[0], "iso_week": iso[1], "weekday": d.weekday(), "limit": 500}) or []
        return visits, [c for c in courses if str(c.get("assigned_staff_id") or "") == sid]

    def snapshot(self, api, op):
        f = fields(op)
        sid = resolve_staff(api, f["staff_id"])
        day = str(to_date(f["date"]))
        visits, courses = self._day(api, sid, day)
        rows = overrides_on(api, sid, day)
        notes = []
        return {"staff_id": sid, "date": day, "override": override_brief(rows[0] if rows else None),
                "visits": [visit_brief(v) for v in visits],
                "courses": [{"id": str(c["id"]), "code": c.get("code"), "office_id": c.get("office_id")} for c in courses],
                "notes": notes}

    def inverse(self, op, before):
        return None

    def verify(self, api, op):
        f = fields(op)
        sid = resolve_staff(api, f["staff_id"])
        day = str(to_date(f["date"]))
        errs = []
        rows = overrides_on(api, sid, day)
        if not rows or rows[0].get("type") != "休み":
            errs.append(f"{day} の休みが登録されていません")
        visits, courses = self._day(api, sid, day)
        if courses:
            errs.append(f"{day} に まだ本人が担当のコースが {len(courses)} 本あります")
        left = [v for v in visits if v.get("status") == "planned" and str(v.get("primary_staff_id") or "") == sid]
        if left:
            errs.append(f"{day} に まだ本人が主担当の予定が {len(left)} 件あります")
        return errs


__all__ = ["overrides_on"]
