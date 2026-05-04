# INV-5: データ活用・反映戦略レポート

## 1. 既存スプレッドシートのデータ実体把握

### 1.1 患者マスタの列定義（既存・推定 20列）
| # | 列名 | 摘要 |
|---|---|---|
| 1 | patient_id | PK相当 |
| 2 | 患者名 | フルネーム |
| 3 | エリア | 拠点・地域 |
| 4-5 | 緯度/経度 | Geo自動入力 |
| 6 | サービス時間 | 訪問分 |
| 7 | 週訪問回数 | 1週あたり |
| 8 | 必要スタッフ数 | 1or2 |
| 9 | 性別制限 | 女性のみ等 |
| 10 | 継続希望 | 継続/終了 |
| 11 | 指定スタッフID | カンマ区切り |
| 12 | 指定タイプ | 固定/希望 |
| 13 | NGスタッフID | カンマ区切り |
| 14 | 希望曜日 | 月木金等 |
| 15 | 曜日NG | 避ける曜日 |
| 16 | 時間タイプ | 固定/柔軟 |
| 17-18 | 希望時間帯（開始/終了） | HH:MM |
| 19 | 曜日優先度 | 低/中/高 |
| 20 | 稼働状況 | 稼働/休止/入院 |

推定: 30患者 × 20列 = 600セル

### 1.2 スタッフマスタの列定義（既存・推定 11列）
| # | 列名 |
|---|---|
| 1 | staff_id |
| 2 | スタッフ名 |
| 3 | 性別 |
| 4-5 | 緯度/経度 |
| 6-7 | シフト開始/終了 |
| 8 | 勤務曜日 |
| 9 | 得意エリア |
| 10 | 最大訪問件数 |
| 11 | 割当量（均等/多め/少なめ） |

推定: 12スタッフ × 11列 = 132セル

### 1.3 CareLink マスタスキーマ（目標DB）

**患者テーブル** 16カラム:
- id, code, name, kana, sex, age, status, insurance
- address, lat, lng, primary_office_id, required_staff_count, sex_restriction
- weekly_pattern (JSONB), special_week (JSONB), note, deleted_at, created_at, updated_at

**スタッフテーブル** 12カラム:
- id, code, name, kana, sex, status, role, primary_office_id
- can_double_team, mentor_id, note, deleted_at

**関連テーブル**: staff_shifts, patient_allowed_offices, staff_secondary_offices

### 1.4 データ移行ギャップ

| 既存 | CareLink | 難度 | 注記 |
|---|---|---|---|
| patient_id | code | 低 | 1:1 |
| 患者名 | name | 低 | 直接 |
| エリア | primary_office_id | 中 | マスタ参照 |
| 緯度経度 | lat/lng | 低 | 直接 |
| 週訪問回数 | weekly_pattern JSONB | 高 | 曜日別パターン化 |
| 必要スタッフ数 | required_staff_count | 低 | 直接 |
| 性別制限 | sex_restriction | 低 | 直接 |
| 指定スタッフID | weekly_pattern内 | 中 | パターン化 |
| 稼働状況 | status | 低 | active/suspended/admitted |
| **(新規) 年齢** | age | 中 | 既存に未記載の場合あり |
| **(新規) 保険種別** | insurance | 中 | medical/care |
| **(新規) フリガナ** | kana | 中 | 新規定義 |

CareLink新規（既存にない）:
- 患者: age, insurance, kana
- スタッフ: role, can_double_team
- 兼務対応: mentor_id, staff_secondary_offices

---

## 2. データ反映方法 4案

### 案A: CSV一回限りエクスポート → CareLink Backend アップロード
- 実装: `/api/v1/import/patients` POST、CSVパーサ + validation + insert
- 工数: 3日（Backend 2 + Frontend 1）
- 利点: GAS既存関数流用、エラー報告堅牢、再インポート対応、監査ログ自動
- 欠点: 一回限り、UI必要、再トライ手作業、移行中の二重登録リスク
- **適用**: Phase 0 → Phase 1 境界

### 案B: スプレッドシート継続リンク（Sheets API）
- 実装: Service Account + gspread + APScheduler 定期同期
- 工数: 5日（Backend 4 + GCP 1）
- Quota: 読込300/分、100/秒（30患者+12スタッフなら余裕）
- 利点: SSOT 維持、リアルタイム/定期同期、既存 GAS 変更最小（read-only）
- 欠点: Backend 複雑化、quota 管理、ネット遅延、列削除/リネームに脆弱
- 整合性: 同期中フラグ or `updated_at` 比較（楽観ロック）
- **適用**: Phase 1（並行稼働）〜 Phase 2 初期

### 案C: Excel ファイル（xlsx）アップロード
- 実装: openpyxl パース + multipart/form-data
- 工数: 4日（Backend 3 + Frontend 1）
- 利点: 複数シート一括、formula保存、部分更新可
- 欠点: バイナリ重い、merged cells 等で失敗、CSVよりトラブルシュート難、列順固定強制
- **適用**: 一度限りの構造化移行のみ

### 案D: GAS → CareLink API Push（双方向同期）
- 実装: OAuth/API Key + GAS PushToCareLink() 関数
- 工数: 6日（Backend 2 + GAS 2 + テスト 2）
- 利点: GAS段階廃止、操作体験維持、既存ロジック再利用
- 欠点: GAS 依存延命、競合制御複雑、quota 制限、不整合リスク、廃止時期曖昧化
- **適用**: 短期並行稼働（1-2ヶ月）のみ。長期非推奨

---

## 3. データ活用範囲別の推奨

| マスタ | 件数 | 更新頻度 | 推奨案 | 理由 |
|---|---|---|---|---|
| 患者マスタ | 30 | 週1 | A + B | A初期、B定期（病状変化対応） |
| スタッフマスタ | 12 | 月1 | A のみ | 小規模・低頻度、CSV再インポート |
| 拠点マスタ | 2 | 固定 | A のみ | 1回限り |
| 訪問パターン | 30×2-3 | 月1 | B | JSON変換で吸収 |
| 過去訪問履歴 | 1000+ | 読取専用 | A | 月次アーカイブ |
| 個別変更リクエスト | 動的 | 廃止予定 | - | AI入力に置換 |

---

## 4. 移行計画（3フェーズ）

### Phase 0: CareLink稼働前（〜2026/6末）
- 既存仕組み保持
- CareLink Backend 開発（D1: 15人日）
- 準備: Service Account 作成、初期データ整備、API契約

### Phase 1: 並行稼働（2026/7-8月、2ヶ月）
- A: CSV一回移行（3日）→ 2026/7/1 実行 → UI検証
- B準備: Sheets API 統合実装（3日）
- 両者並行: GAS 割当 ＆ CareLink 週ビュー
- CareLink → GAS 逆同期（手動DL）

### Phase 2: 完全移行（2026/9以降）
- B: 毎週月曜 00:00 (Asia/Tokyo) Sheets sync
  - cron: `0 0 * * 1`
  - 差分検出: updated_at 比較
- GAS 読取専用化（訪問履歴・統計のみ）
- CareLink 全権（患者・スタッフ編集はすべて CareLink）
- スプレッドシート: 参照用 dashboards に降格

### 工数まとめ

| フェーズ | 期間 | 開発工数 | 運用負担 |
|---|---|---|---|
| Phase 0 | 〜2026/6末 | Backend 15d | 既存体制 |
| Phase 1 | 7-8月（8週） | CSV 3d + B準備 3d | 両システム監視 2h/日 |
| Phase 2 | 9月以降 | Sheets sync 0.5d/週 | CareLink 監視 1h/日 |

---

## 5. Sheets API 採用時の技術詳細（案B）

### 認証フロー
1. Google Cloud Project で Service Account 作成 → JSON keyfile
2. Backend 環境変数: `GOOGLE_SHEETS_CREDENTIALS=/var/secrets/carelink-sheets-sa.json`
3. Scope: `https://www.googleapis.com/auth/spreadsheets.readonly`
4. スプレッドシート（SS_ID）を Service Account に共有

### Python 実装スニペット
```python
import gspread
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    '/var/secrets/carelink-sheets-sa.json',
    scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
)
client = gspread.authorize(creds)

sheet_file = client.open_by_key(SHEETS_ID)
patient_sheet = sheet_file.worksheet('患者マスタ')
data = patient_sheet.get_all_values()

for row in data[1:]:
    patient_code = row[0]
    existing = db.query(Patient).filter(Patient.code == patient_code).first()
    if not existing:
        # INSERT
    elif existing.updated_at < get_spreadsheet_update_time():
        # UPDATE
```

### Quota & リスク管理
- 読込 100req/sec/user → 30+12件なら 1req=0.5sec → OK
- キャッシング: 最終行 checksum 記録 → 変化なければスキップ
- エラーハンドリング: 429 → exponential backoff（最大1回リトライ）
- 監査ログ: `audit_logs` に `{action: 'sync', inserted: N, updated: M}` 記録

### 整合性保証
1. Sheets 全行読込（checksum）
2. CareLink DB の患者 code リスト取得
3. 新規 INSERT、既存更新 UPDATE、シート削除 → 論理 DELETE
4. updated_at newer チェック
5. 競合時（両者更新）: CareLink 優先（DB マスタ化）
6. sync 完了後、audit_log 記録

---

## 6. 推奨案 + 理由

### 最適戦略: **案A（CSV import） + 案B（Sheets sync）の組み合わせ**

#### 選定理由

**初期移行（A）の利点**:
- 開発工数少（3日）
- バリデーション・エラー報告堅牢
- Phase 0→1 境界での「リセット」効果
- データ一致明確化

**継続同期（B）の利点**:
- Phase 1 中の患者追加・変更で SSOT 維持
- CareLink 新規追加もスプレッドシートで GAS 割当ロジック動作
- GAS → CareLink 完全移行への bridge
- Phase 2 への準備整備

#### 実装スケジュール

| 項目 | 時期 | 工数 | 日数 |
|---|---|---|---|
| A. CSV import エンドポイント | 2026/6 | Backend 2 + Frontend 1 | 3d |
| A. テスト・ドキュメント | 2026/6 | QA 1 | 1d |
| B. Sheets API 統合（dev） | 2026/6 | Backend 2 | 2d |
| B. Service Account + 共有 | 2026/6 | DevOps 0.5 | 0.5d |
| B. 定期ジョブ実装 | 2026/6末 | Backend 1.5 | 1.5d |
| B. 本番テスト | 2026/7 | QA 1 | 1d |
| **合計** | | | **約9日** |

#### 運用フロー

**Phase 1（並行稼働）**:
```
GAS側（既存）                CareLink側（新規）
┌─────────────────┐          ┌──────────────────┐
│ スプレッドシート │          │ Backend DB       │
│ 患者・スタッフ・ │◄────────►│ patients/staff/  │
│ パターン・要望   │  Sync(B) │ weekly_pattern   │
└─────────────────┘          └──────────────────┘
   │
   ▼ Python割当実行
  割当結果シート ──→(手動DL or API) → CareLink週ビュー
```

**Phase 2（完全移行）**:
- GAS 読取専用化（統計のみ）
- スプレッドシート → 参考用 棚卸
- CareLink → 唯一の運用マスタ

#### コスト感

| 案 | 初期実装 | 月次運用 | 6ヶ月累計 |
|---|---|---|---|
| **A+B 推奨** | 9日 | 0.5日/月 | 12日 |
| A のみ | 4日 | 0.25日/月 | 5.5日 |
| B のみ | 5日 | 0.25日/月 | 6.5日 |
| D（GAS Push） | 6日 | 0.25日/月 | 7.5日 |

---

## 7. 非推奨理由

### 案C（Excel）
- バイナリ複雑性 > CSV 恩恵
- 既存はクラウド（Sheets）、Excel定期エクスポート必要
- 列順序・merged cells 脆弱

### 案D（GAS Push）
- GAS quota（6分/実行）制限で長時間化
- 競合検出・解決ロジック複雑
- GAS 廃止時期曖昧 → 技術負債化

---

## 8. 実装チェックリスト

### Phase 0（〜2026/6末）
- [ ] Google Cloud Project + Service Account 作成
- [ ] CareLink Backend に `/api/v1/import/patients`, `/import/staff`
- [ ] CSV パーサ（validation 込み）+ テスト
- [ ] Frontend 管理画面 CSV アップロード UI
- [ ] Sheets API 読込コード + local テスト
- [ ] APScheduler weekly sync ジョブ実装

### Phase 1（2026/7-8月）
- [ ] 本番 CSV import 実行（患者・スタッフ）
- [ ] CareLink で一覧・詳細確認 + 差分検証
- [ ] GAS 割当実行 → CareLink 手動 import
- [ ] 週1回 Sheets sync 動作確認 + alert 構築
- [ ] 業務トレーニング

### Phase 2（2026/9以降）
- [ ] GAS スクリプト読取専用化
- [ ] CareLink を編集マスタ宣言
- [ ] 月次アーカイブ（CSV export）自動化
- [ ] スプレッドシート参照用に降格

---

**最終結論**: **案A＋案B の組み合わせ、初期移行 9日の開発工数で、段階的・堅牢・低リスクな完全移行を実現**。
