# 健康診断→処方箋 詳細設計 — 診断・原因・対策・実行後見通しの一気通貫

作成 2026-07-03（PO 承認済み方針）/ 前提: scope-optimization W1-W3 本番稼働（`c6e22fa`）/
親: schedule-advisor-design.md §3 Phase 1 / scope-optimization-design.md

## 0. 承認済み方針

- 目的: 「問題を知らせる」だけの健康診断を、**原因（なぜ重いのか）→ 対策（どう直すか）→
  実行後の見通し（どれだけ変わるか）** まで示す「処方箋」に拡張する。
- D-1: **2つの物差しの橋渡しを明記** — 健康診断は「今週の実績訪問」、範囲最適化は
  「恒久パターン (PFV)」。実行後の姿は**シミュレーション値（見通し）と明記**し、
  適用成功後に「今週の実予定への反映は固定枠戻」を案内する。
- D-2: **対策の事前自動計算はしない** — クリック時に計算（現状方式）を維持。
- 段階: H1（原因ドリルダウン）→ H2（手順の理由文＋コース別 before/after）→
  H3（要対応サマリ）。各段独立デプロイ可・既定挙動不変（additive のみ）。

## 1. H1: 原因ドリルダウン「どこで負担が生じているか」

### 1.1 BE — GET /v2/schedule-health/course-detail

Query: `iso_year, iso_week, office_id, course_code`（対象コースの全曜日分を返す。
FE は曜日フィルタで絞って表示）。RBAC admin/manager・read-only。

計算（`schedule_health.py` に `compute_course_detail` を新設。物差しは健康診断と完全同一）:
- ローダは `_load_health_buckets` を再利用（planned/in_progress/completed・座標欠損を含む）。
- **transitions**: 曜日ごとに start 昇順の連続ペアを列挙し
  `{from/to の patient_id・name・時刻, travel_minutes, travel_km}` を返す
  （同住所=0/0・座標欠損=travel 0 は健康診断と同じ規約。全件返し FE が上位を強調）。
- **patient_costs**: 患者ごとの配置コスト = **厳密限界コスト**
  （improvement_engine `compute_exact_marginal` を import 再利用 = W3 正典）。
  座標欠損の訪問は existing から除外し、その患者はランキング対象外
  （transitions 側では健康診断と同じく travel 0 で現れる。docstring に明記）。

Response: `{ office_id, course_code, weekdays: [ { weekday, course_label, staff_name,
totals{travel_minutes, travel_km}, transitions[], patient_costs[] } ] }`（空曜日は出さない）。

### 1.2 FE — コース行クリックで展開

- `ScheduleHealthDialog` の `CourseBarRow` を**行クリックで展開**（aria-expanded）。
  展開時に course-detail を取得（staleTime 5分）し表示:
  - 「重い移動」: transitions を travel_minutes 降順で TOP3（コース移動合計に占める割合%
    を併記。**33%以上はオレンジ強調**）。曜日フィルタ選択中はその曜日のみ、全曜日なら曜日別見出し。
  - 「配置コストが大きい患者様」: patient_costs 降順 TOP3（「◯◯様の配置で 36分/週」）。
  - 末尾に既存の**「この原因を解消する対策を計算」**（= onOptimizeCourse 既存導線）。
- 既存の「最適化」ボタン・警告バーの挙動は不変（クリック展開は additive）。

## 2. H2: 対策の理由文＋コース別の実行後見通し

### 2.1 手順の理由文（reason）

- `ImprovementCandidateData.reason: str | None` を追加し、**move 提案**に生成:
  「現在は {prev}様→{next}様 の間で移動 {cur_min}分ぶんのコスト。{移動先ラベル} の
  {newPrev}様の後ろでは {cand_min}分になり −{delta}分/週」。
  隣接名はスナップショット（H2 で既に候補へ付与済みの source/destination_course）から
  時刻で導出。先頭/末尾は「コース先頭/末尾」。**swap は簡易文**
  （「◯◯様と入れ替えて双方の回り道を解消 (−N分/週)」）。
- `ImprovementSuggestion.reason: str | None`（既定 None・後方互換）。
  FE は共有カード (`ImprovementSuggestionCard`) のチップ列の下に 1 行で表示
  → **範囲最適化と患者詳細の両方に同時反映**。

### 2.2 コース別 before/after（実行後の姿）

- scope simulate レスポンスに `courses: [ { office_id, weekday, course_code, course_label,
  staff_name, before{ScopeOptimizationMetrics}, after{同} } ]` を追加
  （模擬バケット単位に `_compute_course_metrics` を before/after で 2 回集計。additive）。
- FE (`ScopeOptimizeDialog`): 前後比較タイルの下に**コース別テーブル**
  「稲B(火): 移動 92分→51分 (−41)」。変化のない行は畳んで「変化なし N コース」。
- D-1 対応: 表の見出しに「適用した場合の見通し（恒久パターン基準）」と明記。
  apply 成功トーストに「今週の実予定への反映は『固定枠戻』を実行してください」を追加。

## 3. H3: 要対応サマリ「今週の処方箋」

- `ScheduleHealthDialog` のタイル下に**要対応バナー**: 警告判定（表示中平均の1.5倍超 =
  既存 isHigh 判定を再利用）のコースを移動時間降順で列挙し、各行に
  「稲B 移動92分 〔原因を見る（=行展開へスクロール）〕〔対策を計算〕」。
- 追加 API なし（既存バー集計の再利用）。警告ゼロのときは非表示。

## 4. テスト計画

- BE: course-detail（遷移の値・同住所0・座標欠損の travel0 とランキング除外・
  空コース404 or 空配列・RBAC 403）/ simulate の courses[]（before/after がコース単位で
  合計と整合・変化なしコースも返る）/ reason（move で隣接名と分数を含む・swap 簡易文）。
- FE: zod（course-detail / courses[] / reason の後方互換 default）/
  ScheduleHealthDialog 展開（fetch 発火・TOP3 表示・対策ボタン発火）は既存テストの
  流儀に合わせ最小限（コンポーネントテストが無い場合は zod のみ＋手動確認）。

## 5. 段階・コミット分割

| 段 | 内容 |
|---|---|
| H1-BE | compute_course_detail＋schema＋endpoint＋テスト |
| H1-FE | コース行クリック展開＋重い移動/配置コスト表示＋対策導線 |
| H2-BE | reason 生成＋simulate courses[]＋テスト |
| H2-FE | カード理由行＋コース別 before/after 表＋固定枠戻案内 |
| H3-FE | 要対応サマリバナー |

実装→独立レビュー→コミット→本番デプロイ（migration なし）。

## 6. 制約・割り切り

- 原因分類の自動ラベリング（「孤立患者」「順序の往復」等のカテゴリ分け）は初版では行わず、
  数字（重い遷移・配置コスト）で示す。文言カテゴリは現場フィードバック後。
- patient_costs は「その患者を抜いた場合に浮く量」であり、複数患者の同時削除効果は
  加算できない（注記を UI に出さず、文言を「◯◯様の配置で N分/週」に留める）。
- 健康診断ドリルダウンは実績 visit ベース（当週）。恒久パターンと乖離しうる点は
  対策計算（PFV ベース）側の見出しで吸収（D-1）。
