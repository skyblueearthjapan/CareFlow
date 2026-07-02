# P0-2 詳細設計: 適用経路の安全網（N-4 / I-03, I-04, I-05）

作成 2026-07-02（architect 設計・ディレクター補正済み）/ 親文書: schedule-advisor-design.md（Phase 0）

## 0. 方針
PUT /patients/{id}/fixed-visits（手動系統＋候補採用系統）と apply-individual に最小再検証
（患者間時間衝突[90分占有込み]・H10・容量480分/6名）を追加。**手動運用を壊さない**:
検出→警告返却→FE表示が基本。422は構造破壊と pinned 保護に限定。H10 は force_lunch モデルで統一。

## 1. 現状（要点）
- PUT fixed-visits = `patient_fixed_visits.py:362-413`。全削除→INSERT・検証ゼロ・pinned 保護なし。
  スキーマ `PatientFixedVisitsBulkPut`/`PatientFixedVisitV2Read`（共に extra="forbid"）。
- FE 呼出 2系統: PoolCandidateList.tsx:283 / ProposeNewModal.tsx:501,567。
  他曜日保持は FE `mergeAdoptedIntoNormalFixedVisits`（_proposeSlotUtils.ts:105-114）のみが担保。
  **FE はレスポンスを一切パースしない**（fetcher<unknown>, propose_confirm.ts:48-71）→ エンベロープ化は後方互換。
- apply-individual = schedule_v2.py:706-851。H10ゲート(:766-779)=422。サービス層に pinned 422・
  異住所同時刻 422 はあるが、患者間衝突（90分占有込み）・容量の適用時再検証なし（TOCTOU）。
- apply-week-only = schedule_v2.py:981-。H10 は warning log のみ（#113 hotfix）→ I-05 非対称。

## 2. 再検証カーネル `backend/app/services/scheduling/pfv_validator.py`（新規）
- `PfvValidationWarning(code, message, weekday, severity)` / `PfvValidationResult(warnings, has_errors)`
- `validate_pfv_changes(db, patient_id, proposed_items, mode="normal", *, config)`
- 検証項目:
  | # | 検証 | severity |
  |---|---|---|
  | V1 | 同患者同曜日slot重複（pydantic 既存） | error(422, 既存) |
  | V2 | **pinned 保護**（下記の同一性規約） | error(422) |
  | V3 | 患者間時間衝突（同コース同曜日、90分占有込み、前方/後方制約） | warning |
  | V4 | H10 昼休み重複（_is_in_lunch_break + config lunch窓） | warning |
  | V5 | コース容量 480分 / 6名 超過 | warning |
- V3 の対象は **PFVテーブル（恒久パターン同士）**。当週 placed visits は見ない
  （PFV=恒久宣言。当週衝突は reset-to-fixed 系で別途検出。将来「次回生成時の予告」は Phase 2+）。
- 再利用: proposal_solver `_existing_occupancy_end`(:502-526) / `compute_earliest_start_after` /
  `_add_minutes` / `haversine_minutes`、auto_allocator_v2 `_is_in_lunch_break`(:1071-1130)。
  import 方向は proposal_solver→auto_allocator_v2 の一方向で循環なし（確認済）。
  衝突 Yes/No 用の `_does_visit_conflict()` は solver の前方/後方制約(:570-628)と同一規則で実装。

### 2.1 V2 pinned 保護の同一性規約（ディレクター補正・実装必須）
「pinned が存在したら 422」では**ない**。FE マージは既存行（pinned 含む）を body に含めて送り返すため、
- body に既存 pinned 行と **完全一致**（weekday/slot_index/start_time/duration_min/office系/course_template_id/is_pinned）
  する行が含まれる → **保持とみなし OK**
- 既存 pinned 行が body に無い（=削除）or 属性が異なる（=変更）→ **422**
とする。加えて実装時に **FE の existingFixedVisitToItem / mergeAdoptedIntoNormalFixedVisits が
is_pinned を運搬しているか必ず確認**すること。運搬していない場合、現行 FE は採用のたびに
pinned を silent に落としている（既存バグ）ため、FE 側で is_pinned の保持を先に修正する。

## 3. API 契約
- PUT レスポンスをエンベロープ化: `PatientFixedVisitsBulkPutResponse{items, warnings[]}`
  （PfvValidationWarningOut{code,message,weekday,severity}）。FE 未パースのため BE 先行デプロイ安全。
  スクリプト等の list 前提クライアントには HANDOFF/文書で周知。
- apply-individual: 既存 `warnings: list[str]` に再検証メッセージを追記（構造化は将来チケット）。
- 422/警告の線引き:
  | 条件 | severity |
  |---|---|
  | 同患者同曜日slot重複 | 422（既存） |
  | pinned 削除/変更（2.1 規約） | 422 |
  | 患者間時間衝突 | warning |
  | H10 | warning（apply-individual は force_lunch 導入まで現行422維持→Commit 2 で統一） |
  | 容量 480分/6名 | warning |

## 4. H10 統一（force_lunch モデル）
- `AutoScheduleV2ApplyIndividualRequest.force_lunch: bool = False` を追加。
  false=現行通り422 / true=warning に降格して続行。**既定値で挙動不変**。
- apply-week-only: 現行の「続行」を維持しつつ warning をレスポンス warnings に載せる（現在は log のみ）。
- PUT fixed-visits の H10 は warning のみ（手動運用を止めない）。
- FE（Commit 3）: 422 detail に H10 を含む場合、確認ダイアログ→承認で force_lunch=true 再送。
- #113 再発防止: week-only は 422 化しない / individual も force で迂回可 / 既定は安全側(false)。

## 5. FE 表示（Commit 3）
- `useConfirmFixedVisits` を `fetcher<PatientFixedVisitsBulkPutResponse>` 化＋レスポンス zod 新設。
- warnings を toast.warning で表示（PoolCandidateList:293 / ProposeNewModal:494 の既存パターン）。
- apply-individual H10 の確認→force 再送（FullOptimizeDialog の確認フロー :523-604 参考）。

## 6. リスクと展開
- R1 レスポンス形式変更: FE未パースで安全。周知のみ。
- R2 pinned 保護: 2.1 の同一性規約で採用フロー非破壊。FE の is_pinned 運搬確認必須。
- R3 衝突 false positive: warning 止まり（422にしない）。
- R4 DB負荷: apply_individual_proposal(:10223-10234) と同等クエリ。
- 展開順: Commit 1（カーネル＋PUT）→ Commit 2（apply-individual force_lunch＋再検証、week-only warnings）
  → Commit 3（FE表示）。各コミット独立デプロイ可・既定値で挙動不変。

## 7. テスト計画
- 新規 `backend/tests/test_pfv_validator.py`: 衝突あり/なし・90分占有・H10・容量・pinned(保持OK/変更422/削除422)。
- `test_patient_fixed_visits.py` 追加: エンベロープ・pinned 422・衝突でも200+warnings。
- `test_schedule_v2_*.py` 追加: force_lunch true/false。
- FE: PoolCandidateList / ProposeNewModal の toast.warning、zod。
