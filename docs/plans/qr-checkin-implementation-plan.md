# QR訪問チェックイン — 実装計画 / 設計検討

患者宅の固定QRをスタッフがスマホで読み、GPS付きで到着/退出を記録 → PCの「訪問モニター」で管理者が予定との乖離（未訪問/場所違い/遅延）をリアルタイム把握する機能の実装計画。

- UIモック / 確定事項: `docs/mockups/qr-checkin/`（`mobile-proto.html` / `pc-proto.html` / `README.md`）。
- 既存3UI構成: PC `(app)` / 現場ボード `(field)` / モバイル `(mobile)`。本機能はモバイル `(mobile)` のチェックインと、PC `(app)` の新ページ「訪問モニター」に載る。

## 確定済みの仕様（2026-06-30 合意）
| 項目 | 決定 |
|---|---|
| QR | 患者宅に固定QR。到着・退出とも同じQRを読む |
| 記録 | 到着＋退出の両方（滞在時間も計測） |
| 位置判定 | 距離しきい値で自動。既定 一致≤100m / 要確認≤300m / 不一致>300m。GPS精度±50m超は「測位不良→要確認」 |
| 未訪問 | 時間ベース。予定開始＋20分でスキャン無し→未訪問 |
| しきい値 | 全社一律。判定は記録時に確定保存し遡及変更しない |
| PC可視化 | タイムライン（ガント）専用 + 行クリックでLeaflet実地図 |
| CSV | 当面スコープ外 |

---

## アーキテクチャ概要
- **記録モデル**: 1訪問（`visits` 行）に対し到着/退出/未訪問の `visit_checkins` 行を紐付け。距離・判定はサーバ確定。
- **判定はサーバ責務**: クライアントの lat/lng を信用せず、患者登録座標との距離をサーバが Haversine 計算し、その時点のしきい値で `match_status` を確定・保存（使用しきい値もスナップショット）。
- **未訪問は読み取り時に導出**: モニター集計時に「予定開始＋猶予を過ぎてスキャン無し」を未訪問として算出（記録が無いものなのでテーブル行は基本作らない。スタッフが理由入力した場合のみ no_show 行を作る）。
- **しきい値**: `scheduling_settings`（既存・全社単一行設定）と同じ前例で `checkin_settings` を新設。

---

## Phase 1 — バックエンド中核（記録・判定）
**目的**: checkin/checkout API を実装し、距離・判定・保存まで通す。

### DBマイグレーション 0041（次番号。単一head厳守）
新テーブル `visit_checkins`（model: `backend/app/models/visit_checkin.py`、`TimestampMixin` 継承）:
> ⚠️ 詳細・確定版は `qr-checkin-backend-design.md`（architect+criticレビュー反映 v2）を正とする。以下は概要。
```
id              UUID PK
visit_id        FK visits(id) ON DELETE RESTRICT        -- 監査証跡を守る（CASCADE禁止）
patient_id      FK patients(id) ON DELETE RESTRICT       -- QRが指す患者（検証用）
staff_id        FK staff(id) ON DELETE SET NULL          -- 打刻者（User.staff_id）
kind            String(12)  'arrival'|'departure'|'no_show'
scanned_at      timestamptz   -- サーバ受信時刻（JST換算で比較）
device_time     timestamptz NULL   -- 端末時刻（at の写像／逆転時の滞在計算）
lat, lng        Numeric(10,7) NULL   -- GPS（patient.py と同型）
accuracy_m      Numeric(7,1) NULL
distance_m      Numeric(9,1) NULL    -- サーバ計算 haversine_km()*1000
match_status    String(12)  'match'|'review'|'mismatch'|'no_gps'   -- 位置のみ
threshold_snapshot  JSONBish   -- {"v":1, match_m,...}
reason          Text NULL
is_override     Boolean default false   -- 不一致でも強行記録（旧 override_reason）
checkin_source  String(12) default 'qr'  -- 'qr'|'manual'（Visit.source と混同回避）
reviewed_by     FK users SET NULL / reviewed_at timestamptz NULL
```
インデックス: `(visit_id, kind, scanned_at DESC)`、`(staff_id, scanned_at)`。  
新テーブル `checkin_settings`（単一行、`scheduling_settings` を雛形）: `match_m=100, review_m=300, accuracy_m=50, no_show_grace_min=20, late_min=15`（＋ CHECK `review_m>=match_m`）。
- **API互換（必須）**: `qr_token` は**任意**。無し＝manual扱いで既存 `me.ts {lat,lng,at}` を壊さない（Phase 1 投入で本番チェックイン停止を回避）。

### モデル / スキーマ / API
- `app/models/visit_checkin.py`, `app/models/checkin_settings.py`（`scheduling_settings.py` を踏襲）。
- `app/schemas/visit_checkin.py`: `CheckinCreate{lat,lng,accuracy,device_time,qr_token}`, `CheckinRead`。
- `app/api/v1/visits.py` に実装（フロントが既に叩いている既存URL）:
  - `POST /api/v1/visits/{id}/checkin` / `/checkout`：①セッションの staff を取得 ②`qr_token`→patient を解決し `visit.patient_id` と一致検証 ③`patient.lat/lng` と Haversine で `distance_m` ④`checkin_settings` で `match_status` 確定（精度許容も適用）⑤行を保存し `visit.status` を in_progress/completed に更新 ⑥`CheckinRead` 返却。
  - `POST /api/v1/visits/{id}/no-show`：理由付きで未訪問記録（GPS任意）。
- 距離ユーティリティ: スケジュール距離表示で使っている Haversine を共通化して再利用。
- RBAC: checkin/out/no-show は **staff（自分のvisitのみ）**。既存の auth 依存を流用。
- **テスト**(pytest): 一致/要確認/不一致の境界、精度許容、QR不一致拒否、二重checkin、status遷移。

**完了条件**: モバイルの既存 `useCheckIn/useCheckOut`（`me.ts`）が 404 フォールバックなしで成功。

---

## Phase 2 — モバイル QR/GPS UI
**目的**: `(mobile)` でQR読取→GPS→到着/退出/未訪問を記録（`mobile-proto.html` が design reference）。

- **QRライブラリ追加**: `@zxing/browser` か `html5-qrcode`（カメラ＋デコード）。HTTPS必須・iOS Safariはユーザー操作起点（既存設計メモ §10-12）。
- **GPS**: `navigator.geolocation.getCurrentPosition({enableHighAccuracy:true})` で lat/lng/accuracy。
- **画面**: 既存 `frontend/app/(mobile)/m/today/[visitId]/page.tsx` を置き換え/拡張。スキャン画面→到着確認（地図サムネ＋距離判定）→訪問中（タイマー＋メモ＋写真=既存`visit-photos`）→退出確認→完了。
- **理由入力**: 場所違い（不一致）は理由＋強行記録、未訪問は「訪問できなかった」→理由（クイック選択チップ）→ `no-show` API。
- **オフライン耐性**: 既存の localStorage フォールバック（`lib/checkin-storage`）を本実装後も圏外保険として残す。
- **データ層**: `me.ts` の `CheckInPayload` に `accuracy`/`qr_token` を追加、`useNoShow` 追加。
- **テスト**(vitest): スキャン→確認→記録のフロー、不一致時の理由必須、no_show。

---

## Phase 3 — PC「訪問モニター」（タイムライン＋地図）
**目的**: 管理者が予定vs実績を一覧（`pc-proto.html` が design reference）。

- **集計API**: `app/api/v1/visit_monitor.py` 新設 `GET /api/v1/monitor?date=&office_id=`：その日の visits を checkins と突き合わせ、各訪問の {予定, 到着, 退出, 滞在, distance_m, match_status, reason, 次訪問までの距離, 未訪問判定} を返す。未訪問は猶予超過＆スキャン無しで算出。RBAC=admin/manager。
- **ページ**: `frontend/app/(app)/monitor/page.tsx`、`components/Sidebar.tsx` に「📍 訪問モニター」を追加（admin/manager）。
- **UI コンポーネント**（`components/monitor/`）: タイムライン（スタッフ×時刻ガント、予定バーに患者名・実績バーに判定色・行番号・選択強調・他行ディム）、要対応アラートトレイ（未訪問→場所違い→要確認の優先順＋全件ドロップダウン）、詳細パネル、地図。
- **地図**: `react-leaflet`+OSM を新規依存追加。行クリック→コース目的地ピン＋区間距離、場所違いは自宅↔実GPSを赤破線＋距離＋しきい値円＋近隣患者宅「〇〇様宅？」候補（`patients` の lat/lng 近傍探索＝新API or 既存patients検索を流用）。地図ホイールズーム無効でパネルスクロール優先。
- **即連絡**: 未訪問の患者/スタッフ電話発信（`tel:`）。
- **リアルタイム**: 60秒ポーリング（既存notifications同様）。
- **テスト**(vitest): タイムライン描画、アラート優先順、未訪問即連絡導線。

---

## Phase 4 — しきい値設定UI（全社一律）
- `app/api/v1/checkin_settings.py`：`GET/PUT /api/v1/checkin-settings`（admin/manager、`scheduling_settings` API を雛形）。
- PC: `pc-proto.html` の⚙モーダルを実装（スライダー＋プレビュー再判定）。保存で `checkin_settings` 更新。
- **判定の一貫性**: 過去の checkin は `threshold_snapshot` で当時の判定を保持（遡及しない）。モニターの未訪問判定だけ現行設定で評価。

---

## Phase 5 — QR発行・印刷・通知・仕上げ

### QR発行（確定：DBランダムトークン）
- **データ**: `patients` に `qr_token`（推測不能な乱数・ユニーク）＋ `qr_version`（再発行カウンタ）を追加（migration）。新規患者作成時に自動採番と同様に裏で生成。
- **QRの中身**: `https://<app>/q/{qr_token}`（URL形式。アプリ内スキャナはトークンを抽出、標準カメラでもPWAへディープリンク）。**患者の識別のみ**を入れ、訪問IDや日付は入れない（固定掲示で全訪問に使い回す）。
- **照合**: checkin時に `qr_token → 患者` をサーバがO(1)照合し `visit.patient_id` と一致検証。**再発行＝新トークンで旧QRは即失効**（`qr_version` を上げ旧トークン破棄）。
- **API**: `GET /api/v1/patients/{id}/qr`（トークン取得）、`POST /api/v1/patients/{id}/qr/regenerate`（再発行、admin/manager）。

### QR印刷（確定：1名1枚・二つ折り。個別＆一括の両入口）
- **2つの入口（同じ印刷ビューを共有）**:
  - **個別**: 患者詳細 `(app)/patients/[id]` の「QRコード生成」→ **その患者1名（A4 1枚）**。再発行もここから（新トークン＝旧QR失効）。
  - **一括**: 患者一覧 `(app)/patients` の「QRコード生成」→ 拠点絞り込み＋患者選択で **全員分を各A4 1枚ずつ**。
- **体裁**: A4を中央の折り線で半分に折る二つ折り。上半分=掲示面（患者名・患者コード・拠点・大QR・案内）、下半分=説明面（ご家族向け説明・掲示注意・事業所連絡先・発行日）。患者宅へ折ってお渡し／玄関に掲示できる。
- **実装**: フロントで `qrcode` ライブラリ描画、CSS `@media print`（@page A4・改ページ `page-break-after`）で `window.print()`。患者1名につき1ページ。

### その他仕上げ
- **通知**: 未訪問・場所違い発生で `notifications` 生成（PCヘッダー通知＋モバイルへ催促）。
- **退出押し忘れ救済**: 営業終了で自動退出 or 管理者手動補正。
- **監査**: モニター閲覧・確認済み操作を `audit_logs` に記録。

---

## 主要な設計上の論点（要継続検討）
1. ~~QRトークン方式~~ **決定：DBランダムトークン**（`patients.qr_token`＋`qr_version`、URL形式、再発行で旧QR即失効）。偽造は乱数で実質不可、コピー濫用はGPS第2要素でカバー。
2. **退出スキャン必須化**: 押し忘れ対策（自動退出のしきい値、滞在未確定の扱い）。
3. **近隣患者宅候補の範囲**: GPS近傍 何m以内・何件まで（PHI配慮。他患者宅を出す妥当性も要検討）。
4. **GPS精度の扱い**: 「測位不良」を不一致と別扱いにするUIと、精度悪い記録の再取得促し。
5. **権限**: モニター=admin/manager、スタッフは自分の記録のみ。理由・座標の閲覧範囲。

## テスト/デプロイ方針
- backend: `cd backend && python -m pytest <files> -q`（uv run不可）。frontend: `pnpm vitest run <files>` / `pnpm tsc --noEmit` / `pnpm lint`。
- 大型実装は実装＋code-reviewerを別パスで（自己approveしない）。
- migration を含む Phase は backend も build（alembic bake-in）。単一head確認。デプロイ手順は本番VPS厳守ルールに従う。
