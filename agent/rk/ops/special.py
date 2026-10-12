"""特別訪問週間の操作（設計書 §3-4・S6）。

特別訪問週間 = 期間中だけ回数を上乗せする。固定訪問（毎週の型）は変えない。
○（追加枠）は日ごとのチケットで、保留プールの⭐になる → special-visit-place でコースへ置く。
時刻・日付の書き方は rk/ops/visits.py の先頭を参照。
"""

from __future__ import annotations

from datetime import date

from rk.opsbase import Api, ApiError, Op, register
from rk.ops.visits import check_hhmm, check_uuid, fields, hhmm, iso_day, to_date, visits_on


def _iso(d: date) -> tuple[int, int, int]:
    c = d.isocalendar()
    return c[0], c[1], d.weekday()


def active_period(api: Api, patient_id: str, start_date: str | None = None) -> dict | None:
    """有効な期間（GET /special-visit-periods special_visits.py:334・active は 1 患者 1 本）。"""
    rows = api.get("/special-visit-periods", {"patient_id": patient_id}) or []
    rows = [r for r in rows if r.get("status") == "active"
            and (start_date is None or str(r.get("start_date"))[:10] == start_date)]
    return rows[0] if rows else None


def extra_marks(api: Api, period_id: str) -> dict[str, dict]:
    """期間の○（日付 → MarkRead）。GET /special-visit-periods/{id}/calendar（special_visits.py:405）。"""
    cal = api.get(f"/special-visit-periods/{period_id}/calendar") or {}
    out = {}
    for w in cal.get("weeks") or []:
        for d in w.get("days") or []:
            m = d.get("extra_mark")
            if m and m.get("status") != "cancelled":
                out[str(d.get("date"))[:10]] = m
    return out


@register
class SpecialPeriodCreate(Op):
    """特別訪問週間の期間を登録し、○（追加枠）を立てる（患者の「特別訪問週間」カレンダーと同じ）。

    API（special_visits.py）:
    - 期間の登録 POST /special-visit-periods（:285）body ``{patient_id, start_date, end_date, weekly_target, note}``
    - ○を立てる POST /special-visit-periods/{period_id}/marks（:627）body ``{iso_year, iso_week, weekday}``（1 日 1 回）
    - 期間の取消（戻し用）PATCH /special-visit-periods/{period_id}（:355）body ``{status: "cancelled"}``
    - ○の取消（戻し用）DELETE /special-visit-marks/{mark_id}（:682・置いた後の○は消さない）
    受け取る値: ``action``（"create" 既定 / "add-marks" / "cancel" / "remove-marks"）・``patient_id``*・
    ``start_date``*・``end_date``*（create）・``weekly_target``（1〜7・既定 5）・``note``・
    ``marks``（○を立てる日 "YYYY-MM-DD" のリスト・月〜土・期間の週の中）・``period_id``（add-marks / remove-marks。
    無ければ有効な期間を GET で引く）。
    前提: 有効な期間はひとり 1 本（2 本目は 422）。非稼働の患者は 422。同じ日に○は 1 つ（409）。
    ○を立てただけでは予定は増えない（プールの⭐になるだけ）→ special-visit-place で置く。
    戻し方: create → cancel（期間を「取消」にすると○はプールから消える。行は残る）。add-marks → remove-marks
    （まだ置いていない○だけ。置いた後なら先に置いた訪問を戻す）。cancel・remove-marks の戻しは None（作り直しは別の行）。
    """

    name = "special-period-create"
    summary = "特別訪問週間の登録と○（追加枠）"

    def validate(self, op):
        f, errs = fields(op), []
        action = f.get("action", "create")
        if action not in ("create", "add-marks", "cancel", "remove-marks"):
            return [f"action は create / add-marks / cancel / remove-marks のどれかです（{action}）"]
        if action in ("create", "cancel"):
            check_uuid("patient_id", f.get("patient_id"), errs)
            if to_date(f.get("start_date")) is None:
                errs.append("start_date がありません")
        if action == "create":
            s, e = to_date(f.get("start_date")), to_date(f.get("end_date"))
            if e is None:
                errs.append("end_date がありません")
            elif s and e < s:
                errs.append("end_date が start_date より前です")
            t = f.get("weekly_target", 5)
            if not isinstance(t, int) or not 1 <= t <= 7:
                errs.append("weekly_target は 1〜7 です")
        if action in ("add-marks", "remove-marks"):
            if not f.get("period_id"):
                check_uuid("patient_id", f.get("patient_id"), errs)
            else:
                check_uuid("period_id", f.get("period_id"), errs)
            if not f.get("marks"):
                errs.append("marks（○を立てる日のリスト）がありません")
        for m in f.get("marks") or []:
            d = to_date(m)
            if d is None:
                errs.append(f"marks の日付が読めません（{m}）")
            elif d.weekday() == 6:
                errs.append(f"日曜には○を立てられません（{m}）")
        return errs

    def request(self, op):
        f = fields(op)
        action = f.get("action", "create")
        if action == "create":
            body = {"patient_id": str(f["patient_id"]), "start_date": str(to_date(f["start_date"])),
                    "end_date": str(to_date(f["end_date"])), "weekly_target": int(f.get("weekly_target", 5))}
            if f.get("note"):
                body["note"] = str(f["note"])
            return ("POST", "/special-visit-periods", body)
        pid = f.get("period_id") or "<有効な期間>"
        if action == "cancel":
            return ("PATCH", f"/special-visit-periods/{pid}", {"status": "cancelled"})
        if action == "remove-marks":
            return ("DELETE", "/special-visit-marks/<○>", None)
        y, w, wd = _iso(to_date(f["marks"][0]))
        return ("POST", f"/special-visit-periods/{pid}/marks", {"iso_year": y, "iso_week": w, "weekday": wd})

    def _period_id(self, api: Api, f: dict) -> str:
        if f.get("period_id"):
            return str(f["period_id"])
        p = active_period(api, str(f["patient_id"]),
                          str(to_date(f["start_date"])) if f.get("start_date") else None)
        if p is None:
            raise ApiError(404, "有効な特別訪問週間が見つかりません")
        return str(p["id"])

    def execute(self, api, op):
        f = fields(op)
        action = f.get("action", "create")
        if action == "create":
            _m, path, body = self.request(op)
            period = api.call("POST", path, body)
            pid = str(period["id"])
        elif action == "cancel":
            return api.call("PATCH", f"/special-visit-periods/{self._period_id(api, f)}", {"status": "cancelled"})
        else:
            pid = self._period_id(api, f)
            period = {"id": pid}
        if action == "remove-marks":
            have = extra_marks(api, pid)
            for m in f.get("marks") or []:
                mk = have.get(str(to_date(m)))
                if mk is None:
                    continue
                if mk.get("status") == "placed":
                    raise ApiError(409, f"{m} の○はもう置いてあります（先に置いた訪問を戻してください）")
                api.call("DELETE", f"/special-visit-marks/{mk['id']}", None)
            return period
        marks = []
        for m in f.get("marks") or []:
            y, w, wd = _iso(to_date(m))
            marks.append(api.call("POST", f"/special-visit-periods/{pid}/marks",
                                  {"iso_year": y, "iso_week": w, "weekday": wd}))
        return {"period": period, "marks": marks}

    def snapshot(self, api, op):
        f = fields(op)
        action = f.get("action", "create")
        notes = []
        if action == "create":
            p = active_period(api, str(f["patient_id"]))
            if p is not None:
                notes.append("この方にはもう有効な特別訪問週間があります（2 本目は登録できません）")
            return {"existing_period": p, "notes": notes}
        try:
            pid = self._period_id(api, f)
        except ApiError:
            return {"period_id": None, "notes": ["有効な特別訪問週間が見つかりません"]}
        return {"period_id": pid, "marks": sorted(extra_marks(api, pid)), "notes": notes}

    def inverse(self, op, before):
        f = fields(op)
        action = f.get("action", "create")
        label = f"元に戻す: {op.get('label') or '特別訪問週間'}"
        if action == "create":
            if (before or {}).get("existing_period"):
                return None
            return [{"op": self.name, "action": "cancel", "patient_id": str(f["patient_id"]),
                     "start_date": str(to_date(f["start_date"])), "label": label}]
        if action == "add-marks":
            if not (before or {}).get("period_id"):
                return None
            had = set((before or {}).get("marks") or [])
            new = [str(to_date(m)) for m in f.get("marks") or [] if str(to_date(m)) not in had]
            return [{"op": self.name, "action": "remove-marks", "period_id": before["period_id"],
                     "marks": new, "label": label}] if new else []
        return None

    def verify(self, api, op):
        f = fields(op)
        action = f.get("action", "create")
        if action == "cancel":
            p = active_period(api, str(f["patient_id"]), str(to_date(f["start_date"])))
            return ["特別訪問週間がまだ有効です"] if p else []
        try:
            pid = self._period_id(api, f)
        except ApiError:
            return ["有効な特別訪問週間がありません"]
        errs = []
        if action == "create":
            p = active_period(api, str(f["patient_id"]))
            if str(p.get("end_date"))[:10] != str(to_date(f["end_date"])) or \
                    int(p.get("weekly_target") or 0) != int(f.get("weekly_target", 5)):
                errs.append("期間か目標の回数が案と違います")
        have = extra_marks(api, pid)
        for m in f.get("marks") or []:
            d = str(to_date(m))
            if action == "remove-marks" and d in have:
                errs.append(f"{d} の○が残っています")
            if action != "remove-marks" and d not in have:
                errs.append(f"{d} の○がありません")
        return errs


@register
class SpecialVisitPlace(Op):
    """○（⭐チケット）をその週のコースへ置く（プールの⭐をコースへ置くのと同じ）。

    API: POST /special-visit-marks/{mark_id}/place（special_visits.py:1031、要求 schemas/special_visit*.py PlaceRequest）。
    受け取る値: ``mark_id``（無ければ ``patient_id``＋``date`` でプールの⭐から 1 枚に決める）・
    置き先はどれか 1 つ: ``course_template_id``（ならしの道具はこれ）/ ``course_id`` / ``office_id``＋``course_code``・
    ``start_time``*・``move_to_weekday``（○を同じ週の別の曜日へ移してから置く・任意＝API の weekday）・
    ``acknowledge_constraint_warnings``・``date`` か ``iso_year``＋``iso_week``（反映前の読み取りと照合に要る）。build_ops.py 互換で ``body`` に入れてもよい。
    前提: 利用者の拠点と置き先コースの拠点が同じであること（違えば同じ拠点の M に置いてから move-visit-week で移す）。
    置き済み・取消済みの⭐は 409。非稼働の患者は 422。コースの担当がそのまま担当になる。2 名体制は同じ日に 2 枚。
    「戻る」（op_log）には残らない。
    戻し方: 置いた訪問を return-to-pool（同じ患者・日・時刻で探す）→ ⭐はプールに戻る（訪問が消えると自動で戻る）。
    ``move_to_weekday`` で○の曜日を移した場合は、○の曜日までは戻らないので None。
    """

    name = "special-visit-place"
    summary = "特別訪問週間の○をコースへ置く"
    SELECTORS = ("course_template_id", "course_id", "office_id")

    def _date(self, f: dict) -> date | None:
        d = to_date(f.get("date"))
        if d is None and f.get("iso_year") is not None and f.get("weekday") is not None:
            try:
                d = iso_day(f["iso_year"], f["iso_week"], f["weekday"])
            except (ValueError, TypeError, KeyError):
                d = None
        return d

    def _week(self, f: dict) -> tuple[int, int] | None:
        d = to_date(f.get("date"))
        if d is not None:
            return _iso(d)[:2]
        if f.get("iso_year") is not None and f.get("iso_week") is not None:
            return int(f["iso_year"]), int(f["iso_week"])
        return None

    def validate(self, op):
        f, errs = fields(op), []
        if f.get("mark_id"):
            check_uuid("mark_id", f["mark_id"], errs)
        else:
            check_uuid("patient_id", f.get("patient_id"), errs)
            if to_date(f.get("date")) is None:
                errs.append("mark_id か（patient_id・date）が要ります")
        if self._week(f) is None:
            errs.append("date か（iso_year・iso_week）がありません（反映前の読み取りに使います）")
        chosen = [k for k in self.SELECTORS if f.get(k)]
        if len(chosen) != 1:
            errs.append("置き先は course_template_id / course_id / office_id＋course_code のどれか 1 つです")
        if f.get("office_id") and not f.get("course_code"):
            errs.append("office_id と course_code は両方要ります")
        for k in ("course_template_id", "course_id", "office_id"):
            if f.get(k):
                check_uuid(k, f[k], errs)
        check_hhmm("start_time", f.get("start_time"), errs)
        mv = f.get("move_to_weekday")
        if mv is not None and (not isinstance(mv, int) or not 0 <= mv <= 5):
            errs.append("move_to_weekday は 0〜5（月〜土）です")
        return errs

    def _body(self, f: dict) -> dict:
        body: dict = {"start_time": hhmm(f["start_time"])}
        for k in ("course_template_id", "course_id", "office_id", "course_code"):
            if f.get(k):
                body[k] = str(f[k])
        if f.get("move_to_weekday") is not None:
            body["weekday"] = int(f["move_to_weekday"])  # PlaceRequest.weekday = ○を動かしてから置く
        if f.get("acknowledge_constraint_warnings"):
            body["acknowledge_constraint_warnings"] = True
        return body

    def request(self, op):
        f = fields(op)
        mid = f.get("mark_id") or f"<{f.get('date')} の⭐>"
        return ("POST", f"/special-visit-marks/{mid}/place", self._body(f))

    def _ticket(self, api: Api, f: dict) -> dict | None:
        wk = self._week(f)
        if wk is None:
            return None
        rows = api.get("/special-visit-marks/pool", {"iso_year": wk[0], "iso_week": wk[1]}) or []
        if f.get("mark_id"):
            hits = [t for t in rows if str((t.get("mark") or {}).get("id")) == str(f["mark_id"])]
        else:
            wd = to_date(f["date"]).weekday()
            hits = [t for t in rows if str((t.get("mark") or {}).get("patient_id")) == str(f["patient_id"])
                    and (t.get("mark") or {}).get("weekday") == wd]
        return hits[0] if len(hits) == 1 else None

    def execute(self, api, op):
        f = fields(op)
        mid = f.get("mark_id")
        if not mid:
            t = self._ticket(api, f)
            if t is None:
                raise ApiError(422, f"{f.get('date')} の⭐を 1 枚に決められません")
            mid = t["mark"]["id"]
        return api.call("POST", f"/special-visit-marks/{mid}/place", self._body(f))

    def snapshot(self, api, op):
        f = fields(op)
        t = self._ticket(api, f)
        if t is None:
            return {"mark": None, "notes": ["プールにこの⭐が見つかりません（置き済み・取消済みかもしれません）"]}
        m = t["mark"]
        wk_y, wk_w = int(m["iso_year"]), int(m["iso_week"])
        moved = f.get("move_to_weekday")
        wd = int(moved) if moved is not None else int(m["weekday"])
        return {"mark": {"id": str(m["id"]), "patient_id": str(m["patient_id"]), "iso_year": wk_y, "iso_week": wk_w,
                         "weekday": int(m["weekday"])},
                "date": iso_day(wk_y, wk_w, wd).isoformat(), "notes": []}

    def inverse(self, op, before):
        f = fields(op)
        m = (before or {}).get("mark")
        if not m:
            return None
        if f.get("move_to_weekday") is not None and int(f["move_to_weekday"]) != m["weekday"]:
            return None  # ○の曜日の移動までは戻せない
        return [{"op": "return-to-pool", "patient_id": m["patient_id"], "date": before["date"],
                 "start_time": hhmm(f["start_time"]), "cascade_partner": True,
                 "label": f"元に戻す: {op.get('label') or '特別訪問週間の配置'} を⭐へ戻す"}]

    def verify(self, api, op):
        f = fields(op)
        pid = f.get("patient_id")
        d = self._date(f) if f.get("date") else None
        if not pid or d is None:
            return []  # mark_id だけの案は、プールから消えたかで見る
        rows = [v for v in visits_on(api, str(pid), d)
                if v.get("status") == "planned" and hhmm(v.get("start_time")) == hhmm(f["start_time"])]
        return [] if rows else [f"{d} {hhmm(f['start_time'])} に予定がありません"]
