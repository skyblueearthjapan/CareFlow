# カイポケ 予定×実績 突合レポート（段階①）設計・実装記録 2026-09-10

**背景**: 8 月レセプトで本名さんの実績が重複（実績側に「未・予定外」行が残る）。らく助起因ではないが、機械的に見つける手段が無かった（`session-2026-09-10-HANDOFF.md` §7-3）。PO 指示「慎重に正確に・非破壊で」。

## 0. 原則（非破壊）
- カイポケへは **読み取り（CSV 出力）のみ**。apply / expand / auto_apply は触らない。
- 予定 CSV（`data/current_{YYYYMM}.csv`・Drive の予定ファイル）を実績で **上書きしない**。実績は `_actual` 別名。
- らく助側は追加のみ（列 `division` 既定 `'plan'`・新エンドポイント・新テーブル無し）。既存の diff-local / 取込 / 送信の挙動は不変。
- **例外 = 是正 1 件**: RPA export は失敗/stale 時に `csv_content` を返さず `success=false`。らく助側は `ensure_export_ok` で `success=false` を例外にし、空 CSV を「カイポケ空」と誤解して全件 add にしない（8/31・9/3 の教訓）。

## 1. 構成
| 層 | 変更 | 正典 |
|---|---|---|
| RPA (PlaywrightTest1 `362150f`) | `POST /api/export` に `division: plan|actual`。actual は `#planAchievementsDivision02` を選択→DL 直前に再確認→失敗時は DL せず。`data/current_{YYYYMM}_actual.csv`。結果に `division/csv_sha256/csv_mtime`。stale/失敗/読取不能を区別し `csv_content` を返さない。不正 division は HTTP 400。plan 経路は画面操作を追加しない | `commands/export.py` / `api_server.py` / `tests/test_export_division.py` |
| BE mig 0083 | `kaipoke_csv_snapshots.division` (既定 plan)・索引と COALESCE 一意索引に division を追加 | `alembic/versions/0083_*.py` |
| BE service | `plan_actual_fetch.fetch_month_snapshots`（plan→actual 逐次・区分ごとに commit・echo 検証で fail-closed）/ `plan_actual_compare.build_plan_actual_report` + `render_plan_actual_html` / `plan_actual_job.run_plan_actual_job`（BackgroundTasks・自前セッション・必ず settle）/ `export_guard.ensure_export_ok` | `app/services/kaipoke/` |
| BE API | `POST /integrations/plan-actual-compare {month}` → 202 running（RPA busy / 実行中ジョブ / 10 分超の孤児は整理）・`GET /integrations/plan-actual-report?month&format=html|json`（月全体スナップショットのみ・ジョブが記録した snapshot id を優先・404 は欠けている区分を明示） | `app/api/v1/integrations.py` |
| FE | 連携コンソールに「予実比較レポート（月）」カード（前月既定・実績を取得して比較・最新のレポートを開く・実行中/失敗/完了サマリ・5 秒ポーリング）・ジョブ一覧に 📄 予実レポート | `components/integrations/PlanActualReport*.tsx` / `lib/queries/planActualReport.ts` |

## 2. 比較ルール
- 対象行 = 業務種別が 医療保険/介護保険（イベント/個別業務は `events_skipped`）。
- キー = (日, 利用者名 正規化)。組内で 4 段の貪欲ペアリング: 担当集合{職員1,職員2}+時刻 → **一致** / 担当 → **時刻ズレ** / 時刻 → **担当違い** / 残り同士 → **相違（時刻・担当）**。余り → **予定のみ** / **実績のみ（予定外）**。
- タグ（内数）: **重複** = 同じ側で (担当, 時刻) が同一の行が 2 本以上 / **サービス違い** / **同行違い**（職員2 のみ不一致）。
- 担当 `-`/空 = 担当なしとして同一扱い。時刻は `HH:MM` ゼロ埋め（秒は捨てる）。
- `counts` キー: `一致 時刻ズレ 担当違い 相違 予定のみ 実績のみ 重複 plan_rows actual_rows events_skipped malformed_rows`。

## 3. 既知の限界（レポートの注意書きに明記）
- **未確定（未）の実績はカイポケの CSV 出力に含まれない** → 本レポートには出ない。未確定行は月間スケジュール管理の実績側で確認（段階②＝画面読み取りで対応予定）。
- 同行者（職員2）は一致/同行違いの判定にのみ使う。請求区分の正誤は判定しない。
- 予定側は月全体のスナップショットのみ使う（週単位スナップショットが混ざると 3 週分が「実績のみ」に化ける）。

## 4. リリース手順（順番厳守）
1. RPA を先にデプロイ（`/root/PlaywrightTest1` で `git pull` → RPA が idle であることを `/api/status` で確認 → `docker exec kaipoke-api supervisorctl restart api`）。**旧 RPA は `division` を無視して予定を返す**ため、BE が先だと「全件一致」の偽レポートになる（BE は echo 検証で fail-closed だが順番も守る）。
2. らく助: push → pg_dump → build → up → **`alembic upgrade head`（手動）** → heads 単一確認 → healthz。
3. 本番で 2026-08 を実行（admin_call.py または画面）→ レポートで本名さんの事例（河野 8/15・林 8/27 = 時刻ズレ、並木 8/28 = 相違）を確認。

## 5. 実データ検証（デプロイ前・ローカル）
8/24 22:00 の予定 CSV（18 列）× PO 提供 8 月実績 CSV（記録Ⅱ付き 21 列）を比較サービスに通した結果: 一致 131 / 時刻ズレ 359 / 担当違い 5 / 相違 12 / 予定のみ 12 / 実績のみ 16 / 重複 0（未確定行は CSV に無いため）。21 列でもヘッダ名で読める。本名さん 4 事例は想定どおり分類。

## 6. 段階②（未着手）
月間スケジュール管理画面（利用者別/職員別）の実績側を RPA で読み取り、「未」「重複」「予定外」の印を直接拾う。RPA は既に予定側(01)/実績側(02)を区別して読む処理を持つ（`auto_apply.py:531`）。
