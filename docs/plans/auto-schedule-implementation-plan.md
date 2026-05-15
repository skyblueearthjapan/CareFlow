# CareFlow スケジュール自動算出 実装計画書 v0.2

> **Status**: ドラフト v0.2 (critic レビュー反映、MVP 段階リリース戦略)
> **起稿日**: 2026-05-15
> **対象**: `docs/plans/auto-schedule-v1.md` (v0.4) の実装計画
> **位置付け**: **Wave 41 を 3 段階 (v1.0 / v1.1 / v1.2) に分割** して順次リリース
>
> v0.1 → v0.2 主な変更:
> - Critic 指摘 3 つの Critical (C1: Visit status / C2: H1/H2/H3 / C3: acceptance_calendar) を反映
> - MVP 段階リリース 3 ステップ採用
> - Phase 1 を 1a/1b/1c に細分化、Phase 5 を 5a/5b に分割
> - 既存 Layer 3 companion 機能との関係明確化
> - schedule_fix_service.py のパス修正

---

## 0. 文書の位置付け

- **何をする計画書か**: 設計仕様 v0.4 を 3 段階で実装するロードマップ
- **読者**: imaizumi (発注者) / Claude (実装者) / レビュワー
- **依存文書**:
  - `docs/plans/auto-schedule-v1.md` (v0.4) — 仕様本体
  - `docs/plans/v2-allocation-redesign.md` (Final v0.9) — 上位設計
  - `docs/plans/MASTER-PLAN.md` (Final) — Wave 全体計画
  - `docs/plans/v2-api-contracts.md` — API 契約

---

## 1. 既存実装の現況サマリー (修正版)

### 1.1 Backend: 既存ファイルマップ (パス修正版)

| Layer | ファイル (正しいパス) | 実装状況 | v0.4 対応 |
|---|---|---|---|
| **Layer 1** | `backend/app/services/scheduling/layer1_expander.py` | ✅ 動作 (W4) ※ delete-and-recreate パターン | 内部関数を **再利用**、auto_allocator から直接 expand_week は呼ばない |
| **Layer 2** | `backend/app/services/scheduling/layer2_clustering.py` | ✅ 動作 (K-Means + Haversine) | P3 で再利用 |
| **Layer 3** | `backend/app/services/scheduling/layer3_assignment.py` | ✅ 動作 (ハンガリアン法 + 4 週ローテ + **既存 companion 機能**) | P4 で companion 機能を活用 (新人同行) |
| **Schedule Fix** | `backend/app/services/schedule_fix_service.py` (※ `scheduling/` サブディレクトリ**外**) | ✅ 動作 | 採用フローで利用 |
| **TSP (P5)** | — | ❌ 未実装 | **v1.2 で新規** |
| **統合エントリ** | — | ❌ 未実装 | **v1.0 で新規** |

### 1.2 既存 Course.status (重要)

```python
# backend/app/models/course.py
CourseStatus = Literal["proposed", "course_fixed", "staff_assigned"]
```

→ **既に「提案 → 採用」の状態遷移を表現する仕組みは存在**。新規 migration 不要。

### 1.3 既存 Layer 3 companion 機能

```python
# backend/app/services/scheduling/layer3_assignment.py
# - staff_pool から trainee_ids を抽出
# - _resolve_companion_staff_id で staff_companion_assignments テーブルから companion 検索
# - visit_staff_assignments に companion を追加
```

→ 「事前マッピング」の companion 機能が**既に動作**。v0.4 Pattern A (動的に「ベテランに同行」付け) との差分は v1.2 で整理。

### 1.4 既存 Visit.status と監査必要箇所

```python
# backend/app/models/visit.py
VisitStatus = Literal["planned", "in_progress", "completed", "cancelled"]
```

→ v0.4 では **`visits.status` は触らない** (Course.status を使う設計)。既存クエリへの影響なし。

---

## 2. ギャップ分析 (修正版)

### 2.1 🔴 High (新規実装必須)

| # | ギャップ | 対応 Wave |
|---|---|---|
| G1 | **統合エントリ未実装** (`POST /schedule/auto-allocate`) | v1.0 |
| G2 | **モード 1/2 分岐なし** | v1.0 (Mode 1 のみ) → v1.1 (Mode 2) |
| G3 | **`courses.status='proposed'` を利用した提案フロー基盤** | v1.0 |
| G4-a | **基本 KPI 計算** (距離・偏差) | v1.0 |
| G4-b | **AI 自然文要約** | v1.1 |
| G4-c | **個別 Visit Justification** | v1.1 |
| G5 | **TSP (P5) 未実装** | v1.2 |
| G6 | **新人同行 (Pattern A)**: 既存 companion との関係整理 | v1.2 |
| G7 | **acceptance_calendar 統合**: auto_allocator の **slot generation** 段階で × フィルタ + △ ペナルティ | v1.0 |
| **G8** | **H1 (週次統一) / H2 (同住所ペアリング) / H3 (同住所連続性)** の実装 | v1.0 (H4-H8 と共に) |
| G9 | **proposal_batch_id 設計** (race condition 対策) | v1.0 |

### 2.2 🟡 Medium

| # | ギャップ | 対応 |
|---|---|---|
| G10 | sex_restriction ハード制約 (H7) | v1.0 (migration 0028 で既に拡張済み) |
| G11 | ローテーション履歴 4 週固定 → パラメータ化 | v1.1 |
| G12 | 距離計算 Haversine の重複統一 | v1.0 (auto_allocator 内で参照を一本化) |

---

## 3. MVP 段階リリース戦略 (v0.2 で新規追加)

```
[v1.0] Mode 1 + 基本 KPI ─────────────► 2 週間
   │
   ▼
[v1.1] Mode 2 + AI 要約 + Justification ──► 1 週間
   │
   ▼
[v1.2] TSP + 新人同行 + パラメータチューニング ──► 1 週間
```

### v1.0 (最小利用可能版, 2 週間)
- **入力範囲**: Mode 1 (差分追加) のみ — プール患者を既存固定枠の隙間に挿入
- **算出フェーズ**: P1 + P2 + P3 + P4 (TSP なし、訪問順は希望時刻に従う)
- **制約**:
  - ハード H1-H8 すべて (H1/H2/H3 を含む)
  - ソフトは S1 (距離) + S2 (偏差) のみ
- **説明可能性**: 基本 KPI バー (距離・偏差・違反数) のみ。AI 要約・Justification は v1.1
- **UI**: 算出ボタン + 進捗 + Before/After 並列 + 採用/破棄
- **既存への影響**: 最小限 (新規 course を proposed 状態で作るだけ)

### v1.1 (1 週間)
- Mode 2 (全面最適化) 追加
- AI 自然文要約 (テンプレベース)
- 個別 Visit Justification ポップアップ
- ソフト制約 S3-S7 を評価関数に組込み
- ローテーション履歴を可変パラメータ化

### v1.2 (1 週間)
- TSP (P5: コース内ルート順最適化)
- 新人同行 Pattern A 実装 (既存 companion との関係整理)
- 評価関数の重みチューニング
- 実データベンチマーク

---

## 4. 詳細実装計画

### Phase 0: DB スキーマ準備 (v1.0, 1 日)

| タスク | 内容 | 場所 |
|---|---|---|
| 0.1 | `courses` に `proposal_batch_id UUID NULL` 列追加 | migration `0029_courses_proposal_batch.py` |
| 0.2 | `courses` に `decision_log JSONB NULL` 列追加 | 同 migration |
| 0.3 | `proposal_batch_id` インデックス追加 | 同 migration |
| 0.4 | model 更新 | `backend/app/models/course.py` |

**承認ゲート**: schema 案を imaizumi に提示、Go サイン後 alembic upgrade

### Phase 1a: auto_allocator skeleton + Mode 1 (v1.0, 2 日)

| タスク | 内容 | 場所 |
|---|---|---|
| 1a.1 | auto_allocator サービス骨組み | 新規 `backend/app/services/scheduling/auto_allocator.py` (~250 行) |
| 1a.2 | Mode 1 のプール抽出ロジック | 同上 (active && fixed_visits 無し) |
| 1a.3 | proposal_batch_id 生成 + course 作成 (status=proposed) | 同上 |
| 1a.4 | Layer 1 の内部関数を再利用 (expand_week は呼ばず、`_expand_patient_fixed_visits` を直接) | 同上 |
| 1a.5 | unit test | `tests/services/scheduling/test_auto_allocator_mode1.py` |

### Phase 1b: ハード制約 H1-H8 実装 (v1.0, 2-3 日)

| タスク | 内容 |
|---|---|
| **1b.1 (H1)** | 週次統一: 患者ごとに「全曜日同 start_time」制約。slot generation 時に enforce |
| **1b.2 (H2)** | 同住所ペアリング: lat/lng 完全一致 (誤差 ≤ 0.001) で同一住所判定 → 同 timeslot 配置、最大 2 人 |
| **1b.3 (H3)** | 同住所連続性: course 内ルートで同住所 visit を連続配置 (TSP の制約として組込み、v1.0 は単純最近接で対応) |
| 1b.4 (H4) | 全訪問同スタッフ禁止: 患者の distinct(staff_id) チェック |
| 1b.5 (H5) | acceptance_calendar × 時刻回避 (slot generation 段階で実装) |
| 1b.6 (H6) | staff_shifts + weekly_overrides の実出勤枠遵守 (Layer 3 既存実装を再利用) |
| 1b.7 (H7) | sex_restriction (Layer 3 既存実装を活用) |
| 1b.8 (H8) | 新人単独訪問禁止 (is_trainee=true のみの visit_staff_assignments を rejected) |
| 1b.9 | 各制約の unit test |

### Phase 1c: acceptance_calendar 統合 + 基本 KPI (v1.0, 1-2 日)

| タスク | 内容 |
|---|---|
| 1c.1 | acceptance_calendar 読み込み | auto_allocator の slot generation 段階 |
| 1c.2 | × 時刻を候補から除外 | (H5 と連動) |
| 1c.3 | △ 時刻のペナルティ重み | (S7 と連動、v1.0 は重み 0、v1.1 で重み調整) |
| 1c.4 | 基本 KPI 計算 (距離・偏差・H 違反数) | auto_allocator 内に集計関数 |

### Phase 2: API 層 + 基本 UI (v1.0, 4 日)

| タスク | 内容 |
|---|---|
| 2.1 | request/response schema | 新規 `backend/app/schemas/v2/auto_allocate.py` |
| 2.2 | `POST /api/v1/schedule/auto-allocate` | `backend/app/api/v1/schedule.py` 拡張 |
| 2.3 | `POST /api/v1/schedule/proposal/{batch_id}/apply` (FOR UPDATE 排他制御込み) | 同上 |
| 2.4 | `POST /api/v1/schedule/proposal/{batch_id}/discard` | 同上 |
| 2.5 | OpenAPI contract 更新 | `docs/plans/v2-api-contracts.md` |
| 2.6 | フロント API hooks | 新規 `frontend/lib/queries/auto_schedule.ts` |
| 2.7 | 算出トリガーモーダル (基本) | 新規 `frontend/components/schedule/v2/AutoScheduleDialog.tsx` |
| 2.8 | 結果表示モーダル (Before/After 並列 + 基本 KPI バー) | 新規 `AutoScheduleResultDialog.tsx` |
| 2.9 | 自動算出ボタン配置 | `CourseDayTablePanel.tsx` 拡張 |
| 2.10 | proposed course の visit を schedule grid 上で識別表示 | `CourseDayTable.tsx` 拡張 |
| 2.11 | E2E テスト (Playwright) | `e2e/auto-schedule-mode1.spec.ts` |

**v1.0 リリースゲート**: staging で実データ (患者 69 名 / 固定枠 152 件) で動作確認、imaizumi 承認

---

### Phase 3a: Mode 2 (v1.1, 2 日)

| タスク | 内容 |
|---|---|
| 3a.1 | Mode 2 の全患者展開ロジック | auto_allocator 拡張 |
| 3a.2 | 既存固定枠を参考バッファに退避 | 同上 |
| 3a.3 | UI モード選択 Radio | `AutoScheduleDialog.tsx` 拡張 |

### Phase 3b: AI 自然文要約 (v1.1, 2-3 日)

| タスク | 内容 |
|---|---|
| 3b.1 | decision logger サービス | 新規 `backend/app/services/scheduling/decision_logger.py` |
| 3b.2 | summary generator (テンプレベース) | 新規 `backend/app/services/scheduling/summary_generator.py` |
| 3b.3 | テンプレ文ライブラリ | 同上 (将来 jinja2 へ昇格可) |
| 3b.4 | UI 表示セクション | `AutoScheduleResultDialog.tsx` 拡張 |

### Phase 3c: Visit Justification ポップアップ (v1.1, 2 日)

| タスク | 内容 |
|---|---|
| 3c.1 | Justification データ構造 | decision_log JSON schema 確定 |
| 3c.2 | Justification ポップアップ component | 新規 `frontend/components/schedule/v2/VisitJustificationPopover.tsx` |
| 3c.3 | visit クリックハンドラ | `CourseDayTable.tsx` 拡張 |

**v1.1 リリースゲート**: AI 要約文言レビュー、Justification 各パターン確認、imaizumi 承認

---

### Phase 4a: TSP (v1.2, 2 日)

| タスク | 内容 |
|---|---|
| 4a.1 | TSP optimizer サービス | 新規 `backend/app/services/scheduling/tsp_optimizer.py` |
| 4a.2 | 最近接法 + 2-opt 実装 | 同上 |
| 4a.3 | **TSP × H1 cross-day dependency 対策**: time fixing を Phase 2 で先に行い、TSP は順序のみ最適化 | auto_allocator 内で時刻決定 → TSP は並び替えのみ |
| 4a.4 | 同住所連続性 (H3) を TSP 制約として組込み | tsp_optimizer 内 |

### Phase 4b: 新人同行 Pattern A (v1.2, 2 日)

| タスク | 内容 |
|---|---|
| 4b.1 | 既存 `_resolve_companion_staff_id` と Pattern A の関係整理 | 設計レビュー、imaizumi 確認 |
| 4b.2 | 新人を「ベテランの visit に動的に同行」させる関数追加 | Layer 3 拡張 (+80 行) |
| 4b.3 | 同行先決定ルール (1 週間固定 / 曜日別変動) の選択 UI | (運用判断、UI 簡易) |

### Phase 5: 検証 + 運用準備 (v1.2, 2-3 日)

| タスク | 内容 |
|---|---|
| 5.1 | staging で全 Mode + 全 Phase の回帰テスト |
| 5.2 | KPI 妥当性検証 (距離削減率、ローテ達成率、H 違反 0 件) |
| 5.3 | runbook 作成 (`docs/deployment/auto-schedule-runbook.md`) |
| 5.4 | 評価関数重みのチューニング (実データベース) |
| 5.5 | 仕様書 v0.4 → Final 化 |

**v1.2 リリースゲート**: 性能目標 < 30 秒、ハード制約違反 0、staging 安定動作、imaizumi 承認

---

## 5. 工数とロードマップ

| Wave | フェーズ | 工数 | 累計 |
|---|---|---|---|
| **v1.0** | Phase 0 (DB) | 1 d | 1 d |
| | Phase 1a (skeleton + Mode 1) | 2 d | 3 d |
| | Phase 1b (H1-H8) | 2-3 d | 6 d |
| | Phase 1c (acceptance + KPI) | 1-2 d | 8 d |
| | Phase 2 (API + UI 基本) | 4 d | 12 d |
| | **v1.0 リリース** | | **~2 週間** |
| **v1.1** | Phase 3a (Mode 2) | 2 d | 14 d |
| | Phase 3b (AI 要約) | 2-3 d | 17 d |
| | Phase 3c (Justification) | 2 d | 19 d |
| | **v1.1 リリース** | | **~3 週間** |
| **v1.2** | Phase 4a (TSP) | 2 d | 21 d |
| | Phase 4b (新人同行) | 2 d | 23 d |
| | Phase 5 (検証) | 2-3 d | 26 d |
| | **v1.2 リリース** | | **~4 週間** |

**総工数**: 約 **4 週間** (1 人実装の場合、段階リリース)

---

## 6. リスクと対策 (拡張版)

| # | リスク | 影響度 | 対策 |
|---|---|---|---|
| R1 | Layer 1 の delete-and-recreate と Mode 1 の衝突 | 🔴 高 | auto_allocator は Layer 1 内部関数を選択的に呼ぶ (expand_week は呼ばない) |
| R2 | H1 (週次統一) の slot generation 段階での実装難度 | 🔴 高 | Phase 1b で patient ごとに「曜日横断 start_time」制約を unit test 付きで実装 |
| R3 | H2/H3 (同住所) アルゴリズムの計算量 | 🟡 中 | 拠点内で住所同一クラスタを前処理、cluster 単位で同時 timeslot 配置 |
| R4 | 既存 Layer 3 companion 機能との重複 | 🟡 中 | Phase 4b 開始前に design review、Pattern A を companion の上位レイヤーとして実装 |
| R5 | TSP × H1 の cross-day dependency | 🟡 中 | 時刻は Phase 2 で確定、TSP は並び替えのみ |
| R6 | 採用時の race condition | 🟡 中 | FOR UPDATE ロック + proposal_batch_id 整合性チェック |
| R7 | proposed course が放置される (DB 肥大化) | 🟢 低 | 24h 自動破棄 cron 設定 |
| R8 | 算出時間が 30 秒超過 | 🟢 低 | progress polling (1 秒間隔)、必要なら background task 化 |
| R9 | UI の Before/After 並列表示パフォーマンス | 🟢 低 | virtualization で対応 (実装後判断) |
| R10 | パターン B (2 名体制患者) 保留が業務に影響 | 🟢 低 | v0.4 で明示済み、v2.0 で対応 |
| R11 | AI 要約テンプレ文言が業務に合わない | 🟢 低 | v1.1 完了時 imaizumi レビュー枠を設ける |

---

## 7. オープン問題 (実装着手前の最終判断)

| # | 論点 | 判断者 | 状態 |
|---|---|---|---|
| O1 | Visit/Course status 方針 | imaizumi | ✅ **確定: Course.status を使う** (2026-05-15) |
| O2 | MVP リリース戦略 | imaizumi | ✅ **確定: 段階リリース 3 ステップ** (2026-05-15) |
| O3 | 進捗表示の通信方式 (SSE / polling) | Claude 推奨 → imaizumi | 推奨: polling (1s 間隔)、Phase 2 開始前 |
| O4 | 評価関数重みの初期値 | Claude 試算 | 初期値提案: w_distance=1.0 / w_load=2.0 / w_rotation=3.0 / w_soft=1.0 |
| O5 | アルゴリズム (K-Means / VRP) | 既存採用 | K-Means 採用 (既存 Layer 2 流用) |
| O6 | 進捗 polling の頻度 | Claude 推奨 | 1 秒間隔、最大 60 回 |
| O7 | proposed の生存期間 | Claude 推奨 | 24h で自動破棄 |
| O8 | AI 要約テンプレ文言レビュー | imaizumi | v1.1 完了時 |
| O9 | proposal_batch_id の生成方式 | Claude 推奨 | UUID4 |
| O10 | 同行先決定ルール (1 週間固定 / 曜日別変動) | imaizumi | v1.2 開始前 |

---

## 8. 検証計画

### 8.1 単体テスト (各 Phase 終了時)

- Phase 0: migration up/down
- Phase 1a: Mode 1 のプール抽出、proposal_batch_id 生成
- Phase 1b: **各ハード制約 H1-H8 の境界値テスト** (H2: 3 人同住所はエラー、H1: 全曜日同時刻になっていなければ fail)
- Phase 1c: acceptance_calendar × 時刻除外、KPI 計算正確性
- Phase 2: API レスポンス schema、エラー応答、Atomic transaction
- Phase 3a-c: Mode 2、AI テンプレ網羅、Justification 全パターン
- Phase 4a-b: TSP 最適解検証、新人同行 ペア成立
- Phase 5: 性能・スループット

### 8.2 結合テスト

- 患者 69 名 / 固定枠 152 件 (実 staging データ) で auto-allocate を実行
- v1.0 期待: 既存 152 件は不変、プール患者 (active && fixed_visits 無し) を 0 件追加 (現状あるとは限らない)
- v1.1 期待: Mode 2 で全 69 名再構成、距離が初期状態より改善
- v1.2 期待: TSP で訪問順序最適化、新人同行が ペアになる

### 8.3 E2E (Playwright)

- `e2e/auto-schedule-mode1.spec.ts` (v1.0)
- `e2e/auto-schedule-mode2.spec.ts` (v1.1)
- `e2e/auto-schedule-justification.spec.ts` (v1.1)
- `e2e/auto-schedule-tsp.spec.ts` (v1.2)

### 8.4 性能目標

- 算出時間: < 30 秒 (患者 70 名規模)
- API レスポンスサイズ: < 500 KB
- フロント描画: < 2 秒で結果表示完了

### 8.5 監視 / observability (v1.0 から)

- 各 Phase の実行時間ログ
- ハード制約違反検知のアラート
- proposed course の累積数モニタリング

---

## 9. v0.1 → v0.2 主な変更箇所 (critic 反映)

| 変更 | 場所 |
|---|---|
| Visit status → Course.status ベースに修正 | §1.2, §1.4, §2.1 G3 |
| H1/H2/H3 の実装タスクを Phase 1b として明示 | §4 Phase 1b |
| acceptance_calendar を slot generation に移動 | §4 Phase 1c (Layer 2 拡張ではない) |
| schedule_fix_service.py のパス修正 | §1.1 |
| 既存 Layer 3 companion 機能との関係明記 | §1.3, §4 Phase 4b.1 |
| proposal_batch_id 設計を Phase 0 に追加 | §4 Phase 0 |
| race condition 対策 (FOR UPDATE) を Phase 2 に追加 | §4 Phase 2.3 |
| 評価関数の重み初期値提示 | §7 O4 |
| Mode 1 と Layer 1 衝突の対策明示 | §6 R1 |
| TSP × H1 cross-day dependency 対策 | §4 Phase 4a.3, §6 R5 |
| MVP 段階リリース 3 ステップ採用 | §3 (新規章) |
| Phase 1 を 1a/1b/1c に細分化 | §4 |
| 工数: 4-5 週間 → 4 週間 (段階リリース) | §5 |
| 監視 / observability 章追加 | §8.5 |

---

## 10. 変更履歴

| 版 | 日付 | 編集 | 内容 |
|---|---|---|---|
| v0.1 | 2026-05-15 | imaizumi + Claude | 3 エージェント調査結果を統合、実装計画の初版起稿 |
| v0.2 | 2026-05-15 | imaizumi + Claude | critic レビューを反映、Course.status ベースに修正、H1/H2/H3 実装タスク明示、MVP 段階リリース戦略、Phase 細分化 |

---

> 本計画書 v0.2 のレビュー後、Phase 0 着手は imaizumi の Go サインを待つ。
> 進捗は本計画書を都度更新し、v1.0 / v1.1 / v1.2 各リリース時にステータス更新する。
