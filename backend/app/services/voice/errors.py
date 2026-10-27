"""音声記録の処理で共有する例外 (jobs と correction の両方から使う)."""

from __future__ import annotations


class JobSupersededError(Exception):
    """この処理はもう録音の行を受け持っていない.

    stale 掃除で failed に倒された・再試行で別の処理が受け持った・削除された、のどれか。
    続きを AI に頼まず、行にも書き込まずにやめる (二重実行と費用の重複を防ぐ)。
    """
