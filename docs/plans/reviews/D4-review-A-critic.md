# D4 External Integrations クロスレビュー A（技術観点）

**VERDICT: REVISE**

## 1. 総評

全体として網羅性が高く、既存資産保護の原則（CSP 1行のみ）は明確に宣言されている。中継エンドポイント設計、Gemini プロンプト、VNC token、エラーコード体系は十分な水準。しかし、既存 kaipoke-api の実APIとの乖離（ポーリング型 `/api/apply/result` `/api/export/result` の未対応）、D1 計画との API prefix 食い違い、`companion_change` の根拠不在、VNC token 方式の二重設計という重大な問題がある。

## 2. 強み

1. **既存資産保護の原則が明示的かつ徹底的** — CSP 設定例（nginx/Flask 両パターン）、ロールバック手順まで記載
2. **エラーコード体系が統一的** — 409/502/503/429 の4種定義、Frontend Toast 仕様と対応
3. **Gemini プロンプト設計が具体的** — システムプロンプト全文、JSON Schema 強制、信頼度基準、入出力例

## 3. 重大な指摘

### [CRITICAL-1] 既存 kaipoke-api のポーリング型エンドポイントが D4 計画から欠落
- 既存 `api_server.py` は非同期ジョブ結果取得に `GET /api/export/result` (L473) と `GET /api/apply/result` (L664) を使用
- D4 §5 の endpoint 一覧に**この2つが存在しない**
- B-3 (export) は「30分 TTL キャッシュ」のみ、kaipoke-api 側 `/api/export/result` の中継方式未定義
- **Fix**: ポーリング中継方式を明記。Backend が内部 polling で KaipokeJob テーブルに書込 or Frontend 向け結果取得 endpoint 追加

### [CRITICAL-2] D1 計画との API パス prefix 不整合
- D1: `/api/v1/integration/*` 全12行
- D4: `/api/integration/*` （prefix なし）
- D3 (Frontend) も prefix なしで参照
- → 結合時に全エンドポイントのルーティング破綻
- **Fix**: 3計画書を同時修正で統一

### [MAJOR-1] `companion_change` がコードベースに根拠なし
- D4 B-4 で「±1日 → companion_change」と定義
- 既存 KaipokeRpa.js / KaipokeDiff.js には `companion_change` 文字列が一切なし
- 既存は `date_change` を使用、「同行」は `ACCOMPANY` ロール
- → 境界条件（±1日判定、同一 patient 判定）が既存 diff のどの出力に対応するか不明
- **Fix**: 既存 `/api/diff` レスポンスの JSON 構造を参照、judgment ロジックを擬似コードレベルで記載

### [MAJOR-2] VNC token 方式の二重設計
- 既存 kaipoke-api: `secrets.token_urlsafe(16)` ランダムトークン + サーバーメモリ dict + TTL 管理 (L116-126)
- D4 D-1: 「30分 TTL JWT 発行 (HS256, exp, sub=userId, aud=novnc)」 — 全く異なる方式
- 既存 `/novnc/verify` (L1647) はランダムトークン dict ルックアップで検証
- → CareLink Backend が JWT 発行しても kaipoke-api 側は検証不可。kaipoke-api 変更は CSP 原則違反
- **Fix**: 既存 `/api/kaipoke/vnc-url` (L1633) を Bearer 付きで中継、ランダムトークン入り URL をそのまま Frontend に返す。JWT 不要。kaipoke-api 変更ゼロ

### [MAJOR-3] D1 計画に存在しない D4 エンドポイント3つ
- `GET /api/integration/correction-sheets/:id/items` (D4 C-2)
- `POST /api/integration/correction-sheets/:id/items/bulk` (D4 C-4)
- `GET /api/integration/job-items/:id/screenshot` (D4 G-1)
- → D1 が forward 実装時に漏れる、UI 機能不全
- **Fix**: D1 の Integration 表に追加、T26 のスコープ拡張

## 4. 見落としリスク

- 既存 kaipoke-api の `/api/expand`, `/api/export`, `/api/diff`, `/api/apply` は **Bearer 認証なし**（@require_auth 未付与）。一方 `/api/kaipoke/*` 系は Bearer 必須。KaipokeClient の Bearer 自動付与方針はどちらの認証体系を使うか不明確
- 既存 `/api/config`, `/api/drive/files`, `/api/diff/validate`, `/api/allocate` が D4 計画でカバーされていない
- VNC iframe URL に token が含まれ URL バー露出・履歴残留リスク。30分 TTL で緩和されるが、共有端末漏洩対策（使い捨て＝1回検証で無効化）が未記載
- `GOOGLE_MAPS_API_KEY` の referrer 制限への言及なし

## 5. 改善提案

1. **kaipoke-api 二系統 API の選択を明記** — レガシー系（`/api/expand` 等、認証なし、ポーリング型）と新 API 系（`/api/kaipoke/run` 等、Bearer 必須、ジョブ管理型）。推奨：新 API 系で統一
2. **Gemini quota の具体値定義** — admin 100回/日、staff 30回/日 等
3. **correction_data の Backend スキーマ検証** — `auto_apply.py` 入力スキーマと共有 zod スキーマで再検証を B-5 に明示
4. **Phase E の依存図正確化** — E-2 (プロンプトビルダー) は D1 患者/スタッフ CRUD + seed 完了後でないと検証不能

## 6. 依存ドメイン整合性

| 観点 | 状況 |
|---|---|
| D1 API prefix | 不整合 (CRITICAL-2) |
| D1 endpoint 5つ追加要 | 3つ欠落 (MAJOR-3) |
| D1 allocate forward 先 | D4 に allocate endpoint なし |
| D3 VncCard | Bearer Token と書くが D4 方式（JWT or 既存）未確定 |
| D5 `VNC_TOKEN_SECRET` | D4 が JWT 採用なら D5 .env に追加要 |

## 7. 再レビュー推奨ポイント

1. kaipoke-api 二系統 API の選択確定後、KaipokeClient とポーリング中継再レビュー
2. D1/D4/D3 API prefix 統一後、3計画書 endpoint 表完全照合
3. VNC token 方式確定後（既存中継 or JWT）、D-1 工数妥当性
4. `companion_change` 判定擬似コード追加後、境界条件テスト網羅性

**VERDICT**: CRITICAL 2件 + MAJOR 3件。実装着手前に必須解決。
