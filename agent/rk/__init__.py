"""rk — らく助エージェントの共通の道具（設計書 docs/plans/agent-skills/design-2026-10-12.md §3）。

標準ライブラリだけで動く。`python -m rk ...`（PYTHONPATH=agent）または agent/bin/rk で起動する。
"""

__version__ = "0.1.0"


class RkError(Exception):
    """利用者に日本語で見せて終わるエラー（終了コード 1）。"""
