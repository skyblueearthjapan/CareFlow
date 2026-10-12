"""コースのならし（コーススケジュール＝固定訪問の枠の最適化）。本番は読むだけ。

- extract.py … 本番から読む（rk の読み取り専用 SQL・1 文）。固定訪問の枠・希望・コース・ふだんの担当・勤務・固定イベント・NG
- model.py   … 問題の組み立て（曜日ごと・コース＝1 人 1 日の列）と、いま／案の数え方（標準ライブラリだけ）
- solve.py   … 計算（OR-Tools。ortools が要るので uv の別プロセスで動かす）。week-leveling の solver.py の部品を使う
- plan.py    … 結果を rk の案（pfv-update の全件・ready は止める理由が無いときだけ true）に直す（標準ライブラリだけ）
- run.py     … 上をつなぐ（rk courseplan から呼ぶ）

設計: docs/plans/agent-skills/course-plan-design-2026-10-12.md
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parents[1]
REPO_ROOT = AGENT_DIR.parent
LEVELING_DIR = REPO_ROOT / "docs" / "tools" / "week-leveling"
