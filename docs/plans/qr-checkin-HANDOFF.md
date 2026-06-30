# QR訪問チェックイン — 引き継ぎ書（2026-07-01 時点・全Phase本番稼働）

患者宅の固定QRをスマホで読み→GPS付きで到着/退出/未訪問を記録→PC「訪問モニター」で管理者が予定との乖離（未訪問/場所違い/遅延）を把握する機能。**設計→実装→各フェーズ クロスレビュー→本番デプロイ まで完了し、全機能が本番稼働中。**

- 本番: `https://carelink.kaipoke-api.net`（VPS `root@72.60.211.213` / `/opt/carelink`）。本番ブランチ=`develop`、**本番HEAD=`4f0c939`**。
- 関連ドキュメント: 設計 `qr-checkin-backend-design.md`(Phase1)・`qr-checkin-phase45-design.md`(Phase4/5)、実装計画 `qr-checkin-implementation-plan.md`、UIモック `docs/mockups/qr-checkin/*`、cron手順 `docs/runbook/checkin_jobs_cron.md`。

## 全体構成（3UI + バックエンド）
- **モバイル `(mobile)/m/today/[visitId]`**: スタッフがQR読取(html5-qrcode)＋GPS→確認→単一POSTで到着/退出記録、未訪問理由、オフライン再送キュー。
- **PC「訪問モニター」 `(app)/monitor`**: タイムライン(ガント)＋要対応アラート＋詳細＋Leaflet地図。実効状態を合成表示。確認済み操作。
- **PC「患者」 `(app)/patients`**: QR一括印刷／個別印刷・再発行。**PC「設定」 `(app)/settings/checkin`**: しきい値設定。
- **backend**: 記録・判定・集計・発行・通知・パージ。判定はサーバ責務（クライアントは信用しない）。

## Phase 別 実装状況（すべて本番デプロイ済）
| Phase | 内容 | migration | 主なAPI/画面 |
|---|---|---|---|
| 1 | チェックイン記録・距離/位置判定 | 0041 | `POST /visits/{id}/checkin\|checkout\|no-show`、`visit_checkins`/`checkin_settings` |
| 2 | モバイルQR/GPS記録UI | — | `(mobile)/m/today/[visitId]`、`me.ts`、`checkin-queue.ts` |
| 3 | PC訪問モニター | — | `GET /monitor`(+nearby)、`(app)/monitor` |
| 5-1 | QR発行・印刷 | 0042 | `GET/POST /patients/{id}/qr[/regenerate]`、`(app)/patients/qr-print`、judge=旧QRは410 Gone |
| 4 | しきい値設定UI | 0043 | `GET/PUT /checkin-settings`、`/checkin-settings/public`、`(app)/settings/checkin` |
| 5-3 | 確認済み(visit単位) | 0044 | `POST/DELETE /visits/{id}/review`、`visit_reviews`、モニター抑制 |
| 5-2 | アプリ内通知 | 0045 | mismatch=checkin時イベント駆動、missing=`POST /admin/checkin/check-missing`(cron) |
| 5-6 | GPS2年パージ | — | `POST /admin/checkin/purge-gps`(cron)、lat/lng/accuracyをNULL化(distance/status保持) |

migration は **0041→0045（単一head 0045）**。alembic は本番PostgreSQLでのみ実行（SQLiteテストは create_all。0001付近がSQLite未対応のため全チェーンはSQLiteで通らない＝既存仕様）。

## 判定ロジック（要点）
- **位置 match_status**（記録時に確定・`threshold_snapshot`で固定＝遡及しない）: GPS無/精度>review→`no_gps`、距離≤match_m(既定100)→`match`(精度劣化はreview)、≤review_m(300)→`review`、超→`mismatch`。
- **実効状態（モニターが集計時に現行しきい値で合成）**: phase(future/awaiting/inprogress/done/missing)×alert(none/review/mismatch/missing)。遅延(late_min)・退出忘れ(max_inprogress_min)・未訪問(grace)・確認済み(reviewed→抑制)を合成。JST(Asia/Tokyo)で評価。
- しきい値は **全社一律**（`checkin_settings` シングルトン・6項目: match_m/review_m/accuracy_m/no_show_grace_min/late_min/max_inprogress_min）。モバイル到着確認のプレビューは `/checkin-settings/public`(距離系のみ)で動的取得。

## 主要ファイル（実装本体）
- backend: `app/services/checkin/(judge.py 記録判定 / monitor.py 集計合成 / notify.py 通知 / purge.py パージ)`、`app/api/v1/(visits.py checkin / visit_monitor.py / patient_qr.py / checkin_settings.py / visit_review.py / admin_checkin.py)`、`app/models/(visit_checkin / checkin_settings / revoked_qr_token / visit_review)`、`app/utils/(geo.py haversine / db.py advisory lock)`。
- frontend: `(mobile)/m/today/[visitId]/page.tsx`、`components/mobile/QrScanner.tsx`、`lib/(qr-token,geo,checkin-queue).ts`、`(app)/monitor/*`＋`components/monitor/*`、`(app)/patients/qr-print/*`、`(app)/settings/checkin/page.tsx`、`lib/queries/(me,monitor,patientQr,checkinSettings).ts`。

## 本番運用（cron）
- VPS `carelink-cron` user（本作業で新規作成）＋ `/home/carelink-cron/.secrets/carelink.env`(`ADMIN_TOKEN`)。crontab:
  - `*/5 * * * *` → `/admin/checkin/check-missing`（未訪問通知）
  - `0 3 * * *` → `/admin/checkin/purge-gps`（GPSパージ）
- ログ: `/home/carelink-cron/checkin_missing.log` / `checkin_purge_gps.log`（logrotate未設定＝backlog）。
- **トークン**: アプリ署名のadminアクセストークン。**現在10年有効（exp 2036-06-27）**。`create_access_token(subject=<admin user id>, role='admin', ttl_seconds=...)` で発行。
  - **再発行手順**（漏洩時/失効時）: `cd /opt/carelink && docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec -T backend python -c "from app.core.security import create_access_token; print(create_access_token(subject='<admin uuid>', role='admin', ttl_seconds=315360000))"` → 出力を `printf 'ADMIN_TOKEN=%s\n' "<token>" > /home/carelink-cron/.secrets/carelink.env`（chmod 600 / chown carelink-cron）。

## デプロイ手順（既存ルール準拠・migration含む場合）
①push ②`pg_dump`バックアップ(`/opt/carelink/backups/`) ③`git pull --ff-only` ④`build backend frontend`（migration焼込のためbackendも） ⑤`alembic upgrade head`＋`alembic heads`単一確認 ⑥`up -d --force-recreate backend frontend` ⑦スモーク。compose常に `-f docs/deployment/docker-compose.production.yml --env-file .env`。**注意**: frontend依存追加時は `pnpm install` で `pnpm-lock.yaml` を同期しないと本番buildが `--frozen-lockfile` で失敗する（実際に1度踏んだ）。デプロイ後 現場端末 Ctrl+Shift+R。

---

## 気になる点・残作業（次エージェントへ）
### 運用上の注意（優先度高）
1. **cron監視がログのみ**（アラートなし）。check-missing が止まると未訪問通知が静かに止まる。business時間に成功ログが無ければ通知する watchdog の追加が望ましい。
2. **cronトークンは2036年失効**。長いが有限。理想は scoped service account / APIキー（アプリ改修）。
3. **GPS保持2年の法務確認が未了**（派生値 distance_m/match_status だけで実地指導・紛争に耐えるか）。purge cron は動いているが、初回は対象0件（2年前データ無し）。法務NGなら期限を延ばす（`purge.py DEFAULT_RETENTION_DAYS`／>=365 ガードあり）。

### 機能 backlog（設計で据置と明記済み）
4. **通知の拠点別絞り込み**（現状=全admin/manager宛て。多拠点運用時に要対応）。
5. **通知の一括既読/掃除・bulk review**（mismatch通知や既読の蓄積。missing は到着で自動解決済み）。
6. **しきい値の拠点別上書き**（現状=全社一律）。
7. **`/q/{token}` ランディングページ**未実装（汎用カメラ用ディープリンク。アプリ内スキャナは extractQrToken で動くので不要だが、家族が標準カメラで読むと404）。
8. **連絡(tel:)未対応**（Patient/Staffに電話列なし＝ユーザー判断で見送り。モニターの「連絡」は詳細表示まで）。

### 技術的な留意点
9. **`visit_checkins.reviewed_by/reviewed_at` 列は vestigial**（Phase1で作ったが、5-3は別テーブル `visit_reviews` を採用。撤去は任意）。
10. **`python-jose` が unmaintained**（HS256使用のため既知CVEは無影響だが、PyJWT等への移行が望ましい・backlog）。
11. **しきい値の当日即時フリップ**: 時間系(grace/late)を日中変更すると monitor の判定が即フリップし、既発行の通知と矛盾しうる（設定ページに注意書きあり。運用は翌日/新規visitから推奨）。
12. **モバイルプレビューのしきい値キャッシュ**: `/checkin-settings/public` をmount時取得。admin変更が反映されるのは次回フェッチ時。
13. **既存のフレーキーテスト** `tests/test_visits.py::test_visits_delete_manager_returns_204`（共有in-memory SQLiteのトランザクション競合・本機能と無関係・先行から存在）。
14. **ローカル環境がPython 3.14**（本番3.12）。`test_patients_v2`(special_week_active)等が**素のdevelopでも**失敗するが、これは3.14由来の既存事象で本機能の回帰ではない。全体pytestを回すとこれら既存失敗が混じる点に注意。
