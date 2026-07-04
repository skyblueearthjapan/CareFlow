# 引き継ぎ書：プール一括投入（再構築）＋新規提案廃止セッション

作成 2026-07-04 / **本番HEAD = `b75c58f`**（W-9 追記時に更新）/ DB = **migration 0053**（office_area_prompt_dismissals）/ healthz 正常。

**W-9 完了（2026-07-04・本番稼働）**: 週次ガイドを「週を生成」の右隣に対配置（PO発案）＋
「（5分の儀式）」等の儀式表現をユーザー可視文言から全廃（「約5分」の事実表記に）＋
マニュアル2種の旧称「自動スタッフ割付」15箇所を「自動スタッフ割当」に統一。コミット `b75c58f`。
前セッションの引き継ぎ: `docs/plans/change-scope-unification-HANDOFF.md` → `docs/HANDOFF.md`（プロジェクト基本）。
設計書（正典）: **`docs/plans/pool-bulk-insert-design.md`**（PO決定 D-1〜D-4・実装時判断の追記込み）。

## 1. TL;DR

PO の「尖らせる機能は尖らせ、不要な機能は削る」方針に基づき、4 Wave を一気通貫
（実装 executor → code-reviewer 独立レビュー → 反映 → コミット → デプロイ）で完遂した。

1. **患者の入口を一本化**: 患者登録（＋希望訪問パターン）→ 保留プール → 個別提案/一括投入。
   **新規提案（ProposeNewModal 1,819行）は廃止**（W-4）。
2. **プール一括投入を新規設計で再構築**（W-1/W-2）: 旧距離グリーディの復活ではなく、
   個別提案と同一物差し（proposal_solver＋compute_exact_marginal）の**逐次シミュレーション**。
   「効果を表示」の隣の「一括投入」ボタン → 週ビュー before/after プレビュー → 確認 → 1TX 適用。
3. **反映先は A 固定**（pattern_and_week・PO決定 D-2）。代わりに「見せる」4点で
   「聞いてなかった」と言わせない: 閉じられないアンバーバナー / 必須チェックボックス /
   ボタン文言「固定訪問週間に登録する（N名・M枠）」/ 適用後トースト＋AuditLog。
4. **効率代替をプール個別に移植**＋「希望未登録 N名」チップ（プールに現れない患者の安全網）（W-3）。
5. **「＋新規患者登録」ボタン**をスケジュール画面に設置（患者マスタの PatientForm 完全再利用。
   希望訪問パターンも同フォームで登録完結）（W-4）。

**現場周知が必要**: ①「＋新規提案」ボタンは「＋新規患者登録」に変わった ②一括投入は
毎週の型（固定訪問週間）に登録される（今週だけの試しはできない）③フロント変更のため
Ctrl+Shift+R が必要。

## 2. コミット（時系列・全て本番反映済み）

| Wave | コミット | 内容 | レビュー |
|---|---|---|---|
| W-1 | `1cb702d` | pool_bulk_inserter（逐次シミュレーション・ハイブリッド順序 D-1・複数曜日カバレッジ・state_token）＋ /v2/pool-bulk-simulate＋テスト12本 | REQUEST_CHANGES(H1/M2/L2)→全反映→APPROVE |
| W-2 | `5c26b52` | /v2/pool-bulk-apply（1TX・409・union visit_plans・AuditLog）＋ BulkPoolInsertDialog＋「見せる」4点＋週次ガイド§7 | BE/FE とも APPROVE（M/L 反映） |
| W-3 | `be60024` | PoolCandidateList へ効率代替移植＋希望未登録チップ | APPROVE（M=誘導文言反映） |
| W-4 | `9b367ce` | ProposeNewModal 削除＋RegisterPatientButton/CreatePatientDialog＋運用マニュアル書き換え | APPROVE（M=disabled 伝播反映） |
| W-5 | `ace1e49` | 定員超過の個別相談への橋渡し（A案・設計書§3.6）: 最終sim基準の overcapacity_available_count＋「定員+1名なら入る候補あり」バッジ＋done画面から方式bへの導線 | APPROVE（M=案内文2行化反映） |
| W-5b | `f574c49` | 定員超過相談への直行化（PO実機フィードバック起点）: 完了画面のバッジもクリック可・個別相談で超過候補まで自動展開（ref ガード1回・採用は理由必須不変）・プレビューに「適用後に移動できます」ヒント | APPROVE（M=フォーカスリング/L=防御的リセット反映） |
| W-6 | `ec42622` | 拠点まわり6項目: ①一括投入の拠点自動グループ化（拠点タブ・混入修正・拠点未設定分離・「拠点を選択してから」廃止）②スケジュール最適化の拠点画面スキップ ③改名「範囲最適化」→「スケジュール最適化」④拠点未設定の採用ガード422（place-and-fix/apply_individual_proposal）＋simulate の no_primary_office/office_mismatch ⑤患者編集の拠点自動上書きバグ修正（編集時 officeMode=manual）⑥担当エリア欄に「入口のヒント」説明文 | APPROVE（M=適用中ESCガード反映・L=dark配色反映） |

**W-6 の背景調査（2026-07-04・会話内レポート・要点はメモリ careflow-office-region-model）**:
主担当拠点=運用の正典（全エンジン参照）/ 担当エリア=入口のヒント（新規患者の自動判定のみ・
スケジュール計算は不参照）。担当エリア変更は既存患者に不追随（登録時スナップショット）・
同一City二重登録は created_at 先勝ち・City は静的seed393件で患者登録では増えない。

**W-8 完了（2026-07-04・本番稼働）**: シミュレーション（旧・全面最適化 FullOptimizeDialog）UI 廃止 —
コミット `61d0fb4`。PO 判断「確認不要、畳んでよし」。理由 = アドバイザー4機能と物差し不統一・
説明不能・行動不能な問い・残存適用経路（W41 一括固定時間変更含む）が反映先統一以前の事故再発口。
BE /v2/full-optimize は残置。孤児 hook 3件削除・共有部品（ProposalWeekCalendar 等）無傷。
**残レガシー（次の掃除 Wave 候補）**: DiffAddDialog.tsx（useApplyIndividualMutation 経由で生存中の残置）・
FixedTimeEditModal.tsx＋useUpdateFixedTime系（W-8 で完全孤児化・未削除）・
CourseDayTablePanel 系テストの死蔵 mock・BE /v2/full-optimize・/v2/diff-add。

**W-7 完了（2026-07-04・本番稼働）**: 地域ルールの学習 — コミット `8a402be`・migration **0053**・
設計書 `docs/plans/region-rule-learning-design.md`。患者登録で「拠点エリア外」→手動拠点選択の瞬間に
一度だけ呼びかけ（[担当地域に登録する]=office_cities に1件追加＋却下記憶の自動解除 /
[今回だけ]=City単位で記憶し二度と聞かない）。resolve 拡張は confidence=none 時のみ
matched_city+prompt_dismissed を返す後方互換。レビュー APPROVE
（MEDIUM=bg-warning/10 CSS不生成罠→実値トークンで修正 / LOW=却下APIの404→反映）。
City を特定できない住所（表記ゆれ・マスタ外）は呼びかけを出さない=v1 の既知制約（設計書§4）。

## 3. アーキテクチャ要点（次エージェント向け）

- **エンジン**: `backend/app/services/scheduling/pool_bulk_inserter.py`。
  `load_week_course_buckets` → `_copy_bucket` で可変コピー → 患者ごとに
  `compute_all_proposed_slots` → 不足曜日ごとに delta 最小候補 → `_insert_visit` パターンで
  メモリ内仮確定（これが患者間調停の実体。容量で後続が自然に弾かれる）。完全決定論・read-only。
- **順序（D-1）**: 第1群=投入可能曜日1つの患者（候補数→delta 昇順）→ 第2群=最良 delta 昇順。
  タイブレーク最終は str(patient_id)。
- **state_token**: `compute_bulk_state_token` = 拠点の normal PFV 全行＋当週 visits の SHA-256。
  simulate→apply 間の変更を 409 で検知。**apply は W-1 と同一関数を再利用すること**。
- **apply**: union visit_plans（既存 PFV 保持＋投入曜日追加 — placements だけ渡すと
  apply_individual_proposal が他曜日を削除するため）→ pfv_validator → reset_visits_to_fixed(patient_id)
  → 明示 flush → 全員分まとめて 1 commit。V2 pinned=422 全ロールバック。
- **監査**: AuditLog(action="pool_bulk_apply")。**schedule_op_log は不使用**（undoable 列がなく
  Ctrl+Z スタックを汚染するため — 設計書 §4 に理由記録済み。op_log 非汚染はテストで保証）。
- **FE**: `BulkPoolInsertDialog.tsx`（idle→simulating→previewing→applying→done）。
  ProposalWeekCalendar/WeekdayScheduleCard は無改造再利用。409 は ApiError.status 判定。
- **プール定義は不変**: FE 算出（active＋shortage>0）。一括対象は先頭50名
  （POOL_BULK_MAX_PATIENTS、silent cap なし・明示トースト）。

## 4. テスト状態

- BE: test_pool_bulk_simulate 12本＋test_pool_bulk_apply 8本＋回帰
  （pool-overview/propose-slots/scope-opt/change-scope/op_log）全パス
- FE: フル 1060 本中 fail 91（**全て既知の既存 fail** — CourseDayTablePanel 系
  QueryClient/SessionProvider 系。W-4 前後で同数を確認済み）
- 既知 BE 既存 fail（無関係・従来から）: test_reset_to_fixed_auto_shifts_* / _same_address_* 2件

## 5. バックログ（本セッション由来・優先度順）

1. **現場フィードバック**: 一括投入の使用感（順序の説明・A固定の周知が効いているか）・
   「＋新規患者登録」の定着
2. pinned PFV 持ち患者が simulate に載ると apply が必ず 422 全ロールバック（設計書 §8。
   simulate 段階の除外 or 内容同一再書込の V2 免除で解消）
3. スロットカード JSX の3重複（normal/efficiency/overcapacity — PoolCandidateList 内。
   W-3 レビュー LOW。SlotCardInline 抽出のクリーンアップ）
4. BE /v2/diff-add と DiffAddDialog.tsx の残置分の掃除（旧一括のロールバック余地。
   ProposeNewModal 削除まで完了した今、掃除して良いか PO 確認）
5. 一括投入の非同期ジョブ化（N>100）・ordering 選択肢の公開・
   投入結果→自動スタッフ割当への導線（設計書 §8）
6. 前セッションからの継続: undo v2 / 定員超過の承認記憶 / I-12 / Layer3 閾値調整

## 6. プロセス記録

- 体制: executor（W-1/W-2/W-4=opus、W-3=sonnet）実装 → code-reviewer 独立レビュー →
  指摘反映 → 再判定 → ディレクターがコミット → 一括デプロイ。自己 approve なし。
  レビュー4回（APPROVE 3 / REQUEST_CHANGES 1 → 反映後 APPROVE）
- W-3 実装中に API エラー中断 1回 → 「git status で現物確認してから再開」で復旧（規約どおり）
- BE/FE 並行実装はファイル所有権の明示で衝突ゼロ
- デプロイ: pg_dump（pre-deploy-20260704-0531）→ pull → build → migrate（no-op）→
  recreate → healthz 正常
