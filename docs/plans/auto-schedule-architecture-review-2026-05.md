# 自動算出ロジック アーキテクチャレビュー (2026-05)

**目的**: CareFlow の auto_allocator_v2.py を中心とする自動スケジュール算出ロジックの全体像を読み解き、hotfix 積み重ねで崩れた設計の整合性を一度棚卸しして、ユーザー (スケジュール業務責任者) と合意するための意思決定資料にする。

**スコープ**: read-only 調査。実装変更なし。手動 D&D / PFV 直接編集経路は対象外。

**前提知識**:
- PFV = patient_fixed_visits (患者の固定枠マスタ、mode=normal が通常枠)
- Visit = 1 週分の実 visit レコード (DB の visits テーブル)
- Course = 1 スタッフ × 1 日 (午前 + 午後) を表す枠
- 「全面最適化」= 機能 B (mode=full_optimize で全 active 患者を再配置)
- 「差分追加」= 機能 A (mode=diff_add で固定枠未登録患者だけプール展開)

---

## 1. 現状アーキテクチャ全体図

### 1.1 ファイルレベル

| ファイル | 行数 | 役割 |
|---|---|---|
| backend/app/services/scheduling/auto_allocator_v2.py | 5,313 | v2 本体 (Stage 1-5 + 4 経路 apply) |
| backend/app/api/v1/schedule_v2.py | 1,314 | 4 つの endpoint (FastAPI) |
| backend/app/services/scheduling/auto_allocator.py | 1,943 | v1 (legacy, 現運用は v2 のみ) |
| backend/app/services/scheduling/layer1_expander.py | 1,334 | weekly_pattern -> visits 展開 (v1 経路) |
| backend/app/services/scheduling/layer2_clustering.py | 798 | K-means クラスタリング (v1 経路、v2 では未使用) |
| backend/app/services/scheduling/layer3_assignment.py | 1,704 | スタッフ割付 (v1 経路) |

注: v2 は K-Means を捨て距離グリーディに切り替えたため、layer2 (v1) は v2 経路で呼ばれない。layer1/layer3 も v2 では概念のみ流用しており、v2 のメインロジックは auto_allocator_v2.py ファイル単独で閉じている。

### 1.2 Stage 1-5 (read-only パイプライン: run_v2_pipeline)

run_v2_pipeline (auto_allocator_v2.py:3650) が読み取り専用で提案を返す。DB は書き換えない。

```
[Stage 0: 入力]  iso_year, iso_week, office_ids, mode, pending_edits
        |
[Stage 1] プール作成
  - _load_active_patients()          全 active patient
  - _load_patients_with_fixed()      PFV を持つ patient 集合
  - mode=diff_add  -> PFV 無し + 孤児 PFV あり
  - mode=full_opt  -> 全 active
  - _build_pending_edit_overlay()    今週限定 overlay (DB 書込なし)
  - _load_before_visits_from_pfv()   Before スナップショット
  - build_visits_for_pool()          weekly_pattern or PFV から V2Visit 展開
        |
[Stage 1.5] H5 + H10 フィルタ
  - _filter_unavailable_and_lunch()  受入カレンダー× と昼休憩 12-13 除外
  - mode=full_opt は H5 (受入×) をスキップ、H10 (昼) は両モード強制
        |
[Stage 1.7] _consolidate_same_address_time()   同住所同時刻ソフト集約
  - 同 (office, weekday, address_bucket) 群を「最多 start_time」に寄せる
  - time_type 制約で動かせない visit は warning だけ出して放置
  - 「固定」は常に動かさず warning 出力のみ ★後述の矛盾 (3)
        |
[Stage 2] split_into_buckets()
  - (office_id, weekday, AM/PM) でバケット分け
        |
[Stage 3] cluster_by_distance_greedy()
  - 距離グリーディで 2-3 名 / セット (= V2Set) に
  - _enforce_h2_same_address()           同住所 3 名以上は別 set に分散
  - _enforce_h2_split_overflow()         (上の補強)
        |
[Stage 4] enforce_course_count_constraint()
  - スタッフ数 × 容量 (6 名 / 480 分) で絞り込み
  - _emit_staff_shifts_data_health_warning()  staff_shift 未投入警告
        |
[Stage 5] combine_am_pm_sets() + course_code 割付
  - 午前 set と 午後 set を距離最小でペアリング
  - 通常 A/B/C/D/E はスタッフ数で動的に絞る
  - 超過は M / M2 / M3 ... (マネージャー数で制限)
  - Fix A (#102 Fix B): existing_codes 採用時に他 set と衝突したら fallback で別コード
  - 超過 set の patient は course_code=None -> unassigned_visit_ids に積む
        |
[Stage 5.5] after_visits から unassigned 除去
        |
[Stage 6] _apply_travel_time_to_courses()    ★ Fix E / lunch 再検証 / 5 分切上 / バッファー 8 分
  - コース内 start_time 昇順ソート
  - _reorder_same_address_consecutive()   同住所ペアを連番に
  - _auto_shift_same_time_conflicts()     Fix E: 異住所同時刻を自動シフト
  - 各 visit の actual_start を time_type 別に決定:
    - 固定 : 動かさず、shortage>=5 分なら course_code=None
    - 時間帯: window 内 earliest、超過なら earliest+warning
    - 午前 : 12:00 超なら 13:00 にバンプ可能性判定
    - 午後 : 13:00 以降、18:00 超なら警告
    - 終日 / None: 制約なし
  - 非固定は _round_up_to_5min() で 5 分刻みに切り上げ
  - 昼休憩 12-13 再検証 (重なれば 13:00 にバンプ or warning)
  - 累積移動 30 分超なら course_long_distance warning
        |
[Stage 7] _check_course_capacity_minutes()   480 分制約
[Stage 8] _check_two_staff_availability()    二人組訪問
[Stage 9] _identify_unassigned_patients()    未割当患者抽出
        |
[出力] proposal_batch_id, before_visits, after_visits, pool_visits,
       warnings, staff_count_by_weekday, unassigned_patients
```

### 1.3 4 つの DB 書き込み経路

```
+---------------------------+
| run_v2_pipeline (R/O)     |  <-- /v2/diff-add, /v2/full-optimize
| Stage 1-9 で提案を返す     |      (DB 変更なし)
+---------------------------+
           |
           | ユーザーが UI で確認 -> 「採用」「この週だけ」「リセット」
           |
   +-------+-------------------+--------------------+
   v       v                   v                    v
+------+ +--------------+ +----------------+ +--------------+
| A    | | B            | | C              | | D            |
| /v2/ | | /v2/         | | /v2/           | | (legacy:     |
|apply-| |apply-week-   | |reset-to-       | | visit 直編集 |
|indiv | |only          | |fixed           | | = 対象外)    |
+------+ +--------------+ +----------------+ +--------------+
   |       |                   |
   v       v                   v
 PFV 更新  visits 更新         visits 再生成
 (来週   (今週だけ、PFV       (PFV から)
  以降    変更なし)
  も継続)
```

#### 各経路がどの helper を経由するか

| Stage / helper | A: apply-individual | B: apply-week-only | C: reset-to-fixed | (read-only: run_v2_pipeline) |
|---|:---:|:---:|:---:|:---:|
| _load_active_patients | o | o | o | o |
| _build_pending_edit_overlay | x | o | x | o |
| _filter_unavailable_and_lunch | x | x | x | o |
| _consolidate_same_address_time | x | x | x | o |
| _reorder_same_address_consecutive | x | x | x | o (経由) |
| **_auto_shift_same_time_conflicts (Fix E)** | x | x | x | o (経由) |
| _apply_travel_time_to_courses | x | x | x | **o のみ** |
| _check_course_capacity_minutes | x | x | x | o |
| _detect_cross_address_time_conflicts | o (境界 422) | x (撤去済) | △ (warning only) | x |
| H10 境界チェック (lunch_break) | o (422) | △ (warning) | x | (filter で除外) |
| visit soft-delete | x | o | o | x |
| visit INSERT | x | o | o | x |
| PFV upsert | o | x | x | x |
| course_id 解決 | x | o | o | x |
| スタッフ割付 | x | o (plan の assigned_staff_id) | o (rotation) | x |

**重要な発見**: `_apply_travel_time_to_courses` (= Fix E / lunch 再検証 / 5 分切上 / バッファー 8 分 を内包する関数) は `run_v2_pipeline` 内でしか呼ばれない。**A / B / C の 3 つの apply 経路はこの関数を経由せず、提案された start_time / end_time をそのまま (= overlay 適用程度の補正で) DB に書く。**

---

## 2. 各 Fix の関係性 + 適用範囲マップ

### 2.1 各 Fix の概要

| Fix | 目的 | 実装位置 | 適用範囲 |
|---|---|---|---|
| **Fix A** (#102 Fix B / Stage 5 衝突防止) | Stage 5 で異 set が同じ course_code を取り合った時、fallback で別コードに振り直して「同コース同時刻 2 名」を防ぐ | run_v2_pipeline 内 Stage 5 (auto_allocator_v2.py:4014-4192) | **run_v2_pipeline のみ** |
| **Fix B** (apply-individual 422) | apply-individual で他患者既存 PFV と (weekday, start_time, course_template_id) 重複 + 異住所 -> 422 で拒否 | apply_individual_proposal 内 (auto_allocator_v2.py:5153-5239) + endpoint catch | **apply-individual のみ** |
| **Fix C** (Layer 3 拡張) | 旧 layer3 のスタッフ割付ロジック (v1 経路) | layer3_assignment.py | v2 経路では未使用 |
| **Fix D** (元 3 経路 422) | apply / apply-week-only / reset-to-fixed の 3 経路で異住所同時刻を 422 で拒否 | (D1) apply-individual / (D2) apply-individual で実装、(D3) reset-to-fixed で実装、apply-week-only でも実装していた | 部分的に **撤去** (下記) |
| **Fix D 撤去** (#112 / #113 hotfix) | apply-week-only / reset-to-fixed の 422 拒否を撤去して warning log のみに | apply_week_only_endpoint:875-923, reset_visits_to_fixed:4914-4951, apply_week_only_endpoint の H10 violation も撤去 | 該当 2 経路 |
| **Fix E** (auto_shift) | 同コース異住所同時刻 2 名以上を距離最適化で順序決定 + 後者を時刻シフト (固定でも動かす) | _auto_shift_same_time_conflicts (auto_allocator_v2.py:2301-2506)、_apply_travel_time_to_courses から呼ばれる | **run_v2_pipeline のみ** |
| **#108** (バッファー 8 分 + 5 分切上) | 訪問間バッファー 8 分 + 非固定 visit の 5 分刻み切り上げ | 定数 VISIT_BUFFER_MINUTES=8 (auto_allocator_v2.py:101) + _round_up_to_5min (auto_allocator_v2.py:668)、_apply_travel_time_to_courses 内で適用 | **run_v2_pipeline のみ** |
| **#115 設計提案** (同住所同時刻+倍 duration) | 同住所ペアを「同 start_time + duration 合算占有」に。_align_same_address_pair_to_same_time 新規 + _apply_travel_time_to_courses に挿入 | 未実装 (提案段階) | (実装すれば run_v2_pipeline のみ) |

### 2.2 抜け漏れ表

| 機能 | run_v2_pipeline (= 提案画面表示時) | apply-individual | apply-week-only | reset-to-fixed |
|---|:---:|:---:|:---:|:---:|
| 異住所同時刻ガード | o Fix E で自動シフト | o Fix D2 で 422 | △ warning log のみ (Fix D 撤去) | △ warning log のみ (Fix D 撤去) |
| 同住所ペアの扱い | △ 配列順だけ揃える (時刻は別個) | x チェックなし | x チェックなし | x チェックなし |
| H10 (昼休憩 12-13) | o filter で除外 + travel 後再検証 | o 422 | △ warning log のみ (#113 で撤去) | x チェックなし |
| バッファー 8 分 + 5 分切上 | o | x (提案された時刻をそのまま使う) | x | x |
| 移動時間で時刻調整 | o (_apply_travel_time_to_courses) | x | x | x |
| 同住所 3 名以上の警告 | o | x | x | x |
| 二人組訪問 (requires_multiple_staff) | o | x | x | x |

**この表が示す重大な事実**: 「提案画面に出る Before/After は Fix E / バッファー / lunch 再検証を全て通っているが、apply 後に DB に入る visit は、ユーザーが提案画面で見た通りの時刻 (= 既に補正済) を信用してそのまま書く」設計。つまり、

- 補正は **提案生成時にしか走らない**
- ユーザーが提案画面で時刻を手で書き換えたり、reset-to-fixed で PFV から直接生成したり、apply-individual で 1 件だけ採用した場合、各種補正 (Fix E / バッファー / lunch) は **走らない**
- 結果として apply 経路ごとに「衝突が起きうるかどうか」「起きた時どうするか」が分裂

---

## 3. 設計上の矛盾 + ロジックホール

ここがレビューの中心。hotfix 積み重ねで生まれた整合性のズレを列挙する。

### 矛盾 (1) Fix E が auto_shift で生んだ衝突を本人が再検証しない

_auto_shift_same_time_conflicts (auto_allocator_v2.py:2301-2506) は固定時刻でも例外的に動かす (line 2324)。シフト先で:

- 昼休憩 12-13 に突入したらどうなる?
- shortage threshold (5 分) 判定はどうなる?

-> シフト直後には何も検証していない。後段の _apply_travel_time_to_courses 本体 (line 2599 以降) の earliest_start 計算で吸収される **想定**。だが本体ループは「shift で動いた cur.start_time」をそのまま desired_start として使うため、Fix E でシフトされた visit が再度 shortage 判定にかかると 5 分以上不足扱いで course_code=None (= 未割当) に落ちる可能性がある。

**実害**: 「固定時刻だが auto_shift で動かされた visit が、その後の shortage 判定で未割当化される」というシナリオが論理的に存在する。production で踏むかは未確認だが、コードパス的には open。

### 矛盾 (2) 同住所ペアは「配列順だけ」揃え、時刻は別個

_reorder_same_address_consecutive (auto_allocator_v2.py:2140) は同住所ペアを **配列上で隣接** させるだけ。時刻は _apply_travel_time_to_courses 本体の earliest_start ロジックで個別に決まる。同住所間は travel=0 + buffer=0 なので、前者の end_time が後者の start_time にそのままなる (= 連続)。

しかしユーザー要件 #115 は「**同住所ペアは同 start_time + 倍 duration**」。例えば家族 2 人を 9:00-10:00 (60 分 1 枠) で訪問するのを「9:00-9:30 -> 9:30-10:00 (連続)」ではなく「9:00 開始の 60 分占有」として扱いたい。これは:

- スタッフ目線で 1 件の長時間訪問
- 衝突判定上の占有開始時刻は同じ
- end_time だけ実 service の合算

現状コードはこれを満たしていない (連続配置のみ)。提案 #115 で新規 helper を入れる必要あり。

### 矛盾 (3) 固定時刻の扱いが 3 helper で違う

| helper | 固定時刻の扱い |
|---|---|
| _consolidate_same_address_time (line 1374) | _can_move_to_time が time_type==固定 で False を返す -> 動かさず warning のみ |
| _auto_shift_same_time_conflicts (Fix E, line 2301) | 固定でも **例外的に時刻を動かす** (line 2324) |
| _apply_travel_time_to_courses 本体 (line 2619) | 固定は動かさず shortage>=5 分なら course_code=None で未割当化 |

**同じ「固定時刻」をどう扱うか、3 つの helper で方針が違う**。設計者は「Fix E は同時刻衝突解消の方が優先度高い」と判断したが、_consolidate_same_address_time (同住所集約) と _apply_travel_time_to_courses 本体 (shortage) との一貫性は崩れている。

### 矛盾 (4) 4 経路で衝突ハンドリング方針が分裂

| 経路 | 異住所同時刻が起きたら | H10 違反 (lunch 12-13) | 思想 |
|---|---|---|---|
| run_v2_pipeline (提案生成) | Fix E が auto_shift で解消 | filter で除外 + travel 後再検証 | 自動修復 |
| apply-individual | Fix D2 で 422 拒否 | 422 拒否 | strict gate |
| apply-week-only | warning log のみ (#112 hotfix で撤去) | warning log のみ (#113 hotfix で撤去) | best effort |
| reset-to-fixed | warning log のみ (#112 hotfix で撤去) | チェックなし (PFV ベース) | best effort |

**思想が 3 種類混在**:
1. 自動修復 (run_v2_pipeline)
2. strict gate (apply-individual)
3. best effort = log だけで通す (apply-week-only / reset-to-fixed)

ユーザーが apply-individual で衝突拒否される一方、apply-week-only では何も言わずに「衝突したまま」DB に入る。「同じ衝突」が経路によって違う扱いを受ける。

### 矛盾 (5) LUNCH_START/END が定数 12:00-13:00 固定

auto_allocator_v2.py:117-118 で LUNCH_START = time(12, 0), LUNCH_END = time(13, 0) が hard-code。ユーザー要件は「lunch フレキシブル化 (11:30-12:30 開始、長さ 45-60 分)」。

現状コードで lunch が登場する箇所:

- _filter_unavailable_and_lunch (line 3103)
- _apply_travel_time_to_courses の lunch 再検証 (line 2867 以降)
- _is_in_lunch_break (apply-individual 境界、apply-week-only 境界、apply_week_only サービス内)
- AM/PM 境界判定 (AM_BLOCK_END=12:00, PM_BLOCK_START=13:00)

-> lunch を可変化すると **少なくとも 6 箇所** の修正が必要。さらに AM/PM 境界も連動するので determine_am_pm (line 697) も影響。

### 矛盾 (6) warning type が 11 種 + unassigned reason 11 種 = 22 種類のラベルが UI へ

auto_allocator_v2.py 内の V2WarningType Literal (line 216-244):

1. same_address_consolidation
2. course_capacity
3. course_long_distance
4. course_count
5. acceptance_blocked
6. travel_time_shortage
7. two_staff_shortage
8. diff_add_conflict
9. data_health_staff_shifts_missing
10. auto_time_shift_for_conflict (Fix E)
11. general (= 分類困難)

加えて UnassignedReason (line 251-263) で未割当理由 11 種:

no_coordinates, no_primary_office, no_weekly_pattern, acceptance_calendar, course_capacity, course_overflow, manager_short, same_address_split, fixed_time_conflict, lunch_break, unknown

-> 合計 22 種類のラベルが UI に降ってくる。命名規則も統一されておらず (acceptance_blocked vs acceptance_calendar、course_capacity が warning 側でも reason 側でも存在)、ユーザー側で「これは何の警告か」を即座に判別しづらい。

### 矛盾 (7) diff_add の orphan PFV 救済が hotfix 連発

auto_allocator_v2.py:3720-3801 のコメントで「W41 v2.8 hotfix#1 / #2 / #3 / #4」と 4 つの hotfix が積み重なっている。要旨:

- PFV あるが weekly_pattern=null + 今週 visit なし = 孤児 patient
- 旧仕様では完全に未割当扱い
- 段階的に救済仕様を追加 -> 結果として「PFV ベースで V2Visit 展開する」経路と「weekly_pattern ベースで V2Visit 展開する」経路が併存

-> 救済仕様は機能するが、コードの読み解きコストが高い。今回のレビュー対象ではないが、本格的な再設計時には Stage 1 ロジックの簡素化検討が必要。

### 矛盾 (8) apply-week-only の「旧予定 + 新提案」混在仕様

apply-week-only (auto_allocator_v2.py:4571-4664) は旧バグで「unassigned 患者の旧 visit が DELETE される」事故があった -> P1 修正で DELETE 対象を「patient_visit_plans に含まれる patient のみ」に限定。

その結果、unassigned 患者の旧 visit は **そのまま残る** -> 「旧予定 + 新提案」が混在した状態を warning で通知する設計に。ユーザー判断で手動整理を促す。

**問題**: 「全面最適化を適用したのに、未割当患者の旧 visit が残る」状態は混乱を招く。ユーザーが想定する「全面最適化 = 全週を新提案で置き換える」と乖離する。これは仕様か、hotfix の副作用か、合意が必要。

---

## 4. 意思決定が必要な設計判断リスト

ユーザーと議論する論点。実装フェーズに入る前に合意したい。

| # | 論点 | 選択肢 | 推奨 | 影響範囲 |
|:---:|---|---|---|---|
| **1** | 同住所ペアの時刻 | (a) 同 start_time + 倍 duration / (b) 連続配置 (現状) / (c) 配列順のみ揃え時刻別個 | **(a)** ユーザー方針 #115 と整合 | _apply_travel_time_to_courses 内に _align_same_address_pair_to_same_time 新規 (#115 案: 約 90-110 LOC、テスト 3-4 件 更新) |
| **2** | 異住所同時刻ガード方針 | (a) **4 経路全部で Fix E** / (b) 4 経路全部で 422 拒否 + UX 警告 / (c) 提案画面のみ Fix E、apply 経路は warning | **(a)** ただし apply-individual の 422 は救済経路として残す | (a) なら apply-week-only / reset-to-fixed / apply-individual に _auto_shift_same_time_conflicts 適用 helper を新規 (再利用可能 helper 化が必要) |
| **3** | lunch 仕様 | (a) フレキシブル 11:30-12:30 開始 / 45-60 分長さ / (b) 固定 12:00-13:00 (現状) | **(a)** ユーザー要件で確定済 | LUNCH_START / LUNCH_END 定数を patient or office 単位の動的取得に変更。AM/PM 境界 (AM_BLOCK_END, PM_BLOCK_START) も連動 |
| **4** | 固定時刻例外 (auto_shift 対象) | (a) auto_shift で動かす (現状 Fix E) / (b) 動かさず warning のみ / (c) 動かす vs 動かさないを time_type=固定 のサブカテゴリで分岐 | **議論必要** 現状 Fix E は (a) を選んでいるが矛盾 (3) のとおり一貫性が崩れている。ユーザーの「固定」ラベルの意味次第 (絶対に動かない vs プラスマイナス 5 分は許容) | _auto_shift_same_time_conflicts, _consolidate_same_address_time, _apply_travel_time_to_courses の 3 helper で挙動統一 |
| **5** | apply 経路の衝突ハンドリング (422 vs warning) | (a) 全経路 422 拒否 / (b) 全経路 warning log + 続行 / (c) 個別判断 (現状: apply-indiv=422, 他=warning) | **議論必要** (a) は厳格だが業務詰まり、(b) は柔軟だが事故再発、(c) は分裂。ユーザー impact 次第。例: 「異住所同時刻は不可避 = 422 で止めても運用解決不能」なら (b)、「衝突は本質バグ = 止めるべき」なら (a) | endpoint 層 (schedule_v2.py) で統一 |
| **6** | warning type / UnassignedReason 整理 | (a) 現状 22 種を維持 / (b) UI 向けラベル 5-7 種に集約 / (c) 階層化 (大分類 + 詳細) | **(b) または (c)** ユーザー (非エンジニア) が理解できるラベルへ | V2WarningType Literal + UnassignedReason Literal + Frontend 表示マップ + zod スキーマ + i18n |
| **7** | apply-week-only の旧 visit 保持 (矛盾 (8)) | (a) 現状維持 (未割当患者の旧 visit は保持 + warning) / (b) 全削除 (旧バグ復活リスク) / (c) ユーザー確認ダイアログで選択 | **議論必要** 業務影響大 | apply-week-only サービス + Frontend 確認 UI |
| **8** | バッファー 8 分の異住所間適用範囲 | (a) 現状維持 (run_v2_pipeline 内のみ) / (b) apply-week-only / reset-to-fixed にも適用 | **(b)** 一貫性のため | apply-week-only / reset-to-fixed に _apply_travel_time_to_courses 相当を組み込む |

---

## 5. 推奨される統一実装方針 (Master Plan)

仕様が確定したらこの順で実装を進める。各 wave で executor + Codex+Opus クロスレビュー + 実機検証を挟む。

### Wave 1: 4 経路で衝突ハンドリングを統合 (= 論点 2, 5, 8 確定後)

| 項目 | 内容 |
|---|---|
| 目的 | apply-individual / apply-week-only / reset-to-fixed / run_v2_pipeline の 4 経路で「異住所同時刻 + バッファー + lunch + 5 分切上 + auto_shift」を共通 helper で処理する |
| 実装 | _normalize_visits_for_apply() 新規 (run_v2_pipeline の Stage 6 相当を切り出し) + 各 apply endpoint で呼び出し + 422 / warning 方針を論点 5 の確定に従って一本化 |
| LOC 推定 | +200, -50 (helper 切り出し + 各経路の呼び出し追加) |
| 工数 | 中 (2-3 日) |
| 影響範囲 | apply 系 3 endpoint + auto_allocator_v2.py の helper 切り出し |
| 既存テスト影響 | 約 30-50 件 (apply 系の expected が時刻ずれ) |

### Wave 2: 同住所ペア 同時刻 + 倍 duration (= 論点 1 確定後 #115 案)

| 項目 | 内容 |
|---|---|
| 目的 | 家族・施設の 2 名訪問を「同 start_time + duration 合算占有」として扱う |
| 実装 | _align_same_address_pair_to_same_time() 新規 + _apply_travel_time_to_courses 内で _reorder_same_address_consecutive の直後に挿入 + 後者の duration を「前者 + 後者」合算に書き換える or 占有時刻を同期 |
| LOC 推定 | +90 から +110 |
| 工数 | 小から中 (1-2 日) |
| 影響範囲 | run_v2_pipeline 経路のみ (Wave 1 が完了していれば apply 経路にも自動波及) |
| 既存テスト影響 | 約 3-5 件 (同住所ペア配置テストの expected が変わる) |

### Wave 3: lunch フレキシブル化 (= 論点 3 確定後)

| 項目 | 内容 |
|---|---|
| 目的 | LUNCH_START / LUNCH_END 定数を動的化。11:30-12:30 開始 / 45-60 分長さに |
| 実装 | (a) Office モデルに lunch_start_time, lunch_duration_min カラム追加 (migration) OR (b) global 設定 lunch_window_start, lunch_window_end, lunch_min_duration, lunch_max_duration の 4 値で表現 + 関数シグネチャに lunch_config 引数追加 |
| LOC 推定 | +150 から +200 + migration |
| 工数 | 中から大 (3-5 日) migration + production deploy の慎重さが要因 |
| 影響範囲 | 全 helper (lunch を参照する 6 箇所) + AM/PM 境界 (determine_am_pm) + 全 endpoint + Frontend (UI 表示) + zod schema |
| 既存テスト影響 | 約 80-120 件 (lunch 12-13 を前提にしている expected が動的化で fix 必要) |
| リスク | 本番 DB の migration、既存運用との互換性 |

### Wave 4: 警告 type / UX ラベル整理 (= 論点 6 確定後)

| 項目 | 内容 |
|---|---|
| 目的 | warning type 11 種 + unassigned_reason 11 種 を UI 向けラベル 5-7 種に集約 (or 階層化) |
| 実装 | V2WarningType Literal + UnassignedReason Literal を整理 + Frontend 表示マップ (warningLabelMap.ts) を新規 + zod schema 更新 + i18n 整理 |
| LOC 推定 | +100 から +150 (backend) + +200 (frontend) |
| 工数 | 中 (2-3 日) |
| 影響範囲 | warning を返す全 helper + Frontend 全画面 + zod + テスト |
| 既存テスト影響 | 約 50-80 件 (warning type の expected) |

### Wave 5 (optional): 矛盾 (7) orphan 救済の整理 + 矛盾 (8) 旧 visit 保持の UX 改善

論点 7 が (c) で確定すれば Frontend ダイアログ追加。

---

## 6. リスク + 注意事項

### 6.1 既存テストへの影響

CareFlow には backend pytest が 200+ 件 (推定) ある。Wave 1 から 4 で合計 **約 160-260 件** のテスト fix が必要。

- 時刻 expected ずれ系 (Wave 1, 2, 3)
- warning type ラベル変更系 (Wave 4)
- 期待挙動の変更系 (4 経路統合で「これまで通っていたケース」が変わる)

-> 各 wave で **テスト fix を含めた工数見積もり** が必須。executor 並列起動でテスト fix を分担。

### 6.2 Production deployed 状態との互換性

- **Wave 3 (lunch フレキシブル化)** は migration を伴う。本番 DB バックアップ (pg_dump) -> migration -> rollback 手順を事前検証する必要あり (CLAUDE.md の「デプロイ安全プロトコル」)。
- **Wave 4 (warning type)** は Frontend 表示マップとの整合性が崩れると UI が壊れる。backend / frontend を同 PR で deploy。
- **Wave 1 (4 経路統合)** は「これまで apply できていた」シナリオが warning に変わる可能性。既存運用の挙動が変わる旨をユーザーに事前周知。

### 6.3 ユーザー業務影響 (運用変更が必要な仕様変更)

| Wave | 業務影響 |
|---|---|
| 1 | apply 経路で「衝突警告 -> 自動修復された」visit が増える可能性。最終時刻がユーザーが見たものと違う場合がある (シフトされる) |
| 2 | 同住所ペアが UI 上で「1 件 60 分」に見える (= 2 件並ばない)。ユーザー視認の変更要トレーニング |
| 3 | lunch 時刻が patient or office ごとに異なる場合、運用上の確認手段が必要 (スタッフが当日の lunch 時刻をどう知るか) |
| 4 | warning メッセージが変わる。問い合わせ対応の手順書 update 必要 |

### 6.4 W41 v2.8 hotfix の温存 (= 矛盾 (7))

orphan PFV 救済の 4 つの hotfix は **温存** する方針推奨 (今回スコープ外)。これ自体が機能不全になっているわけではなく、コード可読性の問題のみ。将来の v3 移行時に再整理する。

### 6.5 別タスクの「スタッフ自動割付改善 (random + 連続防止 + 週跨ぎ)」との連動

ユーザー要件 6 (スタッフ割付改善) は本ドキュメントの対象外だが、Wave 1 で apply-week-only / reset-to-fixed のスタッフ rotation を触る場合、同時実施したほうがコンフリクト回避になる可能性。別 wave (Wave 0 or Wave 1.5) として独立計画を検討。

---

## 付録 A: 主要ファイル / 関数 参照表

| 機能 | パス | 行 |
|---|---|---|
| エントリ run_v2_pipeline | backend/app/services/scheduling/auto_allocator_v2.py | 3650 |
| _apply_travel_time_to_courses | 同上 | 2508 |
| _auto_shift_same_time_conflicts (Fix E) | 同上 | 2301 |
| _reorder_same_address_consecutive | 同上 | 2140 |
| _consolidate_same_address_time | 同上 | 1374 |
| _detect_cross_address_time_conflicts | 同上 | 465 |
| _filter_unavailable_and_lunch | 同上 | 3103 |
| LUNCH_START / LUNCH_END | 同上 | 117-118 |
| VISIT_BUFFER_MINUTES | 同上 | 101 |
| SHORTAGE_THRESHOLD_MIN | 同上 | 108 |
| V2WarningType Literal | 同上 | 216-244 |
| UnassignedReason Literal | 同上 | 251-263 |
| apply_individual_proposal | 同上 | 5073 |
| apply_week_only | 同上 | 4510 |
| reset_visits_to_fixed | 同上 | 4816 |
| CrossAddressTimeConflictError | 同上 | 450 |
| Endpoint: /v2/diff-add | backend/app/api/v1/schedule_v2.py | 432 |
| Endpoint: /v2/full-optimize | 同上 | 530 |
| Endpoint: /v2/apply-individual | 同上 | 611 |
| Endpoint: /v2/reset-to-fixed | 同上 | 735 |
| Endpoint: /v2/apply-week-only | 同上 | 826 |
| #112 hotfix (reset 422 撤去) | backend/app/services/scheduling/auto_allocator_v2.py | 4914-4951 |
| #112 hotfix (apply-week-only 422 撤去) | backend/app/api/v1/schedule_v2.py | 881-923 |
| #113 hotfix (apply-week-only H10 422 撤去) | backend/app/api/v1/schedule_v2.py | 859-879 |

## 付録 B: ハード制約 H1-H10 一覧

| ID | 内容 | 実装 helper |
|---|---|---|
| H1 | 週次統一 (同 patient_id は週通して同 start_time) | _consolidate_same_address_time 等 |
| H2 | 同住所ペアリング (最大 2 人) | _enforce_h2_same_address, _reorder_same_address_consecutive |
| H3 | 同住所連続性 | グリーディが自然に satisfy |
| H4 | 全訪問同スタッフ禁止 | Stage 5 で対応 |
| H5 | 受入カレンダー × 回避 | _filter_unavailable_and_lunch (Mode 1 のみ) |
| H6 | 実出勤枠遵守 | count_active_staff_per_weekday |
| H7 | 性別制限遵守 | 呼び出し側で check (本サービスは候補までで止める) |
| H8 | 新人単独訪問禁止 | is_trainee=false のみカウント |
| H9 | コース容量 6 名以内 + 480 分以内 | MAX_PATIENTS_PER_COURSE, COURSE_MAX_MINUTES, _check_course_capacity_minutes |
| H10 | 昼休憩 12:00-13:00 visit 禁止 | _filter_unavailable_and_lunch, _apply_travel_time_to_courses lunch 再検証, apply-individual 境界 |

---

**作成**: 2026-05-18
**作成者**: architect agent (read-only 調査)
**次のステップ**: director がこのドキュメントを読んでユーザーと意思決定セッション -> 論点 1-8 を確定 -> Wave 1 から順次 executor + クロスレビューで実装。
