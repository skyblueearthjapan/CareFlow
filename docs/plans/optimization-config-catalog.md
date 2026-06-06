# 自動最適化(全面最適化 v2) 事業所別設定化カタログ — Step 1 調査結果 (2026-06-06)

目的: 事業所ごとに最適化ルール(バッファ/移動/昼休み 等)を管理UIで設定可能にする。
本書は現行ハードコードの洗い出し(Step 1)。設計はユーザーと協議(Step 2)。

## 重要な前提・発見
- `POST /v2/full-optimize` (`schedule_v2.py:606-627`) は `iso_year/iso_week/office_ids/pending_edits`
  のみ受領。チューニング用パラメータは一切受けない=全て内部ハードコード。
- 現在 DB 駆動の事業所別設定は **2 つだけ**:
  - `Office.operating_weekdays` (稼働曜日, `office.py:34-39`)
  - `CourseTemplate.capacity_{mon..sun}` (拠点×コース×曜日の定員, `course_template.py:55-61`)
- ★ユーザー想定の `DEFAULT_DURATION=35` / `MIN_FREE_GAP=60` は**最適化ランタイム外**:
  35=取込スクリプト専用(`convert_grid_to_pfv.py:46`)、60=フロント専用(`freeGaps.ts:61`)。
  runtime の service_minutes は各患者の PFV/weekly_pattern 由来。
- v1 旧エンジン(`auto_allocator.py`)は未配線(deprecated)。別系統 `/allocate`(`allocation/engine.py`)
  は現役だが v2 とは独立(営業枠 09:00-18:00・別バッファ)。要・運用確認。

## 最優先(ユーザー指定: バッファ/移動/昼休み)
| 項目 | 現在値 | 定義 | 備考 |
|---|---|---|---|
| 訪問間バッファ `VISIT_BUFFER_MINUTES` | 8分 | `auto_allocator_v2.py:116` | propose_solver も import 再利用 |
| 移動速度 `TRAVEL_SPEED_KMH`(km→分換算) | 20km/h | `auto_allocator_v2.py:94,586` | ★「距離→移動時間」の本体knob。都市/郊外で最大の差 |
| 昼休み定数群(6つセット) | 11:30/12:30/13:30 開始幅 + 60/45/30分 三段 | `auto_allocator_v2.py:170-179` | `compute_lunch_window` が6定数に依存 |

## 高(営業構造)
| 項目 | 現在値 | 定義 |
|---|---|---|
| 営業枠 AM/PM `*_BLOCK_*` | 09:30-12:00 / 13:00-18:00 | `auto_allocator_v2.py:212-215` (+ `freeGaps.ts:52-55` フロント複製) |
| コードレンジ `_COURSE_CODES`(=staff数上限) | A-E(5) | `auto_allocator_v2.py:218`(※`layer2:56` は A-D で**不整合**) |
| overflow `_M_OVERFLOW_CODES`(=manager数) | M,M2..M9 | `auto_allocator_v2.py:226` |

## 中(制約閾値)
| 項目 | 値 | 定義 |
|---|---|---|
| コース所要上限 `COURSE_MAX_MINUTES` | 480分 | `auto_allocator_v2.py:98`(+propose重複) |
| コース定員 `MAX_PATIENTS_PER_COURSE` | 6 | **4ファイル重複**(下記)。`CourseTemplate.capacity` と二重管理 |
| 同住所許容 `SAME_ADDRESS_TOLERANCE` | 0.001(≒100m) | `auto_allocator_v2.py:154` |
| 同住所ペア最低占有 `SAME_ADDRESS_PAIR_MIN_OCCUPANCY` | 90分 | `auto_allocator_v2.py:187` |
| 移動不足判定 `SHORTAGE_THRESHOLD_MIN` | 5分 | `auto_allocator_v2.py:132` |
| 希望乖離 warning/unassigned `CARE_ALARM_*` | 30 / 60分 | `auto_allocator_v2.py:150-151` |
| 長距離コース警告 | 30分超 | `auto_allocator_v2.py:5122`(裸のマジックナンバー) |
| Layer3 event バッファ `BUFFER_MINUTES` | 15分 | `layer3_assignment.py:142` |

## 低(スコア重み・チューニング)
- propose-slots 重み: `_W_PROXIMITY=50 / _W_PREFERENCE=30 / _W_BALANCE=20 / _PAIR_BONUS=1000`
  (`propose_slots_service.py:57-63`)
- Layer3 コスト係数: `COST_ALPHA_DISTANCE=1 / BETA_ROTATION=5 / GAMMA_GENDER=inf / DELTA_WORKDAY=inf`
  + ローテ/前日同staff ペナルティ群 (`layer3_assignment.py:108-161`)
- k-means: `KMEANS_MAX_ITER=50 / N_INIT=10` (`layer2_clustering.py:69-72`)
- 目的関数: `calc_h_violations`(`auto_allocator_v2.py:5466`) は重み無しの違反カウント集計
  (H3/H5/H6/H7/H8 は 0 返し=別経路 enforce)。

## ★設定化の前提: 重複定数の単一ソース化が必要
1. `MAX_PATIENTS_PER_COURSE=6` … 4箇所 (`auto_allocator_v2:86`/`layer2:59`/`auto_allocator(v1):107`/`propose_slots:66`)
2. `COURSE_MAX_MINUTES=480` … 2箇所
3. `SAME_ADDRESS_TOLERANCE=0.001` … 2箇所
4. コードレンジ不整合 (v2=A-E / layer2=A-D)
5. 稼働曜日 default … `auto_allocator_v2:76` と `layer3:78` で二重
6. 営業枠/MIN_FREE_GAP のフロント複製 (`freeGaps.ts`)

## 設計の主要論点(Step 2 協議用)
### (a) 設定の伝播方式 — ★関数引数(dataclass注入)を強く推奨
- グローバル定数を DB 値で起動時上書きする方式は、propose-slots と full-optimize が
  同一定数を共有して挙動一致を保証している構造(`proposal_solver.py:8-18`)を壊し、
  並行 office 最適化でグローバル状態が競合する。
- `SchedulingConfig` dataclass を作り、ローダー `_load_office_scheduling_settings(office_id)`
  (既存 `_load_office_operating_weekdays` と同型パターン) で読み、パイプラインへ引数伝播。

### (b) 設定の置き場所
| 案 | 内容 | Pros / Cons |
|---|---|---|
| A office列拡張 | operating_weekdays の隣に列追加 | 既存踏襲/JOIN不要 ・ カラム爆発(20+) |
| B 専用 `office_scheduling_settings` テーブル | 1 office 1 行・各列 nullable + code default | 凝集/型安全/CHECK可 ・ テーブル+loader追加 |
| C JSONB `offices.scheduling_config` | `{buffer,travel,lunch{...}}` | スキーマ不要/柔軟 ・ 型/CHECK弱・typo検出不可 |
| D ハイブリッド | 高頻度=B(型列), 実験的重み=C(JSONB) | バランス良 ・ 2系統管理 |
→ 叩き台: **B(専用テーブル)+ code default fallback**。昼休み6定数の相互依存に CHECK を貼りやすい。

### (c) 第一弾スコープ(協議)
- 最小: バッファ/移動速度/昼休み + 営業枠 のみ(ユーザー指定+構造の核)。
- 段階拡張: 制約閾値 → スコア重み の順。スコア重みは事業所差小・当面共通でも実害少。
