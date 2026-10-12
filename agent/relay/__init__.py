"""らく助エージェントの中継（段 3）。

設計: docs/plans/agent-ui/phase3-implementation-design-2026-10-12.md（D1〜D14 は推奨どおり了承済み）。
- 公開口（ブラウザー向け・/agent-relay/*）: relay.public.create_public_app
- 内側口（箱の中の rk 向け・/rk/*）: relay.checkpoint.create_checkpoint_app
- CLI の会話: relay.runner（ClaudeSession / FakeSession）・画面用イベント: relay.normalize
起動: PYTHONPATH=agent python -m relay
"""
