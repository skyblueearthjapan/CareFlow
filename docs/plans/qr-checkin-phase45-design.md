# QR訪問チェックイン — Phase 4 / Phase 5 設計

Phase 1〜3（記録・モバイルUI・PC訪問モニター）は本番デプロイ済み。本書は **Phase 4（しきい値設定UI）** の詳細化と **Phase 5（QR発行/印刷・通知・仕上げ）** の設計検討。上位は `qr-checkin-implementation-plan.md`、backend確定設計は `qr-checkin-backend-design.md`。

## 既存資産（確認済み・本設計の前提）
- `checkin_settings` テーブル = **Phase 1で作成済**（シングルトン・range/`review_gte_match` CHECK・nullable列=コード既定fallback）。本番では**0行（未シード）**で、`load_thresholds()` が既定(match100/review300/acc50/grace20/late15)を返す。
- `visit_checkins.reviewed_by`/`reviewed_at` 列 = **作成済**（「確認済み」用の箱は用意済、書込API/UIは未実装）。
- `patients.qr_token`/`qr_version` 列 = 作成済（発行/印刷UIは未実装）。
- `notifications` テーブル（per-user inbox: user_id/type/title/body/read_at）= 既存。モバイルのベル・PCの通知ボタンで表示。**producer（イベント→行生成）は未配線**（W6予定として保留中）。
- **電話列は存在しない**（Patient/Staff とも）。モニターの「📞連絡」は現状ダミー。
- 設定APIの前例 = `app/api/v1/scheduling_settings.py`（GET/PUT・admin/manager）。設定ページの前例 = `app/(app)/settings/scheduling/page.tsx`。

---

# Phase 4 — しきい値設定UI（全社一律）

> 設計はほぼ揃っている（モック=pc-proto.html の⚙モーダル、テーブル=実装済、API仕様=設計書済）。残りは API実装＋ページ配線＋**クライアントプレビューのしきい値同期**＋シード。

## 4-1. API（`app/api/v1/checkin_settings.py`）
- `GET /api/v1/checkin-settings`（admin/manager）：`checkin_settings` シングルトン行を返す。無ければ**コード既定値を返す**（`load_thresholds()` を流用）。
- `PUT /api/v1/checkin-settings`（admin/manager）：5値（match_m/review_m/accuracy_m/no_show_grace_min/late_min）を upsert。range/`review_gte_match` は DB CHECK＋Pydantic で二重バリデーション。**シングルトン行を upsert**（無ければ insert・is_singleton=true）。
- `scheduling_settings.py` を雛形に。監査ログ対象。

## 4-2. PCページ（設定画面）
- 配置：`settings/scheduling` と同様に **設定系**。`pc-proto.html` の⚙モーダルをそのまま、または `/settings/checkin` 等の専用ページに。admin/manager のみ（非該当は `/dashboard` リダイレクト）。
- 訪問モニターの「⚙ しきい値設定」ボタンからも開ける（pc-proto と一致）。
- スライダー＋**プレビュー再判定**（pc-proto と同等。`classifyWith` 相当をクライアントで持つ）。保存で PUT → モニター再フェッチで反映。

## 4-3. ★クライアントプレビューのしきい値同期（最終レビュー指摘の解消）
- **問題**：モバイルの到着確認プレビューが `MATCH_M=100/REVIEW_M=300` を**ハードコード**。Phase 4 で管理者が変更すると、プレビュー（概算）と実記録（サーバ判定）が乖離し現場が混乱する。
- **方針**：`VisitRead`（または `MyVisit`）に**現行しきい値を載せる**か、軽量 `GET /api/v1/checkin-settings/public`（staff も読める最小：match_m/review_m/accuracy_m のみ）を用意し、モバイルのプレビューがそれを使う。ラベル「要確認（100〜300m）」も動的化。
- 代替：プレビューを廃し「記録後にサーバ判定を表示」のみにする（実装は単純だが事前の目安が無くなる）。→ **しきい値を載せて動的プレビュー**を推奨。

## 4-4. シングルトン行のシード
- 現状0行で既定fallback。Phase 4 で**初回PUT時に行を作る**ので必須ではないが、GET の一貫性のため migration データ手順 or アプリ起動時シードで1行入れておくと明快。→ **初回PUTで作成（lazy）**で十分。要否は判断点。

---

# Phase 5 — QR発行/印刷・通知・確認済み・仕上げ

## 5-1. QR発行/印刷
- **API**（admin/manager）：`GET /api/v1/patients/{id}/qr`（未発行なら遅延生成し `{token,url,version}` 返却）、`POST /api/v1/patients/{id}/qr/regenerate`（新トークン＋`qr_version+1`、旧失効、**audit_logs に回転記録**）。
- **印刷UI**：`qr-print.html` モックを本実装。患者詳細 `(app)/patients/[id]` に「QRコード生成/印刷」（個別1名）、患者一覧 `(app)/patients` に「QR一括印刷」（拠点絞り込み＋選択で各A4 1枚）。`qrcode` ライブラリ＋`@media print`（A4・中央折り線・二つ折り）。
- **一括発行スクリプト**：`scripts/issue_patient_qr.py`（既存patientへ一括採番、CLI）。

## 5-2. 通知（未訪問・場所違い）
- **対象**：その拠点の admin/manager（または全manager）へ `notifications` 行を生成。
- **producer の置き所（要設計判断）**：
  - **(a) 場所違い(mismatch)**：イベント駆動。checkin 記録時に `match_status==mismatch` なら通知生成（judge/API層でフック）。即時。
  - **(b) 未訪問(missing)**：時間ベースで「イベントが無い」ため、**定期ジョブ**が必要。選択肢：①cron的バックグラウンド（APScheduler等の新規導入）②モニター集計時に「未通知のmissing」を検出して通知生成（管理者が見た時に確定／取りこぼしは画面依存）③軽量な「毎分/5分」タスク。→ **最小は ②（モニター読み込み時に未通知missingを通知化）**だが、誰も見ていないと飛ばない。確実性重視なら ①（スケジューラ導入）。**要判断**。
- **重複防止**：`notifications` に `(type, visit_id)` 相当の冪等キーが無いため、producer 側で「同一visitの同種通知は1回」をどう担保するか設計が要る（補助テーブル or type+bodyにvisit_id埋め込み＋存在チェック）。
- **通知タイプ**：`checkin_mismatch` / `checkin_missing`。frontend のベル/通知ボタンがアイコン選択に使用。

## 5-3. 「確認済みにする」（reviewed_by 書込）
- **API**：`POST /api/v1/visits/{visit_id}/checkins/{checkin_id}/review`（または最新checkinを対象に `POST .../review`）で `reviewed_by=current_user`/`reviewed_at=now` を設定。admin/manager。audit対象。
- **UI**：訪問モニターの詳細パネル/アラートカードに「✓ 確認済みにする」。確認済みは**アラートトレイから外す/淡色化**（要対応の山が消化できる）。pc-proto の該当ボタンを実装。
- **集計影響**：モニターの alert 合成で「reviewed 済みは要対応から除外」するか、別バッジで残すかを決める（推奨：要対応トレイからは外し、タイムラインには小さな「確認済」印を残す）。

## 5-4. 電話発信（tel:）
- **要スキーマ追加**：`patients` に `phone`（利用者/家族連絡先）、`staff` に `phone`（スタッフ連絡先）を追加（migration）。任意・String。
- これにより モニターの「📞連絡」を実 `tel:` リンク化（patient/staff 双方）。モバイルにも患者連絡先表示。
- **要判断**：電話列の導入是非・PHI取扱（連絡先は個人情報）・マスタ編集UIへの追加。

## 5-5. QRトークン失効
- regenerate が新トークン上書き＝旧失効（5-1で実装）。**追加の失効API**（無効化のみ）は要否判断。回転は audit に残す。

## 5-6. GPS座標の保持パージ（APPI）
- `visit_checkins.lat/lng` を保持上限（暫定13ヶ月）超で **NULL化**するジョブ。`distance_m`/`match_status` は監査のため残す。
- 5-2(b) のスケジューラを導入するなら同じ仕組みに相乗り。日次。**要判断**（保持期間の確定）。

---

## 主要な設計判断（レビュー＆ユーザー確認したい点）
1. **未訪問通知の確実性**：スケジューラ新規導入（確実・即時）か、モニター読込トリガ（軽量・画面依存）か。
2. **電話列の追加**：Patient/Staff に phone を持たせ tel: を有効化するか（PHIマスタ拡張）。
3. **確認済みの扱い**：要対応トレイから外す範囲（mismatch/missing/review すべてか、特定だけか）。
4. **しきい値プレビュー同期**：VisitRead 同梱 or 軽量 public 設定API、どちらでスタッフへ配るか。
5. **通知の重複防止**：補助キー設計（visit_id × type の一意化）。
6. **GPS保持期間**：13ヶ月で確定か。

## スコープ順序（提案）
Phase 4（小・設計ほぼ完成）→ Phase 5-1/5-3（QR印刷・確認済み＝即効果）→ 5-2通知 → 5-4電話 → 5-6パージ。

---

# レビュー反映（architect + critic 設計レビュー / 2026-06-30）

両レビューの結論: **Phase 4 = GO**（軽微追加あり）／**Phase 5 = 要設計修正**（下記を解決してから実装）。技術的指摘は本書に反映済み。★=ユーザー判断待ち。

## Phase 4 への追加（実装前に確定）
- **6つ目の設定 `max_inprogress_min`（既定240分）を追加**: 退出忘れ閾値が `monitor.py`/`constants.ts` にハードコードされ「Phase4で設定化」コメント済。`checkin_settings` に列追加＋API/UIの6項目目に。
- **しきい値プレビュー同期 = public設定API**（VisitRead同梱は500件に同値反復で却下）: `GET /api/v1/checkin-settings/public`（staff可・**match_m/review_m/accuracy_m のみ**。time系は出さない）。モバイルの `MATCH_M/REVIEW_M` ハードコードと**ラベル文言「要確認（100〜300m）」も動的化**。
- **シングルトンはlazy**（初回PUTで作成・migrationシード不要）。GETは0行時コード既定を返す（scheduling_settings踏襲）。
- **時間判定の即時フリップに注意**: `no_show_grace/late` 変更は monitor が現行値で都度評価するため、日中変更で missing↔awaiting がフリップしうる。**「変更は翌日/新規visitから」運用** or 当日分はスナップショット。最低でも文書化＋UIに注意書き。

## Phase 5 の必須設計修正
- **通知の冪等キー（前提）**: `notifications` に `reference_type`(String)＋`reference_id`(UUID) を追加し、部分UNIQUE `(user_id, reference_type, reference_id) WHERE reference_id IS NOT NULL`。producerは `reference_id=visit_id` で**冪等生成**。本文5-2の「body埋め込み」案は却下。
- **通知トリガ = VPS cron＋管理API に確定**（APScheduler不採用＝単一uvicornで多重発火/将来multi-worker危険、既存 `lat_lng_audit_cron` の実績パターン）。`POST /api/v1/admin/checkin/check-missing`（5分毎・advisory lock・冪等）。**「モニター読込トリガ」案は安全機能として却下**（土日に誰も見ないと飛ばない）。mismatchはcheckin記録時にイベント駆動で即時生成。
- **「確認済み」は per-visit + per-checkin の二層**（critic C1/C2）:
  - 未訪問(missing)は紐づくcheckinが無いため**visit単位のreview**が必須 → `POST /api/v1/visits/{visit_id}/review`（visit_checkins とは別に visit 単位の reviewed を持つか、合成的に扱う）。checkin単位は `POST /api/v1/checkins/{checkin_id}/review`（最新ではなく**checkin_id指定**）。
  - **モニター拡張が必要**: `MonitorCheckin` に reviewed_by/at、`MonitorVisit` に reviewed フラグ、`compute_alert` を reviewed で抑制、`build_monitor` で配線、frontend アラートトレイのフィルタ。→ Phase3契約に触る非自明な作業。
  - **要対応トレイからは外す＋タイムラインに「確認済」印＋取り消し(undo)導線＋ `reviewed_comment`（確認理由）**。誤確認の復帰を可能に。
- **QR再発行の旧ステッカー事故**: 旧トークンを `revoked` として記録（履歴列 or 小テーブル）。judge は無効トークンが「ローテ済」なら **410 Gone「このQRは更新されました。{患者}の新しいQRをご利用ください」**。印刷シートに `qr_version` を印字。物理貼替の追跡（deployed日）。
- **GPSパージは `accuracy_m` も NULL化対象**（lat/lng/accuracy。distance_m/match_status は残す）。

## ユーザー判断（2026-06-30 確定）
1. **通知チャネル = アプリ内通知のみ**（LINE WORKS連携はしない）。5-2は既存 notifications＋ベル/通知ボタンで実装。冪等キー(reference_id)＋cronトリガは前提のまま。
2. **連絡手段 = 詳細表示のみ（現状維持）**。→ **5-4(電話列追加・tel:・LINE WORKS連絡)は取りやめ**。モニターの「連絡」は visit選択＋詳細表示まで（既存実装のまま）。Patient/Staff への phone 列追加は**しない**。
3. **GPS保持期間 = 2年**（法定最低に整合）。lat/lng/accuracy を2年でNULL化、distance_m/match_status は残す。派生値の証拠十分性は将来の法務確認事項として残す。
4. **確認済み**: 要対応トレイから外す＋タイムラインに「確認済」印＋undo＋comment（前述の二層review設計どおり）。対象は missing/mismatch/review すべて。
5. **実装順 = Phase 5-1（QR印刷）を最優先**。次いで 5-3(確認済み)、Phase 4(しきい値)、5-2(通知)、5-6(GPSパージ2年)。
6. しきい値スコープ = 当面 全社一律（拠点別は将来検討）。
