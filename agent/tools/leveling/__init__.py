"""週のならし（docs/tools/week-leveling）を rk のつなぎ方で動かす包み。

- extract_rk.py … 本番から読む（rk の読み取り専用 SQL。extract.py の SQL をそのまま使い、足りない分を足す）
- solve.py      … ならしの計算と Excel/A4（ortools が要るので uv の別プロセスで動かす）
- plan.py       … 計算の結果を rk の案（plan.json）に直す（標準ライブラリだけ）
- run.py        … 上の 3 つをつなぐ（rk level から呼ぶ）

docs/tools/week-leveling/ は今も使っている道具なので書き換えない（import して使う）。
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parents[1]
REPO_ROOT = AGENT_DIR.parent
LEVELING_DIR = REPO_ROOT / "docs" / "tools" / "week-leveling"
DEFAULT_CONFIG = LEVELING_DIR / "config.local.json"
