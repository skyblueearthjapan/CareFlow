# INV-5 データ戦略 クロスレビュー B（受入観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 4
**Recommendation**: REQUEST_CHANGES

## 受入基準評価

### 1. 案A+B 採用時の実装受入基準
| # | Criterion | Status |
|---|---|---|
| 1 | `/api/v1/import/patients` `/import/staff` 仕様（入力スキーマ・エラーレスポンス）が明示 | MISSING |
| 2 | 案B Sheets同期時のCareLink優先競合解決ルールが明文化 | PARTIAL |
| 3 | weekly_pattern（JSONB）への変換マッピング | MISSING |
| 4 | 案A実行後の受入テスト手順 | MISSING |

### 2. Phase 0/1/2 ゲート判定可能性
| # | Criterion | Status |
|---|---|---|
| 5 | Phase 0→1: CSV import 完了の定量基準（全件 import 成功、エラー率 0%） | MISSING |
| 6 | Phase 1→2: 並行稼働の合格条件（N週連続エラーゼロ、不一致件数 ≤ X等） | MISSING |

### 3. データ整合性検証
| # | Criterion | Status |
|---|---|---|
| 7 | CSV import後の整合性検証クエリ/ツール | MISSING |
| 8 | Sheets sync後のチェックサム検証実装仕様 | PARTIAL |
| 9 | audit_logs スキーマ（失敗件数・失敗詳細含む） | PARTIAL |

### 4. ロールバック戦略
| # | Criterion | Status |
|---|---|---|
| 10 | CSV import 失敗時のロールバック手順（部分挿入50件失敗等） | MISSING |

## Gaps

**Gap 1: weekly_pattern 変換仕様の欠如（HIGH）**
- 「高難度」マークのみ、入出力仕様未定義
- 実装者独自解釈でデータ不整合リスク
- 提案: 変換マッピング表（"月木金" → `{"monday":{...},"thursday":{...},"friday":{...}}`）

**Gap 2: ゲート判定基準の欠如（HIGH）**
- 移行判断が「チェックリスト消化」依存、定量基準なし
- 移行後の問題発覚時の責任判断困難
- 提案: 各ゲートに測定可能な合格条件

**Gap 3: トランザクション境界の未定義（HIGH）**
- 一括CSVインポートのアトミック性が不明
- 部分失敗時のDB不整合リスク
- 提案: 全件成功時のみ commit、または row 単位 commit + エラーログ

**Gap 4: `get_spreadsheet_update_time()` 実装根拠なし（MEDIUM）**
- Google Sheets API はセル行レベル更新時刻を標準取得不可
- 代替: Drive API `modifiedTime` or `last_modified_col` 列追加

**Gap 5: 新規必須フィールドの移行元データなし（MEDIUM）**
- age, insurance, kana が「既存に未記載の場合あり」のまま
- NULL 許容 or Phase 1 中に手入力収集の方針未定義

## 受入チェックリスト

| # | 項目 | 必須度 |
|---|---|---|
| 1 | CSV import API スキーマ（入力/出力/エラーコード）確定 | MUST |
| 2 | weekly_pattern 変換マッピング表（曜日→JSONB）確定 | MUST |
| 3 | Phase 0→1 ゲートの定量基準（成功率/エラー件数閾値） | MUST |
| 4 | Phase 1→2 ゲートの定量基準（連続稼働期間/不一致件数） | MUST |
| 5 | CSV import のトランザクション境界（全件 atomic or row単位） | MUST |
| 6 | データ整合性検証クエリ（DB件数 vs スプレッドシート件数） | MUST |
| 7 | audit_logs スキーマ（inserted/updated/failed/errors[]） | MUST |
| 8 | `get_spreadsheet_update_time()` の代替実装方針 | SHOULD |
| 9 | age/insurance/kana の補完戦略（NULL/手入力/外部参照） | SHOULD |
| 10 | Sheets API 認証情報（Service Account JSON）の secrets 管理 | MUST |
