"""マスターの更新（S7 rakusuke-master）の操作の部品。

画面と同じ API だけを使う（出典は各クラスの docstring に backend のファイル:行）。
元に戻す情報は正直に書く: API で正確に戻せない操作は inverse() が None を返し、
undo_note() が理由（日本語）を返す。`undoable` はクラスの既定（条件で変わるものは undo_note を見る）。

約束（設計書 §3-3・§3-4 / R1 / R2 D-1〜D-8）:
- 患者の状態は PATCH で変えない（PATCH に status を入れると、ダイアログを通らない既定で予定が連動する
  patients.py:290-307）。状態は必ず patient-status（POST /status-change）。
- 終了の「予定と固定訪問の枠も消す」は remove_schedule を**必ず明示**して送る。
- 固定訪問の枠は「その mode の全件の置き換え」（PUT）。前の全件を保存しておけば同じ PUT で戻せる。
  ただし change_scope=pattern_and_week は今週の予定も作り直すので、その分は API で戻せない。
- 赤ピン（movability=locked / is_pinned）の付け外しはしない（docs/plans/red-pin-simplify-design.md で整理予定）。
  既存の枠の movability はそのまま運ぶ（省略すると 'unknown' に戻る罠・patient_fixed_visits.py:705-707）。
- 職員は「画面の編集の全項目保存」をしない（退職が稼働中に戻る罠・R2 D-8）。勤務時刻は PUT /shifts、
  職員の基本項目は変えた項目だけを PATCH（staff.py:177 は exclude_unset なので送った項目しか変わらない）。
"""

from __future__ import annotations

import re
from typing import Any

from rk import RkError
from rk.opsbase import Api, ApiError, Op, register

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PATIENT_STATUSES = {
    "active": "稼働中",
    "suspended": "一時休止",
    "admitted": "入院中",
    "pending": "開始前",
    "cancelled": "終了",
}
_WEEKDAYS_PREF = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
_TIME_TYPES = {"固定", "時間帯", "午前", "午後", "終日"}
_MOVABILITY = {"unknown", "time_flexible", "day_flexible", "locked"}


def _hhmm(v: Any) -> str | None:
    """'09:30' / '09:30:00' → '09:30'。None はそのまま。"""
    if v is None:
        return None
    s = str(v)
    return s[:5] if len(s) >= 5 else s


def _s(v: Any) -> Any:
    """UUID などを文字列にそろえて比べる。"""
    if v is None or isinstance(v, (bool, int, float, dict, list)):
        return v
    return str(v)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False
    return _s(a) == _s(b)


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _need(op: dict, *keys: str) -> list[str]:
    """必須の項目。*_id は API の URL にそのまま入るので UUID だけ（職員コード S004 などは 422 になる）。"""
    errs = []
    for k in keys:
        v = op.get(k)
        if v in (None, ""):
            errs.append(f"{k} がありません")
        elif k.endswith("_id") and not _UUID.match(str(v)):
            errs.append(f"{k} は UUID で書く（{v!r}。職員コードなら rk sql staff_lookup で UUID を調べる）")
    return errs


# ---------------------------------------------------------------------------
# 患者の基本項目（氏名・状態・希望以外）
# ---------------------------------------------------------------------------

#: patient-update で変えてよい項目（schemas/v2/patient.py PatientV2Update のうち）。
PATIENT_UPDATE_FIELDS = {
    "kana": "ふりがな",
    "sex": "性別",
    "address": "住所",
    "lat": "緯度",
    "lng": "経度",
    "insurance": "保険区分",
    "visit_category": "訪問看護区分",
    "kaipoke_service_content": "サービス内容の上書き",
    "primary_office_id": "主担当拠点",
    "sex_restriction": "性別制限（女性限定など）",
    "requires_multiple_staff": "2 名体制",
    "note": "備考",
}
#: 別の操作・別の手順で扱う項目（理由つき）。
PATIENT_UPDATE_FORBIDDEN = {
    "name": "氏名はカイポケの利用者名と突き合わせるため、この操作では変えない（PO が名前を挙げたときだけ別手順）",
    "code": "患者コードは変えない",
    "status": "状態は patient-status（POST /status-change）で変える。PATCH だと予定の連動が既定値で走る",
    "weekly_pattern": "希望は preference-update で変える",
    "special_weekly_pattern": "特別訪問週間は rakusuke-special-week で扱う",
    "special_week_active": "特別訪問週間は rakusuke-special-week で扱う",
}


@register
class PatientUpdate(Op):
    """患者マスターの基本項目を変える（女性限定・2 名体制・拠点・住所・保険 など）。

    API: PATCH /patients/{patient_id}（backend/app/api/v1/patients.py:278・admin・PatientUpdate extra=forbid）。
    送った項目だけが変わる（_model_dump_for_orm(partial=True)＝exclude_unset・patients.py:85-109）。
    明示の null は「消す」（visit_category だけは null 不可で 422・schemas/v2/patient.py:310）。
    住所を変えても座標は自動では変わらない（PATCH は setattr だけ）→ lat/lng も一緒に送る。
    op: {"op":"patient-update","patient_id":"…","changes":{"sex_restriction":"female_only"},"label":"…"}
    """

    name = "patient-update"
    summary = "患者の基本項目を変える（女性限定・2 名体制・拠点・住所 など）"
    undoable = True

    def validate(self, op):
        errs = _need(op, "patient_id")
        ch = op.get("changes")
        if not isinstance(ch, dict) or not ch:
            return errs + ["changes（変える項目と値）がありません"]
        for k, v in ch.items():
            if k in PATIENT_UPDATE_FORBIDDEN:
                errs.append(f"{k}: {PATIENT_UPDATE_FORBIDDEN[k]}")
            elif k not in PATIENT_UPDATE_FIELDS:
                errs.append(f"{k}: この操作で変えられる項目ではありません")
        if "visit_category" in ch and ch["visit_category"] not in ("psychiatric", "general"):
            errs.append("visit_category は psychiatric（精神科）か general（一般）（null は不可）")
        if "sex_restriction" in ch and ch["sex_restriction"] not in (None, "female_only", "male_only"):
            errs.append("sex_restriction は female_only / male_only / null")
        if "sex" in ch and ch["sex"] not in (None, "male", "female", "unknown"):
            errs.append("sex は male / female / unknown / null")
        if "insurance" in ch and ch["insurance"] not in (None, "medical", "care"):
            errs.append("insurance は medical / care / null")
        if "requires_multiple_staff" in ch and not isinstance(ch["requires_multiple_staff"], bool):
            errs.append("requires_multiple_staff は true / false")
        if "address" in ch and not ({"lat", "lng"} <= set(ch)):
            errs.append("住所を変えるときは lat / lng も一緒に（PATCH は座標を作り直さない。座標は患者画面の住所欄が POST /geocode で入れる＝rk post-read の許可外 → 住所の変更は画面で行うか、PO の了承を得た座標を入れる）")
        return errs

    def request(self, op):
        return ("PATCH", f"/patients/{op['patient_id']}", dict(op["changes"]))

    def snapshot(self, api, op):
        p = api.get(f"/patients/{op['patient_id']}")
        return {"name": p.get("name"), "fields": {k: p.get(k) for k in op["changes"]}}

    def inverse(self, op, before):
        fields = dict(before.get("fields") or {})
        if fields.get("visit_category") is None:
            fields.pop("visit_category", None)  # null は送れない（列は NOT NULL なので元も null ではない）
        if not fields:
            return None
        return [{
            "op": "patient-update",
            "patient_id": op["patient_id"],
            "changes": fields,
            "label": f"元に戻す: {op.get('label', '患者の基本項目')}",
        }]

    def verify(self, api, op):
        p = api.get(f"/patients/{op['patient_id']}")
        return [
            f"{PATIENT_UPDATE_FIELDS.get(k, k)}: 期待 {v!r} / 実際 {p.get(k)!r}"
            for k, v in op["changes"].items()
            if not _same(p.get(k), v)
        ]


# ---------------------------------------------------------------------------
# 患者の状態（稼働/休止/入院/開始前/終了）
# ---------------------------------------------------------------------------


def _is_active(s: str | None) -> bool:
    return s == "active"


def _direction(cur: str | None, to: str | None) -> str:
    """services/patient_status_sync.py:131 direction_for と同じ判定。"""
    if _is_active(cur) and not _is_active(to):
        return "deactivate"
    if not _is_active(cur) and _is_active(to):
        return "reactivate"
    return "none"


@register
class PatientStatus(Op):
    """患者の状態を変える（予定の連動つき）。画面の「状態の変更」ダイアログと同じ。

    API: POST /patients/{patient_id}/status-change（patients.py:367・StatusChangeRequest
    schemas/patient_status.py:95 extra=forbid）。影響は GET /patients/{id}/status-impact?to=…（patients.py:316・読むだけ）。
    - 稼働中 → 非稼働（入院・休止・開始前・終了）: from_date 以降の予定を取消（⭐ は既定で残す）。
    - 非稼働 → 稼働中（復帰）: 型（固定訪問の枠）から作成済みの週に予定を作り直す（regenerate 既定 true）。
    - 終了（内部 cancelled）で remove_schedule=true: 今後の予定（取消済み含む）と固定訪問の枠を**消す**
      （resolve_remove_schedule は true の明示だけで消す・patient_status_sync.py:150）。消した週はカイポケへ送ると削除が出る。
    op: {"op":"patient-status","patient_id":"…","to_status":"cancelled","remove_schedule":true,
         "from_date":"2026-10-13"(省略=今日 JST・過去日は 422),"special_period_action":"keep|end",
         "regenerate":true,"note":"…"}
    """

    name = "patient-status"
    summary = "患者の状態を変える（稼働/休止/入院/開始前/終了・予定の連動つき）"
    undoable = False  # 連動のない向き（非稼働どうし）だけ API で戻せる。undo_note を見る

    def validate(self, op):
        errs = _need(op, "patient_id", "to_status")
        to = op.get("to_status")
        if to and to not in PATIENT_STATUSES:
            errs.append(f"to_status は {' / '.join(PATIENT_STATUSES)} のどれか")
        if to == "cancelled":
            if not isinstance(op.get("remove_schedule"), bool):
                errs.append("終了のときは remove_schedule（予定と固定訪問の枠も消すか）を true / false で必ず明示する")
        elif op.get("remove_schedule"):
            errs.append("remove_schedule は終了のときだけ")
        fd = op.get("from_date")
        if fd is not None and not _DATE.match(str(fd)):
            errs.append("from_date は YYYY-MM-DD")
        if op.get("special_period_action", "keep") not in ("keep", "end"):
            errs.append("special_period_action は keep / end")
        if "regenerate" in op and not isinstance(op["regenerate"], bool):
            errs.append("regenerate は true / false")
        return errs

    def request(self, op):
        body: dict[str, Any] = {
            "status": op["to_status"],
            "special_period_action": op.get("special_period_action", "keep"),
            "regenerate": bool(op.get("regenerate", True)),
        }
        if op.get("from_date"):
            body["from_date"] = op["from_date"]
        if op["to_status"] == "cancelled":
            body["remove_schedule"] = bool(op["remove_schedule"])  # 省略させない
        if op.get("note"):
            body["note"] = op["note"]
        return ("POST", f"/patients/{op['patient_id']}/status-change", body)

    def snapshot(self, api, op):
        pid = op["patient_id"]
        p = api.get(f"/patients/{pid}")
        params: dict[str, Any] = {"to": op["to_status"], "special_period_action": op.get("special_period_action", "keep")}
        if op.get("from_date"):
            params["from_date"] = op["from_date"]
        if op["to_status"] == "cancelled":
            params["remove_schedule"] = "true" if op.get("remove_schedule") else "false"
        impact = api.get(f"/patients/{pid}/status-impact", params)
        snap: dict[str, Any] = {"name": p.get("name"), "status": p.get("status"), "impact": impact}
        if op["to_status"] == "cancelled" and op.get("remove_schedule"):
            # 消える枠を記録（API では戻せないが、手で作り直す手がかりと照合のため）
            snap["fixed_visits"] = api.get(f"/patients/{pid}/fixed-visits")
        return snap

    def undo_note(self, op, before: dict | None = None) -> str | None:
        if op.get("to_status") == "cancelled" and op.get("remove_schedule"):
            return "終了で予定と固定訪問の枠を消すので、戻すにはバックアップ（DB 復元）から。カイポケへ送った削除はカイポケ側でも戻しが要る"
        cur = (before or {}).get("status")
        if cur is None:
            return "反映前の状態を読むまで決まらない（予定の連動がある向きはバックアップからしか正確に戻せない）"
        d = _direction(cur, op.get("to_status"))
        if d == "deactivate":
            return "取消した予定は状態を戻しても元どおりにはならない（復帰は型から作り直し・手で動かした分は戻らない）。正確に戻すにはバックアップから"
        if d == "reactivate":
            return "作り直した予定は状態を戻すと取消になるが、行は残り元どおりではない。正確に戻すにはバックアップから"
        return None

    def inverse(self, op, before):
        if self.undo_note(op, before):
            return None
        prev = before.get("status")
        if not prev or prev == op.get("to_status"):
            return None
        return [{
            "op": "patient-status",
            "patient_id": op["patient_id"],
            "to_status": prev,
            **({"remove_schedule": False} if prev == "cancelled" else {}),
            "label": f"元に戻す: {op.get('label', '患者の状態')}（{PATIENT_STATUSES.get(prev, prev)} へ）",
        }]

    def verify(self, api, op):
        pid = op["patient_id"]
        out = []
        p = api.get(f"/patients/{pid}")
        if p.get("status") != op["to_status"]:
            out.append(f"状態: 期待 {op['to_status']} / 実際 {p.get('status')}")
        if op["to_status"] == "cancelled" and op.get("remove_schedule"):
            rows = api.get(f"/patients/{pid}/fixed-visits") or []
            if rows:
                out.append(f"固定訪問の枠が {len(rows)} 件残っている")
        return out


# ---------------------------------------------------------------------------
# 固定訪問の枠（PFV・毎週の型）
# ---------------------------------------------------------------------------

_PFV_KEYS = ("weekday", "start_time", "duration_min", "course_template_id", "sub_office_id",
             "slot_index", "is_pinned", "movability")


def pfv_item(row: dict) -> dict:
    """GET の 1 行（PatientFixedVisitV2Read）→ PUT の items 1 件（PatientFixedVisitV2Base・extra=forbid）。"""
    mv = row.get("movability") or "unknown"
    return {
        "weekday": int(row["weekday"]),
        "start_time": _hhmm(row.get("start_time")),
        "duration_min": int(row.get("duration_min") or 35),
        "course_template_id": _s(row.get("course_template_id")),
        "sub_office_id": _s(row.get("sub_office_id")),
        "slot_index": int(row.get("slot_index") or 0),
        "is_pinned": mv == "locked",
        "movability": mv,
    }


def _pfv_key(it: dict) -> tuple:
    n = pfv_item(it)
    return tuple(n[k] for k in _PFV_KEYS)


@register
class PfvUpdate(Op):
    """患者の固定訪問の枠（mode ごとの全件）を置き換える。患者画面の「固定訪問パターン」の保存と同じ。

    API: PUT /patients/{patient_id}/fixed-visits（patient_fixed_visits.py:532・PatientFixedVisitsBulkPut
    schemas/v2/patient_fixed_visit.py:99）。1 TX で (patient, mode) を DELETE → INSERT。
    - change_scope=pattern_only（既定）: 型だけ。週の予定は「週を作る」まで変わらない。
    - change_scope=pattern_and_week: 型 ＋ その週の予定を型から作り直す（iso_year/iso_week 必須・非稼働は 422）。
    - 赤ピン（locked）の枠を変える・消すと 422（patient_fixed_visits.py:583）。NG/性別の警告は 422
      code=constraint_confirmation_required。NG・女性限定は破らない約束なので acknowledge で通すことはしない（validate で拒否）。
    事前検査（書かない）: POST /patients/{id}/fixed-visits/validate（:443）・空き: GET …/course-load（:474）。
    op: {"op":"pfv-update","patient_id":"…","mode":"normal","change_scope":"pattern_only",
         "items":[{"weekday":1,"start_time":"09:30","duration_min":35,"course_template_id":"…",
                   "slot_index":0,"movability":"unknown","is_pinned":false}], "label":"…"}
    items は**変更後の全件**（今ある枠を残すならそれも入れる）。movability は今の値をそのまま写す。
    """

    name = "pfv-update"
    summary = "固定訪問の枠（毎週の型）を置き換える"
    undoable = True  # pattern_only のとき。pattern_and_week は undo_note

    def validate(self, op):
        errs = _need(op, "patient_id", "mode")
        if op.get("mode") not in ("normal", "special"):
            errs.append("mode は normal / special")
        scope = op.get("change_scope", "pattern_only")
        if scope not in ("pattern_only", "pattern_and_week"):
            errs.append("change_scope は pattern_only / pattern_and_week")
        if scope == "pattern_and_week" and (not op.get("iso_year") or not op.get("iso_week")):
            errs.append("pattern_and_week は iso_year / iso_week が必須")
        items = op.get("items")
        if not isinstance(items, list):
            return errs + ["items（変更後の全件）がありません（全部消すなら []）"]
        if len(items) > 14:
            errs.append("items は 14 件まで（7 曜日 × slot 0/1）")
        seen = set()
        for i, it in enumerate(items):
            tag = f"items[{i}]"
            wd, st = it.get("weekday"), _hhmm(it.get("start_time"))
            if not isinstance(wd, int) or not 0 <= wd <= 6:
                errs.append(f"{tag}: weekday は 0（月）〜6（日）")
            if wd == 6:
                errs.append(f"{tag}: 日曜の枠は作らない（カイポケ取込の対象外・R2 D-4）")
            if not st or not _HHMM.match(st):
                errs.append(f"{tag}: start_time は HH:MM")
            else:
                h, m = int(st[:2]), int(st[3:])
                if m % 5 or not (8 <= h < 19 or (h == 19 and m == 0)):
                    errs.append(f"{tag}: start_time は 08:00〜19:00 の 5 分刻み（画面と同じ）")
            dm = it.get("duration_min", 35)
            if not isinstance(dm, int) or not 1 <= dm <= 480:
                errs.append(f"{tag}: duration_min は 1〜480")
            si = it.get("slot_index", 0)
            if si not in (0, 1):
                errs.append(f"{tag}: slot_index は 0 / 1")
            mv = it.get("movability")
            if mv not in _MOVABILITY:
                errs.append(f"{tag}: movability を今の値のまま必ず書く（省略すると unknown に戻る）")
            elif bool(it.get("is_pinned", False)) != (mv == "locked"):
                errs.append(f"{tag}: is_pinned は movability=locked のときだけ true")
            unknown = set(it) - set(_PFV_KEYS)
            if unknown:
                errs.append(f"{tag}: 使えない項目 {sorted(unknown)}")
            if (wd, si) in seen:
                errs.append(f"{tag}: (weekday, slot_index) が重複")
            seen.add((wd, si))
        if op.get("acknowledge_constraint_warnings"):
            errs.append("NG・性別制限の警告を了承して通すことはしない（AGENTS.md §4-3）。コースか時刻を変えた案にする")
        return errs

    def request(self, op):
        scope = op.get("change_scope", "pattern_only")
        body: dict[str, Any] = {
            "mode": op["mode"],
            "items": [pfv_item(it) for it in op["items"]],
            "change_scope": scope,
        }
        if scope == "pattern_and_week":
            body["iso_year"] = op["iso_year"]
            body["iso_week"] = op["iso_week"]
        return ("PUT", f"/patients/{op['patient_id']}/fixed-visits", body)

    def snapshot(self, api, op):
        rows = api.get(f"/patients/{op['patient_id']}/fixed-visits", {"mode": op["mode"]}) or []
        return {"mode": op["mode"], "items": [pfv_item(r) for r in rows]}

    def execute(self, api, op):
        """赤ピンの付け外しを止めてから PUT（赤は red-pin-simplify-design で整理するまで触らない）。"""
        rows = api.get(f"/patients/{op['patient_id']}/fixed-visits", {"mode": op["mode"]}) or []
        cur = {(int(r["weekday"]), int(r.get("slot_index") or 0)): (r.get("movability") or "unknown") for r in rows}
        for it in op["items"]:
            k = (it["weekday"], it.get("slot_index", 0))
            mv = it.get("movability")
            if k in cur and cur[k] != mv:
                raise RkError(f"曜日 {k[0]} の枠の movability を {cur[k]} → {mv} に変えようとしています（赤ピン等の付け外しはこの操作ではしない）")
            if k not in cur and mv == "locked":
                raise RkError(f"曜日 {k[0]} の新しい枠を赤ピン（locked）にしようとしています（この操作ではしない）")
        return super().execute(api, op)

    def check_response(self, op, response):
        # 警告（重なり・昼休み・容量）は保存済みの注意なので止めない（応答は runs/ に残る）。
        if op.get("change_scope") == "pattern_and_week" and (response or {}).get("week_sync") is None:
            return ["型は保存されたが今週への反映が失敗した（week_sync=null）。週の予定を確かめる"]
        return []

    def undo_note(self, op, before: dict | None = None) -> str | None:
        if op.get("change_scope") == "pattern_and_week":
            return "型は前の全件で戻せるが、その週の予定の作り直し（pattern_and_week）は API で戻せない。正確に戻すにはバックアップから"
        return None

    def inverse(self, op, before):
        if self.undo_note(op, before):
            return None
        return [{
            "op": "pfv-update",
            "patient_id": op["patient_id"],
            "mode": before.get("mode", op["mode"]),
            "change_scope": "pattern_only",
            "items": list(before.get("items") or []),
            "label": f"元に戻す: {op.get('label', '固定訪問の枠')}",
        }]

    def verify(self, api, op):
        rows = api.get(f"/patients/{op['patient_id']}/fixed-visits", {"mode": op["mode"]}) or []
        got = sorted(_pfv_key(r) for r in rows)
        want = sorted(_pfv_key(it) for it in op["items"])
        if got == want:
            return []
        miss = [w for w in want if w not in got]
        extra = [g for g in got if g not in want]
        return [f"固定訪問の枠が案と違う（足りない {miss} / 余分 {extra}）"]


# ---------------------------------------------------------------------------
# 希望（weekly_pattern）
# ---------------------------------------------------------------------------


@register
class PreferenceUpdate(Op):
    """患者の希望（週の回数・曜日・時間の種類・時間帯・基本の時間）を置き換える。

    API: PATCH /patients/{patient_id} body {"weekly_pattern": {...}}（patients.py:278）。
    weekly_pattern は丸ごと置き換え（JSONB 1 列）。前の値を保存して同じ PATCH で戻せる。
    希望を変えても固定訪問の枠は自動では変わらない（R2 D-5）→ 回数と枠の数の食い違いは別に確かめる。
    op: {"op":"preference-update","patient_id":"…","weekly_pattern":{"frequency_per_week":2,
         "preferred_weekdays":["Tue","Fri"],"time_type":"時間帯","preferred_start":"09:00",
         "preferred_end":"12:00","service_minutes":35}}
    """

    name = "preference-update"
    summary = "患者の希望（週の回数・曜日・時間帯）を変える"
    undoable = True

    def validate(self, op):
        errs = _need(op, "patient_id")
        if "weekly_pattern" not in op:
            return errs + ["weekly_pattern（変更後の希望の全体）がありません"]
        wp = op["weekly_pattern"]
        if wp is None:
            return errs
        if not isinstance(wp, dict):
            return errs + ["weekly_pattern は辞書"]
        f = wp.get("frequency_per_week")
        if f is not None and (not isinstance(f, int) or not 1 <= f <= 7):
            errs.append("frequency_per_week は 1〜7")
        for d in wp.get("preferred_weekdays") or []:
            if d not in _WEEKDAYS_PREF:
                errs.append(f"preferred_weekdays の {d!r} は Mon〜Sun で書く")
        if wp.get("time_type") is not None and wp["time_type"] not in _TIME_TYPES:
            errs.append(f"time_type は {' / '.join(sorted(_TIME_TYPES))}")
        for k in ("preferred_start", "preferred_end"):
            if wp.get(k) is not None and not _HHMM.match(str(wp[k])):
                errs.append(f"{k} は HH:MM")
        sm = wp.get("service_minutes")
        if sm is not None and (not isinstance(sm, int) or not 0 <= sm <= 300):
            errs.append("service_minutes は 0〜300")
        return errs

    def request(self, op):
        return ("PATCH", f"/patients/{op['patient_id']}", {"weekly_pattern": op["weekly_pattern"]})

    def snapshot(self, api, op):
        p = api.get(f"/patients/{op['patient_id']}")
        return {"name": p.get("name"), "weekly_pattern": p.get("weekly_pattern")}

    def inverse(self, op, before):
        return [{
            "op": "preference-update",
            "patient_id": op["patient_id"],
            "weekly_pattern": before.get("weekly_pattern"),
            "label": f"元に戻す: {op.get('label', '希望')}",
        }]

    def verify(self, api, op):
        got = api.get(f"/patients/{op['patient_id']}").get("weekly_pattern")
        want = op["weekly_pattern"]
        if want is None:
            return [] if got is None else [f"希望が消えていない: {got!r}"]
        got = got or {}
        return [f"希望 {k}: 期待 {v!r} / 実際 {got.get(k)!r}" for k, v in want.items() if got.get(k) != v]


# ---------------------------------------------------------------------------
# NG スタッフ
# ---------------------------------------------------------------------------


@register
class NgStaffUpdate(Op):
    """NG スタッフを 1 名登録（理由メモの更新を含む）または解除する。患者の「訪問条件」カードと同じ。

    API: 登録 PUT /patients/{pid}/ng-staff/{sid} body {"note"}（patient_ng_staff.py:112・冪等・退職者の新規は 422）
         解除 DELETE /patients/{pid}/ng-staff/{sid}（:175・無ければ 404）
         一覧 GET /patients/{pid}/ng-staff（:73）。
    NG は割当・提案ともハード制約。登録後、今後の予定で違反になる訪問は自動では外れない → スキルで一覧と付け替え案を出す。
    op: {"op":"ng-staff-update","patient_id":"…","staff_id":"…","action":"add|remove","note":"…"}
    """

    name = "ng-staff-update"
    summary = "NG スタッフを登録・解除する"
    undoable = True

    def validate(self, op):
        errs = _need(op, "patient_id", "staff_id", "action")
        if op.get("action") not in (None, "add", "remove"):
            errs.append("action は add / remove")
        return errs

    def request(self, op):
        path = f"/patients/{op['patient_id']}/ng-staff/{op['staff_id']}"
        if op["action"] == "remove":
            return ("DELETE", path, None)
        return ("PUT", path, {"note": op.get("note")})

    def _row(self, api, op) -> dict | None:
        for r in api.get(f"/patients/{op['patient_id']}/ng-staff") or []:
            if _s(r.get("staff_id")) == _s(op["staff_id"]):
                return r
        return None

    def snapshot(self, api, op):
        r = self._row(api, op)
        return {"existed": r is not None, "note": (r or {}).get("note"), "staff_name": (r or {}).get("staff_name")}

    def inverse(self, op, before):
        base = {"op": "ng-staff-update", "patient_id": op["patient_id"], "staff_id": op["staff_id"],
                "label": f"元に戻す: {op.get('label', 'NG スタッフ')}"}
        if before.get("existed"):
            return [{**base, "action": "add", "note": before.get("note")}]
        if op["action"] == "add":
            return [{**base, "action": "remove"}]
        return []  # 無いものを解除しようとした（API は 404 で止まる）＝戻すことは無い

    def verify(self, api, op):
        r = self._row(api, op)
        if op["action"] == "remove":
            return [] if r is None else ["NG が解除されていない"]
        if r is None:
            return ["NG が登録されていない"]
        if (op.get("note") or None) != (r.get("note") or None):
            return [f"NG の理由: 期待 {op.get('note')!r} / 実際 {r.get('note')!r}"]
        return []


# ---------------------------------------------------------------------------
# 職員の勤務時刻・基本項目
# ---------------------------------------------------------------------------


def shift_item(row: dict) -> dict:
    return {
        "weekday": int(row["weekday"]),
        "is_on": bool(row.get("is_on", True)),
        "start_time": _hhmm(row.get("start_time")),
        "end_time": _hhmm(row.get("end_time")),
    }


@register
class StaffHoursUpdate(Op):
    """職員の曜日ごとの勤務（出勤するか・開始・終了）を 7 曜日まとめて置き換える。

    API: PUT /staff/{staff_id}/shifts body {"shifts":[7 件]}（backend/app/api/v1/staff_shifts.py:84・
    ShiftsBulkUpdate schemas/staff_shifts.py:43 は 0〜6 をちょうど 1 回ずつ）。GET …/shifts（:60）は行の無い曜日を
    is_on=true（時刻なし）で補って 7 件返す → それをそのまま PUT すれば同じ意味に戻る。
    【罠】職員の編集画面の「全項目保存」（PATCH /staff）は使わない（退職が稼働中に戻る・R2 D-8）。この操作は
    PATCH を一切呼ばないので状態・資格・拠点は変わらない。
    op: {"op":"staff-hours-update","staff_id":"…","shifts":[{"weekday":0,"is_on":true,"start_time":"09:00","end_time":"18:00"},…7 件]}
    """

    name = "staff-hours-update"
    summary = "職員の曜日ごとの勤務時刻を変える"
    undoable = True

    def validate(self, op):
        errs = _need(op, "staff_id")
        sh = op.get("shifts")
        if not isinstance(sh, list) or len(sh) != 7:
            return errs + ["shifts は 7 曜日ぶん（0=月〜6=日 を 1 回ずつ）。変えない曜日も今の値で入れる"]
        if sorted(s.get("weekday") for s in sh if isinstance(s.get("weekday"), int)) != list(range(7)):
            errs.append("shifts の weekday は 0〜6 をちょうど 1 回ずつ")
        for s in sh:
            st, en = _hhmm(s.get("start_time")), _hhmm(s.get("end_time"))
            for v in (st, en):
                if v is not None and not _HHMM.match(v):
                    errs.append(f"weekday {s.get('weekday')}: 時刻は HH:MM")
            if s.get("is_on", True) and st and en and st >= en:
                errs.append(f"weekday {s.get('weekday')}: 開始 < 終了")
        return errs

    def request(self, op):
        return ("PUT", f"/staff/{op['staff_id']}/shifts", {"shifts": [shift_item(s) for s in op["shifts"]]})

    def snapshot(self, api, op):
        got = api.get(f"/staff/{op['staff_id']}/shifts") or {}
        return {"shifts": [shift_item(s) for s in got.get("shifts") or []]}

    def inverse(self, op, before):
        if len(before.get("shifts") or []) != 7:
            return None
        return [{"op": "staff-hours-update", "staff_id": op["staff_id"], "shifts": before["shifts"],
                 "label": f"元に戻す: {op.get('label', '勤務時刻')}"}]

    def verify(self, api, op):
        got = {s["weekday"]: shift_item(s) for s in (api.get(f"/staff/{op['staff_id']}/shifts") or {}).get("shifts", [])}
        out = []
        for s in op["shifts"]:
            w = shift_item(s)
            g = got.get(w["weekday"])
            if g != w:
                out.append(f"曜日 {w['weekday']}: 期待 {w} / 実際 {g}")
        return out


#: staff-master-update で変えてよい項目（schemas/staff.py StaffUpdate のうち）。
STAFF_UPDATE_FIELDS = {
    "kana": "ふりがな", "sex": "性別", "qualification": "資格", "primary_office_id": "主拠点",
    "note": "備考", "is_trainee": "新人", "status": "状態（在籍/休職/退職）",
}
STAFF_UPDATE_FORBIDDEN = {
    "name": "氏名はカイポケの職員名と突き合わせるため、この操作では変えない",
    "code": "職員コードは変えない",
    "role": "業務ロール（manager）の変更は M コースの作り直しが連動するので PO が名前を挙げたときだけ別手順",
}


@register
class StaffMasterUpdate(Op):
    """職員の基本項目を、**変える項目だけ** PATCH する（画面の全項目保存の罠を避ける）。

    API: PATCH /staff/{staff_id}（backend/app/api/v1/staff.py:162・StaffUpdate schemas/staff.py:85 extra=forbid）。
    model_dump(exclude_unset=True)（staff.py:177）なので送った項目しか変わらない。
    status を在籍以外へ変えると今週以降の同行リンクが消える（staff.py:197-201）→ その場合は API で戻せない。
    op: {"op":"staff-master-update","staff_id":"…","changes":{"qualification":"准看護師"}}
    """

    name = "staff-master-update"
    summary = "職員の基本項目を変える（変える項目だけ）"
    undoable = True

    def validate(self, op):
        errs = _need(op, "staff_id")
        ch = op.get("changes")
        if not isinstance(ch, dict) or not ch:
            return errs + ["changes（変える項目と値）がありません"]
        for k in ch:
            if k in STAFF_UPDATE_FORBIDDEN:
                errs.append(f"{k}: {STAFF_UPDATE_FORBIDDEN[k]}")
            elif k not in STAFF_UPDATE_FIELDS:
                errs.append(f"{k}: この操作で変えられる項目ではありません")
        if "status" in ch and ch["status"] not in ("active", "on_leave", "retired"):
            errs.append("status は active / on_leave / retired")
        if "qualification" in ch and ch["qualification"] not in (None, "看護師", "准看護師", "理学療法士", "作業療法士", "言語聴覚士"):
            errs.append("qualification は 看護師 / 准看護師 / 理学療法士 / 作業療法士 / 言語聴覚士")
        return errs

    def request(self, op):
        return ("PATCH", f"/staff/{op['staff_id']}", dict(op["changes"]))

    def snapshot(self, api, op):
        s = api.get(f"/staff/{op['staff_id']}")
        return {"name": s.get("name"), "status": s.get("status"), "fields": {k: s.get(k) for k in op["changes"]}}

    def undo_note(self, op, before: dict | None = None) -> str | None:
        to = (op.get("changes") or {}).get("status")
        if to and to != "active" and (before or {}).get("status", "active") == "active":
            return "在籍以外にすると今週以降の同行リンクが消える。状態は PATCH で戻せるが同行は戻らない（同行は付け直しかバックアップから）"
        return None

    def inverse(self, op, before):
        if self.undo_note(op, before):
            return None
        return [{"op": "staff-master-update", "staff_id": op["staff_id"], "changes": dict(before.get("fields") or {}),
                 "label": f"元に戻す: {op.get('label', '職員の基本項目')}"}]

    def verify(self, api, op):
        s = api.get(f"/staff/{op['staff_id']}")
        return [f"{STAFF_UPDATE_FIELDS.get(k, k)}: 期待 {v!r} / 実際 {s.get(k)!r}"
                for k, v in op["changes"].items() if not _same(s.get(k), v)]


# ---------------------------------------------------------------------------
# 新患
# ---------------------------------------------------------------------------

#: POST /patients で送ってよい項目（PatientV2Create）。それ以外は API が黙って捨てる（extra=ignore）ので案の時点で弾く。
PATIENT_CREATE_FIELDS = set(PATIENT_UPDATE_FIELDS) | {
    "code", "name", "status", "weekly_pattern", "special_weekly_pattern", "special_week_active",
}


@register
class PatientCreate(Op):
    """新しい患者を登録する。画面の「＋新規患者登録」と同じ。

    API: POST /patients（backend/app/api/v1/patients.py:233・PatientCreate=PatientV2Create extra=ignore）。
    code を空にすると自動採番。主担当拠点（primary_office_id）が無いと配置されないので必須にする。
    座標は自動では入らない（画面の住所欄が POST /geocode で求める）。拠点の判定は rk post-read /offices/resolve で読める。
    登録しただけでは**固定訪問の枠が無い**ので次の週以降に入らない（枠をまだ作っていない新しい方の例）→ 続けて pfv-update を案に入れる。
    op: {"op":"patient-create","patient":{"name":"…","kana":"…","address":"…","lat":35.6,"lng":140.1,
         "primary_office_id":"…","insurance":"medical","visit_category":"psychiatric","status":"pending",
         "sex":"female","sex_restriction":null,"requires_multiple_staff":false,"weekly_pattern":{…}}}
    """

    name = "patient-create"
    summary = "新しい患者を登録する"
    undoable = False

    def validate(self, op):
        p = op.get("patient")
        if not isinstance(p, dict):
            return ["patient（登録する内容）がありません"]
        errs = []
        if not (p.get("name") or "").strip():
            errs.append("name（氏名・カイポケの利用者名と同じ表記）が必須")
        if not p.get("primary_office_id"):
            errs.append("primary_office_id（主担当拠点）が必須（無いと配置されない）")
        unknown = set(p) - PATIENT_CREATE_FIELDS
        if unknown:
            errs.append(f"使えない項目 {sorted(unknown)}（API が黙って捨てる）")
        if p.get("status", "active") not in PATIENT_STATUSES:
            errs.append("status は active / suspended / admitted / pending / cancelled")
        if p.get("address") and (p.get("lat") is None or p.get("lng") is None):
            errs.append("住所があるなら lat / lng も（座標が無いと移動時間・同じ建物の判定ができない。座標は画面の住所欄が POST /geocode で入れる＝rk post-read の許可外 → PO の了承を得た座標を入れるか、登録は画面で）")
        if p.get("weekly_pattern") is not None:
            errs += [f"weekly_pattern: {e}" for e in PreferenceUpdate().validate(
                {"patient_id": "-", "weekly_pattern": p["weekly_pattern"]})]
        return errs

    def request(self, op):
        return ("POST", "/patients", dict(op["patient"]))

    def snapshot(self, api, op):
        """同じ氏名の患者がもういないかを記録（二重登録の確認用）。"""
        name = (op["patient"].get("name") or "").replace(" ", "").replace("　", "")
        same = [{"id": r.get("id"), "code": r.get("code"), "status": r.get("status")}
                for r in _all_patients(api) if (r.get("name") or "").replace(" ", "").replace("　", "") == name]
        return {"same_name": same}

    def undo_note(self, op, before: dict | None = None) -> str | None:
        return ("新しい患者の ID は反映の応答（runs/ の記録）にしか無い。取り消すなら、その ID で "
                "DELETE /patients/{id}（論理削除・予定や枠の連動なし）を別の案にして了承を取る")

    def inverse(self, op, before):
        return None

    def check_response(self, op, response):
        if not isinstance(response, dict) or not response.get("id"):
            return ["応答に患者 ID が無い"]
        if response.get("name") != op["patient"].get("name"):
            return [f"登録された氏名が違う: {response.get('name')!r}"]
        return []

    def verify(self, api, op):
        p = op["patient"]
        hits = [r for r in _all_patients(api) if r.get("name") == p.get("name")
                and (not p.get("code") or r.get("code") == p.get("code"))]
        if not hits:
            return ["登録した患者が一覧に見つからない"]
        if len(hits) > 1:
            return [f"同じ氏名の患者が {len(hits)} 名いる（二重登録でないか確かめる）"]
        return []


def _all_patients(api: Api, page: int = 500) -> list[dict]:
    """GET /patients は limit≦500・offset のみ（patients.py:167）。全件を読む。"""
    out: list[dict] = []
    off = 0
    while True:
        rows = api.get("/patients", {"limit": page, "offset": off}) or []
        out.extend(rows)
        if len(rows) < page:
            return out
        off += page


__all__ = ["ApiError", "pfv_item", "shift_item"]
