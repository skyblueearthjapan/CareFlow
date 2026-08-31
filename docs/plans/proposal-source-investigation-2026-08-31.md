# 提案系エンジンの入力ソース調査 — 「提案は固定訪問スケジュール(PFV)基準なのか？」

調査日: 2026-08-31 / 対象 HEAD: `4601984` (develop) / 調査方法: コード実読 (READ-ONLY・変更なし)
対象週の例: **2026-08-31(月)〜09-06(日) = ISO 2026-W36**

関連正典: `weekly-space-design.md`(週空間の憲法5条) / `week-cockpit-design.md`(Phase E) /
`pin-and-movability-spec.md`(赤=PFV.movability / 青=visits.week_pinned) /
`base-visit-minutes-design.md`(希望=基本時間) / `scheduling-logic-normalization.md`(N-1〜N-8) /
`scope-optimization-design.md` / `schedule-advisor-HANDOFF.md`

---

## 1. 結論

### 1-1. 前提の訂正 — 提案は既に「今週の実 visits」で計算されている

**PO の懸念「提案が PFV(毎週の型)基準で計算されている」は、現在 UI から到達できる提案機能に関しては
事実ではありません。** スケジュール画面の提案系はほぼ全て、選択中 ISO 週の実 `visits` 行を占有
ソースとして読んでいます。占有計算の入口は 1 本の共有ローダに集約されています:

`backend/app/services/scheduling/propose_slots_service.py:251` `load_week_course_buckets`
(SELECT 実体 = `:278-291`):

```python
stmt = (
    select(Visit, Course, Staff)
    .join(Course, Course.id == Visit.course_id)
    .outerjoin(Staff, Staff.id == Course.assigned_staff_id)
    .where(
        Visit.deleted_at.is_(None),
        Visit.status == VISIT_STATUS_PLANNED,
        Visit.visit_date >= week_monday,
        Visit.visit_date < week_upper,
        Course.deleted_at.is_(None),
        Course.iso_year == iso_year,
        Course.iso_week == iso_week,
    )
)
```

docstring (`:258`) も明言: 「**対象週 × 拠点の実 Visit を 1 回ロードし、コース単位に集計する**」。

このローダを共有しているのは: **propose-slots(ここに入りますよ) / pool-overview / pool-bulk-simulate /
配置改善(improvement_engine) / スケジュール最適化(scope_optimizer) / 詰まり解消(unblock_search)**。
さらに **スケジュール診断(schedule_health) / 実現性チェック(feasibility_check) / 盤面(board_service) /
自動スタッフ割当(layer3_assignment) / 代替候補(substitute_candidates) / 重なり警告(events_inbound)**
は各自の週 Visit クエリを持ちます。**PFV を占有ソースとして展開している提案エンジンは 1 つもありません。**

補足の重要な訂正:

- **「スケジュール最適化」ボタン = `ScopeOptimizeDialog` → `scope_optimizer`** であり、
  PFV ベースの旧「全面最適化」ではありません (`CourseDayTablePanel.tsx:4966-4979`)。
- PFV ベースの **旧「全面最適化」(`/v2/full-optimize`) と「プール投入(一括)」(`/v2/diff-add`) は
  FE から呼び出し元が存在しません** (2026-07-04 `61d0fb4` で UI 廃止)。BE は残骸として生存。
- `visits` に `iso_year` / `iso_week` カラムは**ありません** (`backend/app/models/visit.py:118`)。
  週の絞り込みは `visit_date` 範囲、または `Course.iso_year/iso_week` 経由の二通りです。

### 1-2. では何が「的外れ」の原因か — 5 つの構造的欠落

PO が体感している「提案が盤面と噛み合わない」は、**ソースが PFV だから**ではなく、
週 Visit を読む際の **フィルタと結合条件の欠落** が原因です。実害の大きい順:

| # | 欠落 | 実体 | 影響する機能 |
|---|---|---|---|
| **G1** | `Visit.status == 'planned'` のみ | `propose_slots_service.py:284`。**完了済/訪問中 (`completed`/`in_progress`) が占有から消える** | propose-slots / pool-overview / pool-bulk / 配置改善 / 最適化 / 詰まり解消 |
| **G2** | `Course` への **INNER JOIN** + `Course.iso_year/iso_week` 一致 | `propose_slots_service.py:280`。**`course_id IS NULL` の訪問が完全に不可視**(QR 予定外訪問 `visit.py:143`、コース削除で SET NULL された訪問、コース省略の＋訪問) | 上記全部 + 診断 + 盤面 + 受け入れ枠 + 自動スタッフ割当 |
| **G3** | バケットの曜日・担当が **Course 属性** | `propose_slots_service.py:326` `key = (course.office_id, course.weekday, code)` / `:338` `assigned_staff_id=course.assigned_staff_id`(コメント「visit の primary_staff_id ではなくコース割付を正とする」)。**1 件だけ担当を付け替える `visit-assign-staff-week`(`schedule_v2.py:1638`) が見えない** | 上記全部 |
| **G4** | PFV↔週 visit の突合キーが `(patient_id, weekday)` | `improvement_engine.py:520-522` / `scope_optimizer.py:1026-1029` / `unblock_search.py:805`。**曜日を跨ぐ週限定移動をすると「評価不能」で黙って除外される** | 配置改善 / 最適化 / 詰まり解消 |
| **G5** | 楽観ロック `state_token` が **PFV 指紋のみ** | `scope_optimizer.py:291-300` `compute_state_token(pfvs)`。**週だけの手編集ではトークンが変わらない**ため、古い simulate 結果がそのまま apply できる | 最適化 / 詰まり解消 |

加えて機能固有の欠落:

- **G6 受け入れ枠にスタッフ次元が無い** — `acceptance_matrix_service.py` は `Staff` / `StaffWeeklyOverride`
  を一切 import していない。「🛌 休みにする」で木曜を全員休みにしても、受け入れ枠の木曜は ○ のまま。
- **G7 代替候補は「日」スコープ** — `substitute_candidates.py:266` `Visit.visit_date == target_date`。
  週内の他曜日の負荷が候補スコアに入らない。

### 1-3. 回答

**「今週の実スケジュールを反映させることは可能か」→ 大半は既に反映済み。残りの是正も可能で、
最も効くものは共有ローダ 1 箇所の述語修正 (規模 S)。** 二層分離 (マスタ/今週) の設計原則とも
**衝突しません** — むしろ G1〜G5 の是正は「週空間の憲法」を実装に追いつかせる作業です。
唯一 L 規模かつ設計判断が要るのは **G4/G5 の「可動域(movability)を PFV から週へ移す」** 部分で、
これは `Visit` に `movability` 列が無い (`week_pinned` しか無い) ため別設計が必要です。

---

## 2. 機能別一覧

凡例: 入力ソース **W**=今週の実 visits / **P**=PFV / **C**=courses / **希**=weekly_pattern(希望)

| 機能 | 入口 (API / FE) | 現状の占有ソース | 今週の手編集が見えるか | 今週基準にするための変更点 | 規模 | リスク |
|---|---|---|---|---|---|---|
| **スケジュール診断** | `GET /v2/schedule-health` `schedule_v2.py:3305` / `ScheduleHealthDialog.tsx:454` ← `scheduleHealth.ts:48` | **W** 専用ローダ `schedule_health.py:111`(SELECT `:135-145`)。`_HEALTH_STATUSES=(planned,in_progress,completed)` `:76-80`。**PFV 参照ゼロ** | ✅ DnD移動 / place-and-fix(fix_pattern=false) / 今週だけ取消(除外=正) / コース付替<br>❌ G2 course_id NULL / G3 visit 単位担当 | 変更不要。G2/G3 のみ共有課題 | — | — |
| **スケジュール最適化** (=範囲最適化) | `POST /v2/scope-optimization/simulate` `schedule_v2.py:5268` / apply `:5663` / `ScopeOptimizeDialog.tsx:300,867` ← `scopeOptimization.ts:35` | **W**(占有・患者集合) `scope_optimizer.py:912` + **P**(可動域/ピン/token) `:922-930`,`:291` | ✅ 占有としては全部見える<br>❌ **G4** 曜日跨ぎ週移動 → `excluded.no_current_visit`(`:1026-1029`) で静かに対象外<br>❌ **G5** token が PFV 指紋のみ | G4: `sim.pfv_by_pw` 突合を曜日非依存に(患者単位フォールバック)。G5: token に週 visits 指紋を合成(`pool_bulk_inserter.py:218` `compute_bulk_state_token` が既に両方を含む先例) | **M**(G4) / **S**(G5) | M / M |
| **配置改善の提案** | `GET /v2/improvement-suggestions` `schedule_v2.py:4606` / `ImprovementSuggestionsSection.tsx:137` ← `improvementSuggestions.ts:51` | **W**(候補枠・現在位置) `improvement_engine.py:922` + **P**(提案対象の列挙・所要時間・ピン) `:928-934`,`:1005-1013` | ✅ 現在位置の限界コストは週 visit から算出(`_find_current_placement:505-551`)<br>❌ **G4** `if wd != weekday: continue`(`:520-522`) で曜日跨ぎ移動済み患者は `summary.no_current_visit`<br>❌ **週だけ追加した訪問(PFV 無し)は一切提案対象にならない** | G4: `_find_current_placement` の曜日ガードを緩め、見つかった週 visit の曜日を「現在位置」とする。対象列挙を「PFV ∪ 当週 visit」に拡張 | **M** | M(delta 基準がずれる) |
| **詰まり解消 (propose-unblock)** | `POST /v2/propose-unblock` `schedule_v2.py:6042` / apply `:6249` / `PoolCandidateList.tsx:779` ← `unblock.ts:37` | **W** `unblock_search.py:777` + **P** `:797-806` + scope の token を共有 `:783` | 最適化と同一 (G4/G5 を共有) | 最適化と同時に是正 | **M** | M |
| **プール患者クリック→「ここに入りますよ」(propose-slots)** | `POST /v2/propose-slots` `schedule_v2.py:3510` / `PoolCandidateList.tsx:1122,1195` ← `fieldBoard.ts:135` | **W** `schedule_v2.py:3553` + **希**(候補患者の希望時間/分数 `_patient_to_pool_candidate` `:3736`) | ✅ 週の実配置を壁として使う<br>❌ G1/G2/G3 | G1: `status.in_(planned,in_progress,completed)`。G2: OUTER JOIN + 合成バケット。G3: 担当を `visit.primary_staff_id ?? course.assigned_staff_id` に | **S**(G1) / **M**(G2,G3) | M / M |
| **プール俯瞰 (pool-overview)** | `POST /v2/pool-overview` `schedule_v2.py:3811` / `PoolOverviewPane.tsx:167,193` ← `poolOverview.ts:33` | **W** `:3837`(propose-slots と同一ローダ) + **希** | propose-slots と同一 | 同上(共有ローダ 1 箇所で同時解決) | — | — |
| **プール一括投入 (pool-bulk-simulate/apply)** | `POST /v2/pool-bulk-simulate` `schedule_v2.py:3934` / apply `:4045` / `BulkPoolInsertDialog.tsx:542,624` ← `poolBulk.ts:36,63` | **W** `pool_bulk_inserter.py:460` + **P**(state_token に PFV も含む `:218-265`) | propose-slots と同一。**token は週 visits と PFV の両方を指紋化しており G5 の模範解** | 同上 | — | — |
| **保留プールの中身(誰が並ぶか)** | FE 算出 `CourseDayTablePanel.tsx:1348-1374` | **希 − W**(`getDesiredWeeklyVisitCount` − `countWeekVisits(weekVisits)`) | ✅ 今週の実績で不足数が決まる(今週基準) | 変更不要 | — | — |
| **自動スタッフ割当 (assign-staff-only)** | `POST /schedule/assign-staff-only` `schedule.py:1811` / `CourseDayTablePanel.tsx:5591` ← `assign_staff_only.ts:27` | **C+W** `layer3_assignment.py:3092-3103`(当週 Course) / `:3111-3115`(配下 planned visits)。**PFV 参照ゼロ**(4387 行中 0 hit) | ✅ 週の実配置で判断<br>❌ G2 `course_id IS NULL` は Course ループなので不可視 | G2 のみ(NULL コース用の第 2 パス) | **S** | M |
| **「担当なし」投入提案 (assign-candidates)** | `POST /v2/assign-candidates` `substitute_candidates.py:70` / `AssignSuggestionPopover.tsx:143` ← `cockpit.ts:68` | **W**(日単位) `substitute_candidates.py:255-275` | ✅ **唯一 OUTER JOIN + visit 単位担当を正しく扱う**(`_owner_of` `:236-243`) = 参照実装 | 変更不要 | — | — |
| **代替候補 (急休)** | `POST /v2/substitute-candidates` `substitute_candidates.py:39` / `SubstitutePanel.tsx:103` ← `cockpit.ts:67` | **W**(日単位 `visit_date == target_date`) | ✅ 当日は完全に見える<br>❌ **G7** 週内他曜日の負荷は見ない | `load_day_rows` を週レンジ化 + `_score_for`(`:915`) に週負荷項 | **M** | L |
| **受け入れ枠 (acceptance-matrix)** | `GET /acceptance-matrix` `acceptance_matrix.py:45` / `acceptance/page.tsx:50` ← `acceptance_matrix.ts:44` | **C ゲート + W** `acceptance_matrix_service.py:393-415`(当週 Course) → `:427-436`(`Visit.course_id.in_(...)`) | ✅ manual_week 移動 / import / manual_cancel は反映<br>❌ **G2** course_id NULL は占有に数えない<br>❌ M(マネージャ)コースは意図的除外 `:415`<br>❌ **G6** スタッフ休みが枠に反映されない | G2: 拠点解決した course-less 訪問を合成バケットで加算。G6: スタッフ次元の新設 | **S**(G2) / **L**(G6) | M / H |
| **実現性チェック (feasibility)** | `GET /v2/feasibility-report` `feasibility.py:25` / `FeasibilityCheckButton.tsx:31` | **W** `feasibility_check.py:541`。`outerjoin(Course)` + `status != 'cancelled'` + 担当は `course.assigned_staff_id or visit.primary_staff_id`(`:636`) | ✅ **5 機能中もっとも忠実**。G1/G2/G3 すべて回避済み = **改修の手本** | 変更不要 | — | — |
| **盤面 (board) / 今週の運転席** | `GET /v2/board` `schedule_v2.py:4404` / `GET /visits` `visits.py:337` / `CourseDayTablePanel.tsx:566,656` | **W+C** `board_service.py:140-155`(cancelled 込み) / `visits.py:374-395`(status 無条件) | ✅ 参照実装。source フィルタ無し | 変更不要(既知の制限: `useVisits` の 500 件ハードキャップ `visits.ts:39,130`) | — | — |
| **重なり警告 (EventVisitConflictNotice)** | `/integrations/events-inbound-preview` `integrations.py:2860` ほか | **W** `events_inbound.py:168-195`。`primary_staff_id` ∪ `VisitStaffAssignment` の和集合 | ✅ 2 名体制の相方まで見る(代替候補より厳密) | 変更不要 | — | — |
| **週生成 (generate-week-only)** | `POST /schedule/generate-week-only` `schedule.py:1692` / `CourseDayTablePanel.tsx:5083` ← `generate_week.ts:29` | **P + 希** `layer1_expander.py:171-178`。週 visits は**拒否権としてのみ**読む(`:853-863`, `:880-893`, `:1248-1262`) | 手編集は「保護される」が「入力にはならない」 = **設計どおり** | **変更しない**(§4 参照) | L | **H** |
| **固定枠に戻す (reset-to-fixed)** | `POST /v2/reset-to-fixed` `schedule_v2.py:1215` / `ResetToFixedButton.tsx:89` | **P** `auto_allocator_v2.py:9832`。削除は whitelist 方式(`_RESET_DELETABLE_SOURCES` `:8642`)で `manual_week`/`manual_cancel`/`import` は残る | 設計どおり | **変更しない** | — | — |
| **(参考) 全面最適化 / プール投入一括 — UI 廃止済** | `/v2/full-optimize` `schedule_v2.py:867` / `/v2/diff-add` `:752` | **P** `_load_before_visits_from_pfv` `auto_allocator_v2.py:6029-6037` | ❌ **唯一の真の PFV 基準エンジン。ただし FE 呼出元ゼロ**(`autoScheduleV2.ts` の該当 hook は非参照) | 既に切替機構あり(§3-2) | S(フラグ) | M |

---

## 3. 既存の「切替」機構の棚卸し

質問にあった `source="week"|"pfv"` / `use_week_visits` / `BuildOptions` 等の**汎用スイッチは存在しません**。
ただし関連する既存機構が 3 つあります。

### 3-1. `change_scope` — **書き込み側には既に二層スイッチがある**

`backend/app/schemas/v2/scope_optimization.py:227`:

```python
change_scope: Literal["pattern_only", "pattern_and_week", "week_only"] = Field(default="pattern_only", ...)
#   pattern_only     = 型 (PFV) のみ変更 (既定・従来挙動)
#   pattern_and_week = PFV 移動後、影響患者の今週 visits を PFV から再生成 (A)
#   week_only        = PFV は不変、今週の visits にのみ反映 (B; source='manual_week')
```

FE も既に 2 択を出しています — `ScopeOptimizeDialog.tsx:464`
`change_scope: changeScopeChoice === 'pattern' ? 'pattern_and_week' : 'week_only'`。

**つまり「毎週の型」か「今週だけ」かの二層は apply 側では完成済みで、
読み取り(simulate)側だけが PFV 片寄りになっている** — これが非対称の正体です。

### 3-2. `g21_new_algorithm` — 拠点単位フィーチャーフラグ (auto_allocator_v2 専用)

`auto_allocator_v2.py:85-87` / 解決 `:1529-1549` / 分岐 `:7645-7696`。
ON の拠点だけ `_load_before_visits_v2`(`:6176`) を使い、Before を
**「(PFV) ∪ (weekly_pattern) ∪ (当週 DB Visit) ∪ (pending_overlay)、当週 DB Visit が最優先」**
の 4 経路 union で構築します (`:6190-6198`)。当週 Visit 経路の SELECT は `:6313-6322`:

```python
select(Visit).where(
    Visit.patient_id.in_(patient_ids),
    Visit.visit_date >= week_monday,
    Visit.visit_date <= week_sunday,
    Visit.deleted_at.is_(None),
)
```

- **これが「PFV → 週 visits」の唯一の既存切替スイッチ**です。ただし対象は UI 廃止済みの
  full-optimize / diff-add のみで、現在の提案 UI には効きません。
- 注意: **status フィルタが無い** ため `cancelled`(今週だけ取消)も占有として数えてしまいます。
  もし将来この経路を再利用するなら要修正。
- migration による seed は無く (`0051` は別キー `l3_fix_primary_staff` のみ)、
  管理画面 `/admin/feature-flags` からの手動 ON が必要 = **本番は旧 PFV 経路のはず**。

コード自身が問題を明記しています — `auto_allocator_v2.py:7195-7199`:

> Phase G-99 (懸念①): canary 非依存で当週の実 placed visit を衝突相手に注入する。
> legacy before ローダ (`_load_before_visits_from_pfv`) は PatientFixedVisit のみ読み
> 実 visits テーブルを読まないため、PFV 非対応の実 visit (手動配置等) が after_visits に
> 載らず、中尾 16:00 が井上 16:00 と同時刻でも衝突未検出になっていた。

### 3-3. `feasibility_basis="pfv"` — 表示ラベルのみ・かつ実装と不一致

`schedule_v2.py:4594` でハードコードされた文字列。スキーマ既定値
(`backend/app/schemas/v2/improvement_suggestion.py:175`、FE `improvementSuggestion.ts:147`)。
**パラメータではなく、しかも現在の improvement_engine は週 visits を読んでいるので表示が誤り**
(FE では画面表示に使われていない)。`"week_visits"` に直すべき (規模 S・リスク L)。

---

## 4. 具体例 — 8/31 週で提案が的外れになる仕組み

対象週 = ISO 2026-W36 (2026-08-31 月 〜 09-06 日)。

### 例 A (G4) — 曜日を跨ぐ「今週だけ移動」で、その患者が提案対象から静かに消える

田中様の PFV は火 10:00。担当が水 14:00 へ DnD で「この週だけ」移動
(`/v2/visit-move-week-only` `schedule_v2.py:1439` → `visits.visit_date=2026-09-02`,
`start_time=14:00`, `source='manual_week'`、PFV は不変)。

- 盤面 / 診断 / 実現性チェック → **水 14:00 と表示・計算** (正しい)。
- 配置改善 → `_find_current_placement`(`improvement_engine.py:520-522`) は
  `wd != weekday(=1 火)` で走査を打ち切るため火曜に田中様の visit が無い →
  `None` → `summary.no_current_visit += 1`(`:1024-1026`) → **田中様の提案は 0 件**。
  画面上は「当週 visit が未展開」という、実態と違う内訳で表示される。
- 最適化 / 詰まり解消 → 水曜バケットには実在の障害物として乗る(正しい)が、
  `sim.pfv_by_pw[(田中,水)]` が無い → `excluded.no_current_visit`(`scope_optimizer.py:1026-1029`)
  で**動かせない枠として凍結**され、代わりに火曜の PFV が幽霊として token に残る。
- (G5) この移動では `compute_state_token`(PFV 指紋)が変わらないので、
  移動前に取った simulate 結果を**そのまま apply できてしまう**。

### 例 B (G1) — 月曜の午後に提案を出すと、午前の完了済み訪問が「空き」になる

8/31(月) 15:00 に詰まり解消/プール提案を実行。午前の訪問は打刻済みで `status='completed'`。
共有ローダは `Visit.status == VISIT_STATUS_PLANNED`(`propose_slots_service.py:284`) のみ拾うため
**午前の訪問が占有から丸ごと消え**、月曜 09:00 に別患者を入れる提案が出る。
一方で診断(`_HEALTH_STATUSES`)と実現性チェック(`status != 'cancelled'`)はその訪問を見ている
→ **同じ画面の「診断」と「提案」が矛盾する**。3 機能群に同時に効く、最も実害の大きい欠落。

### 例 C (G3) — 1 件だけ担当を付け替えると、警告が別人基準で計算される

火曜 A コースの担当は佐藤さん。佐藤さんが火曜だけ休みになり、訪問 1 件を田中さんへ
「今週だけ付替」(`visit-assign-staff-week` `schedule_v2.py:1638`。docstring 明記:
「コース担当 (courses.assigned_staff_id) と PFV には一切触れない」)。

共有ローダは `outerjoin(Staff, Staff.id == Course.assigned_staff_id)`(`:281`) で
**佐藤さん**を拾い、佐藤さんの `StaffShift` / `StaffWeeklyOverride('off')` / `StaffEvent` /
`PatientNgStaff` を評価します。結果:

- 実際は田中さんが動いているのに「担当が休み(`staff_absent`)」警告が出る。
- 患者 X が**田中さんを NG 指定**していても `ng_patient_ids` は佐藤さん基準なので素通りする。
- 田中さんの朝会イベントが `event_windows` に入らず、重なる時間帯をクリーン枠として提案する。

なお `assign-candidates`(`substitute_candidates.py:236-243`)だけは
`visit.primary_staff_id → course.assigned_staff_id` の順で正しく解決しており、参照実装です。

### 例 D (G2) — QR 予定外訪問・コース無し訪問は誰からも見えない

`Visit.course_id` は nullable(`visit.py:157`, `ondelete="SET NULL"`)で、
QR 予定外打刻は `is_unplanned=True` かつ `course_id=NULL` で作られます(`visit.py:143`, `visits.py:1349`)。
共有ローダ・診断・盤面・受け入れ枠・Layer3 はすべて Course を INNER JOIN / Course ループで回るため、
**水 09-02 14:00 の予定外訪問は存在しないものとして扱われ**、その時間帯へ提案が飛び、
受け入れ枠は ○「60分枠あり」と答え、自動スタッフ割当はその上に人を重ねます。

### 例 E (G6) — 全員休みにした木曜が、受け入れ枠では ○ のまま

「🛌 休みにする」(`staff-off-week`)で木曜を全員休みにすると
`courses.assigned_staff_id` は NULL になりますが visits の `course_id` は不変。
`acceptance_matrix_service.py` は `Staff` を一切読まないので、**誰も出勤しない木曜が
空き枠 ○ として提示され続けます**。逆に全件を「今週だけ取消」した日は
`status='cancelled'` で除外され、**丸一日 ○** になります。

### 例 F (端の話・G3 の変種) — 曜日跨ぎ移動で `course_id` を張り替え損ねた場合

`_apply_visit_move_week_only`(`schedule_v2.py:5402`)は `new_course_template_id` が
渡されたときだけ `course_id` を張り替えます(`:5456-5465`, `:5477`)。
バケットの曜日は **`course.weekday`**(`propose_slots_service.py:326`)であり
`visit_date` ではないため、テンプレート解決に失敗して `course_id` が旧曜日のコースのままだと、
その訪問は**移動前の曜日のバケットに新しい時刻で**積まれます。
日タイムラインの DnD は同一曜日内(`CourseDayTablePanel.tsx:1922` `new_weekday: activeWeekday`)
なので通常は起きませんが、配置改善の曜日変更採用(`ImprovementSuggestionsSection.tsx:342`)は
`tplId` が解決できないと `new_course_template_id` を送らないため、この経路で発生し得ます。

---

## 5. 設計原則との整合

### 5-1. 「PFV が正 / マスタ・今週の二層分離」とは矛盾しない

`weekly-space-design.md` §3 の憲法:

> 1. 週空間の操作はマスタを一切変更しない。マスタ反映は ChangeScopeChoice 経由の明示昇格のみ。
> 4. 同じ事実を二重に持たない。**週空間=courses+visits+staff_events が唯一の「今週の正典」**。

そして PO 決定 2026-07-09 (`careflow-staff-assignment-source`):
①**PFV のコースが正** ②スタッフ後付け ③不足は警告(隠さない) ④マスタ駆動。

この 2 つは矛盾しません。役割分担が明確だからです:

| 問い | 正典 |
|---|---|
| 毎週どういう型で回すか / 来週以降どうなるか | **PFV** (週生成・固定枠戻し・型の昇格) |
| **今週この時間は空いているか / 今週この人は動けるか** | **今週の visits + courses**(憲法 4) |
| 患者の受け入れ可能範囲・基本の訪問時間 | **weekly_pattern(希望)** (`base-visit-minutes-design.md`) |

**「今週の盤面に対する提案」の占有ソースが今週の visits であるべきなのは、憲法 4 の当然の帰結**であり、
実装も既にそうなっています。G1〜G3 は「週の正典を読み切れていない」バグであって、
思想の変更ではありません。

### 5-2. 「週生成=PFV 正 / 新規提案=希望 正」の二重ソースとも整合

`base-visit-minutes-design.md` の表がそのまま生きています:

| 経路 | 使う分数 | 現状 |
|---|---|---|
| 週生成 (Layer1) | PFV.duration_min | `layer1_expander.py:171-178` そのまま。**変更対象外** |
| 新規配置の提案 (空き枠提案/プール投入/詰まり解消) | weekly_pattern.service_minutes | `_patient_to_pool_candidate` `schedule_v2.py:3736-3757` そのまま |
| 既存枠を動かす提案 (改善提案/範囲最適化) | PFV.duration_min を維持 | `improvement_engine.py` の `cand.service_minutes=pfv.duration_min` |

つまり **「何分の枠か」は今も PFV/希望が正で構わない**。是正が必要なのは
**「その枠を今どこに置いてよいか(占有・担当・可動)」の判断材料**だけです。

### 5-3. 唯一 衝突するのは G4/G5 の「可動域を週へ移す」案

`pin-and-movability-spec.md` の 2 軸:

- 🔴 赤 = `PFV.movability='locked'` — **型**の属性・毎週効く・エンジンだけを縛る
- 🔵 青 = `visits.week_pinned` — **今週**の属性・その週だけ・人手もブロック

`Visit` に `movability` 列は無く `week_pinned` しかありません(`visit.py:134`)。
したがって「提案対象を週 visit 起点にする」を素直にやると、
**PFV を持たない週限定訪問(＋訪問・プール配置)の可動域が定義できません**。
また `scope_optimizer` / `unblock_search` が共有する `state_token` は PFV 指紋で定義されており
(`scope_optimizer.py:291-300`)、409 の意味論まで変わります。

→ **ここだけは別設計項目として切り出すべき**。妥当な落とし所は
「PFV があればその movability を継承、無ければ `unknown`(要確認)扱い + 青ピンは常に不可侵」。

### 5-4. 推奨方針

**A. 用語と役割を UI で明示する (設計変更なし・効果大)**

「この提案は**今週の盤面**に対するものです」と明示し、
`change_scope`(型/今週) の 2 択を提案 UI 全体で統一する。scope-optimization には既にあるので、
配置改善・propose-slots の採用ボタンも同じ語彙に揃える。

**B. 「週の正典を読み切る」— 共有ローダの是正 (本命)**

`load_week_course_buckets` に以下を入れる。**新しい切替パラメータは作らない**
(週提案は常に週が正、という一本化のほうが憲法 4 に忠実で、分岐の保守コストも無い):

1. `status.in_(planned, in_progress, completed)` — 診断・実現性チェックに揃える (G1)
2. `Course` を OUTER JOIN 化 + `course_id IS NULL` 用の合成バケット (G2)
3. 担当を `visit.primary_staff_id ?? course.assigned_staff_id` に (G3・`assign-candidates` に揃える)
4. バケットの曜日を `course.weekday` から `visit.visit_date.weekday()` へ (例 F の根治)

**C. 切替パラメータを導入するなら、場所は「提案対象の列挙」であって「占有」ではない**

もし将来 PO が「毎週の型として研ぎ澄ます提案」と「今週だけの応急処置」を分けたいなら、
導入すべきフラグは占有ソースではなく **提案対象(subject)の集合**です:

```
subject_source: "pfv"   → PFV 行を対象に列挙 (= 現状。毎週の型の改善)
subject_source: "week"  → 当週 visits を対象に列挙 (= 今週だけの手直し。PFV 無い訪問も対象)
```

占有(existing)は**どちらのモードでも常に今週の visits**。これが憲法 4 と
「週生成=PFV 正/新規提案=希望 正」の両方を壊さない唯一の切り方です。

**D. 触らないもの**

- `layer1_expander`(週生成) — 自分が書き換える週を読むのは循環設計。現行の
  `manual_week`/`import`/`week_pinned` 日次スキップという保護方式が正解。
- `reset_visits_to_fixed`(固定枠に戻す) — PFV から書き戻すのが機能定義そのもの。
- 週ドリフトを型へ畳む正規ルートは既存: `POST /patients/fixed-visits/from-week-bulk`
  (`patient_fixed_visits.py:796`) / `POST /patients/{id}/sync-week-visits-to-fixed`
  (`patient_sync.py:345`)。

---

## 6. 段階案

### Phase 1 — S 規模・即効 (共有ローダ 1 ファイル)

| # | 内容 | ファイル | 規模 | リスク |
|---|---|---|---|---|
| 1-1 | **G1**: 占有 status を `(planned, in_progress, completed)` に拡張 | `propose_slots_service.py:284` | **S** | M — 占有が増え候補が減る。propose-slots のスナップショット系テストが動く |
| 1-2 | **G5**: `compute_state_token` に週 visits 指紋を合成 | `scope_optimizer.py:291-300`(先例 = `pool_bulk_inserter.py:218-265`) | **S** | M — 409 が出やすくなる(安全側) |
| 1-3 | `feasibility_basis` を `"week_visits"` に訂正 | `schedule_v2.py:4594` / `improvement_suggestion.py:175` / `improvementSuggestion.ts:147` | **S** | L — FE 表示未使用 |
| 1-4 | 例 F の根治: バケット曜日を `visit.visit_date.weekday()` 由来に | `propose_slots_service.py:326,349` | **S** | M |

**1-1 だけで、propose-slots / pool-overview / pool-bulk / 配置改善 / 最適化 / 詰まり解消の
6 機能が同時に是正されます。最優先。**

### Phase 2 — M 規模 (突合キーと担当軸)

| # | 内容 | ファイル | 規模 | リスク |
|---|---|---|---|---|
| 2-1 | **G4**: PFV↔週 visit の突合を曜日非依存に (患者単位フォールバック)。見つかった週 visit の曜日/時刻を「現在位置」とする | `improvement_engine.py:505-551`, `scope_optimizer.py:998-1032`, `unblock_search.py:797-806` | **M** | M — delta の基準が変わるため Before/After の見え方が変わる |
| 2-2 | **G3**: 担当を visit 単位で解決 | `propose_slots_service.py:338` + 判定側 `:723,739,756,560,1163` | **M** | **M〜H** — N-3「コース割付を正とする」は意図的な設計。PO 確認が要る |
| 2-3 | **G2**: OUTER JOIN + course-less 合成バケット | `propose_slots_service.py:280` / `schedule_health.py:136` / `acceptance_matrix_service.py:435` / `layer3_assignment.py:3092`(NULL 用第2パス) | **M** | M — バケットキー `(office_id, weekday, course_code)` に course-less の居場所が無い |
| 2-4 | **G7**: 代替候補に週負荷を導入 | `substitute_candidates.py:255-275, 915` | **M** | L |

### Phase 3 — L 規模 (要 PO 判断・別設計)

| # | 内容 | 規模 | リスク |
|---|---|---|---|
| 3-1 | **提案対象(subject)の週基準化** — `subject_source: "pfv"|"week"` の導入。週限定訪問(PFV 無し)も提案対象に。可動域は「PFV があれば継承・無ければ unknown」 | **L** | **H** — `Visit` に `movability` が無い。ピン 2 軸仕様(`pin-and-movability-spec.md`)の拡張が要る |
| 3-2 | **G6**: 受け入れ枠にスタッフ次元を導入 | **L** | **H** — `CourseState.remaining_count` はコース定員ベースで、契約が変わる |
| 3-3 | 廃止済み `full-optimize` / `diff-add` の後始末 (削除、または `g21_new_algorithm` を既定 ON にして復活) | M | M — `_load_before_visits_v2` は status フィルタ欠落(cancelled を占有に数える)のため要修正 |

### 推奨着手順

**Phase 1-1 → 1-2 → 2-1 → 2-3 → 2-2**。
1-1 は 1 行の述語変更で 6 機能に効き、PO が最も強く体感している「診断の言うことと提案の言うことが
違う」の主因(例 B)を直接潰します。2-1 は「手で動かした患者だけ提案が出てこない」(例 A)の根治。
Phase 3 は思想の拡張なので、Phase 1-2 の運用フィードバックを見てから設計に入るのが安全です。

---

## 7. 付録 — 参照実装 (この 3 つに合わせれば正しい)

1. **`feasibility_check.load_week_items`** `backend/app/services/scheduling/feasibility_check.py:541-590`
   — OUTER JOIN Course / `status != 'cancelled'` / 担当は course ∥ visit のフォールバック(`:636`)。
2. **`substitute_candidates.load_day_rows` + `_owner_of`** `:255-275`, `:236-243`
   — visit 単位の担当解決。
3. **`pool_bulk_inserter.compute_bulk_state_token`** `:218-265`
   — 週 visits と PFV の**両方**を指紋化する楽観ロック。
