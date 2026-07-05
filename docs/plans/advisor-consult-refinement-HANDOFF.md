# 引き継ぎ書：研ぎ澄ましセッション（W-1〜W-15）— プール一括投入・機能廃止・相談型アドバイザー完成

作成 2026-07-05 / **本番HEAD = `d71351c`** / DB = **migration 0053** / healthz 正常。
**次のエージェントはまずこのファイルを読む。** 前史: `docs/plans/change-scope-unification-HANDOFF.md`
（反映先統一）→ `docs/plans/pool-bulk-insert-HANDOFF.md`（本セッションの Wave 別詳細）→ 本書（総括・残工程・懸念）。

---

## 0. 最重要 — 思想の正典（すべての設計・レビューの判定基準）

**`docs/plans/schedule-advisor-design.md` §6**（PO が「本ソフトの要」と明言・2026-07-05）:
1. **余白の原則**: システムが患者の予定を動かすとき、使ってよいのは患者様が予め差し出した
   「希望訪問スケジュールの余白」だけ（ピン=不可侵／希望範囲=確認不要の余白／それ以外=動かさない）
2. **予防・保全・救急の分業**: 診断=予防／スケジュール最適化=保全／詰まり解消相談=救急。
   救急の技術を日常画面に持ち込まない
3. **詰まり解消の適用範囲は閉じた**: PO 承認済み拡張2件（W-14 橋渡し・W-15 定員起因）は完了。
   **これ以上広げない**（最適化・改善提案への組み込みは意図的に非対象）

メモリ: careflow-advisor-philosophy（自動メモリ・索引に「整合を判定基準に」と明記済み）。

## 1. TL;DR — このセッション（2026-07-04〜05）で何が変わったか

**入口の一本化と研ぎ澄まし（削った）**:
- 新規提案 ProposeNewModal 廃止（W-4）→ 代替「＋新規患者登録」ボタン（患者マスタ PatientForm 再利用・
  希望パターンまで1画面）
- シミュレーション（旧・全面最適化）廃止（W-8）— 物差し不統一・説明不能・行動不能な問い
- 欠勤対応廃止（W-10）— 本番 apply 0回を実測して PO 決定。欠勤時はコースの「担当変更」

**プールからの投入（作った）**:
- プール一括投入（W-1/2）: 個別提案と同一物差しの逐次シミュレーション・週ビュープレビュー・
  「見せる」4点・A固定（pattern_and_week）・拠点自動グループ化（W-6）
- 定員超過の橋渡し（W-5/5b）: 「+1名なら入る」バッジ→個別方式b相談へ直行
- 効率代替のプール個別移植＋希望未登録患者の可視化（W-3）

**拠点まわり（W-6/7）**: 主担当拠点=運用の正典／担当エリア=入口のヒントの構造を確定。
採用ガード422・患者編集の自動上書きバグ修正・担当エリア「入口のヒント」説明文・
地域ルールの学習（登録時の一度だけ誘導＋City単位の却下記憶・migration 0053）

**バグ根治（W-11）**: 自動スタッフ割当の警告不表示 — notices-only の成功トースト誤誘導を警告化＋
性別違反の残留を unresolved_warnings で可視化（新規書込経路は元々存在しない — 残留の可視化）

**2名体制と詰まり解消（W-12〜15）**:
- W-12a: ペア探索（主従アンカー・同時刻別コース・警告OR合成）＋2行原子採用（I-12/N-7 解消）＋
  scope保護＋bulk除外
- W-12d: 詰まり解消相談（ブロッカー除去テスト深さ1/2・同コース時間ずらし対応・余白のみ退避・
  上位5プラン・明示確認・1TX・plan_id指紋照合）
- W-13: 拠点自動解決＋**「変更前/変更後」コース一覧の全5箇所統一**（BeforeAfterCourseTimeline）
- W-14: 一括投入→詰まり解消の橋渡し（autoUnblock）
- W-15: 定員起因拡張（他コース退避で定員を空ける手を方式bと並列・frees_capacity バッジ）

**UI/文言**: 「範囲最適化」→「スケジュール最適化」改名・週次ガイドを「週を生成」の隣（左端）へ・
「儀式」表現全廃・「自動スタッフ割付」→「割当」統一・シミュレーション/プール投入ボタンの記述掃除

## 2. コミット一覧（時系列・全て本番反映済み）

| Wave | コミット | 内容 |
|---|---|---|
| W-1 | `1cb702d` | 一括投入エンジン＋simulate |
| W-2 | `5c26b52` | 一括apply＋ダイアログ＋見せる4点 |
| W-3 | `be60024` | 効率代替移植＋希望未登録チップ |
| W-4 | `9b367ce` | 新規提案廃止＋新規患者登録ボタン |
| W-5/5b | `ace1e49` `f574c49` | 定員超過橋渡し＋直行化 |
| W-6 | `ec42622` | 拠点6項目（自動グループ化・スキップ・改名・ガード・上書きバグ・エリア説明） |
| W-7 | `8a402be` | 地域ルールの学習（**migration 0053**） |
| W-8 | `61d0fb4` | シミュレーション廃止 |
| W-9/9b | `b75c58f` `90d0cbe` | 週次ガイド対配置→左端＋儀式表現全廃 |
| W-10 | `3e80cf8`+`19d9e70` | 欠勤対応廃止（**コミット分割事故→追いコミット修復**。教訓=直前 git status 全量確認） |
| W-11 | `b9dae5f` | 警告不表示バグ根治 |
| W-12a | `9f79662` | 2名体制ペア探索＋原子採用 |
| 思想 | `dfa5f58` | 余白の原則・予防/保全/救急を正典化 |
| W-12d | `359322a` | 詰まり解消相談 |
| W-13 | `343fc49` | 拠点自動解決＋before/after統一 |
| W-14 | `9e852a5` | 一括→詰まり解消橋渡し |
| W-15 | `cd4abcb` | 定員起因拡張（最終） |

## 3. コード地図（本セッションの主要新規/改修）

**BE**: `services/scheduling/pool_bulk_inserter.py`（一括投入）/ `unblock_search.py`（詰まり解消 —
compute_plan_id・_bucket_capacity_blocked・_build_plan_courses）/ `propose_slots_service.py`
（W-12a ペア生成 _enumerate_pair_slots・_TWO_STAFF_BONUS=800・no_pair_slot）/ `proposal_solver.py`
（slot_fits_exact）/ `pfv_validator.py`（V7）/ `layer3_assignment.py`（unresolved_warnings）/
`office_assigner.py`＋`api/v1/offices.py`（W-7 resolve拡張・area-cities・dismissals）/
`models/office_area_prompt_dismissal.py`（0053）/ 削除: staff_substitute 系・（UI 残置: full-optimize/diff-add API）

**FE**: `BulkPoolInsertDialog.tsx`（拠点タブ・見せる・橋渡し2種）/ `PoolCandidateList.tsx`
（ペアカード・方式b・詰まり解消 UnblockConsult・採用確認 before/after）/
`CourseMoveTimeline.tsx`（BeforeAfterCourseTimeline 共有ラッパー）/ `RegisterPatientButton.tsx`＋
`CreatePatientDialog.tsx` / `PatientForm.tsx`（地域学習 callout・manual初期化）/
`AssignWarningDialog.tsx`（unresolved セクション）/ 削除: ProposeNewModal・FullOptimizeDialog・StaffSubstituteDialog

**設計書**: pool-bulk-insert-design / two-staff-pairing-design / unblock-consult-design /
region-rule-learning-design / schedule-advisor-design §6（思想）

## 4. 残工程（優先度つき・漏れなし）

### A. 現場フィードバック待ち（最優先 — 実装前に現場の声）
1. 一括投入・詰まり解消・2名体制ペア・「変更前/変更後」表の実使用感（文言・並び・折りたたみ既定）
2. **W-11 の運用確認**: 修正後最初の自動スタッフ割当で「性別違反の残留」がダイアログに現れる —
   現場が担当変更で解消したか／patients.sex_restriction 未設定（active 66名中設定14名）の確認を依頼中
3. 地域ルール学習の発火実績（gender_blocked_no_candidate と併せ、本番ログ/AuditLog で観測可能）
4. 閾値の現場調整（前セッションから継続）: 改善提案10分/週・診断1.5倍・trend+20%・チェックインgrace20分

### B. 実装バックログ（設計記録あり・着手可能）
5. **W-12b**: 週次生成テスト補強＋ _align_same_address_pair_to_same_time が2名体制ペアを壊さない保護
   （two-staff-pairing-design §3 — **未着手**）
6. **W-12c**: scope_optimizer の原子ペア move（現在2名体制枠は「保護」= 最適化対象外。
   excluded.two_staff 会計で見えている）
7. 一括投入のペア原子挿入（D-6 解除 — bulk は2名体制を two_staff_pending で除外中）
8. ペア×定員超過相談の併用（+1名でペアが入るケース）
9. unblock v2: 分数上限×深さ2テスト補強（W-15 LOW）／玉突き連鎖／週限定（B）の詰まり解消／
   「要確認の手も含める」トグル（scope-opt の同名バックログと同時に）／2名体制の相方側ブロッカー複合
10. 一括投入: pinned 患者が simulate に載ると apply 必ず422のUXギャップ（W-2 LOW）／非同期化（N>100）／
    ordering 選択肢公開／投入結果→自動スタッフ割当への導線
11. W-7 後続: エリア変更時の「判定が変わる既存患者 N名」プレビュー（見せて選ばせる）／
    未紐付 City 一覧画面／表記ゆれ・City特定不能住所への対応
12. 承認記憶（定員超過の蒸し返し防止 — suggestion_dismissals 類似。古いバックログ）
13. undo v2（PFV系・一括系スナップショット undo — **bulk/unblock/scope apply は Ctrl+Z 対象外のまま**。
    各画面のバナーで明示済みだが恒久解はスナップショット undo）

### C. レガシー掃除 Wave（まとまったら1回で）
14. DiffAddDialog.tsx（**要調査**: マウント有無を確認してから。useApplyIndividualMutation の消費者）
15. FixedTimeEditModal.tsx＋useUpdateFixedTimeMaster/WeekOnly（W-8 で完全孤児化・未削除）
16. CourseDayTablePanel 系テストの死蔵 mock（削除済み hook のキー）
17. BE 残置 API: /v2/full-optimize 系・/v2/diff-add（ロールバック余地として意図的残置 — 掃除は PO 確認後）
18. docs/HANDOFF.md が**依然 untracked**（2026-06-14 作成・陳腐化。コミットするなら内容更新とセット・PO判断）

### D. 技術負債・環境（気になる点）
19. **BE 既存 fail 群（環境依存）**: test_ai_interpret / test_patients_v2 / test_password_change /
    test_pending_requests / test_visit_v2 — base HEAD 再現の SQLAlchemy/**Python 3.14 互換**（UUID .hex）。
    従来からの test_reset_to_fixed_* 2件・tests/scripts の collection error 1件も継続。
    放置すると回帰検出力が下がる — SQLAlchemy 更新 or Python ピン留めの検討価値あり
20. **FE 既知 fail 91件**: CourseDayTablePanel 系（QueryClientProvider/router mock 欠如）・
    SessionProvider 系。テストの死角 — mock 基盤の修理は独立タスクとして価値大
21. bulk の state_token が他拠点/主担当未設定患者の PFV を含まない非対称（W-2 記録・実害未確認）
22. UNBLOCK_TRIGGER_REASONS が PoolCandidateList と BulkPoolInsertDialog に**複製**
    （テスト mock 都合・両方にドリフト防止コメントあり — reason 追加時は両方更新）
23. マニュアル整備: 週次ガイドは一括投入（§7）まで。**詰まり解消・2名体制ペア・地域学習は
    マニュアル未記載** — 現場定着を見て追記推奨

### E. 現場周知の累積リスト（未周知があれば）
- 「範囲最適化」→「スケジュール最適化」／シミュレーションボタン廃止／欠勤対応ボタン廃止
  （→コースの担当変更）／「＋新規提案」→「＋新規患者登録」／一括投入は毎週の型に登録（A固定・
  Ctrl+Z対象外）／ドラッグ既定は「この週だけ」（前セッション）／フロント更新後は Ctrl+Shift+R

## 5. プロセス規約（本セッションで再確認・追加された教訓）

- 体制: **Opus executor 実装 → code-reviewer 独立レビュー → 反映 → 再判定 → ディレクターがコミット →
  デプロイ**。自己approve禁止。本セッションのレビューは19回（REQUEST_CHANGES 4回 — CRITICAL 1・HIGH 4 を
  レビューが検出。すべて修正後 APPROVE）
- **コミット直前に git status 全量確認**（W-10 分割事故の教訓 — 削除だけ入り参照除去が漏れた）
- 日本語ファイルは Edit/Write ツールのみ（PowerShell Get-Content/Set-Content 絶対禁止 — 累計3事故）
- 実値トークン: bg-warning/10 等の var()/alpha は **CSS 不生成**（bg-warning-bg / border-border-warning /
  text-warning-strong を使う）
- BE テスト: `python -m pytest -q -p no:warnings`（uv run 不可）
- デプロイ: pg_dump → pull → build →（migrate）→ recreate → healthz・`set -eo pipefail`
- API エラー中断エージェントは「git status で現物確認してから再開」
- ID への情報密輸禁止（W-12d CRITICAL の教訓 — 契約は明示フィールド＋サーバ側指紋照合）
- 新機能は思想の正典（§6）との整合をレビュー判定基準に含める

## 6. 次の候補（推奨順）

1. 現場フィードバック収集（§4-A）→ 文言・閾値・表示の磨き込み
2. W-12b/12c（2名体制の残り2 Wave — 設計は two-staff-pairing-design に確定済み）
3. FE テスト mock 基盤の修理（91件の死角解消 — 以後の全 Wave の回帰検出力が上がる）
4. マニュアル追記（詰まり解消・2名体制・地域学習）
5. レガシー掃除 Wave（§4-C — PO 確認とセット）
