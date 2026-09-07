# セッション引き継ぎ 2026-09-07（伊藤様 不規則日程の調査・らく助のみ展開・「＋訪問」設計→実装）

**次のエージェントへ: まずこのファイルを読むこと。** 前セッション総括は `session-2026-09-03-HANDOFF.md`。

## ★ 最初の 5 分
1. **状態**: 本番 HEAD は前セッションのまま `07eedb2`（+docs）。本日のコミットは **未デプロイ・未 push**（§3）。本番 DB への書込は §1-3 の 5 件のみ（伊藤様の W38〜W40 訪問・バックアップ `pre-ito-placement-20260907-0344.sql.gz`）。
2. **お客様案件**: 松岡様依頼「伊藤夢華様 9/14・17・19・22・25・28 12:00」→ 現状調査 → らく助のみへ展開完了（担当なし=M・35 分・固定訪問不変・カイポケ未送信）。報告書 = `docs/reports/2026-09-07-ito-irregular-schedule-report.html`（患者名を含むため未追跡）。
3. **改修**: 「＋訪問（任意日付の訪問追加）」と「固定訪問保存の反映先確認 (E)」。設計 = `docs/plans/add-visit-anywhere-design.md`（PO 決定 12 点）。実装はディレクター方式（Opus executor 並行 + 別レーン code-reviewer で Phase 毎に承認）。
4. **PO が「今週の伊藤様が変わっている」と言ったら**: 松岡様が 9/7 11:31 に固定訪問を月木 12:00 で保存 → 今週 W37 だけ再生成（9/7 09:30→12:00・9/10 12:00 新規）。設計の欠陥 6 = E で是正済み（未デプロイ）。
5. **道具**: `docs/tools/kaipoke-ops/admin_call.py` で API 直叩き（本日の展開もこれ）。

## 1. 伊藤様案件（事実）
### 1-1. 依頼と詰まりの正体
- 川名様→松岡様: 14(月)17(木)19(土)22(火)25(金)28(月) 全て 12:00 = 約 3 日おきの不規則日程。週パターン（PFV）では表現不能。
- 松岡様の操作: 11:24 盤面で 9/14 09:30→12:00（成功）。11:26〜11:31 患者画面の固定訪問を「月 12:00・木 12:00・赤ピン」で保存 → `change_scope=pattern_and_week` + **今日の ISO 週** で W37 だけ再生成。W38〜W40 は不変。
- 操作前の固定訪問 = 月 09:30・35 分・ピンなし 1 件（9/3 15:57〜15:59 のプール一括投入が 4 週分で書いたもの。患者登録 9/3 09:25 川名様時点では未入力）。根拠 = 11:26 の validate 呼び出しの初期値。
- 週生成は W37〜W40 すべて 9/3 実行済み（W41=10/5 は未生成・W42 は生成済み）。
- カイポケ（9/3 23:50 export）: 9/7 高岡 10:00 / 9/10 熊澤+小西 11:00 / 14・17・21・24・28 川名 10:00-11:00（60 分）。らく助は 35 分。

### 1-2. PO 回答
担当は未設定でよい／35 分／固定訪問はそのまま／カイポケへは反映しない。

### 1-3. 実施（12:45 JST・API 直叩き・backup `pre-ito-placement-20260907-0344.sql.gz`）
| 日付 | 操作 | 結果 |
|---|---|---|
| 9/14 月 | なし | 12:00 髙梨 A（松岡様の配置のまま） |
| 9/17 木 | POST /visits manual_week・W38 木 M | 12:00 担当なし M |
| 9/19 土 | 同上・W38 土 M | 12:00 担当なし M |
| 9/22 火 | visit-move-week-only 9/21 09:30→ + new_course_template_id=M | 12:00 担当なし M |
| 9/25 金 | POST /visits manual_week・W39 金 M | 12:00 担当なし M |
| 9/28 月 | visit-move-week-only 09:30→12:00 + M | 12:00 担当なし M |
PFV（月木 12:00 locked）不変・今週 9/7/9/10 据え置き。同期バーには未送信 6 件として載る。

### 1-4. 残る PO 判断
- 今週 9/7 12:00・9/10 12:00（固定訪問保存で変わった分）の扱い。カイポケは 9/7 高岡 10:00・9/10 熊澤+小西 11:00 のまま。
- 固定訪問（月木 12:00 赤ピン）を残すか。残すと W41 以降の週生成で毎週月木 12:00 が並ぶ。
- 9/14 のみ担当 髙梨（他は担当なし）。

## 2. 画面経路の欠陥（調査で確定・設計書 §1）
1. ＋訪問／空き枠登録の候補が保留プール患者のみ 2. ＋訪問の所要時間に 35 分なし 3. 曜日移動がコースを移動先曜日に付け替えない（盤面は course_id 基準・旧曜日タブに残る見込み・本番実行履歴 0） 4. 担当なし行に＋訪問が無い／内部キーを担当 ID として送る 5. M 列が普段非表示 6. 固定訪問保存が無確認で今日の週を再生成。

## 3. 本日のコミット（develop・未 push・未デプロイ）
| コミット | 内容 | レビュー |
|---|---|---|
| `5874e7e` | **Phase E** 固定訪問保存の反映先確認: 型だけ／型＋対象週。消える/保護/作られる件数（BE `reset_visits_to_fixed` の許可リスト auto 系 source × planned/proposed を鏡写し・manual_week/import/青ピンは再生成抑止として別軸）。患者画面既定=型だけ、盤面の患者詳細=表示週（PatientEditDialog→Panel へ iso 伝播） | 承認（HIGH 2 是正後） |
| `bb48c27` | **Phase 0** 所要 15〜120/5 分・基本時間初期値／曜日移動で同コースへ付け替え+移動先に無ければ確認／担当なし行の＋訪問（M 既定）／StaffWeekBoard todayIso | 承認（HIGH 1 是正後） |
| `cf2b7d5` | **Phase 1+2 部品** `AddVisitAnywhereDialog`/`AddVisitAnywhereRows`/`lib/scheduling/addVisitPlan.ts`/`courseTemplateMatch.ts`。propose-slots を `time_type='固定'` で週ごとに呼ぶ・API 順位保持・定員超は既定にしない・主担当拠点→サブ拠点(PFV sub_office_id)「要確認」→M・元訪問の一括割当・型は単一日付のみ | 承認（HIGH 6 是正後） |
| `ed9738f` | **Phase 3+4 結線**: ツールバー「＋訪問」・行ボタン（担当なし行含む）・「📅 曜日を移動…」→新モーダル。`lib/scheduling/addVisitExecutor.ts`（日付順・最初の失敗で停止・422 は該当項目のみ acknowledge・`visits_moved=0` は失敗・型置換は movability 引継ぎ・臨は POST /visits+note）。`AddVisitResultDialog`（✓登録/✓移動/✓型を更新/✓登録(臨時)/✗失敗/—未実行・週ごとの元に戻す）。画面ガード: 2名体制×M 不可・他拠点×新規追加 不可・移動先コース未解決は送らない・📅の訪問を元に固定。盤面の曜日跨ぎ移動コードと move-dest-confirm は削除。SlotRegister 候補を全 active 患者に | 承認（C1/H1〜H4/M1〜M4/L1〜L4 是正後） |

テスト: `pnpm vitest run lib/scheduling components/schedule/v2 components/schedule/timeline` = 87 files / 1138 passed・`pnpm tsc --noEmit` = 0 error・全体 = 既知失敗（e2e spec 9 本・middleware 1・BulkPoolInsertDialog 並列フレーク）のみ。

### 3-1. 第 2 波レビューで確定した BE 制約（実装判断）
- `visit-move-week-only` は該当なしでも 200 `visits_moved:0`（op-log も無し）→ 0 は失敗扱い。
- `place-and-fix` は `requires_multiple_staff` 患者に staff_count=2 必須・同一/単一 M は 422 → **2 名体制 × M は画面でブロック**（BE 緩和は PO 判断・設計書 §10 に追記）。
- `place-and-fix` は主担当拠点以外のテンプレートを 422 → **他拠点候補は「新規追加」では選べない**（週を変える／型も変える は可）。
- op-log undo は週単位（`execute_undo` が iso 週で最新行を探す）→ 多週プランは成功した週ごとに undo。
- PUT fixed-visits は op-log 対象外（型変更は undo 不可）。`POST /visits`（臨）も op-log 無し。

## 4. 教訓（本日）
1. **FE の分類・順位は BE の実装と突合する**: 「消える予定」を deny-list で推測 → BE は allow-list（2 回捕捉）。提案候補を score で再ソート → BE は限界コストで再順位付け。
2. 固定訪問の保存 = 型更新＋「今日の週」再生成。週文脈の無い画面では既定を「型だけ」に（PO 決定 8）。
3. 不規則日程の入口は「今週だけ」の週単位操作（manual_week）。manual_week は同日の型スロット再生成を抑止する意味論を持つ（追加にも効く）。
4. 並行実装はファイル所有を分ける・git 操作はディレクターのみ・レビューは別レーンで BE 契約まで読ませる。
5. BulkPoolInsertDialog.test は並列負荷時のみ落ちる既存フレーク（単体 18/18・7 月以降未変更）。

## 5. 残タスク
1. **デプロイ判断（PO）**: 4 コミット（5874e7e/bb48c27/cf2b7d5/ed9738f）は frontend のみ・migration 無し・BE 変更無し。手順は `careflow-deploy`（push → pg_dump → pull/build/recreate frontend → healthz）。デプロイ後は Ctrl+Shift+R 案内。
2. **実機確認**（設計書 §9・ステージング無し）: (a) E: 患者画面で固定訪問を保存 → 確認モーダルの既定が「型だけ」・盤面の患者詳細から開くと「型＋表示週」 (b) ＋訪問: 未来週・テスト患者で 2 日付 12:00 → 提案 → 登録 → 盤面に出る・同期バー未送信に載る → 元に戻す (c) 📅 曜日を移動… → 火曜へ → 火曜タブに出る・担当が移動先コースの担当 (d) 担当なし行の＋訪問 → M 列に出る。
3. **BE 緩和の PO 判断**（設計書 §10 追跡事項）: 2 名体制患者を M へ入れる／他拠点候補を「新規追加」で使う — いずれも place-and-fix の 422 が理由で画面側でブロック中。
4. op-log undo を `op_group_id` 指定で戻せる BE エンドポイント（多週 undo の厳密化・レビュー残件）。
4. 報告書 `docs/reports/2026-09-07-...html` に「画面から同じことができるか」の調査結果と改修内容を追記（依頼あり・任意）。
5. 前セッションからの残（`session-2026-09-03-HANDOFF.md` §6）: running 残骸ジョブの決着／都賀A 9/10 担当 4 件／レポート Phase 3／ミラー未対応 3 箇所 ほか。

## 6. 参照
- 設計: `docs/plans/add-visit-anywhere-design.md`／報告書: `docs/reports/2026-09-07-ito-irregular-schedule-report.html`
- メモリ: `careflow-ito-yumeka-irregular-request`／`careflow-ui-adhoc-visit-gaps`
- 本番 DB 調査 SQL の要点: 監査ログ `audit_logs`（`actor_user_id`→users.email、`request_body`）で操作再現、`patient_fixed_visits` は履歴を持たない（validate の初期値から復元）。
