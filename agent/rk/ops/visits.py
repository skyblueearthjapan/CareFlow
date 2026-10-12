"""訪問・コース・同行の操作（設計書 §3-4・S4/S5/S6）。

どの操作も「画面と同じ API」を 1 件ずつ呼ぶ。根拠の行番号は develop c17aafe 時点。
- 共通: op の値は最上位に書く。build_ops.py 互換のため ``op["body"]`` に入れてもよい（最上位が優先）。
- 時刻は "HH:MM"（"HH:MM:SS" も可）。日付は "YYYY-MM-DD"。曜日は 0=月..6=日。
- 職員は UUID か職員コード（例 "S005"）。コードは反映の時に GET /staff で UUID に直す。
- request() は表示と検査用（API に問い合わせない）。実行時に ID を引く必要がある操作は
  execute() を上書きしている（opsbase の約束どおり）。

戻し方の要点（正直に）:
- 今週だけの操作は ``source='manual_week'`` を刻む。元の出所（auto など）は API では戻せない
  （戻しても週の生成で守られる側に残る）。visits.manual_staff_override は API に出ないので読めない。
- return-to-pool（DELETE /visits）は API では戻せない（置き直しは別の訪問になる）→ バックアップから。
- 戻しの操作には acknowledge_constraint_warnings を付けない（NG・女性限定に触れるなら 422 で止まって人に聞く）。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from rk.opsbase import Api, ApiError, Op, register

# ---------------------------------------------------------------------------
# 共通の小道具（staff.py / special.py からも使う）
# ---------------------------------------------------------------------------


def fields(op: dict) -> dict:
    """op["body"]（build_ops.py 互換）と最上位をまとめる。最上位が優先。"""
    body = op.get("body") if isinstance(op.get("body"), dict) else {}
    return {**body, **{k: v for k, v in op.items() if k != "body"}}


def hhmm(t: Any) -> str | None:
    if t is None or t == "":
        return None
    s = str(t)
    return s[:5]


def is_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def iso_day(iso_year: int, iso_week: int, weekday: int) -> date:
    return date.fromisocalendar(int(iso_year), int(iso_week), int(weekday) + 1)


def check_hhmm(name: str, v: Any, errs: list[str], required: bool = True) -> None:
    if v is None or v == "":
        if required:
            errs.append(f"{name} がありません")
        return
    s = str(v)
    try:
        h, m = int(s[0:2]), int(s[3:5])
        ok = len(s) in (5, 8) and s[2] == ":" and 0 <= h <= 23 and 0 <= m <= 59
    except ValueError:
        ok = False
    if not ok:
        errs.append(f"{name} は HH:MM の形です（{v}）")


def check_uuid(name: str, v: Any, errs: list[str], required: bool = True) -> None:
    if v is None or v == "":
        if required:
            errs.append(f"{name} がありません")
        return
    if not is_uuid(v):
        errs.append(f"{name} は UUID です（{v}）")


def check_staff_ref(name: str, v: Any, errs: list[str], allow_none: bool) -> None:
    """UUID か職員コード。None は allow_none のときだけ（=担当なし）。"""
    if v is None:
        if not allow_none:
            errs.append(f"{name} がありません")
        return
    if not isinstance(v, str) or not v.strip():
        errs.append(f"{name} は UUID か職員コードです")


def check_week(f: dict, errs: list[str]) -> None:
    y, w = f.get("iso_year"), f.get("iso_week")
    if y is None or w is None:
        errs.append("iso_year と iso_week がありません")
        return
    try:
        date.fromisocalendar(int(y), int(w), 1)
    except (ValueError, TypeError):
        errs.append(f"週が正しくありません（{y} 年 第 {w} 週）")


def is_not_found(e: Exception) -> bool:
    return isinstance(e, ApiError) and e.status == 404


def resolve_staff(api: Api, ref: Any) -> str | None:
    """UUID はそのまま・職員コードは GET /staff（staff.py:87）で UUID へ。None は None（担当なし）。"""
    if ref is None:
        return None
    if is_uuid(ref):
        return str(ref)
    rows = api.get("/staff", {"limit": 500}) or []
    hits = [s for s in rows if str(s.get("code") or "") == str(ref) and not s.get("deleted_at")]
    if len(hits) != 1:
        raise ApiError(422, f"職員コード {ref} を 1 人に決められません（{len(hits)} 人）")
    return str(hits[0]["id"])


def staff_param(ref: Any) -> str | None:
    """request() 用の表示（None=担当なし・コードのままなら反映時に引く）。"""
    if ref is None:
        return None
    return str(ref) if is_uuid(ref) else f"<職員コード {ref}>"


def visits_on(api: Api, patient_id: str, day: date | str) -> list[dict]:
    """その患者のその日の訪問（削除済みは出ない）。GET /visits（visits.py:579）。"""
    d = str(day)[:10]
    return api.get("/visits", {"patient_id": patient_id, "week_start": d, "week_end": d, "limit": 500}) or []


def visit_or_none(api: Api, visit_id: str) -> dict | None:
    """GET /visits/{id}（visits.py:784）。削除済みは 404 → None。"""
    try:
        return api.get(f"/visits/{visit_id}")
    except ApiError as e:
        if is_not_found(e):
            return None
        raise


def staff_ids_of(v: dict) -> list[str]:
    return sorted(str(a.get("staff_id")) for a in (v.get("staff_assignments") or []) if a.get("staff_id"))


def visit_brief(v: dict) -> dict:
    keys = ("id", "patient_id", "patient_name", "visit_date", "start_time", "end_time", "status", "source",
            "course_id", "primary_staff_id", "visit_group_id", "required_staff_count")
    out = {k: v.get(k) for k in keys}
    out["staff_ids"] = staff_ids_of(v)
    for k in ("course_id", "primary_staff_id", "visit_group_id", "id", "patient_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    return out


def template_for_course(api: Api, course_id: str | None) -> str | None:
    """週のコース → コーステンプレート ID。CourseRead に template_id が無いので
    (拠点, ラベル=コード) で引く（GET /courses/{id} courses.py:271・GET /course-templates course_templates.py:93）。
    1 つに決まらなければ None（M・M2・臨は同じ M になりうる）。"""
    if not course_id:
        return None
    try:
        c = api.get(f"/courses/{course_id}")
    except ApiError as e:
        if is_not_found(e):
            return None
        raise
    tpls = api.get("/course-templates", {"office_id": c.get("office_id")}) or []
    exact = [t for t in tpls if str(t.get("label")) == str(c.get("code"))]
    if len(exact) == 1:
        return str(exact[0]["id"])
    return None


# ---------------------------------------------------------------------------
# 今週だけ担当を付け替える
# ---------------------------------------------------------------------------


@register
class AssignStaffWeek(Op):
    """今週だけ担当を付け替える（盤面のドラッグと同じ）。

    API: POST /schedule/v2/visit-assign-staff-week（backend/app/api/v1/schedule_v2.py:1673、
    要求 schemas/v2/auto_schedule_v2.py:670）。
    受け取る値: ``visit_id``*・``staff_id``（UUID/職員コード。null=担当なし）※別名 ``to_staff``・
    ``acknowledge_constraint_warnings``（NG・女性限定の 422 を了承済みで通す）・``op_group_id``。
    前提: planned だけ（打刻済み・完了は 422）。新人は 422。
    2 名体制は相方に触れない（1 件ずつ）。コース担当と固定訪問は変えない。
    戻し方: 元の担当へ同じ操作（manual_staff_override は読めないので、元が「コースの担当に従う」だった
    訪問も戻した後は「手で付けた」扱いになる。後でコース担当を変えてもこの訪問には伝わらない）。
    """

    name = "assign-staff-week"
    summary = "今週だけ担当を付け替える"

    @staticmethod
    def _to(f: dict) -> Any:
        return f["staff_id"] if "staff_id" in f else f.get("to_staff")

    def validate(self, op):
        f, errs = fields(op), []
        check_uuid("visit_id", f.get("visit_id"), errs)
        if "staff_id" not in f and "to_staff" not in f:
            errs.append("staff_id（担当なしは null）がありません")
        else:
            check_staff_ref("staff_id", self._to(f), errs, allow_none=True)
        return errs

    def _body(self, f: dict, staff_id: Any) -> dict:
        body: dict = {"visit_id": str(f["visit_id"]), "staff_id": staff_id}
        if f.get("acknowledge_constraint_warnings"):
            body["acknowledge_constraint_warnings"] = True
        if f.get("op_group_id"):
            body["op_group_id"] = str(f["op_group_id"])
        return body

    def request(self, op):
        f = fields(op)
        return ("POST", "/schedule/v2/visit-assign-staff-week", self._body(f, staff_param(self._to(f))))

    def execute(self, api, op):
        f = fields(op)
        return api.call("POST", "/schedule/v2/visit-assign-staff-week", self._body(f, resolve_staff(api, self._to(f))))

    def snapshot(self, api, op):
        f = fields(op)
        v = visit_or_none(api, str(f["visit_id"]))
        notes = []
        if v is None:
            notes.append("訪問が見つかりません")
        elif v.get("status") != "planned":
            notes.append(f"予定（planned）ではありません（{v.get('status')}）→ 付け替えできません")
        return {"visit": visit_brief(v) if v else None, "notes": notes}

    def inverse(self, op, before):
        v = (before or {}).get("visit")
        if not v:
            return None
        return [{"op": self.name, "visit_id": v["id"], "staff_id": v.get("primary_staff_id"),
                 "label": f"元に戻す: {v.get('patient_name') or ''} {hhmm(v.get('start_time'))} の担当を元へ"}]

    def verify(self, api, op):
        f = fields(op)
        v = visit_or_none(api, str(f["visit_id"]))
        if v is None:
            return [f"訪問 {f['visit_id']} が見つかりません"]
        want = resolve_staff(api, self._to(f))
        got = v.get("primary_staff_id")
        if (str(got) if got else None) != want:
            return [f"担当が案と違います（案 {want}・今 {got}）"]
        return []


# ---------------------------------------------------------------------------
# 今週だけ移動（日・時刻・コース）
# ---------------------------------------------------------------------------


@register
class MoveVisitWeek(Op):
    """今週だけ別の曜日・時刻・コースへ移す（盤面のドラッグ・改善提案の「この週だけ」と同じ）。

    API: POST /schedule/v2/visit-move-week-only（schedule_v2.py:1471、要求 auto_schedule_v2.py:628）。
    受け取る値（build_ops.py の move と同じ）: ``iso_year``*・``iso_week``*・``patient_id``*・
    ``old_weekday``*・``old_start_time``*・``new_weekday``*・``new_start_time``*・
    ``new_course_template_id``（省略=コース据え置き。曜日を変えるときは付けること：据え置くと旧曜日のコースに残る）・
    ``acknowledge_constraint_warnings``・``op_group_id``。任意 ``old_course_template_id``（戻し用。無ければ読み取りで推定）。
    元は ID ではなく「患者×曜日×開始時刻」で決まる（planned のみ・2 名体制は 2 行とも動く）。
    元が無いと 200 で visits_moved=0 → check_response で止める。非稼働の患者は 422。
    戻し方: 新しい位置から元の位置へ同じ操作（コースを変えたときは元のテンプレートへ）。
    2 名体制で 2 行のコースが違う場合・元のテンプレートが決まらない場合は戻せない（None）。
    """

    name = "move-visit-week"
    summary = "今週だけ日・時刻・コースを動かす"
    KEYS = ("iso_year", "iso_week", "patient_id", "old_weekday", "old_start_time", "new_weekday", "new_start_time")

    def validate(self, op):
        f, errs = fields(op), []
        check_week(f, errs)
        check_uuid("patient_id", f.get("patient_id"), errs)
        for k in ("old_weekday", "new_weekday"):
            if not isinstance(f.get(k), int) or not 0 <= f[k] <= 6:
                errs.append(f"{k} は 0〜6 です")
        check_hhmm("old_start_time", f.get("old_start_time"), errs)
        check_hhmm("new_start_time", f.get("new_start_time"), errs)
        check_uuid("new_course_template_id", f.get("new_course_template_id"), errs, required=False)
        return errs

    def request(self, op):
        f = fields(op)
        body = {k: f[k] for k in self.KEYS}
        body["old_start_time"] = hhmm(body["old_start_time"])
        body["new_start_time"] = hhmm(body["new_start_time"])
        if f.get("new_course_template_id"):
            body["new_course_template_id"] = str(f["new_course_template_id"])
        if f.get("acknowledge_constraint_warnings"):
            body["acknowledge_constraint_warnings"] = True
        if f.get("op_group_id"):
            body["op_group_id"] = str(f["op_group_id"])
        return ("POST", "/schedule/v2/visit-move-week-only", body)

    def check_response(self, op, response):
        if not isinstance(response, dict) or int(response.get("visits_moved") or 0) < 1:
            return ["移動元の予定が見つかりませんでした（visits_moved=0・すでに動かされた可能性）"]
        return []

    def _days(self, f):
        return (iso_day(f["iso_year"], f["iso_week"], f["old_weekday"]),
                iso_day(f["iso_year"], f["iso_week"], f["new_weekday"]))

    def snapshot(self, api, op):
        f = fields(op)
        old_d, _new_d = self._days(f)
        rows = [v for v in visits_on(api, str(f["patient_id"]), old_d)
                if v.get("status") == "planned" and hhmm(v.get("start_time")) == hhmm(f["old_start_time"])]
        notes = []
        if not rows:
            notes.append("移動元の予定がありません")
        course_ids = sorted({str(v["course_id"]) for v in rows if v.get("course_id")})
        old_tpl = f.get("old_course_template_id")
        if not old_tpl and f.get("new_course_template_id") and len(course_ids) == 1:
            old_tpl = template_for_course(api, course_ids[0])
        return {"visits": [visit_brief(v) for v in rows], "course_ids": course_ids,
                "old_course_template_id": old_tpl, "notes": notes}

    def inverse(self, op, before):
        f = fields(op)
        before = before or {}
        if not before.get("visits"):
            return None
        inv = {"op": self.name, "iso_year": f["iso_year"], "iso_week": f["iso_week"], "patient_id": f["patient_id"],
               "old_weekday": f["new_weekday"], "old_start_time": hhmm(f["new_start_time"]),
               "new_weekday": f["old_weekday"], "new_start_time": hhmm(f["old_start_time"]),
               "label": f"元に戻す: {op.get('label') or '今週だけ移動'}"}
        if f.get("new_course_template_id"):
            if len(before.get("course_ids") or []) > 1 or not before.get("old_course_template_id"):
                return None  # 元のコースへ戻す先が 1 つに決まらない
            inv["new_course_template_id"] = before["old_course_template_id"]
        return [inv]

    def verify(self, api, op):
        f = fields(op)
        old_d, new_d = self._days(f)
        at_new = [v for v in visits_on(api, str(f["patient_id"]), new_d)
                  if v.get("status") == "planned" and hhmm(v.get("start_time")) == hhmm(f["new_start_time"])]
        errs = []
        if not at_new:
            errs.append(f"{new_d} {hhmm(f['new_start_time'])} に予定がありません")
        if (old_d, hhmm(f["old_start_time"])) != (new_d, hhmm(f["new_start_time"])):
            left = [v for v in visits_on(api, str(f["patient_id"]), old_d)
                    if v.get("status") == "planned" and hhmm(v.get("start_time")) == hhmm(f["old_start_time"])]
            if left:
                errs.append(f"移動元 {old_d} {hhmm(f['old_start_time'])} に予定が残っています")
        return errs


# ---------------------------------------------------------------------------
# 今週だけ取消
# ---------------------------------------------------------------------------


@register
class CancelVisitWeek(Op):
    """今週だけ取消（訪問の操作メニュー「今週だけ取消」と同じ）／``cancel: false`` で取消をやめる。

    API: POST /schedule/v2/visit-cancel-week（schedule_v2.py:6619、要求 schemas/visit.py:100）。
    受け取る値: ``visit_id``*・``cancel``（既定 true）・``reason``（200 字まで・訪問のメモには書かれない）・``op_group_id``。
    前提: 当日以前・打刻あり・実施中/完了は 422。2 名体制はペアごと切り替わる。
    罠: 取消は保留プールに出ない（回数にも数える）。別の日に振り替えるなら return-to-pool か add-visit-week。
    カイポケへ送ると削除になる（送信は別のスキル）。
    戻し方: ``cancel`` を反対にして同じ操作（出所は manual_week になる・元の出所は戻らない）。
    ステータス連動（入院など）で取り消された訪問は cancel=false が 422。
    """

    name = "cancel-visit-week"
    summary = "今週だけ取消（取消をやめる）"

    def validate(self, op):
        f, errs = fields(op), []
        check_uuid("visit_id", f.get("visit_id"), errs)
        if "cancel" in f and not isinstance(f["cancel"], bool):
            errs.append("cancel は true/false です")
        if f.get("reason") is not None and len(str(f["reason"])) > 200:
            errs.append("reason は 200 字までです")
        return errs

    def request(self, op):
        f = fields(op)
        body: dict = {"visit_id": str(f["visit_id"]), "cancel": bool(f.get("cancel", True))}
        if f.get("reason"):
            body["reason"] = str(f["reason"])
        if f.get("op_group_id"):
            body["op_group_id"] = str(f["op_group_id"])
        return ("POST", "/schedule/v2/visit-cancel-week", body)

    def snapshot(self, api, op):
        f = fields(op)
        v = visit_or_none(api, str(f["visit_id"]))
        return {"visit": visit_brief(v) if v else None, "notes": []}

    def inverse(self, op, before):
        v = (before or {}).get("visit")
        if not v:
            return None
        cancel = bool(fields(op).get("cancel", True))
        was_cancelled = v.get("status") == "cancelled"
        if was_cancelled == cancel:
            return []  # 元からその状態（何も変わらない）
        return [{"op": self.name, "visit_id": v["id"], "cancel": not cancel,
                 "label": f"元に戻す: {v.get('patient_name') or ''} {v.get('visit_date')} {hhmm(v.get('start_time'))}"
                          f" の{'取消をやめる' if cancel else '取消'}"}]

    def verify(self, api, op):
        f = fields(op)
        v = visit_or_none(api, str(f["visit_id"]))
        want = "cancelled" if f.get("cancel", True) else "planned"
        if v is None:
            return [f"訪問 {f['visit_id']} が見つかりません"]
        return [] if v.get("status") == want else [f"状態が案と違います（案 {want}・今 {v.get('status')}）"]


# ---------------------------------------------------------------------------
# 保留プールへ戻す（DELETE /visits）
# ---------------------------------------------------------------------------


@register
class ReturnToPool(Op):
    """訪問を消して保留プールへ戻す（盤面の削除・ならしの「入らない訪問」と同じ）。

    API: DELETE /visits/{id}?cascade_partner=..&op_group_id=..（visits.py:1066）。
    受け取る値: ``visit_id``（または探し方 ``patient_id``＋``date``＋``start_time`` ＝ 反映の時に 1 件に決める）・
    ``cascade_partner``（既定 true＝2 名体制の相方も消す。build_ops.py は false を送る）・``op_group_id``。
    前提: プールは保存されず、希望の回数に足りない分として画面が数えて出す。
    戻し方: **API では戻せない（None）**。置き直し（place-from-pool）は新しい ID の別の訪問になり、
    カイポケの対応・担当・出所が元と同じにならない。戻すにはバックアップから（rk backup）。
    """

    name = "return-to-pool"
    summary = "保留プールへ戻す（訪問を消す）"
    undoable = False

    def validate(self, op):
        f, errs = fields(op), []
        if f.get("visit_id"):
            check_uuid("visit_id", f.get("visit_id"), errs)
        else:
            check_uuid("patient_id", f.get("patient_id"), errs)
            if not to_date(f.get("date")):
                errs.append("visit_id か（patient_id・date・start_time）が要ります")
            check_hhmm("start_time", f.get("start_time"), errs)
        return errs

    @staticmethod
    def _path(f: dict, visit_id: str) -> str:
        q = [f"cascade_partner={'true' if f.get('cascade_partner', True) else 'false'}"]
        if f.get("op_group_id"):
            q.append(f"op_group_id={f['op_group_id']}")
        return f"/visits/{visit_id}?" + "&".join(q)

    def request(self, op):
        f = fields(op)
        vid = f.get("visit_id") or f"<{f.get('date')} {hhmm(f.get('start_time'))} の訪問>"
        return ("DELETE", self._path(f, str(vid)), None)

    def _locate(self, api: Api, f: dict) -> str:
        if f.get("visit_id"):
            return str(f["visit_id"])
        rows = [v for v in visits_on(api, str(f["patient_id"]), f["date"])
                if v.get("status") == "planned" and hhmm(v.get("start_time")) == hhmm(f["start_time"])]
        groups = {str(v.get("visit_group_id") or v["id"]) for v in rows}
        if not rows or (len(rows) > 1 and len(groups) > 1):
            raise ApiError(422, f"{f['date']} {hhmm(f['start_time'])} の訪問を 1 件に決められません（{len(rows)} 件）")
        return str(rows[0]["id"])

    def execute(self, api, op):
        f = fields(op)
        return api.call("DELETE", self._path(f, self._locate(api, f)), None)

    def snapshot(self, api, op):
        f = fields(op)
        try:
            vid = self._locate(api, f)
        except ApiError:
            return {"visit": None, "notes": ["対象の訪問が見つかりません"]}
        v = visit_or_none(api, vid)
        return {"visit": visit_brief(v) if v else None, "notes": []}

    def inverse(self, op, before):
        return None

    def verify(self, api, op):
        f = fields(op)
        if f.get("visit_id"):
            return [] if visit_or_none(api, str(f["visit_id"])) is None else [f"訪問 {f['visit_id']} が残っています"]
        left = [v for v in visits_on(api, str(f["patient_id"]), f["date"])
                if v.get("status") == "planned" and hhmm(v.get("start_time")) == hhmm(f["start_time"])]
        return [f"{f['date']} {hhmm(f['start_time'])} の訪問が残っています"] if left else []


# ---------------------------------------------------------------------------
# 今週だけ追加 / プールから置く
# ---------------------------------------------------------------------------


@register
class AddVisitWeek(Op):
    """今週だけ訪問を足す（＋訪問・空き枠への配置と同じ。毎週の型は変えない）。

    API（画面 frontend/lib/scheduling/addVisitExecutor.ts:320-371 と同じ分け方）:
    - コースが決まっている → POST /schedule/place-and-fix（schedule.py:840、要求 :502）``fix_pattern=false``
    - コース未定（臨時）   → POST /visits（visits.py:910）``source='manual_week'``・担当なし
    受け取る値: ``patient_id``*・``date``（または ``iso_year``＋``iso_week``＋``weekday``）*・``start_time``*・
    ``duration_min``*・``course_template_id``（1 名）または ``course_template_ids``（2 名体制は 2 つの別コース）・
    ``staff_count``（1/2・既定 1）・``acknowledge_constraint_warnings``・``op_group_id``・``note``（POST /visits の時だけ）。
    前提: 非稼働の患者は 422。NG・女性限定は 422（了承で再送）。コースの担当がそのまま担当になる。
    戻し方: 足した訪問を return-to-pool（同じ患者・日・時刻で探す）。元から同じ時刻に訪問があった場合は
    1 件に決められないので戻せない（None）。place-and-fix が空のコースを作った場合はそのコースは残る。
    """

    name = "add-visit-week"
    summary = "今週だけ訪問を足す"
    require_course = False

    def _date(self, f: dict) -> date | None:
        d = to_date(f.get("date"))
        if d is None and f.get("iso_year") is not None and f.get("weekday") is not None:
            try:
                d = iso_day(f["iso_year"], f["iso_week"], f["weekday"])
            except (ValueError, TypeError, KeyError):
                d = None
        return d

    def validate(self, op):
        f, errs = fields(op), []
        check_uuid("patient_id", f.get("patient_id"), errs)
        if self._date(f) is None:
            errs.append("date（または iso_year・iso_week・weekday）がありません")
        check_hhmm("start_time", f.get("start_time"), errs)
        dur = f.get("duration_min")
        if not isinstance(dur, int) or not 1 <= dur <= 480:
            errs.append("duration_min は 1〜480 の整数です")
        sc = f.get("staff_count", 1)
        if sc not in (1, 2):
            errs.append("staff_count は 1 か 2 です")
        tpls = f.get("course_template_ids")
        if f.get("course_template_id") and tpls:
            errs.append("course_template_id と course_template_ids は片方だけです")
        if tpls is not None:
            if not isinstance(tpls, list) or len(tpls) != sc or len(set(map(str, tpls))) != len(tpls):
                errs.append("course_template_ids は staff_count と同じ数の別々のコースです")
            for t in tpls or []:
                check_uuid("course_template_ids[]", t, errs)
        else:
            check_uuid("course_template_id", f.get("course_template_id"), errs, required=self.require_course)
            if sc == 2:
                errs.append("2 名体制は course_template_ids に 2 つのコースを書きます")
        return errs

    def request(self, op):
        f = fields(op)
        d = self._date(f)
        if f.get("course_template_id") or f.get("course_template_ids"):
            iso = d.isocalendar()
            body: dict = {"patient_id": str(f["patient_id"]), "iso_year": iso[0], "iso_week": iso[1],
                          "weekday": d.weekday(), "start_time": hhmm(f["start_time"]),
                          "duration_min": int(f["duration_min"]), "staff_count": int(f.get("staff_count", 1)),
                          "fix_pattern": False}
            if f.get("course_template_ids"):
                body["course_template_ids"] = [str(t) for t in f["course_template_ids"]]
            else:
                body["course_template_id"] = str(f["course_template_id"])
            if f.get("acknowledge_constraint_warnings"):
                body["acknowledge_constraint_warnings"] = True
            if f.get("op_group_id"):
                body["op_group_id"] = str(f["op_group_id"])
            return ("POST", "/schedule/place-and-fix", body)
        start = hhmm(f["start_time"])
        h, m = int(start[:2]), int(start[3:5])
        end_min = h * 60 + m + int(f["duration_min"])
        if end_min >= 24 * 60:
            end_min = 24 * 60 - 1
        body = {"patient_id": str(f["patient_id"]), "visit_date": d.isoformat(), "start_time": start,
                "end_time": f"{end_min // 60:02d}:{end_min % 60:02d}", "type": "regular", "status": "planned",
                "source": "manual_week", "course_id": None, "primary_staff_id": None}
        if f.get("note"):
            body["note"] = str(f["note"])
        return ("POST", "/visits", body)

    def snapshot(self, api, op):
        f = fields(op)
        d = self._date(f)
        same = [v for v in visits_on(api, str(f["patient_id"]), d)
                if v.get("status") == "planned" and hhmm(v.get("start_time")) == hhmm(f["start_time"])]
        notes = ["同じ日・同じ時刻にすでに予定があります（二重になります）"] if same else []
        return {"date": d.isoformat(), "existing": [visit_brief(v) for v in same], "notes": notes}

    def inverse(self, op, before):
        before = before or {}
        if before.get("existing"):
            return None
        f = fields(op)
        return [{"op": "return-to-pool", "patient_id": str(f["patient_id"]), "date": before.get("date") or
                 self._date(f).isoformat(), "start_time": hhmm(f["start_time"]), "cascade_partner": True,
                 "label": f"元に戻す: {op.get('label') or '今週だけ追加'} を消す"}]

    def verify(self, api, op):
        f = fields(op)
        d = self._date(f)
        rows = [v for v in visits_on(api, str(f["patient_id"]), d)
                if v.get("status") == "planned" and hhmm(v.get("start_time")) == hhmm(f["start_time"])]
        want = int(f.get("staff_count", 1))
        if len(rows) < want:
            return [f"{d} {hhmm(f['start_time'])} の予定が {len(rows)} 件です（案 {want} 件）"]
        return []


@register
class PlaceFromPool(AddVisitWeek):
    """保留プールの利用者を、今週だけコースへ置く（プールの「置く」「担当なし（M）へ入れる」と同じ）。

    API: POST /schedule/place-and-fix ``fix_pattern=false``（schedule.py:840・frontend
    components/schedule/v2/PoolCandidateList.tsx:1330-1343）。受け取る値は add-visit-week と同じだが
    ``course_template_id``（2 名体制は ``course_template_ids``）が必須。M へ入れたときは担当なし。
    ⭐（特別訪問週間の○）は special-visit-place を使う（チケットと結び付けるため）。
    戻し方: add-visit-week と同じ（置いた訪問を return-to-pool）。
    """

    name = "place-from-pool"
    summary = "保留プールから今週だけ置く"
    require_course = True

    def validate(self, op):
        errs = super().validate(op)
        f = fields(op)
        if not f.get("course_template_id") and not f.get("course_template_ids") and \
                "course_template_id がありません" not in errs:
            errs.append("course_template_id がありません")
        return errs


# ---------------------------------------------------------------------------
# コースの担当（PATCH /courses）
# ---------------------------------------------------------------------------


@register
class PatchCourseStaff(Op):
    """その日のコースの担当を変える（盤面でコースへ職員を貼る・ならしの step4 と同じ）。

    API: PATCH /courses/{course_id}（courses.py:317）body ``{assigned_staff_id, op_group_id, acknowledge_constraint_warnings}``。
    受け取る値: ``course_id``、または（``iso_year``・``iso_week``・``weekday``・``course``＝拠点の略称＋コード 例 "稲A"）
    ＝反映の時に GET /courses・GET /offices で 1 つに決める（build_ops.py の course_staff と同じ）。
    ``staff_id``（UUID/職員コード・null=担当なし）※別名 ``to_staff``・``acknowledge_constraint_warnings``・``op_group_id``。
    働き: コースの訪問のうち planned かつ手で付けていない（manual_staff_override=false）ものの担当も同じ人になる。
    前提: 新人は 422。NG・女性限定は 422（了承で再送）。同行と時間が重なると警告（止まらない）。
    戻し方: 元の担当へ同じ操作（コースの訪問にも伝わる。元から訪問ごとに違う担当だった planned は、
    手の上書きでない限り元の担当にそろう＝完全には戻らないことがある）。
    """

    name = "patch-course-staff"
    summary = "コースの担当を変える"

    @staticmethod
    def _to(f: dict) -> Any:
        return f["staff_id"] if "staff_id" in f else f.get("to_staff")

    def validate(self, op):
        f, errs = fields(op), []
        if f.get("course_id"):
            check_uuid("course_id", f["course_id"], errs)
        else:
            check_week(f, errs)
            if not isinstance(f.get("weekday"), int) or not 0 <= f["weekday"] <= 6:
                errs.append("course_id か（iso_year・iso_week・weekday・course）が要ります")
            if not f.get("course"):
                errs.append("course（拠点の略称＋コード 例 稲A）がありません")
        if "staff_id" not in f and "to_staff" not in f:
            errs.append("staff_id（担当なしは null）がありません")
        else:
            check_staff_ref("staff_id", self._to(f), errs, allow_none=True)
        return errs

    def _body(self, f: dict, staff_id: Any) -> dict:
        body: dict = {"assigned_staff_id": staff_id}
        if f.get("op_group_id"):
            body["op_group_id"] = str(f["op_group_id"])
        if f.get("acknowledge_constraint_warnings"):
            body["acknowledge_constraint_warnings"] = True
        return body

    def request(self, op):
        f = fields(op)
        cid = f.get("course_id") or f"<{f.get('course')} 曜日{f.get('weekday')}>"
        return ("PATCH", f"/courses/{cid}", self._body(f, staff_param(self._to(f))))

    def resolve_course(self, api: Api, f: dict) -> str:
        if f.get("course_id"):
            return str(f["course_id"])
        offices = api.get("/offices", {"limit": 200}) or []
        label = {str(o["id"]): (o.get("short_label") or (o.get("name") or "")[:1]) for o in offices}
        rows = api.get("/courses", {"iso_year": f["iso_year"], "iso_week": f["iso_week"],
                                    "weekday": f["weekday"], "limit": 500}) or []
        hits = [c for c in rows if label.get(str(c.get("office_id")), "") + str(c.get("code")) == str(f["course"])]
        if len(hits) != 1:
            raise ApiError(422, f"コース {f['course']}（曜日 {f['weekday']}）を 1 つに決められません（{len(hits)} 件）")
        return str(hits[0]["id"])

    def execute(self, api, op):
        f = fields(op)
        return api.call("PATCH", f"/courses/{self.resolve_course(api, f)}", self._body(f, resolve_staff(api, self._to(f))))

    def snapshot(self, api, op):
        f = fields(op)
        try:
            cid = self.resolve_course(api, f)
            c = api.get(f"/courses/{cid}")
        except ApiError as e:
            return {"course": None, "notes": [f"コースが見つかりません（{e}）"]}
        return {"course": {"id": cid, "assigned_staff_id": c.get("assigned_staff_id"), "code": c.get("code"),
                           "weekday": c.get("weekday"), "office_id": c.get("office_id")}, "notes": []}

    def inverse(self, op, before):
        c = (before or {}).get("course")
        if not c:
            return None
        return [{"op": self.name, "course_id": c["id"], "staff_id": c.get("assigned_staff_id"),
                 "label": f"元に戻す: {op.get('label') or 'コースの担当'} を元の担当へ"}]

    def verify(self, api, op):
        f = fields(op)
        c = api.get(f"/courses/{self.resolve_course(api, f)}")
        want = resolve_staff(api, self._to(f))
        got = c.get("assigned_staff_id")
        return [] if (str(got) if got else None) == want else [f"コースの担当が案と違います（案 {want}・今 {got}）"]


# ---------------------------------------------------------------------------
# 同行（新人・支援）
# ---------------------------------------------------------------------------


@register
class AccompanimentSet(Op):
    """その職員×その週の同行をまとめて決める（画面「👥新人同行」と同じ・全置換）。

    API: PUT /accompaniments（accompaniments.py:368、要求 schemas/accompaniment.py:110）。
    受け取る値: ``staff_id``*（同行する人。UUID/職員コード）・``iso_year``*・``iso_week``*・
    ``course_ids``（付いて回る先輩のコース）・``visit_ids``（訪問 1 件ずつ）・``acknowledge_constraint_warnings``。
    毎週の既定（defaults）はマスターの変更なのでここでは送らない（rakusuke-master の範囲）。
    前提: 退職者は 409。本人の担当や他の同行と時間が重なると 422（accompaniment_overlap）。
    新人はコースを持てないので、新人を訪問の担当にしない（assign-staff-week は 422）。
    戻し方: 反映前に読んだ同じ週の course_ids / visit_ids で同じ操作（全置換なので元に戻る。出所の印は manual になる）。
    """

    name = "accompaniment-set"
    summary = "同行を決める（その週・全置換）"

    def validate(self, op):
        f, errs = fields(op), []
        check_staff_ref("staff_id", f.get("staff_id"), errs, allow_none=False)
        check_week(f, errs)
        for k in ("course_ids", "visit_ids"):
            v = f.get(k, [])
            if not isinstance(v, list):
                errs.append(f"{k} はリストです")
                continue
            for x in v:
                check_uuid(f"{k}[]", x, errs)
        if f.get("defaults"):
            errs.append("defaults（毎週の既定）はここでは扱いません（マスターの更新で）")
        return errs

    def _body(self, f: dict, staff_id: str) -> dict:
        body = {"staff_id": staff_id, "iso_year": int(f["iso_year"]), "iso_week": int(f["iso_week"]),
                "course_ids": [str(x) for x in f.get("course_ids") or []],
                "visit_ids": [str(x) for x in f.get("visit_ids") or []]}
        if f.get("acknowledge_constraint_warnings"):
            body["acknowledge_constraint_warnings"] = True
        return body

    def request(self, op):
        f = fields(op)
        return ("PUT", "/accompaniments", self._body(f, staff_param(f.get("staff_id"))))

    def execute(self, api, op):
        f = fields(op)
        return api.call("PUT", "/accompaniments", self._body(f, resolve_staff(api, f["staff_id"])))

    @staticmethod
    def _links(api: Api, f: dict, staff_id: str) -> tuple[list[str], list[str]]:
        res = api.get("/accompaniments", {"iso_year": f["iso_year"], "iso_week": f["iso_week"], "staff_id": staff_id}) or {}
        cs, vs = set(), set()
        for it in res.get("items") or []:
            if str(it.get("staff_id")) != staff_id:
                continue
            if it.get("course"):
                cs.add(str(it["course"]["id"]))
            elif it.get("visit"):
                vs.add(str(it["visit"]["id"]))
        return sorted(cs), sorted(vs)

    def snapshot(self, api, op):
        f = fields(op)
        sid = resolve_staff(api, f["staff_id"])
        cs, vs = self._links(api, f, sid)
        return {"staff_id": sid, "course_ids": cs, "visit_ids": vs}

    def inverse(self, op, before):
        if not before or not before.get("staff_id"):
            return None
        f = fields(op)
        return [{"op": self.name, "staff_id": before["staff_id"], "iso_year": f["iso_year"], "iso_week": f["iso_week"],
                 "course_ids": before.get("course_ids") or [], "visit_ids": before.get("visit_ids") or [],
                 "label": f"元に戻す: {op.get('label') or '同行'} を反映前へ"}]

    def verify(self, api, op):
        f = fields(op)
        sid = resolve_staff(api, f["staff_id"])
        cs, vs = self._links(api, f, sid)
        errs = []
        if cs != sorted(str(x) for x in f.get("course_ids") or []):
            errs.append(f"同行のコースが案と違います（今 {len(cs)} 件）")
        if vs != sorted(str(x) for x in f.get("visit_ids") or []):
            errs.append(f"同行の訪問が案と違います（今 {len(vs)} 件）")
        return errs


__all__ = ["fields", "hhmm", "is_uuid", "to_date", "iso_day", "resolve_staff", "visits_on", "visit_or_none",
           "visit_brief", "check_uuid", "check_hhmm", "check_week", "check_staff_ref", "staff_param",
           "is_not_found"]
