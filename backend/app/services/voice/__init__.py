"""音声記録の処理レーン (文字起こし → 要約).

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §10-4。

構成 (責務を分ける):

* ``jobs.py``          — 受領後に走らせるジョブ (status 遷移 / stale 掃除)。
* ``vertex_client.py`` — Vertex AI (東京) への HTTP クライアント。**別レーンで実装**。
* ``prompts.py``       — 看護記録テンプレと用語辞書。**別レーンで実装**。

このパッケージ ``__init__`` は **何も import しない**。``vertex_client`` /
``prompts`` がまだ無い状態でも ``jobs`` を読めるようにしておくため
(API 層は ``jobs`` しか触らない)。
"""

from __future__ import annotations

__all__: list[str] = []
