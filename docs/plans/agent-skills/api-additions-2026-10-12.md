# エージェント用に足した API（2026-10-12・読むだけ）

設計書 `design-2026-10-12.md` §3-5（足りない API）と `docs/plans/agent-ui/step1-facts-2026-10-12.md` §4（週まとめの読み取りが無い・前 4 週の担当歴が無い）を受けて、**読むだけ**の窓口を足した。PO の許可（2026-10-12「作っていくしかない」）の範囲で、フロントエンドは変えていない。マイグレーションなし。

| 追加/変更 | パス | 権限 | 実装 |
|---|---|---|---|
| 新規 | `GET /api/v1/agent/week-context?iso_year&iso_week` | admin のみ | `backend/app/api/v1/agent.py` |
| 新規 | `GET /api/v1/agent/staff-week?iso_year&iso_week[&include_inactive]` | admin のみ | 同上 |
| 絞り込みの追加 | `GET /api/v1/patients?status&q` | 従来どおり（admin 全件／staff は担当患者） | `backend/app/api/v1/patients.py` |
| 絞り込みの追加 | `GET /api/v1/staff?q&include_retired` | 従来どおり（admin 全員／staff は自分だけ） | `backend/app/api/v1/staff.py` |

- ルーターの登録は `backend/app/api/v1/__init__.py` の 1 行（`prefix="/agent"`）。
- どれも DB を書かない。応答は Pydantic を通さない素の JSON（下のフィールド名が正典）。
- クエリ回数は件数に依らず一定（week-context 約 20 本・staff-week 約 8 本。N+1 なし）。
- 時刻は `"HH:MM:SS"`、日付は `"YYYY-MM-DD"`、ID は文字列。`weekday` は 0=月〜6=日。
- ISO 週として無い組み合わせ（例 2025 年の第 53 週）は 422。

---

## 1. `GET /agent/week-context` — 1 週のならしの材料をまとめて

週のならし道具の `docs/tools/week-leveling/extract.py`（`_week_sql`・`_history_sql`）が psql で集めているものを、**同じ意味で** 1 回で返す。道具はこの応答に乗り換えられる（ssh・psql が要らなくなる）。extract.py に無いもの（固定訪問の枠・同住所・コース・同行・プール・担当歴の集計など）も足してある。

### 1-1. extract.py と同じ意味のもの（キー名も同じ）

| キー | 中身 | 条件（extract.py と同じ） |
|---|---|---|
| `extracted_at` | 取り出した時刻（UTC・ISO 8601） | |
| `week_start` | 月曜の日付 | `week_end`（日曜）も付く |
| `visits[]` | 週の訪問 | `visit_date` が週内・`deleted_at IS NULL`・`status <> 'cancelled'`。患者の論理削除は見ない |
| `patients[]` | 患者 | 週の訪問（**取消も含む**・削除済みは除く）の患者 ∪ 週の特別訪問マークの患者。**＋保留プールの患者**（`in_pool=true`・下記） |
| `special_marks[]` | 特別訪問週間の未配置の○ | `kind='extra'`・期間が `active`・患者が active かつ論理削除なし・`status='pool'` か「`placed` だが置いた訪問が消えた/取消」。`duration_min` = 固定訪問の枠（normal・曜日→slot 順の先頭）→ 希望の `service_minutes` → 30 |
| `ng[]` | NG 全件 `{patient_id, staff_id}` | 全件 |
| `scheduling_settings` | 移動の速さ・ゆとりなど | シングルトン行。無ければ `null`（道具は既定値） |
| `offices[]` | 拠点 `{id, name, short_label, lat, lng, operating_weekdays}` | 論理削除なし |
| `staff[]` | 職員 | `status='active'` かつ論理削除なし（extract.py と同じ） |
| `shifts[]` | 曜日ごとの勤務 `{staff_id, weekday, is_on, start_time, end_time}` | 全件 |
| `weekly_overrides[]` | 週の休み/時間変更 `{staff_id, weekday, date, override_type, start_time, end_time, reason}` | その ISO 週のみ。`override_type` = `off`・`custom_time`・`am_off`・`pm_off` |
| `events[]` | 予定 `{id, staff_id, date, start, end, end_date, event_type, title, source, blocking}` | `cancelled_at IS NULL` で週と重なるもの。**時刻は「JST の壁時計を UTC として保存」のまま**（extract.py の `at time zone 'UTC'` と同じ。9 時間ずらさない） |
| `history[]` | 直前 4 週の担当歴 `{patient_id, visit_date, start_time, primary_staff_id}` | 月曜の 28 日前〜前日・削除/取消を除く・主担当ありのみ・日付→時刻順（`_history_sql` と同じ） |

`visits[]` の各行（extract.py のキー＋α）:
`id, patient_id, patient_name, visit_date, weekday, start_time, end_time, type, required_staff_count, visit_group_id, source, week_pinned（青ピン）, status, is_unplanned, manual_staff_override, primary_staff_id, secondary_staff_id, mentor_staff_id, course_id, course_code, course_office（拠点の short_label）, course_staff_id（コースの担当）, effective_staff_id（主担当、無ければコースの担当＝表示の正典）, kaipoke_id, assigned_staff_ids（visit_staff_assignments の職員・2 名体制の確認用）`

### 1-2. 足したもの

| キー | 中身 |
|---|---|
| `iso_year`・`iso_week`・`week_end` | 週の識別 |
| `cancelled_visits[]` | 今週だけ取消（`status='cancelled'`）の訪問。形は `visits[]` と同じ |
| `patients[]` の追加項目 | `code, sex, sex_restriction, lat, lng, primary_office_id, allowed_office_ids, weekly_pattern（希望・staff_count など生のまま）, special_weekly_pattern, requires_multiple_staff, status, deleted, in_week, in_pool` |
| `fixed_visits[]` | 上の患者全員の固定訪問の枠 `{id, patient_id, mode(normal/special), weekday, slot_index, start_time, duration_min, course_template_id, sub_office_id, is_pinned（赤ピン）, movability（unknown/time_flexible/day_flexible/locked）}` |
| `same_address_links[]` | 上の患者が関わる同住所リンク `{patient_a_id, patient_b_id, pair_mode(blocked/preferred/required)}` |
| `special_periods[]` | 週と重なる特別訪問週間（状態を問わない）`{id, patient_id, start_date, end_date, weekly_target, status}` |
| `special_marks_all[]` | 週のマーク全件（`kind`・`status`・`placed_visit_id` 付き。退避 `displaced` も含む） |
| `pool[]` | 保留プール `{patient_id, name, desired_per_week, actual_in_week, shortage, requires_multiple_staff, reason}`。**画面（`CourseDayTablePanel` の poolPatients）と同じ判定**: 患者 `status='active'`・論理削除なし で、複数名体制なら常に候補（`reason='multi_staff'`）、それ以外は `weekly_pattern.frequency_per_week`（1〜7、0/未設定は対象外）＞ 週の訪問数（**取消も数える**・削除済みは数えない）。画面の 200 件の上限は掛けない |
| `staff[]` の項目 | `id, code, name, role, sex, is_trainee, qualification, status, primary_office_id, secondary_office_ids` |
| `staff_inactive[]` | 論理削除されていない休職（`on_leave`）・退職（`retired`）の職員。形は `staff[]` と同じ |
| `courses[]` | 週のコース `{id, weekday, date, code, office_id, office_short_label, assigned_staff_id, template_id, course_status}` |
| `course_templates[]` | コースのテンプレート `{id, office_id, label, capacity[月..日]}` |
| `accompaniments[]` | 週のコース・訪問への同行リンク `{id, accompanying_staff_id, target_type(course/visit), course_id, visit_id, kind(trainee/support), source}` |
| `history_from`・`history_to` | 担当歴の範囲 |
| `history_counts[]` | 担当歴の集計 `{patient_id, staff_id, count, last_date}`（患者ごとに回数の多い順） |

### 1-3. 道具が乗り換えるときの注意
- `staff` は active だけ（extract.py と同じ）。新人（`is_trainee`）も含まれる。道具は従来どおり新人を見ない扱いにする。
- `patients` にはプールの患者が足されている（`in_week=false, in_pool=true`）。訪問から患者を引く使い方なら影響しない。
- 予定の時刻は壁時計。比較はそのまま `visits` の時刻とできる。

## 2. `GET /agent/staff-week` — 職員ごとの 1 週

職員ごとの勤務・休み・予定・訪問件数を 1 回で返す（`/staff/{id}/shifts`・`/staff/{id}/events` を人数分たたかずに済む）。容量の確認用。

- 対象: 論理削除なしの `status='active'` の職員。`include_inactive=true` で休職・退職（論理削除前）も含める。
- 応答: `{iso_year, iso_week, week_start, week_end, staff:[…]}`。
- `staff[]`: `{id, code, name, role, sex, is_trainee, status, primary_office_id, week_visits_count, days:[7 日分]}`
- `days[]`（月〜日の 7 行）:

| キー | 中身 |
|---|---|
| `date`・`weekday` | 日付・曜日（0=月） |
| `shift` | 曜日の勤務 `{is_on, start_time, end_time}`。行が無ければ `null` |
| `override` | その日の休み/時間変更 `{override_type, start_time, end_time, reason}`。無ければ `null` |
| `is_on`・`start_time`・`end_time` | 実際の勤務。`off` → 休み。`custom_time` → 出勤扱い（自動割当 layer3 と同じ）・時刻は変更後（空なら曜日の勤務）。`am_off`/`pm_off` は出勤のまま `half_off` に `am`/`pm` |
| `events[]` | その日と重なる予定（取消を除く・日をまたぐ予定は重なる日すべてに載る）。形は week-context の `events` と同じ |
| `visits_count`・`visit_minutes` | その日に**担当（主担当、無ければコースの担当）**として持つ訪問の件数と合計分。取消・削除は数えない |
| `first_visit_start`・`last_visit_end` | その日の担当訪問の最初の開始・最後の終了 |
| `second_staff_count` | 2 人目（`secondary_staff_id` か visit_staff_assignments で担当以外）として入っている件数 |
| `accompaniment_count` | その日のコース・訪問への同行リンクの件数 |

## 3. 一覧の絞り込み（足しただけ・指定しなければ従来と同じ）

- `GET /patients`
  - `status`: 患者の状態の完全一致（`active`・`suspended`・`admitted`・`pending`・`cancelled`）。
  - `q`: 氏名・かなの部分一致（大小文字を問わない。`%`・`_` は文字として扱う）。
  - 並び・`limit`/`offset`・staff ロールの範囲（担当患者のみ）は従来どおり。
- `GET /staff`
  - `q`: 氏名・かな・職員コードの部分一致。
  - `include_retired`: 未指定 = 従来どおり（論理削除以外の全員・`status` を問わない）。`true` = 論理削除済み（退職で削除した人）も含める（admin のみ有効）。`false` = `status='retired'` も除く。
  - staff ロールは従来どおり自分の行だけ。
- フロントエンドの呼び出しは `?limit&offset` だけ（`frontend/lib/queries/patients.ts:231`・`staff.ts:53`）。新しい引数を送っていないので画面の結果は変わらない（テストで確認）。

## 4. テスト

`cd backend && python -m pytest tests/test_agent_week_context.py tests/test_agent_staff_week.py tests/test_agent_list_filters.py -q` → **12 件すべて通過**（SQLite のメモリ DB）。

| ファイル | 確かめていること |
|---|---|
| `test_agent_week_context.py`（5） | staff → 403・未ログイン → 401・無い ISO 週 → 422・空の週・extract.py と同じ意味（取消/削除/週外の訪問を除く・取消だけの患者も出る・コース担当のフォールバック・青ピン・NG/同住所/固定枠の可動域・active の職員だけ・予定の壁時計と取消/週外の除外・未配置の○（pool と「消えた placed」・長さ）・担当歴の 28 日と取消除外と集計・プールの判定） |
| `test_agent_staff_week.py`（2） | staff → 403・勤務/休み/時間変更・コース担当の訪問を数える・取消/削除を数えない・分の合計・最初/最後・日をまたぐ予定・2 人目・同行・休職の既定除外と `include_inactive` |
| `test_agent_list_filters.py`（5） | 引数なしで従来と同じ結果（患者・職員）・`status`/`q`（かな・ワイルドカード）・`include_retired` の 3 通り・staff ロールは引数があっても自分だけ |

`python -m ruff check` / `ruff format` は変更したファイルで通過。

## 5. デプロイ前の確認

- **本番への反映は PO の了承が要る**（読むだけの API でも本番のコードが変わるため）。反映の前に `pg_dump` を取る約束は守る（このデプロイでは DB は変えない）。
- **マイグレーションなし**（alembic は 0090 のまま）。backend の再起動だけで有効になる。
- 予定の時刻の比較は既存の `staff_events.py` と同じく tz なしの値で行う。backend コンテナが `TZ=UTC` である前提（本番はそう・R3 §「VPS は UTC」）。TZ を変えると予定の週の境目が 9 時間ずれる。
- week-context は患者（active 全員）・NG・勤務・拠点・テンプレートを全件読む。今の件数（患者 200 人前後）なら 1 回で数百 KB 程度。利用者名を含むので、応答を git に入れない・公開しない（`docs/reports/agent/` は git 管理外）。
- 反映後の確認（読むだけ）: VPS 内から `GET http://127.0.0.1:18001/api/v1/agent/week-context?iso_year=2026&iso_week=42` を admin で叩き、`visits` の件数が extract.py の `week.json` と一致すること・`history` の件数が `history.json` と一致することを見る。
