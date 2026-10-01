# 訪問の取消・変更の記録 — ダッシュボードに件数と理由を出す（設計・たたき台）

> **ステータス: 保留（2026-10-01 PO）**
> PO「キャンセルと変更をする際の運用を考えていなかった。現状はカイポケ上でその都度ずらしているので、どれがキャンセルでどれが変更かわかりにくい。そこから詰めないと成り立たない」「どのタイミングでキャンセルなのか変更なのか、スケジュール確定のタイミングを一旦決めないと成り立たない」。
> 理由の選択肢の案（取消 13・担当変更 10・移動 9）は PO が「これでいいなと思った」。期間の区切り・コース単位の担当変更の数え方・先の予定を数えるかは「運用次第」。
>
> **再開の前に運用で決めること**
> 1. スケジュールを「確定」とみなすタイミング（例: 前週の◯曜日・月の予定表を出した時点・カイポケへ送った時点）。確定より前の動きは「組み直し」として数えない
> 2. 取消・変更をどこで行うか（らく助で行ってカイポケへ送る／カイポケで行ってらく助へ取り込む）。カイポケで「ずらす」と、らく助側では取消＋追加・日付変更のどちらにも見える
> 3. 「ずらした」を取消と変更のどちらとみなすか（同じ週の中で日や時刻を動かした＝変更、その週に訪問が無くなった＝取消 など）
> 4. 理由は誰が・いつ入れるか（操作した人がその場で／後から事務がまとめて）
> 5. 上が決まった後に: 期間の区切り（訪問日か操作日か）・コース単位の担当変更の数え方・先の予定を数えるか（§12）


作成: 2026-10-01 ／ ステータス: **設計・PO レビュー待ち**（実装なし）
モック: `docs/mockups/visit-change-log-mock.html`（① 取消の理由 ② 担当変更・移動の理由 ③ B カード ④ C 一覧 ⑤ 理由の選択肢（確認用））
関連: `docs/plans/dashboard-staff-performance-design-2026-09-30.md`（B/C の正典・§8）、`docs/mockups/dashboard-staff-performance-mock.html`、`docs/plans/add-visit-anywhere-design.md` §3-5（モーダルの文字と大きさの基準）

---

## 0. 目的（お客様 → PO 2026-10-01）

- **B（人ごとのカード）**: その人の訪問のうち、取消・変更になった件数も出す。
- **C（1 人を深く見る）**: 取消・変更を 1 件ずつ並べ、**理由**・**変更した日**・**対応したスタッフ名**を出す。

今のらく助には「誰が・いつ・なぜ」取り消したか／変えたかの記録が無い（§2）。そこで、**訪問の取消・変更を 1 件ずつ書き足していく記録（`visit_events`）** を新しく作り、操作のときに理由を選んでもらう。

## 1. PO 決定（2026-10-01・確定）

| # | 論点 | 決定 |
|---|---|---|
| 1 | 理由の入力 | **必須**。一覧から 1 つ選ぶ＋自由記述（任意）。今週だけ取消・担当変更・移動（ドラッグを含む）のときに入れる |
| 2 | 「対応するスタッフ名」 | **操作した人**。ログイン中のユーザー → 紐づくスタッフの氏名、無ければユーザー名 |
| 3 | 件数を誰に数えるか | **変更前の持ち主**に数える。担当変更で引き受けた人には別に「引き受け N 件」 |
| 4 | 過去の分 | 今ある記録から出せる分だけ出す。理由が無いものは「理由の記録なし」。**開始日より前は一部だけ**と画面に書く |
| 5 | 理由の選択肢（追加 2026-10-01） | 訪問看護でよくある理由を増やし、長い一覧は「利用者・家族／スタッフ／事業所」で分ける。**設定画面（設定 → 取消・変更の理由）から後で変えられる**ようにする。最終の確定は PO（§12） |

## 2. いまある記録（調査済み・本番 9 月）

| 記録 | 中身 | 使えるか |
|---|---|---|
| `visits` | `status`・`source`・`note`・`updated_at`/`deleted_at` だけ。取消の理由・取り消した人・日時の列は無い | 今の状態だけ。履歴にはならない |
| `schedule_op_log`（戻る／やり直しのための控え） | `user_id`・`created_at`・`op_kind`・`label`・`forward/inverse_payload`。`cancel_visit` は `reason` を持てる | **一部**。ベストエフォート（失敗しても本体は進む）。戻した操作は次の操作で消える（`clear_redo_branch`）。`move_visit_week_only` は visit_id を持たない |
| `audit_logs`（全変更リクエスト） | パス・本文・操作者。変更前後は持たない | 補助のみ（誰がいつ叩いたか） |
| `kaipoke_jobs` / `kaipoke_job_items` | 取込を実行した人（`created_by_user_id`）、項目ごとの前後（日付・開始・終了・スタッフ名）・action・変更文 | カイポケ由来の取消・変更は追える。カイポケ側の理由と操作者は分からない |

本番 9 月の実数: 取消 66 件（うちカイポケ取込由来 約 45 件）。op_log は `set_visit_staff` 71・`move_visit_week_only` 52・`cancel_visit` 21。

→ **op_log は「戻す」ための控えで、履歴ではない**（消える・欠ける・visit_id が無い）。ダッシュボードの正典にはできないので、専用の記録を新しく作る。

## 3. 新しいテーブル

### 3-1. `visit_events`（訪問の取消・変更の記録・書き足すだけ）

1 訪問 × 1 回の取消・変更 = 1 行。2 名訪問（`visit_group_id`）はそれぞれの訪問に 1 行ずつ（ダッシュボードが 2 名訪問をそれぞれに数えるのと揃える）。

| 列 | 型 | 説明 |
|---|---|---|
| `id` | UUID PK | |
| `visit_id` | UUID NOT NULL | 対象の訪問（FK・カスケードなし。visits は論理削除なので残る） |
| `related_visit_id` | UUID NULL | 削除＋配置で移動したときの**移動先の訪問**（§4-6） |
| `patient_id` | UUID NOT NULL | 絞り込み・表示用に控える |
| `kind` | varchar(16) | `cancel` 取消 ／ `uncancel` 取消をやめる ／ `staff_change` 担当変更 ／ `move` 移動（日・時刻） ／ `delete` 削除。CHECK 制約 |
| `visit_date_before` | date NOT NULL | 変更前の訪問日（**ダッシュボードの期間はこの日で切る**・§6-1） |
| `before` / `after` | JSONB | `{date, start, end, staff_id, staff_name, status, course_label}`。取消は `after.status = cancelled` だけ |
| `owner_staff_id_before` | UUID NULL | **その時点の**持ち主（主担当、空ならコース担当）。後で担当が変わっても動かない |
| `new_staff_id` | UUID NULL | 担当変更・移動で持ち主が変わったときの新しい持ち主（代わりなし = NULL） |
| `reason_id` | UUID NULL | `visit_change_reasons.id` |
| `reason_code` | varchar(64) NULL | システムの理由（`kaipoke_sync`・`status_hospitalized` など）。利用者が選ぶ理由は NULL でよい |
| `reason_label` | varchar(80) NULL | **選んだときの表示名の控え**（設定で名前を変えても過去の行は変わらない） |
| `reason_text` | varchar(500) NULL | 自由記述（任意。「その他」のときは必須・§5-3） |
| `actor_user_id` | UUID NULL | 操作した人（ログイン中のユーザー）。取込はそれを実行した人 |
| `actor_label` | varchar(80) NULL | 書いたときの表示名の控え（スタッフ名 → 無ければユーザー名）。ユーザーを消しても名前が残る |
| `origin` | varchar(16) | `app` らく助の操作 ／ `kaipoke` カイポケ取込 ／ `status_link` 利用者ステータス連動 ／ `system` 自動処理。CHECK 制約 |
| `source_ref` | JSONB NULL | `{kaipoke_job_id, kaipoke_job_item_id, op_log_id, audit_log_id}` など出どころ |
| `op_group_id` | UUID NULL | 1 回の操作のまとまり（op_log と同じ値。戻すときの目印） |
| `is_backfill` | bool | 過去の記録から作った行（§7） |
| `undone_at` / `undone_by_user_id` | timestamptz / UUID NULL | **戻したしるし**（§3-3） |
| `redo_of_event_id` | UUID NULL | やり直しで書いた行が、どの行のやり直しか |
| `created_at` | timestamptz | 操作した日時（＝「変更した日」） |

索引: `(owner_staff_id_before, visit_date_before)`・`(new_staff_id, visit_date_before)`・`(visit_id, created_at)`・`(op_group_id)`・`(origin, created_at)`。

### 3-2. 書き足すだけにする方法

- 行の更新・削除はしない。**例外は `undone_at` / `undone_by_user_id` の 2 列だけ**（戻したしるし）。
- ORM の `before_update` で 2 列以外の変更を例外にし、PostgreSQL には同じ内容のトリガーを付ける（SQLite のテストでは ORM 側で守る）。`before_delete` も例外。
- 本体の変更と**同じトランザクション**で書く（op_log のようなベストエフォートにしない）。記録が書けなければ操作ごと失敗させる。理由を残すことが機能の前提のため。

### 3-3. 「戻る」「やり直し」の表し方（決定案）

**行は消さず、戻したしるしを付ける。逆向きの行は作らない。**

- 戻る（`op_log_service.execute_undo`）: 同じ `op_group_id` で `undone_at IS NULL` の行に `undone_at`・`undone_by_user_id` を入れる。
- やり直し（`execute_redo`）: 新しい行を書く（種類・理由は元の行の写し、操作した人はやり直した人、`redo_of_event_id` に元の行）。
- 件数は `undone_at IS NULL` の行だけで数える。C の一覧は既定で戻した行を隠し、「戻した操作も出す」で灰色にして見せる。

逆向きの行（取消 → 取消をやめる）にしない理由: 打ち消し合う 2 行を毎回ペアにして差し引く必要があり、件数の計算と画面が複雑になる。「戻る」は操作そのものを無かったことにする機能なので、しるしで足りる。

一方、**「取消をやめる」を画面から明示的に押したとき**は別の操作として `uncancel` の行を書く（理由は任意）。件数の扱いは §6-1。

### 3-4. `visit_change_reasons`（理由の選択肢・設定で変えられる）

| 列 | 説明 |
|---|---|
| `id` | UUID PK |
| `kind` | `cancel` ／ `staff_change` ／ `move`（`delete` は `cancel` の一覧を使う） |
| `group_key` | `patient` 利用者・家族 ／ `staff` スタッフ ／ `office` 事業所 ／ `other` その他。ダイアログの小見出し |
| `label` | 表示名（最大 40 文字） |
| `sort_order` | 並び順 |
| `is_active` | 選択肢に出すか。**使われた理由は消さず、隠すだけ**（過去の行は `reason_label` で表示を保つ） |
| `requires_text` | 選んだら自由記述を必須にする（「その他」に初期で付ける） |
| `is_system` | 画面からは選べないシステムの理由（カイポケ・ステータス連動）。名前だけ変えられる |
| `code` | システムの理由の固定キー（`kaipoke_sync`・`status_hospitalized`・`status_suspended`・`status_terminated`・`staff_off`）。それ以外は NULL |

- migration で §5 の初期値を入れる。**どの事業所にも当てはまる一般的な理由だけ**で、お客様固有の値は入れない（別の事業所へ提供する準備 `multi-office-readiness` の方針）。
- 事業所ごとに言い方や必要な理由が違うので、**設定 → 取消・変更の理由** で追加・名前の変更・並べ替え・隠す ができるようにする（§5-4）。コードには書かない。

## 4. 書き込む場所（全部で 1 つのサービスを通す）

`backend/app/services/visit_events.py` に `record_visit_events(db, *, visits, kind, before, after, reason, actor, origin, op_group_id, source_ref)` を 1 つ作り、下の全部から呼ぶ。持ち主（主担当、空ならコース担当）の判定はダッシュボードと同じ関数を使う（`dashboard_staff_performance.py` の持ち主の判定を `services/visit_owner.py` に切り出して共用）。

| # | 操作 | BE | FE（理由ダイアログを足す） | kind / origin / 理由 |
|---|---|---|---|---|
| 1 | 今週だけ取消 ／ 取消をやめる | `api/v1/schedule_v2.py:6619` `visit_cancel_week`（`reason` は既にある → `reason_id`＋`reason_text` に変える） | `components/schedule/v2/cockpit/VisitActionMenu.tsx`・`CourseDayTablePanel.tsx:4370`・`timeline/TimelineDayBoard.tsx`・`timeline/WeekTimelineBoard.tsx`・`cockpit/StaffTimelineView.tsx`・`field/FieldBoard.tsx`（今は 1 クリックで取消） | cancel / uncancel・app・必須（取消をやめるは任意） |
| 2 | 担当変更（1 訪問） | `schedule_v2.py:1674` `visit-assign-staff-week`（op `set_visit_staff` 1799） | `lib/queries/visitAssignStaffWeek.ts` の呼び出し元 | staff_change・app・必須 |
| 3 | 🛌 休みにする（急休＋代わり） | `schedule_v2.py:1820` `staff-off-week`（`set_staff_off` 2089・`set_visit_staff_slot` 2118・`set_course_staff` 2148） | 休みのダイアログ（理由は聞かない） | staff_change・app・**理由は自動で「スタッフの急な休み」**（`code=staff_off`）。代わりなしは `new_staff_id=NULL` |
| 4 | 今週だけ移動 | `schedule_v2.py:1467` `visit-move-week-only`（op `move_visit_week_only` 1645）。**動かした visit_id を集めて返す・op_log にも入れる** | `lib/queries/visitMoveWeekOnly.ts`・`timeline/TimelineMoveDialog.tsx`・盤面のドラッグ | move・app・必須（ドロップ後にダイアログ） |
| 5 | コースの曜日を今週だけ移す | `schedule_v2.py:2371`（op `move_course_weekday`） | 同上 | move（動いた訪問ごと）・app・必須 |
| 6 | ドラッグ = 削除＋配置 | `api/v1/visits.py:1010` `delete_visit`（op 1128）＋ `api/v1/schedule.py` place-and-fix（op 1154）。同じ `op_group_id` | `CourseDayTablePanel.tsx` のドラッグ | **配置側で 1 行**: move・`visit_id`=元・`related_visit_id`=新。削除は `intent=move` のとき書かない。単独の削除は delete・必須（取消の一覧） |
| 7 | 訪問の編集 | `visits.py:906` `PATCH /visits/{id}`（主担当・日付・時刻が変わったときだけ） | 訪問の編集ダイアログ | staff_change / move・app・必須 |
| 8 | コースの担当を変える（毎週の型） | `api/v1/courses.py:317` `PATCH /courses/{id}`（op 458） | コースの編集 | staff_change・app・必須。**生成済みで今日以降の訪問ごと**に 1 行（同じ `op_group_id`。C では 1 行にまとめて「コースの担当変更・訪問 n 件」）→ §12 Q3 |
| 9 | 利用者ステータス連動（入院・休止・解約） | `services/patient_status_sync.py:647` `_apply_deactivation`（取消）・`:766` `_apply_reactivation`（戻す）・`:905` | `components/patients/PatientStatusChangeDialog.tsx`（**任意のメモ欄を足す** → `reason_text`） | cancel / uncancel・status_link・理由は自動（入院 → `status_hospitalized` など）。操作した人 = ステータスを変えた人 |
| 10 | カイポケ取込 | `services/kaipoke/inbound.py` delete（~1200）→ cancel・追加で取消から戻す（~1091）→ uncancel・スタッフ変更（~1435）→ staff_change・edit / date_change（~543・~605）→ move | — | kaipoke・理由 `kaipoke_sync`「カイポケで変更（理由はカイポケ側）」・操作した人 = 取込を実行した人（§6-3） |
| 11 | 戻る ／ やり直し | `services/op_log_service.py:287` `execute_undo`・`:339` `execute_redo` | — | §3-3 |
| 12 | 提案の適用（自動割当・改善提案・範囲最適化の apply） | 各 apply（`ImprovementSuggestionsSection.tsx` ほか） | 適用の確認 | staff_change / move・app・理由 `code=proposal_apply`「提案を適用」（選ばせない）→ §12 Q5 |

記録しないもの: 週の生成・再生成・前の週のコピー（訪問を新しく作るだけで、取消・変更ではない）、同行（副担当・メンター・`accompaniments`）の付け外し、打刻・実績の時刻を合わせる操作（別の記録 `visit_time_adjustments` がある）。

**漏れを防ぐテスト**: op_log の `op_kind` の一覧（`set_visit_staff`・`move_visit_week_only`・`cancel_visit`・`delete_visit`・`place_and_fix`・`patch_course_staff`・`set_staff_off`・`set_visit_staff_slot`・`set_course_staff`・`move_course_weekday`）を、「`visit_events` に書く／書かない（理由付き）」の表と突き合わせ、表に無い `op_kind` が増えたら落ちるテストを置く。

### 4-1. API の形

- 取消・担当変更・移動の各リクエストに `reason_id: UUID`（必須）と `reason_text: str | None`（最大 500）を足す。`reason_id` が無い・無効・種類違い・隠した理由 → 422「理由を選んでください」。`requires_text` の理由で記述が空 → 422。
- 既存の `reason`（自由記述だけ）は `reason_text` として受けるが、`reason_id` が無ければ 422（FE と BE は同時に出す。古い画面は PWA の自己回復で更新される）。
- `GET /api/v1/settings/visit-change-reasons?kind=`（全ロール読み取り・ダイアログ用）／`POST`・`PATCH`・`PUT …/order`（admin）。

## 5. 理由の選択肢（初期値・設定で変えられる）

### 5-1. 一覧（PO のたたき台を整理）

**取消**（13）

| 小見出し | 理由 |
|---|---|
| 利用者・家族 | 利用者の都合（外出・来客など） ／ 家族の都合 ／ 体調不良（利用者） ／ 受診・検査 ／ 入院 ／ 施設入所・ショートステイ ／ 他サービスとの重なり（デイサービスなど） ／ 契約終了・中止 ／ ご逝去 |
| スタッフ・事業所 | スタッフの急な休み ／ 天候・災害 ／ 重複・誤登録 |
| その他 | その他（記述必須） |

**担当変更**（10）

| 小見出し | 理由 |
|---|---|
| スタッフ | スタッフの急な休み ／ スタッフの体調不良・早退 ／ 研修・同行 |
| 利用者・家族 | 利用者・家族の希望 ／ 相性（NG スタッフを含む） |
| 事業所の調整 | 負担の平準化 ／ 資格・医療処置の都合 ／ 移動の効率 ／ 時間の都合 |
| その他 | その他（記述必須） |

**移動**（9）

| 小見出し | 理由 |
|---|---|
| 利用者・家族 | 利用者の都合 ／ 家族の都合 ／ 受診・検査 ／ 他サービスとの重なり |
| スタッフ・事業所 | スタッフの都合 ／ 移動の効率 ／ 天候 ／ カイポケとの調整 |
| その他 | その他（記述必須） |

**画面からは選べないシステムの理由**: カイポケで変更（理由はカイポケ側）／入院（ステータス連動）／休止（ステータス連動）／解約（ステータス連動）／スタッフの急な休み（🛌 休みにする）／提案を適用。

### 5-2. たたき台から変えた点と理由

- 「死亡」→ **「ご逝去」**: 画面に出る言葉なので丁寧に。
- 「スタッフ急休」→ **「スタッフの急な休み」**: 略語を避ける（平易な日本語の決まり）。🛌 休みにする の自動の理由と同じ言葉にそろえ、手で選んでも自動でも同じ集計になる。
- 担当変更の「スタッフ急休」と「スタッフの体調不良」は**分けたまま**、後者を「体調不良・早退」に: 前者は終日の休み（🛌 休みにする と同じ）、後者は出勤後の途中交代。重なりを避ける。
- 「相性・NG」→ **「相性（NG スタッフを含む）」**: NG はらく助の機能名（利用者ごとの NG スタッフ）なので残しつつ、それだけに限らないと分かるように。
- 取消の「デイサービス等との重なり」と移動の「他サービスとの重なり」を **「他サービスとの重なり（デイサービスなど）」**にそろえた。
- 移動の「カイポケとの調整」: らく助の連携先としての一般名なので初期値に残す。カイポケを使わない事業所は設定で隠す。
- **取消・担当変更・移動で同じ言葉を使う理由は、言葉をそろえる**（「利用者の都合」「家族の都合」「受診・検査」「移動の効率」）: 後で理由別に集計するときに種類をまたいで数えられる。
- 一覧が 9〜13 と長いので、小見出し（利用者・家族／スタッフ／事業所）で分ける。理由は利用者側を先に（取消の大半が利用者側のため）、「その他」は常に最後。

### 5-3. 選び方（ダイアログ）

- 1 つ選ぶのは必須。**プルダウンではなく、小見出しごとに並んだ大きめの選択ボタン（ラジオ）**: 9〜13 の候補を 1 画面で見比べられ、タッチでも押しやすい。中身は「一覧から 1 つ選ぶ」のまま。
- 自由記述（補足）は任意。**「その他」を選んだときだけ必須**（`requires_text`）。「その他」だけでは後で何も分からないため。
- 確定のボタン（取り消す／担当を変える／移動する）は、理由を選ぶまで押せない。押せない理由をボタンの近くに書く（「理由を選ぶと押せます」）。
- 文字と大きさは `add-visit-anywhere-design.md` §3-5: 本文 14px・小見出し 16px 太字・タイトル 18px・入力の高さ 36〜40px。幅は 640px（スマホでは画面幅 − 32px）。
- 前回と同じ理由を続けて選ぶことが多い場面（同じ日の複数の取消）に備え、**直前に選んだ理由を一番上に「前回: ◯◯」として出す**（既定では選ばない。選び忘れを防ぐため自動では選ばない）。

### 5-4. 設定 → 取消・変更の理由（後の段階でよい）

- 種類（取消／担当変更／移動）のタブ、小見出しごとの一覧。
- できること: 追加・名前を変える・並べ替え・小見出しを変える・隠す／戻す・「記述を必須にする」の切り替え。
- **使われた理由は削除できない**（隠すだけ）。過去の行は書いたときの名前（`reason_label`）で出る。
- システムの理由は名前だけ変えられる（隠せない）。
- admin だけ。

## 6. ダッシュボードに足すもの

### 6-1. 数え方

| 数 | 定義 |
|---|---|
| 取消 | `kind IN (cancel, delete)`・`owner_staff_id_before` = その人・`visit_date_before` が期間内・戻していない。**同じ訪問は 1 件**。あとで「取消をやめる」があった訪問は数えない（実際には取り消されていないため）→ §12 Q2 |
| 変更 | `kind IN (staff_change, move)` で同じ条件。同じ訪問を 2 回動かしても 1 件。内訳として「担当変更 a・移動 b」 |
| 引き受け | `new_staff_id` = その人・`new_staff_id ≠ owner_staff_id_before`・期間内・戻していない（担当変更と、担当も変わる移動） |
| 理由の記録なし | 上のうち `reason_id` も `reason_code` も無い行（主に過去の分） |

- 期間は**変更前の訪問日**（`visit_date_before`）で切る。カードの「訪問件数」と同じ期間の訪問の話にそろえるため（「その人の訪問のうち何件が」）。「変更した日」は C の一覧に出す → §12 Q1。
- 先の訪問（今日より後）の取消・変更も数える（取消は前もってするもの。訪問件数の「今日まで」とは違う）。カードにこの違いを小さく書く。
- 拠点の絞り込みは今と同じく職員の所属拠点。

### 6-2. B カード

カードの下の方（週ごとの小さな棒の上）に 1 行のまとまりを足す:

> 取消 **4** ・ 変更 **7**（担当変更 3・移動 4） ・ 引き受け **2**　　うち理由の記録なし 3

- 0 件の数も出す（「0」と分かることに意味がある）。
- 上段のチームの数字に「取消・変更」を 1 枚足す（取消 N・変更 N・うちカイポケ N）。
- 期間が記録の開始日より前を含むときは、上段の注意書きに「◯月◯日より前の取消・変更は、残っていた記録から読み取ったもので一部が欠けています」を足す。

### 6-3. C の一覧

C の週の表の下に「取消・変更の記録」を足す。

| 列 | 中身 |
|---|---|
| 訪問日 | 変更前の訪問日・時刻（`10/7(火) 10:00`） |
| 利用者 | 氏名（§9） |
| 種類 | 取消／取消をやめた／担当変更／移動／削除／引き受け（その人が新しい持ち主の行） |
| 変更前 → 変更後 | 担当変更: 佐々木 → 中村 ／ 移動: 10/7(火) 10:00 → 10/8(水) 14:00 ／ 取消: — |
| 理由 | 理由の名前＋補足。無ければ「理由の記録なし」（灰色の札）。カイポケ:「カイポケで変更（理由はカイポケ側）」 |
| 操作した人 | らく助: 氏名 ／ カイポケ:「取込を実行した人: 氏名」 ／ ステータス連動:「ステータスを変えた人: 氏名」／ 自動: 「自動」 |
| 出所 | らく助 ／ カイポケ ／ ステータス連動 ／ 以前の記録（`is_backfill`） |
| 変更した日 | `created_at`（`10/3 9:12`） |

- 絞り込み: 種類（すべて／取消／担当変更／移動／引き受け）・出所（すべて／らく助／カイポケ／ステータス連動）・「理由の記録なしだけ」・「戻した操作も出す」（既定は出さない）。
- 並びは訪問日の新しい順。50 件ずつ。
- 「コースの担当変更」で 1 回に何件も書いた行は、同じ `op_group_id` で 1 行にまとめて「訪問 n 件」と出し、押すと開く。
- 一覧の上に**記録の開始日の注意**を常に出す（§7-3）。
- スマホ幅では 1 件をカード（2〜3 行）で出す。

### 6-4. API

- `GET /api/v1/dashboard/staff-performance` の各職員・チームに `changes: {cancel, change, staff_change, move, taken_over, no_reason}` を足す（期間ごと。週ごとは今は不要）。応答に `change_log_started_on`（記録の開始日）を足す。
- `GET /api/v1/dashboard/visit-events?staff_id&from&to&kind&origin&no_reason&include_undone&cursor`（admin）— C の一覧。持ち主の行と引き受けの行を両方返し、行に `role: owner|taken_over` を付ける。

## 7. 過去の分（バックフィル）

### 7-1. 材料と作り方

一度だけ動かす `scripts/backfill_visit_events.py`（dry-run で件数を出してから本番）。全部 `is_backfill = true`・`reason_id = NULL`。

| 材料 | 作る行 | 理由 | 操作した人 |
|---|---|---|---|
| op_log `cancel_visit`（`undone = false`） | cancel / uncancel（`forward.cancel`） | `forward.reason` があれば `reason_text` に（名前は「以前の記録の理由」）。無ければ「理由の記録なし」 | `user_id` |
| op_log `set_visit_staff`・`set_visit_staff_slot`・`set_course_staff` | staff_change（payload の前後のスタッフ） | 記録なし（`set_staff_off` と同じ `op_group_id` の行は `staff_off`＝「スタッフの急な休み」→ §12 Q4） | `user_id` |
| op_log `move_visit_week_only`・`move_course_weekday` | move | 記録なし | `user_id` |
| op_log `delete_visit`＋`place_and_fix`（同じ `op_group_id`） | move（1 行） | 記録なし | `user_id` |
| op_log `patch_course_staff` | staff_change（その時に生成済みだった訪問ごと） | 記録なし | `user_id` |
| `kaipoke_job_items`（action = delete / edit / date_change・成功したもの） | cancel / staff_change / move | `kaipoke_sync` | `kaipoke_jobs.created_by_user_id` |
| 取消の訪問で上のどれにも当たらないもの（`status = cancelled`・`source = status_cancel`） | cancel・status_link | ステータスの名前（入院など）。日時は `patients.status_changed_at`（0082） | 不明 →「記録なし」 |

### 7-2. 分かっている欠け

- **op_log はベストエフォート**: 書けなかった操作は残っていない。
- **戻した操作は次の操作で消える**: 戻したまま残っている行（`undone = true`）は作らない（実際には起きていないので正しい）。
- **`move_visit_week_only` は visit_id を持たない**: 患者・ISO 週・移動先の曜日と開始時刻から今の訪問を探す。見つからない・2 件以上 → 作らない（件数をレポートに出す）。
- **変更前の持ち主**: payload に前のスタッフがあればそれ。取消は今の主担当／コース担当で代える（その後に担当が変わっていると違う人に数えられる）。代えた行は `before.owner_estimated = true`。
- **カイポケのスタッフ名 → スタッフ**: 名前が 1 人に決まるときだけ ID を入れる。決まらなければ名前だけ（カードには数えず、C には出る）。
- **op_log が入る前・取込のジョブ記録が無い期間**は何も無い。
- `audit_logs` は変更前後が無いので行は作らない（操作した人と日時の照合だけに使う）。

### 7-3. 記録の開始日

- 本番に入れた日を `change_log_started_on`（設定の 1 行・migration で入れる）として持つ。
- B の上段・C の一覧の上に「**◯月◯日より前の取消・変更は、残っていた記録から読み取ったもので、一部が欠けています。理由は記録されていません。**」を出す（期間が開始日より前を含むときだけ・C は常に）。

## 8. 権限

- ダッシュボードと同じ **`require_role("admin")`**（`staff-performance` と `visit-events`）。staff には出さない。
- 理由の一覧の読み取り（ダイアログ用）は全ロール。追加・変更は admin。
- 理由の入力は、その操作ができる人なら誰でも（今の権限は変えない）。

## 9. 利用者の氏名（個人情報）

- C の一覧は利用者の氏名を出す（admin だけが見る画面）。病名・ステータスの詳細は出さない（「入院」という理由の名前は出る）。
- 面談で画面を見せる場面を考え、C に「**利用者名を伏せる**」の切り替えを置く（「佐◯ ◯子 様」のように 1 文字目だけ。画面の上だけで、API は変えない）。
- 自由記述に利用者の様子を書き込みすぎないよう、ダイアログの補足欄に「スタッフの評価にも使われます。必要なことだけ書いてください」と添える。
- `visit_events` は訪問と同じ扱い（論理削除された利用者の行も残すが、一覧では氏名を「（削除された利用者）」）。

## 10. テスト

**BE**
- 各書き込み口（§4 #1〜#12）で 1 行ずつ書かれる。2 名訪問は 2 行。`owner_staff_id_before` が変更前の持ち主（主担当 → 空ならコース担当）。
- `reason_id` 無し・無効・種類違い・隠した理由 → 422。「その他」で記述が空 → 422。
- 記録が書けないときは本体もロールバック（op_log と違う）。
- 戻る → `undone_at` が入る・件数から消える。やり直し → 新しい行・`redo_of_event_id`。
- 書き足すだけ: `undone_*` 以外の更新・削除は例外。
- カイポケ取込: delete / edit / date_change / スタッフ変更 / 取消から戻す で正しい kind・`kaipoke_sync`・操作した人 = 実行した人。
- ステータス連動: 入院 → cancel（理由 `status_hospitalized`）、稼働中へ戻す → uncancel。
- op_log の `op_kind` の突き合わせテスト（§4）。
- 集計: 期間の端・拠点・同じ訪問 2 回の変更 = 1 件・取消をやめた訪問は数えない・引き受けは持ち主と同じ人なら数えない。
- 理由の設定: 使われた理由は削除できない・隠した理由はダイアログに出ない・名前を変えても過去の行の `reason_label` は変わらない。
- バックフィル: 各材料から 1 行・dry-run が書かない・`move_visit_week_only` の見つからないものを数える・2 回動かしても重複しない（`source_ref.op_log_id` で一意）。
- 権限: staff は 403。

**FE**
- 理由ダイアログ: 選ぶまで確定が押せない／「その他」で補足が必須になる／Esc とやめるで閉じて何も送らない／送る中は二重に押せない。
- 盤面のドラッグ → ダイアログ → やめると元の場所に戻る。
- B カードの数・C の絞り込み・「理由の記録なし」・開始日の注意・利用者名を伏せる。
- 各呼び出し元（§4 #1 の 6 画面）で 1 クリック取消が残っていないこと。

## 11. 段階

1. migration 0090: `visit_events`・`visit_change_reasons`（初期値）・`change_log_started_on`。サービスと #1〜#4・#6・#11（よく使う操作）。理由ダイアログ。
2. #5・#7〜#10・#12。ダッシュボードの B／C。
3. バックフィル（dry-run → PO に件数を見せる → 本番）。
4. 設定 → 取消・変更の理由。

## 12. PO に確認したい点

1. **期間の切り方**: 変更前の訪問日で切る（案）か、変更した日で切るか。
2. **取消をやめた訪問**: 取消の件数から外す（案）か、取消として残し「後でやめた」と添えるか。
3. **コースの担当を変える（毎週の型）**: 生成済みの訪問ごとに数える（案・1 回で 10 件以上増えることがある）か、件数には入れず一覧だけに出すか。
4. **過去の 🛌 休みにする の担当変更**: 理由を「スタッフの急な休み」と推定して入れてよいか（案は推定する。推定しなければ「理由の記録なし」）。
5. **提案の適用（自動割当・改善提案）による担当変更**: 理由を聞かず「提案を適用」で数える（案）か、件数から外すか。
6. **先の訪問の取消・変更も数える**（案）でよいか（訪問件数は今日までなので、期間の意味が少し違う）。
7. **理由の選択肢の最終確定（PO）**: §5-1 の 3 つの一覧（モック ⑤ で印を付けて確認できる）。
8. 「その他」を選んだときだけ補足を必須にする（案）でよいか。
9. 理由別の集計（例: 取消の理由の内訳グラフ）を C やチームの画面に出すか（今回は一覧だけ）。
