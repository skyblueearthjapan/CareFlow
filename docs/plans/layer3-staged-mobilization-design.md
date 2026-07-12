# 自動スタッフ割当 4段ソルバ（マネージャー動的動員＋ローテ緩和）設計書 v1.1

作成 2026-07-12 / ステータス: **v1.1（Stage 1-3）は本番稼働済み `a695b1c`。v2.0 = §11 要件訂正（Q3全廃）＋拠点跨ぎ救援・実装中**
★v2.0 の要件訂正により、§0.2欠陥2・§1決定3・§2 Stage 3・§7の該当記述は §11 が上書きする（履歴として残置）。
v1.1 実績: BE=Stage 2/3＋via＋通知（テスト105 pass）・FE=通知2セクション＋チップ（vitest 1197 pass / 0 fail・tsc 0）。
code-reviewer 最終レビュー CRITICAL/MAJOR 0（MINOR 3件は同日反映: Gini母集団の単一ソース化・
テスト命名精密化・ガード意図コメント）。ディレクターレビュー指摘1件（通知の committed 絞り込み）反映済み。
v1.1 変更点: criticレビュー（ACCEPT-WITH-RESERVATIONS→条件反映）— M-1 StaffAssignment実型
（Pydantic BaseModel）への修正、M-2 通知重複の整理方針、M-3 Giniスコア除外規定、
MINOR 6件（Q3早期return構造変更の明示・戻り値float維持・共通ヘルパー抽出方針・
FE開閉条件・書き換えテスト列挙・コスト合算注記）、ロールバック/性能/新関数IF追記
関連: `docs/plans/session-2026-07-10-HANDOFF.md` §8-9・`docs/plans/trainee-accompaniment-design.md` §8・
メモリ `careflow-staff-assignment-source.md`（PFV正の設計原則）

---

## 0. 背景（2026-07-12 調査で確定した事実・再調査不要）

### 0.1 本番データが示す問題

- 過去9週（W20〜W28）で自動割当の結果が採用されたのは **W21・W27 の2週のみ**。他7週は全コース未割当のまま
- 直近12日間で assign-staff-only **25回**・unassign-all-staff **12回**。「実行→不満→全解除→再実行」の反復運用
- W27実績: 熊澤（manager）が救済枠で毎日1コース・計5コース、川名（manager）は0コース
  （救済枠が staff_id UUID昇順の決定的選択のため。熊澤 `70727ae8…` < 川名 `89b9aeb9…`）
- 稲毛の体制: staff 2名（宇田川・高岡）+ manager 2名（川名・熊澤）+ 新人1名（髙梨）。
  1日最大4コース（A〜D）に対し、ハンガリアン法の対象は staff 2名のみ

### 0.2 構造的欠陥（コード根拠つき）

| # | 欠陥 | 根拠 |
|---|---|---|
| 1 | manager は1st passから除外され、greedy救済（コスト計算なし・UUID昇順）でのみ配置。ローテ・患者継続性・履歴が一切考慮されない | `layer3_assignment.py:1717-1720`（除外）・`2384-2484`（greedy救済・docstring「副次的最適化より配置できることを優先」） |
| 2 | 「前週同一コースコード禁止」ハード除外が**曜日不問・週単位**のため、完全に埋まった週の翌週は少人数拠点で構造破綻（W27全割当→W28で宇田川が全コード禁止）| `layer3_assignment.py:2089-2096`（Q3ハード除外）・`3181-3233`（履歴は週単位で code のみ）・`ROTATION_EXCLUSION_WEEKS=1`（:140） |
| 3 | 救済は greedy 1件ずつのため、コース間の組合せ最適が働かない | `layer3_assignment.py:2455-2482` |

## 1. PO確定事項（2026-07-12・全判断の基準）★

| # | 決定 |
|---|---|
| 1 | **マネージャー動員は不足日だけ**（現体制では実質毎日不足だが、原則は「足りない日の救済」。1st passに最初から混ぜない — Phase G-29時のユーザー要望と同一思想を維持） |
| 2 | **マネージャー間の優先順位は同等**。特定個人・拠点の差別化はしない（他事業所展開前提・マスタ駆動原則） |
| 3 | **「候補が1人もいないときだけ」前週同コード除外をハード→ソフトに緩和**することに合意。性別制限・勤務シフト・イベント重複・拠点制約は**ハードのまま**（絶対に緩和しない） |
| 4 | スタッフマスタのロール登録（熊澤=manager）は**変更しない**（クライアント設定を尊重）。挙動はロジック側で改善する |

## 2. 設計: 4段ソルバ

`solve()` の曜日ループ内（`layer3_assignment.py:1747-1785`）で、現行の
「1st pass → greedy manager fallback」を「Stage 1 → Stage 2 → Stage 3」に置き換える。
Stage 4 は既存レビューフロー（変更なし）。

### Stage 1: 正規スタッフのハンガリアン法（現行どおり・変更なし）

- 候補 = `eligible_staff`（role≠manager かつ非新人）。コスト関数・ハード制約すべて現行のまま

### Stage 2: マネージャー動員ハンガリアン（新設・greedy救済の置換）

- **発動条件**: Stage 1 完了後、当該曜日に未割当コースが残っている場合のみ（=「不足日」の実効定義。
  1件も残らなければマネージャーは一切使われない）
- **候補プール**: `role='manager'` かつ `is_trainee=False` かつ 当日未割当（固定Mコース担当済み manager は除外＝現行救済の条件4と同じ）
- **解法**: 未割当コース × 候補マネージャーで**2回目のハンガリアン法**。コストは既存
  `_cost_single_cell` を**そのまま**使う（勤務シフト・性別・イベント・拠点＝ハード、
  β コースローテ・W16前日・患者中心ローテ・Wave5ジッタ＝ソフト、Q3前週同コード＝ハード）
- **効果**: マネージャーにもローテーションと患者継続性が効き、履歴（`_load_rotation_history` は
  role を問わず全 staff_assigned コースを含む）により**川名・熊澤が自然に交代**する。
  UUID昇順タイブレークの固定偏りが消える
- マネージャー間の同等扱い（PO決定2）は「コスト差のみで選ばれる」ことで実現。個人・役職への
  優先度定数は導入しない
- 全マネージャーが当該コースにハード制約違反（全セルINF）の場合、そのコースは埋めずに
  Stage 3 へ送る（現行greedyの「_try_fallback_manager_for_course が None」と同等の挙動）
- Stage 2 で確定した割当は `via='manager_mobilized'` を付す（§3）

### Stage 3: ローテ緩和ハンガリアン（新設）

- **発動条件**: Stage 2 完了後もなお未割当コースが残っている場合のみ
- **候補プール**: 当日未割当の全員（正規スタッフ＋マネージャー、非新人）
- **解法**: 3回目のハンガリアン法。コスト関数に `relax_rotation_exclusion=True` を渡し、
  **Q3「前週同一コースコード」除外セルのみ** `HUNGARIAN_INFINITY` → 新定数
  `COST_ROTATION_RELAXED_VIOLATION`（ソフト大ペナルティ）に差し替える。
  **他のハード制約（性別・シフト・イベント・拠点）は INF のまま**（PO決定3）
- 緩和セル経由で確定した割当は `via='rotation_relaxed'` を付し、警告として通知（§4）
- Stage 3 でも埋まらないコースは現行どおり未割当 → `unassigned_warnings`（真の人員不足）

### Stage 4: レビューフロー（既存・変更なし）

- 患者連続（代替候補あり）→ review_items、不可避連続 → auto_committed_notices、
  性別override → review、apply-staff-review での承認反映——すべて現行のまま。
  Stage 2/3 の割当も既存の rotation_conflicts 検出（`solve():1795-1812`）・
  working_recent 前進伝搬（:1820-1827）・prev_day_pairs 伝搬（:1785）の**対象に含める**
  （曜日ループ内の同じ位置で day_assignments に合流させるため構造的に保証される）
- Stage 2/3 の割当が患者連続に該当した場合の挙動も既存どおり:
  代替候補あり→review_items（承認まで未確定）、不可避→auto_committed_notices（自動確定）。
  通知の重複整理は §4.1 参照

### 定数

```python
COST_ROTATION_RELAXED_VIOLATION: float = 200_000.0
# 根拠: 通常ソフトコスト（β≦~20・W16=100・ジッタ≦10）を確実に支配しつつ、
# 患者中心ローテ最上位 COST_PATIENT_RECENT_1 (1e6) より小さい
# （「同じ患者に直近と同じ担当」の回避を「前週同コード回避」より優先する既存序列を保つ）。
# COST_PATIENT_RECENT_3 (2e5) と同水準 = 「起きてよいが最後の手段」。
# 注記: 同一セルで両方発生時は合算 400_000 だが COST_PATIENT_RECENT_1 (1e6) 未満のため
# 序列逆転は起きない（criticレビューMINOR-6で確認済み）。
```

### W28 への適用予測（設計の妥当性確認用・実データ）

月曜（A,B,C,D / W27履歴: 宇田川={A,B,C,D}, 高岡={A,B,C}, 熊澤={A,C,D}, 川名={}）:
- Stage 1: 高岡→D（宇田川は全コードINF）
- Stage 2: 熊澤→B・川名→A or C（コスト最小側）
- Stage 3: 宇田川→残り1コース（前週同コード緩和・警告つき）
- 結果: **4/4 割当・1回の実行で完了**。通知=「マネージャー動員2件・前週同コース1件」

## 3. 実装詳細

### 3.1 `StaffAssignment` に `via` フィールド追加

実型は **Pydantic BaseModel**（`layer3_assignment.py:299-307`・criticレビューM-1反映）:

```python
class StaffAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekday: int = Field(ge=0, le=6)
    course_code: str
    course_id: UUID
    staff_id: UUID
    via: str = "hungarian"  # "hungarian" | "fixed" | "manager_mobilized" | "rotation_relaxed"
```

- デフォルト値つき追加＝既存生成箇所・既存テストの等価比較は非破壊（両辺同デフォルト）
- fixed 経路（M コース固定等）は `via="fixed"` を明示（判別可能性のため）
- **DBには永続化しない**（内部表現＋レスポンス集計用）。apply-staff-review 経路
  （`schedule.py:2147` 付近）で構築される StaffAssignment はデフォルト "hungarian" のままでよい
  （将来 via を永続化する場合に "review_applied" 等を検討 — 現段階ではスコープ外）

### 3.2 変更ファイルと関数

| 対象 | 変更 |
|---|---|
| `layer3_assignment.py` 行列構築共通化 | `_solve_one_day` :1961-2005 の「コスト行列構築→ハンガリアン→INFフィルタ」を **共有ヘルパー `_solve_matching()` に抽出**（criticレビュー曖昧性解消: 解釈A採用。`_solve_one_day` を fixed 空で呼び回す方式は採らない）。`_solve_one_day` は固定割当処理（:1895-1949）後にこのヘルパーを呼ぶ形へリファクタ |
| `_solve_matching()`（新設ヘルパー） | 引数: `weekday, courses, pool, history, prev_day_pairs, events_by_staff, week_monday, iso_year, iso_week, patient_recent_staff, relax_rotation_exclusion=False`。戻り値: `list[StaffAssignment]`（via は呼び出し側で付す） |
| `_solve_stage2_managers`（新設） | 未割当コース×フリーマネージャー（role='manager'・非新人・当日未割当）を `_solve_matching(relax_rotation_exclusion=False)` で解き、結果に `via='manager_mobilized'` を付して返す |
| `_solve_stage3_relaxed`（新設） | 未割当コース×当日フリー全員（非新人）を `_solve_matching(relax_rotation_exclusion=True)` で解く。**via判定は呼び出し側で Q3 条件（history 内に weeks_ago≦ROTATION_EXCLUSION_WEEKS かつ同 course_code・同 staff_id）を再評価**し、緩和セル経由なら `via='rotation_relaxed'`、そうでなければ `via='hungarian'` |
| `solve()` :1763-1775 | `_apply_manager_fallback` 呼び出しを Stage 2 → Stage 3 の順次呼び出しに置換 |
| `_cost_single_cell` :2089-2096 | `relax_rotation_exclusion: bool = False` 引数追加。**戻り値は float のまま**（tuple化しない — criticレビューMINOR-2: 呼び出し元10箇所超への波及回避）。True のとき Q3 判定は **早期 return をやめてペナルティ変数への加算に構造変更**し、後続のソフトコスト（β・W16・患者中心・ジッタ）を通常どおり加算する（criticレビューMINOR-1） |
| `_apply_manager_fallback` :2384-2484 / `_try_fallback_manager_for_course` | **撤去**（Stage 2 が後継。呼び出し元は solve():1768 の1箇所のみ — criticレビューで検証済み） |
| `solve()` Gini計算 :1829-1838 | `rotatable_assignments` から **`via='manager_mobilized'` を除外**（分子にmanager・分母にmanager不在の歪み防止 — criticレビューM-3反映。`rotation_relaxed` は正規スタッフ由来のため除外しない） |
| `Layer3Result` | 変更なし（via は assignments 内に含まれる） |
| `schedule.py` `_assign_staff_only_impl` | レスポンス組み立てで via を集計し §4 の新フィールドを構築 |

### 3.3 諸原則の維持（回帰させないこと）

- **1スタッフ1日1コース**: Stage 2/3 のプールは「当日未割当の者」のみで構築（構造的保証）
- **新人除外**: 全 Stage で `is_trainee=False` フィルタ（同行設計 §8 の回帰テストを維持）
- **決定性**: ハンガリアン法＋Wave5決定的ジッタのみ。乱数・時刻依存を持ち込まない
- **read-only提案との分離**: 変更は solve 内で完結。`_persist` の書込み3点セット
  （courses.assigned_staff_id / VSA / visits.primary_staff_id）は不変
- **マスタ駆動**: 拠点名・個人名・ロール名のハードコード禁止。挙動はすべて role / is_trainee /
  シフト / 履歴のデータ駆動
- **拠点非依存**: 4段ソルバは office を特別扱いしない（拠点ハード制約がコスト内で分離を保証）。
  3拠点以上への展開でもロジック変更不要
- **g21 / l3_fix_primary_staff フラグ経路**: fixed_staff_by_course の仕組みは不変。
  実装時に `_apply_manager_fallback` の全呼び出し元を grep し、置換漏れがないこと

## 4. API レスポンス拡張（非破壊・追加のみ）

`AssignStaffOnlyResponse`（`schedule.py:1483`）に追加:

```jsonc
{
  // 既存フィールドはすべて不変
  "manager_mobilized_notices": [   // Stage 2 で動員されたマネージャー
    { "course_id": "...", "weekday": 0, "course_code": "B",
      "staff_id": "...", "staff_name": "熊澤　妙子" }
  ],
  "rotation_relaxed_notices": [    // Stage 3 で前週同コード緩和が発生した割当
    { "course_id": "...", "weekday": 0, "course_code": "A",
      "staff_id": "...", "staff_name": "宇田川　優莉" }
  ]
}
```

- 既存クライアントへの影響なし（additive・default 空配列）
- FE スキーマ（`frontend/lib/queries/assign_staff_only.ts`）に既存パターン
  （`.default([]).catch([])`）で optional 追加

### 4.1 既存通知との重複整理（criticレビューM-2反映）

マネージャー動員コースは `_detect_unavoidable_consecutive`（manager を代替候補から除外・
`layer3_assignment.py:1526-1528`）により「不可避連続」= `auto_committed_notices` にも
同一コースが載り得る（W27実績から常態化見込み）。方針:

- **BE は両リストを独立に返す**（それぞれ別の事実を表す: 動員=誰が不足を埋めたか／
  不可避連続=患者継続性の警告。BE側でどちらかを間引くと情報が欠落する）
- **FE 側で重複を視覚整理する**: `auto_committed_notices` の各エントリについて、
  同一 course_id が `manager_mobilized_notices` に存在する場合は
  「👔マネージャー動員」チップを併記し、同じ件だと一目で分かるようにする
- `rotation_relaxed_notices` も同様（同一 course_id が review/auto_committed に載る場合はチップ併記）

## 5. UI 仕様（AssignWarningDialog.tsx への追加）

既存の `auto_committed_notices`（🔵不可避連続・お知らせ折りたたみ）と同じ**情報通知の意匠**で
2セクションを追加（承認操作は不要・確定済みのお知らせ）:

- **「👔 マネージャー動員」**: 「スタッフ不足のため、以下のコースにマネージャーを割り当てました」
  ＋ 曜日・コース・氏名の一覧
- **「🔁 前週と同じコース」**: 「候補がいないため、前週と同じコースを許容して割り当てました」
  ＋ 一覧
- 2セクションとも 0 件なら描画しない。review_items が 0 件でも上記通知が 1 件以上あれば
  ダイアログ（またはtoast内サマリ）で件数を通知する（**不足を隠さない原則③**）
- **ダイアログ開閉条件の更新**: `CourseDayTablePanel.tsx:2349` の
  `items.length > 0 || notices.length > 0 || unresolved.length > 0` に
  新2フィールドを追加する（criticレビューMINOR-4）
- §4.1 のチップ併記（重複の視覚整理）を実装する
- 文言は R-10b 規約（0件のとき断言しない）に準拠

## 6. スコープ外（明示）

1. 稼働スタッフカウント（`count_active_staff_per_weekday`）と⚠不足バナーの分母見直し
   — 別論点としてPOと相談（本設計はバナーに触れない）
2. 週またぎ先読み（来週の自由度を守るコスト）— Stage 3 で破綻が止まるため保留
3. assign-staff-only の非冪等性（再実行で別解）— 既存挙動のまま
4. 熊澤氏のロール登録変更 — 行わない（PO決定4）
5. via の DB 永続化 — 行わない（§3.1）

## 7. テスト観点

### BE（pytest・`backend/tests/`）

1. **Stage 2 発動条件**: スタッフ数 ≥ コース数の日はマネージャーが1件も割当されない／
   不足日のみ動員される
2. **マネージャー同等ローテ**: 2名のマネージャー＋履歴を与え、動員が履歴コストで交代する
   （UUID順固定にならない）ことを2週シミュレーションで検証
3. **Stage 3 発動条件**: Stage 2 まで（＝マネージャー動員を含めて）候補が残っている限り緩和されない／
   全滅時のみ Q3 が緩和される／**性別・シフト・イベント・拠点は緩和後も INF のまま**
4. **via の正しさ**: hungarian / fixed / manager_mobilized / rotation_relaxed の判別
5. **回帰**: 新人除外（全Stage）・1日1コース原則・不可避連続 auto_commit・
   Gini計算（manager_mobilized 除外）・既存 `test_layer3*.py` スイートが全通過
6. **W28実況再現**: §2 の予測シナリオを fixture 化（2 staff + 2 manager + 前週全埋め履歴 →
   4/4割当・動員2・緩和1）
7. エンドポイント: 新レスポンスフィールドのスキーマ・空配列デフォルト

**書き換え対象の既存テスト（criticレビューMINOR-5で列挙済み）**:
- `test_layer3_phase_g29.py`（manager fallback 全8テスト → Stage 2 仕様へ書き換え）
- `test_layer3_phase_g90_office_hard.py`（`test_manager_fallback_*` 3テスト）
- `test_layer3.py:192`（`test_hard_constraint_manager_excluded` — Stage 1 除外自体は不変・
  「最終結果に manager が現れない」前提の assertion があれば Stage 2 発動有無で調整）
- `test_layer3_no_duplicate.py`（`test_w25_manager_not_duplicated*` 2テスト）

### FE（vitest）

- 新フィールドを含む/含まないレスポンス両方でダイアログが正しく描画
- 通知のみ（review 0件）のときの表示経路・開閉条件
- §4.1 チップ併記（動員かつ不可避連続の同一コース）
- 基準値 **1185 pass / 0 fail** 維持（`pnpm vitest run --exclude "e2e/**"`）

## 8. フェーズ分割・性能・ロールバック

| Phase | 内容 | 完了条件 |
|---|---|---|
| **A** | BE: `_solve_matching` 抽出＋Stage 2/3 実装＋fallback撤去＋via＋レスポンス拡張＋テスト | backend 対象テスト green・既存スイート回帰なし |
| **B** | FE: スキーマ＋AssignWarningDialog 2セクション＋チップ＋テスト | vitest 1185+ pass / 0 fail |
| **C** | 最終レビュー（コードレビュー・設計整合・handoff追記） | レビュー指摘 CRITICAL/MAJOR 0 |

- **性能**: 曜日あたりハンガリアン法が最大3回になるが、行列は「未割当コース×フリー要員」に
  縮小していくため計算量は現行比 +数ms オーダー（n≦10 規模の O(n³)）。事業所拡大時も
  コース数はハンガリアン正方化の n を線形に増やすのみで実用上問題なし
- **ロールバック**: migration なし・コードのみ。`git revert` → backend 再ビルドで完全復元。
  feature flag は導入しない（旧 greedy と新 Stage 2 のハード制約は同一で、埋まる集合は
  greedy ⊆ Stage2 のため退行リスクが低い — criticレビューSkeptic観点で確認済み）
- デプロイ: 標準手順（pg_dump → pull → build → up → healthz）。実施はPO/ユーザーの指示を待つ

## 9. リスク・割り切り

1. **Stage 3 の緩和は「品質の劣化を許容して埋める」判断**。警告で必ず可視化し、看護品質の
   最終判断は管理者に残す（勝手に隠して埋めない）
2. マネージャー動員が恒常化している実態（現体制では毎日不足）は本設計では解消しない。
   採用/体制の問題として不足バナー（スコープ外1）で継続可視化
3. 旧 greedy fallback の「必ず埋める」性質は、ハード制約が同一のため Stage 2（組合せ最適化）が
   上位互換。万一の差分は W28 再現テスト（テスト観点6）で検出する
4. `_cost_single_cell` の引数追加は既存呼び出し（gender override :2363・不可避判定 :1537 含む）
   すべてデフォルト値 False で非破壊とする

---

## 11. v2.0（2026-07-12 夜）: 要件訂正による再設計 — Q3全廃＋拠点跨ぎ救援
（criticレビュー REVISE → M-1〜M-6・MINOR・欠落事項をすべて反映した確定版）

### 11.0 PO要件の訂正（最優先・本設計書の関連記述をすべて上書き）★

- **「前週に担当したコースは翌週禁止」というルールはPO要件に存在しない**（PO明言 2026-07-12）。
  Q3ハード除外は過去フェーズ実装のレガシーで、要件由来ではない。v1.1 §1決定3は
  「ルールが存在する」前提の合意だったため**無効**（ルール自体を撤去する）。
- 真の要件は「**同じ患者さんを連続で担当するのを避けたい。拠点をまたいででも**」のみ。
  正しい実装は既存の**患者中心ローテペナルティ**（`COST_PATIENT_RECENT_1/2/3 = 1e6/5e5/2e5`・
  患者ごとの直近担当3名を4週遡り・拠点無関係に追跡）＋連続レビューフロー。
  criticレビューで定量検証済み: 4名体制でも DEPTH=3 により毎日ペナルティゼロの候補が最低1名残る
  ＝Q3なしで日次・週次ローテが成立する。
- 拠点跨ぎの追加確定（PO回答 2026-07-12）: ①**救援（レベル1）のみ** ②発動時は
  **お知らせ・警告・報告を必ず表示** ③**スワップ許可** ④**マネージャーも対象**（同等）
- 運用モデルの確定（criticレビューOpen Question裁定・ディレクター決定）:
  跨ぎ救援は**自動確定＋警告表示**（Stage 2動員と同じ扱い。POの表現は「お知らせ・警告・報告」で
  あり承認フローの要求ではない）。採用ガードは**未割当数のみ**で判定（コスト比較は持ち込まない
  — スワップ許可の趣旨は「埋まるなら入れ替えてよい」）。

### 11.1 変更一覧

| # | 変更 | 詳細 |
|---|---|---|
| 1 | **Q3ハード除外を全廃** | `_cost_single_cell` の Q3 分岐（INF return / relax時の加算）を削除。`relax_rotation_exclusion` 引数を両関数（_cost_single_cell/_solve_matching）から削除。`COST_ROTATION_RELAXED_VIOLATION` 定数＋コメントブロック（:192-206相当）を削除 |
| 2 | **βスキップも削除（criticレビューM-1裁定）** | βローテの `weeks_ago <= ROTATION_EXCLUSION_WEEKS: continue` ガードを削除 → weeks_ago=1 が weight 1.0（最重）で正しく減衰に乗る。これにより `ROTATION_EXCLUSION_WEEKS` は全参照が消えるため**定数ごと削除**。β自体・W16前日ペナルティは存置（微小タイブレーク） |
| 3 | **旧Stage 3（ローテ緩和）を撤去** | `_solve_stage3_relaxed` メソッド本体・solve()内の呼び出し・`via='rotation_relaxed'`・`rotation_relaxed_notices`（BE schema/集計・FE Zod/セクション/チップ/トースト/開閉条件）を撤去。via docstring（StaffAssignment）・Giniコメント・`_detect_unavoidable_consecutive` docstring（「直近1週同コード除外」記述 :1500-1502, :2572相当）も追随更新 |
| 4 | **新Stage 3 = 拠点跨ぎ救援** | §11.2 |
| 5 | 通知・報告 | §11.3 |

### 11.2 新Stage 3: 拠点跨ぎ救援（rescue re-solve）— 実装仕様（criticレビューM-2/M-3/M-4/M-6反映）

**発動条件**: Stage 2（マネージャー動員）後も当該曜日に未割当コースが残る場合のみ。

**solve() ループ内フロー（正確な挿入位置と置換セマンティクス）**:

```python
day_assignments = stage1結果      # fixed(via='fixed')＋Hungarian(via='hungarian')
day_assignments += stage2結果     # via='manager_mobilized'
rescue = self._solve_stage3_cross_office(
    weekday=..., day_courses=..., day_assignments=day_assignments,
    staff_pool=staff_pool, fixed_staff_by_course=..., ...)
if rescue is not None:            # 採用ガード通過時のみ非None
    rescue_swaps_all.extend(rescue.swaps)
    day_assignments = rescue.assignments   # ★日全体を置換
# ↓ 以降は既存コード不変。all_assignments への追加・prev_day_pairs・
#   rotation_conflicts・working_recent は最終 day_assignments を読むため、
#   置換がこの位置なら下流5経路すべて構造的に整合する（append後の巻き戻し不要）
```

**`_solve_stage3_cross_office` の中身**:
1. **fixed の扱い（解釈A・criticレビュー曖昧性裁定）**: `fixed_staff_by_course` のうち
   **Stage 1 で検証を通過して実際に割当済みのもの**（= day_assignments 内の via='fixed'）だけを
   温存し、その (course, staff) を再解の対象から除外する。**検証に失敗して free に落ちた
   fixed 指定（例: 都賀A木曜の性別失敗）は再解の対象に含める**（でないと木曜ケースが解けない）
2. 再解: 温存fixed以外の**その曜日の全コース × 当日勤務の全要員**（非新人・staff+manager・
   全拠点・温存fixedで消費済みの者は除外）を `_solve_matching(relax_office_constraint=True)` で解く
3. **採用ガード**: 「温存fixed＋再解結果」の未割当コース数が、元の day_assignments の
   未割当コース数より**厳密に少なく、かつ元々カバーされていた全コースが引き続きカバーされている
   （退行なし）場合のみ採用**（None を返せば元を維持）。
   これにより office_id=None（全拠点一括）でも per-office でも安全:
   全体不足（コース数>要員数）の週では数が減らないため元の結果が維持され、無駄な入替は起きない。
   また「再解が既存カバーを捨てて別のコースを充足する解」を無報告で採用することを防ぎ、
   担当を失うコースが構造上発生しないためスワップ報告の網羅性も保証される
4. **swaps の算出（criticレビューM-4）**: 採用時、元 day_assignments と採用結果を course_id で
   突合し、staff_id が変わった行を `RescueSwap(course_id, weekday, course_code,
   before_staff_id, after_staff_id)` として収集。**`Layer3Result` に
   `rescue_swaps: list[RescueSwap] = []` フィールドを追加**して返す
5. **via 判定（優先順）**: `effective_office_for_weekday(weekday) != course.office_id` →
   `'cross_office'` ＞ role='manager' → `'manager_mobilized'` ＞ その他 `'hungarian'`。
   温存fixedは `'fixed'` のまま
6. 例外時は None 返却で Stage 1+2 結果を無傷維持（best-effort・既存Stageの流儀）

**`relax_office_constraint` の配線（criticレビューM-2(b)）**:
- `_cost_single_cell` と `_solve_matching` の両方に `relax_office_constraint: bool = False` を追加
- True のとき Phase G-90 拠点チェック（:2339-2342相当）は INF ではなく
  **`COST_CROSS_OFFICE_VIOLATION = 400_000`** をペナルティ変数に加算して続行
  （性別・シフト・イベント・新人は従来どおり INF＝聖域）
- 序列: 患者連続1回前(1e6) > 跨ぎ(4e5) > W16(100) > β(≦~20) > ジッタ(≦10)。
  「同じ患者に連続で当てるくらいなら越境する」= PO表明と整合

**既存応援ロジックとの関係（criticレビュー検証済み）**: `effective_office_for_weekday` の
secondary転入（primary休業日のみ発火）と本救援（両拠点営業日でも発火）は条件が排他で二重加点なし。

**呼び出しモードの注意（criticレビューM-6）**: 現運用（スケジュール画面の一括実行=office_id
なし）は1回の解で全拠点を同時に見るため先行者利益問題なし。**per-office で順に実行すると
先に実行した拠点が他拠点スタッフを消費し得る**——handoff に運用注意として記載し、
跨ぎ救援を期待する週は全拠点一括実行を推奨とする（コードでの禁止はしない）。

### 11.3 API/UI（お知らせ・警告・報告 = PO必須要件）

- **`CrossOfficeNoticeSchema`（新設・既存StageAssignmentNoticeSchemaとは別型）**:
  `{course_id, weekday, course_code, course_office_name, staff_id, staff_name,
  staff_office_name}` → `cross_office_notices`（**警告**・越境した割当）
- **`RescueSwapNoticeSchema`（新設）**: `{course_id, weekday, course_code,
  before_staff_name, after_staff_name}` → `rescue_swap_notices`（**報告**・入替行。
  before は同一実行内の Stage 1+2 解であり DB 既存値ではない旨を docstring 明記）
- **両方とも committed_course_ids で絞る**（既存通知と同じ・レビュー送り分は載せない）。
  office 名解決は offices テーブルから bulk load（Staff.name 解決と同様）
- 削除: `rotation_relaxed_notices`（BE/FE両方。FE Zod は .catch があるため新旧互換とも安全）
- FE Zod: 新2フィールドは既存パターン `.default([]).catch([])` で optional 追加
- FE（AssignWarningDialog）: 「🚗 拠点をまたぐ応援（◯件・確定済み）」= **warning系トーン**
  （既存🟧残留違反に近い強い意匠・POの「警告」要望）＋「🔄 応援による入れ替え（◯件）」=
  お知らせトーン。🔁前週同コースセクション/チップ/州の開閉条件・トースト文言から
  rotation_relaxed を撤去し、新2フィールドを開閉条件・トーストに追加
- 文言は R-10b 規約準拠

### 11.4 テスト観点と書き換え対象一覧（criticレビューM-5反映）

**新規テスト**:
1. Q3全廃: 前週同コードのスタッフが通常割当可能（旧挙動の反転）
2. βスキップ削除: weeks_ago=1 が rotation_count に weight 1.0 で加算される
3. 患者連続回避の主導（既存回帰維持）: recent持ちより他候補優先・不可避はauto-commit
4. Stage 3発動条件: Stage 2まで埋まれば発動しない／採用ガード（減る時のみ採用・
   減らなければ元と完全一致・全体不足週では発動しても不採用）／性別・シフト・イベントは
   越境でもINF／新人除外・1日1コース・決定性
5. **木曜都賀A再現fixture**: female_only患者×男性1名拠点＋隣接拠点に女性 →
   スワップで全充足・via='cross_office' ≥1・rescue_swaps ≥1・通知が committed 絞り込み
6. fixed温存: 検証通過fixedは再解でも不変／検証失敗fixedのコースは再解対象
7. レスポンス: 新2フィールドのスキーマ・空配列デフォルト

**書き換え対象の既存テスト**:

| ファイル | 対象 | 措置 |
|---|---|---|
| `test_layer3_staged_mobilization.py` | 旧Stage3系（stage3_relaxes_q3/stage3_not_triggered 等）・via 4値検証・W28再現・スキーマ既定値の rotation_relaxed 参照（計~10箇所） | 旧Stage3テストは削除or跨ぎ救援テストへ置換。W28再現は「4/4割当・動員2・**緩和0・宇田川はStage 1のhungarianで入る**」へ書き換え。via検証は cross_office を含む4値へ |
| `test_layer3.py` | `test_q3_hybrid_excludes_last_week_assignment` | 反転（前週同コードでも割当可能・βソフトのみ効く）へ書き換え |
| `test_layer3_rotation_conflict.py` | Q3前提の前提コメント/回避fixture 2箇所 | fixture前提の調整（挙動アサーションは患者連続なので原則維持） |
| `AssignWarningDialog.test.tsx` | rotation_relaxed セクション/チップ/開閉/既定値の4テスト | cross_office/swap セクションのテストへ置換 |

### 11.5 本番W28への影響・移行・ロールバック

- 確定済みW28割当は触らない。PO再実行で新ロジック（Q3なし）により解き直される
- migrationなし・コードのみ。デプロイは標準手順・ユーザー指示待ち
- ロールバック: git revert で v1.1（本番 a695b1c）へ復元可

### 11.6 実装フェーズ

| Phase | 内容 |
|---|---|
| A2 | BE: Q3全廃＋βスキップ削除＋旧Stage3撤去＋新Stage3（跨ぎ救援）＋Layer3Result.rescue_swaps＋通知＋テスト |
| B2 | FE: rotation_relaxed撤去＋cross_office/swap 2セクション＋Zod＋テスト |
| C2 | ディレクターレビュー→code-reviewer→handoff/メモリ更新 |
