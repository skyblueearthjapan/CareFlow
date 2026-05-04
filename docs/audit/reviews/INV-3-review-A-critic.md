# INV-3 自動算出ロジック 監査 クロスレビュー A（技術観点）

**VERDICT: REVISE**

## 総評

監査は競争力のある、概ね正確な深掘り。**70-80% が verified accurate**。ただし、いくつかの factual inaccuracies in 行番号・段階記述、**1件の捏造コードスニペット (areas check)**、**5 ordering patterns の誤った特徴付け**、structurally misleading な pipeline 表（"8 stages" を 13-step process と混同）。これらの誤りは migration 技術参照としての信頼性を損なう。

## 重大な指摘

### [MAJOR-1] §4.5 の `areas` コードスニペットは捏造
監査が示す:
```python
for staff in self.staff_list:
    if staff.areas and req.area not in staff.areas:
        continue  # 実装されていない
```
このコードは `allocation_engine.py` のどこにも存在しない。`.areas` を grep すると 0件マッチ。`Staff.areas` は `allocation_models.py:42` で定義されているが、engine では一切参照されない。
- 監査のスニペットはコメント「実装されていない」とともに**修正案コードを既存コードのように見せている**。
- 「check exists but is bypassed」と読めるが、実際は「no check exists at all」
- **Fix**: §4.5 を「`Staff.areas` フィールドは `allocation_models.py:42` で定義されているが `allocation_engine.py` で一切参照されない。エリアベースのフィルタリングは `_build_candidate_list()` ほかに存在しない」と書き直す

### [MAJOR-2] 5 Ordering Patterns 記述が誤り
監査: `日付昇順+need_staff降順 / smart_sort（候補少ない先） / need_staff優先 / ローテーション優先 / 距離順`

実際 `:2190-2237`:
- Order 1: Smart sort (fewest eligible staff first)
- Order 2: Default (date ASC, needStaff DESC, rotation, time)
- Order 3: Time-slot scarcity (fixed-time first, then eligible count)
- Order 4: Coupled+fixed first (2-staff+fixed highest priority)
- Order 5: Reverse date (Friday-to-Monday) + needStaff DESC

→ Order 1/2 が逆順、Order 3/4/5 が完全に誤り。Implementer が porting すると 3 つ wrong orderings を実装する

### [MAJOR-3] Pipeline 表 heading が「8段階」だが実は 13+ 段階
監査 heading: `8段階パイプライン`
実際: Step 1〜12.5 + Step 13 (unassigned list rebuild :349)。docstring `:11-17` は 8 段階だが `_run_pipeline()` :231 はもっと実行。
- 「8 stages」ベースで migration 計画する Executor は scope を underestimate
- **Fix**: heading を「13段階」に変更 or docstring が古いことを明示

### [MAJOR-4] 複数の不正確な行番号参照
- `_run_pipeline()` 「231-350」 → 実際 231-362
- `_relaxed_reinsertion()` がテーブルで line 番号なし、実際 line 2416
- `_final_overlap_sweep()` 実際 line 2146（テーブルになし）
- 他の細かい off-by-one が複数

→ 個別には小さいが、line references が監査の primary evidence trail なので、collectively 信頼性低下

## Minor Findings

1. MentorPair モデル: 監査「`trainee_id`/`mentor_id`」、実際「`trainee_staff_id`/`mentor_staff_id`」`allocation_models.py:210-211`
2. StaffChange `restriction_type` 値が不完全。`"終日不可"`/`"終日"`/`"時間指定制限"` も実装あり `:841`
3. `_relaxed_reinsertion` は実は capacity/time の relaxation、**性別/NG職員は relaxation しない**（hard constraint 維持）`:2416-2475`
4. summary に `coupled_debug` フィールドあり `:221`、監査では omit

## 不足項目

- **Step 0.1-0.3 preprocessing が pipeline 表に未記載** — `allocate()` :136-143 で `_apply_special_week()` / `_apply_patient_changes()` / `_enrich_from_patterns()` が multi-trial loop の **前** に実行される。Migration-critical。
- **`_candidate_sort_key` 7-factor scoring が不在** :755-765 — staff selection priority 決定の core ranking logic
- **`_can_insert()` logic 未記載** :1180+ — time-slot fitting workhorse
- **`PatientChange` model** `:161-176` がモデル表に欠落
- **`allow_partial` flag on `_enforce_coupled_atomicity`** — :331 (Step 11.5 後) `True`、:241 (Step 3 後) `False`、behavioral difference 未記載
- **Rollback / error handling 未記載** — engine が exception throw した場合
- **Performance "数秒" claim が unsubstantiated** — profiling data なし

## Multi-Perspective

- **Executor**: §7「Week 1: data schema unification + Python API spec」だが、JSON schema format 未指定、preprocessing steps 未記載、`_generate_orderings` の処遇未記載 → 3-4 clarifying questions が必要
- **Stakeholder**: 「Case B: 2 weeks」と「Week 1-4」（4週）が矛盾
- **Skeptic**: Case B は Python と他言語の **permanent integration seam** を生む。schema change 毎に coordinated update。**ongoing maintenance cost が不在**

## ACCEPT 昇格条件

1. §4.5 の捏造 areas snippet を修正
2. 5 ordering 記述を実コードから訂正
3. Step 0.1-0.3 preprocessing を pipeline 表に追加
4. MentorPair field 名修正
5. `_relaxed_reinsertion` は capacity/time relaxation と明記
6. 「8 stages」 heading を 13-step 実態と整合
