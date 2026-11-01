"""月の実績 — らく助の QR 実績 × カイポケの実績の突き合わせ・判定・反映・一覧.

正典: ``docs/plans/kaipoke-actual-write-design-2026-11-01.md`` (§5-2・§5-3) と
``docs/plans/session-2026-11-01-HANDOFF.md`` §1。手作業の道具
``docs/tools/monthly-actual/`` (extract_oct.py・review_oct.py・build_final.py) を移したもの。

* ``judge``  — 組み方と判定 (純粋関数・DB を見ない)
* ``service`` — DB の読み出し・判定の保存・RPA で反映するジョブ・予約・通知
* ``export_xlsx`` — 月の一覧 (Excel 4 シート)
"""
