# W-12 2名体制のペア探索・原子採用 設計書 v1

作成 2026-07-04 / PO 承認済み（W-12a 着手・W-12d 方向性）。
前提調査: 2名体制の全実体マップ＋アルゴリズム設計調査（本日・会話内レポート。要点は本書に転記）。
関連: `docs/plans/scheduling-logic-normalization.md` I-12（2名体制の非貫通）・N-7（片肺確定の排除）。

## 0. 背景と根本原因（調査確定）

- **土台は健全**: PFV slot_index 0/1・Visit.visit_group_id・required_staff_count のデータモデル、
  Layer 1（slot0/1 が揃えば同時刻・別コースの2visitを生成、片方欠けはスキップ＋通知）、
  Layer 3（1スタッフ=1日1コース制約で別スタッフを構造保証）は既に正しい。
- **断層は中間層**: 個別提案（slot間アンカー機構なし → 「16:00と16:35」に割れる）／
  採用系（apply_individual・pool-bulk は slot0 のみ書く → 片肺の型）／
  scope_optimizer（slot0 のみロード → move で完全片肺 → 全曜日 visit 消滅）／
  pfv_validator（slot1 非検証）。
- **一括投入の潜在バグ**: poolPatients は 2名体制患者を常に含み、bulk は slot0 のみ配置
  → 片肺 PFV → visit が生成されない。W-12a で塞ぐ。

## 1. 決定事項（PO 承認 2026-07-04）

| # | 決定 |
|---|---|
| D-1 | 候補生成 = **主従アンカー方式**（案2）: slot0 候補を既存ソルバで列挙 → 各候補時刻 T に他コースで slot1 が入るか slot_feasible で点検 → 両立する (T, コースX, コースY) のみ**ペア候補**として提示。主従を入れ替えた逆走査の union で偏り解消 |
| D-2 | **片肺提案は出さない**: 相方が入る時刻がなければ候補0件とし、除外理由 `no_pair_slot`（「同時刻に入れる2コースの組が見つかりません」）を曜日別に表示（N-6） |
| D-3 | **採用は2行原子**: A経路（PUT fixed-visits）は slot0+slot1 を同時刻・別コースで同時書き込み。B経路（この週だけ）は place-and-fix の multi-staff 対応（test_place_and_fix_multi_staff で対応済みの想定 — 実装時に検証し、不可なら B を無効化＋理由ツールチップで v1 出荷） |
| D-4 | **手動運用は壊さない**: 片肺のハードブロック（422）は行わず、pfv_validator に**警告 V7**（「2名体制の相方枠がない曜日」非ブロッキング）を追加。採用 UI 側が常に両 slot を送ることで正規動線から片肺を排除（I-12/N-7 の解消。P0-2「手動編集は現場の生命線」原則に整合） |
| D-5 | **scope_optimizer は当面「保護」**: requires_multiple_staff 患者の PFV を movable から除外（excluded 会計に `two_staff` を追加して黙って消さない）。原子ペア move は W-12c |
| D-6 | **一括投入は v1 で2名体制を対象外**: unplaced reason `two_staff_pending`（「2名体制 — 個別提案から配置してください」）で明示分離。ペアの一括原子挿入は後続 |
| D-7 | **W-12d = 「詰まり解消相談」への一般化**（方向性承認）: 候補0件のとき「既存訪問を1〜3手ずらせば入る」連鎖を提案する相談型（方式b の姉妹）。ピン/locked 不可侵・希望範囲内優先・確認必須・連鎖3手上限。詳細設計は W-12a 安定後 |

## 2. W-12a 詳細設計

### 2.1 BE — ペア候補生成（propose_slots_service / proposal_solver）

- `_enumerate_candidate_slots` に `candidate.requires_multiple_staff` 分岐を追加:
  1. 従来どおり全バケットで slot0 候補を列挙（`find_available_slots_for_candidate`）
  2. 各 slot0 候補 (bucket_X, T) に対し、同一 office・同一 weekday の**他コース** bucket_Y を走査し、
     T ちょうどで `slot_feasible`＋前後移動制約＋容量（90分占有は同住所時のみ・通常は service_minutes）を判定
  3. 成立ペアのみ `ProposedSlot` 化。逆走査（Y を主とする）の union・重複排除（(T, {X,Y}) の無順序キー）
- `ProposedSlot` 拡張（後方互換 optional）: `partner_course_code` / `partner_course_label` /
  `partner_course_template_id` / `partner_staff_name` / `partner_mini_schedule`（可能なら）
- **delta**: `marginal_cost_minutes = delta_X + delta_Y`（compute_exact_marginal をコースごと独立に2回 —
  別コースなので相互作用なし）。DELTA_EVAL_LIMIT の枠内でペアも同列に評価
- **スコア**: `_TWO_STAFF_BONUS = 800.0`（pair 1000 より下・通常より最優先。同住所ペアとの併発時は
  同住所が勝つ既存序列を維持）
- **除外理由**: `no_pair_slot` を excluded_summary 語彙に追加（優先度は no_gap の上）。
  「slot0 は入るが相方が全滅」のときに出す
- 定員超過相談（方式b）との併用は v1 スコープ外（include_overcapacity 時はペア分岐を通らず従来警告 —
  既存挙動不変。設計書 §4 バックログに記録）
- pool-overview / pool-bulk は compute_all_proposed_slots 経由で自動的にペア候補を得るが、
  **bulk は D-6 により patient 入口で除外**（overview の best_delta はペア delta が出てよい）

### 2.2 BE — 採用・検証・保護

- `pfv_validator` V7（警告・非ブロッキング）: normal mode で requires_multiple_staff 患者の曜日に
  slot0/slot1 の片方しかない → warning `multi_staff_incomplete_pair`（曜日付き）
- V3（他患者衝突）は現状 slot0 のみ対象 — v1 では slot1 も衝突検査対象に含める
  （同一患者の slot0/slot1 同時刻は衝突とみなさない例外を明示）
- `scope_optimizer`: ロード段階で requires_multiple_staff 患者の PFV を movable から除外し
  `excluded.two_staff` カウンタを追加（レスポンス＋FE 会計表示）
- `pool_bulk_inserter`: 入口フィルタに requires_multiple_staff → unplaced(reason=`two_staff_pending`) を追加
  （no_primary_office と同じ流儀・overcapacity_available_count は 0 のまま）

### 2.3 FE

- `PoolCandidateList`: ペア候補カード（「Aコース と Bコース の 16:00 に同時配置」— 2コースチップ・
  担当2名・合計 delta・警告バッジ）。採用確定（A経路）は slot0+slot1 の2行を
  mergeAdoptedIntoNormalFixedVisits に載せる（slot_index=1・partner_course_template_id 使用・
  同時刻・is_pinned/movability 運搬は既存規約）。B経路は place-and-fix の multi-staff 対応を検証の上で接続
  （不可なら disabled＋「2名体制はこの週だけ配置に未対応です」ツールチップ）
- 除外理由訳語: `no_pair_slot` →「同時刻に入れる2コースの組が見つかりません」/
  bulk の `two_staff_pending` →「2名体制（個別提案から配置してください）」/
  validator `multi_staff_incomplete_pair` →「2名体制の相方枠がない曜日があります」
- `two_staff_not_guaranteed` 警告はペア候補では出さない（保証されるため）。非ペア経路の残置箇所のみ維持
- PoolPanel の slot0/slot1 2枚カード: どちらをクリックしても同じペア候補が出る（挙動統一）

### 2.4 テスト要点

- ペア生成: 2コース同時刻の成立/不成立（片方の後方制約 NG で除外）/ 逆走査 union の重複排除 /
  決定性 / delta = 2コース合計 / no_pair_slot 理由
- 採用: A経路で slot0+slot1 が同時刻・別コースで PFV 化 → Layer 1 が visit_group 付き2visitを生成（貫通）
- V7 警告 / V3 slot1 衝突（同一患者ペアは非衝突）
- scope_optimizer: 2名体制 PFV が movable に入らない＋excluded.two_staff 会計
- bulk: 2名体制患者が two_staff_pending で unplaced

## 3. 後続 Wave

- **W-12b**: 週次生成まわりのテスト補強・`_align_same_address_pair_to_same_time` がペアを壊さない保護
- **W-12c**: scope_optimizer の原子ペア move（_SimState に visit_group 概念・2枠同時 remove/insert）→ D-5 の保護を解除
- **W-12d 詰まり解消相談**（方向性承認済み・詳細設計は別途）: 候補0件時に「ブロッカー特定 →
  各ブロッカーの退避先列挙（同日別時刻・別コース・希望範囲内の曜日変更）→ 1〜3手連鎖で開通 →
  乱れ最小順に提示 → 原子適用」。ピン/locked 不可侵・却下記憶尊重・確認必須・
  2名体制専用でなく候補0件全般の汎用相談（方式b の姉妹）

## 4. バックログ

- ペア×定員超過相談の併用（+1名でペアが入るケース）
- 一括投入のペア原子挿入（D-6 の解除）
- ペア候補への「ずらし込み」統合（W-12d と合流）
