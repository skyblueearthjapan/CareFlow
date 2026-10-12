"""反映（apply）・照合（verify）・元に戻す（undo）。

apply は「承認の記録」と「30 分以内のバックアップ」が無いと動かない。
runs/<UTC 時刻>-<kind>/ に plan.json・meta.json・before.jsonl・log.jsonl・undo.json を残す。
1 件ずつ API を呼び、最初の想定外で止まる。undo.json は 1 件ごとに書き直す（途中で止まっても戻せる）。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from rk import RkError
from rk.backup import FRESH_MINUTES, backups_log, recent_backup
from rk.config import AGENT_DIR, Config
from rk.journal import record_apply
from rk.opsbase import Api, ApiError, Registry, load_all
from rk.plan import check_plan

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")[:40] or "plan"


def _short(x: Any, n: int = 1500) -> Any:
    try:
        text = json.dumps(x, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(x)
    return x if len(text) <= n else text[:n] + "…"


def _append(path: Path, rec: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def require_approval_and_backup(cfg: Config, approved: str | None, backup: str | None,
                                now: datetime | None = None) -> dict:
    if not approved or len(approved.strip()) < 2:
        raise RkError('反映には --approved "<誰が・いつ>"（了承の記録）が要ります')
    if backup:
        rec = _recorded_backup(cfg, backup, now)
        if rec is None:
            raise RkError(f"--backup のファイル {backup} は {FRESH_MINUTES} 分以内の rk backup の記録"
                          f"（{backups_log(cfg)}）にありません。rk backup --label <名前> で取り直してください")
        return {**rec, "explicit": True}
    rec = recent_backup(cfg, now=now)
    if rec is None:
        raise RkError("30 分以内のバックアップがありません。先に rk backup --label <名前> を実行してください")
    return rec


def _recorded_backup(cfg: Config, path: str, now: datetime | None = None) -> dict | None:
    """runs/backups.jsonl にある、30 分以内に取ったバックアップのうち path が一致するもの。"""
    log = backups_log(cfg)
    if not log.exists():
        return None
    now = now or _utcnow()
    want = path.strip()
    found = None
    for ln in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(ln)
            at = datetime.strptime(rec["at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError, TypeError):
            continue
        p = str(rec.get("path", ""))
        if want not in (p, p.rsplit("/", 1)[-1]):
            continue
        if timedelta(minutes=-2) <= now - at <= timedelta(minutes=FRESH_MINUTES):
            found = rec
    return found


BASIS_CHANGED = "案を作った後に予定が変わりました。案を作り直してください"


def _plan_monday(plan: dict) -> date:
    t = plan.get("target") or {}
    try:
        if t.get("week_start"):
            d = date.fromisoformat(str(t["week_start"])[:10])
            return d - timedelta(days=d.weekday())
        if t.get("iso_year") and t.get("iso_week"):
            return date.fromisocalendar(int(t["iso_year"]), int(t["iso_week"]), 1)
        if t.get("date"):
            d = date.fromisoformat(str(t["date"])[:10])
            return d - timedelta(days=d.weekday())
    except (TypeError, ValueError):
        pass
    raise RkError("案の target から週が分かりません（week_start か iso_year/iso_week）")


def current_basis_hash(cfg: Config, plan: dict, run_sql: Callable[[str], Any] | None = None) -> str:
    """今の週の形を rk level と同じ物差しで読み直す（読み取り専用の SQL 1 本）。

    物差し = tools/leveling/plan.py の basis_hash（docs/tools/week-leveling/extract.py の週の SQL の visits）。
    """
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    try:
        from tools.leveling.extract_rk import load_extract_module, rk_runner, unwrap
        from tools.leveling.plan import basis_hash
        ex = load_extract_module()
    except Exception as e:
        raise RkError(f"週の形を読み直す道具を読み込めません（{type(e).__name__}: {e}）。"
                      "確かめずに反映するなら --no-basis-check（PO の了承のうえで）") from None
    run = run_sql or rk_runner(cfg)
    week = unwrap(run(ex._week_sql(_plan_monday(plan))))
    if not isinstance(week, dict):
        raise RkError("週の形を読み直せません（SQL の結果が想定外）")
    return basis_hash(week)


def check_basis(cfg: Config, plan: dict, run_sql: Callable[[str], Any] | None = None) -> str | None:
    """案に source.basis_hash があれば読み直して比べる。変わっていたら RkError。戻り値は読み直した値。"""
    want = (plan.get("source") or {}).get("basis_hash")
    if not want:
        return None
    got = current_basis_hash(cfg, plan, run_sql)
    if got != want:
        raise RkError(f"{BASIS_CHANGED}（案を作った時 {want}・今 {got}）")
    return got


def apply_plan(cfg: Config, plan: dict, api: Api, approved: str | None, backup: str | None = None,
               registry: Registry | None = None, now: datetime | None = None,
               source: dict | None = None,
               progress: Callable[[int, int, str, bool], None] | None = None) -> dict:
    """案を反映する。戻り値: {run_id, run_dir, total, applied, ok, error, not_undoable}。

    progress(i, total, label, ok) は 1 件ごとに呼ぶ（中継が画面へ進み具合を流す。失敗しても反映は止めない）。
    """
    reg = registry or load_all()
    errs = check_plan(plan, reg)
    if errs:
        raise RkError("案に誤りがあります:\n  " + "\n  ".join(errs))
    backup_rec = require_approval_and_backup(cfg, approved, backup, now)
    now = now or _utcnow()
    run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{_slug(plan['kind'])}"
    run_dir = cfg.runs_dir / run_id
    n = 1
    while run_dir.exists():
        n += 1
        run_dir = cfg.runs_dir / f"{run_id}-{n}"
    run_id = run_dir.name
    run_dir.mkdir(parents=True)
    _write_json(run_dir / "plan.json", plan)
    meta = {"run_id": run_id, "approved": approved.strip(), "backup": backup_rec,
            "started_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": cfg.mode,
            "kind": plan["kind"], "title": plan["title"], "source": source or {}}
    _write_json(run_dir / "meta.json", meta)

    undo_parts: list[list[dict]] = []  # 反映できた操作ごとの戻し方（後で逆順に並べる）
    not_undoable: list[dict] = []
    applied = 0
    unknown = 0  # 応答が無く、書けたか分からない件数（status 0）
    error = None

    def write_undo() -> None:
        ops = [o for part in reversed(undo_parts) for o in part]
        _write_json(run_dir / "undo.json", {
            "kind": "undo",
            "title": f"元に戻す: {plan['title']}",
            "target": plan.get("target", {}),
            "summary": [f"{run_id} で反映した {applied} 件を元に戻す（{len(ops)} 操作）"],
            "ops": ops,
            "checks": [],
            "warnings": ([f"戻せない操作が {len(not_undoable)} 件あります（バックアップから: {backup_rec.get('path')}）"]
                         if not_undoable else []),
            "not_undoable": not_undoable,
            "source_run": run_id,
        })

    write_undo()
    for i, op in enumerate(plan["ops"]):
        impl = reg.get(op["op"])
        label = op.get("label", "")
        try:
            before = impl.snapshot(api, op)
        except Exception as e:
            st = e.status if isinstance(e, ApiError) else 0
            error = f"{i + 1} 件目（{label}）の反映前の読み取りに失敗: {e}"
            _append(run_dir / "log.jsonl", {"i": i, "op": op["op"], "label": label, "stage": "snapshot",
                                            "status": st, "ok": False,
                                            "response": _short(e.body if isinstance(e, ApiError) else str(e))})
            break
        _append(run_dir / "before.jsonl", {"i": i, "op": op["op"], "label": label, "before": before})
        try:
            method, path, _ = impl.request(op)
        except Exception:
            method, path = "?", "?"
        status, ok, response, problems = 200, True, None, []
        try:
            response = impl.execute(api, op)
            problems = list(impl.check_response(op, response) or [])
            ok = not problems
        except ApiError as e:
            status, ok, response = e.status, False, e.body
        except Exception as e:  # つながりが切れた等: 書けたかどうか分からない → status 0
            status, ok, response = 0, False, f"{type(e).__name__}: {e}"
            problems = ["応答を受け取れません（書き込まれたかは rk verify で確かめる）"]
        _append(run_dir / "log.jsonl", {
            "i": i, "op": op["op"], "label": label, "method": method, "path": path,
            "status": status, "ok": ok, "problems": problems,
            "response": None if ok else _short(response), "at_utc": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        if progress is not None:
            try:
                progress(i, len(plan["ops"]), label, ok)
            except Exception:  # 進み具合の通知の失敗で反映を止めない
                pass
        if not ok:
            why = "・".join(problems) if problems else f"HTTP {status}"
            error = f"{i + 1} 件目（{label}）で止めました: {why}"
            # 書き込まれている可能性があるときは戻し方を残す（2xx で想定外・応答なし）
            if status == 0:
                _add_inverse(impl, op, before, i, label, undo_parts, not_undoable)
                unknown += 1
            elif status < 400:
                _add_inverse(impl, op, before, i, label, undo_parts, not_undoable)
                applied += 1
            write_undo()
            break
        applied += 1
        _add_inverse(impl, op, before, i, label, undo_parts, not_undoable)
        write_undo()

    meta.update({"finished_at_utc": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "applied": applied, "unknown": unknown, "total": len(plan["ops"]), "error": error})
    _write_json(run_dir / "meta.json", meta)
    record_apply(cfg, plan, approved, source, run_id, applied, unknown, error, bool(backup_rec.get("path")))
    return {"run_id": run_id, "run_dir": str(run_dir), "total": len(plan["ops"]), "applied": applied,
            "unknown": unknown, "ok": error is None, "error": error, "not_undoable": not_undoable,
            "backup": backup_rec.get("path")}


def _add_inverse(impl, op, before, i, label, undo_parts, not_undoable) -> None:
    inv = impl.inverse(op, before)
    if inv is None:
        not_undoable.append({"i": i, "op": op["op"], "label": label})
        undo_parts.append([])
        return
    part = []
    for o in inv:
        o = dict(o)
        o.setdefault("label", f"元に戻す: {label}")
        part.append(o)
    undo_parts.append(part)


def verify_plan(plan: dict, api: Api, registry: Registry | None = None) -> list[dict]:
    reg = registry or load_all()
    out = []
    for i, op in enumerate(plan.get("ops") or []):
        impl = reg.get(op.get("op", ""))
        if impl is None:
            out.append({"i": i, "label": op.get("label", ""), "ok": False, "problems": ["許可リストに無い操作"]})
            continue
        try:
            problems = list(impl.verify(api, op) or [])
        except ApiError as e:
            problems = [f"読み直しで HTTP {e.status}"]
        out.append({"i": i, "label": op.get("label", ""), "ok": not problems, "problems": problems})
    return out


def resolve_run_dir(cfg: Config, run_id: str) -> Path:
    if not RUN_ID_RE.match(run_id or ""):
        raise RkError(f"run-id の形が違います: {run_id}（runs/ の下のフォルダ名）")
    d = cfg.runs_dir / run_id
    if not d.is_dir():
        raise RkError(f"実行の記録がありません: {d}")
    return d


def undo_run(cfg: Config, run_id: str, api: Api, approved: str | None, backup: str | None = None,
             partial: bool = False, registry: Registry | None = None, now: datetime | None = None,
             progress: Callable[[int, int, str, bool], None] | None = None) -> dict:
    d = resolve_run_dir(cfg, run_id)
    marker = d / "undone.json"
    if marker.exists():
        raise RkError(f"{run_id} はもう元に戻してあります（{marker}）")
    undo_path = d / "undo.json"
    if not undo_path.exists():
        raise RkError(f"{run_id} に undo.json がありません")
    plan = json.loads(undo_path.read_text(encoding="utf-8"))
    nu = plan.get("not_undoable") or []
    if nu and not partial:
        names = "、".join(x.get("label", "") for x in nu)
        raise RkError(f"API で戻せない操作が {len(nu)} 件あります（{names}）。"
                      "戻せる分だけ戻すなら --partial、全部ならバックアップから")
    if not plan.get("ops"):
        raise RkError("戻す操作がありません")
    res = apply_plan(cfg, plan, api, approved, backup, registry, now, source={"undo_of": run_id}, progress=progress)
    if res["ok"]:
        _write_json(marker, {"undone_by": res["run_id"], "at_utc": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")})
    return res


def summary_ja(res: dict) -> str:
    lines = []
    if res["ok"]:
        lines.append(f"反映しました: {res['applied']} / {res['total']} 件（記録 {res['run_id']}）")
    else:
        lines.append(f"途中で止めました: {res['applied']} / {res['total']} 件を反映（記録 {res['run_id']}）")
        lines.append(f"理由: {res['error']}")
    if res.get("unknown"):
        lines.append(f"反映できたか分からない操作: {res['unknown']} 件（応答を受け取れませんでした。"
                     "rk verify で確かめてください。元に戻す案には入れてあります）")
    done = res["applied"] + (res.get("unknown") or 0)
    nu = len(res.get("not_undoable") or [])
    where = f"（戻すにはバックアップから: {res['backup']}）" if res.get("backup") else "（戻すにはバックアップから）"
    if done and nu >= done:
        lines.append("この反映は API では元に戻せません" + where)
    elif done:
        lines.append(f"元に戻すには: rk undo {res['run_id']} --approved \"<誰が・いつ>\"")
        if nu:
            lines.append(f"API で戻せる操作: {done - nu} 件・戻せない操作: {nu} 件{where}。"
                         "戻せる分だけ戻すには rk undo に --partial を付けます")
    lines.append("次に: rk verify で読み直して照合してください")
    return "\n".join(lines)
