# セッション引き継ぎ 2026-08-31（8月誤展開の日・9月第1週反映・准看対応・実現性チェック実装）

**次のエージェントへ: まずこのファイルを読むこと。** 前セッション総括は `session-2026-08-25b-HANDOFF.md`。
本日の詳細な時系列は `incident-2026-08-31-kaipoke-expand-wrong-month.md`（§7 に午後の作業・§6 に見つけた穴）。

| 項目 | 値 |
|---|---|
| 本番 HEAD | `809de09`（コード変更なし。**本番 `.env` に `KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=True` を追記し backend 再作成済み** = 准看/一般のサービス内容分岐が有効・S3 完了） |
| 未コミット | `docs/plans/incident-2026-08-31-*.md` / `backlog-2026-08-31-patient-status-leak.md` / `feasibility-check-design.md` / 本ファイル / **実現性チェック機能一式（下記・テスト緑・レビュー APPROVE・未デプロイ）** / `kaipoke-service-content-design.md` 更新 |
| 保全データ | `C:\Users\imaizumi.LINEWORKS-NET\Documents\kaipoke-2026-08-backup\`（8/24 22:00 の 8 月 CSV・誤展開結果・9 月前後 CSV・突合一覧・実現性チェック各版・`tools/`） |
| 本番バックアップ | `/opt/carelink/backups/pre-pool-place-20260831-1126.sql.gz`（最新）ほか当日分 |

## 0. 本日の到達点（要約）
1. 10:31 川名様が誤って **8 月の月間展開**を実行（105 名中 70 名上書き）。崩れる前の最後の 8 月 CSV = 8/24 22:00（519 行）を保全。
2. 12:21〜12:37 **9 月展開**を正しく実施（新規 76）。
3. 14:08〜15:07 **らく助→カイポケ 9/1〜9/5 反映**（訪問 73 + イベント 23）。8 月は export バイト一致で無変更を証明。
4. 15:22〜16:50 **准看対応（S3）**: 実機テスト合格 → 門を開放 → 高岡さん 14 件を自動＋手作業で准看化。RPA の連動セレクト待ち不足で一時失敗連鎖（§4 教訓）。
5. 16:00〜 **プール 10 枠を M コース（担当なし）へ投入**（プール 0 件化）。
6. 19:50〜21:10 **実現性チェック**（移動/重なり/バッファ/同住所）をスクリプトで 4 回運用 → **らく助の機能として実装**（未デプロイ）。
7. 調査のみ: 非 active 患者が予定に残る問題（`backlog-2026-08-31-patient-status-leak.md`）、ログイン制限が事業所全員に効く問題（同ファイル末尾）。

## 1. 残タスク ①: 患者ステータス非表示（入院・解約・休止で予定から消す）
正典 = `backlog-2026-08-31-patient-status-leak.md`（原因・残置一覧・対応案 A〜D）。
- **原因**: 週生成は生成時点の `patients.status='active'` しか見ず、ステータス変更は生成済み訪問に波及しない。特別訪問枠プールも患者 status を見ない。
- **残置（要運用対処）**: 8/31 の 3 件（朝倉・藤田守・小川・planned）、藤原 9/3・9/4（入院後に特別枠から配置）、**W42（10/12 週）に 17 件**。カイポケ 9 月にも同患者（小川 13 行ほか）。
- **実装の推奨順（案 A → B → C）**:
  1. `PUT /patients/{id}`（`backend/app/api/v1/patients.py`）で status が active 以外へ変わったら、当日以降の `planned` 訪問を `status='cancelled', source='manual_cancel'` にし、`special_visit_periods.status` を ended にする。件数を応答に含め、FE の患者編集で確認ダイアログ（「N 件の予定を取消します」）。監査ログ。
  2. FE: `CourseDayTablePanel` の盤面・週ビュー・プール・`SpecialTicketPlacePanel` で `patient.status !== 'active'` を除外（またはグレー表示）。
  3. 週生成の対象週を「今週＋翌週」に制限 or 再生成時に非 active を掃除（`layer1_expander`）。
  4. 突合（🔄）に「らく助側 非 active 患者のカイポケ行」を削除候補として出す（`local_diff`）。
- テスト観点: status 変更→未来 planned が cancelled / 過去・完了は不変 / 特別期間 ended / 元に戻した (active) とき何もしない。

## 2. 残タスク ②: 9/1〜9/5 の同期（らく助が正 → カイポケ）
- **現状（21:30 JST 突合・シート `d0617a32`）: 差分 80 件 = edit 43 / add 22 / delete 14 / date_change 1**。川名様が 17 時以降に大きく並べ替えたため、14 時に同期した内容から再びずれている。准看（高岡さん）は門が開いているので自動で送れる。
- **必ず守ること**: 8/31 は変更しない。`POST /integrations/apply` には「**当日以前の日付は送らない**」ガードがある（`jst_today`）。**9/1 になると 9/1 が送れなくなる**ので、9/1 分を含めるなら 8/31 中に実行、9/1 以降なら 9/2〜 だけになる（9/1 は手作業）。
- **手順（本日 14 時と同じ・§4 の教訓込み）**:
  1. 川名様の編集完了を確認（同時編集は「予定が見つかりません」で失敗する）。`pg_dump` を取る。
  2. `export 2026-08` を取り、作業前ベースラインとして保存（作業後にバイト比較 = 8/31 不変の証明）。
  3. `diff-local {month:2026-09, weekStart:2026-09-01, weekEnd:2026-09-05}` → シート。項目を確認: `after.staff1 == '-'`（担当未定）は除外、日付 31 が 0 件であること。
  4. **追加（add）を先に**送る → export → 次に edit/date_change/delete。理由: RPA は「削除→追加」順で処理し、追加が失敗すると訪問が欠落する（本日 木村・長尾で発生）。
  5. 各バッチ後に `diff-local` で残差確認。**同一時刻の重なりや移動不可**は事前に `run_feasibility.sh` で洗っておく（川名様に直してもらう）。
  6. イベント: `events-inbound-preview`（カイポケ現況）→ `events-outbound-preview` → 9/1〜9/5 の sendable のうち **カイポケに既にあるもの（タイトル表記違いは二重になる）を除外**して `events-outbound-apply/start`。小西さんは職員 ID 未対応で送れない。
  7. 最終 `diff-local` と `export 2026-08` バイト比較、突合一覧 HTML（`tools/` のスクリプト参照）を残す。
- API の叩き方: `tools/admin_call.py` を backend コンテナで `python - METHOD PATH [JSON]`（今泉アカウントの JWT を内部発行・reconcile cron と同方式）。例は incident 文書 §7。

## 3. 残タスク ③: 8 月スケジュールの実績合わせ（今晩・8/1〜8/30・8/31 は対象外）
**目的**: 誤展開で崩れた 8 月の「予定」を、カイポケの「実績」に合わせて直す（請求は実績ベース。8 月は月締め前）。
- **素材**
  - 崩れる前の予定: `kaipoke_2026-08_schedule_20260824-2200JST_original_cp932.csv`（519 行・8/24 22:00）
  - 崩れた現状: `kaipoke_2026-08_schedule_CURRENT-after-misexpand_20260831-1400JST_cp932.csv`（620 行）＋ `kaipoke_2026-08_expand_result_20260831-1031_utf8bom.csv`（上書き 70 名の一覧）
  - **実績 CSV（未取得）**: カイポケ「各種情報出力 › スケジュール表」で **予定実績区分＝実績** を選んで CSV 出力。RPA の export は 予定 固定（ラジオ `input[name=planAchievementsDivision]`: `#planAchievementsDivision01`=予定 / `#planAchievementsDivision02`=実績）。**RPA で取るなら `commands/export.py` の `set_export_month` の後に `page.check('#planAchievementsDivision02')` を足すだけ**（要 RPA 再起動 `supervisorctl restart api`）。急ぐなら PO がブラウザで DL しても良い（cp932・同 18 列）。
- **実績 CSV（PO 提供・2026-08-31 22:30）**: `CareFlow02\スケジュールデータ\8月実績データ\訪問看護スケジュール_202608.csv`（cp932・523 行・**21 列** = 18 列 + 記録Ⅱ開始/終了/提供時間・患者 72 名・正看 439/准看 78/基本 6・職員名2 あり 26 行）。差分エンジンは列位置依存なので **18 列に正規化したコピー** `kaipoke-2026-08-backup\kaipoke_2026-08_JISSEKI_18cols_cp932.csv` を使う。
  - **突合の鍵は「日付＋利用者」**（開始時刻は実績＝実際の打刻寄りで予定とずれる: 8/24 予定と比べ 0 分 211 / ≤15 分 195 / ≤60 分 53 / >60 分 23）。8/24 予定とは 日付+利用者 で 482 件一致（実績のみ 14・予定のみ 10・担当違い 15・サービス違い 12）＝ **8/24 の予定は実績にほぼ一致**。崩れた現状予定とは 420 件一致だが **担当違い 347・サービス違い 72・現状のみ 174・実績のみ 76** ＝ 修正規模は 500 件超。
  - 正看優先ルール（`tools/grade_rule.py`）を実績に当てると変わるのは 2 行（8/26 川上様・8/28 久須見様: 高岡単独なのに正看表記 → 准看対応）。
  - **RPA で 500 件を 1 件ずつ直すのは非現実的（40 秒/件 ≒ 5〜6 時間）**。先に試すべき近道: カイポケ「月間スケジュール管理 › 利用者別」画面のボタン **「予実管理の実績と比較・取込」**（本日のスクショに存在。実績を予定へ取り込む純正機能の可能性）。PO が 1 名で挙動を目視確認 → 使えるなら 72 名分をボタン操作（RPA 化は `expand.py` の巡回ループを流用可）。それでも残る差分だけを CSV 差分 → `grade_rule` → RPA で当てる。
- **差分の作り方**: `backend/app/services/diff/engine.py` の `compare_schedules_from_content(current=現状予定CSV, target=実績CSV, target_week_start=1, target_week_end=30, normalize_names=True)` → Correction 一覧（add/delete/edit/date_change）。8/31 を除くため日 1〜30 で絞る。実績が無い日（未実施・欠勤）は「実績に無い＝削除」になる点に注意（実績 0 の患者が丸ごと delete になる → PO と要確認）。
- **適用の経路（過去日）**: らく助の `POST /integrations/apply` は過去日を送らない（`skipped_past`）。過去日を書くには **RPA `/api/apply` を直接**（`{"correction_data":[...], "month":"2026-08", "dry_run":true|false}`・トークンは `docker exec carelink-backend printenv KAIPOKE_API_TOKEN`・本日の dry-run 例は incident §7-b）。RPA 側に日付ガードは無い。**必ず dry_run → 少量バッチ（10 件程度）→ export で確認**。カイポケの応答が遅い時間帯は連動セレクトが展開せず失敗が連鎖する（§4）。
- **代替**: 件数が多ければ「実績に合わせて手作業」のチェックリストを A4 で作る（本日の `手作業チェックリスト_2026-09第1週_准看分.html` と同じ作り）。
- **やらないこと**: 8/31 の変更・イベント（個別業務は展開の影響なし）・9 月。
- **ルールの自動適用（今晩から使える）**: `Documents\kaipoke-2026-08-backup\tools\grade_rule.py` — カイポケ CSV 行用 `apply_rule_to_csv_row(row)` と Correction 用 `apply_rule_to_correction(c, qual_by_name)`（自己テスト 6 ケース済み・`python grade_rule.py`）。実績 CSV → 差分 → RPA へ渡す前に 1 件ずつ通す。**サービス内容が変わる修正は RPA では edit できないので delete+add で送る**（`rpa_capability` の説明どおり）。
- **PO 確定ルール（請求区分と職員1・2026-08-31 夜）**: ①正看＋准看が同じ患者に行く（同行/2 名体制）→ **正看を職員1・正看対応** ②准看 1 名 → 准看が職員1・准看対応 ③正看 1 名 → 正看対応。8 月の修正でも、9 月以降の CSV 生成でもこの表に従う（詳細と現行実装のずれ = `kaipoke-service-content-design.md` §3-3）。

## 3-e. 残タスク ⑦: 「正看＋准看の同行は正看を職員1」ルールの実装
現行 `csv_builder.resolve_service_content` は職員1の資格だけで准看/正看を決める（同行は影響しない）→ 准看コースに正看が同行すると准看対応で出力される。`csv_builder` の行生成で正看を職員名1へ昇格（准看を職員名2）し grade を正看にする。`local_diff` も同じ配分を通す。テスト 5 ケース。UI は「准看コースに正看同行」を警告（止めない）。正典 = `kaipoke-service-content-design.md` §3-3。

## 3-b. 残タスク ④: 提案系（診断・最適化・配置改善・プール投入提案）が固定訪問スケジュール基準になっている問題
PO 指摘（2026-08-31 夜）: スケジュール画面の各提案が PFV（毎週の型）を基準に計算され、8/31 週のように現場で大きく手直しした「今週の実スケジュール」と食い違う提案が出る。**「今週の予定」を基準に提案できるか**の徹底調査を依頼 → 調査報告 = `proposal-source-investigation-2026-08-31.md`（機能別の入力ソース・手編集の可視性・今週基準化の変更点と規模・設計原則「PFV 正／二層分離」との整合・段階案）。次のエージェントはこの報告を読んでから PO と方針（どの提案を今週基準にするか）を決める。

## 3-c. 残タスク ⑤: プール／特別訪問枠の患者を「必ず」スケジュールに落とし込める経路
PO 指摘（2026-08-31 夜・業務要件）: 枠が無くても（担当なし・定員超過・移動不可でも）プールの患者を盤面に入れる経路が必要。本日は候補 0 件（`no_gap`×3・`capacity_full`×1）で UI が行き止まりになり、`place-and-fix` を API 直叩きして M コース（担当なし）へ入れて回避した（都賀患者は拠点不一致 422 → 都賀 M へ）。特別訪問枠チケットはドラッグ不可の設計。調査報告 = `pool-placement-blockers-investigation-2026-08-31.md`（投入経路一覧・行き止まりの正確な理由・「強制配置」の設計案・残すガード・段階案）。次のエージェントは報告を読み、PO と「強制配置ボタン／M コース候補化／チケットの DnD」の優先順を決めてから実装する。
**PO 決定（2026-08-31 22:40）: 受け皿は M コース（担当なし）。拠点跨ぎは通さず、患者の自拠点の M へ誘導する。** → 実装順は F-1（候補 0 件の画面に「担当なし(M)へ入れる」ボタン＝自拠点 M テンプレで `place-and-fix fix_pattern=false`・FE のみ）→ F-2（⭐チケットの `place` に `course_template_id` を足して M コースを自動生成し、同じボタンを⭐にも出す）。未決 = 強行時の理由入力を必須にするか／担当なし訪問のカイポケ送信を止めるか警告か（RPA は担当 `-` を扱えないので当面は送信前に担当を付ける運用）。

## 3-d. 残タスク ⑥: 新人（小西さん）を熊澤さんへ終日同行で付けられない
**★先行対応済み（2026-08-31 23:55 JST・PO 指示「9/1〜9/5 の小西さんの同行を全て熊澤さんへ」）**: 調査書 §6 回避策 1 を **訪問単位**で実施。理由 = 熊澤さんの W36 コース（9/1 C・9/2 A・9/3 A・9/4 B）には川名・髙梨・本名・高岡・宇田川の訪問が混在しており（これが「熊澤さん自身の重なり」の正体）、コース単位のリンクだと他スタッフの訪問にも小西さんが付いてしまう。また熊澤さんは M・川名 B/D・髙梨 A の訪問も 6 件持つ。→ `primary_staff_id=熊澤 AND visit_date 9/1〜9/5 AND status<>'cancelled'` の **20 件**（9/1 3・9/2 6・9/3 5・9/4 6・9/5 0＝休み）へ `accompaniments(target_type='visit', source='manual', kind='trainee', created_by=今泉)` を INSERT（`uq_acc_staff_visit` で冪等・バックアップ `/opt/carelink/backups/pre-accomp-konishi-20260831-1351.sql.gz`）。小西さんは元々リンク 0 件・コース 0・既定 0（＝「変更」ではなく新規付与）。8/31 は対象外。
- 注意: 訪問単位なので **この後 熊澤さんに新しく足された訪問には自動で付かない**（同行モードで小西さんを選ぶと 20 件が選択済みで出る。［キャンセル］で抜ければ無事）。W36 の週生成／固定枠に戻すはリンクを消すので実行しない。
- カイポケへの反映: 月次 CSV・週次差分（edit）とも職員名2＝小西で出る（§5-4）。小西さんのカイポケ職員 ID が RPA 側で解決できるかは未確認（イベントは未対応）。9/1〜9/5 再同期（残タスク②）のときに 20 件が edit として出るので、その時に確認する。

PO 指摘（2026-08-31 夜）: 9/1〜9/5 に小西さんを熊澤さんの同行として登録しようとすると、熊澤さん自身の予定に時間の重なりがあるため同行登録がエラーで弾かれる。要件 = **一人のスタッフに終日付ける場合は、そのスタッフ内の重なりで同行を止めない**（複数スタッフに混在して付ける場合の重なり拒否は正しい）。カイポケにも職員名2として載せたい。調査報告 = `accompaniment-overlap-investigation-2026-08-31.md`（登録経路と検証コード・重なりの定義・緩和設計・カイポケ職員名2 への反映可否・今週の暫定回避策）。
**調査結果の要点**: エラーは FE が［確定］を無効化して止めており（`useAccompanimentController.canConfirm`）、BE も同じ検査で 422（`accompaniments.py` 時間重複判定・決定#1）＝ FE だけ緩めても通らない。重なりは「熊澤さん自身の持ち予定どうし」で、`find_time_overlaps` は持ち主を見ない（免除は同住所ペアのみ）。これは trainee 設計の PO 決定#6 どおりなので、今回の要望は決定の部分撤回。推奨 = **案R「持ち主（コース担当）が同じ重なりは免除、別スタッフ混在は 422 のまま」**（BE `collect_accompaniment_conflicts` に owner 解決を挿す・FE entry に ownerStaffId・規模 M・migration 不要・テスト 10 本）。FE 側に**取消済み訪問を除外していない非対称**があり、急休代替の週は FE だけ偽の重なりが出る（要修正）。**今週の回避策**: 報告 §6 の SQL で `accompaniments` へコース単位リンクを直接 INSERT（冪等・盤面/モバイル/CSV に即反映。W36 を再生成すると消える点に注意）。カイポケへは職員名2 として週次差分で送れる（2 名体制の訪問は職員名3 に落ちて週次に載らない → 手入力）。`accompaniment_defaults` は次の週生成から効くので 9/8 週以降の恒久設定に使う。

## 4. 教訓（本日）
1. **展開（expand）は月選択＝週選択の月**。8/31 週を選ぶと 2026-08 になる。展開前に export を必ず取る。
2. **RPA export は「スケジュール表」クリックで間欠タイムアウト**（本日 13 回中 8 回失敗）。失敗しても `data/current_{month}.csv` の**古い内容を csv_content として返す**ので、`CSV出力完了` のログを見て成否を判断する（backend はそれを「今の現況」と誤認する）。
3. **RPA apply はカイポケの連動セレクトが遅いと失敗連鎖**（`inPopupEstimate2/3` が 1 option のまま・時刻欄 disabled）。追加を先に、削除は後に、少量ずつ。成功時は `Estimate2 options=7 / Estimate3 options=4` がログに出る。
4. **カイポケで職員だけ差し替えるとサービス内容が正看のまま**（edit は算定区分を変えない）。准看化は削除→新規追加。
5. **`_reconcile_latest_job` は最新ジョブしか settle しない**（`kaipoke_jobs` に running 残骸 7 件。実害なし）。
6. **ログイン制限 5 回/15 分が事業所全員共通**（frontend 経由で同一 IP）。429 が出たら 15 分待つ。
7. **個別業務パーサは `btnIndividual` 付きしか拾わない**（色区分 color05 の予定が消えたように見える）。
8. **同時編集**: 川名様が盤面/カイポケを触っている間の自動反映は失敗しやすい。必ず声掛け。

## 5. 実現性チェック機能（実装済み・未デプロイ）
正典 = `feasibility-check-design.md`。BE `services/scheduling/feasibility_check.py` + `feasibility_report_html.py` + `api/v1/feasibility.py`（`GET /api/v1/schedule/v2/feasibility-report`・admin・read-only）、FE `components/schedule/FeasibilityCheckButton.tsx`（週セレクタ横・折りたたみ時はコンパクト行）。テスト BE 17 / FE 4、既存 950 緑、レビュー（opus）3 巡 APPROVE。
- **デプロイ手順**（migration なし）: コミット（`git add` は対象ファイルを明示・`-A` 禁止）→ push → VPS で pg_dump → `git pull --ff-only` → `docker compose ... build backend frontend` → `up -d --force-recreate backend frontend` → healthz。frontend は現場でハードリロード。
- **デプロイ後の確認**: `admin_call.py GET "/api/v1/schedule/v2/feasibility-report?iso_year=2026&iso_week=36"` の件数が `tools/run_feasibility.sh` の最終結果（重なり 5 / 移動不可 5 / バッファ不足 5 / 要注意 6 / 同住所 4 / 昼休み 6・21:07 時点）と概ね一致すること（担当解決がコース優先・同行テーブル込みになった分の差はあり得る）。

## 5-b. 併せて修正済み（未デプロイ・同じコミットに含める）: スタッフの「資格」まわり 2 件
PO 指摘（2026-08-31 23:00）: スタッフマスタ一覧が全員「資格未設定」。DB には資格が入っている（看護師 5・准看護師 1・小西さんのみ NULL）。

1. **表示バグ**: `GET /api/v1/staff`（一覧/詳細）の v1 `StaffRead`（`backend/app/schemas/staff.py`）に `qualification` が無く応答から落ちていた（編集画面は v2 スキーマ経由で表示される）。→ `StaffRead.qualification: str | None` を追加、`tests/test_staff.py::test_staff_list_and_get_include_qualification`。
2. **保存バグ（より重い・S1 42bf03e 以来 2026-08-22〜）**: スタッフ編集ページ（`frontend/app/(app)/staff/[id]/edit/page.tsx::toPayload`）は常に `qualification` を `PATCH /api/v1/staff/{id}` に送るが、v1 `StaffUpdate` は `extra="forbid"` で `qualification` を持たず **422 extra_forbidden** になる = **スタッフ編集ページの保存が全滅していた**（本番で `{"qualification":"看護師"}` を PATCH して 422 を実証）。→ `StaffBase`/`StaffUpdate` に `qualification: QualificationV2 | None`（Literal 5 値・v2 と同一定義）を追加。PATCH ハンドラは `model_dump(exclude_unset=True)`→`setattr` なので追加のみで保存される。テスト `test_staff_patch_accepts_qualification`（200 / 未知値 422 / null で解除）・`test_staff_create_accepts_qualification`。`tests/test_staff.py` 10 件 green・ruff clean。
   - デプロイ後の確認: 任意スタッフの編集ページで何も変えずに「保存」→ 成功トースト（従来は失敗）。
3. **小西さん = 看護師（PO 指示 2026-08-31 23:45）**: API が未デプロイのため本番 DB を直接更新済み: `UPDATE staff SET qualification='看護師', updated_at=now() WHERE id='cdc014b5-c84a-48ea-9332-ab0ada023269' AND qualification IS NULL` → `UPDATE 1`、`SELECT` で `小西彩稀|看護師|3` を確認。デプロイ後は UI から編集可能。

## 6. 既知の残骸・小タスク
- `kaipoke_jobs` running 残骸: expand `35590334`(10 月・7/5) / `e540bfec`(9 月) / export `495e50ca` `42cace93` `a70282f5` `2224af2d` / dry-run apply `00fbdab1`。
- 全 pytest で **既存失敗**: `test_audit_log_middleware::test_middleware_records_mutation_with_redacted_body`（staff 作成に username 必須の仕様変更へ未追随）ほか（本日の変更と無関係）。
- `docs/HANDOFF.md`, `docs/manuals/`, `docs/mockups/renkei-layout-wireframe.html`, `CareLink-handoff.zip` は以前からの未追跡ファイル（コミット対象外）。
