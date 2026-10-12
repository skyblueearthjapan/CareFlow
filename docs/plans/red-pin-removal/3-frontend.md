# 赤ピン（完全固定）撤去 — フロントエンド棚卸し（読み取りのみ）

対象: `frontend/`（node_modules / .next 除外）。調べた日: 2026-10-12（develop・c17aafe）。
行番号は現在の作業ツリーのもの。パスは `frontend/` からの相対パス。

---

## 1) 要約

- 赤ピンが**画面に出る場所は 6 系統**:
  ① 患者マスタの固定訪問スケジュール（行ごとの「完全固定」チェック・全曜日一括の帯・閲覧表示の赤バッジ・赤い行の背景・「旧設定」表示）
  ② スケジュール盤面の日タイムライン（右上の赤い画鋲・右下の操作群の赤トグル＋PinScopeMenu・「固」「時」「曜」マーク・ドラッグ中の影の画鋲）
  ③ 週タイムライン（赤い画鋲・「固」マーク）
  ④ 日リスト TimelineDayList（赤い画鋲・琥珀色の行背景・赤トグル PinToggleButton）
  ⑤ コース別の週一覧 CourseWeekOverview（赤トグル PinIconButton）
  ⑥ 確認文・ダイアログ（移動・削除・プールへ戻すときの「⚠ これは完全固定です」、見送りダイアログの「完全固定として記録」、改善提案・範囲最適化・詰まり解消の「完全固定N件」「ピン留め」「固定（可動域）」の内訳、「可動域未設定」バッジ、範囲最適化の説明文、週次ガイドの FAQ、管理画面の機能フラグ説明）
- **モバイル（/m）・現場ボード（field）・訪問モニター・オンボーディングには赤ピンは無い**（grep で 0 件）。職員スケジュール（コックピット）も赤ピンは無く、青ピンだけ。
- **e2e（Playwright）で赤ピンに触れるものは無い**。vitest は **直接関係が 22 ファイル**、g21 フックのモックだけのものが **10 ファイル**。
- API クライアント: `lib/queries/g21.ts`（`useTogglePfvPin` = `PATCH /patients/fixed-visits/{id}/pin`、`useBulkPinPfvs` = `POST /patients/fixed-visits/pin/bulk`）と `lib/schemas/g21.ts`。これは**撤去の対象**。
- **青ピンと同じ部品を使っている箇所が多い**: 右上の画鋲（青は「赤の左隣」へずらす指定）、右下の操作群 `[赤][青][×]`、日リストの `[赤▾][青][×]`、`PushPin` アイコン（既定が赤い頭で、青は className で上書きしている）、`TimelineDayBoard-week-pin.test.tsx`。§5 にまとめた。
- 撤去で**消えるもの**: `PinScopeMenu.tsx`、`MovabilityMark.tsx`、`PinToggleButton`、`PinIconButton`、`CornerPushPin`（赤い画鋲）、`handleTogglePin`、g21 の pin フックとスキーマ、`LEGACY_MOVABILITY_LABELS`、`lockedNotice`。
  **残すが中身を変えるもの**: `masterDivergence.ts`（「完全固定にできません」の文言）、`DismissReasonDialog`（記録する先が無くなる）、提案系の内訳表示、`_proposeSlotUtils`／`addVisitExecutor`（movability をそのまま運ぶ処理）。

---

## 2) 画面ごとの表

### 2-1 患者マスタ › 固定訪問スケジュール（`app/(app)/patients/_components/PatientFixedVisitsPanel.tsx`）

| 行 | 画面に出るもの | 消すもの | 波及（レイアウト・文言） |
|---|---|---|---|
| 64 | — | `type Movability` の import | — |
| 120-125（コメント） | — | 「可動域の旧値と同じ流儀」というコメント | 文言だけ |
| 131-142 | 「旧設定: 時刻変更可（旧設定）／曜日変更可（旧設定）」のラベル表 | `LEGACY_MOVABILITY_LABELS` | — |
| 197-221 | （型）`DayRow.is_pinned` / `DayRow.movability` | 2 つのフィールド | `emptyDayRow`・merge・payload がすべてこれを読んでいる |
| 239-240 | — | `emptyDayRow` の `is_pinned:false, movability:'unknown'` | — |
| 283-306 | — | サーバーの行を DayRow へ変換するときの is_pinned／movability の合成（slot 0 を優先） | — |
| 340-376 | — | 保存の payload（slot 0 と 1）に入れている `is_pinned: mirroredPinned` と `movability` | **論点**: 送らなくなったときに BE が既定値（unknown）で上書きするか、既存値を保つか（§7-3） |
| 388-448 | — | `weeklyPatternToDayRows` が既存の is_pinned／movability を保つ処理 | 「希望から自動生成」のテスト（weeklyPatternToDayRows.test）に影響 |
| 519-558 | **閲覧表示**: 完全固定の行は `bg-red-50` の背景、`data-pinned`、赤いバッジ「📌完全固定」（`ro-pin-${wd}`、PushPin を `text-red-600` で描画） | `roLocked`・背景色・バッジ | バッジが消えると、同じ行の「基本N分と異なる」「サブ拠点」バッジが左へ詰まるだけ |
| 663-676 | **編集表示**: 完全固定の行は `bg-red-50` の背景、`data-pinned` | `isLocked` と背景色 | — |
| 817-852 | **編集表示**: 行内のチェック「📌完全固定」（`pfv-locked-label/checkbox-${wd}`）、注記「システムはこの枠を動かしません（手動での変更はできます）」（`pfv-locked-note`）、「旧設定: …」（`pfv-legacy-movability`） | label・Checkbox・注記・旧設定の span のまとまり | 行が `flex-wrap gap-3` なので、エラー／警告の文が左へ詰まる。行の高さが下がることがある |
| 1043-1070 | — | `enabledDayCount` / `allEnabledLocked` / `handleSetAllLocked` と toast 2 種「全曜日を完全固定にしました…」「…解除しました…」 | `enabledDayCount` を他で使っていないか確認すること（この帯でしか使っていない） |
| 1075-1083 | toast「…既存の完全固定・コース選択は保持されています」 | 文言の「完全固定・」 | → 「既存のコース選択は保持されています」 |
| 1382 | コメント「pinned 保護の violations 等」 | コメントだけ | — |
| 1388-1419 | **全曜日一括の帯**（赤枠・`bg-red-50/50`）「完全固定（システムは動かさない）: [全曜日を完全固定][全曜日の完全固定を解除] 各曜日ごとの切替は行内の「完全固定」で。…」（`pfv-lock-all-toolbar` / `pfv-lock-all-button` / `pfv-unlock-all-button`） | 帯をまるごと | 帯の下の「保存」などのボタン行（`pt-2`）が上へ上がる。帯が無くなると、上の `pfv-form-error` とボタン行の間の余白を確認すること |

関連:
- `app/(app)/patients/_components/FixedVisitScopeConfirmDialog.tsx:362` — 「曜日・開始時刻・所要時間の変更はありません（**コース・完全固定などの変更のみ**）」→ 「（コースなどの変更のみ）」。
- 患者マスタ › **希望訪問スケジュール**（`WeeklyPatternEditor.tsx` ほか）: **赤ピンの痕跡は無い**。`time_type === '固定'`（希望時刻の種類）は別の概念で、残す。
- 患者マスタ › `components/patients/PatientStatusChangeDialog.tsx:72` の `week_pinned: '青ピン（今週固定）'` は青なので対象外。

### 2-2 スケジュール盤面（`components/schedule/v2/CourseDayTablePanel.tsx`）

| 行 | 画面に出るもの | 消すもの | 波及 |
|---|---|---|---|
| 125, 729, 731 | — | `useBulkPinPfvs` / `useTogglePfvPin` の import と呼び出し | 10 本のパネルテストの `vi.mock('@/lib/queries/g21')` が不要になる（§4） |
| 129 | — | `type PinScope` の import | — |
| 292 | — | `type Movability` の import（`SlotIndex` は残す） | — |
| 662-666（コメント）, 1276-1303, 1329-1331 | — | 日の盤面用に PFV から `is_pinned` / `movability` を引く処理（`beIsPinned` の予備処理を含む） | `fixed_visit_id`・`master_start_time` は**型とのズレ表示（青で使う）用なので残す** |
| 1577-1595 | — | 週タイムライン用の同じ処理（`is_pinned`・`movability: pfvHit?.movability…`） | 同上 |
| 2547-2554 | **確認文**: プールへ戻すとき「⚠ これは完全固定です。\n○○ をプールに戻しますか？\n(固定枠は保持されます)」 | `poolVisits.some(is_pinned)` の分岐ごと | 赤が無くなるとプールへ戻すときに確認が出なくなる。**ほかの確認があるか要確認**（§7-6） |
| 3178-3182 | **確認文**: 削除のとき `lockedNote = '\n⚠ これは完全固定です。'` | `lockedNote` | 削除の確認文は残す。挿入している箇所だけ消す |
| 3248-3334 | toast「完全固定にしました (システムは動かしません)」「完全固定を外しました」「完全固定の更新に失敗: …」「既に全曜日 完全固定の状態です」「全曜日 N 件を完全固定にしました…」など | `handleTogglePin` をまるごと | — |
| 5654-5657 | — | 日リストへ渡す `is_pinned: cv.is_pinned === true`（`master_start_time` のコメント「赤トグルが押せない理由…」も） | `master_start_time` は青の型ズレ表示で使うので残し、コメントだけ直す |
| 6202-6219 | 中段「今週の配置: [青ピン一括]」（コメント「赤の一括…は統合により廃止」） | コメントだけ | 表示は青のみで変わらない |
| 7154 | 週一覧へ `onTogglePin={canEdit ? handleTogglePin : undefined}` | prop | — |
| 7262-7263 | 日タイムラインへ `onTogglePin=…`（コメント「型のピン (赤): カード右下クラスタ…」） | prop とコメント | — |
| 7276-7277 | 日リストへ `onTogglePin=…`（コメント「週のピン (青): 赤トグルの右隣に並ぶ」） | prop。**青のコメントを直す** | — |
| 7723-7724 | 移動ダイアログへ `lockedNotice: tlMoveState.visits.some(is_pinned)` | プロパティ | — |

関連する盤面の部品:
- `components/schedule/v2/courseGrid.ts:19, 202-221` — `CourseGridVisit.is_pinned` / `movability`（型・コメント「ピン留めのさらに先」）→ 消す。
- `components/schedule/v2/CourseWeekOverview.tsx`（**コース別の週一覧**）
  - 29, 40: `Movability` と `PinScopeMenu` の import
  - 70-77: 型の `is_pinned` / `movability`
  - 181-186, 302: `onTogglePin` の prop
  - 627-680: 行の組み立てに入っている `isPinned: v.is_pinned === true`（`fixedVisitId`・`masterStartTime` は PinIconButton 専用。**ほかで使っていなければ一緒に消せる**）
  - 959-969 / 1085-1095: 名前の右にある**赤トグル `PinIconButton`**（単独行とペア行の 2 か所）
  - 1140-1223: `PinIconButton` 本体（disabled の理由「固定訪問スケジュールは HH:MM です。この時間帯では完全固定にできません」「先に固定枠登録が必要」、`text-yellow-700`、PushPin／PushPinOff）
  - **波及**: 青は 951-958 / 1078-1084 の「今週固定」アイコン（表示だけ）で、赤トグルの**左隣**にある。赤が消えると青が名前の右端になるだけで、ずれは起きない。
- `components/schedule/v2/BulkWeekPinAllButton.tsx:5-15` — コメントに「旧・赤ピン一括ボタンと対」「赤: 全件ピン留め…」とある。**コメントだけ直す**（青なので残す）。
- `components/schedule/v2/ResetToFixedButton.tsx:17, 87, 129` — 「固定枠戻」ボタンに lucide の `Pin` アイコンを使っている。赤ピンとは**無関係**だが、見た目は画鋲。赤を消したあと「ピン＝青だけ」にそろえるなら、アイコンを替えるか検討（§7-9）。

### 2-3 日タイムライン（`components/schedule/timeline/TimelineDayBoard.tsx`）

| 行 | 画面に出るもの | 消すもの | 波及 |
|---|---|---|---|
| 39 | — | import から `CornerPushPin` を外す（`CornerWeekPushPin`・`PushPin`・`PushPinOff` は青で使うので残す） | — |
| 40-41 | — | `PinScopeMenu` / `MovabilityMark` の import | — |
| 44-47, 300 | — | `masterDivergenceTitle` を赤ボタンの無効理由に使っている箇所 | 779 行目（時刻行の title）でも使っているので関数は残す |
| 214-216, 289, 294, 415-417, 524, 540-542, 861, 873-875, 897, 925-927, 1150-1152, 1344-1346, 1394-1396, 1687, 1702-1704, 1838, 1850, 1875, 2145 | — | `onTogglePin` の prop の宣言と受け渡し（約 20 か所） | — |
| 277-365 | **右下の操作群 `CardPinControls` の赤トグル**（PinScopeMenu の▾付き、title「完全固定にする（システムはこの枠を動かしません）— スコープを選択 (この曜日のみ / 全曜日)」「完全固定を外す…」、無効時は「先に固定枠登録が必要」か型ズレの理由、`tl-pin-toggle-${id}`） | 296-365 の赤のまとまり、`isPinned`／`redDisabled`／`redDisabledReason` | **青とのからみ（§5-B）**: 操作群は `absolute bottom-0.5 right-[19px] flex gap-0.5`。赤が消えると、青トグルが × のすぐ左へ移る（flex なので自然に詰まる）。コメント 277-283 を書き直す。関数名・testid `tl-pin-controls-*` は青専用になる |
| 700-713 | 同行バッジ 👥 の右位置（`is_pinned && isWeekPinned ? right-[34px] : (is_pinned \|\| isWeekPinned) ? right-[16px] : right-0.5`） | 赤の項を外して `isWeekPinned ? right-[16px] : right-0.5` にする | **青とのからみ** |
| 719-726 | **右上の赤い画鋲 `CornerPushPin`**。青は `visit.is_pinned ? 'right-[14px] translate-x-0'` で赤の左へずらしている | 赤い画鋲。青の className の分岐を外す（青を既定の位置＝角にする） | **青とのからみ（§5-A）** |
| 735 | 患者名の前の **「固」「時」「曜」マーク**（`MovabilityMark`、`tl-movability-${id}`） | 呼び出し | 名前が 1 文字ぶん左へ寄る |
| 829-838 | 操作群を描く条件 `(onTogglePin \|\| onToggleWeekPin)` | `onTogglePin` を外す | — |
| 839-840（コメント） | 「右上は CornerPushPin が刺さっているため右下に置く」 | コメント | 「右上は画鋲（青）」に直す |
| 876-879, 928-930, 1349 | — | コメント「赤 (完全固定) はドラッグ可 — …注意書き」 | ドラッグの可否は青と取消・グループだけで決まっていて、赤は条件に入っていない。**処理の変更は不要** |
| 979 | ペアボックス（2 名セット）の各行に赤い画鋲 | 消す | ペアボックスには**青の画鋲が元から無い**（§5-D） |
| 1034 | ドラッグ中の影 `TlVisitDragGhost` の赤い画鋲 | 消す | 影に青の画鋲は無い（§5-D） |
| 1219-1233 | ペアの各行: 同行バッジの位置 `v.is_pinned ? right-[16px] : right-0.5`、赤い画鋲、`MovabilityMark` | 3 つとも消す | ペアの各行にも**青の画鋲は無い**。バッジ位置の分岐が要らなくなる |
| 1272 | ペアの各行: 1 行目の右余白 `v.is_pinned ? pr-[64px] : pr-[50px]` | `pr-[50px]` に固定する | — |

関連:
- `components/schedule/timeline/MovabilityMark.tsx`（**ファイルごと消す**）。「固」= `'可動域: 完全固定（提案も自動割当も動かしません）'`、「時」= `'可動域: 同じ曜日内での時刻変更は可'`、「曜」= `'可動域: 曜日の変更も可'`。`is_pinned` のときは出さない。
- `components/schedule/timeline/masterDivergence.ts:7, 42, 44-50` — `masterDivergenceTitle()` の文が「固定訪問スケジュールは HH:MM です。**この時間帯では完全固定にできません**」。カードの時刻行の title としても出ているので、**関数は残して文を変える**（例: 「固定訪問スケジュールは HH:MM です」で止める）。コメント 7 行目の「赤ピン (型のピン) が刺せない」も直す。
- `components/schedule/timeline/TimelineMoveDialog.tsx:41-44, 107-114` — `lockedNotice` の赤い枠「⚠ これは完全固定です。システムは動かしませんが、この操作では手動で移動します。」（`timeline-move-locked-notice`）→ 消す。

### 2-4 週タイムライン（`components/schedule/timeline/WeekTimelineBoard.tsx`）

| 行 | 画面に出るもの | 消すもの | 波及 |
|---|---|---|---|
| 23 | — | import から `CornerPushPin` を外す（`CornerWeekPushPin` は残す） | — |
| 24, 313-314 | 「固」「時」「曜」マーク（`wtl-movability-*`） | 消す | — |
| 285-296 | 同行バッジの位置 `v.is_pinned ? right-[15px] : right-0.5` | **青で置き換える**（`week_pinned \|\| source==='manual_week'` のときに `right-[15px]`） | **今もある不具合**: 青だけ刺さったカードではバッジが青い画鋲に重なる。赤を消すときに一緒に直すべき（§5-C） |
| 299-305 | 右上の赤い画鋲と、青の `v.is_pinned && 'right-[11px] translate-x-0'` | 赤い画鋲と青の分岐 | §5-A |
| 519-531 | ペアの各行: バッジ位置の分岐・赤い画鋲 | 消す | ペアの各行には青の画鋲が無い |
| 537 | `v.is_pinned ? pr-[64px] : pr-[50px]` | `pr-[50px]` に固定 | — |

### 2-5 日リスト（`components/schedule/timeline/TimelineDayList.tsx`）

| 行 | 画面に出るもの | 消すもの | 波及 |
|---|---|---|---|
| 20 | — | `PinToggleButton` の import | — |
| 21 | — | `CornerPushPin` の import（`PushPin`・`PushPinOff` は青トグルで使う） | — |
| 22, 39, 86, 94, 431, 531, 547 | — | `PinScope` 型と `onTogglePin` の受け渡し | — |
| 133-140 | 赤の行を**琥珀色の背景**（`bg-amber-50/60`）にし、性別色の背景を外す | 消す | 赤の行も性別色の背景に戻る |
| 145-146 | 行の右上の赤い画鋲 | 消す | リストの行には**青の画鋲が元から無い**（青はトグルボタンで状態を出す） |
| 196-200 | `[赤▾]` の `PinToggleButton` | 消す | **§5-B**: `[赤▾][青][×]` が `[青][×]` になる。名前の列の中の inline-flex なので左へ詰まる |
| 201-202, 216, 618-621（コメント） | 「赤トグルの右隣」「ピン留めトグルの隣」「赤 (PinToggleButton) の右隣に並ぶ。見分け: 赤=型のピン…」 | コメントを直す | — |

### 2-6 共有の行・カード部品（`components/schedule/WeekdayScheduleCard.tsx`）

本番で使っているのは**提案ダイアログ**（`BulkPoolInsertDialog` の変更前／変更後の表示）と、TimelineDayList が借りている `PinToggleButton`。BulkPoolInsertDialog の `toCourseListItems` は `is_pinned` を入れていないので、**提案ダイアログで赤は実際には出ていない**（使われていない経路）。

| 行 | 中身 | 消すもの |
|---|---|---|
| 32 | import の `CornerPushPin`・`PushPin`・`PushPinOff` | 赤専用なので全部外せる（このファイルに青は無い） |
| 36 | `PinScopeMenu` の import | 消す |
| 86-87 | `VisitListItem.is_pinned` | 消す（TimelineDayList の行の型もこれを共有している） |
| 196-201, 312, 342, 358, 368, 437, 455, 503, 513, 544, 560, 573, 613, 628, 631, 664, 710, 718 | `onTogglePin` の受け渡し | 消す |
| 577-603 / 634-659 | 赤の行を `bg-yellow-50/60` にする・`data-pinned`・赤い画鋲 | 消す |
| 802-803 | 行内の `PinToggleButton` | 消す |
| 815-893 | `PinToggleButton` 本体（文言「固定訪問スケジュールは HH:MM です。この時間帯では完全固定にできません」「完全固定にするスコープを選択 (この曜日のみ / 全曜日)」など） | まるごと消す |

### 2-7 PinScopeMenu（`components/schedule/v2/PinScopeMenu.tsx`）— **ファイルごと消す**

メニューの文言: 「この曜日のみ 完全固定 / 完全固定を解除」「この患者の全曜日 完全固定 / 完全固定を解除」、`PushPin` を `text-red-600` で描画。使っている所は TimelineDayBoard、CourseWeekOverview、WeekdayScheduleCard、TimelineDayList（型だけ）、CourseDayTablePanel（型だけ）。

### 2-8 改善提案（PatientScheduleDetailDialog の中の ImprovementSuggestionsSection）・見送り（DismissReasonDialog）

| ファイル:行 | 画面に出るもの | 対応 |
|---|---|---|
| `v2/ImprovementSuggestionsSection.tsx:13`（コメント）, 86-95 | 0 件のときの内訳「**完全固定N件**」（`fs.pinned + fs.locked`）。ほかに「曜日変更が未許可N件」（`day_restricted`） | 「完全固定」を消す。`day_restricted` は**可動域の day_flexible と関係しているかもしれない** → BE を確認（§7-5） |
| `v2/ImprovementSuggestionCard.tsx:9-10, 314-321, 329-335, 415-422` | 要確認バッジ「**可動域未設定・患者様への確認推奨**」「○○ 様の**可動域未設定**」（`requires_patient_confirmation`） | 文言を変える（可動域という言葉が無くなるため）。例: 「ご希望の範囲外・患者様への確認推奨」。**BE の判定の根拠（movability=unknown）も変わる**（§7-4） |
| `v2/DismissReasonDialog.tsx:4-17, 45-58, 104, 120-122, 139, 141-145, 181-189` | 見送りの理由「この曜日は動かせない／この時刻は動かせない」→ 確認の段「**可動域として記録しますか？**」「○曜の枠を「**曜日固定／完全固定**」として記録すると、今後この種の提案は表示されません」 | 記録する先（movability）が無くなる。**確認の段ごと消すか、「希望訪問スケジュールの範囲を狭める」に置き換えるか**（§7-1）。理由の選択肢 4 つは残してよい |
| `lib/queries/improvementSuggestions.ts:104-111` | `promote_movability` のときに PFV のキャッシュを無効化している | 確認の段を消すなら一緒に消す |
| `components/schedule/v2/PatientScheduleDetailDialog.tsx` | 赤の直接の表示は**無い**（`TimelineRowMeta` の型を import しているだけ） | — |
| `components/schedule/v2/CourseMoveTimeline.tsx:17, 22, 48-49, 160-161` | 前後比較のタイムライン（改善提案などで使う）の行の右上に赤い画鋲（`row.pinned`）。**ただし本番では `pinned` をどこも入れていない**（`CourseDayTablePanel.tsx:627-640` の `patientRowMetaById` は sex／address／condLabel だけ） | 型の `pinned` と画鋲を消す（テストだけが使っている） |

### 2-9 範囲最適化（ScopeOptimizeDialog）

| 行 | 画面に出るもの | 対応 |
|---|---|---|
| `v2/ScopeOptimizeDialog.tsx:128-132` | 対象外の内訳「**完全固定N件**」（`ex.pinned + ex.locked`） | 消す |
| `v2/ScopeOptimizeDialog.tsx:529` | 説明文「…手順として提案します。**ピン留め・完全固定・**却下済みの枠は動かしません。」 | → 「青ピン（今週固定）・却下済みの枠は動かしません」など。**青をここで除外しているかは BE で確認**（§7-7） |
| `lib/schemas/v2/scopeOptimization.ts:75-76, 171-172` | スキーマの `pinned` / `locked` | BE が出さなくなったら消す。寛容なパースのままなら残しても壊れない |

### 2-10 詰まり解消・プール投入（PoolCandidateList / unblock）

| 行 | 画面に出るもの | 対応 |
|---|---|---|
| `v2/PoolCandidateList.tsx:192-200` | 「動かせない事情:」の内訳の訳語 `pinned: 'ピン留め'`、`locked: '固定（可動域）'` | 消す（BE の `pinned` は scope_optimizer と同じく `pfv.is_pinned`＝赤のこと） |
| `v2/PoolCandidateList.tsx:536-556` | `UnmovableSummaryList`（0 件のキーは出さない） | 訳語の表を減らすだけ |
| `lib/schemas/v2/unblock.ts:152-170` | `unblockUnmovableSummarySchema.pinned/locked` | BE と合わせて消す。`.catch(0)` があるので残しても壊れない |
| `v2/BulkPoolInsertDialog.tsx` | WeekdayScheduleCard を借りているが is_pinned は流していない → 表示は変わらない | 対応なし |
| `lib/scheduling/addVisitPlan.ts:52` | 「青ピン。true の訪問は動かせない」 | 青なので対象外 |

### 2-11 提案の採用・どこでも＋訪問（保存処理だけで、画面には出ない）

| ファイル:行 | 中身 | 対応 |
|---|---|---|
| `v2/_proposeSlotUtils.ts:130`（コメント）, 235-239 | `existingFixedVisitToItem` が `is_pinned` / `movability` を運ぶ（落とすと保存のたびに unknown に戻る、という注記つき） | 消す。**BE が「送られなかったら保持」か「既定値で上書き」かで要否が変わる**（§7-3） |
| `lib/scheduling/addVisitExecutor.ts:194-205, 276-277` | `buildPatternItems` が置き換える枠の movability を受け継ぎ、`is_pinned = movability==='locked'`（コメント「赤ピン = movability 'locked' の枠を黙って自由枠に落とさない・M2」） | 消す |
| `v2/cockpit/AddVisitAnywhereDialog.tsx:178-179, 565, 668` | 「この予定は動かせません（当日以前/固定/予定外）」の「固定」は**青**のこと（H1） | 対象外。赤を消したあと「固定」が青を指すことを明記するなら「青ピン」と書く（任意） |

### 2-12 職員スケジュール（コックピット）

- `v2/cockpit/VisitActionMenu.tsx:61`、`v2/StaffWeekBoard.tsx`、`v2/cockpit/StaffTimelineView.tsx` — **赤ピンは無い**（`week_pinned` だけ）。職員の入れ替えの「青ピン（今週だけ固定）の訪問が N 件…」（CourseDayTablePanel 4903, 5001-5107）も青なので対象外。

### 2-13 説明・ヘルプ・管理画面

| ファイル:行 | 文言 | 対応 |
|---|---|---|
| `v2/WeeklyRitualGuideDialog.tsx:101` | FAQ「手動で動かした訪問は消える？ → 「**ピン留め**」した訪問は保護されます。ピン留めなしの訪問は型から再生成されます。」 | 今の青（今週固定）の挙動に合わせて書き直す（赤の保護は無くなる） |
| `app/(app)/admin/feature-flags/page.tsx:58` | 機能フラグ `g21_new_algorithm` の説明「**完全固定** / 同住所紐付け / 拠点間移動コスト を考慮した…」 | 「完全固定 /」を消す |
| `components/ui/push-pin.tsx:6, 58-107（CornerPushPin・aria-label="完全固定"）, 109-120（CornerWeekPushPin のコメント「赤い画鋲との対比」「併存しても重ならないよう…」）` | アイコン部品 | §5-E |
| `lib/api/errorMessage.ts:8, 15`（コメント） | 「ピン留めされた枠を変更・削除しよう」「PFV の pinned 保護」 | コメントだけ（例の文を差し替え） |
| `v2/ConstraintOverrideConfirmDialog.tsx:20`（コメント） | 「ピンモデルの『エンジンだけ縛り、人手は自由』」 | コメントだけ |
| モバイル（`app/(mobile)`, `components/mobile`）、現場ボード（`app/(field)`, `components/field`）、訪問モニター、オンボーディング、`frontend/README.md`、`docs/manuals/`（frontend の外） | 赤ピンの文言・アイコンは**無い** | 対象外（docs/manuals は今回の範囲外だが、別途 grep を推奨） |

---

## 3) 型・スキーマ・API クライアント

| ファイル:行 | 中身 | 対応 |
|---|---|---|
| `lib/schemas/v2/patient_fixed_visit.ts:33-38` | `MOVABILITY_VALUES = ['unknown','time_flexible','day_flexible','locked']` / `type Movability` | 消す（使っている所: PatientFixedVisitsPanel、MovabilityMark、courseGrid、CourseWeekOverview、CourseDayTablePanel） |
| `lib/schemas/v2/patient_fixed_visit.ts:69-85` | `is_pinned: z.boolean().optional()`、`movability: z.enum(...).optional()` | 消す。optional なので BE が送り続けても zod の object は余分な項目を捨てるだけで壊れない |
| `lib/schemas/g21.ts:5-6, 18-40` | `PATCH /patients/fixed-visits/{id}/pin` と `POST …/pin/bulk` 用のスキーマ（`pfvPinBulkRequestSchema` と重複 id のガード） | pin の部分を消す（同じファイルの feature flag と同住所のスキーマは残す） |
| `lib/queries/g21.ts:4, 75-130` | `useTogglePfvPin`（PATCH /pin）/ `useBulkPinPfvs`（POST /pin/bulk） | 消す |
| `lib/schemas/visit.ts:84-92` | `VisitRead.is_pinned: z.boolean().nullable().optional()`（「BE が将来出すかも」の予備） | 消す（CourseDayTablePanel 1301, 1579 の `beIsPinned` と一緒に） |
| `components/schedule/v2/courseGrid.ts:202-221` | `CourseGridVisit.is_pinned` / `movability` | 消す |
| `components/schedule/v2/CourseWeekOverview.tsx:70-77` | `WeekOverviewVisit.is_pinned` / `movability` | 消す |
| `components/schedule/WeekdayScheduleCard.tsx:86-87` | `VisitListItem.is_pinned` | 消す |
| `components/schedule/v2/CourseMoveTimeline.tsx:48-49` | `TimelineRowMeta.pinned` | 消す |
| `components/schedule/timeline/MovabilityMark.tsx:26-29` | `MovabilityMarkSource` | ファイルごと消す |
| `lib/schemas/v2/improvementSuggestion.ts:162-171, 190-197` | `filtered_summary.pinned/locked` | BE と合わせる |
| `lib/schemas/v2/improvementSuggestion.ts:205-222` | `promote_movability` / `movability_updated` / `new_movability` | §7-1 の結論しだい |
| `lib/schemas/v2/scopeOptimization.ts:75-76` | `excluded_summary.pinned/locked` | BE と合わせる |
| `lib/schemas/v2/unblock.ts:153-166` | `unmovable_summary.pinned/locked` | BE と合わせる |
| `lib/queries/visit_week_pin.ts:3, 5` | 青のフック。コメントに「仕様: pin-and-movability-spec.md」「赤ピン (`useTogglePfvPin`) との違い」 | 青なので残す。コメントだけ直す |
| `lib/queries/copy_week.ts:102` `keep_pinned` | BE は `v.week_pinned` → 青 | 対象外 |

---

## 4) テスト一覧（vitest。e2e は 0 件）

### 4-A 赤ピンそのものを検証しているもの（消すか書き換える）

| ファイル | テスト名（行） | 対応 |
|---|---|---|
| `app/(app)/patients/_components/__tests__/PatientFixedVisitsPanel.test.tsx` | describe「完全固定 (統合)」: LK-1 曜日 ON で「完全固定」チェックボックスが表示される(1259) / LK-2 チェック ON で movability=locked + is_pinned ミラーが payload に乗る(1269) / LK-3 サーバーの locked 行はチェック済みで表示され、注意書きが出る(1298) / LK-4 完全固定の行でも時刻・所要・コース・訪問有無は編集できる(1313) / LK-5 保存済み locked 行のチェック OFF → PUT で movability=unknown / is_pinned=false(1343) / LK-6 週一括「全曜日を完全固定」(1381) / LK-7 週一括「全曜日の完全固定を解除」(1435) / LK-8 旧 4 段階の値が入っている行は「旧設定」表示され、チェックは OFF 扱い(1470) / LK-9 patientFixedVisitV2BaseSchema が movability を受理する(1498) | describe ごと消す。代わりに「チェックも帯も出ない」「payload に movability／is_pinned が無い」ことを確かめるテストを 1〜2 本置くとよい |
| 同上 | 47 行目付近の fixture（`is_pinned`/`movability` 4 か所）、BT-3(1164)・BT-4(1191)・BT-5(1235)、E-5(1706) の fixture／期待値 | fixture から項目を外す |
| `app/(app)/patients/_components/__tests__/weeklyPatternToDayRows.test.ts` | 「C3: current の is_pinned=true は merge 後も保持される」(62)、ほか 3 本の fixture(58, 90, 127) | C3 を消す。ほかは fixture を直す |
| `components/schedule/v2/__tests__/PinScopeMenu.test.tsx` | 全 8 本（1〜8: 「trigger click で popover が開き 2 つの menu item…」「isPinned=true → 表記が「完全固定を解除」系…」など） | ファイルごと消す |
| `components/schedule/__tests__/PinToggleButton-master-rule.test.tsx` | describe「盤面のピン留め: 型と一致する訪問にのみ可能」: 型と一致している訪問はピン留めできる(30) / 型とズレている訪問はピン留めできず、型の時刻を理由として示す(41) / そもそも固定枠が無い訪問は従来どおりの案内にする(57) / ピン留め済みの訪問は解除操作ができる(69) | ファイルごと消す |
| `components/schedule/timeline/__tests__/movabilityMark.test.ts` | movabilityMarkFor: 完全固定 (locked) は「固」を出す(13) / time_flexible は「時」、day_flexible は「曜」(20) / 未設定 (unknown) は出さない(26) / PFV 非紐付け…(30) / ピン留め済みは出さない(35) / 未知の値でも壊れない(41) | ファイルごと消す |
| `components/schedule/timeline/__tests__/masterDivergence.test.ts` | 「説明文 > 完全固定にできない理由まで含めて伝える」(54) | 期待する文を新しい文言に変える |
| `components/schedule/timeline/__tests__/TimelineDayBoard.test.tsx` | 「TlVisitDragGhost はカードと同じ視覚言語…」(345: `is_pinned: true` と CornerPushPin の検証) / 「同住所ペアボックスは dndEnabled で2名セットのまま掴める (青ピン含みは不可)」(437 のコメント) / 「dndEnabled のとき単独カードは draggable・青ピン/ペアは掴めない (**赤=完全固定は掴める**)」(480, 499, 518) | 影の赤い画鋲の検証を消す。480 はテスト名と赤のケースを消し、青とペアのケースは残す |
| `components/schedule/v2/__tests__/DismissReasonDialog.test.tsx` | 「1+2. day_immovable → 昇格確認 → はい で promote_movability=true POST」(85) / 「3. … いいえ で promote_movability=false POST」(110) / 「4. staff_relation は昇格ステップを挟まず即 POST (promote=false)」(135) | §7-1 しだい。確認の段を消すなら 1〜3 を消し、4 を「全理由で即 POST」にする |
| `components/schedule/v2/__tests__/ImprovementSuggestionsSection.test.tsx` | 「2. requires_patient_confirmation=true で要確認バッジ表示」(222) / 「4b. day_change 採用 → …」(335: movability／is_pinned の運搬 6 か所) / 「5. 0 件 + filtered_summary で非表示内訳…」(368) / 「**5b. 統合: pinned と locked は合算して「完全固定N件」と表示**」(382) / 「9. swap カード: Y 側の要確認バッジ…」(456) / 「11. swap 見送り → … day_immovable でも昇格確認を出さず即 dismiss」(546) / 「12. 未知 kind…」(695 の fixture) | 5b を消す。2・9 は文言を変える。4b・11 は運搬と昇格を直す |
| `components/schedule/v2/__tests__/CourseMoveTimeline.test.tsx` | 「BeforeAfterCourseTimeline: before/after 2 枚 + ピン/条件が描画される」(140-156: `pinned: true` → `getAllByLabelText('完全固定')`) | ピンの検証を消し、テスト名を変える |
| `components/schedule/v2/__tests__/_proposeSlotUtils.test.ts` | 「非採用曜日の movability がマージを通して保持される (§1.3 レビューLOW)」(135) / describe「existingFixedVisitToItem (P2-A movability 運搬)」: movability を運搬する(152) / locked / unknown も運搬する(170) | 消す |
| `lib/scheduling/__tests__/addVisitExecutor.test.ts` | 79 行目付近の fixture / 「executeAddVisitPlan — scope > 既存行を読んで同曜日 slot 0 を置換し pattern_and_week で PUT する」(414: 期待値に is_pinned／movability が 9 か所) / buildPatternItems「該当曜日 slot が無ければ追加する」(553) / 「**M2: 置き換える枠の movability を引き継ぐ (is_pinned はそのミラー)**」(560) / 「**M2: 元が自由枠なら自由枠のまま (勝手にピンを立てない)**」(581) | M2 の 2 本を消し、ほかは期待値を直す |
| `lib/schemas/v2/__tests__/patient_fixed_visit.test.ts` | 「P2-A: movability 省略時は undefined…」(53) / 「P2-A: movability の 4 値を許容し、不正値はエラー」(62) | 消す |
| `lib/schemas/__tests__/g21.test.ts` | describe「L2: pfvPinBulkRequestSchema 重複 pfv_id ガード」: 全て uniq な pfv_id なら parse 成功(108) / 同一 pfv_id を 2 件含むと 422(117) / 空配列は parse 成功(132) | describe ごと消す（C1・L1・M3 は残す） |
| `lib/schemas/v2/__tests__/improvementSuggestion.test.ts` | 「契約 1:1: 完全なレスポンスをパースする」(54) / 「default: filtered_summary / suggestions が欠けても既定値で埋める」(69) / 「improvementDismissResponseSchema > movability 昇格レスポンスをパースする」(267) / 「default: movability 未更新 (null) をパースする」(277) | §7-1・BE の契約に合わせる |
| `lib/schemas/v2/__tests__/scopeOptimization.test.ts` | 「BE 契約どおりのレスポンスをパースできる」(102) / 「excluded_summary 省略 (旧 BE) でも既定値で通す」(117) | `pinned`/`locked` を消すなら直す |
| `components/schedule/v2/__tests__/ScopeOptimizeDialog.test.tsx` | 192 行目付近の fixture `excluded_summary.pinned/locked` | 同上 |
| `components/schedule/v2/__tests__/PoolCandidateList.test.tsx` | 「W-12d … plans 0 件: unmovable_summary を「動かせない事情」として表示する」(1360: pinned／locked の訳語の検証) | 期待値を直す（ほかのヒットは「excluded_summary」という名前が一致しただけで、赤とは無関係） |
| `lib/api/__tests__/errorMessage.test.ts` | 「構造化 detail の message + violations を改行区切りで返す」(19: 例の文が「ピン留め」) | 動きは変わらない。例の文を差し替えるかは任意 |
| `components/ui/__tests__/push-pin.test.tsx` | 「PushPin は赤い丸頭 (fill-error) を持つ svg を描画する」(15) | §5-E の結論しだい（PushPin は青も使っている） |

### 4-B 青ピンのテストに赤が混ざっているもの（**消さずに分ける**）

`components/schedule/timeline/__tests__/TimelineDayBoard-week-pin.test.tsx`（describe「週のピン (青ピン)」）
- 残す（青だけ）: 「未固定の訪問はトグルが「未ピン」状態で出る」(59) / 「今週固定済みの訪問は青い画鋲が立ち、トグルで解除できる」(77) / 「week_pinned フラグだけでも青ピンが立つ — 取込 (import) の訪問を固定した状態」(89) / 「ハンドラ未指定 (閲覧のみ) ではトグルを描画しない」(146)
- 名前を変える: 「型とズレた訪問 (source=auto) でも今週固定できる — **赤ピンとの決定的な違い**」(64)
- 消す: 「**赤ピン中でも青ピンのトグルは出る**（先に青を仕込んでから一括赤解除する運用のため）」(93) / 「**赤と青が両方刺さっていれば画鋲が 2 本並ぶ（青は赤の左隣）**」(104) / 「**赤トグルは型と一致する訪問で操作でき、ズレていれば理由を示して無効**」(113) / 「**赤トグル: 型とズレた訪問は無効で、型の時刻を理由に出す（論点1）**」(125)
- 足すとよい: 「青い画鋲は角（既定の位置）に刺さる」「操作群は `[青][×]` だけ」

`components/schedule/v2/__tests__/BulkWeekPinAllButton.test.tsx:4`（ファイル冒頭のコメント「赤の一括は…廃止済み」だけ）→ コメントを直す。

### 4-C g21 フックをモックしているだけのもの（`useTogglePfvPin`／`useBulkPinPfvs` を消したら `vi.mock` も消す）

`components/schedule/v2/__tests__/` の次の 10 本: `CourseDayTablePanel.test.tsx`(228-234) / `-cockpit`(368-370) / `-g44`(254) / `-generate-week-confirm`(197) / `-header-collapse`(233) / `-ng-constraint`(177) / `-pfv-presence`(171) / `-u3`(303) / `-w37-p3c`(268) / `-w38`(241-245) / `-w39`(251)。
※ g21 の feature flag フックなどもモックしているなら、モックは残して pin の 2 行だけ消す。

`components/schedule/__tests__/WeekdayScheduleCard-g55-freegaps.test.tsx:31-32` — `vi.mock('../v2/PinScopeMenu')` → 消す（ファイルが無くなると mock の解決に失敗するため**必須**）。

### 4-D 赤ピンと無関係（「赤」「固定」「動かせない」の語が一致しただけ）

`app/(field)/m/__tests__/approve-placement.test.tsx`・`placement-sheet.test.tsx`・`suggest-new-karte.test.tsx`、`lib/field/__tests__/placement.test.ts`（赤警告＝定員超過）、`app/(mobile)/m/this-week/__tests__/page.status-cancel.test.tsx`（赤い取消バッジ）、`app/(mobile)/m/record/new/__tests__/page.test.tsx`、`components/mobile/__tests__/TimeWheel.test.tsx`、`TimelineDayList.test.tsx:160`（移動警告の赤背景）、`WeekTimelineBoard.accompaniment.test.tsx:76`（重複の赤枠）、`AssignWarningDialog.test.tsx`（性別カードの赤）、`AddVisitAnywhereDialog.test.tsx`（「動かせない」＝青と H1）、`StaffTimelineView.test.tsx`、`addVisitPlan.test.ts`、`MakeWeekDialog.test.tsx`（`keep_pinned`＝青）。

---

## 5) 青ピンとの絡み（赤を消すと青の配置や文言が変わる所）

**A. 右上の画鋲（状態表示）— 青は「赤の左隣」へずらす指定**
- `TimelineDayBoard.tsx:719-726`: `<CornerWeekPushPin className={visit.is_pinned ? 'right-[14px] translate-x-0' : undefined} />`
- `WeekTimelineBoard.tsx:299-305`: `cn('h-4 w-4', v.is_pinned && 'right-[11px] translate-x-0')`
- 対応: 分岐を消し、青を常に既定の位置（`right-0 translate-x-[36%] -translate-y-[29%]`＝カードの角からはみ出す位置）にする。**今までの「赤と青が両方刺さった状態」は、見た目では青が 14px（週は 11px）左へずれていた**ので、撤去後は青がすべて角にそろう。
- `push-pin.tsx:109-120` の CornerWeekPushPin のコメント（「赤い画鋲との対比で色分け」「併存しても重ならないよう…」）を書き直す。

**B. 右下の操作群・日リストの並び**
- 日タイムライン `CardPinControls`（`TimelineDayBoard.tsx:277-365`）: `[赤▾][青]` が × の左隣（`right-[19px]`）。赤が消えると `[青][×]` になる。flex なので**位置の数値は直さなくてよい**。ただし関数名とコメントが赤と青の前提なので、`CardWeekPinControl` などへ名前を変えるとよい（testid の `tl-pin-controls-*` を変えると week-pin テストの直しが必要）。
- 日リスト `TimelineDayList.tsx:196-216`: `[赤▾][青][×]` → `[青][×]`。コメント 201-202, 618-621 は「赤の右隣」と書いてある。
- `CourseDayTablePanel.tsx:7276-7277` のコメント「週のピン (青): 赤トグルの右隣に並ぶ」。

**C. 同行バッジ 👥 の逃がし（右上の画鋲を避ける位置）**
- 日タイムライン 700-713: 赤と青の本数で `right-[34px]/[16px]/0.5` を出し分けている → `isWeekPinned ? right-[16px] : right-0.5` にする。
- 週タイムライン 285-296 / 519-529: **赤だけを見ている**（`v.is_pinned ? right-[15px] : right-0.5`）。青だけ刺さったカードではバッジが青い画鋲に重なる、という**今もある不具合**がある。撤去のときに青の条件（`week_pinned || source==='manual_week'`）へ置き換えること。
- 1 行目の右余白（`pr-[64px]` / `pr-[50px]`）は赤だけを見ている（日 1272、週 537）。ペアの行には青の画鋲が無いので `pr-[50px]` に固定してよい。

**D. 青の画鋲が元から出ていない所（赤だけが出ていた所）**
ペアボックスの各行（日 979）、ペアの各行（日 1232、週 531）、ドラッグ中の影（日 1034）、日リストの行（145-146）、`CourseMoveTimeline`、`WeekdayScheduleCard`。赤を消すと、これらの場所では**画鋲が 1 本も出なくなる**。青をここに出すかは PO の判断（§7-8）。

**E. アイコン部品 `components/ui/push-pin.tsx`**
- `PushPin` は既定で**赤い頭（`fill-error`）**。青は呼ぶ側で `[&_.fill-error]:fill-[var(--info)]` を付けて上書きしている（TimelineDayBoard 390、TimelineDayList 659、CourseWeekOverview 953/1079、BulkWeekPinAllButton 132 の `bluePinCls`）。
- 対応案: (a) `PushPin` は残し、青の上書きはそのまま、または (b) `PushPin` の既定を青にして呼ぶ側の上書きを外す。(b) のほうが「赤が残る」漏れを防げるが、`push-pin.test.tsx`「PushPin は赤い丸頭 (fill-error)…」を直す必要がある。
- `CornerPushPin`（aria-label="完全固定"）は赤専用なので消す。`PushPinOff` は青の未ピン表示で使うので残す。

**F. テストの共有**: §4-B（`TimelineDayBoard-week-pin.test.tsx` の 4 本が赤）。

**G. 文言の共有**: `masterDivergenceTitle()` は赤ボタンの無効理由と、カード時刻行の title（青の文脈でも出る）の両方で使っている → 関数は残し、「この時間帯では完全固定にできません」の部分だけ消す。

---

## 6) 無関係と判定したもの（語が一致しただけ）

- 「固定」: 固定イベント（朝会・`source='fixed'`）、`time_type === '固定'`（希望時刻の種類。PatientCard、FieldSheets、addVisitPlan、FixedTimeEditModal、schemas/patient.ts）、「反映先を固定して開く」、固定 QR、「JST 固定」、固定レート、`pattern_and_week 固定`、`FixedEventRow`「全員（固定）」、「固定枠」「固定訪問スケジュール」（型そのもの。残す）、特別訪問週間の「固定退避」「固定どおりに戻す」「固定 N ＋ プール待ち M」。
- 「赤」: 赤警告（現場ボードの定員超過）、性別の赤、移動警告の赤背景、重複の赤枠、取消バッジ、モニターの赤ハッチ、カイポケの赤いボタン／「未」。
- 「ピン」: ピンク（ブランド色）、ピンチズーム、地図のピン（MonitorMapClient、lucide `MapPin`）、「グルーピング」。
- 「動かせない」: `useDragAutoScroll.ts:13`（スクロールの軸）、`AddVisitAnywhereDialog`（青・当日以前・予定外）、`addVisitPlan.ts:52`（青）、`unblock.ts:152`（内訳の見出しで、キーの一部が赤）。
- `pinned`（青）: `copy_week` の `keep_pinned`、`BulkWeekPinAllButton` の `pinned`、`visit_week_pin.ts`、職員入れ替えの `pinned`（CourseDayTablePanel 4903-5107）、`PatientStatusChangeDialog` の `week_pinned`。
- `locked`: `AddVisitAnywhereDialog` の `lockedDates` / `lockedScope`、`SpecialVisitPlaceLauncher` の `lockedScope`、`TimelineDayBoard:243` の `lidLocked`（青の蓋）。
- `blocked`: 同住所ペアの `blocked`、UserEditDialog の `submitBlocked`、VoiceRecorderPanel。
- lucide の `Pin`: `ResetToFixedButton`（固定枠戻のアイコン。赤ピンとは別。§7-9）。

---

## 7) 判断が必要な論点

1. **見送りダイアログの「可動域として記録」の段**（`DismissReasonDialog`）: 記録する先（`movability` の locked／time_flexible）が無くなる。(a) 段ごと消して理由だけを記録する、(b) 「希望訪問スケジュールの範囲をこの曜日／時刻に絞る」へ置き換える、のどちらにするか。BE の `promote_movability` と応答の `movability_updated` / `new_movability` の扱いもこれで決まる。
2. **盤面に「固定」の状態表示を何も残さないか**: 赤い画鋲・「固」マーク・リスト行の琥珀色の背景・患者マスタの赤い行背景が全部消える。希望の範囲で動かす方針なら、代わりに「希望の範囲が狭い（＝事実上動かせない）」印を出すかどうか。
3. **保存の payload から `movability` / `is_pinned` を外したときの BE の挙動**: 患者マスタの保存（PUT）、`existingFixedVisitToItem`、`buildPatternItems` は今「運ばないと unknown に戻る」前提で運んでいる。BE が列を消すなら FE はただ消せばよい。列を残すなら、FE が送らないと既定値で上書きされるのか確認が必要。
4. **「可動域未設定・患者様への確認推奨」バッジ**（ImprovementSuggestionCard）: BE の `requires_patient_confirmation` は今 `movability=unknown` を根拠にしている。撤去後は「希望の範囲外」が根拠になるはずで、文言（例「ご希望の範囲外・患者様への確認推奨」）と BE の判定をそろえる必要がある。
5. **「曜日変更が未許可N件」（`filtered_summary.day_restricted`）**: day_flexible が無くなったあと、曜日を変える提案を何で許すか（希望の曜日だけで判断するか）。BE と文言の確認が必要。
6. **プールへ戻す・削除するときの確認**: 今は赤のときだけ「⚠ これは完全固定です」を足している。撤去後、ふつうの確認文（削除はある・プールへ戻すは赤のときだけ confirm）で足りるか。プールへ戻すときは、赤以外では確認なしで実行される。
7. **範囲最適化の説明文**（ScopeOptimizeDialog:529）: 「ピン留め・完全固定・却下済みの枠は動かしません」の言い換え。青ピンの訪問を BE が除外しているか確認してから「青ピン（今週固定）」と書くか決める。
8. **青の画鋲を、赤しか出ていなかった場所（ペアボックス・ペアの行・ドラッグ中の影・日リストの行）にも出すか**（§5-D）。出さないと、ペアの訪問が青で固定されていても角に画鋲が出ない（今と同じ）。
9. **アイコンと言葉の整理**: `PushPin` の既定色を赤のままにするか青にするか（§5-E）。lucide `Pin` の「固定枠戻」ボタンは残すか。「ピン」という言葉を青専用にするなら、「ピン留め」（WeeklyRitualGuideDialog の FAQ、PoolCandidateList の訳語、errorMessage のコメント）を「今週固定（青ピン）」へそろえるか。
10. **管理画面の機能フラグ `g21_new_algorithm` の説明**（「完全固定 / …」）: 文言だけ直すか、フラグ自体の意味を見直すか（BE の自動割当の `is_pinned` フェンスと関係する）。
11. **仕様書** `docs/plans/pin-and-movability-spec.md`: §1（完全固定）、§1.3（患者マスタ・盤面の赤トグル・pin API）、§1.4（型と一致する訪問のみ）、表示の節（「赤 = 角、青 = 赤の左隣」「`[赤トグル][青トグル][×削除]`」「日リストは `[赤▾][青][×]`」）を、青だけの仕様へ書き直す必要がある（FE の実装はこの仕様を正典としてコメントで引いている）。
