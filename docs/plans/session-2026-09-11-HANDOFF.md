# セッション引き継ぎ 2026-09-11（カイポケ連携の是正週: 予実比較 → W37 取込検証 → 請求区分不整合の根治 → 取込 2 件の根治）

**次のエージェントへ: まずこのファイル。** 前セッション総括 = `session-2026-09-10-HANDOFF.md`（患者ステータス×予定 Phase 1〜3・不具合連絡 3 件・予実比較 §8・W37 §9・請求区分 §10 の詳細はそちら）。本ファイルは「9/10〜9/11 で確定した事実」「本番の状態」「次のイベントに向けた準備」を一枚にまとめたもの。

## ★ 最初の 5 分
0. **本番稼働**: らく助 `a81c1c8`（2026-09-11 23:30 JST・migration 0083 まで適用済み・作業ツリークリーン）／RPA PlaywrightTest1 `0e00107`（9/11 昼）。両方とも healthz 200。frontend は 9/11 夕方以降変更なし（ハードリロード不要）。
1. **このセッションで本番に入ったもの（時系列）**: `5c10906` ステータスバッジ是正 → `32c9b09` 藤原様「稼働中に戻せない」FE zod 是正 → `e371043`+mig0083 / RPA `362150f` 予実比較レポート（実績 CSV 取得）→ `4cd08d1` 職種未設定/同日複数タグ → RPA `ad780b1` export 30 秒タイムアウト根治 → RPA `0e00107` + らく助 `13af578` 請求区分（正看/准看）不整合の根治 → `a81c1c8` 取込 2 件の根治（跨ぎ date_change / 月跨ぎ週）+ 送信 month 決定。
2. **PO（お客様）が気にしている点への答え（確定）**: (a) 送信で正看/准看が混ざる不具合は **らく助起因・9/11 に根治・本番稼働**。ただし「区分が変わる担当変更」を実際に送る初回の実機確認は **未実施**（§4-1）。(b) 取込（カイポケ→らく助）は正看/准看を **持ち込まない**（サービス内容/職種列を読まない）。(c) 9/11 夕方に残った 18 件の資格不整合は **カイポケ画面での手作業の担当変更が起因**（らく助の 9/8 送信内容と担当が全件異なる・9/8 以降の送信ジョブ無し）。
3. **直近のイベント順（§4）**: ① W38（9/14 週）の方針決定と送信（監督付き・grade_change 1 件から）② 9 月レセプト前の 予実比較（9 月）再実行 ③ 9/28〜10/4 の月跨ぎ週（突合の 100 秒制限が未対応）④ 残骸ジョブ 33 件の決着。
4. **禁止・厳守**: カイポケへの書き込みは PO 承認後のみ／export（読み取り）は自由だが RPA が idle か `/api/status` を先に見る／並行レーンで `git stash` 禁止／RPA は `git pull` 後に `docker compose up -d --force-recreate kaipoke-api`（単一ファイル bind mount）／らく助は migration 手動適用／payload キー追加は RPA 先。
5. 患者名入りの報告書は `docs/reports/`（未追跡のまま・コミットしない）。

## 1. 確定した事実（このセッションで証明・訂正したもの）
- **「未」バッジ = 職種未設定**（正=正看・准=准看）。「未」行は実績 CSV に職種１空で **出る**（6/9 兼行様・7/29 小俣様で実証）。「未確定行は CSV に出ない」は誤りだった。
- **予定→実績反映で作られた未確定行 + 実際の実績行の二重登録**（本名さん 8 月）は、予実比較レポートの「職種未設定」「同日複数」タグで機械的に一覧化できる。6 月 1+2 件・7 月 同・8 月 0（削除済）・9 月 職種未設定 1（9/8 峯﨑様・予定側）。
- **資格×請求区分の不整合**（職員1 の職種と サービス内容の 正看/准看 が食い違う）: 6〜8 月 0・9 月 実績 18（9/10 時点）。起因は 2 系統: (A) らく助 9/3 送信の edit 6 件（根治済み）(B) カイポケ画面での手作業の担当変更（編集ダイアログはサービス内容を変えない）。9/11 夕方の残り 18 件（実績 1: 中原様 9/11 14:00 熊澤・准看／予定 17: 9/15〜9/17）は全て (B)。
- **らく助の請求区分の決め方**: 送信時に職員1 の資格から決める（正看＋准看同行は正看優先＝残タスク⑦は csv_builder 未実装）。訪問ごとの上書き `kaipoke_service_override` は本番 0 件。
- **取込が書くもの**: 担当1/2・日付・時刻・コース・source=import・note 追記のみ。読まないもの: サービス内容・職種・業務種別・職員3・備考。
- **W37（9/7 週）**: 9/11 08:47〜08:52 に松岡さんがカイポケ正で取込済み（失敗 0）。残差 1 件（久須見様 9/7 11:00 髙梨）は smart-inbound の跨ぎ不具合で消えたもの → PO 承認で手動追加済み（visit 3d2b70ae）→ その不具合自体を `a81c1c8` で根治。
- **W38（9/14 週）**: らく助は 9/8 12:05 に送信済み（job 0ff737af・166 件）。その後カイポケ側で手直しが進み、9/11 15:30 現況との机上 outbound 差分は **123 件**（edit 94 / add 12 / delete 11 / date_change 6・うち grade_change 1 = 岡村様 9/17 14:30 高岡→熊澤）。**まだ取込も再送信もしていない**。
- **9/10 の「落ちた」**: Claude Code セッションが 00:02 に終了しただけ。サーバーは無事。

## 2. 本番に入っている仕組み（今回分・使い方）
- **予実比較レポート（月）**: 連携コンソール › 「予実比較レポート（月）」› 月を選び「実績を取得して比較」（約 1.5〜2 分・RPA idle 必須）→ 「最新のレポートを開く」。タグ: 重複／サービス違い／同行違い／職種未設定／同日複数／**資格不整合**。CLI: `python docs/tools/kaipoke-ops/admin_call.py`（本番 backend で JWT を発行して呼ぶ・timeout 900 版を `/tmp/admin_call.py` に置いた）。最新出力 = `docs/reports/2026-09-11-plan-actual-2026-09.pdf`。
- **請求区分の根治**: diff engine が「区分が変わる担当変更」に `grade_change=True`・`service_type`（らく助値）・`service_type_from`（カイポケ値）を付けて 1 件の edit で送り、RPA が削除検証→新サービス内容で再追加（失敗時は元の内容で復元）。`KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=True`。
- **取込の根治**: smart-inbound の跨ぎ date_change を移動として適用・未適用/未選択/日曜宛ては元日を置換しない・打刻付き訪問の移動は 422。月跨ぎ週は両側を月ごとに週内日へ制限。送信 `/apply` の RPA month は選択行の実日付の月（2 か月混在は 422・9/30→10/1 の date_change は `month_boundary` 除外）。
- **机上 diff（RPA を触らない検証手口）**: `kaipoke_csv_snapshots` の CSV を `build_local_diff(current_csv=...)` に注入し rollback。スクリプト例 = scratchpad `dry_diff_w38.py` / `dry_diff_w40.py`（本番 backend コンテナで `PYTHONPATH=. python /tmp/xxx.py`）。

## 3. 残タスク・気になる点（優先順）
### A. 運用イベントに直結（§4 と対）
1. **W38 方針**: らく助正で送信すると 123 件（事務のカイポケ手直しを戻す）／カイポケ正で取込むと らく助の 9/8 送信内容が上書きされる。**PO 判断が必要**。どちらでも資格不整合 17 件は「送信」で自動是正される（取込→送信の順なら担当は事務の手直しどおり・区分だけ直る）。
2. **grade_change 初回実機確認（未実施）**: 岡村様 9/17 14:30 高岡→熊澤 の 1 件で dry-run → 1 件送信 → export で確認 → 残りを送る。失敗時は `grade_change_rollback` / `grade_change_rollback_no_source` の reason を見る。
3. **9/28〜10/4 週の 🔄突合**: smart-preview と diff-inbound がそれぞれ export 2 本（~90 秒×2）で Cloudflare 100 秒制限に当たり得る。**未対応**。対策案 = ② smart-preview が保存した週 snapshot を ③ diff-inbound で再利用（export 0 本）or ジョブ化（events-inbound-preview と同じ start→polling）。送信 `/apply` は 9 月分と 10 月分を **分けて選択して 2 回送る**（422 で案内される）。
4. **残骸ジョブ 33 件**（`kaipoke_jobs.status in (running,pending)`・全て 9/1〜9/3）: 実害なし（`_reconcile_latest_job` は plan-actual を除外済み）だが履歴が汚れる。決着案 = `update kaipoke_jobs set status='failed', error_message='orphan (session lost)' where status in ('running','pending') and started_at < '2026-09-05'`（PO 承認後・pg_dump 後）。
5. **9 月レセプト前の予実比較**: 9 月末〜10 月初に「実績を取得して比較」を再実行し、資格不整合 0・職種未設定 0・同日複数 0 を確認してから請求へ。

### B. 既知の不具合・限界（バックログ）
6. 区分だけ違い staff/時刻が同じ行（カイポケ「精神基本療養費Ⅰ」suffix 無し vs らく助「…・准看」）は差分にならず送信では直らない（予実比較で検出は可能）。
7. 置換取込で消える訪問に紐づく ⭐`placed_visit_id` / 直接同行リンクの付け替え無し（従来からの挙動）。
8. 置換は wipe-first: 名寄せ不可・担当なし・非稼働・重複・時刻不正の行はスキップ＝らく助から消える（通知のみ・自動復元なし）。青ピン/⭐/manual も保護されない。日曜は smart 対象外。職員3 は落ちる。同一患者同時刻 2 行は 1 行に潰れる。
9. 手動 `/apply-inbound` の days 指定は before 側のみ（仕様）。
10. FE「請求区分変更」バッジ未実装。残タスク⑦（正看優先の同行ルール・csv_builder）。
11. 段階②（月間スケジュール画面の実績側の印を画面から読む）は優先度低（CSV で足りる）。
12. カイポケ API 相談書（`docs/reports/2026-09-10-kaipoke-api-consultation.pdf`）の提出は PO 側。
13. 患者ステータス連動の実機確認 (a)〜(h)・`PATCH /special-visit-periods` 未ガード（前セッション §4/§5）。
14. 篠原千晶様 P104 の論理削除（松岡さん自身の操作）: 復元は PO 指示待ち。
15. 既知失敗: backend 29 件 + フレーク 3（`session-2026-09-10-HANDOFF.md` §5・9/11 夜に HEAD で同一失敗を再確認＝退行なし）。frontend 1（middleware manager）。

### C. 気になる点（未証明・観察継続）
- 「未」行が全て本名さんに集中（6/9・7/29・8 月）。RPA の旧「削除→再追加で職種未設定」経緯（9/1 前）が疑われるが、カイポケ側の作成履歴が無く未証明。
- 9/8 峯﨑様・髙梨の予定側 職種未設定（RPA 操作記録なし）。手作業の可能性。
- 事務がカイポケで直接担当を変える運用が続く限り、資格不整合は再発する（らく助では防げない）。運用ルール「担当変更はらく助で行い送信する／カイポケで直す場合は削除して正しい区分で追加」を PO から周知してもらう。

## 4. 次のイベントの準備（チェックリスト）
### 4-1. W38（9/14 週）を送信するとき（PO 判断後）
1. `/api/status` で RPA idle・`kaipoke_jobs` に running が増えていないこと。
2. 連携画面で 🔄突合（または CLI）→ 差分件数が机上 123 件と大きく違わないか。**取込→送信の順**なら先に smart 取込（打刻のある日は差分・無い日は置換）。
3. dry-run 送信 → 「請求区分変更」の行（岡村様 9/17）を含めて 1 件だけ送信 → RPA ジョブのレポート（📄）で `grade_change` の成否・reason を確認 → export で現況確認。
4. 問題なければ残りを送信（日中・RPA 失敗は時刻依存＝memory `careflow-rpa-timing-failures`）。
5. 送信後に 予実比較（9 月）を再実行し 資格不整合 0 を確認。

### 4-2. 9/28〜10/4 の月跨ぎ週
1. **突合前に** §3-A3 の対策（週 snapshot 再利用 or ジョブ化）を入れる。入れられない場合は、突合が 524 で落ちても BE 側はシートを作っている可能性があるので、ジョブ履歴と correction sheet 一覧を確認してから再試行する。
2. 送信は 9 月分・10 月分を分けて選択（混在は 422）。9/30→10/1 の日付変更はカイポケで手動。
3. 取込（smart）は両月 export 済みで安全（`export_current_week_csv`）。

### 4-3. 月次（レセプト前）
1. 連携コンソール › 予実比較（前月）→ PDF 化（`docs/reports/`・未追跡）→ 事務へ。
2. 見るタグ: 資格不整合（請求区分の誤り）・職種未設定/同日複数（未確定行の残り）・実績のみ（予定外）。

### 4-4. デプロイ手順（変更なし・要点）
- らく助: push → `pg_dump` → `git pull --ff-only` → `build backend [frontend]` → `up -d --force-recreate` → **`run --rm backend alembic upgrade head` → `alembic heads` 単一** → healthz（127.0.0.1:18001 と公開）。
- RPA: `/root/PlaywrightTest1` で `git pull` → idle 確認 → `docker compose up -d --force-recreate kaipoke-api` → `/api/status`。payload キー追加は RPA 先。

## 5. 進め方の型（機能したもの）
- ディレクター（私）が仕様を固める → executor（Opus）2 レーン並行（ファイルを分ける・git 操作禁止）→ code-reviewer（Opus）→ 是正 → 対象テスト＋全体テストを HEAD と比較 → コミット → デプロイ → **本番データでの机上検証**（RPA を触らずに snapshot 注入）。
- レビューは今回も BLOCKER を捕まえた（月跨ぎ週の送信が 9 月画面に 10 月行を書く）。**レビュー無しで出さない**。
- 「らく助のせいか」の問いには **送信ジョブ明細（kaipoke_job_items）と現況 CSV の突合** で答える。9/3 以前のジョブは明細無し（改修前）。

## 6. 参照
- 前セッション: `docs/plans/session-2026-09-10-HANDOFF.md`（§7 不具合連絡 / §8 予実比較 / §9 W37・取込監査 / §10 請求区分根治）
- 設計: `plan-actual-compare-design-2026-09-10.md`・`patient-status-schedule-design-2026-09-09.md`・`kaipoke-service-content-design.md`
- 報告書（未追跡）: `docs/reports/2026-09-11-w37-kaipoke-inbound-report.pdf`（5p）・`2026-09-11-w37-service-content-fix-procedure.pdf`（2p・25 件）・`2026-09-11-plan-actual-2026-09.pdf`・`2026-09-10-patient-status-behavior-report.pdf`・`2026-09-10-kaipoke-api-consultation.pdf`
- memory: `careflow-inbound-fixes-20260911` / `careflow-service-grade-rule` / `careflow-plan-actual-compare` / `careflow-rpa-deploy` / `careflow-deploy`
- バックアップ: `/opt/carelink/backups/pre-deploy-inbound-fix-20260911-1429.sql.gz`・`pre-kusumi-0907-add-20260911-0433.sql.gz`
