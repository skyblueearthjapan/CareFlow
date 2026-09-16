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
