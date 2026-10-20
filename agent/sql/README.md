# agent/sql — 名前つきの読み取り SQL（らく助）

`rk sql <名前> -p key=value ...` で動かす。一覧は `rk sql --list`。**読むだけ**（`begin transaction read only` の中で流れる）。

## 書き方の約束（新しく足すときも守る）
- 1 ファイル = **1 つの SELECT 文**（`with` 可）。**末尾にセミコロンを付けない**（rk が `select json_agg(row_to_json(t)) from (<本文>) t` のように包んで JSON にするため）。書き込み文（insert/update/delete/DDL）は書かない。
- 1 行目は必ず `-- <日本語の説明> | params: key1, key2`（引数なしは `params: (なし)`）。rk の一覧と検査がこの行を読む。
- 引数は psql 変数 `:'key'`（文字列として埋め込まれる。日付は `:'date_from'::date`）。**列挙した引数はすべて必須**。「指定なし」は空文字（`-p staff=`）で渡す。
- 数える訪問は `deleted_at is null and status <> 'cancelled'`、【検証】患者を除く（`p.name not like '【検証】%'`）。担当は `coalesce(v.primary_staff_id, case when not v.manual_staff_override and c.deleted_at is null then c.assigned_staff_id end)`。1 回 1 件は `count(distinct coalesce(v.visit_group_id, v.id))`。
- 休みの種類（`staff_weekly_overrides.override_type`）: 終日の休みは `in ('off', 'paid_off')`（公休と有給）。半休は `am_off`（13:00 から勤務）・`pm_off`（12:30 から休み）。`custom_time` は時間変更で休みではない。**`= 'off'` だけで終日休みを見ない**（有給が漏れる）。主担当が空で `manual_staff_override` が真の訪問は「明示の担当なし」（休みの時間帯から外した訪問など）で、上の担当の式のとおりコースの担当に読み替えない。
- 時刻: 予定（visits）は日本時間のまま。打刻・作成時刻は `at time zone 'Asia/Tokyo'`。職員の予定（staff_events）は `at time zone 'UTC'`（壁時計を UTC として保存）。今日は `(now() at time zone 'Asia/Tokyo')::date`。
- 曜日の番号は 0=月〜6=日（staff_shifts・overrides・PFV・courses・marks）。
- 列名は英語の snake_case（JSON のキー）。答えるときは日本語に言い換える。
- 利用者名を含む結果をリポジトリに保存しない（保存するなら `docs/reports/agent/`）。

## 一覧（31 本）

| 名前 | 何を返すか | 引数 |
|---|---|---|
| `visits_valid` | 有効な訪問の一覧（担当・コース・拠点・出所）。`collapse=1` で 2 名体制を 1 回 1 行に | date_from, date_to, office（名か略称・空=全）, collapse（1/0） |
| `visit_counts_by_day` | 日 × 拠点の件数（1 回 1 件・行数・完了） | date_from, date_to |
| `visits_by_staff_day` | 職員 × 日の担当件数・最初/最後の時刻・訪問分（副担当も 1 件・同行は入れない） | date_from, date_to, staff（コード/氏名の一部・空=全員） |
| `visits_by_patient` | 利用者の訪問（取消も状態つき） | patient（コード/氏名/かな）, date_from, date_to |
| `patient_next_visits` | 利用者の次の予定 5 件 | patient |
| `day_load_vs_staff` | 日ごとの件数と出勤人数（終日休み＝公休・有給／うち有給／半休・マネージャー・新人を別列。半休は出勤に数える。休みの列は新人・マネージャーも数える）・1 人あたり | date_from, date_to |
| `unassigned_visits` | 本当に担当なしの訪問（明示の担当なしを含む） | date_from, date_to |
| `staff_overlaps` | 同じ職員で時間が重なる訪問の組（移動は見ない） | date_from, date_to |
| `visit_source_breakdown` | 出所 × 状態の件数（取消の内訳・削除済み） | date_from, date_to |
| `checkin_arrivals` | 打刻の実績（誰が・何時に・どの家・到着/退出・合わせた理由・場所判定）日本時間 | date_from, date_to, patient（空=全）, staff（空=全） |
| `checkin_rate_by_staff` | 担当職員ごとの打刻率（終わった予定のみ・到着/QR 到着/両方） | date_from, date_to |
| `checkin_location_alerts` | 場所が要確認・場所違い・GPS なしの打刻 | date_from, date_to |
| `time_adjustment_reasons` | 実績の時刻を合わせた件数（理由別・最新行のみ） | date_from, date_to |
| `staff_roster` | 稼働中の職員（拠点・ロール・新人・資格・勤務時刻・応援拠点） | (なし) |
| `staff_lookup` | 職員の名前の同定（候補 30 件まで） | name |
| `staff_week_overrides` | 週の休み（公休・午前休・午後休・有給）と時間変更・種類の日本語・終日か・入れた元（申請／管理者／カイポケ） | monday |
| `staff_week_events` | 週の職員の予定（壁時計・今週だけ外した印） | monday |
| `pending_leave_requests` | 未承認の申請（休み申請など）。休み申請は種類・変更のお願いか・理由 | request_type（空=全種類・例 staff_off） |
| `accompaniments_week` | 週の同行（新人・支援）とコースの担当・件数 | monday |
| `patient_lookup` | 利用者の名前の同定（候補 30 件まで） | name |
| `patients_by_status` | 状態ごとの利用者・いつから・今日以降の予定の残り・枠の数 | status（空=稼働中以外すべて） |
| `patient_counts_by_office` | 拠点 × 状態の利用者数 | (なし) |
| `patient_constraints` | NG 職員・女性限定・2 名必須・希望・固定枠の要約（🔴赤ピン）・同住所 | patient（空=条件のある稼働中の方すべて） |
| `patient_fixed_visits` | 固定訪問の枠（1 枠 1 行・赤ピン・コース） | patient（空=稼働中の全員） |
| `preference_vs_fixed_count` | 希望の週回数と固定枠の数が違う方 | (なし) |
| `master_vs_week_diff` | 毎週の型とその週の予定の食い違い（曜日 × 時刻） | monday |
| `pool_week` | 週の保留プールの近似（特別訪問の未配置○・希望回数に足りない方） | monday |
| `special_periods_active` | その日に特別訪問週間中の方・その週の○と未配置・訪問数 | on_date |
| `kaipoke_snapshot_latest` | 保存したカイポケ CSV の鮮度（区分 × 月 × 週・日本時間） | (なし) |
| `kaipoke_jobs_recent` | 最近のカイポケ連携ジョブと明細の成否 | days |
| `audit_recent_writes` | 最近の書き込み API（誰が・いつ・どこへ・結果。本文は出さない・最大 500） | days, path_like（空=全部） |

## 検証
2026-10-12 に本番で読み取り専用トランザクションの中で全 31 本 × 2 組の引数を件数だけ数えて確認（エラー 0）。
