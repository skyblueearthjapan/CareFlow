# D4 External Integrations クロスレビュー B（受入観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 7
**Recommendation**: REQUEST_CHANGES

## 受入基準評価（11項目）

| # | 受入基準 | 評価 | 根拠 |
|---|---|---|---|
| 1 | 全13エンドポイント Postman 疎通 | ○ | 表は整合。疎通=200のみで構造検証未記述 |
| 2 | 409 → KAIPOKE_BUSY Toast 伝播 | △ | エラーコード定義あり、変換仕様（Backend → Frontend）が曖昧、伝播パステスト未策定 |
| 3 | CSP 1行追加後 noVNC iframe 表示 | △ | nginx/Flask 両パターン記載されているが選定未決、ステージング検証手順なし |
| 4 | Bearer Token / API Key が Frontend バンドル不在 | △ | grep 対象・パターン・CI 組み込みが未指定。手動1回確認では回帰リスク未対処 |
| 5 | `/api/ai/interpret` がスタッフ/患者一覧を動的注入 | ○ | Phase E-2 設計済 |
| 6 | 複数アクション解釈で actions[] 2件以上 | ○ | 09設計と整合 |
| 7 | delete+add → companion_change 統合反映 | △ | 境界条件（±1日端、±2日、週またぎ）の網羅未保証 |
| 8 | manuallyHandled 切替で履歴 UI 即時更新 | △ | 楽観更新 or 再取得戦略、同時更新競合の挙動が受入基準外 |
| 9 | Geocoding 同一住所 2回目以降キャッシュヒット | ○ | F-1 設計済、source='cache' テスト未明示 |
| 10 | Gemini/Maps quota 超過時 429 | ○ | H-3 監視設計、Frontend UX (Toast 文言) は受入基準外 |
| 11 | dryRun 統合テスト全パス、ユニット 80%+ | △ | dryRun 定義の二重性（実 kaipoke 呼ぶ vs スタブ）が曖昧 |

## Gaps（優先度順）

**GAP-1: kaipoke ログインセッション切れ警告（5分前 Toast）の検証手段欠落（HIGH）**
- リスク表に 5分以下警告 Toast 記載、受入基準11項目に含まれず
- 提案: 「`loginRemainSec < 300` で Toast 表示」を受入基準に追加、status エンドポイントモックで閾値境界テスト

**GAP-2: Bearer Token 漏洩検出を CI に組み込む手段が未定義（HIGH）**
- 受入基準4「grep 確認」のみ、CI 自動組み込み未記載
- 提案: D5 CI に `grep -rn "KAIPOKE_API_TOKEN\|GEMINI_API_KEY\|GOOGLE_MAPS_API_KEY" .next/static/` を `npm run build` 後ステップとして追加

**GAP-3: dryRun の定義が二重に使われている（HIGH）**
- 「実 kaipoke を呼ぶが書き込まない」と「kaipoke を呼ばないスタブ」が混在
- 提案: 「network-dryRun（スタブ）」と「kaipoke-dryRun（VPS接続あり・write-less）」を分離定義、CI/CD は前者のみ

**GAP-4: confidence < 0.7 の継続計測手段がない（MEDIUM）**
- AiInterpretLog に値は記録されるが、集計・アラート・閾値調整サイクル不在
- 提案: 週次集計、confidence < 0.7 の割合 20% 超でアラート

**GAP-5: VNC token (30分 TTL) 失効後の Frontend 挙動が未定義（MEDIUM）**
- expiry 後 iframe 切断時のエラーハンドリング・再発行フローなし
- 提案: VNC Card にカウントダウン表示と「再接続」ボタン、Backend `/api/integration/vnc-url` 再呼び出しフロー

**GAP-6: CorrectionSheet delete+add 境界条件のテストケース未定義（MEDIUM）**
- 「±1日で companion_change」とあるが、同日/±2日/週またぎのフィクスチャがない
- 提案: 4ケース（同日/±1日/±2日/週またぎ）フィクスチャを H-1 テストに明示

**GAP-7: CSP 適用検証の具体的手段が計画未記載（MEDIUM）**
- ステージングで frame-ancestors 有効化確認手段未記載
- 提案: 受入基準に「DevTools Console で X-Frame-Options / CSP ヘッダ確認、carelink ドメインが含まれる」追加

## dryRun 統合テストシナリオ提案（最低5ケース）

1. **正常系フルフロー (network-dryRun)** — KaipokeClient/Gemini/Geocoding を respx スタブ、expand→diff→apply で全件 DB 永続化
2. **kaipoke 409 Busy 伝播** — `/api/apply` で 409 → FastAPI が KAIPOKE_BUSY 返却、KaipokeJob status=failed、2回目 `/expand` は 200
3. **Gemini 低信頼度・unknown フォールバック** — confidence: 0.55 → action_type: "unknown"、AiInterpretLog 記録、不正 JSON 1回リトライ
4. **loginRemainSec 閾値テスト** — `{loginRemainSec: 290}` → Frontend Toast 「5分以内に切れます」（D3 結合受入）
5. **GeocodingCache ヒット確認** — 同住所 2回実行、1回目 source='google'、2回目 source='cache' (スタブ呼び出し ZERO)

## モック改善提案

1. **KaipokeClient 6エンドポイント別スタブ** — 正常/5xxリトライ/タイムアウトを個別、特に「1回目503→2回目200」リトライ後成功
2. **GeminiClient 4 フィクスチャ** — (a)正常 JSON, (b)malformed (リトライ用), (c)15秒超タイムアウト, (d)429 quota
3. **VNC token exp テスト** — expiry 後の `/api/integration/vnc-url` 再取得シナリオ

## 他ドメイン整合性

| 観点 | 状況 |
|---|---|
| D1 | prefix `/api/v1/` 不整合、環境変数で吸収を明記すべき |
| D3 | bulk endpoint が D3 §3 API リストに未記載 |
| D5 | CSP 設定の owner が D4 と D5 で宙に浮く |
| D1 DB | D1 の `kaipoke_sync` (空定義) と D4 の KaipokeJob/Item で構造異 |

## リリースゲート提案

| ゲート | 条件 | 担当 |
|---|---|---|
| G1 セキュリティ | ビルド成果物に Bearer/API Key 文字列なし (CI アサート) | D4+D5 |
| G2 エラー伝播 | 409/502/503/429 が Frontend Toast に正しく表示 | D4+D3 |
| G3 セッション監視 | `loginRemainSec < 300` で Toast 発火 | D4+D3 |
| G4 dryRun CI | network-dryRun 5シナリオ全 pass、ユニット 80%+ | D4 |
| G5 CSP 検証 | ステージング DevTools で frame-ancestors 確認 (スクショ承認) | D4+D5 |
| G6 confidence 計測 | 本番1週間で confidence < 0.7 が 30%未満 | D4 (運用) |
| G7 スキーマ整合 | D1 DB と D4 永続化ロジックの構造一致 | D1+D4 |

**REQUEST_CHANGES** — GAP 1〜7 解消後、Phase H 実施承認。
