---
name: rakusuke-visit-change
description: らく助で利用者の訪問を今週だけ変えるときに使う。キャンセル（今週だけ取消・取消をやめる）・振替・臨時の追加・時刻や曜日の変更・担当替え・保留プールへ戻す／プールから置く・3 日おきなど不規則な日程の追加。案を作って見せ、了承とバックアップのうえで反映・照合し、元に戻せるかを伝える。「〇〇様がキャンセル」「時間を変えて」「別の日に振り替え」「追加で入れて」「担当を替えて」と言われたとき。訪問記録の到着・退出の実績時刻を合わせる（「着いたのは 9:00 だった」）ときも。
---

# rakusuke-visit-change — キャンセル・追加・時刻の変更・担当替え

書き込むスキル。AGENTS.md §4-1 の手順（案 → 見せる → 了承 → `rk backup` → `rk apply` → `rk verify`）を必ず通す。

## 0. 最初に必ず聞くこと

**「今回だけですか、今後ずっとですか」**（選択肢で）
- 今回だけ → このスキル（今週だけの操作。毎週の型＝固定訪問の枠は変えない）。
- 今後ずっと → 固定訪問の枠の変更（`rakusuke-master`）。このスキルでは扱わない。

## 1. 依頼ごとの操作

> 組み直しを伴う変更（急なキャンセルで残りをならし直す・特定の曜日だけ見直す）は `rk level week|day` の指定が使える: `--cancel <訪問 ID>`（取消の操作つき）・`--days thu,fri`・`--keep-current`・`--fast`。表と約束（まず指定で・言えなければ scratch のスクリプトで `tools.leveling.api`・共有の道具は書き換えない）は `rakusuke-week-optimize` の §1-4a。

### キャンセル（お休み）
- 振り替えない → `cancel-visit-week`（今週だけ取消）。
- **別の日に振り替える → 取消ではなく** `move-visit-week`（その週の中で日・時刻を動かす）か、`return-to-pool`（プールへ戻して、後で置く）。
- 罠: **今週だけ取消は保留プールに出ない**（回数にも数える）。振替のつもりで取消すと、振替先が出てこない。
- 当日以前・打刻ありは取り消せない（らく助が断る）。カイポケへ送ると削除になる。
- 取消をやめる: `cancel-visit-week` に `"cancel": false`。

### 時刻・曜日・コースの変更（今週だけ）
- `move-visit-week`。元は「利用者×曜日×開始時刻」で決まる（同じ時刻に 2 件あると決まらない）。
- **曜日を変えるときは `new_course_template_id`（移る先のコース）を必ず付ける**。付けないと旧曜日のコースに残る。
- 2 名体制は 2 行とも動く。非稼働（入院・休止など）の方は断られる。

### 担当替え（今週だけ）
- 1 件だけ → `assign-staff-week`（コースの担当は変えない・2 名体制の相方は触らない）。
- その日のコース丸ごと → `patch-course-staff`（コースの訪問のうち、手で付け替えていないものも同じ人になる）。
- 新人は担当にできない（同行で）。NG 職員・女性限定に触れると断られる → **了承して通す（acknowledge）は使わない**。別の方を選ぶ。

### 臨時の追加・振替先への追加
- コースが決まっている → `add-visit-week`（`course_template_id` 付き＝画面の空き枠への配置と同じ・型は変えない）。
- コース未定（臨時）→ `add-visit-week`（コースなし＝担当なしで入る。後で担当を付ける）。
- 保留プールの方を置く → `place-from-pool`（コース必須。M＝担当なしへ入れてもよい）。
- 特別訪問週間の⭐は `rakusuke-special-week`。

### 不規則な日程（例: 約 3 日おきの 12:00）
- 週の型では表せない → 日付の列をもらい、**週ごとに** `add-visit-week` を並べた案にする（1 日 1 件）。
- 各週が「週を作った後」か確かめる（作る前の週に足すと、週を作るときに二重になることがある → 作った後に足す）。
- 送信まで面倒を見る場合は `rakusuke-kaipoke` へ（明日以降だけ・日中）。

### 実績の時刻を合わせる（訪問記録・到着／退出）
- 予定ではなく**実績**（QR を読んだ時刻）の話。「今回だけ／今後ずっと」は聞かない。予定の時刻は動かない。
- 読む: `rk get /visit-history -q from=<日> -q to=<日>`（打刻履歴）か `rk get /visits/<訪問 ID>`（`actual_arrival_at` など・UTC → 日本時間で言う）。今の時刻・読み取った時刻・合わせてあるかを確かめる。
- `actual-time-adjust`: `visit_id`・`kind`（`arrival` 到着／`departure` 退出）・`time`（HH:MM）・`reason`（理由・必須・200 字まで）・`reason_code`（任意: `intercom_wait` インターホン待ち／`read_later` 読み取りが後になった／`no_read` 読み取りなし／`other`）。
- 決まり（らく助が断る）: 到着は読み取った時刻より後にできない・退出より前。退出は到着より後で、読み取った時刻の 30 分後まで・今より後は不可。到着の読み取りが無い訪問へ手で到着を入れるのは管理者だけ（案に `"manual": true`・確かめてから）。
- 戻し方: `rk undo` で前の時刻へ（合わせていなかった時刻は「読み取った時刻に戻す」`actual-time-reset`）。言葉は「合わせる・調整」（「直す・修正」は使わない）。理由も責めない言い方で。

## 2. 読むもの（書かない）

```
rk sql patient_lookup -p name=伊東                                    # 利用者を確かめる（複数なら選択肢）
rk get /visits -q patient_id=<UUID> -q week_start=2026-10-19 -q week_end=2026-10-25   # その週の予定（状態・コース）
rk get /schedule/v2/board -q iso_year=2026 -q iso_week=43             # 盤面（コースと担当）
rk get /course-templates -q office_id=<拠点>                           # コースのテンプレート（A〜E・M）
rk get /schedule/v2/feasibility-report -q iso_year=2026 -q iso_week=43 -q days=6   # 変えた後の重なり・移動
```
空き枠の候補が要るときは、その日だけならし（`rk level day --date …`）で時刻と担当まで出してもよい（休む人がいないなら `rk level week --week <月曜> --day <日付>`）。

## 3. plan.json の op と受け取る値

| op | 何をする | 主な値 | 元に戻せるか |
|---|---|---|---|
| `cancel-visit-week` | 今週だけ取消／やめる | `visit_id`・`cancel`（既定 true）・`reason` | 戻せる |
| `move-visit-week` | 今週だけ日・時刻・コース | `iso_year`・`iso_week`・`patient_id`・`old_weekday`・`old_start_time`・`new_weekday`・`new_start_time`・`new_course_template_id` | 戻せる（2 名体制で 2 行のコースが違うと不可） |
| `assign-staff-week` | 1 件の担当 | `visit_id`・`staff_id`（null=担当なし） | 戻せる |
| `patch-course-staff` | コースの担当 | `course_id`（または `iso_year`・`iso_week`・`weekday`・`course`）・`staff_id` | 戻せる |
| `add-visit-week` | 今週だけ追加 | `patient_id`・`date`・`start_time`・`duration_min`・`course_template_id`（2 名は `course_template_ids` と `staff_count: 2`） | 戻せる（足した訪問を消す） |
| `place-from-pool` | プールから置く | add と同じ（コース必須） | 戻せる |
| `return-to-pool` | プールへ戻す | `visit_id` | **戻せない**（画面からは元に戻せません） |
| `actual-time-adjust` | 実績の到着・退出の時刻を合わせる | `visit_id`・`kind`・`time`・`reason`（必須）・`reason_code`・`manual` | 戻せる（前の時刻・読み取った時刻へ） |

例（キャンセルと振替）:
```json
{ "kind": "move", "title": "10/22（木）伊東様 → 10/23（金）へ振替", "target": {"iso_year": 2026, "iso_week": 43},
  "summary": ["伊東様 10/22 9:30 → 10/23 10:00（鈴木さんのコースへ）"],
  "ops": [ { "op": "move-visit-week", "iso_year": 2026, "iso_week": 43, "patient_id": "<UUID>",
             "old_weekday": 3, "old_start_time": "09:30", "new_weekday": 4, "new_start_time": "10:00",
             "new_course_template_id": "<移る先のコース>", "label": "伊東様 木 9:30 → 金 10:00" } ],
  "checks": ["NG", "女性限定", "重なり"], "warnings": [] }
```

## 4. 見せ方・了承・報告

- 要点: 何が・いつからいつへ・担当は誰・**利用者へのご連絡が要るか**（時刻が変わる方）・カイポケへ送る件数・元に戻せるか。
- 選択肢:「この案で反映する／条件を変える／やめる」。
- 反映後 `rk verify` と実現性チェック（重なりが増えていないか）を読み、3〜5 行で報告。戻せる操作は「ご希望があれば戻します」、戻せない操作があれば「この操作は、画面からは元に戻せません」と添える（バックアップ・run-id は言わない）。
- カイポケへ送るのは別（`rakusuke-kaipoke`・送る前に差分・日中・当日分は送れない）。

## 案の出し方（必須・AGENTS.md §5-2）

案を作るときは `headline`（結論 3 行まで）・`ops[i].reason`（1 件ごとの理由）・`issues[]`（気になる点・足りない所と選択肢）・`conditions[]`（今回だけの条件）を必ず埋める（`rk plan check` が足りないと注意する）。
`rk plan report` で出した帳票（案のペイン）を指し、チャットでは **結論・気になる点・確かめの問い（①この案で反映する ②ここを直してほしい ③やめる）** だけを話す。

> **最後に必ず**（案を出したら、この順で。長くしない）
> ① 結論と気になる点を短く　② 「案は画面の右の欄（シート）にあります。見てください」と一言
> ③ 「どれにしますか？」と聞き、次の 3 つを**この番号のまま、1 行ずつ分けて**示す（聞かずに終わらない・1 行に並べない）:
> 　①この案で反映する
> 　②ここを直してほしい
> 　③やめる
> 進み具合の一言も**日本語で**（英語を書かない）
> ④ 相談の終わり・直されたとき・試行錯誤のあとは、引き継ぎ帳に 1 件書く（AGENTS.md §2-5・固定の 5 行・お客様には言わない）
「ここを直して」が出たら案を作り直し（`compare_to`・`diff` に「案 1 との違い」）、了承が出るまで繰り返す。了承 → `rk backup` → `rk apply` → `rk verify` → 短い「反映しました」の報告。
