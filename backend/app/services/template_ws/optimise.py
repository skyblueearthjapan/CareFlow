"""最適化 (OR-Tools) のバックグラウンドジョブ. kaipoke ジョブと同じく「始める → ポーリング」.

計算は CPU を食うので、別プロセス (``python -m app.services.template_ws.solve``) で動かす。
ジョブの状態はこのプロセスの中だけに持つ (uvicorn 1 ワーカー前提。再起動すると途中のジョブは消えるが、
下書きは計算が終わるまで変わらないので壊れない)。
"""

# ruff: noqa: N812, N806, N803, N818, B905

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.user import User

from . import engine as E
from . import model as M
from . import problems as P
from . import raw as R
from . import report as TR
from . import service as S
from .errors import TemplateError

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[3]
JOBS: dict[str, dict] = {}
_TASKS: set[asyncio.Task] = set()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def run_solver(problems: list[dict], seconds: int, w_change: int = 20, max_changes: int | None = None) -> list[dict]:
    """別プロセスで OR-Tools を動かす。ortools が入っていなければ TemplateError."""
    with tempfile.TemporaryDirectory(prefix="template-ws-") as td:
        inp, out = Path(td) / "problems.json", Path(td) / "solution.json"
        inp.write_text(json.dumps(problems, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, "-m", "app.services.template_ws.solve", "--in", str(inp), "--out", str(out),
               "--seconds", str(seconds), "--w-change", str(w_change)]
        if max_changes is not None:
            cmd += ["--max-changes", str(max_changes)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(BACKEND_DIR), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=seconds * 2 * max(1, len(problems)) + 90)
        except TimeoutError:
            proc.kill()
            raise TemplateError("計算が時間内に終わりませんでした。秒数を減らすか、曜日を分けてもう一度") from None
        text = (stdout or b"").decode("utf-8", "replace")
        if proc.returncode != 0:
            if "No module named 'ortools'" in text or "ortools" in text and "ModuleNotFoundError" in text:
                raise TemplateError("計算の部品（ortools）がこのサーバーに入っていません")
            logger.warning("template_ws solve failed: %s", text[-2000:])
            raise TemplateError("計算が止まりました。もう一度やってみてください（続くときは管理者へ）")
        return json.loads(out.read_text(encoding="utf-8"))


async def execute_optimise(db: AsyncSession, draft_id: UUID, user_id: UUID | None, *, actor: str,
                           weekdays: list[int] | None, seconds: int, reason: str | None,
                           base_version: int, w_change: int = 20, max_changes: int | None = None) -> dict:
    """最適化の本体 (ジョブからも、テストからも呼ぶ)。計算中に下書きが変わったら TemplateConflict."""
    row = await S.get_draft(db, draft_id)
    S.check_version(row, base_version)
    raw = await R.build_raw(db)
    parts = await S.engine_parts(db, row, raw)
    problems = [p for ed in parts for p in P.build_week(ed, weekdays)]
    if not problems:
        raise TemplateError("計算する枠がありません（曜日を確かめてください）")
    await db.rollback()  # 計算の間、トランザクションを持ち越さない
    sols = await run_solver(problems, seconds, w_change, max_changes)
    row = await S.get_draft(db, draft_id, for_update=True)  # 読み直しから書き込みまで行をロック
    if row.version != base_version:
        raise S.TemplateConflict("計算している間に下書きが変わりました。もう一度最適化してください")
    parts = await S.engine_parts(db, row, raw)
    notes, items = [], []
    for ed in parts:  # 拠点ごとに解く (問題・解は拠点の略称で見分ける)
        probs = P.build_week(ed, weekdays)
        before = {p["weekday"]: E.draft_assign(ed, p) for p in probs}
        mine = [x for x in sols if x.get("office", ed["office"]) == ed["office"]]
        notes += [(f"{ed['office']} " if len(parts) > 1 else "") + n for n in E.apply_solutions(ed, probs, mine, w_change)]
        items += [(p, before[p["weekday"]], E.draft_assign(ed, p)) for p in probs]
    changes = await S.sync_slots(db, row, parts)
    await S.refresh_metrics(db, row, raw, parts)
    summary = TR.optimise_summary(items, changes, *S.report_names(raw, parts))
    user = await db.get(User, user_id) if user_id else None
    days = "".join(dict.fromkeys(M.WD[p["weekday"]] for p in problems))
    step = await S.add_step(db, row, actor=actor, user=user, kind="optimise",
                            reason=reason or f"{days}曜を最適化しました", changes=changes, summary=summary)
    await db.commit()
    return {"step": step.seq, "changed": len(changes), "notes": notes, "version": row.version,
            "text": summary["text"]}


async def _run_job(job_id: str, draft_id: UUID, user_id: UUID | None, kwargs: dict) -> None:
    job = JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = _now()
    try:
        async with get_session_factory()() as db:
            res = await execute_optimise(db, draft_id, user_id, **kwargs)
        job.update(status="done", result=res)
    except (TemplateError, S.TemplateConflict) as e:
        job.update(status="failed", error=str(e))
    except Exception:  # noqa: BLE001
        logger.exception("template_ws optimise job failed")
        job.update(status="failed", error="計算に失敗しました。もう一度やってみてください")
    finally:
        job["finished_at"] = _now()


def start_job(draft_id: UUID, user_id: UUID | None, **kwargs) -> dict:
    for j in JOBS.values():
        if j["draft_id"] == str(draft_id) and j["status"] in ("queued", "running"):
            raise S.TemplateConflict("この下書きは今、計算中です。終わるまでお待ちください")
    job_id = uuid4().hex
    JOBS[job_id] = {"id": job_id, "draft_id": str(draft_id), "status": "queued", "queued_at": _now(),
                    "weekdays": kwargs.get("weekdays"), "seconds": kwargs.get("seconds"), "actor": kwargs.get("actor")}
    task = asyncio.create_task(_run_job(job_id, draft_id, user_id, kwargs))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    for old_id in [k for k, j in JOBS.items() if j["status"] in ("done", "failed")][:-50]:
        JOBS.pop(old_id)  # 古い終わったジョブだけ捨てる（動いているジョブは捨てない）
    return JOBS[job_id]
