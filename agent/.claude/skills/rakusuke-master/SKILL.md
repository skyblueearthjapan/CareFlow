---
name: rakusuke-master
description: らく助のマスター（毎週の型の元になる情報）を、画面と同じ仕組みで更新する。新しい患者の登録（新患）・ご利用の終了・入院/一時休止/開始前/復帰・固定訪問の枠（毎週の曜日・時刻・コース）・希望（週の回数・曜日・時間帯）・NG 職員・女性限定・2 名体制・主担当拠点・住所・職員の曜日ごとの勤務時刻や資格の変更を頼まれたときに使う。必ず「今を読む → 前と後の表 → 選択肢で了承 → rk backup → rk apply → rk verify」の順。
---

# rakusuke-master — マスターの更新

マスター＝**毎週の型の元**（患者・固定訪問の枠・希望・NG・職員の勤務）。変えると**次に週を作ったときから**効く。
「今週だけ」の変更（今週だけ取消・移動・追加・担当替え）はこのスキルではなく `rakusuke-visit-change`。
まず「**今回だけ／今後ずっと**」を選択肢で確かめる。今後ずっと → このスキル。

書く手順は AGENTS.md §4-1 のとおり（案 → 帳票と要点 → 選択肢で了承 → `rk backup` → `rk apply` → `rk verify`）。
操作の部品は `agent/rk/ops/masters.py`（各クラスの説明に API と backend の行番号）。

## 1. どの操作を使うか

| 頼まれごと | op（plan.json） | 画面で言うと | API で戻せるか |
|---|---|---|---|
| 女性限定・2 名体制・主担当拠点・住所・保険・訪問看護区分・ふりがな・備考 | `patient-update` | 患者の編集 → 保存 | ○（前の値を PATCH） |
| 入院・一時休止・開始前・終了・復帰 | `patient-status` | 患者画面の「状態の変更」ダイアログ | 非稼働どうし（例 入院→休止）だけ ○。稼働⇄非稼働・終了で枠も消す → **×（画面からは元に戻せません）** |
| 固定訪問の枠（毎週の曜日・時刻・時間・コース） | `pfv-update` | 患者画面「固定訪問パターン」の保存 | 型だけ（pattern_only）○。型＋今週（pattern_and_week）は **×** |
| 希望（週の回数・曜日・時間の種類・時間帯・基本の時間） | `preference-update` | 患者の編集「希望訪問パターン」 | ○ |
| NG 職員の登録・解除・理由メモ | `ng-staff-update` | 患者の「訪問条件」カード | ○ |
| 職員の曜日ごとの勤務（出勤・開始・終了） | `staff-hours-update` | 職員画面「週間シフト」 | ○ |
| 職員の資格・性別・主拠点・新人・休職/退職・備考（**変える項目だけ**） | `staff-master-update` | 職員の編集（全項目保存はしない） | ○（休職/退職にすると同行リンクが消えるので ×） |
| 新しい患者 | `patient-create` | 「＋新規患者登録」 | ×（取り消すなら別の案で削除） |

使わない（既定で禁止・PO が名前を挙げたときだけ別の手順）: 患者や職員の Excel の**完全置換**、固定枠の一括置換（`from-week-bulk`）、患者・職員の削除、パスワードの再発行・ユーザー操作、QR の再発行、職員編集の**全項目保存**、氏名・コードの変更、業務ロール（manager）の変更、赤ピン（完全固定）の付け外し（患者マスタで人がする操作・K-6。PO が承認したときだけ op に `pin_change_approved_by` を書く）。

## 2. 共通の流れ（どの変更も同じ）

1. **対象を同定する** — `rk sql patient_lookup -p name=…`／`rk sql staff_lookup -p name=…`。候補が複数なら選択肢（稼働中を先に）。
2. **今を読む**（根拠として時刻を添える）
   - 患者: `rk get /patients/<id>`（状態・性別制限・2 名体制・拠点・住所・希望 `weekly_pattern`）
   - 固定訪問の枠: `rk get /patients/<id>/fixed-visits -q mode=normal`（`movability`＝赤ピンかどうかも）
   - NG: `rk get /patients/<id>/ng-staff`／職員: `rk get /staff/<id>`・`rk get /staff/<id>/shifts`
   - 条件の要約: `rk sql patient_constraints -p patient=<名前>`・次の予定: `rk sql patient_next_visits -p patient=<名前>`・希望の回数と枠の数の食い違い: `rk sql preference_vs_fixed_count`・型とその週の食い違い: `rk sql master_vs_week_diff -p monday=<月曜>`
   - 影響: 状態の変更は `rk get /patients/<id>/status-impact -q to=<状態> [-q remove_schedule=true]`（消える予定・枠・カイポケの週数）。枠は `rk post-read /patients/<id>/fixed-visits/validate --json -`（重なり・昼休み・コースの容量・今週との衝突を書かずに検査）。コースの空きは `rk get /patients/<id>/fixed-visits/course-load`。
3. **前と後の表を見せる**（1 項目 1 行。例「女性限定: なし → 女性のみ」「火 9:30 A コース 35 分 → 火 10:00 A コース 35 分」）。あわせて
   - 影響（今後の予定で違反になる訪問・プールに出る/消える回数・カイポケへ送ると出る差分）
   - **元に戻せるか**（上の表の右列。× なら「この操作は、画面からは元に戻せません」とはっきり）
4. **選択肢で了承**（例:「この内容で反映する／内容を変える／やめる」）。了承の言葉と時刻を `--approved` に書く。
5. 案を書く（下の §4 の形）→ `rk plan check` → `rk plan report`（A4）→ `rk backup --label <名前>` → `rk apply <plan.json> --approved "…"` → `rk verify <plan.json>`。
6. **報告**（3〜5 行）: 反映した件数・照合の結果・元に戻せるか・**次にすること**（例「週45 を作るときに入ります」「カイポケへ送るのは差分を見てから」）。

## 3. 変更ごとの注意

### 新患（D-1）
- ヒアリング項目: 氏名（**カイポケの利用者名と同じ表記**。齋藤/齊藤など）・ふりがな・住所・主担当拠点（`rk post-read /offices/resolve --json -` に `{"address": "…"}` で判定を読める）・保険（医療/介護）・訪問看護区分（精神科/一般）・性別・性別制限・2 名体制・希望（週の回数・曜日・時間帯・基本の時間 35 分）・**初回はいつか**・状態（まだ始まらないなら「開始前」）。
- 座標（lat/lng）が無いと移動時間・同じ建物の判定ができない。座標は画面の住所欄が自動で入れる（rk からは入れられない）→ 座標が分からないときは「登録は画面で（住所を入れるだけで座標が入る）」を選択肢に入れる。
- 先に同じ氏名がいないか確かめる（`patient-create` の反映前の記録にも残る）。
- **登録しただけでは毎週の訪問は入らない**。固定訪問の枠（`pfv-update`）を同じ案に入れるかを必ず聞く。枠をまだ作っていない新しい方は今週だけ追加したので枠が無く、週45 以降に入らなかった（10/9）。空き枠は `rk post-read /schedule/v2/propose-slots` で探せる。
- 「今週から入れる」なら、枠に加えて今週の予定は `rakusuke-visit-change`（今週だけ追加）か `pfv-update` の `pattern_and_week`（その週を型から作り直す・戻せない）。
- カイポケ側に利用者がいるかは、カイポケのマスタ突合（連携画面 👥）で確かめてもらう（送信で「利用者が見つかりません」になる）。

### 終了（D-2）
- `patient-status` に `to_status: "cancelled"`。**`remove_schedule` を必ず明示**（true = 今後の予定と固定訪問の枠も消す＝画面の既定 ON）。
- 終了日（`from_date`）を確かめる（省略＝今日・過去日は不可）。過去の実績は残る。
- 予告すること: 消える予定の件数・消える枠・**その週をカイポケへ送ると削除が出る**（送るのは `rakusuke-kaipoke` で差分を見てから）。特別訪問週間の○が指す予定は残ることがある → 反映後に点検。
- 元に戻せません（画面からは）（消えた予定と枠は API で戻せない）。了承の時に必ず言う。
- 空いた枠にプールの方を入れる案（10/9 の例）を続けて提案してよい（別の案・`rakusuke-week-optimize`）。

### 入院・一時休止・開始前・復帰（D-3）
- 稼働中 → 入院/休止/開始前: `from_date` 以降の予定が取消になる（⭐ 特別訪問は既定で残す `special_period_action: keep`）。
- 復帰（→ 稼働中）: 型から作成済みの週に予定を作り直す（`regenerate: true`・担当も戻す）。復帰日を確かめ、作り直した週の点検（`rakusuke-week-check`）を勧める。
- 稼働⇄非稼働は API で正確には戻せない（取消・作り直しが伴う）→ 「この操作は、画面からは元に戻せません」と伝える。
- 状態は**PATCH で変えない**（`patient-update` は status を受け付けない）。

### 固定訪問の枠（D-4）
- `items` は**変更後の全件**（今ある枠も残すなら入れる）。今の枠は `rk get …/fixed-visits -q mode=normal` で読み、`movability` と `is_pinned` を**そのまま写す**（省略すると「unknown」に戻る罠）。
- 時刻は 08:00〜19:00 の 5 分刻み。日曜の枠は作らない。2 名体制は同じ曜日に `slot_index` 0 と 1 の 2 行。
- 赤ピン（`locked`＝完全固定）は**曜日・時刻だけ**を固定する。赤の枠のコース・長さの変更は通る。曜日・時刻の変更は `pfv-update` が断る。赤の付け外し（外す・消す・新しく付ける）も断る → PO に相談し、承認されたときだけ op に `pin_change_approved_by`。
- 反映先を選択肢で: 「型だけ（次に週を作るときから）」＝`pattern_only`（戻せる）／「型と、作成済みのこの週も」＝`pattern_and_week` ＋ `iso_year`/`iso_week`（その週の予定を型から作り直す・戻せない・非稼働の患者は不可）。
- 型を変えても**作成済みの週は変わらない**（pattern_only のとき）。影響する作成済みの週を列挙し、それぞれ今週だけの移動で合わせるか聞く。
- NG・女性限定の警告（422 `constraint_confirmation_required`）が出たら、**了承して通さない**。コースか時刻を変えた案を作り直す。
- `sync-fixed-to-week` は使わない。希望の回数と枠の数が合っているかも見せる（例: 週 3 回に枠 2 つ → 毎週プールに残る）。

### 希望（D-5）
- `weekly_pattern` は**丸ごと置き換え**。今の値を読んで、変える所だけ変えた全体を書く。曜日は `Mon`〜`Sun`、時間の種類は 固定/時間帯/午前/午後/終日。
- 希望を変えても固定訪問の枠は自動では変わらない → 枠も変えるか聞く（回数と枠の数の食い違いはプールに残り続ける）。

### NG・女性限定・2 名体制（D-6）
- NG は 1 名ずつ `ng-staff-update`（`action: add|remove`・理由メモ `note`）。理由メモは内部用（お客様向けの帳票に出さない）。
- 女性限定は `patient-update` の `sex_restriction: "female_only"`（解除は null）。2 名体制は `requires_multiple_staff`。
- 変えた後、**今後の予定で違反になる訪問**を一覧にし（`rk sql` か `rakusuke-week-check`）、付け替え案（`rakusuke-visit-change`／`rakusuke-week-optimize`）を出す。登録しただけでは既存の予定は外れない。

### 職員（D-8）
- 勤務時刻は `staff-hours-update`（7 曜日まとめて。変えない曜日も今の値で入れる）。**職員の PATCH を呼ばないので退職が稼働中に戻る罠に当たらない**。
- 資格・性別・主拠点・新人・休職/退職は `staff-master-update` に**変える項目だけ**。資格はカイポケの請求区分（正看/准看）に効く → 次の送信で差分が出ると予告。休職/退職にすると今週以降の同行リンクが消える（戻せない）。
- 入職・退職・勤務の変更は、影響する週の担当を洗い出して `rakusuke-week-optimize`（ならし）を提案する。
- アカウント・パスワードの操作はしない（統一パスワードが崩れる）。

## 4. 案（plan.json）の例

```json
{ "kind": "master-update",
  "title": "山田様（例） 固定訪問の枠を作る（火 9:30）",
  "target": {"patient": "山田様（例）"},
  "summary": ["固定訪問の枠: なし → 火 9:30 A コース 35 分", "週45 を作るときから入る"],
  "ops": [
    { "op": "pfv-update", "patient_id": "…", "mode": "normal", "change_scope": "pattern_only",
      "items": [ {"weekday": 1, "start_time": "09:30", "duration_min": 35, "course_template_id": "…",
                  "slot_index": 0, "movability": "unknown", "is_pinned": false} ],
      "label": "山田様（例） 枠: なし → 火 9:30" } ],
  "checks": ["重なり・昼休み・コースの容量（validate）", "NG・女性限定"], "warnings": [] }
```

他の op の形（必須の項目）:
- `patient-update`: `patient_id`・`changes`（例 `{"sex_restriction": "female_only"}`。住所を変えるなら `lat`/`lng` も）
- `patient-status`: `patient_id`・`to_status`（active/suspended/admitted/pending/cancelled）・終了なら `remove_schedule`（true/false 必須）・任意 `from_date`・`special_period_action`・`regenerate`・`note`
- `preference-update`: `patient_id`・`weekly_pattern`（全体）
- `ng-staff-update`: `patient_id`・`staff_id`・`action`（add/remove）・任意 `note`
- `staff-hours-update`: `staff_id`・`shifts`（7 件 `{weekday, is_on, start_time, end_time}`）
- `staff-master-update`: `staff_id`・`changes`
- `patient-create`: `patient`（`name`・`primary_office_id` 必須）
- どれも `label`（帳票に出る 1 行）を付ける。

## 5. 答え方・報告の型

```
山田様（例）の固定訪問の枠を作りました（火 9:30 A コース 35 分）。照合も一致しました。
・次に週45 を作るときから入ります（週43・44 は今週だけ追加済み）。
・元に戻す: ご希望があれば、確かめてから戻します
（らく助の 10/12 14:05 時点の固定訪問を見て確かめました）
```

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
「ここを直して」が出たら案を作り直し（`compare_to`・`diff` に「案 1 との違い」）、了承が出るまで繰り返す。了承 → `rk backup` → `rk apply` → `rk verify` → 短い「反映しました」の報告。
