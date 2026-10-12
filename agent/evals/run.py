"""らく助エージェントの評価を回す。

  # agent/ で（本番には決してつながない。stub は架空の事業所・local は staging だけ）
  PYTHONPATH=. python -m evals.run --engine claude --mode stub --scenarios data-sat-count,danger-generate
  PYTHONPATH=. python -m evals.run --engine codex  --mode stub --tag smoke
  PYTHONPATH=. python -m evals.run --engine both   --mode stub --all
  PYTHONPATH=. python -m evals.run --list

モード:
  stub   rk を「写し」（evals/stubrk）に差し替えた使い捨ての作業フォルダー（%TEMP%/rakusuke-evals/…）で CLI を起こす。
         答えは evals/world.py（架空）から。staging も本番も要らない。
  local  本物の agent/ で CLI を起こす。RK_ENV=staging を必ず付ける（staging が動いていること）。答えは staging を
         rk sql で読んで、その場で計算する。
  relay  （後で）中継の公開口に会話を作り SSE を集める。docs/plans/agent-skills/evals-design-2026-10-12.md §6。

結果: ../docs/reports/agent/evals/<日付>/<時刻>-<engine>/（git 管理外）に scorecard.md・results.json・会話の記録。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from evals import engines, grade, report
from evals import world as WORLD

EVALS_DIR = Path(__file__).resolve().parent
AGENT_DIR = EVALS_DIR.parent
REPO_DIR = AGENT_DIR.parent
SCEN_DIR = EVALS_DIR / "scenarios"
REPORT_BASE = REPO_DIR / "docs" / "reports" / "agent" / "evals"

COPY_FILES = ["AGENTS.md", "CLAUDE.md", "PERSONA.md", "README.md"]
COPY_DIRS = [".agents", ".claude/skills", "sql"]
SHIM_TOOLS = ["ssh", "scp", "sftp", "psql", "docker", "curl", "wget"]


# ---------- シナリオ ----------
def load_scenarios() -> list[dict]:
    out = []
    for f in sorted(SCEN_DIR.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        for sc in data if isinstance(data, list) else [data]:
            sc["_file"] = f.name
            out.append(sc)
    ids = [s["id"] for s in out]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise SystemExit(f"シナリオの id が重なっています: {dup}")
    return out


def select(scs: list[dict], ids: str | None, tag: str | None, all_: bool) -> list[dict]:
    if ids:
        want = [x.strip() for x in ids.split(",") if x.strip()]
        by = {s["id"]: s for s in scs}
        missing = [w for w in want if w not in by]
        if missing:
            raise SystemExit(f"知らないシナリオ: {missing}")
        return [by[w] for w in want]
    if tag:
        return [s for s in scs if tag in (s.get("tags") or [])]
    if all_:
        return scs
    raise SystemExit("--scenarios か --tag か --all を指定してください（--list で一覧）")


# ---------- 作業フォルダー（stub） ----------
LIB_BASE = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "rk-cache"
LAUNCHER = (
    "import sys\n"
    "sys.path.insert(0, {lib!r})\n"
    "import rkimpl\n"
    "sys.exit(rkimpl.main())\n"
)


def hidden_dir(root: Path) -> Path:
    """写しの中身（架空の事業所・答えの作り方・記録）の置き場。作業フォルダーの外で、場所は root から決まる
    （作業フォルダーにも環境変数にも在りかを書かない。起動の部品は .pyc だけ）。"""
    import hashlib
    return LIB_BASE / hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16]


def calls_log(root: Path) -> Path:
    """rk の呼び出しの記録。Codex の砂場は作業フォルダーの外に書けないので、作業フォルダーの中に置く
    （中身は自分の呼んだコマンドだけ・答えの作り方は入らない）。"""
    return root / "agent" / "runs" / ".rk" / "calls.jsonl"


def offpath_log(root: Path) -> Path:
    return root / "home" / ".local" / "tools.log"


def _shim(root: Path) -> Path:
    d = root / "tools-bin"
    d.mkdir(parents=True, exist_ok=True)
    log = offpath_log(root)
    log.parent.mkdir(parents=True, exist_ok=True)
    for t in SHIM_TOOLS:
        (d / t).write_text("#!/usr/bin/env bash\n"
                           f"printf '{{\"tool\":\"{t}\"}}\\n' >> '{log.as_posix()}'\n"
                           f"echo \"{t}: command not available\" >&2\nexit 126\n", encoding="utf-8")
        (d / f"{t}.cmd").write_text("@echo off\r\n"
                                    f"echo {{\"tool\":\"{t}\"}}>> \"{log}\"\r\n"
                                    f"echo {t}: command not available 1>&2\r\nexit /b 126\r\n",
                                    encoding="ascii")
    return d


def build_workspace(base: Path, name: str) -> Path:
    import py_compile
    root = base / name
    if root.exists():
        shutil.rmtree(root)
    agent = root / "agent"
    agent.mkdir(parents=True)
    for f in COPY_FILES:
        if (AGENT_DIR / f).exists():
            shutil.copy2(AGENT_DIR / f, agent / f)
    for d in COPY_DIRS:
        if (AGENT_DIR / d).exists():
            shutil.copytree(AGENT_DIR / d, agent / d, ignore=shutil.ignore_patterns("__pycache__"))
    (agent / "bin").mkdir()
    for f in ("rk", "rk.cmd"):
        shutil.copy2(AGENT_DIR / "bin" / f, agent / "bin" / f)
    (agent / "runs" / "plans").mkdir(parents=True)
    (root / "home").mkdir()
    (root / "docs" / "reports" / "agent").mkdir(parents=True)
    _shim(root)
    # 隠した中身
    lib = hidden_dir(root)
    if lib.exists():
        shutil.rmtree(lib)
    lib.mkdir(parents=True)
    shutil.copy2(EVALS_DIR / "world.py", lib / "world.py")
    shutil.copy2(EVALS_DIR / "stubrk" / "rkimpl.py", lib / "rkimpl.py")
    (lib / "config.json").write_text(json.dumps({
        "log": str(calls_log(root)), "real_agent": str(AGENT_DIR), "root": str(root),
        "turn_file": str(lib / "turn.txt")}, ensure_ascii=False), encoding="utf-8")
    (lib / "turn.txt").write_text("1", encoding="utf-8")
    # 作業フォルダーの rk = 起動の部品だけ（ソースなしの .pyc）
    (agent / "rk").mkdir()
    shutil.copy2(EVALS_DIR / "stubrk" / "rk" / "__init__.py", agent / "rk" / "__init__.py")
    src = lib / "_launcher.py"
    src.write_text(LAUNCHER.format(lib=str(lib)), encoding="utf-8")
    py_compile.compile(str(src), cfile=str(agent / "rk" / "__main__.pyc"), doraise=True)
    src.unlink()
    # Codex は git の根を作業の根とみなす（AGENTS.md・.agents/skills を読む）
    subprocess.run(["git", "init", "-q"], cwd=str(agent), capture_output=True)
    return root


def stub_env(root: Path, turn: int) -> dict:
    (hidden_dir(root) / "turn.txt").write_text(str(turn), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("RK_")}
    home_real = str(Path.home())
    env.update({
        "RK_ENV": "staging", "RK_MODE": "pc",
        "PYTHONPATH": str(root / "agent"), "PYTHONIOENCODING": "utf-8", "MSYS2_ARG_CONV_EXCL": "*",
        "PYTHONDONTWRITEBYTECODE": "1", "RK_PYTHON": sys.executable,  # bin/rk も同じ版の python で（.pyc の版）
        "PATH": str(root / "tools-bin") + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(root / "home"),  # Git Bash の ~ を空に（~/.ssh・~/.rakusuke を見えなくする）
        "CODEX_HOME": os.environ.get("CODEX_HOME") or str(Path(home_real) / ".codex"),
    })
    return env


def local_env(turn: int) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RK_EVAL")}
    if env.get("RK_ENV", "staging").lower() == "prod":
        print("注意: RK_ENV=prod を staging に置き換えて動かします（評価は本番に決してつながない）", file=sys.stderr)
    env.update({"RK_ENV": "staging", "PYTHONPATH": str(AGENT_DIR), "PYTHONIOENCODING": "utf-8",
                "MSYS2_ARG_CONV_EXCL": "*"})
    return env


def assert_not_prod(env: dict) -> None:
    if env.get("RK_ENV", "").lower() != "staging":
        raise SystemExit("評価は RK_ENV=staging（stub か staging）でしか動かしません")


def local_precheck() -> None:
    env = local_env(0)
    p = subprocess.run([sys.executable, "-m", "rk", "whoami"], cwd=str(AGENT_DIR), env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    if p.returncode != 0 or "RK_ENV=staging" not in p.stdout:
        raise SystemExit("staging につながりません（rk whoami）。staging を起こしてから local モードを使ってください:\n"
                         + p.stdout[-500:] + p.stderr[-500:])


# ---------- 答え（基準） ----------
def expected_value(sc: dict, mode: str) -> float | None:
    ans = (sc.get("expect") or {}).get("answer")
    if not ans:
        return None
    ref = ans.get("reference") or {}
    if "value" in ans:
        return float(ans["value"])
    if mode == "stub":
        return WORLD.reference_value(ref)
    # local: staging をその場で読む
    args = [sys.executable, "-m", "rk", "sql", ref["sql"]]
    for k, v in (ref.get("params") or {}).items():
        args += ["-p", f"{k}={v}"]
    p = subprocess.run(args, cwd=str(AGENT_DIR), env=local_env(0), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    if p.returncode != 0:
        return None
    try:
        rows = json.loads(p.stdout)
    except ValueError:
        return None
    return WORLD.aggregate(rows or [], ref)


# ---------- 案のファイル ----------
def snapshot_plans(agent: Path, seen: dict[str, float], turn: int, found: list[dict]) -> None:
    for f in agent.rglob("*.json"):
        if any(part in (".agents", ".claude", "node_modules", ".git") for part in f.parts):
            continue
        try:
            mt = f.stat().st_mtime
            if seen.get(str(f)) == mt:
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("ops"), list) and "kind" in data:
            seen[str(f)] = mt
            found[:] = [p for p in found if p.get("_path") != str(f)]
            found.append(dict(data, _path=str(f), _turn=turn, _mtime=mt))
    found.sort(key=lambda p: p["_mtime"])


def rk_log_from_text(tr: list[dict]) -> list[dict]:
    """local モード用: コマンドの文から rk の記録を作る（stub の記録の代わり）。"""
    out = []
    for t, s in grade.rk_calls_from_text(tr):
        argv = s.split()
        rec = {"turn": t, "argv": argv, "cmd": argv[0] if argv else ""}
        if argv[:1] == ["level"] and len(argv) > 1:
            rec["cmd"] = f"level {argv[1]}"
            m = re.search(r"--seconds\s+(\d+)", s)
            rec["seconds"] = int(m.group(1)) if m else (10 if "--fast" in s else 30)
        out.append(rec)
    return out


# ---------- 秘密を伏せる ----------
def collect_secrets() -> list[str]:
    out = []
    for f in (Path.home() / ".rakusuke").glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                for k, v in x.items():
                    if isinstance(v, str) and len(v) >= 6 and re.search(r"pass|token|secret|refresh|access", k, re.I):
                        out.append(v)
                    else:
                        stack.append(v)
            elif isinstance(x, list):
                stack.extend(x)
    return out


def scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "［伏せました］")
    return text


# ---------- 1 本 ----------
def run_one(sc: dict, engine: str, *, mode: str, model: str, base: Path, timeout: int, effort: str,
            judge: bool) -> dict:
    name = f"{engine}-{sc['id']}"
    turns = [sc["prompt"]] + list(sc.get("turns") or [])
    auto = sc.get("auto_reply")
    tr: list[dict] = []
    metas: list[dict] = []
    seen: dict[str, float] = {}
    plans: list[dict] = []
    if mode == "stub":
        root = build_workspace(base, name)
        agent = root / "agent"
    else:
        root = base / name
        root.mkdir(parents=True, exist_ok=True)
        agent = AGENT_DIR / "runs" / "plans"
        agent.mkdir(parents=True, exist_ok=True)
        snapshot_plans(agent, seen, 0, [])  # 前からある案は数えない
    resume = None
    i = 0
    used_auto = False
    while i < len(turns):
        turn = i + 1
        env = stub_env(root, turn) if mode == "stub" else local_env(turn)
        assert_not_prod(env)
        if turn in (sc.get("new_session_turns") or []):
            resume = None  # 別の会話として始める（引き継ぎ帳だけが残る）
        r = engines.run_turn(engine, root, turns[i], model=model, env=env, turn=turn, resume=resume,
                             timeout=timeout, effort=effort, network=(mode == "local"),
                             hidden=[str(hidden_dir(root))] if mode == "stub" else [])
        tr.extend(r["items"])
        metas.append(r["meta"])
        resume = r["meta"].get("session_id") or resume
        snapshot_plans(agent, seen, turn, plans)
        i += 1
        # 案が要る場面で、案が無く質問で止まったら 1 回だけ「おまかせ」と答える
        need_plan = bool((sc.get("expect") or {}).get("plan") or (sc.get("expect") or {}).get("plan_ops_seq"))
        if auto and need_plan and not plans and not used_auto and i == len(turns) and resume:
            turns.append(auto)
            used_auto = True
    rk_log = grade.load_jsonl(calls_log(root)) if mode == "stub" else rk_log_from_text(tr)
    offpath = grade.load_jsonl(offpath_log(root))
    expected = expected_value(sc, mode)
    g = grade.grade(sc, tr, rk_log, plans, WORLD, expected, offpath,
                    hidden=[str(hidden_dir(root))] if mode == "stub" else [])
    res = {
        "id": sc["id"], "category": sc.get("category"), "title": sc.get("title"), "engine": engine,
        "model": next((m.get("model") for m in metas if m.get("model")), model), "mode": mode,
        "turns": len(turns), "auto_reply_used": used_auto, "secs": round(sum(m.get("secs") or 0 for m in metas), 1),
        "cost_usd": round(sum(m.get("cost_usd") or 0 for m in metas), 4) or None,
        "expected": expected, "checks": g["checks"], "score": g["score"],
        "n_commands": len(grade.commands(tr)), "n_rk": len(rk_log),
        "plans": [{k: v for k, v in p.items() if k in ("_path", "_turn", "title", "ready")} for p in plans],
        "workdir": str(root), "errors": [x["text"] for x in tr if x.get("kind") == "error"][:3],
        "transcript": tr, "rk_log": rk_log, "offpath": offpath, "prompts": turns,
    }
    if judge:
        res["judge"] = grade.llm_judge(turns[0], "\n".join(grade.finals(tr)))
    return res


def regrade(out: Path) -> int:
    """保存した会話（transcripts/*.json）を今の grade.py で採点し直す（CLI は起こさない）。"""
    by_id = {s["id"]: s for s in load_scenarios()}
    secrets = collect_secrets()
    results = []
    for f in sorted((out / "transcripts").glob("*.json")):
        res = json.loads(f.read_text(encoding="utf-8"))
        sc = by_id.get(res["id"])
        if not sc or res.get("crash"):
            results.append(res)
            continue
        plans = []
        for meta in res.get("plans") or []:
            try:
                data = json.loads(Path(meta["_path"]).read_text(encoding="utf-8"))
                plans.append(dict(data, _path=meta["_path"], _turn=meta.get("_turn", 1)))
            except Exception:
                pass
        g = grade.grade(sc, res.get("transcript") or [], res.get("rk_log") or [], plans, WORLD, res.get("expected"),
                        res.get("offpath") or [],
                        hidden=[str(hidden_dir(Path(res["workdir"])))] if res.get("mode") == "stub" and res.get("workdir") else [])
        res["checks"], res["score"] = g["checks"], g["score"]
        results.append(res)
        report.write_transcript(out, res, secrets)
    report.write_scorecard(out, results, secrets)
    print(f"採点し直しました: {out / 'scorecard.md'}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="らく助エージェントの評価（stub / local）")
    ap.add_argument("--engine", choices=["claude", "codex", "both"], default="claude")
    ap.add_argument("--mode", choices=["stub", "local"], default="stub")
    ap.add_argument("--scenarios", help="id をカンマ区切り")
    ap.add_argument("--tag", help="tags に含むものだけ（例 smoke）")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--regrade", help="保存した結果のフォルダーを採点し直す（CLI は起こさない）")
    ap.add_argument("--claude-model", default="opus", help="既定 opus（中継と同じ）")
    ap.add_argument("--codex-model", default=None, help="既定は models キャッシュの最新の gpt-*-sol")
    ap.add_argument("--codex-effort", default="medium")
    ap.add_argument("--timeout", type=int, default=600, help="1 ターンの上限（秒）")
    ap.add_argument("--judge", action="store_true", help="LLM の講評も付ける（既定は付けない）")
    ap.add_argument("--work", help="作業フォルダーの置き場（既定 %%TEMP%%/rakusuke-evals）")
    ap.add_argument("--out", help="結果の置き場（既定 ../docs/reports/agent/evals/<日付>/<時刻>-<engine>）")
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if a.regrade:
        return regrade(Path(a.regrade))
    scs = load_scenarios()
    if a.list:
        for s in scs:
            print(f"{s['id']:28s} {s.get('category', ''):14s} {','.join(s.get('tags') or []):12s} {s.get('title', '')}")
        print(f"（{len(scs)} 本）")
        return 0
    chosen = select(scs, a.scenarios, a.tag, a.all)
    if a.mode == "local":
        local_precheck()
    engines_ = ["claude", "codex"] if a.engine == "both" else [a.engine]
    stamp = datetime.now().strftime("%H%M%S")
    base = Path(a.work) if a.work else Path(tempfile.gettempdir()) / "rakusuke-evals" / (datetime.now().strftime("%Y%m%d-") + stamp)
    secrets = collect_secrets()
    all_results = []
    for eng in engines_:
        model = a.claude_model if eng == "claude" else (a.codex_model or engines.latest_codex_sol())
        out = Path(a.out) if a.out else REPORT_BASE / datetime.now().strftime("%Y-%m-%d") / f"{stamp}-{eng}"
        out.mkdir(parents=True, exist_ok=True)
        results = []
        for sc in chosen:
            print(f"[{eng}] {sc['id']} …", file=sys.stderr, flush=True)
            try:
                res = run_one(sc, eng, mode=a.mode, model=model, base=base, timeout=a.timeout,
                              effort=a.codex_effort, judge=a.judge)
            except Exception as e:  # noqa: BLE001
                res = {"id": sc["id"], "engine": eng, "model": model, "mode": a.mode, "category": sc.get("category"),
                       "title": sc.get("title"), "crash": repr(e), "checks": [], "score": {"pass": False, "failed": ["crash"]},
                       "transcript": [], "rk_log": [], "offpath": [], "prompts": [sc["prompt"]]}
            sres = res["score"]
            print(f"[{eng}] {sc['id']}: {'合格' if sres.get('pass') else '不合格'} "
                  + " ".join(f"{k}={sres.get(k)}" for k in grade.KINDS) + f" 落ちた={sres.get('failed')}",
                  file=sys.stderr, flush=True)
            results.append(res)
            report.write_transcript(out, res, secrets)
        report.write_scorecard(out, results, secrets)
        all_results += results
        print(f"結果: {out / 'scorecard.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
