# 休み申請の本格化と「休み・勤務表」— 設計書（2026-10-20・改訂 3）

> 状態: **仕様確定（PO 決定 10-20 をすべて反映）・実装待ち**。コード・本番は未変更。ブランチ `feature/agent-skills`。
> **共通の土台（段階 0）** `docs/plans/shared-foundation-leave-acceptance-2026-10-20.md` に合わせてある。食い違ったら**段階 0 の文書が正**。この設計書は**段階 0 が済んでいる前提**（`app/core/leave.py`・`kaipoke_source_snapshots`（alembic 0098）・`app/services/kaipoke/sources.py`）。
> 改訂 2（10-20）: 1 回目のレビュー（REVISE）の H1〜H6・M1〜M11・L1〜L7 と PO 決定 1〜10 を反映。
> 改訂 3（10-20）: 再レビュー（REVISE）の H-a〜H-f・中1〜中8・低を反映し、段階 0 に合わせた。どこで解決したかは §12 の対応表。
> モック（PO 確認済みの操作の正典）: `docs/mockups/leave-admin-mock-2026-10-20.html`（公開版 https://claude.ai/artifact/NEfxP1JjVLBg5zvL48Vkyy）。モックのコメントの「設計 §5-3」は本書の §5-3。
> 画面の言葉は「合わせる・調整」（「直す・修正」は使わない）。返答・選択肢は日本語。
> 根拠の `ファイル:行` は 2026-10-20 にコードを読んで確かめたもの（`backend/app/` は `app/` と略す）。

---

## 0. 要約

| 項目 | 決定（PO 2026-10-20） |
|---|---|
| 休みの種類 | **公休・午前休・午後休・有給**。午前休 = **13:00 から勤務**、午後休 = **12:30 から休み** |
| 必要休日数 | その月の **土日の数＋祝日の数＋事業所の休み（年末年始など）**。土曜の祝日は土曜として 1 日、日曜の祝日は振替休日を数える。祝日・事業所の休みは**それだけでは休みにならない**（休みの登録がある日だけ休み。数えるのは必要休日数だけ）。常勤だけ。**有給は数えない**。半休は 0.5 日 |
| 休日数 | **「型で勤務しない曜日」＋「表で入れた休み」**を数える（有給は数えない・別に数える） |
| 半休の日の割り振り | **コースは持たせ、休みの時間帯にかかる訪問だけ「担当なし」へ**（方式 (c)）。担当なしは「`primary_staff_id` が空 ＋ `manual_staff_override` が真」（§3-2）。**らく助の中で訪問を作る・担当を書く処理では休む人に戻らない**（最後に共通の後始末を必ず呼ぶ・§4-2）。**カイポケの取込では戻ることがある**（カイポケが正）。そのときは取込のプレビューと「休みの時間帯に担当が付いている訪問 N 件」で知らせる |
| 承認・追加したとき | 休みの時間帯の本人の訪問を**「（担当なし）」へ移す**。管理者に「〇〇さんは M/D に N 名の訪問を予定していましたが、未割り当てとしました」、本人に承認を通知。画面は移らない。**今日より前の日は休みの記録だけ**（訪問・コース・固定イベントは動かさない） |
| 取り消し | 「休み・勤務表」は**「取り消す」だけ**（「元に戻す」は出さない）。休みは消え、申請は承認済みのまま。担当なしへ移した訪問は戻さない |
| カイポケとの関係 | **カイポケが正**。流れ: らく助で申請を受けて休みを確定 → 組んだ予定と休みをカイポケへ送る → カイポケで手直し → らく助に取り込んで差分をなくす。**「らく助に合わせる」例外は作らない** |
| 休みの送信の状態 | 休みごとに **照合前／未送信／送信済み／カイポケで要対応**（§2-5・§7-4-3 の全通りの表）。送ったあとにらく助で取り消す・種類を変えると「カイポケで要対応」。今ある休み（本番 96 件）は「照合前」で、**その月の照合が一度済むまで確定でカイポケへ送らない** |
| 照合 | 「カイポケと比べる」で、段階 0 の仕組み（`KaipokeJob` の `op='leave-compare'`）がその月の週の個別業務と実績 CSV を取り直して `kaipoke_source_snapshots` に置き、照合はそこから読む。未送信は差分ではなく「送る予定」。件名で種類が分からない予定は管理者が種類を選び、記録する |
| 月の締め | **自動ではない**。照合で差分 0 件・送る予定 0 件のあと、管理者が「実績を記録する」を押す。照合の後に休みを変えたら比べ直す。cron は作らない |
| PC の画面 | メニューに **「休み・勤務表」**（全員×日付の月の表＋右に申請中／カイポケとの差分の一覧） |
| 申請履歴 | 右の小さなカレンダー（`StaffLeavePanel`）は**無くす**。「休み・勤務表で開く」の帯と行のリンク |
| スマホ | **1 つの「休み」ページ**。種類を選んで日を押す。休みは**数だけ**見せる。その日何人休むかは**見せない**。締め切り = **前の月の 20 日（JST）**。締め切り後は「変更をお願いする」（種類を変える・**休みを取り消してほしい**） |
| 新着の申請 | PC 右上のベル＋メニューの未処理件数（メールなし） |
| 勤務表 | **Google スプレッドシート**。「この月を確定」で**予定版**、「実績を記録する」で**確定版**を、それぞれ**別ファイル**で作る。**5 年間はらく助から消さない** |
| 実装とデプロイ | 段階 0 のあと**一気に作り、一気にデプロイ**（休みの中で段階を分けたデプロイはしない）。作る順番は §9 |

---

## 1. 現状（2026-10-20 調査・読むだけ）

### 1-1. 本番のデータ
| もの | 件数 | 意味 |
|---|---|---|
| `pending_requests`（全種類） | **0 件** | スマホの休み申請（8/18 公開）は一度も使われていない |
| `staff_shift_confirmations`（月の確定） | 0 件 | 未使用 |
| `shift_requests`（月の自由記述の希望） | 0 件 | 未使用 |
| `staff_weekly_overrides` | 96 件・**全部 `off`** | 7 人分・作成 7/21〜9/19・reason も時刻も空。PC で入れたもの |
| `staff_events` の件名「休み」 | 140 件（＋「PM休み」2 件） | **実際の休みはカイポケで管理**され、件名だけの予定として取り込まれている。override とはつながっていない |

### 1-2. コードの現状（根拠つき）
- 申請: `app/api/v1/pending_requests.py`。staff_off の payload は自由な dict。反映 `app/services/pending_request_applier.py:235-262` `_apply_staff_off` は **INSERT だけ**・種類を検証しない・**訪問も固定イベントも動かさない**・承認の通知なし。反映の呼び出し `applier.apply(db, row)`（`pending_requests.py:526`・`:735`）は操作した人も op_group も渡さない。
- 作成時の重複の止め `_ensure_staff_off_creatable`（`pending_requests.py:290-350`）: 同じ日の申請中があると 409（`:330`）、**同じ日に override があれば種類を問わず 409**（`:348`・時間変更の日も止まる）。
- `pending_requests.request_type` の DB の CHECK は **mig0013 の旧 9 種のまま**（`alembic/versions/0013_v2_pending_requests.py:57-67`）。`staff_status_update`・`patient_status_update`・`patient_visit_add` は本番で保存に失敗するはず（別件のバグ・§8-1）。
- 休みの表 `staff_weekly_overrides`（`app/models/staff.py:130-156`）: (職員, ISO 年, ISO 週, 曜日) で 1 日 1 行。`override_type` は String(16)・**DB の CHECK なし**。書き込みで許す値は `off / custom_time / am_off / pm_off`（`app/schemas/staff_overrides.py:31-54`）、読み出しの `WeekOverrideRead`・`OverrideRead` は 4 ラベルの Literal（`:138-165`）＝**知らない値が 1 行でもあると 500**。
- 「🛌休みにする」`POST /schedule/v2/staff-off-week`（`app/api/v1/schedule_v2.py:1813` から）: 休みの登録＋その日の本人の planned 訪問の付け替え（引き受け先なし＝担当なしも可）＋コース担当＋固定イベントの取消を 1 トランザクション・同じ op_group で行う。**`off` 固定**（`:323`・`:2045`）・**過去日は 422**（`:1864-1870`）。本人の訪問の判定 `_owns_primary`（`:1937-1941`）は「primary が正・空ならコース担当」で、`manual_staff_override` を見ない。
- 訪問 1 件の付け替え `visit-assign-staff-week`（`schedule_v2.py:1675-`）: 担当を外すと `manual_staff_override=False`（`:1736-1741`）＝コース担当に読み替えられる。付け替え先が休みかは見ない。
- 「戻る」（op_log）は**ユーザー×ISO 週**ごと（`app/services/op_log_service.py:9-12`）。休みの upsert `set_staff_override`（`:1007-1062`）は種類・理由・時刻だけを控え、undo もそれだけを戻す（`:1064-`）。
- 職員 Excel の書き出し→全置換で半休が消える（`app/services/staff_excel/schema.py:122-123`・`:340-369` が `off/custom_time` だけ・`replace_all.py:1627-1636` が全行を物理削除）。
- 画面: スマホ `/m/leave`（`override_type:'off'` 固定・`app/(mobile)/m/leave/page.tsx:156`）・`/m/shifts`（`lib/shift-calendar.ts` は午前と午後を `partial` にまとめ、半休を出勤に数える）・PC `admin/pending-requests/_components/StaffLeavePanel.tsx`・訪問モニターは `off / custom_time` しか表示しない（`components/monitor/MonitorTimeline.tsx:435-441`・`:733-742`）。
- 祝日: 週のコピーの警告用に `app/services/scheduling/jp_holidays.py` が既にある（固定日・ハッピーマンデー・春分秋分・**振替休日・国民の休日**。`holidays_of_year` `:62`・`holidays_between` `:80`）。
- `staff_events` の時刻は **JST の壁時計をそのまま**入れる決まり（送信は `datetime.combine` と `.time()` で壁時計として扱う `app/services/kaipoke/events_outbound.py:96-97`・`:136-137`）。照合ではこの約束で読む（L1 は確かめ済み）。

---

## 2. データの持ち方

### 2-1. 休みの種類（`staff_weekly_overrides.override_type`）
| 値 | 画面 | 勤務できない時間 | 休日数 | 有給 | カイポケの件名と時刻（送るとき） |
|---|---|---|---|---|---|
| `off` | 公休（表・スマホでは「休」） | 終日 | 1 | — | 「休み」・その人の勤務時間（**型で勤務しない曜日なら営業時間 `config.business_start`〜`business_end`**＝既定 9:30〜18:00） |
| `am_off` | 午前休 | 〜13:00 | 0.5 | — | 「AM休み」・始業〜13:00 |
| `pm_off` | 午後休 | 12:30〜 | 0.5 | — | 「PM休み」・12:30〜終業 |
| `paid_off`（新） | 有給 | 終日 | **0**（数えない） | 1 | 「有給」・その人の勤務時間（型で勤務しない曜日なら `config.business_start`〜`business_end`） |
| `custom_time` | 時間変更（今のまま・**休みではない**） | start〜end の外 | 0 | — | 送らない |

- 定数と判定は**段階 0 の `app/core/leave.py`**（フロントは `lib/leave.ts`）を使う: `FULL_DAY_OFF`・`HALF_DAY_OFF`・`LEAVE_TYPES`・`AM_OFF_UNTIL`・`PM_OFF_FROM`・`leave_window`・`classify_kaipoke_leave`・`is_leave_event`。**ほかの場所に 13:00・12:30・`== "off"` を書かない**（段階 0 §0-1）。
- 休みの段で `app/core/leave.py` に足すもの（段階 0 の値は変えない）:
  - `visit_hits_leave(type, start, end) -> bool`: 終日は常に真／`am_off` は**開始が 13:00 より前**／`pm_off` は**終了が 12:30 より後**（終了が空の訪問は開始で判定し、開始が 12:30 以後なら真）。
  - `kaipoke_send_window(type, shift_start, shift_end, is_working_day) -> (start, end)`（上の表の右の列）。
- 1 日 1 行のまま（L3）。時間変更のある日に休みを入れると、時間変更は置き換わる（小窓で「時間変更 9:00–15:00 は消えます」と出す）。
- 新しい列（alembic **0099**）:
  - `source` String(16) NOT NULL DEFAULT `'admin'`: `request`（本人の申請を承認）／`admin`（管理者が入れた・継ぎ足し）／`kaipoke`（照合でカイポケから取り込んだ）。表の「足」の印・勤務表の区別に使う。
  - `pending_request_id` UUID NULL（承認元の申請・FK `pending_requests.id` ON DELETE SET NULL）。
- 読み書きのスキーマに「有給」を足す（`schemas/staff_overrides.py:31-54` の入力と対応表、`:138-165` の 2 つの Literal）。フロントの型（`lib/schemas/staff-overrides.ts:19-32`・`lib/queries/staff-overrides.ts:75`）・`lib/shift-calendar.ts` も同時に変える（一覧は付録 A）。
- **override を書くのは `set_staff_override`（`op_log_service.py:1007`）1 か所だけ**にする（付録 A-1 の入口をすべてここへ）。ここで月の「最後に休みを変えた時刻」も更新する（§2-5 `leave_months`・中6）。更新するのは**書く前か後の種類が `LEAVE_TYPES` のときだけ**（時間変更どうしの書き換えでは更新しない）。試験で「`StaffWeeklyOverride(` の生成と `delete(StaffWeeklyOverride` ／ `db.delete(` による削除が `set_staff_override` の外に無い」ことを grep で確かめる（範囲は `backend/app` に限る。`backend/scripts/` と試験は除く）。

### 2-2. 必要休日数と休日数
- **祝日**: `jp_holidays.holidays_of_year` をそのまま使う（振替休日・国民の休日を含む）。表の見出しに祝日名を出すが、**祝日は勤務日**（型どおり出勤。休みにするなら表で入れる）。
- **事業所の休み**（新しい表 `office_closed_days`）: `date` PK・`label`（例「年末年始」）・`created_by`・`created_at`。管理者が「休み・勤務表」の上の「事業所の休み」から入れる・消す。**これも勤務日扱いは変えない**: 表・勤務表で「休」になるのは**休みの登録がある日だけ**（事業所の休みの日は見出しに灰色の帯と名前を出し、「この日の全員に公休を入れる」ボタンで登録できる）。数えるのは必要休日数だけ（中4）。
- 必要休日数（その月）= 次の日の数:
  - 土曜・日曜（土曜の祝日は土曜として 1 日・日曜の祝日はそれ自体は数えず、振替休日が平日に出るのでそちらを数える）
  - 月〜金の祝日（振替休日・国民の休日を含む）
  - 月〜金の事業所の休みのうち、祝日でない日
  - 例（試験の正解）: 2026 年 9 月 = 土 4＋日 4＋祝 3（9/21・9/22 国民の休日・9/23）= **11**／10 月 = 土 5＋日 4＋祝 1（10/12）= **10**／11 月 = 土 4＋日 5＋祝 2（11/3・11/23）= **11**（お客様の勤務表の「祝1〜祝3」の数と一致）。
- 休日数（職員・その月）= Σ 日ごとに:
  - 登録が `off` → 1／`am_off`・`pm_off` → 0.5（ただし型で勤務しない曜日なら 1）／`paid_off` → 0（有給の日数として別に数える）
  - 登録なしで、型（`staff_shifts.is_on=false`）の曜日 → 1
- 常勤／非常勤: `staff.employment_type`（`full_time` / `part_time`、既定 `full_time`）を足し、非常勤は「対象外」と表示。
- スマホには「12月の休み N 日」だけ出す（必要休日数・あと何日は出さない）。

### 2-3. 申請
- `request_type` は **`staff_off` のまま**。種類は payload の `override_type`（`off/am_off/pm_off/paid_off`、変更のお願いだけ `cancel` も可）。作成時に検証し、それ以外は 422。
- CHECK 制約を今の 12 種に貼り直す（§8-1）。
- 締め切り: 対象日の月の前の月の 20 日 23:59（JST）を過ぎた申請、または確定済みの月への申請は `payload.kind = 'change'`（変更のお願い）として受け付け、理由（`payload.note`）を必須にする。管理者の一覧では「変更のお願い」の印。
- 変更のお願いの中身: **種類を変えてほしい**（`override_type` = 新しい種類）／**休みを取り消してほしい（出勤したい）**（`override_type = 'cancel'`・その日に休みの登録があるときだけ作れる）。`cancel` の承認は `cancel_staff_leave`（§3-3）。
- **重複の止め**（`_ensure_staff_off_creatable` `pending_requests.py:290-350`）:
  - 「同じ日に申請中がある」の 409（`:330`）は種類によらず残す。
  - 「同じ日に override がある」の 409（`:348`）は、**その行が休みの種類（`LEAVE_TYPES`）のときだけ**（時間変更の日は休みで置き換えられるので止めない・中7）。さらに `kind='change'` のときは当てない（変更のお願いは登録済みの日が相手・M4）。

### 2-4. 月の確定
- 今の `staff_shift_confirmations`（職員×月で 1 行）はそのまま使い、確定 POST を「月＋職員の一覧」で受けられるようにする（中身は職員ごとに upsert・通知も職員ごと）。
- 確定は「通知した記録」。確定後の変更を止めるロックは作らない（表で変えて再確定）。
- 確定と同時に: ①送ってよい未送信の休みをカイポケへ送る**ジョブを始める**（§7-4-4・`op='leave-outbound'`・非同期。確定の API の中では送らない・送る前に一覧で確認・**その月の照合が一度も済んでいなければ送らない**）②勤務表の予定版を作る（§7-2）。
- 実装（#13）: 確定の記録と通知を commit してから、新しいトランザクションで ① と ② を行う。どちらも失敗しても確定は成功のまま（応答の `outbound: {started, job_id?, count, reason?}`・`kinmu_sheet: {created, kind, sheet, reason?}` に理由。予期しない例外もログに残して 500 にしない）。**一部の職員だけを確定したとき（`staff_ids`）は、送るのもその職員の休みだけ**（ディレクター決定・`build_leave_send_plan(staff_ids=…)`）。予定版は月全体。`staff_ids=[]` は 422。

### 2-5. 新しい表（alembic 0099・`down_revision='0098'`）
| 表 | 中身 | 使う節 |
|---|---|---|
| `office_closed_days` | 事業所の休み（日付・名前） | §2-2 |
| `staff_leave_kaipoke` | 職員×日付で 1 行。`staff_id`・`leave_date`・**`sent_type`**（カイポケへ最後に送った／カイポケにあると確かめた種類・NULL＝無し）・**`baseline_pending`**（bool・照合前）・`sent_at`・`sent_by`・`kaipoke_external_id`・`updated_at`。UNIQUE(staff_id, leave_date) | §7-4 |
| `kaipoke_leave_judgements` | 件名で種類が分からないカイポケの予定に管理者が選んだ種類。`staff_id`・`event_date`・`content_hash`・`subject`・`starts`・`ends`・`chosen_kind`（`not_leave`/`off`/`am_off`/`pm_off`/`paid_off`）・`decided_by`・`decided_at`。UNIQUE(content_hash) | §7-4 |
| `leave_compare_runs` | 照合の**差分の結果だけ**（段階 0 §0-6）。`month`・`kaipoke_job_id`（段階 0 の `KaipokeJob`）・使った材料の `fetched_at`（週ごと・実績 CSV）・差分（JSON）・`diff_count`・`planned_send_count`・`unconfirmed_days`・`created_by`・`created_at` | §7-4 |
| `leave_months` | 月ごとに 1 行。`month` PK・`required_hours`（常勤の勤務すべき時間数）・**`last_leave_changed_at`**（その月の override の追加・変更・削除で必ず更新・中6）・`last_compared_at`・`last_compare_run_id`・`actual_recorded_at`・`actual_recorded_by` | §5・§7-1・§7-2・§7-4 |
| `kinmu_sheets` | 作った勤務表のファイル。`month`・`kind`（`plan`/`final`）・`version`・`drive_file_id`・`url`・`created_by`・`created_at`。**行を消す API を作らない** | §7-2 |
| `google_drive_connections` | 1 行だけ。暗号化したリフレッシュトークン・保存先フォルダ ID・共有ドライブ ID・接続した管理者・`status`（`connected`/`needs_reconnect`）・`last_error` | §7-3 |

- カイポケから取った材料（個別業務・実績 CSV）は**段階 0 の `kaipoke_source_snapshots`（0098）**に置かれる。休みの段では表を作らない・書かない。
- **既存の休みの移し替え（H-f）**: 0099 の中で、今ある休みの種類の override（本番 96 件・全部 `off`）ごとに `staff_leave_kaipoke` を `sent_type=NULL・baseline_pending=TRUE` で作る（=「照合前」）。`leave_months` は override のある月に行を作り `last_compared_at=NULL`。
- 職員の列（`staff`）: `employment_type`・`work_form`（勤務表の区分 A〜D）・`admin_hours_per_day`（管理時間・NULL＝管理者でない）・`kinmu_title`（勤務表の 1 行目に出す役職。任意）。

**休みの送信の状態**は列に持たず、いまのらく助の休み（R）・`sent_type`（S）・`baseline_pending` から**その場で決める**（取り消し・戻る・種類の変更のどれでも食い違わない）。カイポケ（K）まで入れた全通りの表と遷移は §7-4-3。
| R（らく助） | S（sent_type） | 状態 |
|---|---|---|
| X | NULL・`baseline_pending` | **照合前**（送らない。照合で決める） |
| X | NULL | **未送信** |
| X | X | **送信済み** |
| X | Y（≠X） | **カイポケで要対応**（カイポケの Y を消して X を送り直す） |
| 無し | X | **カイポケで要対応**（カイポケの X を手で消す） |
| 無し | NULL | （何もない・行は消す） |

---

## 3. 休みを入れる・取り消す共通の処理

### 3-1. 入口（M1・網羅は付録 A-1）
休みの種類（`LEAVE_TYPES`）を書く入口は、すべて共通の処理 `apply_staff_leave` / `cancel_staff_leave`（新しい `app/services/staff_leave.py`）を通す。時間変更 `custom_time` は今のまま（訪問を動かさない）。行の書き込みは中で `set_staff_override` を使う（§2-1）。

| 入口 | 今 | 変えたあと |
|---|---|---|
| 申請の承認（一覧・表・まとめて・`approve-with-edit`）・`create-and-apply` | applier が INSERT だけ（`pending_request_applier.py:235-262`） | applier が `apply_staff_leave(source='request', request=…)`（変更のお願いの `cancel` は `cancel_staff_leave`）を呼ぶ。`PendingRequestApplier.apply` に `actor` と `op_group_id` を渡す（L6・呼び出しは `pending_requests.py:526`・`:735`） |
| 「休み・勤務表」で入れる・種類を変える・取り消す | （無い） | `apply_staff_leave(source='admin')` / `cancel_staff_leave` |
| 照合でカイポケに合わせる | （無い） | `apply_staff_leave(source='kaipoke')`／`cancel_staff_leave`（§7-4-3・`sent_type` も同じトランザクション） |
| 運転席の「🛌休みにする」 | `off` 固定の専用処理（`schedule_v2.py:1813-`） | 種類を選べるようにして `apply_staff_leave(source='admin', to_staff_id=…)` を呼ぶ |
| スタッフ詳細の「休み・時間変更」・エージェントの `staff-day-off` | `POST/PATCH/DELETE /staff/{id}/overrides`（`app/api/v1/staff_overrides.py:182`・`:213`・`:266`）が行を書くだけ | 種類が休みのときは同じ API の中で `apply_staff_leave` / `cancel_staff_leave` を呼ぶ（API の形は変えない）。時間変更は今のまま（`set_staff_override` で書く） |
| 職員 Excel の取込・全置換 | 行を消して作り直す（`replace_all.py:1627-1636`） | §4-3 |
| 「戻る」・「やり直す」（op_log の `set_staff_off`） | 種類・理由・時刻を戻す | §3-4 |

### 3-2. 「明示の担当なし」の表し方（H1・PO 決定 1）
- **決め**: 訪問ごとの担当なしは **`visits.primary_staff_id IS NULL` かつ `visits.manual_staff_override = TRUE`** で表す。意味は「この訪問だけ担当を外した。コース担当に読み替えない」。
- この約束を**すでに守っている箇所**（読み替えない・埋め直さない）:
  - スマホの可視性と名前 `app/api/v1/visits.py:128-145`・`:183-190`・`:212-220`
  - 打刻履歴 `app/services/checkin/history.py:256-262`・`:370`／訪問モニター `app/services/checkin/monitor.py:592-596`・`:754`
  - カイポケ送信の職員1 `app/services/kaipoke/csv_builder.py:342`・`:361-367`
  - コース担当の伝播 `app/services/scheduling/course_staff_mirror.py:74`・`:103`／`app/api/v1/courses.py:418`
  - 自動スタッフ割当の対象外 `app/services/scheduling/layer3_assignment.py:3947-3956`
  - 実現性チェックのミラー検査 `app/services/scheduling/feasibility_check.py:758`
  - ダッシュボード `app/services/dashboard_staff_performance.py:347`・`:380`
  - エージェントの SQL（`coalesce(v.primary_staff_id, case when not v.manual_staff_override … )` の形）: `agent/sql/visits_valid.sql:7`・`visits_by_staff_day.sql:4`・`visits_by_patient.sql:12`・`unassigned_visits.sql:13`・`staff_overlaps.sql:4`・`patient_next_visits.sql:9`・`day_load_vs_staff.sql:6`・`checkin_rate_by_staff.sql:4`・`checkin_arrivals.sql:42`／説明 `agent/AGENTS.md:208`・`agent/sql/README.md:9`・`agent/.claude/skills/rakusuke-ask/SKILL.md:28`
- **守っていないので変える箇所**（コース担当へ読み替えてしまう）。`rg "primary_staff_id or|coalesce\(v.primary_staff_id" backend/app agent frontend/lib frontend/components` で拾い、上の「守っている」SQL を除いたもの＋盤面の行決定（2026-10-20 確認）:
  | 箇所 | 変えること |
  |---|---|
  | `app/api/v1/schedule_v2.py:1937-1941` `_owns_primary` | primary が空で `manual_staff_override` が真なら「本人のものではない」 |
  | `app/services/scheduling/substitute_candidates.py:236-242` `_owner_of` | 同上（担当なしを返す） |
  | `app/services/scheduling/feasibility_check.py:644` | `primary_sid` の読み替えを `manual_staff_override` が偽のときだけに |
  | `app/api/v1/agent.py:154`（`effective_staff_id`） | 同上 |
  | `app/api/v1/agent.py:823`（職員×日の件数・時間） | 同上 |
  | `app/services/scheduling/auto_allocator_v2.py:8152`（`_staff_busy` の主担当） | 同上 |
  | `app/services/kaipoke/inbound.py:1758`（日付変更・予定外の昇格の `follow_staff`） | 同上（明示の担当なしならコース担当を引き継がない） |
  | `agent/sql/accompaniments_week.sql:13` | `coalesce(v.primary_staff_id, case when not v.manual_staff_override then cc.assigned_staff_id end)` に |
  | `app/schemas/visit.py:140` `VisitRead` | `manual_staff_override: bool` を出す |
  | `frontend/components/schedule/v2/CourseDayTablePanel.tsx:1608-1610` | 盤面用の訪問に `manual_staff_override` を渡す |
  | `frontend/components/schedule/v2/StaffWeekBoard.tsx:362-368` | 行の決め方: primary → （manual でなければ）コース担当 → 担当なし |
  | `frontend/components/schedule/v2/cockpit/StaffTimelineView.tsx:350-363` `resolveVisitRowKey` | 同上 |
  - `kaipoke/inbound.py:483` の `sid == uv.primary_staff_id or …` は打刻した職員との比べで読み替えではないので対象外。
- **1 件ずつの付け替え（中2）**: `visit-assign-staff-week`（`schedule_v2.py:1675-`）
  - 担当を外すとき（`staff_id=None`）、その訪問がコース担当の休みの時間帯にかかるなら **`manual_staff_override` を真のまま**にする（`:1736-1741` の `new_manual = payload.staff_id is not None` を「または休みの時間帯」に）。偽にするとコース担当＝休む人に読み替えられるため。
  - 付け替え先がその日終日休み、または半休でその訪問が休みの時間帯にかかるなら **422**「〇〇さんはこの時間は休みです」。
- 担当なしの付け方は休みの種類で変える:
  - **半休**: コースは本人のまま。休みの時間帯にかかる訪問だけ `primary=NULL・manual=TRUE`・本人の VSA を外す。
  - **終日（公休・有給）**: 今の「🛌休みにする」と同じ。コース担当を外し（または引き受け先へ）、訪問は `primary=NULL・manual=FALSE`（コースごと担当なし＝あとでコースに人を付ければ一緒に付く）。
- 担当なしの訪問は**カイポケの週の送信から外れる**（`app/services/kaipoke/rpa_capability.py:192-240`・担当が付くまで送らない）。管理者への通知に「割り当て直すまでカイポケへは送られません」と書く（L5）。

### 3-3. 処理の中身
`apply_staff_leave(db, *, staff_id, day, leave_type, source, note, actor, op_group_id, request=None, to_staff_id=None, acknowledge_constraint_warnings=False, origin, notify=True)` — 1 トランザクション・同じ op_group。`origin` は `leave_page` / `cockpit` / `request_list` / `staff_detail` / `agent` / `kaipoke_compare` / `excel`。

1. override を **upsert**（`set_staff_override`。同じ日にあれば種類を置き換える）。`source`・`pending_request_id`・`reason=note` も書く。`leave_months.last_leave_changed_at` を更新（`set_staff_override` の中）。
2. **今日（JST）より前の日なら、ここで終わり**（H3・PO 決定 5）。訪問・コース・固定イベント・同行には触れない。通知は本人への承認だけ。
3. その日の本人の **planned の訪問**のうち、休みの時間帯にかかるもの（`visit_hits_leave`）を担当なしへ（§3-2 の付け方）:
   - 「本人の訪問」= primary が本人／primary が空・manual でない・コース担当が本人／**2 名体制の 2 人目（`visit_staff_assignments` に本人）**（今の `staff-off-week` と同じ。2 人目は本人の枠だけ外し、相方は残す。M3）。
   - 打刻済み・実施中・完了・取消済みは動かさない（`skipped_visit_ids` で返す）。
   - 引き受け先（運転席だけ）があるときは今の検査（新人・退職・NG・性別）に加え、**引き受け先がその日終日休みなら 422、半休なら移す訪問のどれかが引き受け先の休みの時間帯にかかるとき 422**（`schedule_v2.py:1898-1911` を置き換える）。
4. 終日の休みでコースの担当が本人なら、今と同じくコース担当を外す（その週のその曜日だけ）。半休ではコースは動かさない。
5. 時間帯にかかる固定イベント（`source='fixed'`）に取消の印（終日は全部・半休は時間で判定）。
6. **同行（新人の付き添い・`accompaniments`）は書き換えない**（M3）。本人が同行者に入っている訪問が休みの時間帯にあれば、件数を結果と通知に出す（「同行 N 件はそのままです。職員スケジュールで外してください」）。理由: 同行は op_log で戻せる操作が無く、新人はコースを持たず人数にも数えないため。
7. 通知（同じトランザクション・`notify=False` のときは呼び出し側がまとめて出す）:
   - 管理者全員へ（移した件数が 1 件以上）:「スタッフの〇〇さんは M/D（曜）に N 名の訪問を予定していましたが、未割り当てとしました。職員スケジュールの「（担当なし）」から割り当て直してください。割り当て直すまでカイポケへは送られません。」
   - 本人へ（申請の承認のとき）:「M/D（曜）の〇〇（午前休など）が承認されました」。`app/services/leave_notify.py` に `notify_leave_approved` を足す。
   - 照合の「らく助に入れる」を**まとめて押したとき**は、管理者への通知を 1 通にまとめる（「カイポケに合わせて休みを N 件入れ、訪問 M 件を未割り当てとしました（〇〇さん 11/12 3 件・…）」）。本人への通知は送らない（カイポケで既に決まっている休みのため）。
8. 申請の承認なら `pending_requests` を approved に（今の行ロック・CAS のまま）。

`cancel_staff_leave(db, *, staff_id, day, actor, op_group_id, origin, notify=True)`: override を消し（`set_staff_override(type=None)`）、本人へ今の `notify_leave_cancelled`（`leave_notify.py:99`）を送る。**担当なしへ移した訪問・外したコース・取消の印は戻さない**（戻したいときは職員スケジュールで割り当てる）。申請は承認済みのまま（表のマスに「承認後に取り消し」の印）。カイポケへ送信済みなら、状態は自動で「カイポケで要対応」になる（§2-5）。

**週をまだ作っていない日**: override だけ入れる。週を作るときに、最後の共通の後始末（§4-2）が休みの時間帯の訪問を担当なしにする。

### 3-4. 「戻る」との関係（H6・PO 決定 4）
- **「休み・勤務表」と申請の一覧には「元に戻す」を出さない**（§5-3）。
- それでも op_log には記録する（運転席の「戻る」の一貫性のため）。`set_staff_off` の payload（前向き・逆向きとも）に **`source`・`pending_request_id`・`reason`・`origin`** を足し、`set_staff_override`（`op_log_service.py:1007-1062`）と undo の `_set_staff_override`（`:1064-`）はこれらも戻す。
- 運転席の「戻る」は今どおり**ユーザー×ISO 週の最新の操作**だけを戻す（`op_log_service.py:9-12`）。表で入れた休みも、同じ人・同じ週なら戻りうる。そのときの決まり:
  - 休みが消えても、**申請は承認済みのまま**（「取り消す」と同じ扱い）。表のマスは「承認後に取り消し」。
  - 「戻る」「やり直す」では本人へ通知しない（すぐ戻す操作のため）。
  - `set_staff_override` を通るので `last_leave_changed_at` は更新される（照合の後なら比べ直しが要る）。カイポケの状態は §2-5 の表どおり自動で決まる。
  - 担当なしへ移した訪問・コース・取消の印は op_log どおり戻る（運転席では今までどおり「戻る 1 回で全部戻る」）。

---

## 4. 割り振り（エンジン）と休み

### 4-1. 有給
`off` しか見ていない箇所（付録 A-2）を `FULL_DAY_OFF` で判定するよう置き換える。機械的な変更。

### 4-2. 半休（方式 (c)・PO 決定 1・H2・H-a・H-b）
- **エンジンの形は変えない**: layer3 は「1 人 1 日 1 コース」（`layer3_assignment.py:16-17`）のまま。半休の人もその日のコースを 1 つ持てる。
- **共通の後始末 `settle_leave_unassign(db, *, dates=None, iso_week=None, staff_ids=None, journal=None) -> int`**（`staff_leave.py`）:
  - 受けた範囲（日の集合・ISO 週・職員）の、**今日（JST）以降の日だけ**を見る（中3。過去の日は実績なので触らない）。
  - その日に休みの登録がある職員について、「本人が担当（§3-3 の判定）」で `visit_hits_leave` が真の **planned の訪問**を担当なしにし、本人の VSA を外す。付け方は §3-2・§3-3 の 4 と同じ: **半休**は訪問を `primary=NULL・manual=TRUE`（コースは本人のまま）／**終日（公休・有給）**はその日のコース担当が本人なら外し（その週のその曜日だけ）、訪問は `primary=NULL・manual=FALSE`（コースごと担当なし）。
  - op_log を書く経路では `journal` を渡し、同じ op_group に `set_visit_staff_slot` で記録する。
  - 戻り値は担当なしにした件数。1 件以上なら管理者へ §3-3 の 7 と同じ文面で通知。
- **呼ぶ場所（H-a）**: 経路ごとに足すのではなく、**訪問を作る処理・担当を書く処理の最後で必ず呼ぶ**。`rg "(^|[^A-Za-z0-9_.])Visit\(" backend/app` と担当を書く箇所（`primary_staff_id =`・`.values(primary_staff_id=`・`assigned_staff_id =`）から作り直した一覧（2026-10-20）:
  | 処理（入口） | 場所 | 呼ぶ |
  |---|---|---|
  | 週の生成（`generate-week-only`） | `app/services/scheduling/layer1_expander.py:1222`・`:1462` | ○（週） |
  | 自動スタッフ割当（`assign-staff-only`） | `layer3_assignment.py` `_persist`（`:3927` コース・`:4050` 訪問） | ○（週） |
  | 今週だけの反映（`apply_week_only`・`patient_sync.bulk_apply_week_only…`） | `auto_allocator_v2.py:9382`・`:9408` | ○（日） |
  | 固定枠へ戻す（`reset_visits_to_fixed`・`patient_fixed_visits.py:733`・`schedule_v2.py:1199`） | `auto_allocator_v2.py:9878`・`:10048`・`:10085` | ○（週） |
  | 週のコピー（`copy_week.py:300`） | `app/services/scheduling/copy_week.py:782`・`:816` | ○（週） |
  | ワークシートの週の反映 | `app/services/template_ws/commit.py:363` `week_apply` | ○（週） |
  | 置いて固定（`place-and-fix`） | `app/api/v1/schedule.py:1034`・`:1050` | ○（日） |
  | 特別訪問の置く・戻す | `app/api/v1/special_visits.py:923`・`:1310`・`:1321` | ○（日） |
  | 申請の反映（臨時の訪問・振替） | `pending_request_applier.py:856`・`:965` | ○（日） |
  | 訪問の作成（`POST /visits`） | `app/api/v1/visits.py:926` | ○（日） |
  | 今週だけ動かす | `app/api/v1/schedule_v2.py:5372` `_apply_visit_move_week_only` | ○（日） |
  | コースの担当の変更（`PATCH /courses`） | `app/api/v1/courses.py:394-421`（伝播は `course_staff_mirror.py:107` も） | ○（日） |
  | 旧の自動配置（v1 `auto_allocate`） | `app/services/scheduling/auto_allocator.py:1375`・`:1824` | ○（週） |
  | 1 件ずつの付け替え | `schedule_v2.py:1740` | ×（休みの相手へは 422・§3-2 中2） |
  | 打刻で作る予定外の訪問 | `visits.py:1869` `adhoc_checkin` | ×（実績・planned ではない） |
  | 戻る・やり直す | `op_log_service.py:792`・`:835`・`:924`・`:1188` | ×（前の状態へ戻すだけ。漏れは下の検知で出る） |
  | カイポケの取込・置換・取り込み前に戻す | `kaipoke/inbound.py:1280`・`:1346`・`:1383`・`:1857`・`:1888`／`replace_inbound.py:546`・`:565`・`:613`／`inbound_snapshot.py:222` | ×（**カイポケが正**・H-b） |
  | プール一括の試算 | `pool_bulk_inserter.py:408`・`:426` | ×（メモリ上の試算で書かない） |
- **カイポケの取込（H-b・方式 (i)）**: 取込・置換では後始末を呼ばない（カイポケが正）。代わりに**取込のプレビューに「休みの時間帯に担当が付く訪問 N 件」**（職員・日・時刻の一覧）を出し、反映後は下の検知に載る。
- **漏れの検知**: `count_visits_on_leave(db, date_from, date_to)` ＝ 今日以降で、休みの時間帯に本人が担当（§3-3 の判定）の planned の訪問。
  - 実現性チェック（`feasibility_check.py`）に「休みの時間帯に担当が付いている訪問」を足す（今は休みを見ていない）。
  - 「休み・勤務表」の上に「休みの時間帯に担当が付いている訪問 N 件」（押すと一覧・職員スケジュールへのリンク）。
- **週を作り直したとき**（らく助の中では休む人に戻らない）:
  - 自動スタッフ割当だけをやり直す: 担当なしの訪問は `manual=TRUE` なので割当の対象外（`layer3_assignment.py:3947-3956`）＝担当なしのまま。割当の最後の後始末も通る。
  - 「一斉未割当」（`auto_allocator_v2.py:10435`・`:10523-10540` が manual を外す）→ 自動スタッフ割当: 割当の最後の後始末がもう一度担当なしにする。
  - 週を空にして作り直す（`/v2/weeks/{y}/{w}/clear` → `generate-week-only` → `assign-staff-only`）: 生成と割当のそれぞれの最後で後始末。コースが別の人へ行けば、その人が担当する。
- **割当の好み**: layer3 の StaffInfo に日ごとの半休（`am_off`/`pm_off`）を持たせ、コストに「休みの時間帯にかかるそのコースの訪問の数 × 重み」を足す（午後休の人には午前の多いコースが行きやすい）。ハードな制約にはしない（(c) のとおりコースは持てる）。`load_active_staff`（`layer3_assignment.py:3195-3245`）で半休を読む。
- **「0.5 人」の意味**: **休日数を数えるときだけ** 0.5 日。エンジンの人数（`count_active_staff_per_weekday`＝その日のコースの数の目安・`auto_allocator_v2.py:3235-3330`）では半休の人も **1 人**（コースを持つため）。終日休み（有給を含む）は 0 人。
- 候補の検査（`propose_slots_service.py:374-403`・`substitute_candidates.py:391-416`・`:484-496`）: 半休の人を候補に出すとき、その訪問が休みの時間帯にかかれば「休み」（`off` の理由コード・`schemas/substitute.py:31`）として × にする。かからなければ ◎/△ のまま。代わりの候補はコース単位の訪問の束で判定するので、束のどれか 1 件でも休みの時間帯にかかれば束ごと ×（厳しいまま・2026-10-21 レビュー LOW-2。午前だけ引き受ける等は 1 件ずつの付け替えで行う）。
- 固定イベントの不参加（`staff_event_defaults.py:100-143`）: 終日は有給も含める。半休は時間で判定する。

### 4-3. 職員 Excel（M7）
- 書き出し: 勤務例外のセルに「午前休」「午後休」「有給」も書く（`schema.py:122-123` の `OVERRIDE_TYPE_VALUES`・`format_override_cell` `:340-353`・`parse_override_cell` `:356-`・`exporter.py:204-240`）。
- 全置換（`replace_all.py:1627-1636`）: **休みの行を全部消して作り直すのをやめ、差分で反映**する。同じ (職員, 週, 曜日) の行が同じ種類・時刻なら触らない（`source`・`pending_request_id` を残す）。種類が変わった・増えた休みは `apply_staff_leave(source='admin', origin='excel', notify=False)`、消えた休みは `cancel_staff_leave(notify=False)`、最後に管理者への通知を 1 通。時間変更は `set_staff_override` で行だけを書く。
- 取込（`importer.py:1151-`）も同じ。プレビューに「休みの日の訪問 N 件を担当なしへ移します」を出す。

---

## 5. 「休み・勤務表」ページ

- 場所: メニュー「申請履歴」の下に **「休み・勤務表」**（管理者のみ）。URL `/admin/leave`。メニューに未処理の申請の件数。
- 上: 月の切り替え・確定の状態・「押すと入るもの」（選んで操作／公休／午前休／午後休／有給／取り消す）・「事業所の休み」・「カイポケと比べる」（最後に比べた時刻と、その後に休みを変えたか）・「この月を確定して通知」・「実績を記録する」（§7-4-2 の条件を満たすまで押せない・理由を横に出す）・「休みの時間帯に担当が付いている訪問 N 件」（§4-2）。
- 中央: **職員×日付の表**。
  - 見出し: 日・曜日・祝日（赤・名前）・事業所の休み（灰色の帯・名前・「全員に公休を入れる」）・その日の申請の件数「申N」。
  - マス: 公休（塗り）・午前休（上半分）・午後休（下半分）・有給（別の色）・申請中（枠＋「?」）・型で勤務しない曜日（灰色）・管理者が入れた休み（右上に「足」）・カイポケから取り込んだ休み（右上に「カ」）・承認後に取り消し（斜線）・週を作り済みの日は訪問の件数・カイポケとの差分（橙の点）・送信の状態（照合前＝灰の丸・未送信＝白丸・要対応＝赤の「!」）。
  - 右端: 休日数 / 必要休日数・「あと N 日」（足りないときは黄色）・有給 N 日・申請中 N 件（通ればあと N）。非常勤は「対象外」。
  - 下の行: **その日休む人の数**＝**承認済みの公休・半休・有給（半休も 1 人）**。**型で勤務しない曜日は数えない**・**新人は数えない**（M9・PO 決定 10）。**2 人以上で黄色**。申請中の一覧・マスの小窓でも「承認するとこの日の休み N 人」を出す。
- 右: 一覧を「申請中」と「カイポケとの差分」で切り替える。
  - 申請中（日付順／職員順）: 日付順「11/12（木）申請 3 件・休む人 0 人 → 全部承認すると 3 人（休みが重なります）」・「この日をまとめて承認」／職員順はその人の休日数・「この人をまとめて承認」。各行: 種類・メモ・「変更のお願い」の印（種類の変更／休みの取り消し）・移す訪問の件数・承認／「…」（表のマスへ移って小窓：種類を変えて承認・理由つきの却下）。行に指を置くと表のマスと列が光る。
  - カイポケとの差分: §7-4-3。
- マスの小窓: 申請なら内容・移す訪問の件数・「この日の出勤：今 N 人 → 承認すると N 人」・承認／種類を変えて承認／却下。登録済みなら種類を変える・**取り消す**。過去の日は「訪問は動かしません（休みの記録だけ）」と出す。
- 確定のダイアログ: 申請中の残り・休日数が足りない人・勤務すべき時間数（未入力なら入力欄・前の月の値が初期値）・**カイポケへ送る休みの一覧（§7-4-4）**（その月をまだ比べていなければ「まだカイポケと比べていないので、休みは送りません」）を出し、「このまま確定する」も選べる。
- 下: 勤務表（Google スプレッドシート）の一覧と接続の状態（§7-3）。
- 権限: 管理者のみ（`require_role("admin")`）。`summary` だけ職員も読める。
- API（新設・`app/api/v1/leave.py`・#8 で実装。型は `app/schemas/leave.py`）。月のパスと入力は `YYYY-MM` か月の 1 日（それ以外の日付は 422）:
  - `GET /leave/month/{month}` — 月×全職員を 1 回で返す（職員・型・休み・申請・訪問の件数・祝日・事業所の休み・確定の状態・送信の状態・最新の照合の差分の件数・`last_leave_changed_at`・`last_compared_at`・休みの時間帯に担当が付いている訪問の件数）。クエリの回数は職員・週の数によらず一定。**ISO 週ではなくその月の日付の範囲から (iso_year, iso_week) の組を作って読む**（1 月 1 日が前の年の ISO 週に入る年末年始を落とさない・L2）。その日休む人は在籍（active）の人だけを数える（退職・休職の人の休みは数えない・ディレクター決定）。
  - `GET /leave/month/{month}/summary` — 祝日・事業所の休み・締め切り・確定の状態（職員は本人の分だけ・休みの中身は返さない）。管理者には申請中の件数・足りない人の数・カイポケで要対応の件数も（訪問は読まない軽い数え方）。
  - `POST /leave/day`（入れる・種類を変える）・`DELETE /leave/day?staff_id=&date=`（取り消す・担当なしへ移した訪問の件数を返す）— 中身は `apply_staff_leave` / `cancel_staff_leave`。**その日に申請中があれば、入れる・取り消すとも 409**（申請を先に承認か却下で決めてもらう。申請と表の休みが食い違わないため）。
  - `POST /leave/day/bulk`（「この日の全員に公休を入れる」。`staff_ids` が無ければ非常勤・型で勤務しない曜日の人・在籍でない人・申請中の人・休みが入っている人を除く全員・新人は含む。1 トランザクション・管理者への通知は 1 通）。
  - `POST /leave/requests/approve-batch`（申請 id の一覧）— **1 トランザクションで全部か無し**（ディレクター決定。1 件でも承認できなければ全部やめ、どの申請かを返す。理由: 「この日をまとめて承認」で一部だけ通ると、その日休む人の数と画面の見込みが食い違い、管理者への通知も 1 通にまとめられないため。行ロックは id の順で取る）。`POST /leave/requests/{id}/approve`（`override_type` で種類を変えて承認）・`POST /leave/requests/{id}/reject`（理由必須）。承認は申請一覧と同じ行ロック＋CAS。
  - `POST /leave/month/{month}/confirm`（任意の `staff_ids`・無ければ表の全員。#8 は記録と本人への通知だけ。§9 #13 で送信と予定版をつなぐ）・`GET /leave/closed-days?month=`・`PUT /leave/closed-days`・`DELETE /leave/closed-days/{date}`・`PUT /leave/months/{month}`（勤務すべき時間数）・`GET /leave/month/{month}/kinmu-sheets`（作った勤務表の一覧）。
  - 照合・送信・実績の記録・勤務表は §7（照合と送信の API は `app/api/v1/leave_kaipoke.py`）。
- 文字は 14px・補足 12px 以上（`add-visit-anywhere-design.md` §3-5）。

### 5-1. 申請履歴ページの変更
- 右の `StaffLeavePanel` を外し、2 カラムをやめる（ファイルも消す）。
- 一覧の上に「休みの申請 N 件・M月は未確定 → 休み・勤務表で開く」の帯。
- 休みの行に種類（午前休など・変更のお願い）と「表で開く」（その人・その日のマスを開いた状態）。1 件ずつの承認は一覧からもできる（中身は同じ共通の処理）。
- 職員の絞り込みを、UUID の入力欄から職員の選択欄（`StaffCombobox`）に。
- 承認したあと、休みのデータも読み直し、成功のトーストを出す（「元に戻す」は付けない・§5-3）。

### 5-2. ほかの入口
- 新着申請の通知（管理者向け・ベル）→「休み・勤務表」をそのマスを開いた状態で。
- ダッシュボードのカード「M月の休み：申請中 N 件・足りない人 N 人・カイポケで要対応 N 件」。
- スタッフの詳細の「今後の休み・時間変更」の横に「月の表で見る」。

### 5-3. 取り消しと「元に戻す」（モックのコメントの参照先）
- 「休み・勤務表」と申請履歴の**トーストに「元に戻す」は出さない**（H6・PO 決定 4）。取り消しは表のマスの小窓の「取り消す」で行う。
- 取り消したときのトースト: 「取り消しました（〇〇さん M/D）。担当なしへ移した訪問 N 件は自動では戻りません」。
- 運転席の「戻る」との関係は §3-4。

---

## 6. スマホの「休み」ページ

- `/m/leave` と `/m/shifts` を 1 つの「休み」ページにまとめる（古い URL はこちらへ転送）。ホームとマイページの導線は 1 つに。
- 上: 月の切り替え・締め切りの帯（「12月の休みの希望は 11/20（金）まで」）・確定済みなら「✓ 12月は確定しました」。
- 「12月の休み N 日」（数だけ）。
- 種類のボタン 4 つ（公休・午前休 13時から勤務・午後休 12時半から休み・有給）→ 日を押す（何日でも）→ 下の「N 日を申請する」でメモを付けて送る。
- カレンダー: 出勤の日（白）・型で勤務しない曜日（灰色）・祝日（日付を赤・ただし出勤の日のまま）・承認済み（塗り・半休は半分）・申請中（枠）・却下（✕）・選択中（濃い色＋種類）。その日何人休むかは出さない。
- 申請中の日を押すと「取り下げる」。承認済みの日は「変えたいときは管理者へ」。締め切り後・確定後は日を押すと「変更をお願いする」（「種類を変えてほしい」／「休みを取り消してほしい（出勤したい）」・理由が必須・§2-3）。
- 下に「申請と結果」（申請中・承認・却下と理由・承認後に取り消された休み）。
- 送信は今の逐次 POST と部分失敗の扱いを残し、`override_type` に種類を入れる。

---

## 7. カイポケとの照合と勤務表（Google スプレッドシート）

### 7-1. 勤務表の形（M11）
お客様の現行の様式（`Sampledata/らく助反映_勤務表（常勤換算計算あり）.xlsx コピー.xlsx`・git 管理外・シート `2026.9`〜`2026.11` を読んだ）に合わせる。月ごとに 1 ファイル（シート「勤務表」と「集計」）。

**見出し**
- A1「　M月」・右上に**常勤の従業者が当該月勤務すべき時間数**（`leave_months.required_hours`・例 160）と「時間」。A2「従業者の勤務の体制及び勤務形態一覧表」。
- 週の帯: **週は日曜〜土曜**。第1週は 1 日から最初の土曜まで、第2週から日曜始まり（10 月: 第1週 1〜3 日・第2週 4〜10 日）。
- 日付の行・曜日の行。列の右に「暦月の合計」「常勤換算後の人数」「備考」。

**職員ごとの行**（並びは管理者 → 役職のある人 → ほかの常勤 → 非常勤、同じ中は職員の並び順）
- 1 行目: A=役職（`kinmu_title`。無ければ職種）・B=勤務形態の区分（`work_form`・A〜D）・C=所属拠点の略称（応援拠点があれば「稲毛・都賀」）・D=「予定」・日ごとの**予定の勤務時間**。
- 管理者だけ 2 行目: A=職種・C=氏名・D=「管理時間」・勤務した日ごとに `admin_hours_per_day`。この人の「予定」「実績」の時間は**勤務時間 − 管理時間**（見本: 8 時間の日に 4.0 と 4.0）。半休の日の管理時間は 0（勤務時間はすべて看護へ）。日ごとの例外（見本の 10/26 の 8.0／0）はらく助では作らず、出来たスプレッドシートで手で調整する。
- 次の行: A=職種・C=氏名（管理者以外）・D=「実績」・日ごとの**実績の勤務時間**（予定版では空・確定版で入れる）。
- 次の行: **休みの番号**（下の規則）。
- 氏名はらく助の職員名で書く（見本の「高梨 佳子」はらく助では「髙梨 桂子」）。

**日ごとの勤務時間のセル**
| その日 | 予定・実績のセル |
|---|---|
| 出勤（型どおり。祝日・事業所の休みでも休みの登録が無ければ出勤） | 型の時間 − 休憩。休憩は**勤務の長さが 6 時間を超える日だけ 60 分**（9:00〜18:00・8:30〜17:30 → 8.0。見本と一致） |
| 時間変更 | その時刻で同じ計算 |
| 午前休 | 13:00〜終業（例 9〜18 時の人 = 5.0）・休憩なし |
| 午後休 | 始業〜12:30（例 9〜18 時の人 = 3.5・8:30〜17:30 の人 = 4.0）・休憩なし（PO 決定 10-20。見本は文字「PM休」で合計に入っていないが、らく助は時間を入れる） |
| 公休の登録がある日・型で勤務しない曜日 | 「休」 |
| 有給 | 「休」（番号の行に「有給」。見本の 11 月どおり） |

（事業所の休みの日は、休みの登録があるときだけ「休」。登録が無ければ出勤として時間を書く・中4。）

**休みの番号の行（見本の 9〜11 月から読み取った規則）**
1. 終日の休み（公休の登録・型で勤務しない曜日）を、**その週（日曜〜土曜・前の月の日を含む）の中で日付の順に** `1`・`2` と番号を付ける（例: 川名様 10 月 = 3 日（土）は 9/27（日）に続く 2 つ目なので `2`、4 日（日）は `1`）。
2. 週の 3 つ目以降の終日の休みは **`祝n`**。n は**その月の中の通し番号**（9 月は祝1〜祝3・11 月は祝1・祝2）。
3. 半休は `0,5`（カンマ）で番号を取らない。有給は `有給` で番号を取らない。
4. 見本の誤りとみられるもの（規則を優先し、試験の正解も規則で作る）: 川名様 11 月の `祝1` が 2 回・11/26 の「休」に番号なし・合計の式で行が抜けている（10 月の予定の合計に宇田川様の行が無い・実績の合計に一部の行が無い）。

**合計と常勤換算**
- 「暦月の合計」= その行の日ごとの合計。「常勤換算後の人数」= 合計 ÷ 勤務すべき時間数を**小数第 2 位で切り捨て**（様式の注記どおり。見本の式は切り捨てていない）。
- 下の「合計」行: 予定・実績それぞれ全員の合計（らく助は全員の行を足す）。
- 注記の行（区分 A〜D の説明・常勤換算の説明）は見本の文をそのまま書く。
- 勤務すべき時間数が未入力の月は、勤務表を作る前に入力を求める（§5 の確定のダイアログ）。

**集計シート**: その年の 1 月〜その月の、職員×月の休日取得数（§2-2 の休日数・有給は数えない）。

**試験の正解（M11）**: 見本の 10 月分（9/27〜9/30 の休みを含む）の型と休みを入力にし、**上の規則で手計算した 10 月の勤務表**（番号の行・予定の時間・合計・常勤換算）を正解として、生成の関数（`app/services/kinmu_sheet/build.py`・純粋な関数で表の値だけを作る）を試験する。実装者が手計算の表を作り、試験のデータに置く。

### 7-2. 作る時期（PO 決定 7・8）
1. 「この月を確定して通知」→ その月の**予定版**を新しいファイルで作る（`勤務形態一覧表_YYYY年MM月_予定`）。
2. 月の途中で再確定 → **予定版を新しいファイルで作り直す**（`…_予定_第2版`・`第3版`…）。前の版のファイルは残す（Google の版の履歴に頼らない・M10）。
3. 照合で差分 0 件・送る予定 0 件になったあと、管理者が **「実績を記録する」** を押す → 照合を済ませた休みで実績の行を入れた**確定版**を作る（`…_確定`）。以後は書き換えない（間違えたときは管理者が理由を入れて「確定版を作り直す」＝ `…_確定_第2版` を作る・前の版は残す）。**自動では作らない・cron は作らない**。
4. 作ったファイルは `kinmu_sheets` に記録し、「休み・勤務表」の下に一覧で出す。**作ってから 5 年間はらく助から消さない**（消す API・ボタンを作らない。ドライブ側で消されたら一覧に「ドライブに見つかりません」と出す）。
5. Google ドライブに接続していないときも確定・実績の記録はでき、勤務表だけ「未作成（ドライブ未接続）」として残る。接続後に「作る」ボタンで作れる。
6. 実装（#13）: 作り直し・「作る」は `POST /leave/month/{month}/kinmu-sheets/rebuild {kind, reason}`（**理由は必須**・確定版は実績を記録した月だけ・**実績の記録の時刻は変えない**）。同じ種類の**最新の版が「未作成」なら新しい行は足さずその行で作る**（確定のたびに未作成の行が増えない・同時に押されたときは行の鍵で 1 つだけ作る）。「実績を記録する」は月に 1 回だけ（2 回目は 409 `already_recorded`・作り直しは rebuild）。一覧は `GET /leave/month/{month}/kinmu-sheets`。

### 7-3. Google ドライブへの接続（M10）
- **形式**: Google スプレッドシート（xlsx ではない）。Drive API の `files.create`（`mimeType=application/vnd.google-apps.spreadsheet`・`parents=[保存先フォルダ]`）で作り、Sheets API の `batchUpdate` で値と書式（罫線・塗り・列幅・セルの結合）を書く。
- **方式**: OAuth（「Google でログイン」と同じ許可の画面）。権限は **`drive.file`** だけ（らく助が作った・選ばれたファイルだけに触れる）。
- **保存先フォルダの選び方**: `drive.file` ではフォルダの一覧を読めないので、**Google Picker** でフォルダを選んでもらう（`DocsView` の `setSelectFolderEnabled(true)`・`setIncludeFolders(true)`・`setEnableDrives(true)`）。Picker には API キーとアプリ ID（Cloud のプロジェクト番号）が要る。
- **共有ドライブ**: 保存先は共有ドライブを推奨（持ち主が辞めても残る）。Drive API の呼び出しには必ず **`supportsAllDrives=true`**（一覧には `includeItemsFromAllDrives=true`）を付ける。
- **トークン**: リフレッシュトークンは Fernet で暗号化して `google_drive_connections` に保存（手本 `app/services/kaipoke/credentials.py`）。`invalid_grant` などで使えなくなったら `status='needs_reconnect'` にし、**管理者全員へ通知（ベル）**＋「休み・勤務表」に「Google ドライブにもう一度接続してください」の帯。勤務表は「未作成」として残し、接続し直したあと「作る」で作れる。
- **事前準備（PO の Google アカウントで 1 回・§10）**: Google Cloud のプロジェクト・Drive API と Sheets API と Picker API の有効化・OAuth の同意画面・許可するリダイレクト `https://carelink.kaipoke-api.net/api/v1/google-drive/callback`・API キー（Picker 用・HTTP リファラーで本番の URL に限定）・**「本番」へ公開**（「テスト中」のままだと許可が 7 日で切れる）。
- **Cloudflare**: OAuth の戻り先 `/api/v1/google-drive/callback` に Google からのリダイレクトが届くかを確かめる。ログイン中の管理者のブラウザが戻ってくる経路なので、今の公開ルート（5 本・5 本目 `/agent-relay`）の扱いと同じ設定でよいかを、デプロイ前に本番で確かめる（届かなければ 6 本目として足す）。
- **鍵の置き場**: OAuth のクライアントシークレットと API キーは VPS の secrets の置き場に置く。**ディレクトリの権限は 711 のまま**（700 にすると backend から読めない・音声記録の Vertex が止まった前例）。置いたあと backend のコンテナから読めることを確かめる。
- **時刻**: 勤務表の作成日時は JST で表示。定期処理は作らない（作る場合は VPS が UTC なので cron は UTC で書く）。
- 注意: 会社の Google Workspace で外部アプリが止められていると接続できない（§10 のお客様確認）。サービスアカウントはマイドライブに作れないので使わない。
- ライブラリ: **新しいライブラリは足さない**。Drive と Sheets は `httpx` で REST を直接呼ぶ（`app/services/google_drive/`・#12 で `google-api-python-client` から変更）。フロントは Picker の script を読み込む。

### 7-4. カイポケとの照合（H4・H5・H-c・H-d・H-f・PO 決定 2・3）

#### 7-4-1. カイポケでの休みの実際（2026-10-20 調査・読むだけ）
- 休み専用の機能（勤怠・休暇）は使っていない。**職員スケジュール画面の個別業務**（件名は自由・時刻あり・終日や種類の欄なし）。予定/実績の CSV には個別業務は入らない（`kaipoke-event-inbound-design.md:29-31`）。
- RPA は個別業務の**取得と作成のみ**（変更・削除なし）・**同時に 1 本だけ**。
- 本番の件名は「休み」140 件・「PM休み」2 件のみ（7/20〜10/31）。override との重なり（7/21〜10/31）: 両方 88・カイポケだけ 53・らく助だけ 8。
- 10 月を勤務表（Sampledata）と照合: 終日＝「休み」＋勤務時間／午後休＝「PM休み」12:30〜17:30／午前休＝「休み」9:00〜13:00（1 件）／日曜は取り込まない。食い違い: 小西様 10/13〜16・20（カイポケだけ休み）、髙梨様 10/21（カイポケは午前休・勤務表は出勤）→ §10 のお客様確認。
- 既存の取込の不具合: 非同期の取込の通常の経路で `covered_dates` を渡していない（`app/api/v1/integrations.py:4037`。同期版 `:3835` と別経路 `:3981` は渡している）→ 月をまたぐ週で、表示されなかった日のカイポケ行を誤って消すおそれ。今回あわせて渡す（§8-6）。

#### 7-4-2. 決まり
- **カイポケが正**。差分はカイポケかららく助へ取り込んでなくす。**「らく助に合わせる」で差分を解決済みにする例外は作らない**（らく助の側が正しいなら、カイポケを手で合わせて比べ直す）。
- 「実績を記録する」は次がすべてそろったときだけ押せる:
  - 最新の照合が**その月の全部の週（月〜土）を取れている**（未確認の日が 0）
  - **差分 0 件・送る予定 0 件・照合前 0 件**
  - **`leave_months.last_leave_changed_at` が `last_compared_at` より前**（照合の後に休みを足した・変えた・消したら比べ直す・中6）
  - （実装 #13 で足した条件）**月の最後の日が過ぎている（JST）**（月の途中では記録しない・ディレクター決定）・**まだ記録していない**・**種類を選んだだけの差分が無い**（選んだ種類は次の照合で効くので比べ直す・送信の「比べ直してください」と同じ判定）・**使った材料が新しい**（`fetched_at` がすべてその照合のジョブの開始以降）
  - 判定は `leave_compare.record_actuals_blockers` の 1 つ。欠けていれば 409 で理由の一覧（code: `month_not_ended`・`already_recorded`・`not_compared`・`diffs_remaining`・`recompare_after_choose`・`unconfirmed_days`・`planned_sends`・`changed_since_compare`・`stale_material`）。月の表の `record_actuals` も同じ判定。
- 日曜は取り込まないので照合の対象外（型で休み）。

#### 7-4-3. 「カイポケと比べる」
**材料の取り直し（段階 0 を使う・M6）**
1. `POST /leave/compare/start {month}` → 段階 0 の仕組み（`app/services/kaipoke/sources.py`）で `KaipokeJob`（`params.op='leave-compare'`）を始める。中身は、その月にかかる ISO 週の `fetch_week_tasks`（個別業務）と、その月の `fetch_month_csv(kind='actual_csv')`。結果は `kaipoke_source_snapshots` に置かれる。**休みの段は自分で RPA を呼ばない**。
2. ほかの RPA のジョブが動いていれば段階 0 が 409 を返す（「カイポケの処理が動いています。終わってから押してください」）。塞がっていたときの待ちと再試行、一部の失敗（`result_summary.failed[]`）は段階 0 の決まりどおり。17〜18 時台は注意を出す。
3. 画面は `KaipokeJob` の状態を数秒おきに読み、進み具合「3/6 週」を出す（Cloudflare の 100 秒の制限を避ける）。
4. **材料が新しいと認める条件 = `fetched_at` が今回の leave-compare ジョブの開始以降**（段階 0 §0-2）。受け入れ枠などで先に取った材料でも、この条件を満たさない週は使わない。画面に週ごとの `fetched_at` を出す。
5. ジョブが終わったら、`kaipoke_source_snapshots` から読んで差分を作り、`leave_compare_runs` に保存する（差分の結果だけ）。`leave_months.last_compared_at` を更新。
6. **新しくない週（`fetched_at` がジョブの開始より前＝今回の取り直しが失敗して前の行が残った週を含む）・材料の無い週・`status='failed'` の週の日は「未確認」**。未確認は差分に数え、**差分 0 件にしない**（「この週を取り直す」）。`tasks` の `covered_dates`（段階 0 §0-2）に無い日（月をまたぐ週で表示されなかった日）も「未確認」。実績 CSV が新しくない・`failed` なら、実績を使う差分は「未確認」（新しい `empty` は実績 0 件として扱う）。
7. 職員の結び付けは段階 0 §0-4（空白を除いた名前・`name_match.py`）。結び付かない名前は差分「カイポケの職員『〇〇』がらく助に見つかりません」として出す。

**件名＋時刻で種類を決める**: 段階 0 の `app/core/leave.py` の `classify_kaipoke_leave(subject, start, end, shift_start, shift_end)` を**そのまま使う**。規則の本体はそこにあり（段階 0 §0-1 が正）、休みの段では規則を書かない・足さない。結果の扱いだけを決める:
- 戻り値が None（休みではない）→ 照合に使わない。`off`/`am_off`/`pm_off`/`paid_off` → その種類を K とする。
- `"unknown"`（休みの語を含むが判断できない）→ **種類がわからない**。管理者が選んだ記録（`kaipoke_leave_judgements`）が同じ `content_hash` であればそれを使う。
- 規則の全文と試験の正解は段階 0 §0-1 の表だけを見る（例: 「年休」→ `paid_off`・休み 12:30〜17:30 → `pm_off`・休み 9:00〜13:00 → `am_off`・「休日当番」→ None・休み 10:00〜11:00 → `unknown`）。
- `content_hash` の材料は 1 通り: **sha256(職員 id ‖ 日付 ‖ 件名 ‖ 開始 HH:MM ‖ 終了 HH:MM)**（区切りは `\x1f`・件名は前後の空白を除く）。**内容が変わったらまた出す**。
- 同じ職員・同じ日に休みの予定が 2 件以上 → 差分「同じ日に休みの予定が 2 件あります」（カイポケで要対応）。

**全通りの表と遷移（H-d）**: R＝らく助の休み、S＝`sent_type`、K＝カイポケの休み（種類の判定と管理者の選択のあと）。X・Y・Z は互いに違う種類。「合わせる」ボタンの処理は、**override の変更と `sent_type` の書き込みを同じトランザクションで行う**（H-c）。

| # | R | S | K | 状態・一覧での出し方 | 合わせ方（ボタン）と遷移 |
|---|---|---|---|---|---|
| 1 | 無 | 無 | 無 | 何もない | — |
| 2 | 無 | 無 | X | 差分「カイポケだけ休み」 | 「らく助に入れる」→ R=X・**S=X** |
| 3 | 無 | X | 無 | 一致（らく助で取り消し、カイポケでも消した）→ 自動で **S=NULL**（行を消す） | — |
| 4 | 無 | X | X | **カイポケで要対応**「カイポケの X を消してください」 | カイポケで消して比べ直す → #3 |
| 5 | 無 | X | Y | 差分「カイポケは Y（らく助は取り消し済み）」 | 「らく助に入れる（Y）」→ R=Y・**S=Y**／カイポケを消して比べ直す → #3 |
| 6 | X | 無 | 無 | **未送信＝送る予定**（差分に数えない）。`baseline_pending` なら **照合前**→ 差分「らく助だけの休み（以前から）」 | 送る予定: 確定でまとめて送る（§7-4-4）・急ぐなら「この休みを送る」→ S=X。照合前: カイポケが正なので「らく助の休みを取り消す」→ #1（カイポケに入れ直したなら比べ直す → #7） |
| 7 | X | 無 | X | 一致 → 自動で **S=X**（送信済み）・`baseline_pending` を外す | — |
| 8 | X | 無 | Y | 差分「らく助は X・カイポケは Y」 | 「カイポケの種類に合わせる」→ R=Y・**S=Y** |
| 9 | X | X | 無 | 差分「カイポケで消されています」 | 「らく助の休みを取り消す」→ R=無・**S=NULL**（#1） |
| 10 | X | X | X | 送信済み・一致 | — |
| 11 | X | X | Y | 差分「送ったあとカイポケで Y に変わっています」 | 「カイポケの種類に合わせる」→ R=Y・**S=Y** |
| 12 | X | Y | 無 | カイポケの Y は消し終えた → 自動で **S=NULL**＝**未送信（送る予定）** | 確定で X を送る → S=X |
| 13 | X | Y | X | 一致（カイポケでも X に合わせ済み）→ 自動で **S=X** | — |
| 14 | X | Y | Y | **カイポケで要対応**「カイポケの Y を消してください（X は送り直します）」 | カイポケで消して比べ直す → #12 |
| 15 | X | Y | Z | 差分「カイポケは Z」 | 「カイポケの種類に合わせる」→ R=Z・**S=Z** |
| — | 任意 | 任意 | 種類がわからない | 差分「『〇〇』の種類を選んでください」 | 選ぶ（休みではない／公休／午前休／午後休／有給）→ 記録して比べ直す（K が決まり、上のどれかの行になる） |
| — | 任意 | 任意 | 未確認 | 「未確認」 | 「この週を取り直す」 |
| — | 休み | 任意 | 休み | 休みの日・半休の時間帯にカイポケの**実績**の訪問がある → 差分「カイポケで要対応：休みと実績が食い違っています」 | カイポケで休みか実績を合わせてから比べ直す |

- 「自動で」の行（#3・#7・#12・#13）は差分を作るときに `sent_type` をそろえる（同じトランザクションで `leave_compare_runs` を保存）。
- 差分に数えるのは: #2・#4・#5・#6 の照合前・#8・#9・#11・#14・#15・種類がわからない・未確認・実績との食い違い・職員が見つからない・同じ日に 2 件。**送る予定**（#6 の未送信・#12）は別に数える。
- 照合の結果と管理者の操作（誰が・いつ・どの差分を・どう合わせたか）は `leave_compare_runs`・op_log・`kaipoke_leave_judgements` に残る。
- 最初の照合では、これまでカイポケだけで入れてきた休み（#2）や照合前の休み（#6）がたくさん出るので、「らく助に入れる」「取り消す」を**同じ職員・同じ月でまとめて押せる**ようにする（通知は 1 通・§3-3 の 7）。

#### 7-4-4. らく助からカイポケへ休みを送る（M5・H-f）
- 送るのは**状態が「未送信」の休みだけ**（照合前は送らない）。さらに**その月の照合が一度済んでいる（`leave_months.last_compared_at` がある）**ときだけ。照合がまだの月は、確定でも休みは送らず「まだカイポケと比べていないので、休みは送りません」と出す。
  - 照合の後に入れた・変えた休みは「未送信」になる（#12 を含む）。照合の前からある休みは照合で「照合前」から外れたもの（#7 で送信済み、または #6 で管理者が判断）だけが残るので、**送るのは照合が済んだ月の、照合で「送る予定」と確かめたもの・照合の後に変わったものだけ**になる。
- **週の個別業務の送信（`events_outbound.py:91-147` の `source='manual'/'fixed'` の予定）とは別の経路**にし、朝会などの予定と混ぜない。
- 送る前に一覧で確認（職員・日付・件名・時刻・送れない理由）。件名と時刻は §2-1 の表（型で勤務しない曜日の公休・有給は `config.business_start`〜`business_end`）。日曜とカイポケの職員 ID が分からない職員は送れない（今の `build_outbound_plan` と同じ理由を出す）。
- 送信は**段階 0 の `KaipokeJob`（`params.op='leave-outbound'`・非同期）**で動かす。`POST /leave/outbound/start {month}` がジョブを作って返し、画面は状態を数秒おきに読む（Cloudflare の 100 秒の制限を避ける）。**確定 API（`POST /leave/confirm`）の中で同期で送らない**: 確定は記録と通知のあと、送る休みがあれば送信のジョブを始めてその id を返すだけ。ほかの RPA のジョブが動いていれば 409（段階 0 §0-3）。
- ジョブの中で、RPA の個別業務の作成（`to_rpa_items` と同じ形・`events_outbound.py:150-163`）を週ごとに順番に呼ぶ（RPA の空き確認・待ち・再試行・120 分の閉じ時間・進み具合の書き戻しは段階 0 §0-3 の決まりどおり）。`external_ref` は `leave:{staff_leave_kaipoke.id}`。
- 1 件ずつ、送れたら（`added`／`skipped_duplicate`）同じトランザクションで `sent_type`・`sent_at`・`sent_by`・`kaipoke_external_id` を書き、`staff_events` に `source='kaipoke'`・`external_id`＝返ってきたキーの行を作る（同じキーの行が既にあれば作らずにつなぐ・今の `promote_sent_events` `:166-` の重複解消と同じ考え）。これで次のイベント取込・照合で二重にならない。
- 失敗した件は未送信のまま（一覧に理由）。

---

## 8. あわせて片付けるもの

1. **申請の種類の CHECK 制約**（別件のバグ）: mig0013 の 9 種を、アプリの 12 種に貼り直す（0099 に含める）。試験は `create_all` だとモデルに CHECK が無く見つからないので、モデル側の CHECK もそろえる。
2. **反映処理の upsert 化**（§3 で解決）。
3. **訪問モニター**で半休・有給を表示（`MonitorTimeline.tsx:435-441`・`:733-742`・`MonitorRowPanel.tsx:66`。`monitor.py:968` は種類をそのまま渡しているので、`schemas/visit_monitor.py:210-216` の説明を 6 種に）。
4. 古い型定義 `frontend/lib/schemas/staff.ts:154`（`off / custom_time` だけ・未使用）を消す。
5. 使われていない `components/schedule/v2/PendingRequestPanel.tsx` を消す。
6. イベント取込の非同期の経路で `covered_dates` を渡す（`integrations.py:4037`・§7-4-1）。

---

## 9. 実装の順序（段階 0 が済んでいる前提・一気に作り、一気にデプロイ）

前提（段階 0・ディレクター）: `app/core/leave.py`・`lib/leave.ts`・`is_leave_event` の置き換え・`kaipoke_source_snapshots`（0098）・`app/services/kaipoke/sources.py`（`KaipokeJob` の `op='leave-compare'`・`'leave-outbound'`）。休みの中で段階を分けたデプロイはしない。作る順番と依存関係だけを決める（**依存は必ず前の番号へ向く**）。

**進め方（並行）**: **#1（migration 0099・全部の表・モデル・スキーマ）を 1 人で先に作ってマージ**し、そのあと下の表の前提に従って並行で作る（例: #2〜#7 の担当・#8 と #13 の担当・#9〜#10 の担当・#11〜#12 の担当・#14 の担当）。ファイルは担当ごとに分け、同じファイルを 2 人で書かない: `app/api/v1/leave.py`（月の API・確定・実績の記録）は **#8 の担当が持つ**（#13 も同じ担当）／照合は `app/services/leave_compare.py`（#9）／送信は `app/services/kaipoke/leave_outbound.py`（#10）／共通の処理は `app/services/staff_leave.py`（#4）／勤務表は `app/services/kinmu_sheet/`（#11）／Google は `app/services/google_drive/`（#12）。照合と送信の API の口（`/leave/compare/...`・`/leave/outbound/...`）は `leave.py` に置き、中身はそれぞれのファイルの関数を呼ぶだけにする。

| # | 作るもの | 前提 | 主な場所 |
|---|---|---|---|
| 1 | migration **0099**（`down_revision='0098'`）: override の列・申請の CHECK・`staff` の列・§2-5 の**全部の**新しい表・既存の休みを「照合前」に。モデル・スキーマもそろえる。**1 人で先にマージ** | 段階 0 | `alembic/versions/0099_leave_and_kinmu.py`・`app/models/` |
| 2 | `core/leave.py` への足し（`visit_hits_leave`・`kaipoke_send_window`）・必要休日数・事業所の休み | 1・段階 0 | `app/core/leave.py`・`app/services/leave_calendar.py` |
| 3 | 「明示の担当なし」の読み方を全部そろえる（§3-2 の表）・1 件ずつの付け替え（中2） | — | backend 7 か所・frontend 3 か所・SQL 1 本・`VisitRead` |
| 4 | `set_staff_override` を唯一の書き口に（`last_leave_changed_at`・op_log の列）・共通の処理 `apply_staff_leave`・`cancel_staff_leave`・後始末 `settle_leave_unassign`・検知 `count_visits_on_leave`・通知 | 1〜3 | `app/services/staff_leave.py`・`op_log_service.py`・`leave_notify.py` |
| 5 | 入口をすべて共通の処理へ（付録 A-1）・申請の作成の検証・締め切り・変更のお願い（`cancel` を含む）・409 の条件 | 4 | `pending_request_applier.py`・`pending_requests.py`・`staff_overrides.py`・`schedule_v2.py` |
| 6 | エンジン・候補・固定イベント（§4-1・§4-2）・**後始末の呼び出し（§4-2 の表の ○）**・実現性チェックの検知・カイポケ取込のプレビューの件数 | 2・4 | 付録 A-2・§4-2 の表 |
| 7 | 職員 Excel（§4-3） | 4 | `staff_excel/` |
| 8 | 月の API: 表・入れる／取り消す・まとめて承認・事業所の休み・勤務すべき時間数・**確定の記録と通知だけ**（送信と予定版は #13） | 4・5 | `app/api/v1/leave.py` |
| 9 | 照合: 段階 0 のジョブを始める API・材料から差分を作る・全通りの表（§7-4-3）・合わせるボタン（`sent_type` を同じトランザクションで）・判定の記録 | 4・8・段階 0 | `app/services/leave_compare.py` |
| 10 | 休みの送信（§7-4-4・`KaipokeJob` の `op='leave-outbound'`・非同期） | 9 | `app/services/kaipoke/leave_outbound.py` |
| 11 | 勤務表の生成（純粋な関数・§7-1）と 10 月の正解での試験 | 2 | `app/services/kinmu_sheet/` |
| 12 | Google ドライブの接続・Picker・スプレッドシートの書き込み・トークン切れの知らせ | 11・Google Cloud の準備 | `app/services/google_drive/`・`app/api/v1/google_drive.py` |
| 13 | 確定と締めの仕上げ: `POST /leave/confirm` から送信のジョブ（#10・非同期で始めるだけ）と予定版（#11・#12）を呼ぶ・`/leave/outbound/...` の口・「実績を記録する」（#9 の条件 → 確定版） | 8・9・10・11・12 | `app/api/v1/leave.py`（#8 の担当） |
| 14 | フロント: 「休み・勤務表」・申請履歴・ベル・ダッシュボード・スタッフ詳細・モニター・スマホの「休み」・取込のプレビュー | 8・9・13 | `app/(app)/admin/leave/`・`app/(mobile)/m/leave/` |
| 15 | エージェントのスキル・SQL・ならし道具の説明（付録 A-3） | 3・5・6 | `agent/` |
| 16 | 片付け（§8） | — | — |

進め方: 設計の細部 → 実装 → 別担当のレビュー（1 回で要点）→ 試験 → PO 了承 → pg_dump → デプロイ（migration 0099 → backend → frontend を 1 回で・head が 1 つであることを確かめる）→ 本番で確かめる（休みを 1 件入れて取り消す・Google ドライブの接続・照合を 1 か月分）。Google Cloud の準備が間に合わなくても、#12 の画面は「未接続」として出るので、ほかは先に使える。**定期処理（cron）は作らない**。

---

## 10. 決定済みと、実装の前に要るもの

### 10-1. 決定済み（PO 2026-10-20）
- 種類 = 公休・午前休（13:00 から勤務）・午後休（12:30 から休み）・有給。有給は必要休日数に数えない。半休 0.5。
- 必要休日数 = 土日＋祝日（振替休日を含む・土曜の祝日は 1 日）＋事業所の休み（管理者が入れる）。祝日・事業所の休みはそれだけでは休みにならない（休みの登録がある日だけ休み）。
- 休日数は型で勤務しない曜日＋表で入れた休み。
- 半休の日の割り振り = 方式 (c)（コースは持たせ、休みの時間帯の訪問だけ担当なし。らく助の中の作り直しでは休む人に戻らない。カイポケの取込では戻ることがあり、そのときは知らせる）。
- 今日より前の日の休みは記録だけ。
- 「休み・勤務表」は「取り消す」だけ（「元に戻す」なし）。取り消しても申請は承認済み・訪問は戻さない。
- その日休む人 = 承認済み（半休・有給も 1 人）・型の休みの曜日と新人は数えない・2 人以上で黄色。
- 新着の申請はベル＋メニューの件数（メールなし）。
- 申請の締め切り = 前の月の 20 日（JST）。スマホは 1 つの「休み」ページ・休みは数だけ・その日何人休むかは見せない。申請履歴の右パネルは無くす。
- カイポケが正。「らく助に合わせる」例外は作らない。送ったあとにらく助で取り消す・種類を変えると「カイポケで要対応」。未送信は「送る予定」。件名で種類が分からない予定は管理者が選び、記録する（内容が変われば出す）。
- カイポケへ送る件名: 公休「休み」・午前休「AM休み」・午後休「PM休み」・有給「有給」。件名＋時刻で種類を決める。照合は「カイポケと比べる」を押したときにその月の週を取り直す。承認した休みは月の確定でまとめて送る（照合が一度済んだ月だけ）。
- 月の締めは自動ではない。照合で差分 0 件のあと「実績を記録する」。予定版は「この月を確定」で作る。cron は作らない。
- 勤務表は Google スプレッドシート。予定版と確定版は別ファイル。5 年間はらく助から消さない。半休の勤務時間は型から計算。実績は照合を済ませた休みで作る。
- QR の実績をカイポケへ反映するのは範囲外（事務の方がカイポケで行う）。
- 段階 0 のあと、一気に作り、一気にデプロイ。

### 10-2. 実装の前に要るもの
1. **段階 0 の完了**（ディレクター）。
2. **Google Cloud の準備（PO の Google アカウント）**: プロジェクト・Drive／Sheets／Picker API・OAuth の同意画面・リダイレクト URI・Picker 用の API キー・「本番」への公開（§7-3）。#12 の前に必要（無くてもほかは作れる）。
3. **お客様への確認（実装は止めない・答えで定数や文言を調整）**:
   - 会社の Google Workspace で外部アプリ（らく助）の接続が許されているか。保存先の共有ドライブ。
   - 本番データの食い違い: 小西様 10/13〜16・20 はカイポケだけ休み／髙梨様 10/21 はカイポケが午前休・勤務表は出勤。
   - 勤務表の細かい扱い（今の設計の仮の決め）: 休憩は 6 時間を超える日だけ 60 分／半休の日の管理時間は 0／有給の日は「休」で時間に入れない／週の 3 つ目以降の休みが祝日の数を超えたときも `祝n` と書く。
   - カイポケの「実績」側の個別業務にだけ休みを入れることがあるか（あれば照合の材料に足す）。

---

## 11. 試験とリスク

### 11-1. 試験（主なもの）
- **共通の処理**: 種類ごとの担当なしの範囲（午前休は開始 12:59 が対象・13:00 は対象外／午後休は終了 12:30 は対象外・12:31 が対象／終了が空の訪問）・打刻済みは動かさない・2 名体制の 2 人目は本人の枠だけ外れる・同行はそのまま件数だけ出る・upsert・過去日は記録だけ（訪問・コース・固定イベントは不変）・承認の通知・管理者の通知の件数・引き受け先の半休の 422・まとめて入れたときの通知は 1 通。
- **後始末と検知**: §4-2 の表の ○ の経路それぞれで、休む人の半休の時間帯の訪問が担当なしになる（今日以降だけ・過去日は触らない）。× の経路（カイポケの取込）では担当が付いたままで、取込のプレビューと「休みの時間帯に担当が付いている訪問 N 件」・実現性チェックに出る。「`StaffWeeklyOverride(` の生成と削除が `set_staff_override` の外に無い」grep の試験。
- **明示の担当なし**: 半休で外した訪問が、盤面・タイムライン・スマホ・打刻履歴・モニター・送信 CSV・代わりの候補・実現性チェック・エージェントの API（`agent.py:154`・`:823`）・空き時間の計算（`auto_allocator_v2.py:8152`）のどれでも「担当なし」。コースの担当の変更・伝播・自動スタッフ割当のやり直しで戻らない。一斉未割当→自動スタッフ割当・週を空にして作り直し→割当でも休む人に戻らない。1 件ずつ外したとき休みの時間帯なら manual が真のまま・休みの相手への付け替えは 422。
- **戻る**: 運転席の「戻る 1 回で全部戻る」は今どおり。表で入れた休みを運転席の「戻る」で戻すと、申請は承認済みのまま・source と pending_request_id も戻る・`last_leave_changed_at` が進む・送信済みなら「カイポケで要対応」になる。
- **申請**: CHECK 制約の 12 種・締め切りの判定（JST・前の月の 20 日 23:59・月末・年末年始）・変更のお願い（種類の変更・`cancel`）は override がある日でも作れる・時間変更の日には普通の申請も作れる・休みの日の普通の申請は 409・同じ日の申請中は 409・`cancel` の承認で休みが消える。
- **数え方**: 必要休日数（2026 年 9・10・11 月＝11・10・11、2 月、年末年始の事業所の休みのある 12 月・1 月、土曜の祝日・日曜の祝日と振替休日のある月）・事業所の休みは登録が無ければ出勤・半休 0.5・有給を数えない・型の休み・その日休む人（型の休み・新人を数えない）・1 月の ISO 週が前の年になる年の月の表（L2）。
- **エンジン**: 有給を終日休みとして避ける・半休の人にコースが付いても時間帯の訪問は担当なし・候補で × になる・固定イベントの不参加・エンジンの人数で半休は 1 人。
- **職員 Excel**: 往復で半休・有給・source・pending_request_id が残る・全置換で増えた休みの訪問が担当なしになる。
- **カイポケ**: 種類の判定は段階 0 の試験が持つ。休みの段では判定の結果の扱い（None は使わない・`unknown` は種類を選ぶ差分）と、照合の通しの例（段階 0 §0-1 の試験の正解と同じもの: 休み 9:00〜13:00 → 午前休・休み 12:30〜17:30 → 午後休・休日当番 → 休みではない・年休 → 有給・休み 10:00〜11:00 → 種類がわからない）・`content_hash` が同じなら選んだ種類が効き、件名か時刻が変われば出る・**§7-4-3 の表の 15 行と付帯の行を 1 行ずつ試験にする**（遷移後の R・S・状態・差分の数・送る予定の数）・合わせるボタンで override と `sent_type` が同じトランザクションで書かれる（途中で失敗したらどちらも残らない）・既存の休み（照合前）は照合前の月の確定で送られない・照合の後に変えた休みだけ送られる・失敗した週・新しくない週（`fetched_at` がジョブの開始より前）・`covered_dates` に無い日は未確認で「実績を記録する」が押せない・送る予定が残ると押せない・照合の後に休みを消すと押せない・休みの送信が朝会を含まない・送信後のイベント取込で二重にならない・`covered_dates` の非同期の経路・段階 0 のジョブが動いているときの 409。
- **勤務表**: §7-1 の規則で手計算した 2026 年 10 月分を正解に、番号の行（9 月末の休みを含む週）・予定の時間（8.0・半休 3.5／4.0）・管理時間の行・合計・常勤換算の切り捨て・週の帯・事業所の休みで登録の無い日は出勤。勤務すべき時間数が未入力なら作らない。
- **Google ドライブ**: 作成・共有ドライブ（`supportsAllDrives`）・再確定で第 2 版のファイルができ前の版が残る・確定版は 1 回だけ・トークン切れで `needs_reconnect` と通知・未接続でも確定できる（Google の呼び出しはモックで試験）。
- **フロント**: 表の集計・申請中の一覧の並べ替え・まとめて承認・「元に戻す」が出ない・差分の一覧の切り替え・送信の状態の印・スマホの選択と送信・締め切り後の「変更をお願いする」（取り消しを含む）。

### 11-2. リスク
- 本番には休み申請が 0 件・override は `off` だけ。0099 で新しい列の既定値と「照合前」の行を作るだけで、データの移し替えは不要。既存 96 件は最初の照合で、カイポケと同じものは「送信済み」、カイポケに無いものは「らく助だけの休み（以前から）」の差分になる。
- 最初の照合では、これまでカイポケだけで入れてきた休み（本番で 53 件の重なり）が「カイポケだけ休み」としてたくさん出る。まとめて押せるようにする（§7-4-3）。
- 職員を消したときの連鎖削除（`staff_weekly_overrides` の `ON DELETE CASCADE`・`app/models/staff.py:140`）では `set_staff_override` を通らず `last_leave_changed_at` が更新されない。退職者の削除はまれで、その職員は照合の対象からも外れるので**許す**。
- カイポケの RPA は同時に 1 本だけ。照合の取り直し（5〜9 分）の間はほかの取込・送信を待たせる（段階 0 の 409）。
- お客様がカイポケで休みを直接入れ続ける間は、らく助の表と食い違う（照合で取り込む運用で吸収する）。カイポケの取込で休む人に担当が戻ることがある（知らせるだけ・H-b）。

---

## 12. レビュー対応表

### 12-1. 1 回目（`leave-and-kinmu-sheet-review-2026-10-20.md`）
| 指摘 | 内容 | 解決した節 |
|---|---|---|
| H1 | 半休で「担当なし」にできない | §3-2（primary 空＋manual 真・変える箇所の一覧）・§4-2 |
| H2 | エンジンは 1 人 1 日 1 コース・0.5 人の意味 | §4-2（方式 (c)・後始末・0.5 は休日数だけ） |
| H3 | 過去日の休みで実績の訪問を動かす | §3-3 の 2・§4-2（後始末は今日以降） |
| H4 | カイポケが正と月の確定で送るの間に状態がない | §2-5・§7-4-3・§7-4-4 |
| H5 | 差分 0 件と「らく助に合わせる」がぶつかる | §7-4-2（例外を作らない）・§7-4-3（判定の記録とハッシュ） |
| H6 | 「戻る 1 回で全部戻る」が表で成り立たない | §3-4・§5-3 |
| M1 | 入口の数え漏れ | §3-1・付録 A-1 |
| M2 | `off` を見ている箇所の漏れ・Literal で 500 | §4-1・付録 A-2・A-3 |
| M3 | 2 人目・同行 | §3-3 の 3・6 |
| M4 | 変更のお願いが 409 | §2-3 |
| M5 | 確定で送ると他の予定も送る・二重 | §7-4-4 |
| M6 | 比べるときの取り直し | §7-4-3（段階 0 のジョブ・未確認・進み具合） |
| M7 | Excel の全置換で新しい列が消える | §4-3 |
| M8 | 設計とモックの食い違い | モックを設計に合わせた（改訂 2）・§5-3・§7-2・§9 |
| M9 | 休む人の数に型の休み・有給を数えるか | §5 |
| M10 | Picker・共有ドライブ・版の履歴・形式・トークン切れ | §7-2・§7-3 |
| M11 | 番号の付け方・勤務すべき時間数・管理時間・休憩・見本 | §7-1 |
| L1 | staff_events は JST の壁時計 | §1-2 |
| L2 | 月の表を iso_year で絞ると年末年始が抜ける | §5 の API |
| L3 | 1 日 1 行 | §2-1 |
| L4 | 事業所の休みの表が §9 に無い | §2-2・§2-5・§9 の 1・2 |
| L5 | 担当なしは週の送信の止めに当たる | §3-2 |
| L6 | 承認処理が user/op_group を受け取れない | §3-1 |
| L7 | §7 の節の番号 | §7 を 7-1〜7-4 の順に並べ直した |
| 確認 1〜10 | PO に確認すべき点 | 1→§4-2／2→§2-5／3→§7-4-2／4→§3-4／5→§5／6→§2-2／7→§7-2／8→§7-2・§7-3／9→§9／10→§10-2 |

### 12-2. 再レビュー（改訂 3）
| 指摘 | 内容 | 解決した節 |
|---|---|---|
| H-a | 後始末を経路ごとでなく共通に・経路の一覧・漏れの検知 | §4-2（`settle_leave_unassign`・`Visit(` と担当を書く箇所からの一覧・`count_visits_on_leave`）・§5 |
| H-b | カイポケの取込で休む人に戻る | §0・§4-2（方式 (i): 取込のプレビューで知らせ、反映後は検知に載る）・§10-1 |
| H-c | 合わせる処理と `sent_type` を同じトランザクションで | §3-1・§7-4-3・§7-4-4 |
| H-d | （R・S・K）の全通りの表と遷移 | §7-4-3 の表（15 行＋付帯）・§11-1 |
| H-e | migration の番号 | §2-5・§9 #1（0099・`down_revision='0098'`） |
| H-f | 既存の override は照合前・照合が済むまで送らない | §2-5（`baseline_pending`）・§2-4・§7-4-4・§11-2 |
| 中1 | 担当の読み替えの漏れ | §3-2 の表（`agent.py:154`・`:823`・`auto_allocator_v2.py:8152`・`inbound.py:1758`・`accompaniments_week.sql:13`。rg で確かめた） |
| 中2 | 1 件ずつ外すときの manual・休みの相手への付け替え | §3-2「1 件ずつの付け替え」 |
| 中3 | 後始末は今日以降だけ | §4-2 |
| 中4 | 事業所の休みは登録がある日だけ休 | §0・§2-2・§7-1 のセルの表 |
| 中5 | §9 の依存の向き | §9（確定は #8 で記録だけ・送信と予定版のつなぎは #13） |
| 中6 | 照合の後に休みを消したことの検知 | §2-1・§2-5（`leave_months.last_leave_changed_at`・`set_staff_override` 1 か所で更新）・§7-4-2 |
| 中7 | 申請の 409 は休みの種類の行だけ | §2-3 |
| 中8 | 受け入れ枠との重なり | 段階 0 の文書で解決。§2-1（判定は `core/leave.py` の 1 つ）・§7-4-3（材料の取り直しは段階 0 を共有） |
| 低 | `content_hash` の材料 | §7-4-3（職員・日付・件名・開始・終了の 1 通り） |
| 低 | 「休み」で 12:30 ごろ始まる予定は午後休 | 段階 0 の `core/leave.py` の規則（§7-4-3 はそれを使うとだけ書き、試験の例に載せた） |
| 低 | まとめて「らく助に入れる」の通知は 1 通 | §3-3 の 7・§7-4-3 |
| 低 | 変更のお願いに「休みを取り消してほしい」 | §2-3（`override_type='cancel'`）・§6 |
| 低 | 型で勤務しない曜日の公休の送信時刻 | §2-1（`config.business_start/end`・既定 9:30〜18:00）・§7-4-4 |
| 低 | モックの「設計 §5-3」の参照先 | §5-3 を作った |

### 12-3. 最終確認（APPROVE-WITH-NOTES）の仕上げ
| 項目 | 反映した節 |
|---|---|
| 年休 → `paid_off`・件名の規則は段階 0 §0-1 を参照するだけ | §7-4-3・§11-1 |
| `week_dates` → `covered_dates`・段階 0 への依頼を消した | §7-4-3 の 6・§10-2 |
| 材料が新しい条件 = `fetched_at` がジョブの開始以降・古い週は未確認 | §7-4-3 の 4・6・§11-1 |
| 休みの送信は `KaipokeJob`（`op='leave-outbound'`・非同期）・確定 API で同期で送らない | §2-4・§7-4-4・§9 #10・#13 |
| 後始末の終日の扱いを `apply_staff_leave` の 4 にそろえた | §4-2 |
| `last_leave_changed_at` は前か後が休みの種類のときだけ・grep の範囲 | §2-1 |
| 送信時刻は `config.business_start/end` | §2-1・§7-4-4 |
| 連鎖削除で更新されないことは許す | §11-2 |
| #1 を 1 人で先にマージ → 並行・ファイルの持ち分 | §9 |

---

## 付録 A. 網羅の一覧（2026-10-20 に grep で確かめた）

### A-1. 休みを書く入口（M1）
| 場所 | 今 | 変えたあと |
|---|---|---|
| `app/services/pending_request_applier.py:235-262` `_apply_staff_off`（呼ぶのは `pending_requests.py:526` create-and-apply・`:735` approve／approve-with-edit） | INSERT | `apply_staff_leave(source='request')`（`cancel` は `cancel_staff_leave`）・actor と op_group を受ける |
| `app/api/v1/staff_overrides.py:182` POST・`:213` PATCH・`:266` DELETE（呼ぶのは `frontend/app/(app)/staff/[id]/_components/OverrideForm.tsx`・`StaffLeavePanel.tsx`（消す）・`agent/rk/ops/staff.py:45-111` `staff-day-off`） | 行だけ（`:196` で生成） | 休みの種類は共通の処理へ・時間変更は `set_staff_override`。`agent/rk/ops/staff.py:24-25` の種類の表に「有給」`paid_off`、`:45-54` の説明（「訪問は動かさない」「戻るには残らない」）を新しい動きに |
| `app/api/v1/schedule_v2.py:1813-` staff-off-week（呼ぶのは `frontend/lib/queries/cockpit.ts:72`） | off 固定 | 種類を受けて共通の処理へ |
| `app/services/op_log_service.py:1007-1062`・`:1064-` undo/redo の `set_staff_off` | 種類・理由・時刻 | §3-4 の列も・`last_leave_changed_at` |
| `app/services/staff_excel/importer.py:1151-`・`replace_all.py:1627-1636` | 行を作る・全削除 | §4-3 |
| `backend/scripts/import_staff_overrides.py` | 行を作る（手で流す道具） | 休みの種類は使わない旨を先頭に書く（使うなら API 経由） |
| 新設: `/leave/...`・照合の「合わせる」 | — | 共通の処理 |
| スマホ `app/(mobile)/m/leave/page.tsx:156`（申請の作成） | `override_type:'off'` 固定 | 種類を選ぶ（§6） |

### A-2. `off` などの種類を見ている箇所（バックエンド・フロント）（M2）
| 場所 | 変えること |
|---|---|
| `app/schemas/staff_overrides.py:31-54`（入力と対応表）・`:138-165`（`WeekOverrideRead`・`OverrideRead` の Literal＝有給 1 件で 500） | 有給を足す |
| `app/services/scheduling/layer3_assignment.py:3195-3245`（`load_active_staff`・`:3241`） | 終日に有給・半休を StaffInfo へ（§4-2） |
| `app/services/scheduling/auto_allocator_v2.py:3320-3330`・`:9719-9733` | 終日に有給 |
| `app/services/scheduling/propose_slots_service.py:374-403` | 終日に有給・半休は時間帯 |
| `app/services/scheduling/substitute_candidates.py:77`・`:391-416`・`:484-496`／`app/schemas/substitute.py:31` | 同上（理由コードは `off` のまま・説明を「休み（有給・半休の時間帯を含む）」に） |
| `app/api/v1/schedule_v2.py:323`・`:1898-1911`・`:2045`・`:2054` | 共通の処理へ移す |
| `app/services/staff_event_defaults.py:100-143` | 終日に有給・半休は時間 |
| `app/api/v1/agent.py:870-895`（`:880`） | 有給を終日休みに |
| `app/services/dashboard_staff_performance.py:60-65`・`:69-98`（語の一覧と `is_leave_event`＝段階 0 で置き換え）・`:108-110`（`NOON_MIN`=12:00）・`:419-421` | 有給・境目を `core/leave.py` に |
| `app/services/template_ws/wl_core.py:27`（`NOON`=12:00） | 境目を `core/leave.py` に |
| `app/services/checkin/monitor.py:968`・`app/schemas/visit_monitor.py:210-216` | 種類はそのまま渡る（説明を 6 種に） |
| `app/services/staff_excel/schema.py:122-123`・`:340-369`・`exporter.py:204-240` | §4-3 |
| `app/services/allocation/engine.py:1020-1026`（午前休・午後休の境目 720 分） | v1 の配置エンジンには休みを渡していない（`app/api/v1/allocate.py:309` `staff_changes=[]`）ので**変えない**（対象外と明記） |
| `app/services/scheduling/feasibility_check.py` | 休みの検査を足す（§4-2） |
| `frontend/lib/schemas/staff-overrides.ts:19-32`・`:111`・`lib/queries/staff-overrides.ts:75` | 有給 |
| `frontend/lib/shift-calendar.ts:9`・`:17`・`:50`・`:107` | 午前・午後・有給を分ける（半休を出勤に数えない） |
| `frontend/components/schedule/v2/cockpit/FixedEventRow.tsx:54-56` | 有給・境目を `lib/leave.ts` に |
| `frontend/components/monitor/MonitorTimeline.tsx:435-441`・`:733-742`・`MonitorRowPanel.tsx:66` | 半休・有給を表示 |
| `frontend/app/(mobile)/m/shifts/page.tsx:95`・`m/leave/page.tsx:16`・`:156` | 「休み」ページへまとめる（§6） |
| `frontend/app/(app)/admin/pending-requests/_components/StaffLeavePanel.tsx:330`・`:436` | ファイルごと消す（§5-1） |
| `frontend/app/(app)/staff/[id]/_components/OverrideForm.tsx:25-30`・`:82` | 有給を選べるように |
| `frontend/lib/schemas/v2/cockpit.ts:28`（理由コード `off`） | そのまま（説明だけ） |
| `frontend/lib/schemas/staff.ts:154` | 消す（§8-4） |

### A-3. エージェント・スキル・ならし道具
| 場所 | 変えること |
|---|---|
| `agent/sql/day_load_vs_staff.sql`（`override_type <> 'off'`・`= 'off'`） | 終日に `paid_off` を含める・半休の列は今のまま |
| `agent/sql/staff_week_overrides.sql` | 有給を「有給」と出す・`source` を出す |
| `agent/sql/accompaniments_week.sql:13` | §3-2 の表（明示の担当なし） |
| `agent/rk/ops/staff.py:24-25`・`:45-54` | A-1 のとおり |
| `agent/.claude/skills/rakusuke-absence/SKILL.md:21`・`:44`・`:89`（`.agents/` 側も同じ） | 種類に有給・`staff-day-off` が訪問を担当なしへ移すこと |
| `agent/.claude/skills/rakusuke-ask/SKILL.md:28`・`:46`・`references/questions.md:65`（`.agents/` 側も） | 担当の式は今のまま（明示の担当なしと一致）・休みの種類に有給 |
| `agent/.claude/skills/rakusuke-week-check/SKILL.md:19`・`rakusuke-week-optimize/SKILL.md:28`・`:71`・`:79-81`・`:189` | 有給も終日の休み・半休の扱い。ならし道具は休みを **DB から読む**（`docs/tools/week-leveling/extract.py` が `staff_weekly_overrides` を読み、終日・半休を道具の入力にする）。`--off` は「まだ登録していない終日の休み」を試算で足すときだけ。**まだ登録していない半休は `--off` で表せない**ので、先に休みを登録してから試算する（スキルにもそう書いた・#15） |
| `.claude/skills/week-leveling/` | 同上 |
