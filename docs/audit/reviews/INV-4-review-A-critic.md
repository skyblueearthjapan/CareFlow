# INV-4 VPS稼働検証 クロスレビュー A（技術観点）

**VERDICT: ACCEPT-WITH-RESERVATIONS**

## 総評

INV-4 は構造化された証拠ベースの VPS 監査で、D5-review-A の3つの CRITICAL（kaipoke-net 不在、cloudflared 形態、VPS スペック）を実データで解決。MASTER-PLAN Phase 0 Go/No-Go ゲートを直接駆動可能。ただし**CPU「同居不可」結論はソフトリミットの不一致を過大解釈**しており、いくつかの所見はリソース判断に必要な分析深度を欠く。

## 重大な指摘

### [MAJOR-1] CPU 2.2コア「同居不可」判断は過度に断定的
- 証拠: §7「CPU | 2 vCPU、idle | 2.2コア要求 > 2 vCPU | 同居不可」
- D5 docker-compose の `deploy.resources.limits` は postgres 0.5 + backend 1.0 + frontend 0.5 + nginx 0.2 = 2.2
- **Docker `cpus` limit はハードリザベーションではなく CFS bandwidth 制限**
- 4コンテナが同時に上限を使い切ることはほぼない
- 既存6コンテナ合計 CPU 3.3% という実測値が証明、idle 時はゼロ
- 2 vCPU で limits 合計 2.2 は「ピーク時に若干のスロットリング」=「同居不可」ではない
- このまま進むと不要なプランアップグレード（月額コスト増）or 別 VPS 調達（運用複雑化）に誘導
- **Fix**: 「同居可（ピーク時にスロットリングの可能性あり）」に修正
  - (a) backend cpus を 0.8 に下げて合計 2.0 に収める案
  - (b) 実負荷テスト後にアップグレード要否を判断
  - 「不可」ではなく「要注意・要検証」が正確

### [MAJOR-2] D5 計画との衝突分析が不完全（3件未言及）
- §7 差分表は7行あるが以下が欠落:
  1. D5 「cloudflared (既存コンテナ)」想定 → 実際は systemd → D5 Phase 3 タスク12-14 が全て無効、影響度未記載
  2. D5「dev/prod 両環境」のポートバインド衝突（D5-review-A MAJOR-2）が完全未言及
  3. D5 バックアップ先 `/mnt/backup/carelink/` だが INV-4 で `/mnt` は空と実測 → 直接矛盾が差分表になし
- **Fix**: D5 衝突表に上記3件追加、特にバックアップパス矛盾は Phase 5 ブロッカーとして明記

## Minor Findings

1. **Swap 4GB の根拠不明**: メモリ 7.8GB / available 4.8GB / CareLink 1.9GB なら残り 2.9GB で OOM リスク低い。Swap が保険なら 2GB で十分な可能性も。サイジング根拠を追記すべき
2. **公開ポートのセキュリティ評価浅い**: 5984/5678/8080 が `0.0.0.0` バインドか `127.0.0.1` バインドかの区別なし。外部到達可能ならファイアウォール整備が急務
3. **line-webhook（Exited 2ヶ月）の扱い未言及**: Docker GC 推奨アクションに含まれず
4. **nginx 死に設定の処置**: `linebot` 設定が死に設定だが推奨アクションに含まれず

## 不足項目

- 既存コンテナ CPU 使用パターンの時系列データ（cron healthcheck 集中時のピーク）
- ネットワーク帯域/レイテンシ実測（日本→マレーシア RTT がユーザ体験に直結）
- ディスク I/O 性能（PostgreSQL 同居判断には IOPS が重要、`iostat`/`fio` 結果なし）
- 既存サービスのリソーストレンド（2ヶ月 RPA 未実行で現在使用量は異常に低い、復活後の見込みなし）
- firewall (ufw/iptables) 状態（ポート列挙のみ、実際のルール未確認）

## ACCEPT 昇格条件

1. CPU 判断を「不可」から「要注意（limits 調整 or 実負荷テストで判断）」に修正
2. D5 衝突表にポート衝突・バックアップパス矛盾・Phase 3 手順無効化を追加
3. 推奨アクション7件に優先度と依存関係を付与（Docker GC → 残容量再評価 → Swap → デプロイ試行 → CPU 不足確認 → アップグレード判断）

## Open Questions

- hermes-agent (298 MiB) と openclaw-gateway (612 MiB) の用途と必要性。不要なら停止で 910 MiB 回収可能
- Hostinger プランアップグレードの選択肢（4vCPU/8GB/価格/マイグレーション手順）未調査
- state.json への Access が 4/29 にあるが何が読みに来ているか不明（cron? 外部?）

## Verdict Justification

THOROUGH モードのまま（ADVERSARIAL 不要）。SSH 実測に基づく数値は具体的で信頼性が高い。ただし MAJOR-1 の CPU 判断は技術的に不正確で、このまま意思決定に使うと過剰投資。MAJOR-2 の D5 衝突欠落は、せっかくの実態調査が D5 改訂に完全には活用できない状態を意味する。
