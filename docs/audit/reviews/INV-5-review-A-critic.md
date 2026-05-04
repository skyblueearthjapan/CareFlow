# INV-5 データ戦略 クロスレビュー A（技術観点）

**VERDICT: REVISE**

## 総評

INV-5 は妥当な high-level 戦略文書。A+B アプローチを最適と正しく特定、合理的な phased migration plan を提供。ただし**列レベル分析（central technical foundation）が `PythonAllocateBridge.js` と `UnifiedCode.js` ソースと照合すると material omissions and inaccuracies を含む**。これらの誤りは migration gap analysis を蝕み、Phase 1 中の CSV import 失敗 or データ損失を引き起こす可能性。

## 重大な指摘

### [MAJOR-1] 患者マスタ「20列」記載は不正確、少なくとも4列以上が監査表から欠落
- 監査リスト（§1.1）は 20列、`PythonAllocateBridge.js:364-374` の読込列と一致
- しかし `UnifiedCode.js:1099-1125` の入力フォームは追加列を書き込み:
  - `住所` (line 1105)、`性別` (1104)、`保険区分` (1124)、`訪問頻度` (1110)、`訪問週` (1111)、`備考` (1125)、`希望曜日（複数可）` (1112、`希望曜日` と異なる名前)
- 監査が**最低限欠落**: **住所**, **性別**(患者), **保険区分**, **訪問頻度**, **訪問週**, **備考**
- **住所列は特に critical**: `UnifiedCode.js:6163` で geocoding に使用 (`updateSheetLatLng_(...,'住所','緯度','経度')`)。マッピングしないと lat/lng 自動取得が崩壊
- **Fix**: `UnifiedCode.js` headerMapping 1099-1125 を再監査し、§1.1 と §1.4 gap analysis 更新（実際は 25-26列）

### [MAJOR-2] スタッフマスタ列リスト不完全 — `拠点住所` と `スキル` 欠落
- 監査（§1.2）は 11 列
- `UnifiedCode.js:1127-1135` の入力フォームは追加で `拠点住所` (1128) と `スキル` (1135) も書込
- `拠点住所` は `UnifiedCode.js:6164` で geocoding 入力
- CareLink staff table（D1 schema）に address 列なし → migration gap 検出不能
- **Fix**: §1.2 に追加、staff address gap analysis エントリ作成

### [MAJOR-3] 運用シートが migration scope から完全に欠落
- 監査は患者・スタッフのみ、以下の active sheets は未対応:
  - `週間リクエスト`, `イベントリクエスト`, `スタッフ個別変更リクエスト`, `個別変更リクエスト`, `週間訪問パターン`, `確定スケジュール履歴`, `特別訪問週間_ヘッダ`, `特別訪問週間_明細`, `スタッフ同行割付`, `割当結果`, `割当不可`, `差分結果`, `管理者`, `訪問履歴`, `週ビュー`, `ルートサマリ`, `適用結果`, `検証結果`
- §3 で「訪問パターン」「過去訪問履歴」を簡易言及するが他は未対応
- Phase 1 並行運用中、GAS 割当エンジンは**全シート依存**
- どのシートを CareLink が代替し、どれが GAS-only なのか不明
- **Fix**: 全 ~18 active sheets の inventory（Phase 0/1/2 owner + migration strategy）を追加

### [MAJOR-4] CareLink 患者テーブル列数が誤り（16 ≠ 実際 20+）
- §1.3 「患者テーブル 16カラム」と記載、列挙すると 20列（id, code, name, kana, sex, age, status, insurance, address, lat, lng, primary_office_id, required_staff_count, sex_restriction, weekly_pattern, special_week, note, deleted_at, created_at, updated_at）
- D1 計画（`D1-backend-plan.md:214-217`）では `ng_time_start, ng_time_end` も追加 → 22列
- gap analysis が「16列」を target としており scope 過小評価
- **Fix**: 22 列に訂正、gap analysis 更新

## Minor Findings

1. §5 Sheets API code snippet が同期 `gspread`、D1 backend は async（`FastAPI + SQLAlchemy 2.0 async`）→ `gspread_asyncio` or thread pool 必要
2. §2 案B Quota 「読込300/分、100/秒」は不正確 — Sheets API v4 は 300/分/プロジェクト、60/分/ユーザー
3. §4 Phase 2 cron `0 0 * * 1` 「毎週月曜 00:00 (Asia/Tokyo)」だが、cron はサーバ TZ で動作。VPS が Malaysia なら `TZ=Asia/Tokyo` 明示が必要
4. §6 cost table 「A のみ 4日」と §2 「案A 3日」が矛盾

## 不足項目

- **Phase 1 CSV import 失敗時のロールバック計画なし** — 列マッピング誤りでデータ corrupt 時の recovery procedure 未記載
- **CSV import の validation rules 未定義** — 空 patient_id、重複 code、無効 lat/lng で何が起きるか
- **Sheets API sync 競合検出メカニズム不在** — 「CareLink 優先」の前提として`updated_at` 比較が必要だが、スプレッドシート側に `updated_at` 列が存在しない
- **`管理者` シート未言及** — GAS のアクセス制御、Phase 1 で両系統に user management が必要
- **CareLink staff table に address 列がない** — 既存スプレッドシートは `拠点住所/緯度/経度` を保持。これは CareLink schema 自体の gap
- **`weekly_pattern JSONB` の transformation 仕様 0** — 「高難度」と分類のみ、詳細仕様皆無。既存 `週間訪問パターン` シート（`PythonAllocateBridge.js:594`）から JSONB へのマッピングは non-trivial

## Multi-Perspective

- **Executor**: §5 Python snippet を見ても、実際の spreadsheet column headers と SHEETS_ID が不明、`weekly_pattern` JSONB 構造も不明 → reverse-engineer から始める必要 → feasibility gap
- **Stakeholder**: 9日見積もりは pure 実装には妥当、ただし column mapping research（本レビューが指摘した不完全性）の追加 1-2 日要
- **Skeptic**: 案B Sheets sync を 2ヶ月だけ維持して Phase 2 で deprecate なら 5日の throwaway 開発。Phase 2 が slip すると permanent technical debt

## Open Questions

- Spreadsheet structure は環境間で identical か？ `UnifiedCode.js` headerMapping で書込まれるが `PythonAllocateBridge.js` で読込まれない列（`訪問頻度`, `訪問週` 等）は dormant の可能性。実 production シートで verify 必要
- 既存シート `保険区分` 列は CareLink `insurance` enum に直接 map するか？ 監査は `insurance` を「新規」とするが、実際は既存に存在
