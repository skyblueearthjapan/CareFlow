# 設計書: プール投入の個別化・物差し統一（C案）

作成 2026-07-03 / PO 承認済み（同日）: **C案ハイブリッド・3段階**で進める。
調査レポート＝配置提案5機能のエンジン比較（3系統: auto_allocator_v2 / propose_slots / improvement_engine 正典）。
正典の定義は `docs/plans/scope-optimization-HANDOFF.md` §4、既知不整合は `docs/plans/scheduling-logic-normalization.md`（I-01〜I-20）。

## 0. 目的（PO 指示）

1. 一括「プール投入」を最終的に廃止し、**患者一人ずつの個別提案**に統一する
2. 提案の物差しを「配置最適化」正典（`compute_exact_marginal` 厳密限界コスト）に揃え、
   診断・改善提案・範囲最適化・プール投入が**同じ数字**で語れるようにする
3. 管理者が「この患者をどこに投入するのが最適か」を判断できる情報を提示し、
   判断 → 実行（スケジュール反映）のフローを成立させる

**3段階の順序が安全装置**: ①判断材料 → ②俯瞰一覧（廃止の受け皿）→ ③一括廃止。
受け皿ができるまで既存の一括プール投入は温存する。

## Stage P-1: 判断材料（delta 表示＋除外理由）

### P-1a. 個別提案候補への厳密限界コスト付与
- `propose_slots_service.compute_all_proposed_slots` の候補それぞれに
  **挿入の厳密限界コスト** `marginal_cost_minutes` を追加:
  `course_travel_buffer_total(挿入後) - course_travel_buffer_total(挿入前)`
  （improvement_engine の `compute_exact_marginal` / `course_travel_buffer_total` を import 再利用。コピー禁止）
- **計算量対策（2段構え）**: 従来の加点スコアで候補を生成・粗ランキング →
  上位 `DELTA_EVAL_LIMIT`（既定 20）件のみ delta を厳密計算 → **delta 昇順を主キー**に再ソート
  （同値は従来スコア降順 → 開始時刻昇順で決定的に）。同住所ペアは delta≈0 になるため自然に最上位に来る
  （pair_bonus の役割を正典の物差しが吸収する）。
- レスポンス: `ProposeSlotItem` に `marginal_cost_minutes: float | None` を後方互換で追加
  （delta 未計算の下位候補は null）。
- FE `PoolCandidateList`: 候補カードに「コースの移動 **+N分**」バッジを表示し、delta 順で並べる。
  診断・改善提案と同じ「分」の物差しであることを明記（ツールチップ or 添え書き）。

### P-1b. 候補 0 件時の除外理由（N-6「黙って消さない」）
- `find_available_slots_for_candidate` の走査結果から、コース×曜日ごとの除外理由を集約して
  `excluded_summary` として返す: `{ reason: 'capacity_full' | 'lunch_window' | 'travel_shortage' |
  'no_gap' | 'course_closed', count, sample_course_code, weekday }`
- FE: 候補 0 件のとき「なぜ入れられないか」を理由別に表示
  （例: 「火曜: 全コース容量上限 / 木曜: 移動時間が確保できず」）。

### P-1 スコープ外（後続バックログ・別 Wave）
- H5 受入カレンダーの propose-slots 統合（I-07・warning 推奨）/ pair_mode 参照（I-11）/
  2名体制の相方枠自動作成（I-12）/ 挿入理由文 `build_insert_reason`

## Stage P-2: プール患者の俯瞰一覧（一括廃止の受け皿）

- BE: `POST /v2/pool-overview`（または既存プール一覧の拡張）: プール患者ごとに
  **最良候補 1 件**（P-1 の delta 最小候補）を軽量計算して返す:
  `{ patient_id, best_slot: {course_code, weekday, start}, best_delta_minutes, candidate_count }`
  計算量: 1 患者 = 粗ランキング → 上位数件のみ delta。патient 数×この計算で重い場合は
  limit/サイズガード（範囲最適化④と同じ方針）。
- FE: 保留プールペインに「効果順」ソート＋各患者の best_delta 表示。
  クリックで既存 PoolCandidateList 展開（= 既存導線の強化であり新画面は作らない）。
- これにより「全体を見渡してから効果の大きい患者から着手する」という
  一括投入の俯瞰価値を個別フローで代替する。

## Stage P-3: 一括プール投入の廃止

- FE: DiffAddDialog の一括推奨カード（auto_allocator_v2 提案）を撤去し、
  「プール投入」ボタンは俯瞰一覧（P-2）への導線に置換。現場周知後に実施。
- BE: `POST /v2/diff-add` は当面残置（ロールバック余地・full-optimize は別モードで継続）。
  安定後に diff_add 分岐の削除を別途判断。
- `apply-individual` / `PUT fixed-visits` の採用経路は不変（pfv_validator 安全網済み）。

## 体制・検証

- 各 Stage: executor 実装 → code-reviewer 独立レビュー → 修正 → コミット → デプロイ
- P-1a の回帰テスト: 同住所同時刻挿入で delta≈0 が最上位 / 回り道挿入で delta 大 /
  改善提案の `compute_exact_marginal` と同一値になるクロスチェック
- 性能: 本番相当データで propose-slots 応答時間を前後比較（DELTA_EVAL_LIMIT 調整）
