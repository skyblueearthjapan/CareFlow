"""下書き（仮の 1 週間）の形・保存・読み込み・マスターの指紋。標準ライブラリだけ。

下書き 1 つ = agent/runs/template-drafts/<名前>.json（利用者名入り・git 管理外。後で中継の DB へ移す）。

{
  "version": 1, "name": "ina-v1", "office": "稲", "office_id": "…",
  "origin": "master" | "<元の下書きの名前>", "created_at": "…", "updated_at": "…",
  "snapshot": {"extracted_at": "…", "raw": {courseplan.extract の形＋candidates},
               "scope_patients": ["患者 ID", …],          # この拠点の型に関わる方（枠のある方＋まだ枠の無い方）
               "master_hash": {"患者 ID": "sha256…"}},     # 写した時点のその方の枠の指紋（登録の前に読み直して比べる）
  "scenario": {"red": "keep", "no_pref": "fixed", "cap": 6,
               "staff_count": {"0": 4}, "drop_staff": ["S004"],
               "add_patients": {"P123": {"days": [0, 3], "win": [540, 720]}},
               "prefs": {"P123": {"0": [540, 660]}}},
  "assign": {"枠のキー": {"course": "テンプレート ID", "start": 分}},   # 下書きの枠（無い枠はマスターのまま。新しい枠は置けたものだけ）
  "held": ["枠のキー"],          # 手で動かした枠（解き直しても動かさない）
  "unplaced": {"枠のキー": "理由"},   # 置けなかった新しい枠
  "sids": {"枠のキー": "mon03"},     # 人が指す短い番号
  "metrics": {"0": {...}},           # 最後に数えた曜日ごとの数（マスター／下書き）
  "history": [{"at": "…", "action": "solve", "detail": "…"}]
}
枠のキー = 今ある枠は固定訪問の枠の ID。新しい枠は "new:<患者 ID>:<曜日>:<slot>"。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

VERSION = 1
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}$")
MASTER = "master"


class TemplateError(Exception):
    """利用者に日本語で見せて終わるエラー。"""


def drafts_dir(cfg) -> Path:
    return Path(cfg.runs_dir) / "template-drafts"


def check_name(name: str) -> str:
    if not NAME_RE.match(name or "") or name == MASTER:
        raise TemplateError(f"下書きの名前は英数字と - _ . で 41 文字まで（master は使えません）: {name!r}")
    return name


def path_of(cfg, name: str) -> Path:
    return drafts_dir(cfg) / f"{check_name(name)}.json"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load(cfg, name: str) -> dict:
    p = path_of(cfg, name)
    if not p.exists():
        have = sorted(x.stem for x in drafts_dir(cfg).glob("*.json")) if drafts_dir(cfg).exists() else []
        raise TemplateError(f"下書き {name} がありません（今ある下書き: {'・'.join(have) or 'なし'}）")
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("version") != VERSION:
        raise TemplateError(f"下書き {name} の形が古い（version {d.get('version')}）。作り直してください")
    return d


def save(cfg, draft: dict) -> Path:
    draft["updated_at"] = now_iso()
    p = path_of(cfg, draft["name"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


def log(draft: dict, action: str, detail: str) -> None:
    draft.setdefault("history", []).append({"at": now_iso(), "action": action, "detail": detail})


def default_scenario() -> dict:
    return {"red": "keep", "no_pref": "fixed", "cap": 6, "staff_count": {}, "drop_staff": [],
            "add_patients": {}, "prefs": {}}


def new_draft(name: str, office: str, office_id: str, raw: dict, scope_patients: list[str], origin: str = MASTER,
              scenario: dict | None = None) -> dict:
    return {
        "version": VERSION,
        "name": check_name(name),
        "office": office,
        "office_id": office_id,
        "origin": origin,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "snapshot": {
            "extracted_at": raw.get("extracted_at"),
            "raw": raw,
            "scope_patients": sorted(scope_patients),
            "master_hash": master_hashes(raw.get("pfv") or [], scope_patients),
        },
        "scenario": scenario or default_scenario(),
        "assign": {},
        "held": [],
        "unplaced": {},
        "sids": {},
        "metrics": {},
        "history": [],
    }


def copy_draft(src: dict, name: str) -> dict:
    d = copy.deepcopy(src)
    d["name"] = check_name(name)
    d["origin"] = src["name"]
    d["created_at"] = d["updated_at"] = now_iso()
    log(d, "new", f"下書き {src['name']} から写しました")
    return d


# ---------------------------------------------------------------------------
# マスターの指紋（写した後にマスターが変わっていないか）
# ---------------------------------------------------------------------------


def _norm_row(r: dict) -> tuple:
    mv = r.get("movability") or ("locked" if r.get("is_pinned") else "unknown")
    return (int(r["weekday"]), str(r.get("start_time"))[:5], int(r.get("duration_min") or 35),
            r.get("course_template_id") or "", r.get("sub_office_id") or "", int(r.get("slot_index") or 0), mv)


def master_hashes(pfv_rows: list[dict], patient_ids) -> dict[str, str]:
    """患者ごとの固定訪問の枠（mode=normal）の指紋。枠が無い方は空の指紋。"""
    by = {pid: [] for pid in patient_ids}
    for r in pfv_rows:
        if r["patient_id"] in by:
            by[r["patient_id"]].append(_norm_row(r))
    return {pid: hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
            for pid, rows in by.items()}


def combined_hash(hashes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(hashes.items())).encode("utf-8")).hexdigest()[:16]


UUID_RE = re.compile(r"^[0-9A-Za-z-]{1,40}$")  # 引用符などが入らないことだけ確かめる


def pfv_rows_sql(patient_ids: list[str]) -> str:
    """登録の前に、その方々の今の枠を読み直す SQL（読み取り専用・1 文）。"""
    ids = [p for p in patient_ids if UUID_RE.match(str(p))]
    if len(ids) != len(patient_ids):
        raise TemplateError("患者 ID の形が想定外です（読み直しを止めました）")
    inlist = ",".join(f"'{p}'" for p in ids) or "NULL"
    return f"""
select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
  select f.id, f.patient_id, f.weekday, to_char(f.start_time, 'HH24:MI') start_time, f.duration_min,
         f.course_template_id, f.sub_office_id, f.slot_index, f.movability, f.is_pinned, f.visit_week
  from patient_fixed_visits f
  where f.mode = 'normal' and f.patient_id in ({inlist})) x"""
