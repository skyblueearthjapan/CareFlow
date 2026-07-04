# プール一括投入（再構築）＋新規提案の段階的縮退 設計書 v1

作成 2026-07-04 / 前提文書: `docs/plans/pool-unification-design.md`（C案3段階・完了）、
`docs/plans/change-scope-unification-HANDOFF.md`（反映先2択・manual_week）、
`docs/plans/scope-optimization-design.md`（SimState・プレフィックス適用・state_token の先行例）

## 0. 背景と方針

PO 方針（2026-07-04 対話で確定）: **尖らせる機能は尖らせ、不要な機能は削り、アプリを濁りなく研ぎ澄ます**。

- 患者の入口を一本化する: **患者マスタ登録 → 希望訪問スケジュール（weekly_pattern）登録 → プール流入** が唯一のデータフロー。weekly_pattern が「患者から聞き取った受け入れ範囲」の正典（権威3層の第2層）であることを崩さない。
- 初期立ち上げシナリオ（患者50〜100名を一括登録し、プールに大量の患者×週回数分の枠需要がある状態）に応える **「プール一括投入」を新規設計で再構築**する。旧一括（auto_allocator_v2 距離グリーディ）の復活ではない。
- **新規提案（ProposeNewModal）は段階的に廃止**する。固有機能のうち残す価値のあるものだけを移植してから削除する。

### 旧一括投入と何が違うか（再発防止の要点）

| 観点 | 旧 /v2/diff-add（廃止済み） | 本設計 |
|---|---|---|
| 物差し | 距離グリーディ（物理距離のみ） | 個別提案と同一（proposal_solver 実行可能性 + compute_exact_marginal の delta 分/週） |
| 調停 | クラスタリング内で暗黙 | **投入順序として明示**（誰から入れたか・なぜかを表示） |
| 説明可能性 | 段階適用で崩れ説明不能 | 1患者ずつ決定論的に積む。患者ごとに配置理由を個別提案と同じ言葉で説明 |
| 適用 | 全か無か | プレビュー（read-only simulate）→ 確認 → 1TX 一括適用 |

## 1. PO 決定事項

| # | 決定 | 内容 |
|---|---|---|
| D-1 | 投入順序 = ハイブリッド | 「投入先候補が1つしかない患者」を先に確保 → 残りは効果（delta）昇順。説明文言: 「選択肢のない方を先に確保し、残りを効率順に投入しました」 |
| D-2 | 反映先 = **A固定**（pattern_and_week） | 一括では1件ずつ聞けないため、全件「固定訪問週間に登録（今週にも即反映）」に固定。**その代わり「見せる」を徹底**し「聞いてなかった」と言わせない（§5.3） |
| D-3 | 新規提案は移植後に削除 | 複数曜日カバレッジ→一括エンジンへ、効率代替→プール個別へ移植してから ProposeNewModal を削除 |
| D-4 | スケジュール画面に新規患者登録＋ボタン | 「新規の方」タブの代替。患者マスタと同等の登録がスケジュール画面から可能に。**削除と同じ Wave で設置**（導線の空白期間を作らない） |

## 2. 用語・前提

- **プール患者**（現行定義のまま・変更なし）: `status='active'` かつ `weekly_pattern.frequency_per_week >= 1` かつ 当週実訪問数 < 希望回数（shortage > 0）。FE 算出（`CourseDayTablePanel.tsx:978-993` / `lib/scheduling/preferred-visits.ts`）。
- **不足曜日**: 希望曜日（preferred_weekdays）のうち当週未配置の曜日。1患者の投入は不足曜日ぶんの複数枠（= 新規提案 StageC カバレッジの後継）。
- **物差しの正典**: 実行可能性 = `proposal_solver.find_available_slots_for_candidate`、コスト = `improvement_engine.compute_exact_marginal`。本機能は両方を import 再利用する（コピー禁止）。
- simulate は read-only。apply と完全分離（既存原則）。

## 3. エンジン設計 — 逐次シミュレーション

新規ファイル `backend/app/services/scheduling/pool_bulk_inserter.py`（想定）。

### 3.1 全体の流れ

```
1. load_week_course_buckets で現状バケットを1回ロード（propose_slots_service.py:217）
2. scope_optimizer の _copy_bucket パターンで可変コピー（メモリ内シミュレーション状態）
3. 各患者に compute_all_proposed_slots を1回実行して「候補数・最良delta」を採取（= pool-overview と同一計算）
4. D-1 の順序で患者列を確定:
     第1群: 候補が1曜日ぶんしかない患者（詰みやすい順 = 候補数昇順・同数は delta 昇順）
     第2群: 残りを最良 delta 昇順
     タイブレークは patient_id 辞書順（決定性）
5. 患者を順に処理（§3.2）。確定枠はメモリ内バケットに仮追加 → 次の患者はそれを既存訪問として見る
6. 全員分の placements / unplaced / before-after 週ビュー / KPI を返す
```

### 3.2 1患者の処理（複数曜日カバレッジ）

- `compute_all_proposed_slots` を現在のメモリ内バケットに対して実行（希望曜日・weekly_pattern の条件は患者マスタの値のみ。自由上書きは存在しない）。
- 候補は曜日ごとに独立したバケットに属する（曜日占有により同一患者の複数枠は必ず別曜日）ため、**1回のソルバ呼び出しで不足曜日ごとの最良候補を選べる**: 不足曜日それぞれについて delta 最小の候補を1つ採用。
- 部分投入を許容: 不足3曜日中2曜日しか入らなければ2枠投入し、残り1曜日は unplaced_days として理由コード（excluded_summary と同じ語彙: capacity_full / travel_shortage / lunch_window / no_gap / course_closed）つきで報告。
- delta 未評価候補への配慮: `DELTA_EVAL_LIMIT=20` は全候補共通の上位20件のため、曜日によっては最良候補に delta が付かないことがある。一括では**不足曜日ごとの先頭候補に限り追加で厳密 delta を計算**する（compute_exact_marginal 1回/曜日の追加コストのみ）。
- 座標未登録（lat/lng null）患者は投入不能（reason: no_coordinates）として unplaced に回す（現行 pool-overview と同じ扱い）。

### 3.3 仮確定（メモリ内バケット）

- `_CourseBucket.visits` は可変 list（`propose_slots_service.py:107-123`）。scope_optimizer の `_insert_visit`（`scope_optimizer.py:733-735`: append + start 昇順 sort）と同パターンで仮 visit を追加する。
- 容量制約（6人/480分）はバケット内 visits 数で自然に効く: 仮追加で埋まったバケットは後続患者のソルバ走査で capacity_full として弾かれる。**これが調停の実体**。
- DB には一切書かない（simulate は read-only）。

### 3.4 決定性

乱数なし。順序（§3.1-4）・候補ソート（delta → score → 開始時刻 → course_code）・タイブレークすべて決定的。同じ入力（同じ週・同じプール・同じ PFV 状態）なら必ず同じ結果。scope_optimizer と同じ設計思想。

### 3.5 KPI（プレビュー用サマリ）

- 投入成功: N名 / M枠、部分投入: n名、投入不能: k名（理由別内訳）
- 週全体の移動時間・距離の before → after（schedule_health の `_compute_course_metrics` を模擬バケットに適用 — scope_optimizer `_metrics_of` と同パターン）

## 4. API 契約

### POST /v2/pool-bulk-simulate（read-only）

```jsonc
// Request
{
  "iso_year": 2026, "iso_week": 27, "office_id": "…",
  "patient_ids": ["…"],            // FE がプール患者を送る（pool-overview と同方式）
  "ordering": "hybrid"              // v1 は hybrid のみ。将来拡張用に enum
}
// Response
{
  "placements": [ { "seq": 1, "patient_id": "…", "patient_name": "…",
                    "weekday": 1, "course_code": "B", "start_time": "10:15",
                    "service_minutes": 35, "delta_minutes": 4, "warnings": ["staff_absent"] } ],
  "partial": [ { "patient_id": "…", "placed_days": 2, "missing_days": 1,
                 "unplaced_reasons": { "3": "capacity_full" } } ],
  "unplaced": [ { "patient_id": "…", "reason": "no_gap" } ],
  "week_before_after": { /* V2WeekdayBeforeAfter[] — ProposalWeekCalendar 互換 */ },
  "kpi": { "placed_patients": 12, "placed_slots": 27, "travel_minutes_before": 512,
           "travel_minutes_after": 574, "travel_km_before": 96.2, "travel_km_after": 108.4 },
  "state_token": "sha256…"
}
```

- 上限: `POOL_BULK_MAX_PATIENTS = 50`（pool-overview の `POOL_OVERVIEW_MAX_PATIENTS` と同値）。超過は 422。
- 同期 API（N=50 で推定 2.5〜5秒。FullOptimize 同期実績と同等）。FE は spinner。
- state_token: scope_optimizer 方式（`scope_optimizer.py:285-297`）を流用し、**対象拠点の全 normal PFV 行 + 当週 visits の指紋**とする（一括投入は visits にも依存するため PFV のみでは不足）。

### POST /v2/pool-bulk-apply

```jsonc
// Request
{ "iso_year": …, "iso_week": …, "office_id": "…",
  "placements": [ /* simulate の placements をそのまま。プレフィックス選択は v1 なし＝全件 */ ],
  "state_token": "…" }
// Response
{ "applied_patients": 12, "applied_slots": 27, "warnings": [ … ] }
```

- state_token 再計算 → 不一致は **409**（simulate 後に誰かがスケジュールを触ったら必ずやり直し）。
- **1トランザクション**で全患者分を処理: 患者ごとに PFV upsert（既存 apply-individual の内部ロジックを再利用）→ `pfv_validator`（V2 pinned / V3 衝突 / V4 昼休み / V5 容量）→ `reset_visits_to_fixed(patient_id=…)` で今週再生成。V2 違反は 422 で全体 rollback、V3-V5 は warnings。
- change_scope は **pattern_and_week 固定**（D-2）。リクエストに change_scope フィールドを持たせない（選ばせない＝仕様として固定）。
- 監査: 適用サマリを `schedule_op_log` に記録する（op_group_id=1適用1グループ、**undoable=false**。Ctrl+Z 対象外は undo v2 バックログの既定方針どおり）。

## 5. UI 設計

### 5.1 起動ボタン

- `PoolOverviewPane.tsx` の headerAction（:227-244）を `flex gap-1` 化し、「効果を表示」の隣に小さく **「一括投入」**（`h-6 px-2 text-[11px]`）を追加。
- プール患者 0 名時は disabled。51名以上は先頭50名対象である旨を tooltip/toast で明示（silent cap 禁止）。

### 5.2 BulkPoolInsertDialog（新設）

- 骨格: `FullOptimizeDialog` の `DialogContent max-w-5xl max-h-[92vh]` + Tabs を流用。ステートは `idle → simulating → previewing → applying → done` の5段。
- プレビュー: 「全体」タブ = `ProposalWeekCalendar`（before/after 縦積み）、曜日タブ = `BeforeAfterWeekPanel`。**投入された枠はハイライト表示**（新規挿入チップの強調。DiffAdd の固定枠/希望枠色分けを参考）。
- 左カラムに投入リスト（seq 順・患者名・曜日コース時刻・delta・警告バッジ）＋ 部分投入/投入不能の患者と理由。順序の説明文言（D-1）を先頭に固定表示。

### 5.3 「見せる」設計（D-2 の対価 — 最重要）

反映先を聞かない代わりに、**知らなかったと言えない状態**を4点で作る:

1. **常時バナー（プレビュー画面上部・閉じられない・アンバー）**:
   「適用すると **N名 M枠が固定訪問週間（毎週の型）に登録**され、**今週のスケジュールにも即反映**されます。この操作は元に戻す（Ctrl+Z）の対象外です。」
2. **確認チェックボックス（必須）**: 「固定訪問週間（毎週の型）が変わることを確認しました」— チェックするまで適用ボタンは disabled。
3. **適用ボタンの文言自体に書く**: 「固定訪問週間に登録する（N名・M枠）」— 「適用」「OK」という無色な動詞を使わない。
4. **適用後トースト＋履歴**: 「N名 M枠を固定訪問週間に登録しました」トースト＋ op_log 記録（§4）。週次ガイドにも一括投入の節を追記（W-2 に含める）。

## 6. Wave 分割（実装→独立レビュー→デプロイを1単位）

| Wave | 内容 | 受け入れ基準 |
|---|---|---|
| **W-1** | BE: pool_bulk_inserter + /v2/pool-bulk-simulate + テスト（決定性・調停[先行患者が枠を埋めたら後続が避ける]・部分投入・50上限・state_token） | pool-overview と1人目の結果が厳密一致 / 同一入力で同一出力 |
| **W-2** | BE: /v2/pool-bulk-apply（1TX・409・pfv_validator・op_log）+ FE: ボタン＋BulkPoolInsertDialog＋「見せる」4点 + 週次ガイド追記 | 409 経路の動作 / チェックボックス未チェックで適用不能 / 適用後に週ビュー・モニターへ反映 |
| **W-3** | プール個別（PoolCandidateList）へ効率代替（include_efficiency_alternatives）移植 + 「希望未登録の active 患者 N名」の可視化安全網 | 新規提案とプール個別で効率代替の結果一致 |
| **W-4** | ProposeNewModal 削除（本体1,819行＋テスト＋ボタン＋state）**＋ D-4 の新規患者登録＋ボタンを同時設置**（患者マスタの登録フォームを再利用。登録完了→希望訪問スケジュール登録へ誘導→プールに現れる） | スケジュール画面から患者登録→プール流入→一括/個別投入が途切れず成立 |

- W-1/W-2 が本番で安定してから W-4 に進む（機能の穴を作らない）。
- BE の /v2/diff-add と DiffAddDialog.tsx の残置分は、W-4 完了後の掃除タスクとして別途判断（本設計のスコープ外）。

## 7. 非ゴール・既知の制約

- 一括投入の Ctrl+Z（undo）は対象外 — undo v2（PFV系スナップショット undo）バックログに従う。バナーで明示（§5.3）。
- 順序の最適性保証はしない（逐次貪欲）。「説明できる順序」を優先する — 範囲最適化と同じ設計判断。
- 51名以上のプールは複数回に分けて実行する運用（将来: 非同期ジョブ化のオプションを残す）。
- 複数拠点横断の一括投入はしない（office_id 単一。範囲最適化と同じ）。
- スタッフ適格性は現行 L1.5（警告＋降格）のまま。placements の warnings に表示するのみで、割付（Layer 3）は別工程。

## 8. 将来バックログ（本設計から派生）

- 一括投入の非同期ジョブ化（N>100）
- ordering の選択肢公開（delta_asc / candidate_count_asc）— 現場から要望が出たら
- 投入結果から「自動スタッフ割当」への導線（一括投入→割当→レビューの立ち上げ一気通貫）
