# 引き継ぎ書：範囲最適化〜スケジュール診断の処方箋化（フォーカス最適化まで）

作成 2026-07-03 / **本番HEAD = `5ad3648`** / DB = migration **0050**（本セッションで DB 変更なし）/
前セッションの引き継ぎは `docs/plans/schedule-advisor-HANDOFF.md`（アドバイザー Phase 0〜3・P4/P5）と
`docs/HANDOFF.md`（プロジェクト全体）。**次のエージェントはまずこのファイル → schedule-advisor-HANDOFF.md の順に読む。**

このドキュメントは 2026-07-03 の大規模セッション（範囲最適化 W1-W3 → UI統一 →
スケジュール診断の処方箋化 H1-H3 → フォーカス/探索範囲の分離 §10 → 名称変更）の全体像と現在地。

---

## 1. TL;DR — いま何がどうなっているか

- **「診断 → 原因 → 対策 → 実行後の見通し → 適用」の一気通貫が本番稼働**。
  スケジュール診断（旧称: 健康診断。5ad3648 で表示名統一）→ 要対応コースバナー →
  コース名クリックで原因ドリルダウン（重い移動TOP3・患者別配置コストTOP3）→
  〔対策を計算〕→ 範囲最適化が自動計算（理由文つき手順・コース別見通し・タイムライン）→
  スライダーで先頭N手を適用 → 固定枠戻の案内。
- **範囲最適化は「①フォーカス（対策対象）②探索範囲（移動先）」の2段階選択**（§10）。
  健康診断導線は自動的に「フォーカス=クリック行・探索=拠点全体」。手の採用条件は二重
  （探索範囲全体の合計 delta ≥ 10分/週 かつ フォーカスの負担が実減）。
- **効果計算は厳密化済み**（W3）: 限界コスト＝コース合計(travel+buffer)の差。本番実データで
  発見した「同住所・同時刻ペアの見かけ倒し提案（−36分/−9.4km）」を修正。患者詳細の
  改善提案も同じ正典なので同時に修正済み。
- 全 16 コミット本番稼働・healthz 正常。フロント変更後は現場で **Ctrl+Shift+R** 必須。

## 2. 本番状態とデプロイ

- VPS `root@72.60.211.213` / `/opt/carelink` / https://carelink.kaipoke-api.net / develop
- migration 追加なし（DB は 0050 のまま）。デプロイ手順は schedule-advisor-HANDOFF.md §2 のとおり
  （pg_dump → pull → build → recreate → healthz。`set -eo pipefail`）。
- 本セッションで 5 回デプロイ（W1 / W2 / W3+hotfix+チップ表示 / UI統一+H1-H3+フロー修正 / §10+名称変更）。

## 3. このセッションの実装一覧（時系列）

| 塊 | コミット | 内容 |
|---|---|---|
| W1 範囲最適化 simulate | `1b55910` `b040011` `6afcfa4` | `scope_optimizer.py`（貪欲反復・正典再利用・決定性・state_token）＋ `POST /v2/scope-optimization/simulate` ＋ ScopeOptimizeDialog（チップ選択→手順列＋前後タイル） |
| W2 apply＋診断導線 | `42d575f` `3cb7868` | `POST .../apply`（**プレフィックス適用のみ**・state_token 409・1TX・pfv_validator・step毎flush・監査ログ）＋適用スライダー＋健康診断コース行「最適化」ボタン。**FEの409判定は ApiError.status で行う**（message に detail は入らない） |
| W3-1 厳密計算化 | `a133a2e` | **実データバグ**（同住所・同時刻ペアの片割れ移動に−36分/−9.4kmの見かけ倒し）→ `compute_exact_marginal`＝コース合計の差に全経路置換（improvement_engine 正典ごと。`_neighbors_at` 近似は削除）。回帰テストで本番シナリオ再現 |
| W3-2 タイムライン＋拠点チップ | `c9d4634` | step に適用前スナップショット → 同一コース=1枚/別コース=2枚並列のタイムライン。全拠点モードでもダイアログ内拠点チップ |
| hotfix | `ecbbe37` | 拠点チップ無反応（open リセット effect の deps に runSimulate → effectiveOfficeId 化でループ）。**deps は [open] のみに固定が正** |
| チップ型表示 | `61bc722` | 移動元(muted)→太矢印→移動先(ブランド色太枠+塗りバッジ)の SlotChip/MoveVisual。swap は患者名つき2行 |
| UI統一 | `28c721b` `c6e22fa` | スナップショット正典を improvement_engine/improvement_suggestion に一元化（scope側は互換alias）。共有 `CourseMoveTimeline.tsx` を新設し**患者詳細の改善提案にも同じタイムライン** |
| 処方箋 H1-H3 | `b8cf00c` `d0cfb40` | H1: `GET /v2/schedule-health/course-detail`（遷移内訳＋患者別配置コスト=厳密限界コスト）＋コース行クリック展開。H2: 提案の理由文 `reason`（build_move_reason）＋ simulate `courses[]`（コース別 before/after）＋固定枠戻案内。H3: 要対応コースバナー。設計書 `health-prescription-design.md` |
| 処方箋フロー修正 | `a8774c2` | 対策計算ボタンが単一拠点フィルタ時のみ表示 → 行の拠点(courseOfficeId)を引き継ぎ全拠点表示でも繋がる。**runSimulate の officeOverride 引数で setState 未反映の stale を回避** |
| §10 フォーカス最適化 | `07923d6` | ①フォーカス②探索範囲の分離（PO の構造課題指摘への回答）。search_scope（省略=従来・包含422）・focus_before/after・swap のみ `_swap_focus_delta` でフォーカス実減を検証（move は数学的含意: 挿入限界コスト≥0）。FE 2段階チップ（拠点全体/同じ/カスタム=和集合） |
| 名称変更 | `5ad3648` | 表示名「健康診断」→「スケジュール診断」（内部識別子・APIパスは不変） |

## 4. 概念モデル（このセッションで確立）

| 概念 | 実体 | 意味 |
|---|---|---|
| フォーカス | simulate の `scope` | 対策を練りたい範囲＝**動かす対象**の枠が属する範囲 |
| 探索範囲 | `search_scope`（省略=フォーカス） | 移動先・入れ替え相手を探す範囲。**⊇フォーカスを強制** |
| 厳密限界コスト | `compute_exact_marginal` | コース合計(travel+buffer)の差。診断・提案・見通しが数学的に一致する物差し |
| スナップショット | `CourseSnapshot(Data)` | 提案が触るコースの訪問列（タイムライン表示・理由文の隣接名導出） |
| 理由文 | `reason` / `build_move_reason` | 「現在の◯◯様と△△様の間への配置で回り道…−N分/週」 |
| プレフィックス適用 | apply の steps=seq 1..N | 手順は前の手が空けた枠に依存 → 先頭からN手のみ許可 |
| state_token | 探索範囲患者の PFV 集合の sha256 | 楽観ロック。simulate/apply で**同一ロード規約**（`_load_scope_buckets_and_pfvs`） |

## 5. コード地図（今回追加・改修）

**BE（backend/app/）**
- `services/scheduling/scope_optimizer.py` — 範囲最適化エンジン（模擬状態・貪欲反復・focus/search・
  スナップショット・コース別 before/after・state_token）
- `services/scheduling/improvement_engine.py` — **正典に追加**: `course_travel_buffer_total` /
  `compute_exact_marginal`（W3厳密計算）/ `CourseSnapshot(Visit)Data`+`snapshot_course_bucket`+
  `_cached_snapshot` / `build_move_reason`。`ImprovementCandidateData` に snapshots+reason
- `services/scheduling/schedule_health.py` — `compute_course_detail`（H1 原因内訳）。`_HealthVisit.patient_name`
- `api/v1/schedule_v2.py` — simulate/apply/course-detail エンドポイント＋`_validate_search_contains_focus` 等
- `schemas/v2/scope_optimization.py` / `improvement_suggestion.py`（CourseSnapshot 正典）/ `schedule_health.py`

**FE（frontend/）**
- `components/schedule/v2/ScopeOptimizeDialog.tsx` — 2段階選択・適用スライダー・フォーカス主役表示
- `components/schedule/v2/CourseMoveTimeline.tsx` — **共有**タイムライン（範囲最適化と改善提案で同一）
- `components/schedule/v2/ImprovementSuggestionCard.tsx` — SlotChip/MoveVisual（チップ型）＋reason 行
- `components/schedule/v2/ScheduleHealthDialog.tsx` — 要対応バナー・行クリック展開（CourseCauseDetail）
- `lib/schemas/v2/scopeOptimization.ts`（steps は**最長有効プレフィックス**寛容化）/ `improvementSuggestion.ts` / `scheduleHealth.ts`
- `lib/queries/scopeOptimization.ts` / `scheduleHealth.ts`

## 6. 設計文書
- `docs/plans/scope-optimization-design.md` — 本体設計＋**§10 フォーカス/探索分離**（W1実装で確定した契約も反映済み）
- `docs/plans/health-prescription-design.md` — 診断→処方箋 H1-H3（D-1: 2つの物差しの橋渡し / D-2: 事前自動計算しない）

## 7. 残タスク・提案・気になる点

**残タスク（優先度順）**
1. **ドリルダウンの患者コスト行 → 患者詳細の改善案への導線**（§10 設計書の後続として明記済み。
   健康診断→PatientScheduleDetailDialog を開く配線が必要）
2. 「要確認の手も含めて計算」トグル（設計 D-2 後段。scope_optimizer の D-2 ゲートを条件化するだけ）
3. ④拠点週全体スコープの**性能実測**（現状は同期API。遅ければサイズガード→非同期化）
4. 閾値の現場調整（`SCOPE_STEP_THRESHOLD_MIN=10` / 重い遷移の33% / TOP3件数 / 警告1.5倍）
5. W4（任意・PO合意済み）: **B案参考値**（auto_allocator_v2 白紙再配置の理論値を数字のみ併記。
   フォーカス最適化で 0 手のとき「構造的問題」の判断材料になる）
6. LOW記録: コース別 travel_km はコース単位丸めで全体タイルと±0.1kmずれうる（advisory・実害なし）/
   M1-M9 コースの個別選択チップ（全コースでのみ対象）/ favicon.ico 未設置（無害404）/
   `course_travel_buffer_total` は座標None非対応（現状 ExistingVisit 経由で座標必須のため実害なし）

**気になる点（次エージェントへの注意）**
- `docs/HANDOFF.md` が**前セッションから untracked のまま**（コミットされたのは
  docs/plans/schedule-advisor-HANDOFF.md のみ）。POに確認済みだが回答なし。放置中。
- BE テストに順序依存らしきフレーク1件を1回観測（`test_dismissed_fingerprint_excluded` で
  `'float' object has no attribute 'replace'`＝expires_at が float で返った様子。単発・再現せず。
  既知の tz フレーク系とみられる。頻発するなら `_as_naive` に float ガードを）。
- `ruff format app/` を**広く当てると無関係ファイルが巻き添え整形**される（checkin系・staff_substitute等）。
  今セッションでは2回 revert した。**format は変更ファイルのみに当てること**。
- 既存fail（無関係・従来から）: BE `test_reset_to_fixed_*` 2件 / FE CourseDayTablePanel系・SessionProvider系。

**提案（未着手のアイデア）**
- 適用後に「固定枠戻」をワンクリックで実行できる導線（現在はトースト案内のみ）
- 原因の自動ラベリング（「遠方の孤立患者」「順序の往復」等のカテゴリ分け）— 現場FB後に
- office横断の改善提案フィード（schedule-advisor-HANDOFF §9 から継続）

## 8. 開発プロセス規約（前セッション＋今回の教訓）

- 体制: **実装→独立レビュー(code-reviewer)→修正→再検証→コミット→デプロイ**。自己approve禁止。
  今セッションのレビューは W1/W2/W3/UI統一/H1-H3/§10 の6回＋検証パス3回（REQUEST_CHANGES 1回→修正→APPROVE）
- BEテスト: `cd backend && python -m pytest <files> -q -p no:warnings`（**uv run 不可**）。
  FE: `pnpm tsc --noEmit` / `pnpm vitest run <files>` / `pnpm lint` / prettier
- **日本語ファイルの一括置換に PowerShell Get-Content/Set-Content 禁止**（Edit ツールか python -c）
- FE の既知の罠: Tailwind は var() 色に /alpha 修飾子を生成しない / fetcher の ApiError.message に
  detail は入らない（**status で判定**）/ ダイアログの open リセット effect は **deps [open] のみ**
  （state 未反映は officeOverride/searchOverride のような明示引数で回避）
- コミットは日本語 conventional・レビュー判定を明記・`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## 9. 次の候補（優先提案）

1. 現場での実使用フィードバック収集（診断→対策→適用の一巡。特に §10 で提案の質が上がったか）
2. §7 残タスク 1（患者コスト行→改善案導線）— 小粒で価値が高い
3. 閾値・文言の現場調整 → その後 W4（B案参考値）
