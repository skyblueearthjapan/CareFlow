# INV-1: GAS App コード監査レポート

## 1. アーキテクチャ全体像

GASアプリの責務：
- Webフロントエンド（UnifiedCode.js: 7446行）で入出力UI提供
- Python割当エンジンへのデータブリッジ（PythonAllocateBridge.js: 1022行）
- カイポケAPI連携（KaipokeRpa.js: 2687行）
- カイポケCSV収支確認（KaipokeImport/Export/Diff: 1454行）
- スプレッドシート操作・マスタ管理

### データフロー図

```
【入力】患者マスタ/スタッフマスタ → 【GAS】週間リクエスト生成 
  ↓
【Python API】/api/allocate（10種類データ送信）
  ↓ 割当結果
【GAS】割当結果シート更新 + 週ビュー・ルートサマリ生成
  ↓
【出力】CSV化 → Google Drive or カイポケRPA自動入力
  ↓
【差分確認】カイポケ実績CSV ↔ GAS出力CSV（修正差分レポート）
```

---

## 2. スプレッドシート構造

定義箇所: `UnifiedCode.js:13-48`

### 出力系シート
| シート名 | 役割 | 主要列 |
|---------|------|-------|
| 週ビュー | スタッフ×患者マトリックス | staff_name × date |
| 週間リクエスト | 割当前の訪問需要 | patient_id, date, start_time, end_time |
| 割当結果 | Python割当の最終出力 | visit_id, patient_id, staff_id, date, 開始/終了 |
| 割当不可 | 未割当の理由報告 | patient_id, date, 理由 |
| ルートサマリ | スタッフ別行動順序・移動距離 | staff_id, 訪問順序, 移動時間 |

### 入力系シート
| シート名 | 役割 |
|---------|------|
| 患者マスタ | patient_id, 患者名, 保険区分, 週訪問回数, 指定スタッフID |
| スタッフマスタ | staff_id, スタッフ名, シフト開始/終了, 勤務曜日 |
| 個別変更リクエスト | patient_id, date, 開始時刻, 終了時刻 |
| スタッフ個別変更リクエスト | staff_id, date, 制限タイプ |
| イベントリクエスト | event_id, staff_id, date |

### 参照用シート
- 外部CSV_RAW（カイポケCSV貼付け）: `KaipokeImport.js:14`
- 外部_正規化（正規化済カイポケデータ）
- 差分レポート（差分検出結果・色分け）
- 訪問履歴（過去割当・rotation 用）
- スタッフ同行割付、特別訪問週間_ヘッダ/明細、週間訪問パターン

### FK 関係
- 週間リクエスト → 患者マスタ (patient_id)
- 週間リクエスト → スタッフマスタ (staff_id, 指定スタッフID)
- 割当結果 → 週間リクエスト (patient_id + date)
- 割当結果 → スタッフマスタ (staff_id)
- スタッフ個別変更 → スタッフマスタ
- スタッフ同行割付 → スタッフマスタ (trainee/mentor)

### データ量規模
- 患者マスタ: 数十〜数百件
- スタッフマスタ: 10〜50名
- 週間リクエスト: 週当たり数百件
- 割当結果: 週当たり数百件

---

## 3. カイポケCSV ↔ スケジューリング結果CSV 差分検出フロー

### 3a. CSV出力（KaipokeExport.js）
エントリ: `kaipoke_exportCsv(weekStartStr)` Line 35-146

処理フロー：
1. 割当結果シート読込 (Line 44)
2. 患者マスタ読込 (Line 85, `kaipoke_loadPatientMaster_`)
3. イベントリクエスト読込 (Line 88)
4. 同一訪問でグルーピング (Line 91, `kaipoke_groupVisits_`)
   - 通常: `日付|patient_id|開始時刻|終了時刻` キー
   - イベント: `EV|日付|visit_id|開始時刻|終了時刻` キー
5. CSV行変換 (Line 94, `kaipoke_buildCsvRows_`)

出力CSV 18列 (Line 101-106):
```
職員名1, 職種1, 職員名2, 職種2, 同行2,
職員名3, 職種3, 同行3, 事業所名,
日付, 曜日, 利用者, 業務種別, サービス内容,
開始時間, 終了時間, 提供時間, 備考
```

ファイル保管：
- Google Drive: `gas_optimized_YYYYMMDD_YYYYMMDD.csv` (Line 128-136)
- 同名ファイルは上書き

### 3b. CSV取込み正規化（KaipokeImport.js）
エントリ: `kaipoke_importFromRawSheet(yearMonth)` Line 49-86

処理フロー：
1. 外部CSV_RAW読込 (Line 55)
2. スタッフ名→IDマップ作成 (Line 70)
3. 患者名→IDマップ作成 (Line 73)
4. 行を正規化 (Line 76, `kaipoke_normalizeRows_`)
   - 職員1 → MAIN ロール
   - 職員2/3 → ACCOMPANY ロール
   - 各行を独立レコード化（名前→ID変換）
5. 外部_正規化シートに出力 (Line 79)

正規化形式 (Line 359-362):
```
source, ymd, dow, staff_name, staff_id,
patient_name, patient_id, start, end, duration_min,
role, business_type, service_type, note, raw_row
```

### 3c. 差分検出（KaipokeDiff.js）
エントリ: `kaipoke_detectDiff(weekStartStr, options)` Line 40-81

差分判定ステータス (Line 17-24):
- `OK`: 完全一致
- `TIME_MISMATCH`: 時間差異（許容5分: Line 27）
- `MISSING_INTERNAL`: カイポケにあるが内部なし
- `EXTRA_INTERNAL`: 内部にあるがカイポケなし
- `STAFF_MISMATCH`: 担当スタッフ違い
- `ID_UNKNOWN`: スタッフ/患者ID不明

比較ロジック (Line 184-308 `kaipoke_compareSets_`):
1. 内部データをインデックス化
   - 厳密キー: `ymd|staff_id|patient_id|start`
   - 緩いキー: `ymd|patient_id`
2. 外部レコードを優先順照合：
   - (1) 厳密マッチ → OK
   - (2) 患者同一・時間違う・同スタッフ → TIME_MISMATCH
   - (3) 患者同一・違うスタッフ → STAFF_MISMATCH
   - (4) 患者も不一致 → MISSING_INTERNAL
3. 残った内部 → EXTRA_INTERNAL

レポート出力 (Line 317-412):
- 差分レポートシート（色分け）：OK緑/時間黄/スタッフ橙/MISSING赤/EXTRA青/ID_UNKNOWN灰
- 右側にサマリテーブル

### 既知のエッジケース
1. イベント行の患者名抽出: 備考から `（P\d+）` 正規表現 (Line 195) - 形式違いで失敗
2. シリアル値の時間変換: 24*60 分で正規化、GAS/Python間で精度差
3. スタッフ4人以上: CSV最大3人 (Line 295-304)、4人目は備考
4. 時間許容範囲: 5分固定 (Line 27)、ユーザーカスタマイズ不可

---

## 4. カイポケAPIへのデータ連携フロー（GAS→VPS）

API ベース: `https://kaipoke-api.net` (`KaipokeRpa.js:6`)

### 全エンドポイント

| エンドポイント | HTTP | 用途 | 認証 |
|---|---|---|---|
| /api/status | GET | サーバー状態 | なし |
| /api/stop | POST | 緊急停止 | なし |
| /api/expand | POST | 月間展開 | なし |
| /api/export | POST | CSV出力 | なし |
| /api/diff | POST | 差分検出 | なし |
| /api/apply | POST | 修正適用 | なし |
| /api/apply/result | GET | 適用結果ポーリング | なし |
| /api/test | GET | 接続テスト | なし |
| /api/kaipoke/logs | GET | 実行ログ | なし |
| /api/kaipoke/vnc-url | GET | VNC接続URL | なし |
| /api/kaipoke/status | GET | カイポケ画面状態 | なし |
| /api/config | GET | サーバー設定 | なし |
| /api/allocate | POST | Python割当 | なし |
| /api/allocate/debug | POST | デバッグモード | なし |

認証: Bearer なし、`muteHttpExceptions: true`

### ポーリング動線

**expand**: (Line 107-188)
- POST /api/expand → Cloudflare 524 可能性
- /api/status を15秒×40回ポーリング（10分）
- `current_task.running === false` で完了判定

**apply**: (Line 481-621)
- POST /api/apply → 即返
- バックグラウンド処理開始
- GET /api/apply/result を1回呼び確認

### エラー時UX
- 409 Conflict: 別タスク実行中
- 524 Timeout: ポーリングで完了待ち
- 接続不可: muteHttpExceptions で例外化なし

---

## 5. PythonAllocateBridge.js の連携IF

エントリ: `runPythonAllocate(weekStartStr)` Line 16-168

### 入力ペイロード（10種類）
POST /api/allocate (Line 64-76):
```javascript
{
  week_start: "yyyy/MM/dd",
  staff_masters: [],
  patient_masters: [],
  weekly_requests: [],
  events: [],
  staff_changes: [],
  weekly_patterns: [],
  confirmed_history: [],
  patient_changes: [],
  special_week: {},
  mentor_pairs: []
}
```

### 各データローダー出力形式

**staff_masters** (Line 305-347): staff_id, staff_name, gender, lat/lng, shift_start/end_minutes, work_days, areas, max_per_day, alloc_pref

**patient_masters** (Line 349-406): patient_id, patient_name, area, lat/lng, service_minutes, weekly_count, need_staff, sex_limit, continuation_pref, fixed/ng_staff_ids, pref/ng_days, time_type, start/end_pref_minutes, day_priority, status

**weekly_requests** (Line 408-482): request_id, date, weekday, patient_id/name/area, start/end_time_minutes, service_minutes, need_staff, specified/ng_staff_ids, sex_limit, continuation_pref, time_type, earliest/latest_time, prev_staff, change_type, notes

**events / staff_changes / weekly_patterns / confirmed_history / patient_changes / special_week / mentor_pairs**: 詳細省略

### POST /api/allocate レスポンス
```javascript
{
  success: true,
  result: {
    assignment_results: [...],   // visit_id, date, staff_id, patient_id, time, movement_km, notes
    unassigned: [...],           // date_str, pid, pname, need_staff, slot, reason
    summary: { ... }
  }
}
```

### 戻り値処理（4ステップ）
1. 割当結果シート書込 (Line 104, 17列)
2. 割当不可シート書込 (Line 108, 7列)
3. 週ビュー更新 (Line 138)
4. ルートサマリ生成 (Line 150)

---

## 6. 「保護対象」と「再構築候補」の境界

### 完全保護
1. KaipokeDiff.js: 差分検出ロジック（VPS依存）
2. PythonAllocateBridge.js: Python割当IF（10種データ固定）
3. KaipokeExport.js 18列CSV: カイポケ固有フォーマット

### 部分流用
1. UnifiedCode.js のシート操作ユーティリティ
2. スプレッドシート列定義スキーマ
3. 時刻変換ユーティリティ
4. 正規化・バリデーション

### 廃棄候補
1. KaipokeRpa.js: VPS API直呼び出し → CareLink新エンドポイント
2. InteractiveWeekViewServer.js: GAS HTML Service UI → React/Vue 移行
3. AuditExpected.js, AuditJudge.js: 監査ロジック → ビジネスロジック層へ統合

---

## 7. CareLink 再構築への影響・移行論点

### 移行経路

**シナリオA（推奨・段階的）**:
```
[Sheets] → CSV エクスポート → Bulk Import → [CareLink DB]
                                    ↑↓ 同期
                              [Sheets] (read-only)
```

**シナリオB（並行運用）**:
```
[CareLink] ← UI入力 → スケジューリング → CSV出力
[Sheets] (キャッシュ) → 差分確認 → [VPS] カイポケ自動入力
```

### データ活用方法 4案比較

| 方式 | 工数 | 速度 | 監視性 | 推奨 |
|---|---|---|---|---|
| Sheets→新DB Sync | ★★★ | 遅 | 高 | ユーザー確認多い場合 |
| Sheets→CSV→Bulk Import | ★★ | 中 | 中 | 月次定期同期 |
| REST API Bridge | ★★★★ | リアル | 低 | マイクロサービス |
| Sheets Link (read-only) | ★ | 中 | 中 | 過渡期参照 |

### 推奨フェーズ
1. Phase 1（初期）: Sheets Link 読専で参照
2. Phase 2（3ヶ月）: CSV同期で患者/スタッフ取込
3. Phase 3（6ヶ月）: 双方向同期実装
4. Phase 4（12ヶ月）: 完全オフボード

---

## 付録: コード参照ガイド

| 質問 | ファイル:行 |
|---|---|
| カイポケCSV取込仕様 | KaipokeImport.js:14-38 |
| 差分判定ルール | KaipokeDiff.js:17-24 |
| スプレッドシート列定義 | UnifiedCode.js:13-48 |
| Python API ペイロード | PythonAllocateBridge.js:64-76 |
| 割当結果シートヘッダ | PythonAllocateBridge.js:902 |
| VPS API一覧 | KaipokeRpa.js:6-1208 |
| 時刻正規化 | PythonAllocateBridge.js:972-986 |
| イベント処理 | KaipokeExport.js:188-237 |
| スタッフ同行制約 | PythonAllocateBridge.js:833-880 |
