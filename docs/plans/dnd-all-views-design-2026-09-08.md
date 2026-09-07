# ⭐/プールカードの DnD を全ビューへ — 設計（残タスク 8-6・2026-09-08）

PO 指示（9/8）: ①どこでも掴める ②置く瞬間に案内/警告 ③曜日が違う場所なら「これは◯曜日の予定ですが、ここに配置して本当によろしいですか？」の確認 → 配置。対象 = 週ビュー・コースの週ビュー・**職員スケジュール（必須）**。現行 8-2 の「他曜日は掴めない／曜日タブへ案内」は置き換える。

## 1. 現状（調査 9/8）
- 3 ビューとも同じ dnd-kit `DndContext`（`CourseDayTablePanel.tsx` ~5453）の内側。**droppable 名前空間を足すだけで 1 つの仕組みに統一できる**。
- 職員スケジュール `StaffWeekBoard` はコース帯/訪問帯の draggable にブラウザ標準 DnD を使うが、セルを dnd-kit の droppable にしても衝突しない（標準 drag は dnd-kit の active を作らない）。
- 週タイムライン `WeekTimelineBoard` は時間軸あり（行高が日タイムラインと同じで `snapYOffsetToMinutes` 流用可）。週リスト `CourseWeekOverview` と職員スケジュールは時間軸なし。
- プールカードは他ビューで離すと「列の上で離してください」、⭐ は日タイムライン以外では掴めない（8-2 のゲート）。

## 2. 設計
### 2-1. droppable 名前空間と共通リゾルバ（`courseDnd.ts`）
```
sw-cell:{rowKey}:{weekday}       職員スケジュールの <td>（行 = staffId or __unassigned__）→ 時刻なし
cwo-cell:{templateId}:{weekday}  週リストのセル                                        → 時刻なし
wtl-col:{templateId}:{weekday}   週タイムラインの列                                    → 時刻あり(snap)
```
`resolveDropTarget(overId, activeRect, overRect) → { weekday, courseTemplateId|null, staffId|null, time|null }`。`handleDragEnd` の先頭で 1 回解決し、既存の `tl-col:` も同じ形に載せる。

### 2-2. 「配置の確認」モーダル `PlacementConfirmDialog`（新規）
開く条件 = `time === null` **または** `weekday !== ticket.mark.weekday`（プールカードは曜日束縛なし → time===null のときのみ）。
- 患者/チケット名（⭐ は種別バッジ）・対象曜日。**曜日が違うときだけ**警告行「これは◯曜日の予定ですが、△曜日に配置して本当によろしいですか？」。
- 対象コース: 職員スケジュールは staffId×weekday → その職員のコースを逆引き（0 件 = 担当なし行等 → その拠点の M を既定候補・2 件以上 → select）。週ビューは列から確定（表示のみ）。拠点跨ぎは候補から除外。
- 開始時刻 select（`TIME_OPTIONS` 9:00〜18:00）: 既定 = ⭐ `last_placement.start_time` → 患者の希望開始 → 09:00。時刻あり（週タイムライン・同曜日）ならモーダルは出さない。
- 所要 = ⭐ `service_minutes ?? 60`／プール `weekly_pattern.service_minutes ?? 60`。
- 「やめる」「配置する」→ 既存 `applySpecialTicketDrop` / `applyPoolDrop` を呼ぶ（NG/性別 422 は既存の確認→再送）。2 名体制のプール患者は既存の相方コースダイアログへ。⭐ の 2 名体制は現行どおり DnD 不可（クリック経路）。
- `AddVisitAnywhereDialog` は流用しない（多目的プランナで無効化する prop の方が多い）。

### 2-3. BE: `POST /special-visit-marks/{id}/place` に `weekday`（任意・0〜5）
指定かつ `mark.weekday` と異なるとき「この週の別曜日へ ○ を移してから配置」を **同一トランザクション**で行う。
1. `kind='displaced'` → 422「退避枠は曜日を変えられません」
2. 移動先が期間外 → 422
3. 同 period/iso 週/移動先曜日/kind=extra/status≠cancelled の別 mark → **409** `{"code":"special_mark_cell_conflict","existing_mark_id":..,"weekday":..}`
4. `mark.weekday = weekday` → 以降は既存の配置処理（`mark_date` は新曜日で計算・過去日ガードは新モードのまま）。
FE は 409 を受けたら「△曜には既に追加枠（○）があります。そちらを配置しますか？」→ OK なら `existing_mark_id` へ `place`（weekday なし）。元のチケットは触らない。
プールカードの `place-and-fix` は BE 変更なし（weekday は既存項目）。

### 2-4. ゲート撤去
`SpecialTicketCard` の `dragDisabled` は `!canEdit` のみ。`CourseDayTablePanel` の `activeWeekday` ゲートと `handleDragEnd` の曜日警告は「確認モーダル起動」に置換。

## 3. フェーズ
| Phase | 内容 | 規模 |
|---|---|---|
| 1（必須） | 職員スケジュール: `sw-cell:` droppable（`StaffWeekDropCell`）・リゾルバ・`PlacementConfirmDialog`・ゲート撤去・BE `weekday` | FE ~400 行 / BE ~60 行 |
| 2 | 週タイムライン `wtl-col:`（同曜日は snap で確定・異曜日のみ確認）・週リスト `cwo-cell:`（常に確認） | FE ~180 行 |

## 4. テスト
- StaffWeekBoard: セルが droppable・標準 DnD のコース帯移動が回帰しない。
- SpecialTicketPlacePanel: 他曜日でも掴める（現行テストを反転）・クリック導線維持。
- PlacementConfirmDialog: 異曜日の警告文言・既定時刻の優先順・コース 0/1/2 件。
- 盤面: `sw-cell:` drop → ⭐ は `place {course_template_id, start_time, weekday}` 1 回／プールは `place-and-fix` が cell の weekday／409 → 既存 ○ へ再送／422 → 制約確認 → acknowledge。
- BE: weekday 上書きで mark が動き週合計不変／衝突 409 + existing_mark_id／displaced 422／期間外 422／過去日 422。

## 5. リスク・PO 確認
1. 曜日移動は特別訪問週間カレンダーの ○ の位置が変わる（週合計は不変）。成功トーストで「◯曜の ○ を △曜へ移して配置しました」を明示。
2. FE の曜日ガードを外すので確認モーダルが唯一の砦。閉じたら place が飛ばないことをテストで固定。
3. undo なし（`place` は op-log 非対応）。取消はカレンダーの ● メニュー。
4. 既定時刻が 09:00 に集中する恐れ → 既定を空にして必須選択にするかは PO 確認。
