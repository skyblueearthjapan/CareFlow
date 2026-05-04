# INV-2 Python API監査 クロスレビュー B（受入観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 4
**Recommendation**: REQUEST_CHANGES

## 受入基準評価

| # | 観点 | Status |
|---|---|---|
| 1 | PlaywrightTest1 中継の必須前提条件（.env/state.json/KAIPOKE_API_TOKEN/起動順序） | PARTIAL |
| 2 | 同時実行制御の受入基準（409 → KAIPOKE_BUSY 伝播） | PARTIAL |
| 3 | VNC iframe 統合の CSP frame-ancestors 設定 | MISSING |
| 4 | /api/apply/result, /api/export/result の CareLink 結合検証手順 | PARTIAL |

## Gaps

**Gap 1: CSP frame-ancestors への CareLink ドメイン未追加（HIGH）**
- 現状 `api_server.py:157` は `https://script.google.com https://*.googleusercontent.com` のみ
- CareLink ドメイン（`carelink.kaipoke-api.net` 等）は未追加
- D4 Phase D-2「frame-ancestors 1行追加」が計画されているが未実施
- ALLOWED_ORIGINS 環境変数化（INV-2 §7 推奨）も未実装
- 提案: `ALLOWED_ORIGINS=https://script.google.com https://*.googleusercontent.com https://carelink.kaipoke-api.net` を docker-compose.yml に追加

**Gap 2: セッション切れ時の CareLink 側エラー定義が欠如（HIGH）**
- INV-2 に「state.json 無効時は kaipoke が 502/ログイン失敗」前提条件記載なし
- CareLink Backend が `503 KAIPOKE_LOGIN_EXPIRED` にマッピングする処理を D4 Phase A-2 KaipokeClient に含めるべき
- D4計画には記載あるが INV-2 反映が未完

**Gap 3: 同時実行「超え」の受入基準が定性記述のみ（MEDIUM）**
- 「キュー + 複数インスタンス展開が必須」止まり
- 最大同時リクエスト数 N、キュータイムアウト T 秒、CareLink Frontend 待機 UI 表示条件 が数値未定義
- 提案: §7.3 に数値で明記

**Gap 4: ポーリングの結合検証手順が未定義（MEDIUM）**
- §7.1 に「CareLink → /api/integration/apply → kaipoke /api/apply → /api/apply/result ポーリング（5秒×120回 = 10分）」のシーケンス図 or test_api.py 流用検証スクリプト未記載

**Gap 5: CareLink Backend 実装未着手（HIGH・ブロッカー）**
- `CareLink/` に Python/JS ソース不在（設計書のみ）
- INV-2 は PlaywrightTest1 監査として完成しているが、「CareLink との結合検証」は D1/D4 実装完了後でなければ実施不可能

## 受入チェックリスト

| # | 項目 | 必須度 |
|---|---|---|
| 1 | api_server.py CSP に CareLink ドメイン追加（ALLOWED_ORIGINS 環境変数化） | MUST |
| 2 | セッション切れ → 503 KAIPOKE_LOGIN_EXPIRED マッピング実装 | MUST |
| 3 | 同時実行リクエスト数・タイムアウト閾値の数値定義 | MUST |
| 4 | ポーリング検証スクリプト（test_api.py 流用拡張）作成 | MUST |
| 5 | CareLink Backend D1/D4 実装完了 → 結合テスト可能化 | MUST |
| 6 | KaipokeClient で 409/502/503 → KAIPOKE_BUSY/UNREACHABLE/LOGIN_EXPIRED 変換 | MUST |
| 7 | VNC iframe ステージング動作確認（CSP 適用後） | SHOULD |
| 8 | /api/kaipoke/run のジョブ ID 管理と KaipokeJob テーブル紐付け | MUST |
| 9 | api_server.py の test_api.py を CareLink 結合用に拡張 | SHOULD |
| 10 | supervisord 起動順序の手動確認チェックリスト | SHOULD |
