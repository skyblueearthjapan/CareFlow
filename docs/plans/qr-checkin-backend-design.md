# QR訪問チェックイン — Phase 1 バックエンド設計（レビュー反映 v2）

実装前の詳細設計（DBスキーマ／API契約／判定ロジック／エッジケース／テスト）。既存コードの作法に準拠。上位計画は `qr-checkin-implementation-plan.md`、確定仕様は `docs/mockups/qr-checkin/README.md`。

> **2026-06-30 architect + critic 独立レビュー反映済み**。下記「レビュー反映」の決定を本文に織り込み済み。

## レビュー反映（必須修正と決定）
| # | 指摘（重大度） | 決定 |
|---|---|---|
| R1 | **本番モバイルが壊れる契約齟齬（Critical）**：`qr_token` 必須だと既存 `me.ts` の `{lat,lng,at}` が422で弾かれ、404フォールバックも効かず本番チェックインが停止 | **`qr_token` は任意**。無し＝`checkin_source='manual'`で `visit.patient_id` を直接使用（QR照合スキップ）。既存ペイロード `at` は `device_time` のエイリアスとして受理。→ Phase 1 投入で既存挙動を壊さない |
| R2 | **監査テーブルのCASCADE（Major）**：`visit_checkins.visit_id ON DELETE CASCADE` は監査証跡を黙って消す | **`ON DELETE RESTRICT`**（同表 `patient_id` と一致）。visit はソフト削除のみの既存方針とも整合 |
| R3 | **「当日」判定のTZ未定義（High）**：`visit_date`/`start_time` は naive、`scanned_at` は timestamptz | 全比較は **JST(Asia/Tokyo)**。judge で `scanned_at`→JST 変換後に `visit_date` と比較 |
| R4 | **可視性フィルタは当日/削除/取消を見ない（High）** | judge で明示ガード追加：`visit_date==今日(JST)`・`deleted_at IS NULL`・`status != 'cancelled'` ＋ `_staff_visibility_filter` |
| R5 | **型不整合（High）**：計画書は double、設計は Numeric | **Numeric に統一**（patient.py 準拠）。計画書側も修正 |
| R6 | **Haversineはkm返し（High）** | 共通化先 `app/utils/geo.py` に集約し、distance_m は `haversine_km()*1000` |
| R7 | **manualフローAPI未定義（M）／CheckinRead未定義（M）** | §2にmanual分岐を明記、§3に `CheckinRead` と返却形を定義（MyVisitに任意 `latest_checkin` を加える非破壊拡張） |
| R8 | 部分UNIQUEはdialect両系統（High）／`review_m>=match_m` CHECK（M）／index・命名・snapshotスキーマ（Low） | 本文に反映（下記） |
| R9 | **スコープ曖昧（QR発行API/設定API）** | Phase1=checkin/checkout/no-show＋visit_checkins＋checkin_settings**テーブル**＋judge＋patientsのqr列(＋遅延発行ヘルパ)。**QR発行/印刷API・UIはPhase5**、**checkin-settings GET/PUT APIはPhase4**（judgeはPhase1ではテーブル/既定値を直接読む） |
| 据置 | 2名体制(visit_group)のno_show集計、オフライン時刻逆転、KPI影響、GPS保持方針(APPI) | Phase 2/3 の留意点として明記（後述） |

## 既存コードの前提（確認済み）
- `Patient`（`app/models/patient.py`）: `code`(unique)/`lat`,`lng`(Numeric(10,7))/`primary_office_id`/`status`('active')/`address`。
- 設定シングルトン前例 = `SchedulingSettings`（`is_singleton`+部分UNIQUE、nullable列=既定fallback、CHECK、dialect変種）。
- `Visit`: `patient_id`/`primary_staff_id`/`visit_date`(naive Date)/`start_time`,`end_time`(naive Time)/`status`(planned/in_progress/completed/cancelled)/`deleted_at`/`source`。
- `User.staff_id`（FK staff、nullable）。`AuditLog` の actor FK は `ON DELETE SET NULL`。
- visits API: `CurrentActiveUser/DbDep/require_role`、`_staff_visibility_filter`（**担当のみ判定。当日/削除/取消は見ない**）。
- JSONB×SQLite: `JSONBish = JSONB().with_variant(JSON(),'sqlite')`。Haversineは km 返しが3箇所（`allocation/utils.py` 他）。

---

## 1. DBマイグレーション 0041（down_revision=0040・単一head・downgrade定義）

### 1-A. `patients` 列追加（QRトークン）
| 列 | 型 | 備考 |
|---|---|---|
| `qr_token` | String(32) nullable | 乱数 `secrets.token_urlsafe(16)`。NULL可（未発行） |
| `qr_version` | Integer not null default 1 (server_default '1') | 再発行カウンタ |
- 部分UNIQUE `uq_patients_qr_token`：`postgresql_where=text("qr_token IS NOT NULL")` **かつ** `sqlite_where=text("qr_token IS NOT NULL")`（式は両dialect同一）。

### 1-B. 新テーブル `visit_checkins`（append-only 監査ログ）
model: `app/models/visit_checkin.py`（`TimestampMixin`）。
| 列 | 型 | 説明 |
|---|---|---|
| `id` | UUID PK | |
| `visit_id` | FK visits(id) **ON DELETE RESTRICT**, not null | 監査証跡を守る（R2） |
| `patient_id` | FK patients(id) ON DELETE RESTRICT, not null | QRが指す患者 |
| `staff_id` | FK staff(id) ON DELETE SET NULL, nullable | 打刻者(User.staff_id) |
| `kind` | String(12) not null | 'arrival'｜'departure'｜'no_show' |
| `scanned_at` | timestamptz not null | サーバ受信時刻（JST換算で比較） |
| `device_time` | timestamptz nullable | 端末時刻（既存 `at` を写像／逆転時の滞在計算に使用） |
| `lat`,`lng` | Numeric(10,7) nullable | GPS |
| `accuracy_m` | Numeric(7,1) nullable | GPS精度 |
| `distance_m` | Numeric(9,1) nullable | サーバ計算（`haversine_km()*1000`） |
| `match_status` | String(12) not null | 'match'｜'review'｜'mismatch'｜'no_gps'（**位置のみ**） |
| `threshold_snapshot` | JSONBish not null | `{"v":1,"match_m":100,"review_m":300,"accuracy_m":50,"no_show_grace_min":20,"late_min":15}` |
| `reason` | Text nullable | 場所違い/未訪問の理由 |
| `is_override` | Boolean not null default false | 不一致でも強行記録した旗（旧名 override_reason を改称） |
| `checkin_source` | String(12) not null default 'qr' | 'qr'｜'manual'（Visit.source と混同回避で改称） |
| `reviewed_by` | FK users(id) ON DELETE SET NULL, nullable | 管理者「確認済み」 |
| `reviewed_at` | timestamptz nullable | |
- Index: `(visit_id, kind, scanned_at DESC)`（最新採用クエリに整合）、`(staff_id, scanned_at)`。`match_status` 単独indexは低選択性のため**作らない**（Phase3でモニター要件確定後に部分/複合indexを検討）。
- CHECK: `kind IN ('arrival','departure','no_show')`、`match_status IN ('match','review','mismatch','no_gps')`。
- **append-only**：再スキャン＝新行。読み取りは kind ごと **`ORDER BY scanned_at DESC LIMIT 1`** で最新採用。

### 1-C. 新テーブル `checkin_settings`（シングルトン・全社一律）
`SchedulingSettings` を雛形。列は nullable（NULL=コード既定）。
| 列 | 既定 | CHECK |
|---|---|---|
| `match_m` | 100 | 10..1000 |
| `review_m` | 300 | 10..2000 |
| `accuracy_m` | 50 | 5..500 |
| `no_show_grace_min` | 20 | 0..240 |
| `late_min` | 15 | 0..240 |
+ 交差CHECK `review_gte_match`：`review_m IS NULL OR match_m IS NULL OR review_m >= match_m`。
+ `is_singleton`(部分UNIQUE 両dialect変種)、`created_at`/`updated_at`。

### 1-D. downgrade
`drop_table(visit_checkins)`・`drop_table(checkin_settings)`・`drop_column(patients.qr_token/qr_version)`。downgrade で checkin データは失われる（適用前 `pg_dump` バックアップ必須＝既存デプロイ手順どおり）。

---

## 2. 判定ロジック（サーバ責務・`app/services/checkin/judge.py`）
1. `staff = user.staff_id`（未紐付け→403）。
2. **QR照合**：`qr_token` 有り→`patients.qr_token==token`(active) 解決、無効→404、`patient.id != visit.patient_id`→409。**`qr_token` 無し→`checkin_source='manual'`、QR照合スキップし `visit.patient_id` を採用（R1/R7）**。
3. **visit ガード（R4）**：`_staff_visibility_filter(staff)` ∧ `visit.visit_date == 今日(JST)` ∧ `deleted_at IS NULL` ∧ `status != 'cancelled'`。否→403/409。
4. `distance_m`：端末GPSと `patient.lat/lng` から `haversine_km()*1000`。どちらか欠→NULL。
5. **`match_status`（位置のみ・精度×距離の全組合せを明示）**：
   - GPS無し or 患者座標無し → `no_gps`
   - `accuracy_m > review_m`（精度が要確認しきい値より粗い＝測位不能）→ `no_gps`
   - 上記以外で距離判定：
     - `distance ≤ match_m` → `accuracy_m > 許容` なら `review`（測位不良）、否なら `match`
     - `match_m < distance ≤ review_m` → `review`
     - `distance > review_m` → `mismatch`（精度が許容内なら明確な遠隔。精度が粗い場合は前段の no_gps で吸収済み）
6. `threshold_snapshot` 保存（version付き）。行を永続化。
7. `visit.status` 更新（v1 Literal）：arrival→`in_progress` / departure→`completed` / no_show→`planned` 据置。
8. 不一致でも記録は止めない（reason 任意、`is_override` で明示）。
9. **退出が到着より先（departure without arrival）**：拒否せず記録し `review` 相当で旗立て（「記録を止めない」方針）。滞在時間は arrival/departure の **device_time** 優先で算出（オフライン同期の scanned_at 逆転対策）。

> **位置のみに限定する理由**：遅延(late)・未訪問(missing) は時間ベースで、Phase 3 モニターが「実効状態 = worst(位置 match_status, 予定+late_min 超過→review, 予定開始+grace 超過＆無スキャン→missing)」を**集計時に合成**。これで過去 checkin の位置判定は不変（遡及なし）、未訪問は現行設定で都度評価という一貫性を確保。合成アルゴリズムは Phase 3 設計で擬似コード化する。

---

## 3. API契約（`app/api/v1/`）
### チェックイン系（staff・自分のvisit）— `visits.py` に追加
- `POST /api/v1/visits/{visit_id}/checkin`
  - body `CheckinCreate`: `{qr_token?:str, lat?:float, lng?:float, accuracy?:float, device_time?:datetime(別名 at 受理), reason?:str, is_override?:bool}`（**全て任意**。R1）
  - 動作: §2 を実行し arrival 行作成、visit→in_progress。
  - **返却**: 既存 `useCheckIn` の `MyVisit` 契約を維持。`MyVisit` に **任意フィールド `latest_checkin: CheckinRead | null` を非破壊追加**（既存呼出は無視可能）。
- `POST /api/v1/visits/{visit_id}/checkout` — departure 行、visit→completed。
- `POST /api/v1/visits/{visit_id}/no-show` — `{reason:str(必須), lat?, lng?}`、no_show 行。

`CheckinRead`（新スキーマ）: `{id, kind, match_status, distance_m, accuracy_m, scanned_at, checkin_source, reason, is_override}`。

### QR発行（admin/manager）— **Phase 5**（本Phaseは patients に列追加＋遅延発行ヘルパのみ）
- `GET /api/v1/patients/{id}/qr`（未発行なら生成）／`POST .../qr/regenerate`（新トークン＋`qr_version+1`、旧失効、**回転を audit_logs に記録**）。

### しきい値（admin/manager）— **Phase 4**（テーブルは本Phase。judgeは Phase1 でテーブル/既定値を直接読む）
- `GET/PUT /api/v1/checkin-settings`（`scheduling_settings.py` 雛形、range CHECK）。

---

## 4. QRトークン発行
- 生成: `secrets.token_urlsafe(16)`。既存患者は**遅延発行**（`GET /qr` 時に NULL なら生成）＋一括スクリプト `scripts/issue_patient_qr.py`。migration はカラム追加のみ（データ生成しない＝軽量・可逆）。
- 失効: regenerate で上書き（旧トークンは保持しない）。**回転イベントは audit_logs に記録**（compromise 対応の追跡）。

---

## 5. エッジケース
| ケース | 挙動 |
|---|---|
| QR無し/読めない（手入力） | `checkin_source='manual'`、QR照合スキップ、`visit.patient_id`採用 |
| 再スキャン(二重) | append。kind最新（scanned_at DESC）採用 |
| GPS拒否/取得失敗・患者未ジオコード | `no_gps`（記録は残す。モニターで要確認） |
| 精度が極端に粗い(>review_m) | `no_gps`（測位不能。距離で断定しない） |
| 別患者のQR | 409 |
| 担当外/当日でない/削除/取消 visit | 403/409 |
| 退出が到着より先 | 記録し review 旗。滞在は device_time 優先 |
| 不一致で理由未入力 | 記録許可（reason任意・モニターで「理由未入力」） |
| オフライン | フロント localStorage 退避(既存)→復帰時同期(Phase 2) |
| User に staff 未紐付け | 403 |

## 6. テスト（pytest・`python -m pytest`）
- 距離境界(99/100/101・299/300/301)、精度×距離の全分岐、`no_gps`、精度>review_m。
- QR: 別患者→409、qr_token無し→manual成立、regenerateで旧無効。
- visit status 遷移、no_show は reason 必須、当日外/取消/削除→拒否、JST境界(深夜)。
- checkin_settings range/`review_gte_match`/シングルトン。既存 visits テスト非回帰。

## 7. マイグレーション/デプロイ注意
- down_revision=0040、`alembic heads` 単一確認、downgrade定義。SQLite互換: JSONBish・部分UNIQUE両dialect・timestamptz。
- migration含むため **backend も build**（alembic bake-in）。適用前 `pg_dump` バックアップ。本番VPS厳守ルール準拠。

## 8. 後続フェーズへの申し送り（レビュー据置事項）
- **Phase 2**: `me.ts` の `CheckInPayload` を `CheckinCreate` 互換へ拡張（qr_token/accuracy 追加、`at`→device_time）。404/422 両方をフォールバック判定に。オフライン同期で scanned_at 逆転時は device_time で滞在算出。retryストーム対策にクライアント debounce。
- **Phase 3 モニター**: 実効状態の合成アルゴリズムを擬似コード化。2名体制(`visit_group_id`)の no_show は group 単位で「一部未実施」を集計。`match_status` 用の部分/複合index をここで確定。
- **Phase 5**: QR発行/印刷API・UI。QR回転の audit。
- **横断**: no_show は `visit.status=planned` 据置のためダッシュボードKPIでは「未訪問」と「進行中/予定」が区別不可（モニターが区別の場）＝既知の制限として明記。v2 Pydantic `VisitStatusV2` は `in_progress` を持たない既存不整合があるため、checkin の status 書込みは v1 Literal を用いる。

### GPS座標の保持方針（APPI・Phase 1 レビュー指摘 M1 受け）
- `visit_checkins.lat/lng/accuracy_m` はスタッフの行動履歴＝個人関連情報。append-only で削除機構が無いため**無期限蓄積になる**。
- **方針（運用開始前に確定）**: 保持上限を定める（暫定 **13ヶ月**）。`distance_m`/`match_status`（判定結果）は監査のため長期保持し、**`lat`/`lng` は保持期間経過後に NULL 化（マスキング）**するパージジョブを Phase 3 以降で実装。
- `CheckinRead` は `lat`/`lng` を返さない（実装確認済み・現状維持）。座標を返す監査用エンドポイントを作る場合は admin 限定＋監査ログ必須。
- レビュー指摘の軽微改善（reason 上限・座標境界・checkin_source CHECK・status退行ガード 等）は Phase 1 修正で対応済み。
