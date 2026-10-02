"""Jev (TypeSafe System One) による「要確認」の目安づけ。任意 (--jev のときだけ)。

送るのは記号と数字だけ (PO 2026-10-02): 利用者名・住所・職員名・メモは送らない。
決まり (人数・NG・勤務時刻など) はソルバーと検査が必ず守る。Jev は「この変更を送る前に人が見た方が
よいか」の目安を確率で返すだけで、案そのものは変えない。

API キー: 環境変数 TYPESAFE_API_KEY、無ければ ~/.jev-hands/.env の TYPESAFE_API_KEY (jev-hands と共用)。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
MODEL = os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest")
BATCH = 20
THRESHOLD = 0.7  # 0.5 だと「担当が変わるだけ」の大半に付いて目安にならなかった (2026-10-02 週42)

QUESTION = (
    "A visiting-nurse weekly schedule was re-balanced automatically. This item describes one visit change "
    "(anonymous; numbers only). Should a human coordinator confirm this change with the patient or staff "
    "before it is sent? Large time moves, a different nurse than last time, overtime, and cross-office "
    "visits make confirmation more advisable; small moves inside the patient's preferred window less."
)


def _api_key() -> str | None:
    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        return os.environ["TYPESAFE_API_KEY"].strip()
    env = Path.home() / ".jev-hands" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "TYPESAFE_API_KEY" and v.strip():
                return v.strip().strip('"').strip("'")
    return None


def build_items(visits, out, flags) -> dict[str, dict]:
    """visit_id -> 記号と数字だけの状態 (変更のある訪問のみ)。"""
    from report import new_assignments

    new = new_assignments(out)
    items = {}
    for v in visits:
        sid, st = new.get(v.id, (None, None))
        if sid is None:
            continue
        f = dict(flags.get(v.id, []))
        # 今の盤面の担当は自動割当の結果なので「担当が変わる」だけでは聞かない
        if not f:
            continue
        items[v.id] = {
            "time_type": {
                "固定": "fixed",
                "時間帯": "window",
                "午前": "morning",
                "午後": "afternoon",
            }.get(v.time_type, "any"),
            "moved_minutes": f.get("move", 0),
            "preferred_window_minutes": v.win[1] - v.win[0],
            "same_nurse_as_previous_visit": bool(f.get("same_as_last")),
            "overtime_minutes_for_manager": f.get("overtime", 0),
            "cross_office": bool(f.get("cross")),
        }
    return items


async def _ask(client, key, batch: dict[str, dict]) -> dict[str, float]:
    ids = list(batch)
    state = {f"item_{i}": batch[vid] for i, vid in enumerate(ids)}
    questions = {
        f"q{i}": {"type": "noul", "instructions": f"{QUESTION} Item: item_{i}."}
        for i in range(len(ids))
    }
    r = await client.post(
        "/v1/systemone",
        json={"state": state, "model": MODEL, "questions": questions},
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    r.raise_for_status()
    answers = r.json().get("answers", {})
    return {vid: float(answers.get(f"q{i}", {}).get("noul", 0.0)) for i, vid in enumerate(ids)}


async def _run(items: dict[str, dict]) -> dict[str, float]:
    key = _api_key()
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY が見つかりません（環境変数か ~/.jev-hands/.env）")
    ids = list(items)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20.0) as client:
        parts = await asyncio.gather(
            *[
                _ask(client, key, {vid: items[vid] for vid in ids[i : i + BATCH]})
                for i in range(0, len(ids), BATCH)
            ]
        )
    probs = {}
    for p in parts:
        probs.update(p)
    return probs


def jev_labels(visits, out, flags) -> tuple[dict[str, str], dict]:
    """visit_id -> "要確認 0.82" / "目安 0.12"。送った内容の見本も返す (記録用)。"""
    items = build_items(visits, out, flags)
    if not items:
        return {}, {}
    probs = asyncio.run(_run(items))
    labels = {
        vid: (f"要確認 {p:.2f}" if p >= THRESHOLD else f"目安 {p:.2f}") for vid, p in probs.items()
    }
    sample = next(iter(items.values()))
    return labels, {"sent_items": len(items), "sample_state": sample}
