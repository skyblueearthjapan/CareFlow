# ⭐特別訪問週間チケットの DnD 化 — 設計メモ（2026-09-08・PO 指摘「⭐ だけドラッグできない」）

正典 = 8/31 調査 `pool-placement-blockers-investigation-2026-08-31.md` §4 F-6。前提の F-2（BE `place` が `course_template_id`+`start_time` を受け Course を自動生成）は 2026-09-07 夜に実装済み（0337525）。**残りは FE のみ**。

## 1. 根本原因
- **⭐ カードは draggable でない（意図的）**: `SpecialTicketPlacePanel.tsx` は `PatientCard`（dnd-kit の `useDraggable`）を使わず素の `<button onClick>` で描いている（コメントに「チケットはドラッグ配置に対応していないため」）。DndContext・センサーはプール全体を包んでいるので、draggable の登録だけが無い。
- **drop 側にも分岐が無い**: `CourseDayTablePanel.handleDragEnd` は `pool-patient:` / `tl-visit:` / `tl-pair:` / event の 4 名前空間しか解釈しない。`special-ticket:` は全分岐に落ちず無反応。
- **曜日の罠**: プール drop は表示中の曜日タブ（`activeWeekday`）で仮想セルを作るが、BE `place` は **`mark.weekday`** で Course を解決する。木曜チケットを月曜タブに落とすと画面と違う木曜に配置される。FE で守る必要がある。

## 2. 設計（最小）
- id: `special-ticket:{markId}`（`SpecialTicketPlacePanel.tsx` に build/parse helper）。
- カード: `SpecialTicketCard` に切り出し `useDraggable` を付与。`PatientCard` と同じ pointerDown 6px 判定 / TouchSensor でクリック（従来のポップアップ）とドラッグを区別。`ghost` で DragOverlay 描画。見た目は据え置き。
- **掴めるのは表示中の曜日タブと同じ曜日のチケットだけ**（他曜日は `disabled` + title「木曜のカードです。木曜タブに切り替えてください」）。`handleDragEnd` にも `mark.weekday !== activeWeekday` → 警告トーストの保険。
- drop 先: `tl-col:` 列のみ。プール本体へ戻すのは noop。列の外は既存の「列の上で離してください」。
- `handleDragEnd` の新分岐: markId → チケット（`useSpecialVisitPool` のキャッシュ）→ 列テンプレート + スナップ時刻（9:00〜18:00・所要は `ticket.service_minutes ?? 60`）→ `applySpecialTicketDrop` = `usePlaceSpecialMark({course_template_id, start_time})`。NG/性別 422 は `placementConstraintConfirm.capture` で確認→acknowledge 再送（既存フロー）。成功トースト「◯◯様を 木曜 10:15 に配置しました（この週のみ・固定化しません）」。
- **2 名体制患者**: `place` は 1 名分しか作らないため DnD を塞ぎ、カードクリック→「配置先を決める」（＋訪問モーダル）へ誘導するトースト。
- **undo は無い**（`place` は op-log を書かない）→ トーストで「元に戻す」を約束しない。取消はカレンダーの ● メニュー。
- 週の食い違いは起きない（⭐ セクションは表示中の週のチケットだけを取得）。`kind='displaced'`（固定退避）チケットも同じ経路で置ける。

## 3. 変更ファイル（FE 約 220 行・1 日）
| ファイル | 内容 |
|---|---|
| `SpecialTicketPlacePanel.tsx` | `SpecialTicketCard`（useDraggable・click/drag 判別・ghost）・id helper・`activeWeekday` prop・他曜日 disabled |
| `PoolOverviewPane.tsx` | `activeWeekday` を素通し |
| `CourseDayTablePanel.tsx` | `useSpecialVisitPool` キャッシュ→Map・`activeSpecialTicket` state・DragStart/DragEnd 分岐・`applySpecialTicketDrop`・DragOverlay |
| BE | 変更なし |

## 4. テスト
- Panel: 同曜日カードに draggable 属性／他曜日は disabled＋title／クリック（0px）で従来ポップアップ（回帰の本命）。
- 盤面 DnD: `special-ticket:` → `tl-col:` で `place` が `{course_template_id, start_time}` で 1 回／範囲外は警告／曜日不一致は警告／2 名体制は誘導／422 → 確認 → acknowledge 再送。

## 5. リスク・PO 確認
1. 曜日の取り違え（FE ガードが唯一の防波堤・テストで固定）。2. クリック導線の回帰（6px/250ms を移植）。3. undo 無し（文言で明示・op-log 対応は別タスク）。4. DnD は定員・移動時間・空きを見ない強制配置（⭐ の方針とは整合・F-4 `override_reason` 未実装）。5. 固定退避チケットの DnD 運用可否は PO 確認。
