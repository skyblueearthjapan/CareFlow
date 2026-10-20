# rk level が出す案（plan.json）のフィールド

`tools/leveling/plan.py` が出す形。操作の中身（API の組み立て）は I4 の `rk/ops/visits.py`・`rk/ops/special.py`。
名前は I4 の部品の `fields()` が読む名前に合わせてある（2026-10-12 に突き合わせ済み・`rk plan check` で検査）。
元にした手順: `docs/tools/week-leveling/apply/generic/build_ops.py` と `apply.py`（週43・44 で実績あり）。

## 最上位
| キー | 型 | 中身 |
|---|---|---|
| `kind` | str | `week-leveling`（rk level week）／`staff-absence`（rk level day） |
| `title` | str | 例「週43（10/19〜10/25）ならし案」「10/22（木） 田中さん お休みの振り分け」 |
| `created_at` | str | 案を作った時刻（PC の時計・日本時間） |
| `target` | obj | `iso_year`・`iso_week`・`week_start`、1 日だけなら `date` |
| `source` | obj | `options`（level.py のオプション）・`extracted_at`（本番を読んだ時刻）・`basis_hash`（読んだ時点の週の形。反映の前に読み直して比べる用） |
| `summary` | [str] | 要点（件数・担当/時刻の変わる数・移動の今/案・操作の内訳・休みとして計算した人） |
| `ops` | [obj] | 操作（下）。`step` の小さい順＝流す順 |
| `checks` | [str] | 決まりごとの結果。先頭が `○`（守れている）／`△`（気を付ける点あり）／`×`（問題あり） |
| `check_details` | [obj] | `{rule, ok, errors, warnings, details[]}`（checks の元） |
| `warnings` | [str] | 入らない訪問と理由・相談先、検査の「気を付ける点」、昼休みを 30 分に下げた日、移動の元が 1 件に決まらない所 など |
| `blockers` | [str] | 反映してはいけない理由（決まりの検査の問題・新人が担当・コースが決まらない・テンプレートが読めない） |
| `ready` | bool | `blockers` が空なら true。**false の案は反映しない** |
| `expect` | [obj] | 反映後の姿 `{visit_id(追加は null), patient_id, date, start "HH:MM", staff_id, course "稲A"}`（照合用） |
| `course_map` | obj | 日 → 職員 id → コース（"稲A"） |
| `timeline` | obj | rk plan report --a3 用の 1 日分（変わるところがいちばん多い日）`{date, lunch_min, rows:[{name, staff_code, course, manager, hours, blocks:[{start, end, label, changed, kind, visit_id, from}]}]}`。`kind` = visit / add / event（予定）/ unplaced（「入らない」の行） |
| `timelines` | [obj] | 全日の timeline（週の案の A3 は日ごとに 1 枚） |
| `files` | obj | `dir`（docs/reports/agent/... の出力フォルダ）・`excel`・`a4` |
| `undo_note` | str | 戻し方の注意 |

## 操作（`ops[]`）
すべてに `op`・`step`・`label`（日本語 1 行）。`op_group_id` は案ごとに決まる UUID（プールへ戻すものは別の group）。

### step 0 `assign-staff-week`（手の上書きを外す）
`visit_id`・`staff_id: null`・`patient_id`・`date`・`op_group_id`
→ `POST /schedule/v2/visit-assign-staff-week {visit_id, staff_id: null, op_group_id}`。
手の上書き（manual_staff_override）の訪問には PATCH /courses の担当が伝わらないので先に外す（R2 B-2）。
案の担当と今の上書きの担当が同じなら出さない。
明示の担当なし（主担当が空で manual_staff_override が真＝休みの時間帯から外した訪問など）には出さない（外すとコースの担当＝休む方に読み替わる）。代わりに step 5 で直接担当を付ける。

### step 5 `assign-staff-week`（明示の担当なしに直接担当を付ける）
`visit_id`・`staff_id`（案の担当）・`patient_id`・`date`・`op_group_id`
→ `POST /schedule/v2/visit-assign-staff-week {visit_id, staff_id, op_group_id}`。コースの担当付け（step 4）の後に出す（手の上書きの印がある訪問にはコースの担当が伝わらないため）。

### step 1 `return-to-pool`（入らない訪問 → 保留プール）
`visit_id`・`patient_id`・`date`・`start_time "HH:MM"`・`course`（今のコース）・`cascade_partner: false`・`op_group_id`・`required_staff_count`
→ `DELETE /visits/{visit_id}?cascade_partner=false&op_group_id=…`。「今週だけ取消」はプールに出ないので使わない。API では戻せない（バックアップから）。

### step 3 `special-visit-place`（特別訪問週間の○を置く）
`mark_id`・`patient_id`・`date`・`weekday`・`course_template_id`・`course`（"稲M" など・置く先）・`start_time "HH:MM"`・`duration_min`・`staff_id`（案の担当・参考）
→ `POST /special-visit-marks/{mark_id}/place {course_template_id, start_time}`。
place は拠点一致が必須なので、担当のコースが利用者の拠点と違えば **利用者の拠点の M** に置き、続けて下の move-visit-week（`visit_id: null`・時刻は同じ・`new_course_template_id` = 担当のコース）で移す。

### step 3 `move-visit-week`（今週だけ移動）
`visit_id`（置いた直後の移動は null）・`patient_id`・`date`・`iso_year`・`iso_week`・`old_weekday`・`old_start_time "HH:MM:SS"`・`new_weekday`（＝old・曜日は変えない）・`new_start_time "HH:MM:SS"`・`new_course_template_id`（コースを変えないときは null）・`from_course`・`to_course`・`op_group_id`
→ `POST /schedule/v2/visit-move-week-only`（本文は上の API 名のキーだけ・extra=forbid）。
元は「利用者・曜日・開始」で決まる。案を作る時に流す順に追って 1 件に決まるかを確かめ、決まらなければ warnings に出す。
コースも時刻も変わらない訪問は操作を出さない。

### step 4 `patch-course-staff`（コースの担当）
`iso_year`・`iso_week`・`weekday`・`date`・`office`（拠点の略称）・`course_code`（"A"）・`course`（"稲A"）・`course_id`（案の時点でコースがあれば。無ければ null＝反映の時に iso_year/iso_week/weekday/course で探す）・`course_template_id`・`staff_id`（null＝担当を外す）・`staff_code`・`op_group_id`
→ `PATCH /courses/{course_id} {assigned_staff_id, op_group_id}`。
日ごとに 職員 1 人 = コース 1 つ（今のコースに残る訪問が最多・新しく作るコースが少ない・マネージャーは M）。
正規の職員は自分の拠点が開いている曜日は自分の拠点のコースだけ（build_ops.py の「ある正規職員は月〜金だけ自拠点の A コース」を一般化）。
使うテンプレートは 1 文字のコード（A〜E・M）だけ（M2・臨時へは移さない）。拠点の休みの曜日のテンプレートは使わない。
案で使わないコースに担当が残っていて訪問も残らないときは `staff_id: null` で外す。

### step 2 `cancel-visit-week`（急遽キャンセル）
`rk level --cancel <訪問 ID の先頭>` で出る。`visit_id`・`cancel: true`・`reason: "急遽キャンセル"`・`patient_id`・`date`・`start_time "HH:MM"`・`op_group_id`
→ `POST /schedule/v2/visit-cancel-week`。計算からは外れ（`--drop` と同じ）、プールへは戻さない。予定でない訪問（実施済みなど）には出さず warnings に書く。
最上位の `conditions[]` は「今回だけの条件」（`{text, relax}`。`rk plan report` の「今回だけの条件」と A4 に出る）。`relax: true`（少しオーバー・`--fast`・重みの変更・自作の条件）は `warnings` にも「今回だけの条件（決まりをゆるめる）: …」で入る。

## 出さないもの
- `place-from-pool`（ならしはプールから置かない。○は special-visit-place）。
- 週を作る（copy-week）・一斉スタッフ未割当（禁止の操作）。ならしは週がもう作られている前提。
- 新人の同行（`accompaniment-set`）は案の後で別に作る（rakusuke-week-optimize の手順）。
