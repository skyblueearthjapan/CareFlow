# 共通の土台（段階 0）— 休み申請と受け入れ枠の両方が使うもの（2026-10-20）

> 状態: ディレクター決定（2026-10-20）。2 つの設計書（`leave-and-kinmu-sheet-design-2026-10-20.md`・`acceptance-strict-design-2026-10-20.md`）はこの文書に合わせる。食い違ったら**この文書が正**。
> 段階 0 を先に作ってマージし、その上で休み（段階 L）と受け入れ枠（段階 A）を並行で作る。

## 0-1. 休みの種類と判定 — `backend/app/core/leave.py`（フロントは `frontend/lib/leave.ts` に同じ値）
- `FULL_DAY_OFF = frozenset({"off", "paid_off"})`
- `HALF_DAY_OFF = frozenset({"am_off", "pm_off"})`
- `LEAVE_TYPES = FULL_DAY_OFF | HALF_DAY_OFF`（`custom_time` は休みではない）
- `AM_OFF_UNTIL = time(13, 0)`（午前休 = 13:00 から勤務）／`PM_OFF_FROM = time(12, 30)`（午後休 = 12:30 から休み）
- `leave_window(type, shift_start, shift_end) -> (work_start, work_end) | None`（終日なら None）
- `classify_kaipoke_leave(subject, start=None, end=None, shift_start=None, shift_end=None) -> "off" | "am_off" | "pm_off" | "paid_off" | "unknown" | None`
  - **規則の全文はここだけ**（休み・受け入れ枠の設計書はこれを参照するだけ）。上から順に当てて、最初に当たったものを返す。
  - 時刻・勤務時刻は省略できる（型で勤務しない曜日・時刻の無い呼び出し）。勤務時刻が無いときは `config.business_start/end`（既定 9:30〜18:00）で代える。

  | 順 | 条件（件名は NFKC で正規化・前後の空白を除く） | 結果 |
  |---|---|---|
  | 1 | 「休憩」「休日出勤」「休日当番」「休薬」を件名から取り除いたあと、「休」「有給」「有休」「年休」「欠勤」「忌引」「病欠」のどれも含まない | None（休みではない） |
  | 2 | 「有給」「有休」「年休」を含む（時刻が半日でも） | `paid_off`（年休 = 年次有給休暇・10-20 ディレクター決定） |
  | 3 | 「AM」「午前」を含む | `am_off` |
  | 4 | 「PM」「午後」を含む | `pm_off` |
  | 5 | 「代休」「欠勤」「病欠」「忌引」「公休」を含む | `off` |
  | 6 | 時刻が無い | `off` |
  | 7 | 始まりが勤務の始まり＋30 分以内、かつ終わりが勤務の終わり−60 分以降 | `off` |
  | 8 | 始まりが勤務の始まり＋30 分以内、かつ終わりが 12:00〜14:00 | `am_off`（例: 休み 9:00〜13:00） |
  | 9 | 始まりが 12:00〜13:30、かつ終わりが勤務の終わり−60 分以降 | `pm_off`（例: 休み 12:30〜17:30） |
  | 10 | それ以外（休みの語はあるが時間が決まりに合わない） | `"unknown"`（管理者が選ぶ） |

  - 試験の正解（必ず入れる）: 休み 9:00-18:00→off／休み 8:30-17:30（勤務 8:30-17:30）→off／PM休み 12:30-17:30→pm_off／休み 9:00-13:00→am_off／休み 12:30-17:30→pm_off／有給→paid_off／年休→paid_off／代休・欠勤・病欠・忌引・公休→off／休憩・休日出勤・休日当番・休薬→None／会議→None／休み 10:00-11:00→unknown／休み（時刻なし）→off。
- `is_leave_event(subject)` は `classify_kaipoke_leave(...) not in (None,)` を返す薄い関数として残し、`dashboard_staff_performance.py` の今の関数はこれを呼ぶよう置き換える（ダッシュボードの結果が変わらないことを試験で確かめる）。
- `app/core/leave.py` 以外に 13:00・12:30・`== "off"` を書かない（受け入れ枠・エンジン・モニター・エージェントの SQL も、ここかフロントの `lib/leave.ts` を読む）。

## 0-2. カイポケの材料の保存 — 新しい表 `kaipoke_source_snapshots`（alembic **0098**）
`kaipoke_csv_snapshots`（●未送信・予実比較・送信後の `drop_snapshots` が使う）には**触らない・書かない**。
| 列 | 内容 |
|---|---|
| `id` | UUID |
| `kind` | `plan_csv` / `actual_csv` / `tasks` |
| `period_start` | `plan_csv`・`actual_csv` は月の 1 日、`tasks` は週の月曜 |
| `office_id` | 常に NULL（**拠点で行を絞らない**。職員が他拠点で訪問した時間も塞ぐため） |
| `rows` | JSONB。CSV は 1 行 1 オブジェクト（ヘッダ名で）、`tasks` は RPA の個別業務の配列そのまま |
| `row_count` | 件数 |
| `covered_dates` | JSONB。`tasks` のとき RPA の画面に出た日の一覧（`week_dates`／ヘッダの日付）。月をまたぐ週で表示されなかった日を「未確認」と区別するため。CSV のときは NULL |
| `status` | `ok` / `empty`（該当なし）/ `failed` |
| `error` | 失敗の理由（短く） |
| `fetched_at` / `job_id` | いつ・どのジョブで |
- UNIQUE(`kind`, `period_start`)。取り直したら行を置き換える（履歴は持たない）。
- 空の CSV は `empty`（例: 月の 1 日の実績 0 件）として保存し、`failed` とは分ける。
- **失敗したとき**: 前に `ok`（または `empty`）の行があれば書き換えず、失敗はジョブの `result_summary.failed[]` にだけ残す。前の行が無いときだけ `status='failed'` の行を書く。
- **材料が新しいかの判定は使う側が行う**: `fetched_at` が「今回の取り直しのジョブの開始」以降なら新しい。休みの照合（月の締め）は、新しくない週の日を「未確認」として差分 0 件にしない。受け入れ枠は古い材料を使ってよいが、`fetched_at` を画面に出す。

## 0-3. 取り直しの仕組み — `backend/app/services/kaipoke/sources.py`
- `fetch_month_csv(db, client, kind, month)`、`fetch_week_tasks(db, client, week_start)`。どちらも 0-2 に保存。
- ジョブは既存の `KaipokeJob` を使う（`status` は `completed` / `failed` / `cancelled` のみ・一部失敗は `result_summary.failed[]`・`week_start` には範囲の最初の月曜）。`params.op` = `acceptance-refresh` / `leave-compare` / `leave-outbound`（休みをカイポケへ送る。休みの段階 L が使う。非同期・画面は状態を数秒おきに読む）。
- `job_type`（`String(16) NOT NULL`）は、取り直し（acceptance-refresh・leave-compare）が `"fetch"`、送信（leave-outbound）は今のイベント送信（`op="events-outbound"`）と同じ **`"push"`**（`integrations.py:4319` で確認・段階 0 実装 `5bc1f514`）。409 の判定では、30 分以上 `updated_at` が更新されていない `pending`/`running` は残骸として数えない（書き換えはしない）。op の名前を `job_type` に入れない。
- `covered_dates` は `sources.py` の中で作る（`integrations.py` の私的な関数 `_events_covered_dates` は呼ばず、同じ処理を持つ）。
- 始める前に、種類を問わず RPA を使うジョブ（`KaipokeJob` が `pending`/`running`）があれば 409。
- 1 本ごとに RPA の空きを確かめ、塞がっていれば待って再試行（30 秒おき・最大 10 回）。それでも駄目ならその本を `failed` にして次へ。
- イベント取り込みの非同期の経路（RPA の結果の置き場が 1 つ）を壊さないよう、個別業務を取るときは同じ「RPA の空き確認 → 自分の結果を受け取ってから次」の順を守る。
- 17〜18 時台は押せるが画面に注意を出す。
- 汎用の後始末 `_reconcile_latest_job` の除外の一覧（`backend/app/api/v1/integrations.py:560` 付近）に `acceptance-refresh`・`leave-compare`・`leave-outbound` を足す。あわせて、この 3 つは「作られてから 30 分で閉じる」の待ち時間を **120 分**にする（待ちと再試行で 30 分を超えても途中で閉じられないように）。どちらも段階 0 で行い、これ以外に `integrations.py` は触らない。
- 長い取り直しの間、1 本ごとにジョブの `updated_at`（と進み具合）を書き戻す。

## 0-4. 職員の名前の結び付け
CSV・個別業務の職員名は、空白（全角・半角）を除いて `staff.name` と比べる（既存の `name_match.py` があればそれを使う）。結び付かない名前は件数と名前を結果に返す（黙って捨てない）。

## 0-5. migration の番号
- **0098** = 段階 0（`kaipoke_source_snapshots`）
- **0099** = 休み（override の列・申請の CHECK・`staff` の列・休みの新しい表。休み設計書 §9 #1。`down_revision='0098'`）
- **0100** = 受け入れ枠（必要なら。`down_revision='0099'`）
- 並行で作るとき、受け入れ枠の migration は仮に `down_revision='0098'` で作り、統合のときにディレクターが `'0099'` へつなぎ直す。head が 1 つであることを統合の試験で確かめる。

## 0-6. 休み設計書の照合が使うもの
- 休みの「カイポケと比べる」も 0-3 の仕組みで個別業務と実績 CSV を取り、0-2 に保存したものを読む（自分で RPA を呼ばない）。`leave_compare_runs` は差分の結果を持つだけ。
- 受け入れ枠と同じ週を取り直したばかりなら、その材料をそのまま使ってよい（`fetched_at` を画面に出す）。
