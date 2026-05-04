# D5 DevOps & QA クロスレビュー A（技術観点）

**VERDICT: REVISE**

## 1. 総評

D5計画は構成・網羅性に優れ、既存サービスの保護を最優先に据えた設計意図は明確。しかし、既存インフラの実態調査が不十分なまま前提条件を置いている箇所が複数。特に「kaipoke-net」という名前付きネットワークは実際には存在せず、cloudflared の実行形態（コンテナ vs systemd）も未確認。D1 計画との逆プロキシ矛盾（Caddy vs nginx）も未解決。Phase 0 で発覚するリスクが高く計画変更を余儀なくされる可能性がある。

## 2. 強み

1. **段階的デプロイ設計** — 手動→CI→自動の段階移行はリスク最小化、Phase 0 棚卸しを冒頭配置も正しい
2. **セキュリティ意識の高さ** — 暗号化バックアップ、PII 非出力、監査ログ、non-root、gitleaks 統合を網羅
3. **Docker Compose リソース制限の明示** — 各コンテナにメモリ・CPU 上限、合計値（1.9GB / 2.2 コア）まで算出

## 3. 重大な指摘

### [CRITICAL-1] 「kaipoke-net」ネットワークは実在しない
- 既存 `PlaywrightTest1/docker-compose.yml` には `networks:` セクションが存在しない
- kaipoke-api はデフォルトブリッジ（`playwrighttest1_default` 等の自動生成名）で動作
- → D5 の「carelink-net と kaipoke-net 完全分離」設計が成立しない
- **Fix**: Phase 0 で `docker network ls` と `docker inspect cloudflared` で実態確認、結果に基づきインフラ図とネットワーク設計修正

### [CRITICAL-2] cloudflared の実行形態が未確認
- 既存 `docker-compose.yml` では cloudflared はコメントアウト
- DEPLOY_GUIDE.md でも「推奨」記載のみ、実態不明
- → Dashboard 管理型（`tunnel run --token`）の場合、ローカル `config.yml` は存在せず ingress は Dashboard 側設定
- → Phase 3 全手順（タスク12-14）が根本変更
- **Fix**: Phase 0 に「cloudflared 実行形態確認」追加（systemctl/docker ps/ps aux）、両形態の ingress 変更手順を分岐記載

### [CRITICAL-3] D1 計画との逆プロキシ矛盾 — Caddy vs nginx
- D1: `Caddy リバースプロキシ` と明記
- D5: 全て nginx ベース設計
- → 設定形式・TLS 取得・リバプロ構文が全く異なる
- **Fix**: D1 を nginx に修正（Cloudflare Tunnel 経由で TLS 終端不要のため nginx で問題ない）

## 4. 見落としリスク

### [MAJOR-1] VPS スペックが完全に不明 — リソース割り当てが空中楼閣
- D5 は CareLink で 1.9GB + 2.2 コア要求、kaipoke-api shm_size 2GB
- VPS 総メモリ・CPU コア数は memory にも記載なし
- Hostinger 最安 4GB/2vCPU なら確実に超過
- **Fix**: Phase 0 を「ブロッキング判定ゲート」に格上げ、判定基準を数値で明記（空きメモリ X GB 未満なら VPS プラン昇格 or DB 外部化）

### [MAJOR-2] dev/prod 同一 VPS の compose project 名分離 — ポートバインド衝突
- nginx は `127.0.0.1:8080:80` にバインド、dev/prod 両方なら衝突
- **Fix**: `docker-compose.dev.yml` に `127.0.0.1:8081:80`、cloudflared ingress の dev エントリも 8081

### [MAJOR-3] kaipoke-api ±10% — ベースライン測定手順が未定義
- Phase 0 タスクに `docker stats`/`free`/`df` のみ、レスポンスタイム計測なし
- **Fix**: Phase 0 に `curl -w "%{time_total}"` 100回測定、平均/P95/P99 記録

### [MAJOR-4] バックアップ先 `/mnt/backup` の実在性が未確認
- 単一ディスク VPS ではバックアップ同一ディスク = 障害時同時消失
- **Fix**: Phase 0 で `lsblk`/`df -h` 確認、別ディスク無ければ週次 R2 オフサイトを必須に格上げ

### [MINOR-1] healthcheck endpoint 不一致 — D1 `/healthz`/`/readyz` vs D5 `/api/health`
### [MINOR-2] Phase 7 ロールバックが `git revert` — Docker イメージタグベースの方が即時性高い
### [MINOR-3] RPO 24h でも WAL アーカイブ/レプリケーション未言及

## 5. 改善提案

1. **Phase 0 を Go/No-Go ゲートに格上げ** — VPS スペック実測 + 判定基準を数値化
2. **cloudflared 管理形態の分岐手順** — Dashboard 型と config.yml 型の両方の手順、再起動時のダウンタイム対策
3. **Docker イメージタグベースのロールバック** — git SHA タグでロールバック、RTO 短縮
4. **dev 環境リソース制限を prod の半分に** — 合計 ~1GB
5. **gitleaks を CI にも追加** — pre-commit のみでは漏れあり

## 6. 依存ドメイン整合性

| 依存 | 状態 | 詳細 |
|---|---|---|
| D1 → D5 | **不整合** | Caddy vs nginx、`/healthz` vs `/api/health` |
| D2 → D5 | 概ね整合 | `output: standalone` 一致、ただし `pnpm` 言及が D5 Dockerfile タスクになし |
| D3 → D5 | 整合 | D2 経由 |
| D4 → D5 | 概ね整合 | D4 追加テーブルの DB 容量見積もりが D5 に含まれない |
| D5 → 既存資産 | **要検証** | kaipoke-net 非実在、cloudflared 形態未確認、VPS スペック未記載 |

## 7. 再レビュー推奨ポイント

1. Phase 0 完了後 — VPS スペック・cloudflared 形態・ディスク構成が判明後、リソース・ネットワーク設計再レビュー
2. D1/D5 Caddy/nginx 統一後
3. dev/prod 分離詳細化後 — ポート/ボリューム/cloudflared ingress
4. cloudflared 設定変更手順の具体化後 — ロールバック可能性含む

**Verdict Justification**: CRITICAL 3件は「計画前提条件が実態と合っていない」同一パターンで、既存インフラ実態調査不足が根本原因。骨格・セキュリティ・段階移行は良質、Phase 0 反映で ACCEPT 可。
