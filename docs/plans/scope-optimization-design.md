# 範囲最適化（スコープ最適化）詳細設計 — 選択範囲の一括改善シミュレーション

作成 2026-07-03（ディレクター承認済み方針に基づく）/ 親: schedule-advisor-design.md /
前提: アドバイザー Phase 0〜3・P4/P5 本番稼働（HEAD `efb1b7b` / DB 0050）/
関連: p2-improvement-mvp-design.md（限界コスト・却下記憶）・schedule_health（物差し）

## 0. 承認済み方針（2026-07-03 プロダクトオーナー合意）

- **A案（反復ローカルサーチ）を本体機能として実装**する。B案（auto_allocator_v2 の範囲制限
  全面再配置）は適用可能な別機能としては**作らない**。将来 Wave で「理論上の参考値」の数字併記
  としてのみ検討（§9 W4）。
- 設計判断3点（承認済み）:
  - D-1: **locked（可動域=完全固定）はピン留め同様、最適化の対象外**（P4 3層の意味論どおり）。
  - D-2: 既定は**患者確認不要の手のみ**で計算（希望内 or movability が確認不要で許す手）。
    「要確認の手も含める」はトグルで後続 Wave。
  - D-3: **却下記憶（suggestion_dismissals）を尊重**し、範囲最適化でも蒸し返さない。
- 用語注意（誤解防止）: `movability='locked'` と `weekly_pattern` の `time_type='固定'` は
  **無関係**（p2設計 §1.1「3軸は別物」）。時間帯設定「固定」は希望時刻の性質であり、locked を
  含意しない（「固定希望だが管理者判断で動かせる」は正当）。locked の発生源は
  ①ピンON自動付与 ②却下理由 time_immovable からの人間確認付き昇格 ③詳細設定での手動選択、の3つのみ。

## 1. 機能概要と位置づけ

管理者が範囲（①1コース / ②1曜日 / ③複数曜日 / ④拠点週全体）を選ぶと、その範囲内の
動かせる患者について move（時刻/曜日変更）と swap（2患者入れ替え）を**貪欲反復**で積み上げ、
「手順1: ○○様を火10:00へ（−15分/週）→ 手順2: …」という**説明可能な手順列**と
**最適化前後の健康診断メトリクス**を返す。管理者は「先頭からN手」を選んで一括適用する。

既存機能との関係: 健康診断（見える化）→ **範囲最適化（解決策）** → 適用、の導線を完成させる。
提案の1手の評価・実行可能性・効果の物差しはすべて既存正典を import 再利用する（コピー禁止）。

## 2. スコープ定義

```
scope = { office_id: UUID,            # 単一拠点（MVP。複数拠点は対象外）
          weekdays: list[int] | None,  # None=全曜日 (0=Mon..6=Sun)
          course_codes: list[str] | None }  # None=全コース
```

- 4区分はすべてこの1つの形で表現（①=`weekdays:[0],course_codes:["A"]` … ④=両方 None）。
- **移動元も移動先も scope 内に限る**（月曜Aコースを選んだのに火曜へ動かす提案は出さない）。
  swap は両患者の枠がともに scope 内であること。
- scope 内のピン留め枠・locked 枠・確認必要な枠は「動かせない障害物」として存在し続ける
  （feasibility 判定の existing には含める。動かさないだけ）。

## 3. エンジン設計（backend/app/services/scheduling/scope_optimizer.py 新設）

### 3.1 模擬状態（SimState）

- `load_week_course_buckets`（propose_slots_service）で当週バケットをロードし、scope 内の
  バケットを**ミュータブルなコピー**として保持。scope 外バケットは参照のみ（移動先候補にしない）。
- 併せて scope 内患者の PFV（mode='normal', slot_index=0）と Patient（weekly_pattern 判定用）、
  却下指紋、患者ごとの占有曜日集合（x_occupied_weekdays 相当）をロード。
- 手を1つ適用するたび、模擬バケットの visits を更新（旧位置から除去→新位置へ挿入）し、
  PFV 側の (patient, weekday) 占有マップも更新する。DB には一切書かない（read-only）。

### 3.2 動かせる集合（movable set）

PFV 単位で以下をすべて満たすもの:
- scope 内バケットに当週配置がある（なければ `no_current_visit` で会計）
- `is_pinned=False`（違反→ `pinned` 会計）かつ `movability != 'locked'`（→ `locked` 会計）
- D-2 既定モード: 手ごとに「確認不要」判定。within_preference（希望内）はすべて可。希望外は
  time_flexible=同曜日のみ / day_flexible=曜日も可 / unknown=**不可**（要確認になるため。
  → `confirmation_required_excluded` 会計）

### 3.3 手（step）の生成 — 既存正典の再利用

improvement_engine の部品をそのまま使う（必要なら private → 公開への薄い export を追加。挙動不変）:
- move 候補: 自分を除いた existing で `find_available_slots_for_candidate`＋`compute_lunch_window`
  （proposal_solver 正典 = 90分占有・前方/後方・営業枠・昼窓が自動で守られる）
- 1手の効果: `compute_marginal_cost`（delta = 現在枠の限界コスト − 候補枠の限界コスト）
- swap 候補: `_swap_candidates_for_pfv` と同じ双方向 feasibility（`_find_conflict` 相互整合）。
  X-Y / Y-X の重複ペアは (min(id),max(id)) で dedupe
- 希望内判定: `_slot_within_preference`（P4-A 実装をそのまま）
- 却下指紋: (patient_id, kind, target_weekday)。move/swap とも生成前に除外（D-3）

### 3.4 反復ループ（貪欲・最良優先）

```
steps = []
while len(steps) < MAX_STEPS:
    cands = 全 movable PFV の move+swap 候補を模擬状態に対して列挙
    best = max(cands, key=(delta_minutes, delta_km, -開始時刻))   # 同点は既存 sort 規約
    if best is None or best.delta_minutes < SCOPE_STEP_THRESHOLD_MIN: break
    模擬状態に best を適用; steps.append(best + 累積効果)
```

- `SCOPE_STEP_THRESHOLD_MIN = 10`（分/週。IMPROVEMENT_THRESHOLD_MIN と同値の別定数。
  現場調整前提 — backlog と同じ扱い）
- `MAX_STEPS = 30`（安全弁。到達時はレスポンスに truncated=true を立て「黙って切らない」）
- 決定性: 乱数なし・tie-break 固定 → 同一入力は常に同一手順列（テスト容易・再現可能）
- 各 step は improvement_engine の 1 手表現 (`ImprovementCandidateData`) をそのまま持ち、
  視点主の patient_id / patient_name と累積 delta を付加する（W1 実装で確定）

### 3.5 前後メトリクス

schedule_health の `_compute_course_metrics`（純関数）を模擬バケットに適用できる公開アダプタを
追加し、**最適化前・後の scope 内合計**（travel_minutes / travel_km / buffer / gap / visit_count）を
算出する。健康診断と同一物差しであることが「見える化→解決策」導線の根拠。

### 3.6 除外の会計（N-6「黙って消さない」）

`excluded_summary = { pinned, locked, dismissed, no_current_visit,
confirmation_required_excluded, truncated: bool }`。FE は 0 件時・手順列の末尾に内訳を表示。

## 4. API 契約（backend/app/api/v1/schedule_v2.py に追加）

### 4.1 POST /v2/scope-optimization/simulate（read-only / admin・manager）

Req: `{ iso_year, iso_week, scope{office_id, weekdays?, course_codes?} }`
Res（**W1 実装で確定**: 手順の中身は既存 `ImprovementSuggestion` schema を再利用。
kind='move' ではなく time_change/day_change/swap をそのまま使い、FE は
ImprovementSuggestionCard を表示専用で流用する）:
```
{ iso_year, iso_week, office_id,
  steps: [ { seq, patient_id, patient_name,
             suggestion: ImprovementSuggestion,   # kind/current/candidate/delta/changes/
                                                  # staff_warnings/within_preference/
                                                  # swap_counterpart を含む既存契約
             cumulative_delta_minutes, cumulative_delta_km } ],
  before: {travel_minutes, travel_km, buffer_minutes, gap_minutes, visit_count},
  after:  {同上},
  excluded_summary: {pinned, locked, dismissed, no_current_visit,
                     confirmation_required_excluded, truncated},
  state_token: str }   # §5 の楽観ロック
```
- `state_token` = scope 内 PFV 行の (patient_id, weekday, start_time, duration_min,
  course_template_id, is_pinned, movability) を正規化ソートした列の sha256。
- 0 手でも 200（before=after + excluded_summary）。エラー系: 無効 ISO 週=422 / office 不在=404。

### 4.2 POST /v2/scope-optimization/apply（admin・manager / 1TX）

Req: `{ iso_year, iso_week, scope, state_token, steps: simulate の先頭からN件そのまま }`
- **プレフィックス適用のみ**: steps は simulate 結果の先頭からの連続区間（seq=1..N）。
  途中の欠番は 422（依存関係が壊れるため）。
- サーバは state_token を再計算し不一致なら **409**（「スケジュールが変更されました。
  再計算してください」）。一致すれば steps を順に PFV へ書く:
  - move: 該当 PFV の weekday / start_time / course_template_id を更新
    （course_template_id は office_id＋course_code からサーバ側で解決。apply-swap と同方式）
  - swap: apply-swap 相当の 2 PFV 同時更新
- 書込前に各 step を `_find_conflict` で最終検証し、全体を pfv_validator（V2 pinned 同一性/
  V3 衝突/V4 昼休み/V5 容量）に通す。V2 違反=422、V3-V5 は warnings で返す（P0-2 と同じ扱い）。
- 1 トランザクション（全成功 or 全ロールバック）。Res: `{ applied_count, warnings[] }`。
- 監査: 既存 apply 系と同様に audit log へ（scope・step 数・delta 合計）。

## 5. FE 設計（frontend/components/schedule/v2/）

- **ScopeOptimizeDialog.tsx**（新設）:
  - 上段: 前後比較タイル（ScheduleHealthDialog の SummaryTile 流用。移動時間/隙間/距離、
    「−41分/週」形式の delta）
  - 中段: 手順カード列（ImprovementSuggestionCard の表現を流用: 変わるもの/変わらないもの・
    希望内バッジ・スタッフ警告）
  - 下段: **適用範囲スライダー**（先頭からN手。動かすと累積 delta がタイルに反映）＋
    「N手を適用」ボタン。409 時は「再計算」導線
- 入口:
  - W1: CourseDayTablePanel のツールバーに「範囲を選んで最適化」→ 拠点/曜日/コース選択
  - W2: **ScheduleHealthDialog の警告バー（移動が多いコース）に「このコースを最適化」ボタン**
    （見える化→解決策のワンクリック導線。本機能の主目的）
- zod スキーマ `lib/schemas/v2/scopeOptimization.ts`: BE 契約 1:1・未知 kind は寛容パース
  （FE 規約踏襲）。クエリ `lib/queries/scopeOptimization.ts`
- RBAC: admin/manager のみ（BE でも担保）。simulate 実行中はスピナー、失敗時もダイアログは生かす

## 6. テスト計画

- **エンジン単体**（SQLite/インメモリ・決定性があるので golden 的に書ける）:
  収束（改善が尽きたら止まる）/ 閾値未満の手を積まない / MAX_STEPS 打切りと truncated /
  pinned・locked・却下・unknown(既定モード) の除外と会計 / scope 外へ動かさない・scope 外から
  持ち込まない / 曜日跨ぎの占有衝突（x_occupied_weekdays）/ swap の相互整合と dedupe /
  同一入力→同一手順列 / 前後メトリクスが健康診断と一致
- **API**: simulate 契約（0手200・422/404）/ apply の state_token 409 / プレフィックス以外 422 /
  1TX ロールバック / pfv_validator warnings 貫通 / RBAC
- **FE**: ダイアログ描画（steps/空/エラー）/ スライダーと累積 delta 連動 / apply 配線・409 再計算 /
  zod 寛容パース

## 7. 性能・安全

- 全計算はメモリ上。曜日スコープ（〜80訪問）で1手あたり数十ms級 × 最大30手 → 同期APIで足りる想定。
  ④拠点週全体は実測し、遅ければ scope サイズガード（警告つき）→ 非同期化の順で対処。
- read-only（simulate）と write（apply）の分離・apply の 422/409/validator 安全網により、
  「提案は一切 DB を汚さない」既存原則を維持。

## 8. 既知の制約（初版で割り切るもの）

- 貪欲法のため厳密最適ではない（3名以上の玉突きは、途中に空き枠があれば move の連鎖で到達する
  ことはあるが保証はない）。改善余地が疑われる場合の拡張: コース内並べ替え（2-opt 相当）を
  「手」の種類として追加 / B参考値との乖離表示（§9 W4）
- 計算ベースは PFV（恒久パターン）。当週限定の最適化（特殊週）は対象外
- 複数拠点横断・slot_index=1（2名体制の相方）の自動移動は対象外（改善提案と同じ割切り。
  片肺化は validator warnings で可視化される）
- FE のコース選択チップは A-E + M のみ（オーバーフローコース M1-M9 は個別選択不可、
  「全コース」でのみ対象。レビュー LOW-2 — 現場フィードバックで要否判断）

## 9. Wave・コミット分割（実装+独立レビュー体制・自己approve禁止）

| Wave | コミット | 内容 |
|---|---|---|
| W1 | BE-1 | scope_optimizer エンジン＋単体テスト（improvement_engine/schedule_health の公開アダプタ含む） |
| W1 | BE-2 | POST simulate＋契約テスト |
| W1 | FE-1 | ScopeOptimizeDialog（simulate 表示のみ・適用ボタンなし）＋範囲選択入口＋zod/クエリ |
| W2 | BE-3 | POST apply（state_token/1TX/validator）＋テスト |
| W2 | FE-2 | 適用スライダー＋apply 配線＋健康診断ダイアログからの導線 |
| W3(実施済) | — | **限界コストの厳密計算化**（実データで発見した同住所・同時刻ペアの見かけ倒し提案を修正。`compute_exact_marginal`=コース合計 travel+buffer の差。improvement_engine 正典ごと修正し patient 詳細の改善提案にも波及）/ **手順カードのコースタイムライン**（step に適用前スナップショット source_course/destination_course を付与。同一コース=1枚で移動を視覚表示・別コース=2枚並列比較）/ **全拠点モードでもダイアログ内で拠点チップ選択可** |
| W3後続 | — | 「要確認の手も含める」トグル（D-2 後段）/ ④全体スコープの性能実測とガード / 閾値の現場調整 |
| W4(任意) | — | B案参考値の併記（auto_allocator_v2 範囲制限実行・数字のみ表示・適用不可） |

W1 デプロイ時点で「無駄の指摘に対する具体的な手順列」が見える（適用は手動）。W2 で一括適用まで完成。

## 10. フォーカス最適化 — 問題範囲と探索範囲の分離（PO承認 2026-07-03）

構造課題（PO指摘）: 範囲が「直したい問題」と「解を探す空間」を兼ねるため、
狭い範囲では道具（他コースへの移動/入れ替え）を封じ、全体では焦点が消える。

解決: 選択を 2 段階に一般化する。
- **① フォーカス（対策を練りたい範囲）** = 動かす対象の患者枠が属する範囲
- **② 探索範囲** = 移動先・入れ替え相手を探す範囲。**探索 ⊇ フォーカス を UI/API で強制**。
  選択肢: 拠点全体（既定・推奨）/ フォーカスと同じ（= 従来挙動）/ カスタム

手の採用条件（二重）:
1. 探索範囲全体の合計 delta ≥ 閾値（従来どおり厳密計算。しわ寄せで見かけ改善する手を禁止）
2. **フォーカスの負担が実際に減ること** — move はフォーカス発なので条件1に含意される
   （挿入の限界コスト ≥ 0 のため）。swap で相手コースがフォーカス外の場合のみ、
   フォーカス側バケットの before/after を追加検証して ≤0 なら除外。

API: simulate/apply に `search_scope`（省略=フォーカスと同じ=後方互換）。
レスポンスに `focus_before/focus_after`。state_token は**探索範囲**の患者集合から計算
（simulate/apply 同一規約）。courses[] は探索範囲全体（しわ寄せの可視化）。

UI: 結果はフォーカスの前後比較を主役に、探索範囲全体の合計を添える。
健康診断導線はフォーカス=クリック行・探索=拠点全体で自動計算（両入口が同一UIに収束）。

後続（本便に含めない）: ドリルダウンの患者コスト行→患者詳細の改善案への導線。
