# 「担当なし」からの投入提案 — Phase 2 設計 (2026-08-23 案)

前提: Phase 1(`staff-off-week`・本番 `3a4e2cc`)で、急休は「一度 担当なし に戻す」が既定になった。
Phase 2 は **担当なし に溜まった予定を、保留プールと同じ体験で「誰に入れられるか」提案して戻す**。
PO 要望: コース丸ごとの提案（「この人に当てられます」）と、患者1人の提案（患者クリック）の両方。

## 1. 体験（モック = `docs/mockups/unassigned-suggestions-mock.html`）

### 1-1. 「（担当なし）」行の見え方（リスト／タイムライン共通）
- コース帯（例: `稲毛C  6件`）の右端に **「◎ 2名 引受可」** のような小バッジ（= コース丸ごと引き受けられる人数）。未計算なら「提案を見る」ボタン。
- バッジ／ボタンをクリック → **コース提案ポップオーバー**:
  `稲毛C（8/24 月・6件 09:30〜17:35）を誰に？`
  - ◎ 空き（コース丸ごと可）: `髙梨さん  空き・同拠点・馴染み 3名`  [このコースを割り当てる]
  - △ 一部重なり: `宇田川さん  13:30 が重なる`（割当不可・理由のみ）
  - × 休み／NG など: 折りたたみで一覧
  - 下部: 「1件ずつ分けて入れる」→ 患者ごとの提案へ
- 訪問（患者1人）をクリック → 既存 **VisitActionMenu** に **「提案」セクション**: `◎ 髙梨さん / ◎ 熊澤さん / △ 宇田川さん(13:30 重なり)` → 1クリックで `visit-assign-staff-week`。

### 1-2. 保留プールとの対応
| 保留プール | 担当なし（新） |
|---|---|
| 「効果を表示」で各患者に「ここに入れそう」バッジ | 「提案を見る」でコース帯に「◎ N名 引受可」バッジ |
| 患者カードクリック → 投入提案 | 訪問クリック → 提案セクション（候補スタッフ） |
| 提案 = 空き枠（時刻）を探す | 提案 = **時刻は固定**で、入れる**人**を探す（急休の逆操作） |

## 2. API（新規 1 本・read-only）

`POST /api/v1/schedule/v2/assign-candidates`
```json
req: { "date": "YYYY-MM-DD", "course_id": UUID | null, "visit_ids": [UUID] | null }
     // どちらか必須。course_id = その日のそのコースの planned 訪問すべて / visit_ids = 指定訪問のみ
res: SubstituteCandidatesResponse と同形 (groups[].visits[] / candidates[] status ok|warn|ng / reasons / score / load_today)
     + "whole_ok_staff_ids": [UUID]   // 全訪問 ok の交差（コース丸ごと可）
```
- 実装は `substitute_candidates.build_substitute_candidates` の **対象集合の作り方だけ差し替え**（抜けるスタッフ無し → 「対象訪問の現担当を除外しない」。担当なしの訪問は現担当が居ないので除外対象なし）。判定ロジック（休み/非勤務日/NG/性別/新人/拠点/イベント重なり/時間重なり(同住所免除)/同行拘束/継続性スコア）は単一ソースのまま。
- ついでに `substitute-candidates` に `whole_ok_staff_ids` も追加（Phase 1 の FE 交差判定を BE に寄せる）。

## 3. 実行（既存 API のみ）
- コース丸ごと → 訪問ごとに `visit-assign-staff-week`（同一 op_group_id・既存 `runAssignQueue`・NG/性別は 422→確認→ack）。完了後 `PATCH /courses/{id}` で `assigned_staff_id` も合わせる（表示の正典）。**コース経路だけで付替はしない**（undo/manual_staff_override の教訓）。
- 患者1人 → `visit-assign-staff-week` 1 件。
- トースト「稲毛C 6件 を 髙梨さんへ（今週だけ・戻るで復元）」。

## 4. FE 構成
- `cockpit/AssignSuggestionPopover.tsx`（新）: コース提案（候補一覧・理由・割当ボタン・「1件ずつ」）。
- `VisitActionMenu`: `suggestions` セクション（props で候補を受ける or 内部で `useAssignCandidates({visit_ids:[id]})`）。
- `StaffWeekBoard` / `StaffTimelineView`: （担当なし）行のコース帯に `onSuggestCourse(courseId, weekday)` と バッジ用 `suggestionBadges: Map<courseKey, {ok:number}>`。
- 「提案を見る」= その日の担当なしコースを一括で `assign-candidates`（コース数分・並列・キャッシュ 60 秒）。自動計算はしない（API 負荷と誤読防止・プールの「効果を表示」と同じ on-demand）。
- `lib/queries/cockpit.ts`: `useAssignCandidates`。

## 5. 段階
| Phase | 内容 |
|---|---|
| 2-A | BE `assign-candidates` + テスト（course_id/visit_ids・whole_ok・read-only・403） |
| 2-B | コース提案ポップオーバー + 担当なし行のバッジ/「提案を見る」 + 割当実行（runAssignQueue 流用） |
| 2-C | 患者1人の提案（VisitActionMenu 提案セクション） |
| 2-D（任意） | タイムラインの空きレーンに「ここに入れそう」のゴースト表示（候補選択中に候補スタッフ行へ薄いバー） |

## 6. 決めごと／注意
- 提案の並び: ok（score 降順）→ warn → ng。ok が 0 件なら「丸ごと引き受けられる人はいません。1件ずつ分けて入れてください」。
- 同じ日に複数の担当なしコースがある場合、A を髙梨さんに入れた直後は B の候補が変わる → 割当後に再計算（キャッシュ破棄）。
- 「提案」は今週だけの操作。型（PFV/テンプレ）は不変（憲法1）。
- 担当なし行の訪問に `manual_staff_override=False` で置かれた場合、週生成で型の担当に戻る可能性 → 既存の保護規則（manual_week）どおり。
