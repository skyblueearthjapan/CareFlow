# セッション引き継ぎ 2026-08-22〜24（運転席 Phase E 本番化 → 同期ストリップ → サービス内容 → 休み1操作化 → 担当なし投入提案）

**次のエージェントへ: まずこのファイルを読むこと。作業ツリーはクリーン（全コミット・全デプロイ済み）。進行中の未完了タスクは無い。**

| 項目 | 値 |
|---|---|
| 本番 HEAD | `9da5764`（develop・デプロイ済み・healthz 健全） |
| DB migration | **0078**（0075 staff_events.cancelled_at / 0076 kaipoke_csv_snapshots+correction_sheets.origin / 0077 patients.visit_category / 0078 visits.kaipoke_service_override） |
| RPA リポ（VPS /root/PlaywrightTest1・コンテナ kaipoke-api） | `4c5303c` |
| バックアップ | `/opt/carelink/backups/pre-deploy-20260823-*.sql.gz` ほか多数（サーバは UTC 表示） |
| 未追跡ファイル | `CareLink-handoff.zip` / `docs/HANDOFF.md`(2026-06 の古い要点集) / `docs/mockups/renkei-layout-wireframe.html` — 本セッション外の持ち込み。触らなくてよい |

デプロイ手順は従来どおり（runbook）。migration を含む時は `build --no-cache` + `docker exec carelink-backend alembic upgrade head`。FE 変更後は現場でハードリロード案内。

---

## 1. このセッションで本番化したもの（時系列）

| コミット | 内容 | 正典ドキュメント |
|---|---|---|
| `1441545` | **運転席 Phase E**: 急休代替(substitute-candidates)・今週だけ取消(visit-cancel-week・status='cancelled'+source='manual_cancel')・固定イベント週内除外(staff_events.cancelled_at)・●未送信+🔄突合の同期バー(kaipoke_csv_snapshots・unsent-summary=RPAなし)・横バータイムライン。mig 0075/0076 | `week-cockpit-design.md`（契約・決定事項 D1〜D8・§6 既知制約）/ `week-cockpit-investigation.md` / `week-cockpit-progress.md` |
| `8573a39` `90c1ee3` | 横スクロールを上の1本に統一（SyncedHScroll・下の既定バー非表示・空き部分ドラッグでパン・初回ヒント） | — |
| `b582d72` | 週間シフト既定 09:00〜18:00 + 月〜金/月〜土一括ボタン | — |
| `238fc20` `390dab5` `e89877c` | タイムライン拡大（横2400px・行54px・文字13px）・氏名列 sticky | — |
| `5d6e886` | スタッフ入れ替えDnD（⠿）+ タイムライン行アクション。**急休付替を PATCH /courses 経路から訪問単位(runAssignQueue)に統一**（undo整合の根治）。useConstraintConfirmRetry の連続422/中止対応 | — |
| `be6da50` | 突合の担当者名正規化・異体字 槇↔槙（8/17週の偽差分46件根治）。マスタ修正: 髙梨　桂子(看護師)・槙　恵 | `kaipoke-service-content-investigation.md` §1 |
| `42bf03e` `745deb8` | **カイポケ「サービス内容」S1+S2**: patients.visit_category(精神科/一般・mig 0077)・スタッフ資格UI・CSV出力=区分×職員1資格・diff前方一致・訪問単位上書き(mig 0078)・資格突合・RPA未対応通知・ペア保護(addスキップ時は対のdeleteも止める)・**送信ガード `KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=False`(未解除)** | `kaipoke-service-content-design.md`（§3-1 editでは直せない/§3-2 ガード解除手順） |
| `48bb5ee` `1387270` `e473b09` | 上部ツールバー折りたたみ（コンパクト表示・localStorage 永続）・診断/最適化は畳んでも1段目右端に常設 | — |
| `4a6c479` `5b57f82` `ed7b182` | **同期ストリップ（方向性A）**: 1行+3ボタン(⇩取り込む/⇧送る/🔄同期確認)・押した時だけカード行パネル・行内「何から何へ」・同期確認は開閉トグル(再実行は「再確認」・結果は畳んでも残る)・全パネルに らく助作業中演出 | `docs/mockups/sync-strip-mock.html` |
| `46a56d8` | スタッフ並びをコード順（S001川名→S002熊澤→S004高岡→S005本名→S006宇田川→S007髙梨→S008小西→担当なし）。入れ替えは「予定の交換」で行は動かない | — |
| `3a4e2cc` | **休みにする1操作化**: `POST /schedule/v2/staff-off-week`（休み登録+当日担当の付替+コース担当を1TX・同一op_group・新op set_staff_off / set_visit_staff_slot(2名体制の相方保持)）・モーダル確認(担当なしに戻す既定/丸ごと引受可の人のみ提案)・undo「これ以上戻せません」・削除済み患者の訪問除外(404根治) | — |
| `11152e3` | **Phase 2 担当なし投入提案**: `POST /schedule/v2/assign-candidates`(course_id/course_ids/visit_ids 排他・read-only・whole_ok_staff_ids/whole_ok_by_course・束間衝突検査・重い前処理1回)・「◎ 提案を見る」→コース帯バッジ→コース提案ポップオーバー(◎割当/△理由のみ/0名は1件ずつ)・訪問メニューに提案セクション・候補ホバーで行ハイライト・割当は訪問単位+成功時のみコース担当整合・週切替/盤面変更でキャッシュ破棄 | `unassigned-suggestions-design.md` / `docs/mockups/unassigned-suggestions-mock.html` |
| `9da5764` | 訪問メニュー幅 w-80・下部2ボタンを2行構成に | — |

RPA 側コミット: `d2f5cbd`(異体字 槇) / `4c5303c`(**S3**: service_type→サービス区分/基本療養費Ⅰ/職員資格を value で選択。`commands/probe_service_options.py`(選択肢採取) / `commands/dryrun_service_branch.py`(4パターン実画面確認・登録なし))。

## 2. 次にやること（優先順）

1. **准看1件の本番テスト → 送信ガード解除**: 通知「カイポケへ自動送信できない予定が N 件あります」が出たら、未来日の准看 add を1件 ⇧送信 → カイポケ画面で「精神基本療養費Ⅰ・准看」を目視 → OK なら VPS `.env` に `KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=True` → backend 再作成。手順は `kaipoke-service-content-design.md` §3-2。
2. **8/24 週の運用**: 取込→突合。8/26 植田様（高岡=准看なのにカイポケ正看）が「サービス内容のズレ」に出るので「この訪問だけカイポケに合わせる」（またはカイポケ側修正）。
3. **小西さん(S008)の資格設定**（運用開始時・スタッフ編集画面）。
4. バックログ（小粒）: 提案キャッシュの指紋は visits 軸のみ（スタッフイベントだけの変更は拾わない）/ タイムライン 2-D 本格版（候補の「ここに入れそう」ゴースト）/ イベントの削除方向送信（今週だけ外してもカイポケに残る）/ 月跨ぎ週の●未送信（現状フェイルクローズ）/ Phase D（突合の定期自動実行+ベル通知）。
5. 週空間以前からの持ち越し: `session-2026-08-22-HANDOFF.md` §4 参照（半休エンジン・都賀A誤紐付け24行・加藤/並木5枠・QR/同行実機ほか）。

## 3. 運用・遠隔オペの手口（本セッションで確立/更新）

- **admin トークン鋳造**: `docker exec carelink-backend python -c "from app.core.security import create_access_token; print(create_access_token(subject='<admin users.id>', role='admin'))"`（**キーワード引数必須**）。
- VPS のスクリプト: `/tmp/unsent_check.sh`（未送信サマリ）/ `/tmp/apply_override.sh`（訪問単位のサービス内容上書き）/ `/tmp/live_check.sh`（RPA稼働状況）。
- 突合の裏取り: `kaipoke_jobs`（`params->>'op'`＝events-preview/smart-preview/diff-inbound/diff-local/apply）と `correction_sheets/items` を psql で。**「確認中」が長い時はまずジョブ完了を確認**（イベント33s+訪問70s+全曜日差分=計2〜3分が正常）。
- op_log テーブル名は `schedule_op_log`。「今週だけ取消」= status='cancelled' + source='manual_cancel'（取込 delete 由来と区別・取込 add で復活しない）。

## 4. 教訓（次のエージェントへ）

1. **並行 executor への指示に「git 操作禁止・stash も禁止」を毎回明記**。それでも2回起きた（いずれも即 pop 復元。`git stash list` と成果物の実在確認を必ず行う）。
2. レビューで繰り返し出た欠陥型: (a) コース単位付替は undo/manual_staff_override と相性が悪い → **訪問単位に統一** (b) 差分の add をスキップするなら**対の delete も止める** (c) VSA は全削除でなく**本人の行だけ**差し替える (d) FE の集計と BE の対象集合（planned のみ等）を**契約で一致**させる (e) 部分一致(`in`)は前方一致に（「基本療養費Ⅰ」⊂「精神基本療養費Ⅰ」の偽一致）。
3. jsdom: PointerEvent 無し→素の Event に座標 / ResizeObserver スタブ / scrollWidth は prototype に defineProperty / Radix Trigger の span はキーボードで開かない（onKeyDown を自前で）。
4. **既知ベースライン fail**: BE 32件（manager ロール廃止系 RBAC・audit middleware・reset-to-fixed 2件・sqlite フレーク2件。`git archive HEAD` の複製で突合済み）/ FE 1件（middleware の manager）+ `BulkPoolInsertDialog` 並列フレーク + e2e/*.spec の vitest 収集エラー。**増減だけを見る**。
5. デプロイの SSH 切断対策: リモートで `nohup sh -c "build && up -d && echo DEPLOY_DONE" > /tmp/deploy-X.log &` → 別 ssh で `until grep DEPLOY_DONE`。
6. ssh 経由 psql の引用符は事故りやすい → スクリプトを scp してから実行が確実。

## 5. ドキュメント地図

- 正典（週空間/運転席）: `weekly-space-design.md`（§8 に Phase E 追記済み）→ `week-cockpit-design.md` → `week-cockpit-investigation.md` / `week-cockpit-progress.md`
- カイポケ サービス内容: `kaipoke-service-content-investigation.md` → `kaipoke-service-content-design.md`（S1〜S3・送信ガード解除手順）
- 担当なし提案: `unassigned-suggestions-design.md`
- お客様向け資料: `docs/mockups/kaipoke-service-type-proposal.html`（A4 1枚・印刷対応）
- モック: `sync-strip-mock.html` / `staff-schedule-week-cockpit-mock.html` / `staff-schedule-reconcile-mock.html` / `unassigned-suggestions-mock.html`
- 前セッション: `session-2026-08-22-HANDOFF.md`（週空間 A1〜M）
