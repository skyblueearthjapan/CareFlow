# W4-A. kaipoke-api 中継 + 差分プレビュー

**実装 commit**: `d424b8f` (2026-05-05)
**ドメイン**: D4 (Integrations) + D1 / Phase 3

## 概要

既存 VPS 上の kaipoke-api (Flask + Playwright) を CareFlow Backend で中継し、
Frontend から expand/export/diff/apply のフルフロー実行を可能にする。
Wave 2-B で導入した KaipokeJobsList のスケルトン状態を解消し、業務系
オペレーションが CareFlow UI 単独で完結するようにする。

## 実装範囲

- **Backend (新規 14 endpoints, all admin only)**:
  - `services/kaipoke_client.py`: httpx async + Bearer 自動付与 + 30s
    timeout + 5xx で 1 回 retry + 409 → KaipokeBusyError +
    `set_test_client()` seam
  - `api/v1/integrations.py`: 大幅拡張
    - `GET /status` (loginRemainSec / lastSyncAt / runningJob 含む)
    - `POST /expand` (KaipokeJob 作成 → kaipoke async job 起動 → 202)
    - `POST /export` (CSV を `/tmp/carelink/exports/` に 30 分 TTL)
    - `POST /diff` (差分結果 → CorrectionSheet/Item 展開、delete+add 同
      patient × ±1日 → companion_change 統合)
    - `POST /apply` (CorrectionItem.include=true のみ抽出 → 修正シート再構築)
    - `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/stop`,
      `PATCH /job-items/{id}`
    - `GET /correction-sheets/latest`,
      `GET /correction-sheets/{id}/items`
    - `PATCH /correction-items/{id}`,
      `POST /correction-sheets/{id}/items/bulk`
  - `schemas/integrations.py`: 全 schema (alias 付き、populate_by_name=True)
  - `core/config.py`: kaipoke_api_base_url / token / export_dir /
    export_ttl 追加
- **Frontend**: KaipokeJobsList を実 API に接続、差分プレビュー UI
  (CorrectionSheet 単位で include 切替) + apply モーダル

## 関連 commit

- `d424b8f` feat(W4-A): 本体
- `de5c36b` feat(W5-A): compose external network
  (`playwrighttest1_default` を取り込み backend 再生成時も自動接続)

## テスト被覆

- 12 ケース pytest: RBAC / 202 shape / 409 / 502 / diff coalesce /
  apply / bulk
- ruff All checks passed
- 本番では Phase H 後の手動 dry-run で expand → export → diff → apply の
  フルフローを 1 回通している

## 残課題 / 次 Wave 移譲

- kaipoke-api 側の P95 latency baseline ±10% 監視は W5-B 監視 cron で
  カバー (現状は健全性のみ、性能監視は未実装)
- VNC novnc 経由のスクショ取得 (D4 当初計画) は別 sprint で検討
- `companion_change` 判定ロジックは現状 ±1日 + 同 patient で coalesce
  しているが、業務確認の上で更に条件追加の余地あり
