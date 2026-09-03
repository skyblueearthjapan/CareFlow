# 残タスク: courses.assigned_staff_id を書くのにミラーしない経路 (2026-09-03)

調査のみ・**コード変更なし**。2026-09-03 の W37 事故 (プール一括投入がコース担当だけ書き、
既存訪問の `visits.primary_staff_id` が NULL のまま残り、カイポケ月次CSV が 職員名1='-' で
送信されてカイポケ側の担当を消した) の是正で、次の 2 箇所は鏡を持つようになった:

- `backend/app/api/v1/courses.py` (`PATCH /courses/{id}`) … 以前からの bulk UPDATE
- `backend/app/services/scheduling/auto_allocator_v2.py` `reset_visits_to_fixed`
  … 今回 `services/scheduling/course_staff_mirror.mirror_course_staff_to_visits` を追加

しかし `courses.assigned_staff_id` を書く経路は他にもあり、**いずれも visits へ伝播しない**。
盤面 (コース担当を表示) とカイポケCSV (`visits.primary_staff_id` を見る) が食い違う余地が残る。
どれも「その場で visit 側も一緒に書いているか」が争点なので、直す前に経路ごとの意図を確認すること。

| 経路 | 場所 | 起きうるズレ |
| --- | --- | --- |
| undo/redo の `set_course_staff` | `backend/app/services/op_log_service.py:1105` `_set_course_staff` | コース担当だけ元へ戻り、順操作で書き換えた `visits.primary_staff_id` は戻らない (undo 後に盤面と CSV が不一致)。 |
| 急休代替のコース引き受け | `backend/app/api/v1/schedule_v2.py:2101` 付近 (`course.assigned_staff_id = payload.to_staff_id`) | その日の visit は直前のループで個別に付け替えるが、**引き受け対象外だった同コースの担当なし visit** は NULL のまま残る (代替後も CSV は '-')。 |
| カイポケ取込の `course_takeover` | `backend/app/services/kaipoke/inbound.py:1352` 付近 | 取込対象の visit (`partners`) だけ `primary_staff_id` を書く。同コースの取込対象外 visit は旧担当/NULL のまま = 取込後にコース担当と食い違う。 |

補足:
- 当面の実害は `services/kaipoke/csv_builder.resolve_month_rows` の安全網
  (primary が NULL かつ手動上書きでなければ、**在籍中の**コース担当を職員1 に使う) で
  カイポケ送信側は吸収される。上のズレが露出するのは主に「visit に別の担当が残る」型。
- 直す場合は `mirror_course_staff_to_visits` をそのまま呼べる。ただし同ヘルパは自動経路向けに
  青ピン (`week_pinned`) / 打刻済み (`status`) / 手動上書きを除外しているので、
  「管理者の明示操作」に使うなら `courses.py` 側の広い意味論とどちらに合わせるかを決めること。
