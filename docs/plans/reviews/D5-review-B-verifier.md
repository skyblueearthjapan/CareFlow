# D5 DevOps & QA クロスレビュー B（受入観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 7
**Recommendation**: REQUEST_CHANGES

## 1. 総評

D5計画は「既存サービスとの隔離」「日次バックアップ」「段階的自動化」の三軸が明確で、土台構造は本番投入に耐える水準。ただし受入基準10項目のうち計測手段が曖昧なものが3項目、secrets 漏洩時の失効手順・flaky test のリトライ戦略・PWA 検証基準の具体性不足。runbook 4種は文書として存在するが「他者が実行可能か」の検証プロセスが計画に含まれていない。

## 2. 受入基準各項目評価

| # | 受入基準 | 評価 | 根拠 |
|---|---|---|---|
| 1 | `/api/health` 200 | ○ | Phase 3 疎通確認あり。**D1 `/healthz` と D5 `/api/health` パス不一致** |
| 2 | ログイン画面描画 | ○ | next build 通れば確認可、Playwright E2E カバー |
| 3 | dev/prod 独立 DB | ○ | compose project 名・volume 名分離明示 |
| 4 | kaipoke-api ±10% 以内 | △ | Phase 0/3 計測コマンドが未定義、比較元データ取得手順なし |
| 5 | CI 自動実行・green | ○ | backend-ci/frontend-ci.yml 仕様具体的 |
| 6 | 手動デプロイで prod 反映 | △ | runbook 検証プロセスが計画内に存在しない |
| 7 | 日次バックアップ・月次復元テスト | ○ | 自動テスト記載、ただし「誰が実行・通知先」未定義 |
| 8 | Discord 通知 | ○ | healthcheck-carelink.sh 失敗時 Webhook 明示 |
| 9 | secrets が git に含まれない | △ | gitleaks pass 要件のみ、pre-commit hook + CI workflow が計画外 |
| 10 | runbook 4種完成 | △ | パス明示のみ、完成定義（チェックリスト・他者レビュー）なし |

## 3. Gaps（優先度順）

**A. ヘルスチェック URL 不一致（HIGH）**
- D1: `/healthz`/`/readyz`、D5: `/api/health`
- nginx `/api/` プレフィックス経由では `/api/health` は存在しないので常に失敗
- 提案: 統一、healthcheck スクリプトを正しいパスに

**B. alembic マイグレーション実行タイミング欠落（HIGH）**
- 起動時に誰がいつ実行するかが deploy.md 想定にない
- マイグレーション未実行のまま本番起動オペミスリスク

**C. dev 環境バックアップ方針の欠如（MEDIUM）**
- prod のみ対象と明記、dev に個人情報を入れない明文化が必要

**D. 月次 pg_restore 自動テストの成功判定基準（MEDIUM）**
- 「流して終わり」では復元品質保証なし
- 提案: テーブル件数・主要クエリ疎通の assertion

**E. ログローテーション後の永続性（MEDIUM）**
- docker `--log-opt max-size=50m max-file=5` で最大 250MB
- アクセスログ長期保存先なし
- 提案: 医療情報の保持期間要件明記、`/mnt/backup/carelink/logs/` 週次 archive

**F. ロールバック具体的トリガー定義（HIGH）**
- 「失敗」の定義（healthcheck 何秒以内に 200 か）未定義
- ロールバック起動しないリスク

**G. Cloudflare Tunnel 設定変更ロールバック（HIGH）**
- cloudflared config 失敗時の手順が disaster-recovery.md にあるか不明
- 既存 kaipoke-api が道連れ停止リスク

## 4. 既存サービス無影響の計測プロトコル提案

**Phase 0（ベースライン取得）**:
```bash
# レスポンスタイム10回平均
for i in $(seq 1 10); do
  curl -o /dev/null -s -w "%{time_total}\n" https://kaipoke-api.net/api/status
done | awk '{sum+=$1; count++} END {print sum/count}'

# Docker stats 5分×12回 (1時間分)
docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}" >> baseline.csv

# ホスト OS
free -h && df -h && uptime
```
記録: P50/P95/P99、CPU 使用率平均・ピーク、メモリ使用量（14日分理想）

**Phase 3（CareLink デプロイ直後）**:
```bash
# 同一スクリプト再実行 + 差分算出
# 判定: |新P95 - 旧P95| / 旧P95 > 0.10 で即時停止
hey -n 100 -c 5 https://kaipoke-api.net/api/status
```

**継続監視**: healthcheck-kaipoke.sh に time_total 記録追加、週次サマリで baseline と比較

## 5. runbook 検証方法

| runbook | 検証方法 | タイミング |
|---|---|---|
| deploy.md | 作成者以外が dev 環境に手順通り実行、ゼロ差分で成功 | Phase 6 完了後・本番適用前 |
| rollback.md | 意図的に壊れたイメージをデプロイ → rollback 手順で旧版復帰 + healthcheck 200 | Phase 7 自動デプロイ実装前 |
| rotate-secrets.md | NEXTAUTH_SECRET ローテで再ログイン要求と旧セッション無効化確認 | Phase 6 完了後 |
| disaster-recovery.md | pg_restore を dev の別 DB に実行、テーブル件数整合性、RTO 4時間達成確認 | 月次 pg_restore と同期 |

完成基準: 「作成者以外が30分以内に完遂できること」

## 6. 他ドメイン整合性

**D1**: `/healthz`/`/readyz` vs `/api/health` 不一致、`/readyz` を healthcheck に含めていない（DB 障害時にプロセス生存だが動作不能を検知不可）

**D2**: PWA Lighthouse 通過の検証責務がどちらか不明（HTTPS 提供は D5 側）

**D3**: モバイル E2E が「main PR 時のみ」、PWA standalone モードの CI 組込確認必要

**D4**: Bearer Token 漏洩検出の grep（ビルド成果物検査）が D5 CI に未組込

## 7. リリースゲート提案

| Gate | 条件 |
|---|---|
| G1 既存サービス無影響 | デプロイ後24時間モニタで kaipoke-api P95 が baseline ±10% 以内（ログ・グラフ証跡） |
| G2 バックアップ復元 | pg_restore 成功、patients/visits/staff 件数一致（月次ログ証跡） |
| G3 Secrets 完全性 | git log で機密値不在、gitleaks CI green、rotate-secrets を他者実行済 |
| G4 全 runbook の他者実行 | 4種を作成者以外が dev で実行（署名 or ログ） |
| G5 PWA + モバイル動作 | Lighthouse PWA Installability green、iOS Safari + Android Chrome でログイン〜週ビュー動作 |

**REQUEST_CHANGES** — 受入基準4・9・10の具体化、ヘルスチェック URL 統一、gitleaks の CI 組込タスク追加の3点を最低限解消後に本番投入判断可。
