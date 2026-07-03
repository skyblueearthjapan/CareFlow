# P5 詳細設計: 欠勤対応のコース単位再設計（引き継ぎプラン階層）

作成 2026-07-03（architect 設計・ディレクター承認済み）/ 前提: P3-① 訪問単位実装（p3-1-staff-substitute-design.md）

## 0. 背景（ユーザーの運用フィードバック・確定）
スタッフ配置の単位はコース（1コース1スタッフ/日）。欠勤=コースが丸ごと空く。訪問単位の候補提示は
「他スタッフの隙間への場当たり的な差し込み」を量産し、管理者がコースの言葉で判断できない。
**提案単位を「引き継ぎプラン」に再設計**。確定ポリシー: 一般スタッフ優先（Mgrはフォールバック）/
分散は最終手段のみ / 患者単位UIは最終手段時のみ。

## 1. プラン階層
| 層 | 内容 | 生成条件 |
|---|---|---|
| 1 | 丸ごと引き継ぎ（一般スタッフ。例外≦EXCEPTION_THRESHOLD=2 なら「例外付き」で成立） | 常に列挙 |
| 2 | AM/PM 分担（2人。境界=既存営業ブロック。12:00台開始は保守的にAM） | コースがAM/PM両方に跨るときのみ |
| 3 | マネージャー丸ごと | **層1-2が0件のときのみ**（一般優先ポリシー） |
| 4 | 分散（貪欲・移動最小。受け手ごとに束ね時系列順。unassignable は明示） | 層1-3全滅時のみ |

- course_id=NULL の visit は層4のみ対象。複数コースは独立にプラン生成。
- スコア: TIER_SCORE(10000/5000/2000/0) + 例外×(-500) + 追加移動合計×(-1) + 継続性 + 負荷×(-5)。
  ソート (-score, exception_count, plan_id)。上限: 層毎2・合計5。
- 判定は既存カーネル完全再利用: _hard_constraint_reason（6制約）をコース内全visitへ一括適用
  （extra_others=コース内他visit で intra-batch 衝突も既存パターン）。_score_candidate / _load_eval_context 不変。

## 2. API 契約（レスポンス拡張・後方互換）
- GET candidates のレスポンスに `plans: list[SubstitutePlan] = []` を追加（パス・パラメータ不変。旧FEは無視）。
- SubstitutePlan{plan_id, tier(1-4), tier_label, course_id?, course_code?, assignees[]{staff_id, staff_name,
  staff_sex?, block('full'|'am'|'pm'), visit_ids[], visit_count, added_travel_minutes, existing_load},
  total_visits, exception_count, exceptions[]{visit_id, patient_id, patient_name, start/end_time, reason},
  score, warnings[]}
- **POST apply は不変**: FE がプランを substitutions[]（visit_id×staff_id）に展開して既存フローへ。
  例外visitの個別選択分も同配列に追加。既存の N-4 再検証・intra-batch 検証・all-or-nothing がそのまま効く。
- _VisitContext に course_id/course_code を追加（_load_absent_visit_contexts で Course.code を追加取得）。

## 3. FE（StaffSubstituteDialog Step2 再構成）
- plans あり: プランカード（ラジオ）中心。層バッジ・担当・件数・例外・「継続◎/移動+N分/当日N件」。
  選択プランに例外があれば「例外患者の個別選択」セクション（軽量候補ラジオ）。
- 「個別に選択する」トグルで従来の訪問単位UI（VisitCandidateCard 温存）へ。plans 空なら従来UIのみ。
- state: selectedPlanId / exceptionSelections / manualMode（既存 selections は手動モード用に温存）。
- buildSubstitutions(): プラン assignees 展開＋例外選択を合流 → 既存 apply。

## 4. コミット分割・テスト
- **BE-1**: エンジン（層1-4）＋スキーマ＋GET拡張＋テスト#1-19（層別成立/例外閾値/AM-PM分類/
  Mgr生成条件/分散の束と時系列/上限/NULLコース/2名体制/プラン展開apply統合/後方互換）
- **FE-1**: プランUI＋zod＋テスト#20-23（カード表示/例外セクション/最終手段切替/展開payload）
- リスク: コース内visit同士の偽陽性衝突（同住所判定で概ね回避・境界テスト）/ NULLコース多環境は層4のみ。
