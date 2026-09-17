# セッション引き継ぎ 2026-09-16（スマホ盤の職員スケジュール不一致 → 調査 → 設計 → 実装 → 本番稼働）

**次のエージェントへ**: カイポケ連携の本線は引き続き `session-2026-09-11-HANDOFF.md`（W38 方針・grade_change 初回・月跨ぎ週・残骸ジョブ）。本ファイルは 9/16 の「スマホ盤にカイポケ/PC の担当が反映されない」対応の総括。

## ★ 最初の 3 分
0. **本番稼働**: らく助 `58ca484`（2026-09-16 12:30 JST・migration 無し・backend/frontend 再作成・healthz 200 両方）。RPA 変更なし `0e00107`。バックアップ `/opt/carelink/backups/pre-deploy-mobile-staff-schedule-20260916-0322.sql.gz`。
1. **発端**: PO 松岡様 9/16 09:27「個別の看護師のスケジュールにカイポケの情報が反映されていない」。社内仮説「スマホが PFV と連動」は **不成立**（スマホは visits.primary_staff_id を読む）。
2. **真因①**: 9/15 の W38/W39 カイポケ取込で訪問 apply が 422（置換ガードが「らく助側の取消」を見て止めたが、その枠は事務がカイポケ側で先に消していた）。イベント取込だけ成功し UI は「取り込み完了」を出した。→ **根治 `18fa5f8`**（ガードをカイポケ現況に残る取消だけに限定・失敗ジョブ記録・部分失敗を成功と見せない UI）。
3. **真因②**: 9/3 15:59 プール一括投入で W38〜W40 のコース A(水・高岡) 7 件×3 が主担当 NULL（ミラー修正 f90ee71 の 4 時間前の操作・修復 SQL は W37 のみ）+ 麻生様 9/14 1 件（place-and-fix が primary を書かない）。→ **根治 `da68223`**（可視性にコース担当フォールバック・place-and-fix で埋める・実現性チェックで検出）。
4. **新機能 `043fb0e`**: スマホ 今日/今週に職員イベント＋休み/時間変更を合成表示（PC 職員スケジュールタブと同じ集合）。手入力朝会×取込朝会の二重は表示側で畳む。
5. **本番実測（読み取り）**: 高岡さん 9/16 = 改修前 6 件 → 改修後 **13 件**（A 7 件は primary NULL のまま staff_name=高岡 で出る）。週 29 件。

## 1. 正典
- 調査書: `docs/plans/mobile-staff-schedule-mismatch-investigation-2026-09-16.md`（§0〜§4 初回・§5〜§9 追補）
- 設計書: `docs/plans/mobile-staff-schedule-design-2026-09-16.md`（レビュー決定を反映済み）
- memory: `careflow-mobile-staff-schedule-mismatch`

## 2. コミット（origin/develop に push 済み・本番 HEAD 58ca484）
| コミット | 内容 |
|---|---|
| `18fa5f8` | fix(kaipoke): 置換ガード限定（非稼働患者・名寄せ不能は一致なし扱い）／smart-apply 422 を failed ジョブ記録／FE 部分失敗 UI（Alert・❸無効化・履歴の理由） |
| `da68223` | fix(visits): 可視性フォールバック（一覧/詳細/打刻/QR resolve・manual_staff_override=false・在籍中コース担当のみ）／staff_name フォールバック／place-and-fix primary／feasibility finding「主担当なし(コース担当あり)」 |
| `043fb0e` | feat(mobile): useMyStaffEvents/useMyOverrides（行単位 safeParse）／foldStaffEvents（生存行優先→kaipoke>manual>fixed）／MobileEventChip／this-week・today 合成表示 |
| `58ca484` | docs: 調査書・設計書 |

## 3. テスト・レビュー
- code-reviewer（Opus）: 初回 REQUEST CHANGES（HIGH 3/MEDIUM 6/LOW 7）→ 全 HIGH/MEDIUM と LOW 大半を是正 → **APPROVE**。残 LOW（見送り）: `_fail_smart_apply` の detail 重複、FE の小ヘルパ重複、休職者コース担当の NULL 訪問が「見えない・送らない・報告もされない」（csv_builder と同規則。別 kind の finding で将来埋める）。
- backend 全体: fail 29（ベースライン 30 − 既知フレーク 1・変更ファイル内の失敗 0）。frontend 全体: 既知 2（middleware manager・PatientFixedVisitsPanel の日付依存）+ e2e 9（vitest では常に失敗）以外緑。tsc 0。

## 4. PO 判断待ち（未実施・データ変更なし）
1. **W38 方針**（らく助正／カイポケ正）。`18fa5f8` で小湊様の取消はブロックされなくなったので、カイポケ正なら取込を再実行できる（RPA idle を確認・取込前に 🔄突合で差分件数を見る）。
2. **主担当 NULL 22 件の修復 SQL**（設計書 §6・退職者除外付き・pg_dump 後）。スマホ表示と送信は既にフォールバックで直っているため急がない。実現性チェックで △「主担当なし(コース担当あり)」として見える。
3. **朝会の二重**（manual 31 × kaipoke 24）: 送信で昇格させるか manual を削除。スマホは畳んで 1 件表示。
4. **畳み込みの優先順位**「生存行優先」（設計書 §3 C-2）の確認。
5. **実機確認**: /m/today・/m/this-week のイベント・休みバッジ・「取消」バッジ・休みだけの日の見出し（PWA は初回ハードリロード推奨）。

## 5. 教訓
- 「カイポケが反映されない」は audit_logs の apply の HTTP ステータス + visits の source='import' の有無で即答できる。
- 部分失敗（訪問 422 → イベント 200）を成功トーストで隠す UI は事故になる。
- 「表示の正典=コース担当・visits はミラー」の規則は、可視性フィルタ・CSV・feasibility の 3 箇所で同じ条件（override=false・在籍中）に揃えておく。
- ミラー修正を出したら、修正前に作られた週の修復（W38〜W40）まで確認する。

## 6. 追補（同日 15:07 JST）: W38 をカイポケ正で取込 → 実施・検証・ダメ出し
- **実施**: pg_dump `backups/pre-inbound-w38-20260916-0605.sql.gz` → smart-preview（12:43 / 15:06、137 行）→ smart-apply 15:07（sheet d379422d）。9/15 = 差分（取消 4・更新 12・追加 1・失敗 0）、9/14・9/16〜19 = 置換（削除 125・挿入 114・臨時コース 6・スキップ 0）。復元用 `inbound_snapshots` 9f706e51（W38・smart）。
- **検証（読み取り）**: 突合 = カイポケ 137 行 ⇔ らく助 137 件 **全一致**（取消 4 件はカイポケに無い 9/15 分で整合）。主担当 NULL 0。高岡さん 9/16 = 6 件（カイポケどおり: 藤原/山岡/小宮/村上/安永/菅原）。同行リンク 21 件（小西=新人 → visit 直リンク）。
- **なぜ 9/15 に失敗したか（確定）**: 9/8 送信で小湊様 9/14 13:30 をカイポケへ追加 → 9/9 の Phase 0 一掃でらく助側だけ取消（未送信）→ 事務がカイポケ側で手動削除 → 9/15 の取込で置換ガード（`replace_inbound.py` 旧 :195-217）が「らく助に取消行がある日」を無条件にブロックし 422 → FE がイベント取込だけ続行し「取り込み完了」を表示。
- **なぜ直ったか**: `18fa5f8` でガードを「カイポケ現況に同一 患者×日付×開始時刻 が残っている取消」だけに限定（非稼働患者は除外）。今回のカイポケ現況に小湊様 9/14 は無い → ブロック対象 0 → 通った。失敗しても failed ジョブと画面 Alert が残る。
- **ダメ出し（次の改善候補）**:
  1. 取込後の未送信サマリに **資格不整合 13 件**（grade_change）が残る = カイポケ側で担当を変えたがサービス内容（正看/准看）を変えていない行（例: 9/16 篠原様 川名(看護師)なのに「准看」、9/16 藤原様 高岡(准看護師)なのに「正看」）。らく助から送れば直るが **grade_change の初回実機確認（9/11 総括 §4-1）が未実施**。9 月レセプト前に要対処。
  2. 差分モード（9/15）が **同一実行内で同じ職員の臨時コースを 2 つ作る**（火曜 川名「臨」×2: 並木様 date_change と 手渡様 add がそれぞれ「臨時コース新設」）。作ったコースを同一実行内で再利用すべき。
  3. 置換で **臨時コース 6 本**（月 川名 / 火 川名×2・本名 / 水 川名 / 木 宇田川・熊澤・高岡 / 金 熊澤）。カイポケ側で 1 職員が同日に複数コース相当の担当を持つため。PC 盤面に「臨」列が増える（既知の残タスク⑨ コース 1 名制と分割割当）。
  4. 未送信サマリの **イベント 41 件** = 手入力の朝会/月次MTG/研修（source=manual）。送信で昇格させるか削除しないと恒久的に「未送信」に出続ける。
  5. 取込が作る同行リンクの `source` が `manual` になっている（`import` が正しい）。
  6. 9/15 の差分で取消になった 4 件（シング/石川/仙石/唐鎌）は打刻 0 で実害なし。過去日の置換（9/14）は打刻 0 のため実績影響なし（9/14 は全員ログイン不可の日で打刻が無かった）。
- **次**: W39（9/21 週）・W40（9/28 週）はまだ取り込んでいない。同じ手順（プレビュー→突合レポート→適用→再突合）で進める。W40 は月跨ぎ（9/28〜10/4）で 100 秒制限の未対応事項あり（9/11 総括 §3-A3）。

## 7. 追補（9/17 朝）: ダメ出しの是正 → 本番稼働 36b2b44（mig 0084+0085）
- **お客様承認「全て実装」** → レーン D（同行 source）・E（イベント引き継ぎ）を実装・レビュー（REQUEST CHANGES: CRITICAL 1/HIGH 2/MEDIUM 9/LOW 3 → 是正 → APPROVE）。
- **本番稼働**: `35301cb` 同行 source=import（mig 0084・FE zod 3 値+未知値は manual）／`a7de187` イベント取込の「引き継ぎ(absorb)」（同 職員×開始×終了×正規化題名 の手入力行を kaipoke 行として引き継ぐ・運転席 SyncBar も対応・整理スクリプト `docs/tools/kaipoke-ops/dedupe_manual_events.py`）／`9d91c85` .gitignore（docs/reports・スケジュールデータ・zip）／`36b2b44` **mig 0085**: 0072 の改名で残っていた旧名 CHECK `ck_trainee_accompaniments_ck_ta_source`（2 値）を除去（0084 適用直後の本番検査で発見。残っていたら import の同行リンクで取込全体がロールバックしていた）。ROLLBACK 付き UPDATE で source='import' が通ることを確認。healthz 200。
- **ダメ出し 2（臨時コース二重生成）は取り下げ**: 索引再利用は実装済み。9/15 火 川名「臨」×2 は稲毛/都賀の拠点違いによる正規動作（特性テスト 2 本で固定）。
- **コース枠の実態**: 稲毛 A〜E（5）+M/M2、都賀 A（1）+M。人数に連動しない。都賀は 1 枠なので 2〜4 人動く日は必ず「臨」。枠数は「その日その拠点で稼働する看護師の人数」、容量は「1 人が回れる患者数」で決めるのが設計に合う（実績集計から案を出せる・PO 相談中）。
- **dedupe dry-run（本番・読み取り）**: 二重行 41 件（9/14 週 18・9/21 週 23）、全て「朝会 09:00〜09:15」の manual×kaipoke 完全一致。`--apply` は **お客様/PO の OK 後**（削除前に JSON 退避・cancelled_at/blocking は残す側へ移送）。
- テスト: backend 全体 = 既知 28 件のみ（前半 22 + 後半 6・新規 0）。※全体を一気に回すと 25〜30 分超で打ち切られることがある（同時に別 pytest が走っていると顕著）。分割実行で確認。frontend 全体 = 既知 2 + e2e 9 のみ・tsc 0。
- 見送り LOW: 吸収時の題名上書き（保つと 2 回目の取込で毎回「更新」が出て収束しないため上書きを維持・コメントに明記）／dry-run と実適用の「臨時コース新設」文言ずれ。

## 8. 追補（9/17 10:00）: W39・W40 をカイポケ正で取込（お客様指示「次の週、確認後さらに次の週」）
- **W39（9/21 週）**: pg_dump `pre-inbound-w39-20260917-0055` → preview（147 行・全 6 日置換・ブロック無し）→ 取込前突合: 一致 13 / 時刻違い 69 / 担当違い 38 / カイポケのみ 27 / らく助のみ 19 → apply 09:57（削除 140 / 挿入 145 / 臨時 4 / 対象外 2 = 石塚様 入院中）→ **取込後 145/145 一致**・主担当 NULL 0。
- **W40（9/28〜10/3・月跨ぎ）**: pg_dump `pre-inbound-w40-20260917-0059` → preview（9 月・10 月の両方が現況 CSV に入ることを確認・全 6 日置換）→ 取込前: 一致 13 / 担当違い 79 / 時刻違い 30 / カイポケのみ 25 / らく助のみ 16 → apply 10:00（削除 139 / 挿入 138 / 臨時 7 / 対象外 9）→ **取込後 138/138 一致**・NULL 0。残差 9 = らく助で非稼働（入院中/一時休止/解約）の 7 名の枠がカイポケに残っている（石塚・藤田守・渡辺・朝倉・小川・瀧本・清水政憲）。**事務がカイポケ側の週間パターンを止める必要**。
- 月跨ぎ週の 100 秒制限は **admin_call（ASGI 直叩き）経由では無関係**。画面からの突合は従来どおり注意。
- レポート（未追跡）: `docs/reports/2026-09-17-w39-kaipoke-vs-rakusuke.html` / `2026-09-17-w40-...`（取込前後）。
- **取込後の未送信サマリ**: W38 資格不整合 13（前述）／W39 **資格不整合 25（全件 高岡=准看護師 なのにカイポケ「正看」）+ 削除 2（石塚様）**／W40 0（非稼働 9 件は W40 の差分に出ない = 要観察）。イベント未送信 41/34/40 = 手入力の朝会等（dedupe --apply は OK 待ち）。
- 臨時コース: W39 4 本（稲毛 1・都賀 3）、W40 7 本（都賀のみ・臨2 まで）。都賀の枠不足が主因（§7）。
- 観察: カイポケ側で 9/21 に **新人の小西さんが職員1・高岡さんが職員2** の行が 4 件（麻生/清水/野口/並木）。取込は職員1=主担当として忠実に反映（請求区分は職員1 の資格 = 看護師で正看）。意図どおりか PO 確認。

## 9. 追補（9/18 未明）: 訪問の音声記録 Phase 1 本番稼働（案 A: Vertex AI 東京・gemini-2.5-flash）
- **本番**: らく助 `c7bec58`（mig 0086 適用・backend/frontend 再作成・healthz 200）。コミット: `c2dae44` BE / `6c322ac` FE / `e064d95` docs / `c7bec58` requests 依存修正。バックアップ `pre-deploy-voice-phase1-20260917-1624.sql.gz`。
- **GCP**: project `rakusuke-voice`・SA `rakusuke-voice-sa`（鍵は VPS `/opt/carelink/secrets`）・予算 5,000 円/月・`.env` に VOICE_AI_PROVIDER=vertex ほか 11 行（バックアップ `.env.bak.pre-voice-*`）。gcloud はローカルに導入済み（PATH 未反映・フルパス）。
- **初回 AI 実行（本番・合成音声 44 秒）**: 1 回目は `requests` 未導入で `auth` 失敗（google.auth.transport.requests が要求・テストは認証をモックしていて検出不能）→ `c7bec58` で修正・再処理で成功: tokens 1,649/659・**$0.0027**・構造化要約・話者付き逐語・本人通知 `voice_summary`。検証レコードは削除済み（soft delete・音声 unlink）。検証用 staff `S009 らく助 検証用（音声）`（retired）を作成し今泉 admin に紐付け（**残置**。盤面には出ない）。
- **cron**: 毎日 03:30 `purge-audio`（`docs/runbook/voice_recording_cron.md`）。疎通確認 `{"locked":false,"purged":0}`。
- **レビュー**: BE 3 ラウンド（最終 APPROVE）・FE 4 ラウンド（最終 APPROVE）。設計変更 2 点: ①紐付け用の予定外訪問は**生成しない**（既存訪問の再利用のみ・visit 無しを正）②非稼働患者にも紐付け可。CSP `media-src` は撤回（FE に文書 CSP 無し→PO 確認事項 §8-9）。
- **テスト**: backend 全体 fail 34 = ベースライン 29 + 既知フレーク 2（単独で通過）+ **日付依存の既存失敗 3**（`test_patient_status_sync` の `ck_svm_weekday`・HEAD の別 worktree でも同じく失敗＝退行ではない）。frontend 既知 2 のみ・tsc 0・lint 0。
- **実機確認が必要（未実施）**: iPhone/Android で「録音→停止→保存→要約表示」「録音中に QR で到着」「録音中に画面ロック→復帰（残骸復元）」「圏外→復帰の自動再送」「ボイスメモ取り込み」「音声再生 1.5×」。PWA は初回ハードリロード。
- **Phase 2 進行中**: 2-A 導線 C（`/m/record/new`・患者選択・要紐付けバナー）／2-B BE（一覧 office_id/q/order/reviewed・summary_text 編集・mig 0087）／2-B FE（PC `/records`・詳細ダイアログ・患者/スタッフ詳細カード・モニターリンク・サイドバー）。

## 10. 追補（9/18 02:30）: 音声記録 Phase 2 本番稼働（QR なし導線・PC 訪問記録ページ）
- **本番**: らく助 `640003f`（mig 0087 適用・backend/frontend 再作成・healthz 200・`/records` 307=要ログイン・一覧の `order/reviewed/q` 動作・`q` 1 文字は 422）。バックアップ `pre-deploy-voice-phase2-20260917-1730.sql.gz`。コミット: `09eceea` BE / `351486e` FE / `640003f` docs。
- **入ったもの**: `/m/record/new`（先に録音→患者選択・保存応答 ID で紐付け・圏外時は選択を出さない・離脱救出）、`/m/today` の第 2 ボタンと要紐付けバナー（直近 14 日）、`PatientPickerSheet`、PC `/records`（フィルタ・ページング・URL パラメータ UUID 検証・エラー表示・staff はスタッフ/拠点セレクト disabled）、`RecordDetailDialog`（要約表示規則「手修正あり→summary_text／無し→JSON 構造／JSON 無し→summary_text」を PC/モバイル共通化・要約編集で確認済み失効・紐付け変更・再処理/削除）、患者/スタッフ詳細カード、モニターの「記録を見る」、サイドバー、middleware。BE: 一覧 office_id/q(2〜100 文字)/order/reviewed・PATCH summary_text・previous_manual 退避は `_save_result` で・AI 書き直しで reviewed 失効・note_append は承認維持。
- **レビュー**: BE 2 ラウンド・FE 3 ラウンド → 全件是正（残 LOW のみ）。backend 全体 fail 32（ベースライン 29 + 既知 3・新規 0）、frontend 既知 2 のみ。
- **PO 確認事項 追加**: §8-11 `office_id` の第 2 レグ（現在の所属＝異動で過去記録が拠点間を移る）。
- **Phase 3 本番稼働（9/18 03:30・らく助 `818a8bd`・migration なし・alembic 0087 のまま）**: コミット `5c3a510` BE / `1eaa411` FE / `818a8bd` docs（設計 §11-5）。バックアップ `pre-deploy-voice-phase3-20260917-1823.sql.gz`。
  - 入ったもの: `GET /visit-recordings/{id}/report?format=html|json`（A4 1 件 1 枚・admin/本人・audit `report_read`・`Cache-Control: no-store`・json は transcript 省略）／`render_visit_record_html` 純関数（要約出し分けは Phase 2 規則・手修正で空にした録音は「要約は削除されています。」で旧 JSON に落とさない・全補間エスケープ）／`GET /admin/visit-recordings/usage?month=`（JST 月境界・総計＝スタッフ別内訳の合計・by_status 0 埋め）／PC 詳細ダイアログ「A4 で出力」（SyncReportButton 同方式）／連携ページ末尾「音声記録の利用状況」カード（USD_JPY=150 固定概算・`lib/voice-usage-rate.ts`）／preflight の visit_audio サイズ・バックアップは音声対象外の注記／**/audio の既存不具合是正**（監査 commit 失敗→rollback 後の expire 属性参照で 500）。
  - レビュー: BE HIGH 1・MEDIUM 4・LOW 7 → HIGH/MEDIUM 全件＋LOW 4 件是正（LOW-2 集計スナップショット/LOW-3 created_at index/LOW-5 preflight「読めない」表示は記録のみ）。FE HIGH 1（records ページテストのモック漏れ→共有ファクトリ化）・LOW 7 → 全件是正。
  - テスト: backend 全体 fail 32＝ベースライン 32 と同一集合（新規 0）／frontend fail 3（middleware・PatientFixedVisitsPanel E-3・BulkPoolInsertDialog の断続）＝既知／tsc・lint 0。
  - 本番検証: healthz 200（local/公開）・usage 2026-09 → 200（0 件・by_status 5 キー）・`month=2026-9` → 422・report 削除済み ID → 404「録音が見つかりません」・`format=pdf` → 422・レンダラを本番コンテナで合成データ描画（`<b>` がエスケープされ患者名/バイタルが出る）。本番の録音は Phase 1 検証分 1 件のみで削除済みのため、**実データでの A4 出力は実機確認項目**。
  - デプロイの罠: 本番 `/opt/carelink` で `preflight-check.sh` が chmod +x されていて（5 月・内容差分なし）`git pull --ff-only` が「local changes」で拒否され、1 回目のビルドは旧コードで走った（`git pull | tail` でエラーが握られる）。`git config core.fileMode false` を本番に設定して解消。**以後の pull は `git pull --ff-only origin develop && git rev-parse --short HEAD` で HEAD を必ず目視**。
  - 未実施: 要約テンプレ v2（PO フィードバック待ち）・`/records` ヘッダへの A4 入口（詳細ダイアログのみ）。
  - 後片付け: ローカルの SA 鍵コピー（scratchpad・C:/tmp/gsa・一時 gcloud config）は削除済み。鍵は VPS `/opt/carelink/secrets/rakusuke-voice-sa.json` のみ。
- **実機確認（未実施・累積）**: Phase 1 の 6 項目 + `/m/record/new` の一連（録音→患者選択→訪問詳細へ遷移）・`/records` の音声再生（位置保持・1.5×）・要約編集→モバイルの表示追随・**Phase 3: 詳細ダイアログ「A4 で出力」→ 新タブに A4 が開き印刷プレビューで 1 枚に収まる（ポップアップブロック時の toast も）・連携ページ末尾の利用状況カードで当月の件数/費用が出る（月を戻せる・未来は不可）**。
