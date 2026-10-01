# セッション総括 2026-09-30〜10-01（引き継ぎ書）

作成: 2026-10-02 ／ 対象期間: 2026-09-30〜2026-10-01
前の総括: `session-2026-09-24-HANDOFF.md`（9/24〜26）。この期間の作業中の詳細記録は `session-2026-09-30-HANDOFF.md`（随時更新した生ログ。細部はそちら）。

---

## ★ 最初の 3 分（次のエージェントへ）

1. **本番 = `680e984`（develop）・alembic `0089_multi_office_settings`（単一 head）**。2026-10-01 に 6 回デプロイした（§2）。ローカル develop は `2779370`（docs のみ先行）で origin と一致。
2. **未完了の最優先**は §4「すぐ」: 現場への案内／本番の取込画面でプレビューを 1 回試す（10/26 の週の前に）／松岡様への 9 月分の送付は今泉さん。
3. **作業の決まり**（必ず守る・§9）: 本番 DB は読み取りが原則（書くときは PO の了承とバックアップ）・デプロイ前に pg_dump・migration は手動で適用・`--no-verify` 禁止・並行作業で `git stash` 禁止・日本語ファイルを PowerShell の Get-Content/Set-Content で読み書きしない・利用者名入りの帳票は git に入れず公開もしない・コミット/push/デプロイは PO の了承の後。
4. **言葉の決まり**: 実績の時刻は「合わせる」「調整」。**「直す」「修正」「補正」は使わない**（PO 指示・看護師に失礼）。アプリ名は「らく助」（「楽スケ」と書かない）。
5. PO（今泉さん）は**選択式の質問**を好む（AskUserQuestion）。モックを先に見せてから実装する進め方が基本。

---

## 1. 目的

- お客様: 訪問看護ステーション よりより（拠点 稲毛・都賀）。窓口 = 松岡様。PO = 今泉さん。
- らく助（CareFlow）= 訪問看護のスケジュール管理アプリ（FastAPI + PostgreSQL 16 / Next.js 15 PWA）。カイポケ（請求システム）と RPA で双方向連携。
- 今期の大きな目標: **10 月は紙＋QR 併用、11 月に QR 打刻へ完全移行**。そのための記録の正確さ（読んだ時刻・打刻漏れ・送信遅れ）と、管理者の見える化（モニター・ダッシュボード）、カイポケ取込の安全性。
- 並行して、**将来ほかの訪問看護事業所へ提供できるよう、お客様固有の決め打ちをコードから外す**（PO 2026-10-01「今の予定を先に。ただし忘れずに」）。

## 2. 経緯と実施内容（時系列）

### 9/30
- アプリ全体・引き継ぎ書の調査、QR 運用／スマホ／PC 反映の徹底調査。
- 松岡様（9/30 14:22）「9 月の QR 読み取り履歴を明日朝までに」→ HTML（A4 縦・改ページ）／PDF／Excel を作成（`docs/reports/2026-09-30-qr-history-2026-09.*`・git 管理外）。道具 = `docs/tools/visit-history/`。
- 訪問記録 `/records` を 2 タブ（打刻履歴／音声記録）に（モック承認 → 実装）。打刻履歴は月・週で Excel（4 シート）・A4 を出せる。
- 実績の時刻を合わせる（スマホのホイール式・10 分基本→1 分・その場でも後からでも・予定は動かさず実績だけ・計算は `services/checkin/actuals.py` に一本化・読取時刻 = 端末時刻が妥当ならそれ）。migration 0088 `visit_time_adjustments`（追記のみ）。
- モニターの職員単位化・ダッシュボード刷新・前の週をコピーの調査とモック（いずれも PO 承認）。

### 10/1（デプロイ 6 回・すべてバックアップ → pull → build →（migration）→ recreate → healthz）
| # | 時刻(JST) | コミット | 内容 | バックアップ（/opt/carelink/backups/・名前は UTC） |
|---|---|---|---|---|
| 1 | 05:20 頃 | `4900946` (mig 0088) | 打刻履歴タブ＋実績の時刻を合わせる | `pre-deploy-actualtime-adjust-20260930-2015.sql.gz` |
| 2 | 07:1x | `9ed25d3` | 訪問モニターを職員単位に（行の下に開くパネル・札は行ヘッダだけ・略称 = `offices.short_label`）／時刻を合わせる理由を撤去 | `pre-deploy-monitor-staffrows-20260930-2211.sql.gz` |
| 3 | 10:10 頃 | `a890c2d` (mig 0089) | ダッシュボード刷新（人ごとのカード B＋1 人を深く C・管理者のみ・不在/同行の表示）／前の週をコピー＋「週を生成」500 修正／別事業所の準備 #1〜#4・#7・#9＋カルテの拠点名エラー | `pre-deploy-dash-copyweek-0089-20261001-0105.sql.gz` |
| 4 | 14:5x | `43e8e9c` | 日をまたいで届いた打刻の受け付け（3 日以内）／カイポケ取込の修正 6 件 | `pre-deploy-latedelivery-inbound-20261001-0545.sql.gz` |
| 5 | 16:2x | `680e984` | 取込プレビューを裏で動かして画面が待つ形に（月跨ぎ週の約 100 秒・524 対策） | `pre-deploy-smartpreview-async-20261001-0718.sql.gz` |

各本とも: 実装（別作業ツリー）→ **別担当のレビュー**（ほぼ毎回 REQUEST CHANGES → 反映）→ develop 取込 → 結合確認（PostgreSQL 16＋実画面・Playwright）→ PO 了承 → デプロイ。

### 10/1 本番データの操作（PO 了承済み・すべてバックアップ後）
- **9 月の QR 29 件を看護記録（カイポケ実績）の時刻に合わせた**（松岡様の「30 分未満を 35 分に」への回答。一律の書き換えは改ざんになりうるため断り、カイポケ実績＝看護記録の時刻で合わせる形で PO 了承）。
  - 28 件・49 回は API（`PUT /visits/{id}/actual-time`・今泉さんの管理者アカウント yuji.imaizumi@thousands.jp・理由欄「看護記録（カイポケ実績）に合わせる（2026-10-01 PO 了承）」）。バックアップ `pre-adjust-sep-kaipoke-20261001-0256.sql.gz`。
  - 9/7 11:15 植田様（川名さん）の 1 件は QR がテスト読み取り（利用者宅から約 4.2km・理由「テスト」）で、「到着は読み取りより後にできない」決まりに当たるため、**この 1 件に限り本番サーバー内のスクリプトで調整 2 行＋監査ログ 2 行を追記**（method=SCRIPT・path=ops/2026-10-01/fix_0907）→ 11:15〜11:50。
  - 結果: 9 月の時刻調整 29 件・30 分未満 0 件。実績表 v3 = `docs/reports/2026-10-01-qr-history-2026-09-v3.{xlsx,html,pdf}`。
- **藤原様 9/23 の打刻を付け替え**: `backend/scripts/reattach_cancelled_checkins.py` を確認だけで実行（9 月全体で対象 1 件）→ `--apply`。取消済み 13:00（高岡さん・`00bce4ed…`）の打刻 2 件 → 11:30（熊澤さん・`78960321…`）へ。11:30 は完了・到着 11:55／退出 12:29。バックアップ `pre-reattach-fujiwara-20261001-0551.sql.gz`。
- **予実比較（カイポケ 9 月の予定・実績 CSV の取得）を 1 回実行**（読み取りのみ・11:11）: カイポケ 予定 589＝実績 589、一致 223・時刻ズレ 366・資格不整合 2。

## 3. 現在の状態

| 項目 | 状態 |
|---|---|
| 本番 | `680e984`・alembic `0089`・healthz 200・最後のデプロイ後エラーログ 0 |
| ローカル | develop `2779370` = origin/develop。未追跡は前セッション以前の中身未確認のもの（`docs/HANDOFF.md`・`docs/manuals/`・`docs/mockups/renkei-layout-wireframe.html`）と `.claude/scheduled_tasks.lock` の削除のみ |
| 作業ツリー | `.claude/worktrees/agent-*` が 6 つ残っている（すべて develop に取込済み。うち 3 つは locked）。消してよい（§4 後始末） |
| テストの基準 | backend 全体で **既知の失敗 29 件**（manager ロールの RBAC・audit middleware・patients_v2・reset_to_fixed 2 件ほか。b70f7ab でも同じく失敗）＋実行順で時々落ちるもの（`test_admin_users::test_staff_cannot_create_user_manager_alias_can`・単独では通る）。frontend は **失敗 1 件**（`__tests__/middleware.test.ts` manager 経路）＋ vitest が拾う Playwright e2e 9 ファイル。`BulkPoolInsertDialog` のテストが並列負荷で時々落ちる（単独では通る） |
| この PC | Docker Desktop 起動中・`postgres:16` イメージあり（結合確認用） |
| 9 月の QR | 予定 602・到着 113・退出 113・退出なし 0・時刻調整 29・打刻なし 489 |

## 4. 未完了の残タスク

### すぐ（今週）
- [ ] **現場への案内**（今泉さん）: PC は Ctrl+Shift+R、スマホはアプリを開き直す。新しく使える画面 = ダッシュボード（管理者）・スケジュールの「週を作る」・設定 → 事業所の情報・打刻履歴の「遅れて届いた」絞り込み・取込プレビューの待ち表示。
- [ ] **本番の取込画面で「❶ プレビュー」を 1 回試す**（カイポケを読むだけ・反映しない）。非同期化の実地確認。10/26 の週（月跨ぎ）の前に。
- [ ] **松岡様へ 9 月分を送付**（今泉さん）: 実績表 v3。説明文の案 =「30 分未満と出ていた分は QR の読み忘れ・読み取りの遅れで、看護記録（カイポケ実績）の時刻に合わせました。元の読み取り時刻と調整の記録は残っています」。
- [ ] 確認表 v2（`docs/reports/2026-10-01-qr-check-2026-09-v2.xlsx`）は、29 件をカイポケ実績で合わせ終えたので、職員への確認は「念のため」の位置づけ（PO 判断）。

### 実機でしか確かめられないこと
- [ ] 時刻を合わせるホイールの回し心地（iPhone / Android）／圏外で打刻 → 電波復帰で送られる／**夜に圏外で読み翌朝に届く**（日またぎの受け付け）／A4 を実画面から開いて実プリンタで印刷。
- [ ] **前の週をコピー → 自動スタッフ割当でローテーションになるか**（結合確認ではテストデータに勤務表が無く未確認）。初回の実運用で一緒に見る。

### 期限のあるもの
- [ ] **9/21 週の未反映 5 件**（前回総括 §4-1）: 取込の修正が入ったので、今の取込で読み込めば打刻済み訪問は守られる。まだ取り込んでいなければ連携画面から（プレビューを確認してから）。
- [ ] **11 月の QR 完全移行まで**: 到着の読み取りが無い訪問に、到着を後から入れられるようにするか（打刻漏れの補完）＝**PO 判断が必要**。QR 運用の定着（髙梨さん＝入るときの読み忘れ・熊澤さん＝退出を読まない・高岡さん＝ほぼ読まない → 個別の声かけ）。

### 前回からの残り
- [ ] 音声テストデータの後片付け（S009・架空患者 8 名・`docs/tools/voice-test`）。済むまでカイポケ送信と週生成は注意。5 分おきの check-missing の actor が S009 になっている件もこれで解消。
- [ ] 高岡さんの資格不整合（請求区分変更 grade_change の初回実送信が未実施）。予実比較でも資格不整合 2 件。
- [ ] 8/31 誤展開事故の再発防止 A〜H（未実装・メモリ `careflow-incident-20260831-expand-wrong-month`）。

### コードでは直らない既存データ（PO と相談して手で）
- [ ] 9 月の日付変更 4 件のコースが元の曜日のまま。
- [ ] 川名さんの 9/23 予定外訪問 6 件（今は取込で自動昇格するが、過去分は再取込するか手で）。
- [ ] **らく助にだけ残っている 9 月の予定 33 件**（カイポケに予定も実績も無い。9/1〜9/5 の 24 件は 8/31〜9/3 の固定枠戻し reset_v2 週 36 の残り、9/11〜9/26 の 9 件は取込後にカイポケ側で消えた予定）。**PO「予定は QR の実績とは別。今回は対象外」**。取り消すかは別途判断。
- [ ] 利用者名の字体違い（カイポケ「齋藤 奈己」／らく助「齊藤 奈己」）。突合で取りこぼす原因 → データ整備の候補。

### 保留
- [ ] **取消・変更の記録（ダッシュボードに取消・変更の数、詳細に理由・変更日・操作した人）**: 設計 `visit-change-log-design-2026-10-01.md`・モック `docs/mockups/visit-change-log-mock.html`。**PO が保留**（今はカイポケ上でその都度ずらしているので取消か変更か区別できない。**取消・変更をどこで行うか・ずらしの扱い・スケジュール確定のタイミング**を先に決める）。再開前に決める 5 点は設計書の冒頭。理由の選択肢の案（取消 13・担当変更 10・移動 9）は PO が概ね了承。

### 別の事業所へ提供する準備の残り（急がない）
- [ ] #5 カイポケへ送るサービス内容の規則（精神科前提の 4 通り・RPA の正看固定）と訪問看護区分の既定値（**PO 判断**）
- [ ] #6 設定画面があるのに効いていない値（移動の余裕・人数上限・1 人 6 名・表示の時間帯と昼休み）
- [ ] #8 管理者→M・先頭の職員→A の固定割当のフラグ化
- [ ] #10 コースコードの固定（A〜E・M・臨の CHECK 制約）と距離の既定 12km
- [ ] カイポケ RPA 側（別リポジトリ PlaywrightTest1）の決め打ち調査
- 正典 = `docs/plans/multi-office-readiness-audit-2026-10-01.md`（残りの INAGE/TSUGA 等の記述はコメント・例のみと確認済み）

### 小さな後始末
- [ ] `openpyxl` を `backend/pyproject.toml` に追加（requirements.txt にはあるので本番は問題なし。作業ツリーで uv sync した venv では足りない）
- [ ] 既知の失敗テスト 29 件の整理（manager ロールが admin 扱いになった後に古いままのもの）
- [ ] 打刻済み訪問の時刻が固定訪問と違う日に「週を生成」が同じ日に 2 件目を作る（500 ではない・`copy-week-design` §8-10）
- [ ] 予定外訪問で、完了後に到着を再送すると 2 件目ができうる件 → 日またぎの修正で device_time による冪等化済み。残りなし（確認のみ）
- [ ] `.claude/worktrees/agent-*` 6 つの削除（`git worktree remove`、locked は `--force` 前に中身が develop に入っていることを確認）
- [ ] 実績時刻のカイポケ実績への反映・PDF の直接ダウンロード（やらないと決めたもの・必要になったら）

## 5. 気になる点・リスク

- **時計をずらした打刻**: 日またぎの受け付けで「端末の時刻」を信じる範囲が 3 日に広がった。抑え = 端末が 2 分以上進んでいれば不採用・72 時間超は拒否・前日以前に届いた到着は管理者へ通知＋モニターの要対応・打刻履歴で「遅れて届いた」を絞り込める・GPS は読んだ時点のものを判定。同じ日の 30 分未満の後ずらしは印が付かない（以前より改善はしている）。
- **取込で打刻済み訪問をカイポケ側で別日に移した場合**（PO 決定）: 取込は進め、元の日に残し、移動先にも入る → **一時的に両方の日に同じ利用者の訪問が並ぶ**。プレビュー・結果の「打刻済みのため日付を動かしていません（要確認）」を見て人が整理する運用。
- **予定外訪問の自動昇格**: 同じ利用者・同じ日・同じ担当・開始 60 分以内で 1 対 1（近い順）。担当が違うと昇格しない。
- **9/7 の 1 件はアプリの決まりを例外的に通さず書いた**（監査ログに method=SCRIPT で残っている）。
- **カイポケ実績に 30 分未満が 0 件**（事務の入力の傾向）。QR と比べて短いものが今後も出うる。9/18 13:00（髙梨さん）は QR 26 分・カイポケ 35 分だった（看護記録で 35 分として合わせ済み）。請求区分に関わるので、今後も一律の書き換えはしない。
- **打刻の取りこぼし**: 9 月は 81% が打刻なし（ただし 456/489 はカイポケに実績あり）。11 月に完全移行するまでに運用の定着が必要。
- **プレビューが終わった結果を戻ったときに出す**（30 分以内・未反映・後から取込が無いとき）。戻った後はイベントの読み込み（約 1 分の RPA）も自動で走る。RPA が使用中ならイベントの失敗として出る。
- **ダッシュボードの数字の前提**: 実績時間は QR が定着するまで参考値。距離は直線。2 名訪問はそれぞれに数える。休みの判定は勤務表と休みの上書きが正、題名は補助。
- **テストは SQLite**。PostgreSQL 固有のこと（部分一意インデックス・advisory lock・タイムゾーン）は結合確認で通しているが、新しい変更のたびに確認が要る。
- 空の DB に最初から migration を当てると 0012 で止まる（`alembic_version` が varchar(32)。本番は 64 に広げ済み）。結合確認では先に varchar(64) で作ってから当てる。
- Windows でフロントの本番ビルド（standalone）は権限で失敗する（本番は Linux コンテナなので無関係）。結合確認は `pnpm dev`。

## 6. 判断済みの事項（PO 決定・主なもの）

**打刻・実績**
- 実績は「合わせる／調整」。理由の入力は不要（撤去・DB の列と API は残す）。予定は動かさず実績だけ。
- 本人が合わせられるのは 7 日以内・到着は読み取りの 90 分前まで（管理者は下限なし）・退出は読み取りの 30 分後まで。値は `checkin_settings`（行なし＝既定値）で変えられる。
- 日をまたいで届いた打刻は**読んだ日で判断し 3 日以内なら受け付け**、「遅れて届いた」の印。
- 9 月の短い記録・到着だけの記録は**看護記録（カイポケ実績）の時刻に合わせる**（予定の時刻で埋めない・一律に 35 分にはしない）。読み取りが 1 件も無い訪問は「打刻なし（予定のみ）」として実績とはみなさない。
- 松岡様のご相談は QR の部分だけ。予定（らく助にだけある 33 件など）は別の話。

**訪問モニター**
- 行 = 職員（主担当 → コース担当 → 担当なし行）。地図・順路は行の下に開く。コースの札は行ヘッダとパネルだけ（カードには出さない）。略称は `offices.short_label`（稲・津）。行を開いたときのスクロールは今のまま。

**ダッシュボード**
- トップ = 人ごとのカード（B）、押すと 1 人を深く（C）。管理者だけ。出勤 = 訪問のあった日。距離は直線。会議・研修は合間から分ける。既存の数字は小さく残す。不在は件数に数え「うち不在 N 件」を別に。チームの実績時間 = 全員の QR 記録の平均。同行は「同行 N 日・N 件」を別の行（訪問件数に入れない）。

**前の週をコピー**
- 写すのは患者の予定（曜日・時刻・長さ）。**担当は写さない**（ローテーション）。時刻は予定として確定した時刻。固定訪問へは書かない。写した訪問は `source='manual_week'`。写す先に取込済み・打刻済み・青ピンの訪問がある（患者・日）は時刻が違っても写さない。担当が付かない訪問は担当なしで入れて一覧で知らせる。臨時コース配下は持ち主のテンプレートのコースへ（無ければコースなし）。

**カイポケ取込**
- 打刻済みの訪問は取込で取り消さない・日付も動かさない（要確認で知らせる）。担当 2 を外したときは取込で付いた同行だけ外す。予定外の訪問とカイポケの訪問は同じ担当・開始 60 分以内でまとめる。プレビューは裏で動かし、戻ったら 30 分以内の結果を出す。

**別の事業所**
- 事業所名・電話・ロゴ・拠点コード・略称をコードに書かない（`offices` と `business_profile`・設定から取る）。カルテ Excel の拠点名がマスタに無ければその行を止める（空欄は住所から）。スケジュールのコース表の行名は今のまま（稲毛-A）。

**保留・やらない**
- 取消・変更の記録 = 保留（運用とスケジュール確定のタイミングが先）。
- 到着の後入れ（打刻漏れの補完）= 11 月までに決める。

## 7. 関連ファイルのパス

**設計・記録（`docs/plans/`）**
| 内容 | ファイル |
|---|---|
| この総括 | `session-2026-10-01-HANDOFF.md` |
| 期間中の生ログ（細部） | `session-2026-09-30-HANDOFF.md` |
| 前回の総括 | `session-2026-09-24-HANDOFF.md` |
| 打刻履歴タブ | `visit-history-design-2026-09-30.md` |
| 実績の時刻を合わせる | `actual-time-adjust-design-2026-09-30.md`（§12 確定点） |
| 訪問モニター職員単位 | `monitor-staff-rows-design-2026-09-30.md`（§8 確定点） |
| ダッシュボード | `dashboard-staff-performance-design-2026-09-30.md`（§8・PO 決定） |
| 前の週をコピー | `copy-week-design-2026-09-30.md`（§8） |
| 別事業所の準備（点検と対応状況） | `multi-office-readiness-audit-2026-10-01.md` |
| 取消・変更の記録（保留） | `visit-change-log-design-2026-10-01.md` |
| 日またぎの受け付け | `checkin-late-delivery-design-2026-10-01.md` |
| 取込の修正 6 件 | `inbound-fixes-2026-10-01.md` |
| 取込プレビューの非同期化 | `smart-preview-async-2026-10-01.md` |

**モック（`docs/mockups/`）**: `visit-history-tab-mock.html`・`mobile-actual-time-fix-mock.html`・`monitor-staff-rows-mock.html`・`dashboard-staff-performance-mock.html`・`copy-week-mock.html`・`visit-change-log-mock.html`

**お客様向けの帳票（`docs/reports/`・git 管理外・利用者名あり・公開しない）**
- `2026-09-30-qr-history-2026-09.{html,pdf,xlsx}`（9/30 に渡した初版）
- `2026-10-01-qr-history-2026-09-v2.*`（28 件を合わせた後）／**`-v3.*`（最新・29 件）**
- `2026-10-01-qr-check-2026-09.xlsx`（確認表 初版）／`-v2.xlsx`（カイポケ実績の列つき・らく助にだけある予定 33 件のシートつき）

**道具**: `docs/tools/visit-history/extract.sql`・`build_report.py`（月次レポート）／`docs/tools/kaipoke-ops/three_way_check.py`（三者照合）／`backend/scripts/reattach_cancelled_checkins.py`（取込で取り消された訪問の打刻を付け替え・既定は確認のみ・`--from/--to` 必須で `--apply`）

**主なコード**
- 実績時刻の単一の源: `backend/app/services/checkin/actuals.py`（`resolve_read_time`・`load_actuals`・遅れて届いた判定）
- 時刻を合わせる: `backend/app/services/checkin/adjust.py`／API `backend/app/api/v1/visits.py`（`/actual-time`）
- 打刻の判定・日の判定: `backend/app/services/checkin/judge.py`（`_guard_visit`・`CheckinRejected` の code）
- 通知: `backend/app/services/checkin/notify.py`
- 打刻履歴: `backend/app/services/checkin/history.py`・`history_xlsx.py`・`history_report_html.py`／`backend/app/api/v1/visit_history.py`
- モニター: `backend/app/services/checkin/monitor.py`／`frontend/components/monitor/*`（`MonitorTimeline.tsx`・`MonitorRowPanel.tsx`）
- ダッシュボード: `backend/app/services/dashboard_staff_performance.py`／`frontend/components/dashboard/performance/*`
- 週コピー: `backend/app/services/scheduling/copy_week.py`・`jp_holidays.py`／`backend/app/api/v1/copy_week.py`／`frontend/components/schedule/v2/MakeWeekDialog.tsx`
- 拠点の略称: `backend/app/services/office_labels.py`／事業所の情報: `business_profile`（mig 0089）・`/settings/business`
- 取込: `backend/app/services/kaipoke/inbound.py`・`backend/app/services/diff/engine.py`／プレビューの非同期: `backend/app/api/v1/integrations.py`（`smart-inbound-preview/start|status|active`）・`frontend/app/(app)/integrations/kaipoke/_components/useInbound.ts`
- スマホの打刻キュー: `frontend/lib/checkin-queue.ts`・`checkin-flush.ts`

**メモリ**（`C:\Users\imaizumi.LINEWORKS-NET\.claude\projects\C--Users-imaizumi-LINEWORKS-NET-Documents-CareFlow02\memory\`）: `MEMORY.md`（索引）・`careflow-visit-history-and-time-adjust.md`・`careflow-monitor-course-rows.md`・`careflow-multi-office-readiness.md`・`careflow-visit-change-log-on-hold.md`・`careflow-deploy.md`・`careflow-session-handoff-index.md`

## 8. 次にやるべきことの順序

1. **現場への案内の確認**（PO が済ませたか聞く）と、**本番の取込画面でプレビューを 1 回**（PO と一緒に。読み取りだけ）。
2. **松岡様への 9 月分送付の状況確認**（実績表 v3）。お客様から追加の要望があれば対応。
3. **9/21 週の未反映 5 件**の扱いを PO に確認（必要なら取込。プレビューで要確認の行を見てから）。
4. **前の週をコピーの初回実運用に立ち会う**（自動割当のローテーション・「担当を手で付ける必要がある訪問」の一覧）。10/5 の週以降。
5. **10/26 の週（月跨ぎ）の取込**が非同期プレビューで問題なく動くかを見る。
6. **11 月の完全移行に向けた判断**: 到着の後入れ（打刻漏れの補完）を作るか → PO に選択式で。作るならモック → 設計 → 実装。
7. 前回からの残り: 音声テストデータの後片付け → 高岡さんの資格不整合（grade_change の初回実送信）→ 8/31 再発防止 A〜H。
8. 既存データの整理（9 月の日付変更 4 件のコース・川名さんの予定外 6 件・らく助にだけある予定 33 件・字体違い）を PO と相談。
9. 取消・変更の記録の再開判断（運用が決まったら）。
10. 別事業所の準備の残り（#5 は PO 判断から）。
11. 小さな後始末（openpyxl・古いテスト 29 件・作業ツリー削除）。

## 9. 手順メモ（本番）

- 接続: `ssh root@72.60.211.213`、コード `/opt/carelink`、公開 https://carelink.kaipoke-api.net。コンテナ `carelink-backend` / `carelink-frontend` / `carelink-postgres`。
- デプロイ: `git push origin develop` → `docker exec carelink-postgres pg_dump -U carelink -d carelink | gzip > backups/pre-deploy-<名前>-$(date +%Y%m%d-%H%M).sql.gz`（`gunzip -t` で検査）→ `git pull --ff-only origin develop`（HEAD 目視）→ `docker compose --env-file /opt/carelink/.env -f docs/deployment/docker-compose.production.yml build backend frontend` →（migration があれば）`... run --rm backend alembic upgrade head` を **recreate の前に** → `alembic heads` 単一 → `up -d --force-recreate backend frontend` → healthz（`127.0.0.1:18001/api/v1/healthz` と公開 URL）→ エラーログ確認。
- 本番コンテナで pytest を実行しない。
- 管理者としての読み取り確認は、コンテナ内で `create_access_token` で短命トークンを作り `http://127.0.0.1:8000/api/v1/...` を呼ぶ（スクリプトは使用後に消す）。
- Windows からのコマンド: PowerShell は引用符が崩れやすい。サーバー側の手順はファイルに書いて `cmd /c "ssh ... ""tr -d '\r' > /tmp/x.sh && bash /tmp/x.sh; shred -u /tmp/x.sh"" < file"` の形で流すと確実。日本語を含む Python はファイルに書いて `docker exec -i ... python - < file`。
- 作業ツリーで backend テストを流すとき: `uv sync --no-install-project --extra dev && uv pip install -r requirements.txt`、実行は `python -m pytest`（`uv run pytest` は使わない）。
