---
name: rakusuke-ask
description: らく助のデータへの質問に、本番を読むだけで根拠つきで答える。訪問の予定・件数（今日/週/月・曜日別・拠点別）・担当・担当なし・重なり・打刻（誰が何時にどの家に着いたか・打刻率・退出なし）・実績の時刻を合わせた記録・音声記録・職員の休み/半休/会議/同行・入院中/休止/終了/開始前の利用者・NG 職員・女性限定・固定訪問の枠/赤ピン・希望の回数・保留プール・特別訪問週間・カイポケとのずれ/未送信/送信結果・誰がいつ動かしたか、を聞かれたときに使う。書き込みはしない。
---

# rakusuke-ask — らく助のデータに答える

このスキルは**読むだけ**。何かを変える依頼に変わったら、該当のスキル（AGENTS.md §8）へ移り、書く手順（AGENTS.md §4-1）に従う。

## 1. 答える手順

1. **問いを決める** — 対象（誰・どの利用者・どの拠点）、期間（日付を曜日つきで確定。「来週の土曜」などあいまいなら両方示すか選択肢で聞く）、数え方（何を数えるか）。
2. **名前を同定する** — `rk sql patient_lookup -p name=山本` ／ `rk sql staff_lookup -p name=田中`。候補が複数なら選択肢で確かめる（稼働中を先に）。API に渡す ID（`patient_id`・`staff_id`）もここで取る。
3. **読む（この順で探す）**
   1. **集計済みの API**（アプリの画面と数え方が一致する）→ `rk get <path> -q ...`。下の §3 の表。
   2. **名前つきの SQL** → `rk sql <名前> -p ...`（一覧 `rk sql --list`・`agent/sql/README.md`）。
   3. どちらにも無いときだけ、**その場の読み取り SQL** を `rk sql --file <file.sql> -p ...`（1 つの SELECT・末尾セミコロンなし・§2 の決まりを守る）。ファイルは作業用の一時フォルダか `runs/` に置き、リポジトリに残さない。
4. **確かめる** — 数字が画面（ダッシュボード等）と違えば理由を探す（§2 の数え方・検証用・写しの時点）。
5. **答える** — §4 の書き方で。

質問ごとの具体的なコマンドは `references/questions.md`（48 問）を見る。

## 2. 数え方の決まり（必ず守り、答えに一言添える）

- **有効な訪問** = `deleted_at is null and status <> 'cancelled'`、【検証】で始まる患者を除く。
- **1 回 1 件**: 2 名体制は `count(distinct coalesce(visit_group_id, id))` で 1 回に畳む。職員ごとの件数では各人に 1 件。
- **同行は件数に入れない**（`accompaniments` は別に数える）。新人の訪問数は同行件数で答え、そう書く。
- **担当** = `coalesce(v.primary_staff_id, case when not v.manual_staff_override and c.deleted_at is null then c.assigned_staff_id end)`。これが空のものだけが「担当なし」。ダッシュボードの「担当なし」は主担当だけを見るので多めに出る。
  主担当が空で手の上書きの印（`manual_staff_override`）がある訪問は「明示の担当なし」（半休の時間帯から外した訪問など）。この式のとおりコースの担当に読み替えない。
- **休みの種類**（`staff_weekly_overrides.override_type`）: 公休 `off`・午前休 `am_off`（13:00 から勤務）・午後休 `pm_off`（12:30 から休み）・有給 `paid_off`（終日・必要休日数に数えない）。`custom_time` は時間変更で休みではない。**終日の休みは `off` と `paid_off` の 2 つ**で数える（`= 'off'` だけだと有給が漏れる）。入れた元は `source`（`request` 本人の申請を承認／`admin` 管理者／`kaipoke` 照合でカイポケから）。休み・半休を承認・登録すると、今日以降なら休みの時間帯の訪問は担当なしへ移る。今日より前の日は記録だけ。
- **休みはカイポケが正**。照合は画面の「休み・勤務表」（管理者のメニュー・月の全員×日付の表）の「カイポケと比べる」。らく助とカイポケで休みが違うと聞かれたら、その照合の結果を見るよう案内し、推測で「どちらが正しい」と言わない。
- **休み申請の締め切り**は前の月の 20 日（日本時間）。過ぎた後や確定済みの月は本人が「変更をお願いする」（種類を変えてほしい・休みを取り消してほしい・理由つき）で出す（`pending_leave_requests` の `is_change`・`leave_type`＝`cancel` は取り消しのお願い）。
- **ダッシュボード API** は行数で数え、検証用を含む → SQL の値と違えばその理由を書く。
- **取消の内訳**は `source`（`manual_cancel` 今週だけ取消／`status_cancel` 患者の非稼働化）。取消・削除の訪問にも打刻が残ることがある。
- **打刻**: 種類（到着/退出）ごとに最新 1 行。`no_show` は実績にしない。`manual`（手入力）・`signature`（サイン）は「QR で読んだ」ではない。打刻率は**定義を必ず書く**（到着の率か、到着と退出が揃った率か／終わった予定だけか）。
- **週を作っていない週**は 0 件ではなく「まだ週を作っていない」。固定訪問の枠（`rk sql patient_fixed_visits`）で補う。

## 3. まず使う集計済みの API（`rk get`）

| 知りたいこと | コマンド |
|---|---|
| 今日・今週の件数・完了・担当なし・重なり | `rk get /dashboard/kpi` |
| 日ごとの推移 | `rk get /dashboard/trend -q days=30` |
| 職員別の件数・1 日あたり・利用者数・予定/実績の分・QR 率・移動・同行 | `rk get /dashboard/staff-performance -q from=2026-10-01 -q to=2026-10-31`（最大 93 日・未来は数えない） |
| その日の進み具合（職員ごと・遅れ・アラート） | `rk get /monitor -q date=2026-10-12` |
| 打刻の実績（合わせた時刻込み）と集計 | `rk get /visit-history -q from=… -q to=… [-q patient_id=… -q staff_id=… -q state=nodep\|none\|adjusted\|special\|late -q q=山本]`（最大 92 日・1 回 200 行まで・`offset`） |
| 週の盤面 | `rk get /schedule/v2/board -q iso_year=2026 -q iso_week=42 [-q office_id=…]` |
| 実現性（移動・重なり・ゆとり・同住所） | `rk get /schedule/v2/feasibility-report -q iso_year=2026 -q iso_week=42 -q days=6` |
| 週の健全性 | `rk get /schedule/v2/schedule-health -q iso_year=2026 -q iso_week=42` |
| 休み・申請・同行 | `rk get /staff/overrides-week -q iso_year=… -q iso_week=…`・`rk get /pending-requests -q status=pending -q request_type=staff_off`・`rk get /accompaniments -q iso_year=… -q iso_week=…` |
| 特別訪問週間・未配置の○ | `rk get /special-visit-periods`・`rk get /special-visit-marks/pool -q iso_year=… -q iso_week=…` |
| カイポケの ●未送信（ロボットを動かさない） | `echo '{"weekStart":"2026-10-19"}' \| rk post-read /integrations/unsent-summary --json -` |
| らく助 × カイポケの週の突合 | `rk get /integrations/reconcile-report -q weekStart=2026-10-19 -q format=json` |
| 月の予定 × 実績（カイポケ） | `rk get /integrations/plan-actual-report -q month=2026-10 -q format=json` |

**呼ばない**（カイポケのロボットや書き込みが動く）: `/integrations/diff-local`・`/diff`・`/export`・`/smart-inbound-preview/start`・`/plan-actual-compare`・`/master-reconcile`・`/integrations/credentials` など。最新のカイポケを取り直したいときは「保存した写しは◯日◯時のものです。取り直すにはカイポケのロボットを動かします（数分）。動かしてよいですか」と選択肢で聞き、`rakusuke-kaipoke` スキルへ。

## 4. 答え方

- **結論 → 内訳 → 根拠 → 注意** の順に、平易な日本語で。日付は「10/17（土）」、時刻は日本時間。
- **根拠（お客様の言葉で）**: 何を見て数えたかと**読んだ時刻**をふつうの言葉で（例「らく助の 10/12 14:05 時点の予定から数えました」）。カイポケとの比較は**写しの時刻**を必ず（中では `fetched_at`・`rk sql kaipoke_snapshot_latest` で確かめる）。**コマンド名・SQL 名は言わない**（AGENTS.md §2-4・§9）。PO が「くわしく」と聞いたときだけ示す。
- **数え方**: 「取消・検証用を除く／2 名体制は 1 回／同行は含めない」など 1 行。
- **事実と推測を分ける**。分からないことは「記録からは分かりません」と言い、誰に何を確かめればよいかを添える（例:「取消と変更の数は今は記録が無く数えられません」）。
- 一覧が長い（目安 15 行超）ときは要点だけ答え、表は `docs/reports/agent/`（git 管理外）に A4 の HTML で置いて場所を示す。利用者名入りの結果を他の場所に保存しない。
- 音声記録の要約など健康の情報は、聞かれたことだけ最小限。NG の理由メモは内部用。
- 画面の言葉: 「合わせる・調整」（「直す・修正」と書かない）。アプリ名は「らく助」。

## 5. その場の SQL を書くときの決まり

- 読み取りだけ（SELECT）。引数は psql 変数 `:'key'`（`-p key=value` で渡す）。
- 時刻: 予定は日本時間のまま／打刻・作成時刻は `at time zone 'Asia/Tokyo'`／`staff_events` は `at time zone 'UTC'`／今日は `(now() at time zone 'Asia/Tokyo')::date`。
- 曜日の番号は 0=月〜6=日（`staff_shifts`・`staff_weekly_overrides`・固定訪問の枠・`courses`・`special_visit_marks`）。ISO 週は `extract(isoyear from d)`・`extract(week from d)`。
- 見てはいけない列: `kaipoke_credentials` の全部・`users.password_hash`・`audit_logs.request_body`（必要なら PO に聞く）。
- よく使う問いになったら、`agent/sql/` に名前つきで足すことを提案する（1 行目 `-- 説明 | params: …`）。
