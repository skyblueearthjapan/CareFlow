"""型の作業場（テンプレートの作業場）— 日付の無い「仮の 1 週間」で、コースの型（固定訪問の枠）を試してから登録する。

本番のデータには触らない（読むのは rk の読み取り専用 SQL だけ）。下書きは agent/runs/template-drafts/<名前>.json（git 管理外）。

- draft.py    … 下書きの形・保存・読み込み・マスターの指紋（作った後にマスターが変わっていないか）
- problems.py … 下書き（材料の写し＋もしもの条件＋手で動かした枠）→ 曜日ごとの問題（courseplan.model.build_problem を使う）
                 ＋「まだ枠の無い方」の新しい枠（希望の曜日・時間帯の中）
- ops.py      … 作る・解く（曜日ごと・週まるごと）・もしも・手で動かす・比べる・登録の案（pfv-update）
- report.py   … A3 横（行 = コース・列 = 月〜土）＋ A4 の要点

設計: docs/plans/agent-skills/template-workspace-design-2026-10-12.md
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
