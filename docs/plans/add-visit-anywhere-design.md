# ＋訪問（任意日付の訪問追加）＋ 固定訪問保存の事前確認 設計書 — 2026-09-07

PO 指示（2026-09-07・松岡様依頼「伊藤様 9/14・17・19・22・25・28 12:00」の事後）に基づく。
調査の正典 = `docs/reports/2026-09-07-ito-irregular-schedule-report.html`（経緯）と本書 §1（画面経路の欠陥）。

## 0. 一言で

**スケジュール画面から「患者・日付（複数可）・時刻・所要時間」を指定して予定を入れられる入口を作る。**
時刻を指定すると、システムが **その週の生成済みスケジュール** を見て「このコースなら入れます」を提案する（判定は既存の提案エンジン `propose-slots` を再利用）。入れるコースが無いときだけ M コース（担当なし）を受け皿にする。反映先は「型を変える／その週のスケジュールを変える／新しく 1 件追加」の 3 択。
あわせて、患者画面の固定訪問保存に **「今週も作り直す」の事前確認** を付ける（E）。

### PO 決定（2026-09-07）
| # | 決定 |
|---|---|
| 1 | 入口は「＋訪問」ボタン。F（日付複数選択の一括追加）はこのボタンで対応し、別画面は作らない |
| 2 | 反映先は 3 択: (a) 固定訪問スケジュール（型）を変える／(b) その週の生成済みスケジュールを変える／(c) 全く新しい予定を 1 件追加 |
| 3 | コースは M 固定ではない。指定時刻に入れるコースをシステムが提案する。無ければ M などを使う |
| 4 | 提案は型ではなく **各週の生成済みスケジュール** を見る。仕組みは他の提案機能と同じロジックを使う |
| 5 | A（候補を全患者に）・B（所要時間＝患者の基本時間・35 分）は OK。C（曜日移動）・D（担当なし）は提案の仕組みで吸収 |
| 6 | E（固定訪問保存の事前確認）は本書 §6 の方向で OK |
| 7 | 本体はモーダル。日付はモーダル内のカレンダー（複数選択可） |

### PO 決定（2026-09-07・選択式確認の回答）
| # | 論点 | 決定 |
|---|---|---|
| 8 | E の既定（患者画面・週文脈なし） | **「型だけ変える」**。確認画面で「型＋対象週も作り直す」へ切り替え可 |
| 9 | (b) で動かす元の予定 | **自動で既定＋変更可**: 同じ曜日 → 無ければ最も近い日付。一覧から変更できる |
| 10 | 0 件で M に入れるときの理由 | **任意入力**。入力があれば訪問のメモ（`visits.note`）に残す |
| 11 | 提案の対象拠点 | **段階的緩和**: 基本は主担当拠点のみ。主担当拠点で 0 件のときだけ「主担当拠点に空きがありません」と断りを出し、サブ担当拠点（`patient_allowed_offices`）の候補を「他拠点（要確認）」として提示。選ぶには明示的な確認（チェック）を要する |
| 12 | 所要時間の選択肢 | **5 分刻み・15〜120 分**。初期値は患者の基本時間 |

## 1. 背景 — 画面経路の欠陥（2026-09-07 調査）

API 直叩きでは 5 操作で入った依頼が、画面では次の 6 か所で塞がる。

| # | 欠陥 | 場所 | 影響 |
|---|---|---|---|
| 1 | ＋訪問・空き枠登録の患者候補が **保留プール**（希望回数 > 実訪問数）のみ | `cockpit/AddVisitDialog` の `poolCandidates`／`timeline/SlotRegisterDialog` の `patients=poolPatients` | 希望週 1 回で既に 1 件ある患者は選べない |
| 2 | ＋訪問の所要時間が 30/45/60/90 のみ | `AddVisitDialog.DURATION_OPTIONS` | 35 分が選べない |
| 3 | 「📅 曜日移動」がコースを移動先曜日に付け替えない | `CourseDayTablePanel.moveVisitWeekOnly` が `new_course_template_id` を送らない → BE「省略時コース据え置き」 | 訪問は旧曜日のコース ID のまま。盤面は `visitsByCourse`（course_id 基準・日付フィルタ無し）で描くため旧曜日タブに残る見込み。本番で曜日跨ぎ移動の実行履歴 0 件（未使用・未検証） |
| 4 | 「（担当なし）」行に今週だけの訪問を足せない | `StaffWeekBoard` は行キーが `UNASSIGNED_KEY` のとき＋訪問を出さない。`StaffTimelineView` は出すが `row.staffId='__unassigned__'` をそのまま渡す | ダイアログに「（不明）」・POST の `primary_staff_id` が UUID でなく失敗する見込み |
| 5 | M 列が普段は非表示 | `courseTablesForActiveDay`（定員・固定枠・訪問のいずれも無い日は列を出さない） | 担当なしの受け皿へ直接置けない |
| 6 | 固定訪問の保存が無確認で **今日の週** を作り直す | `PatientFixedVisitsPanel`: 常に `change_scope='pattern_and_week'`・週文脈が無ければ `isoWeekFromLocalDate(new Date())` | 9/14 以降を直したいのに 9/7 週が変わる（松岡様の事象） |

## 2. 再利用する既存部品（調査結果）

### 2-1. 提案エンジン `POST /schedule/v2/propose-slots`（read-only）
- 入力: `existing_patient_id`・`service_minutes`・`time_type`・`preferred_start/end`・`preferred_weekdays`・`iso_year/iso_week`・`office_ids`・`requires_multiple_staff`・`sex_restriction`・`limit`・`include_overcapacity`。
- **`time_type='固定'` は `preferred_start` に厳密一致する開始時刻だけを返す**（`proposal_solver._time_type_allows`）。→「この時刻に入れるコースはどれか」がそのまま聞ける。
- 判定材料 = **対象週の実 Visit をコース単位に集計**（`load_week_course_buckets`）＋ イベント窓 ＋ 距離／移動／バッファ／昼休み／18:00／容量／time_type／同住所ペア／NG・性別／受入カレンダー警告。自動割当・プール投入提案と同じソルバ。PO 決定 4 を満たす。
- 出力 `slots[]`: `office_id/office_name/weekday/course_code/course_label/staff_name/start_time/end_time/score/reasons/warnings/mini_schedule/overcapacity/event_conflicts`。0 件時は `excluded_summary[]`（`capacity_full > pair_blocked > travel_shortage > lunch_window > no_pair_slot > no_gap` の優先で代表理由）。
- 制限: 1 リクエスト = 1 週。座標が無い患者は 0 件（`message` に理由）。

### 2-2. 書き込み API
| 目的 | API | 備考 |
|---|---|---|
| 今週だけ 1 件追加（コース実体の解決/生成・制約確認フロー・2 名体制込み） | `POST /schedule/place-and-fix` `fix_pattern=false` | `source='manual_week'`・Course が無ければ template から生成・NG/性別は 422→acknowledge 再送・op-log（undo）記録あり。`POST /visits` 直叩きより優先する |
| その週の既存訪問を動かす（コース付け替え・担当は移動先コースの担当へ） | `POST /schedule/v2/visit-move-week-only` `new_course_template_id` 付き | 青ピンは 422。`source='manual_week'` を刻む |
| 担当を外す／付ける（週のみ） | `POST /schedule/v2/visit-assign-staff-week` `staff_id: null` | |
| 型を変える（＋その週を再生成） | `PUT /patients/{id}/fixed-visits` `change_scope='pattern_and_week'` + `iso_year/iso_week` | `pattern_only` も可（週を触らない）。応答 `week_sync.visits_regenerated / visits_soft_deleted` |
| 型の検査（dry-run） | `POST /patients/{id}/fixed-visits/validate` | 毎週（型同士）＋今週（実配置）の二段検査 |

### 2-3. 保護の意味論（重要）
- `source='manual_week'` の訪問は、週生成・固定枠戻し・型保存の再生成で消えない。加えて **同じ日付の型スロットの再生成をスキップする**（`layer1_expander._fetch_manual_week_dates`・青ピン・`import` も同様）。
  → (b)「その週を変える」に望ましい意味論。(c)「新しく 1 件追加」で **型のある曜日に足した場合**、その週を再生成すると同日の型スロットが出なくなる副作用がある（週は原則 1 回しか生成しないため実害は限定的。§8 に注意書き）。
- `source='manual'`（`fix_pattern=true` の place-and-fix、POST /visits 既定）は保護されるが型スロットの抑止はしない。

### 2-4. 画面部品
- `components/ui/dialog`（Radix・モーダル）・`components/ui/calendar`（react-day-picker・`ja`・`mode="multiple"` は `/m/leave` と休み管理で使用実績あり）・`components/ui/date-picker`（ポップオーバー 1 日選択）。
- `useConstraintConfirmRetry`（NG/性別の 422 → 確認 → acknowledge 再送）・`useProposeSlots`（`lib/queries/fieldBoard.ts`・警告コードの日本語ラベル表あり）・`useCreateVisit`・`useVisitMoveWeekOnly`・`useUpdateFixedVisits`・`useValidateFixedVisits`。
- 患者の基本時間 = `patients.weekly_pattern.service_minutes`（伊藤様 35）。型（PFV）の `duration_min` は既定でこれを継承。

## 3. 画面設計

### 3-1. 入口（2 か所）
1. **スケジュール画面ツールバーの「＋訪問」**（週に紐づく・患者/日付は空で開く）。
2. **既存の行ボタン（職員週盤面・タイムラインの職員 × 曜日、担当なし行を含む）**。押した日付が選択済み・押した行の職員が「希望担当」の初期値で開く。担当なし行では希望担当なし。

いずれも同じモーダル `AddVisitAnywhereDialog`（新規・既存 `AddVisitDialog` は置き換え。`SlotRegisterDialog`（日タイムラインの空き枠クリック）は §7 Phase 1 で候補を全患者に広げるだけに留める）。

### 3-2. モーダルの構成（上から順に・1 画面で完結・幅 `max-w-2xl`）
```
＋ 訪問を追加                                   [×]
① 患者   [検索して選ぶ ▼]  （プール患者を上に「不足あり」バッジ・全 active 患者を検索可）
         基本 35 分・希望 月/木 午前・稲毛・（担当なし）行から開いた等の補足
② 日付   [◀ 2026年9月 ▶]  カレンダー（複数選択・日曜はグレー・過去日は選択不可）
         選択中: 9/14(月) 9/17(木) 9/19(土) 9/22(火) 9/25(金) 9/28(月)   [クリア]
③ 時刻   [12:00 ▼]（8:00〜18:45 15 分刻み）   所要 [35 分 ▼]（15〜120 の 5 分刻み・初期値＝患者の基本時間）
         希望担当 [（指定なし）▼]（行から開いたときはその職員。提案の並びで優先されるだけで強制しない）
④ 提案   [🔍 入れる場所を探す]  → 日付ごとに 1 行
   9/14(月) 12:00  ◉ 稲毛A（髙梨）  ○ 稲毛C（熊澤）… ○ M（担当なし）   ⚠ 前後の移動がタイト
   9/17(木) 12:00  ◉ 稲毛A（熊澤）  ○ M（担当なし）
   9/19(土) 12:00  入れるコースなし（理由: 定員いっぱい）→ ◉ M（担当なし）
   …（各行のコースはラジオで変更可。既定 = 提案 1 位、0 件なら M）
   この週にすでにある予定: 9/21(月) 09:30 高岡 A ← ⑤で「この週を変える」を選ぶとこれを動かす
⑤ 反映先 ( ) 固定訪問スケジュール（型）も変える     ※ 日付が 1 つのときだけ選べる
         (•) その週のスケジュールを変える（既存の予定を動かす・型は変えない）
         ( ) 新しく 1 件追加する（既存の予定はそのまま・型は変えない）
⑥       [キャンセル]                       [6 件を登録する]
```

### 3-3. 動作の細目
- ①患者: 候補 = active 全員。プール（不足あり）を先頭にバッジ表示（欠陥 1 の解消・PO 決定 5-A）。選択で基本時間・希望・拠点を補足表示。座標なしの患者は「住所のジオコードが無いため提案できません（M へは入れられます）」を出す。
- ②日付: `Calendar mode="multiple"`。表示週の月を初期表示。行ボタンから開いた場合はその日を選択済み。日曜は選択不可（コースは月〜土）。今日以前は選択不可（盤面の `isPast` と同じ規則。過去日の追加は従来どおり不可）。
- ③時刻・所要: 所要は **5 分刻み・15〜120 分**で患者の基本時間を初期値（PO 決定 5-B・12）。基本時間が選択肢に無い値（5 分刻み外）のときは選択肢に加える。`TIME_OPTIONS` は既存を流用。
- ④提案: 「入れる場所を探す」押下で、**選択日付を週ごとにまとめ、週ごとに 1 回** `propose-slots` を呼ぶ（`time_type='固定'`・`preferred_start=③`・`preferred_weekdays=[その週で選んだ曜日]`・`existing_patient_id`・`service_minutes=③`・`office_ids=[患者の主担当拠点]`・`include_overcapacity=true`・`limit=50`）。結果を日付ごとに `weekday` で振り分ける。
  - 表示: 1 位を既定選択。同一コースの `staff_name`・`warnings`（日本語ラベル表を流用）・`overcapacity` は「定員超」バッジ。`mini_schedule` はホバー/展開で前後の予定を見せる。
  - 0 件の日（PO 決定 11・段階的緩和）: まず「主担当拠点に空きがありません（理由: 定員いっぱい 等）」と断りを表示し、患者に **サブ担当拠点** があれば、その拠点だけを `office_ids` にして `propose-slots` をもう 1 回呼ぶ。サブ担当拠点の実体 = その患者の固定訪問行（`patient_fixed_visits.sub_office_id`・Phase E-5 のサブ拠点指定）に現れる主担当拠点以外の拠点の集合（本番 51 行で使用中）。`patient_allowed_offices` テーブルは本番 0 件・API 未公開のため使わない。読み込みは `GET /patients/{id}/fixed-visits`（既存 `useFixedVisits`）を患者選択時に 1 回。得られた候補は「他拠点（要確認）」の見出しの下に **既定未選択** で並べ、選ぶには行の「拠点跨ぎを承知で入れる」チェックを要する。サブ拠点の許可が無い患者、または他拠点でも 0 件のときは **M（担当なし）を既定選択**（受け皿は自拠点の M）。M テンプレートは `label='M'` で拠点ごとに解決（無い拠点は「臨（コースなし）」）。
  - M を選んだ日には「理由（任意）」の入力欄を出す（PO 決定 10）。入力があれば `place-and-fix` / 移動後の PATCH で `visits.note` に「M 配置理由: …」として残す。
  - 希望担当が指定されているときは、その担当のコースを先頭に並べ替えるだけ（判定は変えない）。
  - 提案を押さずに登録は不可（コース未決定のため）。時刻・所要・日付を変えたら提案は失効し再検索を促す。
- ⑤反映先（PO 決定 2）:
  - **(a) 型も変える**: 日付が 1 つのときだけ有効（型は曜日単位で、複数日付の組み合わせと矛盾するため）。`PUT fixed-visits change_scope='pattern_and_week'`、`iso_week = 選んだ日付の週`（今日の週ではない・欠陥 6 の再発防止）。items は既存 PFV を読み、同曜日 slot 0 を選んだ時刻・所要・コース（`course_template_id`）で置換。保存前に §6 の確認を通す。
  - **(b) その週を変える**: 患者がその週に持つ既存訪問（planned・青ピン以外・当日以前以外）を一覧し、**動かす元を 1 件選ぶ**（PO 決定 9: 既定 = 同じ曜日 → 無ければ最も近い日付。一覧から変更可）。`visit-move-week-only`（`old_*` = 元、`new_*` = 選んだ日付/時刻、`new_course_template_id` = 選んだコース）。元が無い週は自動的に (c) の動きになる旨を行に表示。M を選んだときは移動後に担当が M の担当（なし）へ付け替わる（BE 既存動作）。
  - **(c) 新しく 1 件追加**: `place-and-fix fix_pattern=false`（`course_template_id` = 選んだコース/M、`weekday/start_time/duration_min`、`staff_count` = 患者が 2 名体制なら 2）。担当は生成される Course の担当（M なら担当なし）。
  - 複数日付 × (b)/(c) は日付ごとに順に実行。1 件でも失敗したら **その時点で止め、成功分と失敗分を結果画面に列挙**（成功分は op-log の undo で戻せる旨を表示）。
- NG スタッフ／性別制限: (a)(b)(c) いずれも BE が `constraint_confirmation_required` の 422 を返す → `useConstraintConfirmRetry` で確認 → acknowledge 再送（既存フローそのまま）。
- 実行後: 盤面を再取得（`invalidateCockpitBoard`）、トーストに「N 件を登録（今週のみ）」＋「元に戻す」。同期バーには未送信として載る（カイポケ送信は別操作・担当なしは送信ガードで除外される）。

### 3-4. C・D の吸収（PO 決定 5）
- **C（曜日移動）**: 操作メニューの「📅 曜日移動」を選んだら、上記モーダルを **患者・元の訪問・反映先 (b) 固定** で開く（日付を選ぶ → 提案 → 登録）。これにより移動先コースが必ず決まり、欠陥 3（コース据え置き）が消える。最小修正として、移動先曜日の **同コードのコース** を `new_course_template_id` に入れる暫定対応も Phase 0 で行う。
- **D（担当なし行の＋訪問）**: 行ボタンの `staffId` が `UNASSIGNED_ROW_KEY` のときは希望担当なしで開く（欠陥 4）。M 列は提案で M を選んだ日に自動表示される（訪問が付けば `courseIdsWithVisits` で列が出る）ので欠陥 5 は運用上解消。列の常時表示は行わない（盤面の横幅を守る）。

## 4. 提案ロジックの再利用方針（新規ロジックを作らない）
- 判定は `propose-slots` 1 本に寄せる。`assign-candidates`（担当なし訪問の担当候補）や `feasibility_check`（週の実現性レポート）は使わない。理由: 前者は「既存訪問に誰を付けるか」、後者は「並んだ結果の評価」で、いずれも「この時刻に新しい枠を作れるか」の判定を持たないため。
- BE 変更は原則不要。必要になり得る小改修は次の 2 点のみ。
  1. `ProposeSlotsRequest` に `preferred_dates: list[date]`（任意）を足し、**同一週内の複数曜日を 1 リクエスト**で受けられるようにする（現状も `preferred_weekdays` 複数指定で可能なので、まずは FE で週ごとにまとめて対応し、遅ければ検討）。
  2. `excluded_summary` の理由コードに日本語ラベルが無いもの（`no_pair_slot` 等）を FE ラベル表へ追加。

## 5. データと制約
- 青ピン（`week_pinned`）の訪問は (b) の元として選べない（BE 422 と一致）。
- 当日以前の日付は選べない。今日の予定の変更は従来どおり盤面のメニューで行う。
- 特別訪問週間（⭐）の患者: (c) は可。(a) は `mode='special'` の週では現行どおり「今週反映なし」。
- 2 名体制患者（`requires_multiple_staff`）: 提案は `requires_multiple_staff=true` で 2 コース同時枠を返す（`partner_*`）。(c) は `staff_count=2`。(b) は 2 行まとめて移動（`visit-move-week-only` の既存挙動）。
- 拠点（PO 決定 11）: 提案は 1 段目 = 主担当拠点のみ。0 件のときだけ 2 段目 = サブ担当拠点（固定訪問行の `sub_office_id` に現れる拠点）を「他拠点（要確認）」として出し、明示チェックで選択可。サブ担当拠点が無い患者は 2 段目を飛ばして M。M は常に自拠点（拠点跨ぎの M は出さない）。
- カイポケ: ここでは送らない。担当なし（M）で入れた訪問は送信ガードで自動除外され、同期バーに「担当なし」と出る（2026-09-03 導入）。

## 6. E — 固定訪問保存の事前確認（患者画面 `PatientFixedVisitsPanel`）

### 6-1. 現状
保存 → 案Z の警告確認（警告があるときだけ）→ `PUT change_scope='pattern_and_week'`（週文脈が無ければ今日の ISO 週）→ 事後トースト。**どの週が何件作り直されるかは事前に出ない**。

### 6-2. 変更
保存ボタン押下時、警告確認の **前** に必ず次の確認モーダルを出す。
```
固定訪問スケジュールを保存します
  変更内容: 月 09:30(35分) → 月 12:00(35分)・木 12:00(35分) を追加
  反映先
   (•) 型だけ変える（今後生成する週から反映。既にある週の予定は触らない）
   ( ) 型と 9/7 週（9/7〜9/13）の予定も作り直す
        → 伊藤様の 9/7 週の予定 1 件（9/7 09:30 高岡）を消し、型どおり 2 件（9/7 12:00・9/10 12:00）を作ります
        ⚠ 9/7 は今日です／打刻済みの予定があります（該当時のみ）
  [やめる]  [保存する]
```
- 既定（PO 決定 8）: **患者画面（週文脈なし）から開いたときは「型だけ」**。盤面の患者詳細（週文脈あり）から開いたときは「型と表示中の週」。
- 「作り直す」の対象週は、週文脈があればその週、無ければ **選択式**（今日の週／来週）にし、週の日付範囲を明記する。今日の週を選んだ場合は当日以前の訪問がある旨を警告する。
- 件数の根拠: 消える側 = `GET /visits?patient_id&from&to` の planned 件数（青ピン・manual_week・import は再生成で保護されるため「保護 N 件」と別掲）。作る側 = 保存後の型を曜日展開した件数（同日に manual_week があれば除外）。BE の `reset-to-fixed dry_run` は拠点単位で患者絞りが無いため使わず、FE で数える（誤差が出る保護規則は §2-3 に従う）。
- 実装: `handleSave` の冒頭に `setScopeConfirm({...})` を挟み、選択結果で `change_scope`（`pattern_only` / `pattern_and_week`）と `iso_year/iso_week` を決めてから既存の案Z 検査 → PUT へ進む。既存の警告ダイアログはそのまま残す（順序: 反映先確認 → 警告確認）。
- 事後トーストは「型だけ保存しました（既存の週は変わっていません）」／「型を保存し、9/7 週を作り直しました（消 1・作 2）」と週と件数を出す。

## 7. 実装フェーズ

| Phase | 内容 | 主な変更箇所 | 規模 |
|---|---|---|---|
| **0（即効・小）** | 欠陥 2・3・4 の最小修正: ＋訪問の所要時間を 5 分刻み＋患者基本時間初期値／曜日移動で移動先曜日の同コードコースを `new_course_template_id` に付与／担当なし行は staff null で開く | `AddVisitDialog`・`CourseDayTablePanel.moveVisitWeekOnly`・`renderCockpitVisitMenu`・`StaffTimelineView` | FE のみ・1 日 |
| **E（独立）** | §6 の反映先確認モーダル | `PatientFixedVisitsPanel`（＋ `PatientScheduleDetailDialog` からの呼び出しに週文脈を渡す確認） | FE のみ・1 日 |
| **1** | 新モーダル `AddVisitAnywhereDialog`（患者全員・カレンダー複数選択・時刻/所要・提案なしで (c) のみ・コースは手選択＋M） | 新規コンポーネント・ツールバーのボタン・`SlotRegisterDialog` 候補拡大 | FE・2〜3 日 |
| **2** | 提案（`propose-slots` `time_type='固定'` を週ごとに呼ぶ・日付別候補一覧・0 件時の理由と M 既定） | 同上＋ラベル表 | FE・2 日（BE 小改修は必要時のみ） |
| **3** | 反映先 (a)(b) と C の吸収（曜日移動メニュー → モーダル） | 同上＋ `VisitActionMenu` | FE・2 日 |
| 4 | 結果画面（部分失敗の列挙・undo 案内）・実機確認・報告書追記 | | 1 日 |

各 Phase は独立にデプロイ可能。Phase 0 と E は先行して入れる。

## 8. 注意書き（実装者向け）
- (c) を **型のある曜日に足す** と、その週を再生成したとき同日の型スロットが出ない（§2-3）。結果画面に「この日は今週だけの予定として扱われます」を出すに留め、挙動は変えない（PO 決定 2026-08-09 の意味論を尊重）。
- `place-and-fix` は `POST /visits` と違い Course 実体の解決/生成と制約確認フローを持つ。新モーダルは必ず `place-and-fix` を使う。
- `visit-move-week-only` は `(patient, old_date, old_start)` で元を特定する。同時刻 2 行（2 名体制）はまとめて動く。
- 盤面の `visitsByCourse` は course_id 基準で日付を見ない。Phase 0 の暫定対応（同コード付け替え）を入れるまで曜日移動メニューは使わない運用。
- 提案の `office_ids` を空にすると住所判定拠点 or 全拠点になる。必ず患者の拠点を渡す。

## 9. テスト観点
- FE 単体: 候補が全 active 患者／プール優先表示／所要初期値＝基本時間・5 分刻み 15〜120／複数日付の週分割と `propose-slots` 呼び出し回数／主担当拠点 0 件 → サブ拠点の 2 回目呼び出しと「要確認」表示・既定未選択／サブ拠点許可なし or 0 件 → M 既定／M 選択時の理由欄と note 反映／(a) は単一日付のみ有効／(b) の元訪問既定（同曜日→最近）と変更／部分失敗時の停止と列挙／担当なし行から開いたとき staff null。
- FE 単体（E）: 週文脈なし → 既定「型だけ」・`change_scope='pattern_only'`／週文脈あり → 表示週・件数表示／当日・打刻の警告。
- BE（変更が入った場合のみ）: `preferred_dates` の週外日付 422。
- 実機: 伊藤様のケース（6 日付・12:00・35 分）を新モーダルで再現し、提案 → 登録 → 盤面表示 → 同期バーの未送信件数まで確認。曜日移動メニューで火曜へ動かした訪問が火曜タブに出ること。

## 10. 決定済み事項（2026-09-07 PO 回答・§0 の決定 8〜12 に転記）
1. E の既定: 患者画面からは **「型だけ」**（決定 8）。
2. (b) の元: **自動既定（同曜日 → 最も近い日付）＋変更可**（決定 9）。
3. M の理由: **任意入力**（決定 10）。
4. 対象拠点: **主担当拠点 → 0 件時のみサブ拠点を「要確認」で提示**（決定 11）。
5. 所要時間: **5 分刻み・15〜120 分**（決定 12）。

残る未決事項: なし。実装は §7 の Phase 0 と E から着手可。

## 11. 参照
- 調査報告: `docs/reports/2026-09-07-ito-irregular-schedule-report.html`
- 関連設計: `week-cockpit-design.md`（＋訪問 D6・今週だけ操作）・`change-scope-unification-design.md`（反映先 A/B）・`pin-and-movability-spec.md`（青ピン）・`patient-ng-staff-design.md` §7-2（制約確認フロー）・`pool-placement-blockers-investigation-2026-08-31.md`（M 受け皿）・`base-visit-minutes-design.md`（基本時間）
- コード: `backend/app/api/v1/schedule_v2.py`（propose-slots / visit-move-week-only）・`backend/app/api/v1/schedule.py`（place-and-fix）・`backend/app/services/scheduling/propose_slots_service.py`・`proposal_solver.py`・`layer1_expander._fetch_manual_week_dates`・`frontend/components/schedule/v2/cockpit/AddVisitDialog.tsx`・`CourseDayTablePanel.tsx`・`app/(app)/patients/_components/PatientFixedVisitsPanel.tsx`
