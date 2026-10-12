# 赤ピン（完全固定）撤去 — API・DB・テストの棚卸し

対象: `backend/app/api/**`・`backend/app/schemas/**`・`backend/app/models/**`・`backend/alembic/versions/**`・`backend/tests/**`・`backend/scripts`（services/ は別担当）
調査は読むだけ。repo のファイル・git・サーバーには触れていない。行番号は 2026-10-12 時点の develop（c17aafe）のもの。

---

## 1) 要約

- **赤ピンの実体は 2 列**: `patient_fixed_visits.movability`（4 値。CHECK `ck_pfv_movability`）と、その非推奨ミラー `patient_fixed_visits.is_pinned`（部分 index `ix_pfv_pinned_patient`）。モデル: `backend/app/models/patient_fixed_visit.py:104-123, 155-158`。
- **赤ピンだけを扱う API は 2 本**: `PATCH /api/v1/patients/fixed-visits/{pfv_id}/pin` と `POST /api/v1/patients/fixed-visits/pin/bulk`。どちらも削除する。
- **値を書き込む・引き継ぐ経路は 8 か所**: PUT fixed-visits・from-week・from-week-bulk・place-and-fix・sync-week-visits-to-fixed・dismiss の昇格・apply-swap の `_apply_pfv_move`・propose-unblock/apply の `_insert_target_pfv`。
- **赤ピンを理由に 422 を返す API ガードは 3 か所**: apply-swap（5055-5069）、scope-optimization/apply と propose-unblock/apply の `_validate_and_move_one`（5654）、propose-unblock/apply の `_insert_target_pfv`（6270-6281）。
- **services の例外を API が 422 に変換している経路も 3 か所**: apply-week-only（`pinned_visit_moved`）、apply-individual / pool-bulk-apply（`apply_individual_proposal` が出す 422）。
- **使われていない分岐**: `validate_pfv_changes` で `severity="error"` を出すのは `_check_pinned`（V2）だけ。V2 は 2026-08-09 に呼び出しが外れている。このため PUT・apply-swap・unblock/apply にある `has_errors` → 422 の分岐（「完全固定 (is_pinned) の枠を…」「ピン留めされた枠は入れ替えできません」）は今は発火しない。
- **一番の地雷 = `extra="forbid"`**: `PatientFixedVisitV2Base` と `ImprovementDismissRequest` は余分なキーを拒否する設定になっている。BE が先に `is_pinned` / `movability` / `promote_movability` をスキーマから消すと、まだそれらを送る FE の PUT / dismiss が全部 422 になる。**FE が先に送るのをやめる**か、**BE は 1 リリースの間「受け取って捨てる」**かのどちらかが必要（§7）。
- **migration の案**: 新しく `0091_drop_pfv_pin_and_movability` を作り、index → CHECK → 2 列の順に drop する。過去の migration（0037/0047/0050/0065/0067）は履歴として残し、編集しない。列を消すのは、コードが読まなくなってから（2 段リリース）。
- **テストへの影響**: 約 40 ファイルに及ぶ。中心は「赤ピンそのものの」テストで、まるごと削除になる（`test_g21_pfv_pin_api.py`・`test_movability_lock_fence.py` など）。数が多いのは、`time_flexible` / `day_flexible` / `locked` を**シナリオの許可や壁として使っている**提案エンジン系のテスト（improvement / scope / swap / unblock / constraint_confirm）。この系統は services 側の新ルール（希望訪問スケジュールの範囲）に合わせて、シナリオの土台から書き直しになる。
- **今のテストの状態**: 関係する 32 ファイルを実行した結果は **すべて成功（skip 1 = PG 専用の `test_t1_8`）**。今の時点で失敗しているテストは無い。

---

## 2) API エンドポイント表

ルーターの前置き: `patient_fixed_visits.router` と `patient_sync.router` は `/patients`、`schedule.router` と `schedule_v2.router` は `/schedule`（`app/api/v1/__init__.py:92,105,213,217`）。

### 2-1. 赤ピン専用（まるごと削除）

| メソッド・パス | ハンドラ（file:line） | 中身 | 撤去時 |
|---|---|---|---|
| PATCH `/api/v1/patients/fixed-visits/{pfv_id}/pin` | `update_pfv_pin` patient_fixed_visits.py:940-983 | is_pinned と movability を切り替え（on→locked / off→unknown）＋ audit | **エンドポイント削除** |
| POST `/api/v1/patients/fixed-visits/pin/bulk` | `bulk_pin_pfvs` patient_fixed_visits.py:986-1062 | 一括切替。pfv_id が重複すると 422、存在しない ID があると全体をロールバック | **エンドポイント削除** |
| （補助関数）`_release_pin_lock` | patient_fixed_visits.py:922-937 | 上の 2 本でしか使っていない | **削除** |
| （セクション見出しコメント） | patient_fixed_visits.py:913-920 | | 削除 |

### 2-2. 値を読み書き・引き継ぐ（エンドポイントは残し、赤ピンの部分だけ外す）

| メソッド・パス | ハンドラ・該当行 | 赤ピンとの関わり | 撤去時 |
|---|---|---|---|
| GET `/patients/{patient_id}/fixed-visits` | `get_fixed_visits` patient_fixed_visits.py:411 | `PatientFixedVisitV2Read` で `is_pinned` / `movability` を返す | レスポンススキーマから 2 項目を削除（§3） |
| PUT `/patients/{patient_id}/fixed-visits` | `put_fixed_visits` patient_fixed_visits.py:532-776 | 587-604: `validation.has_errors` → 422「完全固定 (is_pinned) の枠を変更・削除しようとしています」（今は発火しない）。674-695: `is_pinned=(item.movability=="locked")` と `movability=item.movability` を INSERT | 422 分岐を削除（services が `_check_pinned` を消すのに合わせる）。INSERT から 2 kwarg を削除。コメント 615-617・674-675 も整理 |
| POST `/patients/{patient_id}/fixed-visits/validate` | `validate_fixed_visits` :443 | body が `PatientFixedVisitV2Base`（movability などを持つ） | スキーマに連動して自動で変わる |
| POST `/patients/{patient_id}/fixed-visits/from-week` | `from_week` :1065-1170 | 1085-1088 の docstring、1133-1155: `_load_pfv_pin_partition` で pinned 行を残して `is_pinned.is_(False)` だけ DELETE、pinned の slot は skip、movability を引き継ぐ | **素直な DELETE→INSERT に戻す**（pinned_slots と movability_by_wd_start の引数を削除） |
| POST `/patients/fixed-visits/from-week-bulk` | `from_week_bulk` :806-911 | 824-828 の docstring、879-901: 上と同じ（pinned を残す・引き継ぐ） | 同上 |
| （補助）`_load_pfv_pin_partition` | :283-309 | 上の 2 本専用 | **削除** |
| （補助）`_insert_pfv_from_visits` / `_add_one_pfv` | :312-405 | 引数 `pinned_slots`・`movability_by_wd_start`・`movability` | 引数と分岐を削除 |
| POST `/api/v1/schedule/place-and-fix` | `place_and_fix` schedule.py:841-、該当 1071-1111 | 置き換える前の movability を `prior_movability_paf` に退避し、新しい行へ引き継ぐ（`is_pinned` はミラー） | 退避の dict と 2 kwarg を削除 |
| POST `/patients/{patient_id}/sync-week-visits-to-fixed`・POST `/patients/bulk-sync-week-to-fixed` | `_apply_sync_changes` patient_sync.py:289-335（呼び出し元は :344 / :615） | INSERT のとき、同じ曜日の既存 PFV から `is_pinned` / `movability` を引き継ぐ（317-328） | 引き継ぎを削除。docstring 301-306 も |
| POST `/api/v1/schedule/v2/improvement-suggestions/dismiss` | `improvement_dismiss_endpoint` schedule_v2.py:4762-4861、`_PROMOTE_TARGET` 4755-4759 | `promote_movability=true` のとき PFV.movability を昇格（day_immovable→time_flexible / time_immovable→locked）。is_pinned の行は飛ばす（4832-4853）。レスポンスは `movability_updated` / `new_movability` | **昇格ブロックと `_PROMOTE_TARGET` を削除**。却下の upsert（suggestion_dismissals）は残す。リクエスト・レスポンスのスキーマも整理（§3） |
| POST `/api/v1/schedule/v2/improvement-suggestions/apply-swap` | `improvement_apply_swap_endpoint` :4963-、ガード 5055-5069、`has_errors` 422 5131-5145（今は発火しない） | locked / is_pinned なら 422「完全固定の枠は入れ替えできません」 | **ガードを削除**。has_errors 分岐の文言（「ピン留め…」）は validator の方針次第で削除 |
| （補助）`_pfv_to_base` / `_proposed_with_move` | :4869-4909 | `is_pinned=` と `movability=` を写している | 2 kwarg を削除 |
| （補助）`_apply_pfv_move` | :4912-4960（4923 docstring、4953-4954） | 別の曜日へ移すとき target に movability と is_pinned を複写 | 2 行を削除 |
| POST `/api/v1/schedule/v2/scope-optimization/apply` と POST `/v2/propose-unblock/apply` | `_validate_and_move_one` :5614-、ガード 5654-5662 | locked / is_pinned なら 422「手順N: 完全固定の枠は動かせません」 | **ガードを削除** |
| POST `/api/v1/schedule/v2/propose-unblock/apply` | `_insert_target_pfv` :6220-6345、ガード 6270-6281、6288-6300 | 既存の完全固定の slot を上書きすると 422。新しい行に is_pinned / movability を引き継ぐ | ガードと 4 kwarg を削除 |
| GET `/api/v1/schedule/v2/improvement-suggestions` | :4698-4752（4745-4746） | `filtered_summary.pinned` / `.locked` を services から写している | スキーマの 2 項目を削除。services に連動 |
| POST `/api/v1/schedule/v2/scope-optimization/simulate` | :5370-、5431-5432 | `excluded.pinned` / `.locked` | 同上 |
| POST `/api/v1/schedule/v2/propose-unblock` | :6144-、6208-6209 | `summary.pinned` / `.locked` | 同上 |
| POST `/api/v1/schedule/v2/apply-week-only` | `apply_week_only_endpoint` :2589-、2736-2755 | services の `PinnedVisitMovedError` を 422 `code="pinned_visit_moved"` に変換 | services が例外を消すなら except 節を削除 |
| POST `/api/v1/schedule/v2/apply-individual`・POST `/v2/pool-bulk-apply` | :955 / :4115（4133・4308 のコメント） | `apply_individual_proposal`（services）が pinned の既存 PFV で 422（`test_f7_apply_individual_rejects_pinned_existing` で確認済み） | API 側はコメント修正だけ。本体は services |
| POST `/api/v1/schedule/v2/reset-to-fixed` | :1240-（`payload.mode`） | `mode: "legacy"|"auto"`。説明は「legacy = 全 PFV を pinned 扱い / auto = pinned PFV だけ厳守」（auto_schedule_v2.py:560-567） | **要判断**。赤ピンが無くなると auto の意味が変わる。スキーマの説明と mode 自体の存続を services と決める |
| POST `/api/v1/schedule/v2/visit-move-week-only` | :1471-、docstring 1488-1489 | docstring に「is_pinned=True の PFV があれば 422」が残っているが、実装は 2026-08-09 に撤廃済み（1523-1525） | docstring の修正だけ |
| DELETE 訪問（visits.py:1157-1159） | コメントだけ | 赤の 422 は撤廃済み | コメント整理 |

### 2-3. 青ピンとの絡み（青ピンは範囲外。注意点として記す）

- `schemas/v2/auto_schedule_v2.py:773-830`（`VisitWeekPin*`）の docstring が赤ピン `PFV.is_pinned` と、赤ピンの bulk API（「全件ピン留め」）に触れている → **docstring だけ修正**。項目名 `pinned` は青ピンのものなので**残す**。
- `schedule_v2.py:2513` の `visit_week_pin_bulk_endpoint` の docstring「赤ピンの『全件ピン留め…』と対になる」→ 文言だけ修正。
- `copy_week` の `keep_pinned`（schemas/v2/copy_week.py:166）は `v.week_pinned`（青）→ **無関係**。
- 1 つのエンドポイントで赤と青の両方を扱っているものは無い。赤の API（`/patients/fixed-visits/...pin`）と青の API（`/schedule/v2/visits/.../week-pin`）は別ルーター・別スキーマ。

### 2-4. PFV を作るが赤ピンに触れていない経路（変更不要。列を drop してもサーバー既定値に頼らないので安全）

`schedule.py:446`（fix_or_pattern）・`patient_sync.py:552`・`schedule_v2.py:2986`（update-fixed-time-master）・`schedule_v2.py:6333`。

---

## 3) スキーマ・モデル・制約

### 3-1. モデル `backend/app/models/patient_fixed_visit.py`

| 箇所 | 中身 | 撤去時 |
|---|---|---|
| :104-112 `is_pinned` | Boolean・NOT NULL・default False・server_default false | 列を削除（§4 の migration と同時） |
| :114-123 `movability` | String(16)・NOT NULL・default/server_default 'unknown' | 列を削除 |
| :155-158 `CheckConstraint ck_pfv_movability` | 4 値 | 削除 |
| （モデル外）`ix_pfv_pinned_patient` | 0037 で作成。モデルの `__table_args__` には**無い**（モデルと DB が今もずれている） | migration で drop |
| import の `Boolean` / `sa` | is_pinned だけが使っている | 未使用になれば削除 |

### 3-2. スキーマ

| file:line | 項目 | 使う API | 撤去時 |
|---|---|---|---|
| schemas/v2/patient_fixed_visit.py:19-23 | `PatientFixedVisitMovability` Literal | Base | 削除 |
| 同 :64-74 | `PatientFixedVisitV2Base.is_pinned` | PUT/validate の body、GET/PUT/from-week/pin のレスポンス | **削除**（`extra="forbid"` に注意。§7） |
| 同 :75-85 | `PatientFixedVisitV2Base.movability` | 同上 | **削除**（同上） |
| 同 :157-171 | `PfvValidationWarningOut` の docstring にある `pinned_protection` | PUT レスポンス | 文言を修正 |
| 同 :247-271 | `PatientFixedVisitPinUpdate` / `PatientFixedVisitPinBulkItem` | pin の 2 API | **クラスごと削除**（patient_fixed_visits.py の import も） |
| schemas/v2/improvement_suggestion.py:223-232 | `ImprovementFilteredSummary.pinned` / `.locked` | GET improvement-suggestions | 削除（services に連動） |
| 同 :255-265 | `day_restricted`（「movability が同曜日限定のため抑制」） | 同上 | services の判断次第。希望範囲ベースに意味を言い換えるか削除 |
| 同 :104-107・:178-181 | `requires_patient_confirmation`（「movability=unknown のとき」） | 提案・swap 相手 | **残す見込み**（希望外なら要確認）。説明文を直す |
| 同 :183-189 | `within_preference` | 同上 | **残す**（撤去後の主役）。説明文の movability への言及を削除 |
| 同 :296-303 | `ImprovementDismissRequest.promote_movability` | POST dismiss | **削除**（`extra="forbid"`。FE は `.default(false)` で常に送っている可能性が高い → §7） |
| 同 :312-315 | `ImprovementDismissResponse.movability_updated` / `new_movability` | POST dismiss | 削除 |
| schemas/v2/unblock.py:229-230 | `pinned` / `locked` | POST propose-unblock | 削除 |
| 同 :237 | `confirmation_required` の説明「希望外+movability 不明」 | 同上 | 説明文の修正 |
| schemas/v2/scope_optimization.py:120-129・139 | `pinned` / `locked`、`confirmation_required_excluded` の説明 | POST scope-optimization/simulate | 削除と説明文の修正 |
| schemas/v2/auto_schedule_v2.py:560-567 | reset-to-fixed の `mode` の説明 | POST reset-to-fixed | 要判断（§7） |
| schemas/v2/auto_schedule_v2.py:773-830 | 青ピンの docstring にある赤への言及 | week-pin | 文言だけ |

### 3-3. suggestion_dismissals（見送り → 昇格の経路）

- モデル `app/models/suggestion_dismissal.py`: `kind`（time_change/day_change/swap）と `reason`（day_immovable/time_immovable/staff_relation/other）は CHECK で固定。**テーブル自体は却下の記録として残せる**（同じ提案が蒸し返されないようにする役目は赤ピンと別）。
- 赤ピンとの接点は `schedule_v2.py:4755-4759, 4832-4853` の**昇格の書き込みだけ**。削除すれば、reason の `day_immovable` / `time_immovable` は「却下の理由ラベル」として残る。理由ラベル自体を残すかは PO 判断（§7）。

### 3-4. FE が使っているレスポンス項目（撤去すると FE の型が壊れる箇所）

| BE の項目 | FE 側の定義（参考・FE は別担当） | 消したときの影響 |
|---|---|---|
| PFV Read の `is_pinned` / `movability` | `frontend/lib/schemas/v2/patient_fixed_visit.ts:74,85`（どちらも `.optional()`） | zod は通る。参照している UI（PatientFixedVisitsPanel・CourseDayTablePanel・TimelineDayBoard・MovabilityMark・WeekdayScheduleCard など）は「undefined = 未固定」として静かに動く。型の掃除が必要 |
| PUT body の `is_pinned` / `movability` | 同上（FE が送っている） | **BE 側で消すと 422**（extra=forbid） |
| pin の 2 API | `frontend/lib/schemas/g21.ts:5-39`、`frontend/lib/queries/g21.ts:84-130`、`PinScopeMenu.tsx` | 404 になる |
| dismiss の `promote_movability` / `movability_updated` / `new_movability` | `frontend/lib/schemas/v2/improvementSuggestion.ts:212-220`、`lib/queries/improvementSuggestions.ts:104-106`、`DismissReasonDialog.tsx` | リクエストは 422（extra=forbid）。レスポンスは `.default()` があるので通る |
| `filtered_summary.pinned/locked`・scope の `excluded.pinned/locked`・unblock の `summary.pinned/locked` | improvementSuggestion.ts ほか | 項目が消えるだけ（FE が `.default` を持っていれば通る。要確認） |
| 422 detail の `code="pinned_visit_moved"` と「完全固定 (is_pinned)…」の文言 | `frontend/lib/api/__tests__/errorMessage.test.ts:19-27` | 出なくなる。FE のテスト用データだけが残る |
| `visit.is_pinned` | `frontend/lib/schemas/visit.ts:87-92`（「将来 BE が返すかも」の optional） | BE は返していない。FE 側の掃除だけ |

---

## 4) migration の経緯と削除案

### 4-1. 経緯（すべて `backend/alembic/versions/`）

| rev | ファイル | 赤ピンに関する中身 | 撤去時の扱い |
|---|---|---|---|
| 0037 | `0037_g21_pfv_is_pinned_and_same_address_links.py` | `is_pinned` を追加（server_default true で既存行を true に → false に切替）、部分 index `ix_pfv_pinned_patient`（PG: `WHERE is_pinned = true` / SQLite も WHERE 付き）。同時に same_address_links と office_feature_flags も作成 | **編集しない**（履歴。後ろの 2 つは別機能で現役） |
| 0038 / 0048 / 0051 | revision の文字列に 0037 / 0047 / 0050 の名前が入っているだけ | — | 無関係 |
| 0047 | `0047_movability_and_suggestion_dismissals.py` | `movability` を追加（server_default 'unknown'）、`is_pinned=true → 'locked'` の backfill、CHECK `ck_pfv_movability`（PG は raw ALTER、SQLite は batch）。同時に `suggestion_dismissals` テーブル | 編集しない |
| 0049 | `0049_swap_dismissal_kind.py` | dismissals の kind に 'swap' を追加（説明文で 0047 に言及） | 無関係 |
| 0050 | `0050_release_pin_movability_locked.py` | `movability='locked' AND is_pinned=false → 'unknown'`（downgrade は何もしない） | 編集しない |
| 0065 | `0065_reset_machine_set_movability_locked.py` | `is_pinned=true AND locked → 'unknown'`（機械が付けた locked を戻す） | 編集しない |
| 0066 | `0066_visit_week_pinned.py` | `visits.week_pinned`（**青**） | 範囲外・残す |
| 0067 | `0067_pin_mirrors_locked.py` | is_pinned=true の行を locked に昇格 → `is_pinned := (movability='locked')` に同期 | 編集しない |
| 最新 | `0090_visit_signatures.py`（本番も 0090） | — | 新しい migration はここに続ける |

### 4-2. 削除案（2 段リリースを推奨）

**第 1 段（コードだけ・migration なし）**
1. API とサービスが `is_pinned` / `movability` を読まない・書かないようにする。どちらにも server_default があるので、列が残っていても INSERT は通る。
2. スキーマでは、FE が送ってくる `is_pinned` / `movability` / `promote_movability` を**しばらくは受け取って捨てる**（`Field(default=None, exclude=True, deprecated=...)` のような形で残す）。または FE を先にデプロイして送るのをやめさせる。
3. レスポンスからは消してよい（FE は optional / default で受けている）。

**第 2 段（新しい migration `0091_drop_pfv_pin_and_movability`。down_revision は `0090_visit_signatures`）**
1. （保全）drop する前に、今の `movability <> 'unknown'` の行を記録する。例: `CREATE TABLE _bak_pfv_movability_20261012 AS SELECT id, patient_id, weekday, slot_index, start_time, movability, is_pinned FROM patient_fixed_visits WHERE movability <> 'unknown' OR is_pinned`。本番 DB のバックアップでも代わりになる。完全固定が実際に使われていた行の件数を PO に報告するためでもある。
2. `op.drop_index("ix_pfv_pinned_patient", table_name="patient_fixed_visits")`（`if_exists` 相当。SQLite のテストでは create_all がこの index を作らない点に注意）
3. `op.drop_constraint("ck_pfv_movability", "patient_fixed_visits", type_="check")`
4. `op.drop_column("patient_fixed_visits", "movability")` と `op.drop_column(..., "is_pinned")`
5. SQLite（テスト）では `batch_alter_table` で 2〜4 をまとめて行う。
6. downgrade: 2 列を server_default（false / 'unknown'）で付け直し、CHECK と index を作り直す。**値は戻らない**と docstring に書く（0050 と同じ方針）。

**列を残すだけの案（低リスク・後始末が残る）**: migration を作らず、コードから参照を外すだけ。CHECK と index は残るが害は無い。ただし ORM モデルに列が残る限り「使われていない列」として誤用されうる。**推奨は 2 段で drop**。

**テストへの影響**: migration のテスト（`test_migration_0047.py` / `0050` / `test_g21_migration.py`）は migration のモジュールを直接読み込み、まっさらな SQLite で upgrade するので、0091 を足しても壊れない。壊れるのは ORM モデルを調べる 3 本だけ（§5）。`script.get_heads()` が 1 本であることを確かめるテスト（test_actual_time_adjust.py:1827、test_migration_0089.py:104 など）は、0091 の down_revision を正しく 0090 にすれば通る。

---

## 5) テスト一覧（ファイル::関数 / 処置）

凡例: **削除** = 赤ピンという概念そのもののテスト。**書換** = 目的は残るが、赤ピンを前提にした検証やシナリオを直す。**kwarg** = PFV を作るときに `is_pinned=` / `movability=` を渡しているだけで、引数を消せば済む。**S** = services 側の方針に連動して決まる（services 担当と調整）。

### 5-1. ファイルごと削除してよいもの

- `tests/test_g21_pfv_pin_api.py`（9 本すべて）: `test_t2_1_patch_pin_toggles_is_pinned`・`test_unpin_unlocks`・`test_pin_toggle_roundtrip_normalizes_to_two_values`・`test_bulk_unpin_unlocks_all`・`test_t2_2_bulk_pin_updates_multiple`・`test_t2_3_staff_cannot_toggle_pin`・`test_t2_4_pin_toggle_writes_audit_log`・`test_t2_5_bulk_pin_duplicate_pfv_id_returns_422`・`test_t2_6_bulk_pin_atomic_rollback_on_missing` → **削除**
- `tests/test_movability_lock_fence.py`（5 本中 4 本が該当。残る 1 本もこの機能の検証）: `test_pfv_movability_locked_predicate`・`test_movability_locked_visit_is_frozen_by_corrections`・`test_legacy_pipeline_propagates_movability_locked`・`test_unknown_movability_does_not_set_flag` → **削除（S: `_pfv_movability_locked` と `is_movability_locked` の撤去と連動）**

### 5-2. API・スキーマ系

- `tests/test_patient_fixed_visits.py`（25 本中 10 本）。補助関数 `_make_pfv_with_movability` も削除。
  - `test_put_locked_row_is_editable` → 削除
  - `test_put_is_pinned_mirrors_locked` → 削除
  - `test_put_movability_roundtrip` → 削除
  - `test_put_default_movability_is_unknown` → 削除
  - `test_put_pinned_row_keeps_sent_movability` → 削除
  - `test_patch_unpin_unlocks_movability` → 削除
  - `test_patch_unpin_normalizes_legacy_movability` → 削除
  - `test_patch_pin_on_sets_locked` → 削除
  - `test_patch_unpin_clears_stray_locked` → 削除
  - `test_bulk_pin_toggle_sets_movability` → 削除
  - （追加を推奨）「PUT body に `is_pinned` / `movability` が来ても 422 にならない（移行期間）」または「来たら 422」のどちらか、決めた仕様を固定するテスト
- `tests/test_suggestion_dismissals.py`（13 本中 8 本）。補助 `_seed_pfv` の `movability` / `is_pinned` 引数は kwarg。
  - `test_promote_day_immovable_sets_time_flexible`・`test_promote_time_immovable_sets_locked`・`test_promote_leaves_pinned_row_locked`・`test_promote_other_reason_no_update` → **削除**（昇格の経路が無くなるため）
  - `test_get_locked_pfv_counts_in_summary` → 削除
  - `test_dismiss_creates_row` → 書換（`movability_updated` の assert を外す）
  - `test_get_empty_returns_200_with_summary`・`test_get_no_current_visit_counted` → 書換（summary の `pinned` / `locked` キーの assert を外す）
- `tests/test_change_scope_u0.py`（11 本中 4 本）。補助 `_make_pfv(is_pinned, movability)` は kwarg。
  - `test_sync_week_visits_to_fixed_preserves_pin_on_update` → 削除
  - `test_from_week_bulk_preserves_pinned_and_no_duplicate` → 削除（「重複しない」側は下の書換で担保）
  - `test_from_week_bulk_replaces_nonpinned_and_inherits_movability` → 書換（「置き換わる」ことだけを検証）
  - `test_from_week_single_preserves_pinned` → 削除
- `tests/test_change_scope_u2.py::test_visit_move_week_only_pinned_pfv_is_allowed` → 削除。補助 `_make_pfv` の `movability="day_flexible"`（:117）は kwarg（この補助を使う他の 7 本は引数を消すだけ）
- `tests/test_change_scope_u1.py`: 補助 `_make_pfv(movability=)`（:113-122）と `_seed_sandwich_office` の `movability="time_flexible"`（:402）→ **書換（S）**。対象は `test_scope_apply_pattern_and_week_regenerates_affected_only`・`test_scope_apply_week_only_moves_visits_and_keeps_pfv`。範囲最適化が動いてよい根拠を `weekly_pattern` の希望範囲に置き換える。PUT 系の 3 本は kwarg。
- `tests/test_constraint_confirm_paths.py`: 補助 `_fv_body` が PUT body に `"movability": "day_flexible"` を入れている（:690）→ **書換**（キーを削除。消さないとスキーマ削除後に 422）。影響: `test_f1_put_fixed_visits_ng_requires_confirmation`・`test_f2_put_fixed_visits_acknowledge_notifies`・`test_f3_put_fixed_visits_pattern_only_skips_check`
- `tests/test_constraint_confirm_apply_paths.py`: 補助 `_mk_pfv(movability="day_flexible" が既定)`（:161-178）、`_seed_scope_sandwich` の time_flexible（:429）、`_seed_unblock` の **壁役の filler `movability="locked"`**（:508）→ **書換（S）**。影響: `test_sw1_apply_swap_a_side_ng_requires_confirmation`・`test_sw2_apply_swap_b_side_ng_requires_confirmation`・`test_sw3_apply_swap_clean_passes`・`test_sc1_scope_apply_ng_registered_after_simulate`・`test_ub1_unblock_apply_ng_blocks_without_partial_apply`（locked の壁が無くなると、unblock の探索が filler を動かしにいく恐れ）
- `tests/test_scope_optimization_api.py`
  - `test_apply_pinned_crafted_payload_422` → 削除
  - 補助 `_seed_sandwich_office` の `movability="time_flexible"`（:125）→ 書換（S）。この補助を使う残り 11 本（`test_apply_happy_path_moves_pfv_and_converges`・`test_simulate_returns_steps_with_improvement_contract` など）が影響を受ける
- `tests/test_pool_bulk_apply.py`
  - `test_apply_pinned_violation_rolls_back_all` → 書換か削除（S: apply_individual の pinned 422 が無くなるなら、全体ロールバックを別の失敗要因で確かめる形に）
  - `test_apply_preserves_existing_pfv_union` → kwarg（`is_pinned=False`）
- `tests/test_pfv_smart_master.py`（:89, :175 の `is_pinned=False`）→ kwarg。対象 `test_course_load_aggregates_other_patients_only` と `_seed`
- `tests/test_reset_course_staff_sync.py`: 補助 `_seed_reset_fixture` の `is_pinned=True`（:98）→ kwarg（S: reset の auto/legacy の意味に連動）

### 5-3. migration・モデル系

- `tests/test_migration_0047.py`
  - `test_pfv_model_has_movability_column`・`test_pfv_movability_check_constraint_via_orm` → **削除**（ORM モデルを見ている）
  - `test_movability_column_added_with_default`・`test_movability_backfill_locked_for_pinned`・`test_new_pfv_defaults_to_unknown`・`test_movability_check_rejects_invalid`・`test_downgrade_removes_column_and_table` → 残す（0047 単体の検証。履歴として有効）。不要と判断するならファイルごと削除してもよい
- `tests/test_migration_0050.py`（`test_backfill_releases_only_free_locked`・`test_backfill_does_not_touch_non_locked`・`test_downgrade_is_noop`）→ 残す（0050 単体）
- `tests/test_g21_migration.py`
  - `test_pfv_model_has_is_pinned_column` → **削除**
  - `test_t1_1_existing_pfv_rows_are_backfilled_to_true`・`test_t1_2_new_pfv_row_defaults_to_false`・`test_t1_7_downgrade_removes_column_and_tables` → 残す（0037 単体）
- （新規）`tests/test_migration_0091.py`: 0091 の upgrade で 2 列・CHECK・index が消えること、downgrade で既定値付きの列が戻ること、heads が 1 本であることを検証

### 5-4. エンジン系（S = services 担当の新ルールに連動して決まる）

- `tests/test_g21_final_checks.py`（12 本中 8 本）
  - `test_f2_delete_visit_cascade_is_sealed_422` → kwarg（`is_pinned=True` は前提を作るためだけ）
  - `test_f3_place_and_fix_allows_pinned_overwrite_and_inherits_lock` → 書換（place-and-fix が上書きすることだけを検証し、locked の引き継ぎは外す）
  - `test_f4_apply_week_only_rejects_pinned_overlay_move` → 削除（S）
  - `test_f5_before_includes_pinned_pfv_with_flag`・`test_f5_before_non_pinned_pfv_keeps_flag_false` → 削除（S）
  - `test_f6_pinned_participates_in_constraint_but_keeps_start_time` → 削除（S）
  - `test_f7_apply_individual_rejects_pinned_existing` → 削除（S）
  - `test_f7_apply_individual_allows_no_pinned` → kwarg
- `tests/test_g21_algorithm.py`（32 本中 21 本。補助 `_make_pfv(is_pinned=True が既定)`）
  - pinned の意味そのものを検証している → 削除（S）: `test_t3_1_pinned_keeps_pfv_start_time`・`test_t3_2_pinned_with_cross_address_same_time_warns_but_not_moved`・`test_t3_3_non_pinned_uses_weekly_pattern`・`test_t3_4_invariant_g21_a_pinned_skips_weekly_pattern`・`test_t3_13_reset_to_fixed_auto_mode_pinned_strict`・`test_t3_15_apply_week_only_rejects_pinned_dnd`・`test_reviewer_c1_run_v2_pipeline_uses_build_visits_for_pool_v2`・`test_reviewer_c1_canary_off_keeps_legacy_after`・`test_reviewer_h3_apply_week_only_sets_is_pinned`・`test_reviewer_m3_pinned_moved_violations_has_patient_name`
  - apply_week_only の拒否が pinned PFV を前提にしている → 書換か削除（S）: `test_reviewer_c2_apply_week_only_rejects_weekday_move`・`test_reviewer_c2_apply_week_only_rejects_office_change`・`test_reviewer_c2_apply_week_only_rejects_duration_change`
  - `is_pinned=True` は前提作りだけ → kwarg（ただし reset の legacy/auto の結果が変わりうるので S で確認）: `test_t3_11_before_applies_pending_overlay`・`test_t3_12_reset_to_fixed_legacy_mode_keeps_pfv_time`・`test_t3_14_reset_to_fixed_dry_run_returns_counts`・`test_t3_18_reset_to_fixed_auto_calls_travel_corrections`・`test_t3_19_feature_flag_off_uses_legacy_before`・`test_t3_19b_feature_flag_on_uses_new_before`・`test_reviewer_h1_reset_dry_run_does_not_insert_course`・`test_reviewer_l2_reset_dry_run_logs_info`
- `tests/test_layer3_phase_g30.py`（8 本すべて）
  - 削除（S）: `test_legacy_path_pinned_visit_time_not_moved`・`test_legacy_path_pinned_visit_pair_not_moved`・`test_legacy_path_unpinned_visit_can_move`・`test_g21_path_still_works`・`test_build_visits_for_pool_propagates_pfv_is_pinned`・`test_legacy_path_weekly_pattern_with_pinned_pfv_uses_pfv_time`・`test_legacy_path_multi_entry_weekday_with_pinned_pfv_no_duplicate`
  - kwarg: `test_legacy_path_weekly_pattern_without_pfv_uses_weekly_pattern_time`（「重複しない」側を残す価値はある）
- `tests/test_layer3_phase_g31.py`（3 本）: `test_legacy_path_pinned_pfv_keeps_course_code`・`test_build_visits_for_pool_pinned_pfv_propagates_course_code_from_template`・`test_build_visits_for_pool_pinned_pfv_without_template_keeps_none` → 書換（S）。コースコードの伝わり方の検証は残し、PFV の前提を「pinned」から「fixed」に変える
- `tests/test_layer3_phase_g32.py`（3 本）: `test_pinned_visit_course_code_not_overwritten_by_clustering`・`test_pinned_visit_pair_keeps_course_codes_when_matching`・`test_pinned_visit_pair_keeps_pinned_course_when_one_unpinned` → 書換か削除（S）
- `tests/test_layer3_phase_g33.py`（6 本）: `test_cross_office_pinned_pfv_uses_template_office_id`・`test_same_office_pinned_pfv_keeps_patient_office`・`test_build_visits_for_pool_pinned_pfv_cross_office_diverts_office_id` → 書換（S）。`test_no_pfv_uses_patient_primary_office`・`test_build_visits_for_pool_fixed_source_cross_office_diverts_office_id`・`test_build_visits_for_pool_fixed_source_no_ct_office_keeps_primary` → kwarg
- `tests/test_pfv_validator.py`（21 本中 11 本。S: `_check_pinned` / CODE_PINNED を消すのと連動）
  - 削除: `test_pinned_preserved_is_ok`・`test_pinned_changed_is_allowed`・`test_pinned_deleted_is_allowed`・`test_pinned_flag_only_diff_is_allowed`・`test_pinned_row_keeps_its_movability_unknown`・`test_pinned_row_keeps_explicit_locked`・`test_pinned_row_keeps_time_flexible`・`test_non_pinned_row_keeps_its_movability`・`test_p4c_movability_diff_does_not_trigger_pinned_error`・`test_p4c_unpinned_row_movability_change_is_ok`
  - kwarg: `test_non_pinned_change_is_ok`。補助 `_item` / `_add_pfv` も kwarg
- `tests/test_improvement_engine.py`（22 本中 13 本。補助 `_seed_pfv`・`_two_course_improvement_scenario(target_movability)`・`_three_weekday_preference_scenario`）
  - 削除: `test_locked_pfv_excluded`・`test_pinned_pfv_excluded`
  - 書換（S。許可の根拠を weekly_pattern の範囲へ）: `test_unknown_same_weekday_time_change_requires_confirmation`・`test_day_flexible_allows_day_change`・`test_time_flexible_same_weekday_no_confirmation`・`test_within_preference_cross_weekday_unknown_no_confirmation`・`test_within_preference_day_change_dismissed_counted`
  - シナリオの movability を直す（S）: `test_improvement_staff_ng_destination_excluded`・`test_below_threshold_filtered`・`test_dismissed_fingerprint_excluded`・`test_threshold_constant_and_delta_sign`・`test_empty_weekly_pattern_falls_back_to_layer3`・`test_suggestions_carry_course_snapshots`
- `tests/test_scope_optimizer.py`（16 本中 14 本。補助 `_seed_pfv`・`_sandwich_scenario(target_movability, is_pinned)`）
  - 削除: `test_pinned_excluded`・`test_locked_excluded`
  - 書換（S）: `test_unknown_without_preference_excluded`・`test_unknown_within_preference_allowed_cross_weekday`
  - シナリオの修正（S）: `test_single_course_move_converges_and_metrics_match`・`test_scope_move_excludes_ng_staff_course`・`test_dismissed_fingerprint_respected`・`test_scope_never_moves_outside_selected_weekdays`・`test_simulate_is_deterministic`・`test_state_token_changes_when_pfv_changes`・`test_visits_without_pfv_counted_not_moved`・`test_apply_swap_updates_sim_state`・`test_same_address_same_time_pair_yields_no_phantom_step`・`test_focus_search_separation_moves_out_of_focus`
- `tests/test_swap_suggestions.py`（23 本中 16 本。補助 `_seed_pfv(movability="time_flexible" が既定)`・`_swap_scenario(x/y_movability, y_is_pinned)`・`_apply_pfv_pair`）
  - 削除: `test_swap_rejected_when_counterpart_pinned`・`test_swap_rejected_when_counterpart_locked`・`test_apply_swap_pinned_is_422_and_rolls_back`
  - 書換（S）: `test_swap_cross_weekday_requires_day_flexible_on_both`・`test_swap_cross_weekday_ok_when_both_day_flexible`・`test_swap_same_weekday_both_unknown_requires_confirmation`・`test_swap_within_preference_one_side_only_that_side_confirmed`・`test_swap_cross_weekday_within_preference_authorizes_unknown`・`test_swap_cross_weekday_unknown_x_rejected_without_preference`
  - シナリオの修正（S）: `test_swap_feasible_both_directions`・`test_swap_rejected_when_one_direction_infeasible`・`test_swap_skipped_when_x_already_has_pfv_on_target_weekday`・`test_apply_swap_moves_both_patients`・`test_apply_swap_same_weekday_success`・`test_apply_swap_omitted_b_course_retains_existing`・`test_apply_swap_explicit_b_course_overwrites`
- `tests/test_unblock_consult.py`（35 本中 16 本。補助 `_add_visit_and_pfv(movability="day_flexible" が既定)`・`_seed_single_blocker`・`_seed_retreat_shape`・`_seed_capacity_full_bucket`。**`locked` の filler を「動かない壁」として使っている**: :217, :393）
  - 削除: `test_pinned_blocker_not_moved`・`test_locked_blocker_not_moved`・`test_apply_pinned_crafted_422_rollback`・`test_w15_pinned_blocker_respected_under_capacity`
  - 書換（S）: `test_confirmation_required_blocker_not_moved`
  - シナリオの修正（S。壁を「希望範囲外」や「2 名体制」などに置き換える）: `test_depth1_unblock_forms_plan`・`test_same_address_pair_half_not_moved`・`test_depth2_two_moves_open_slot`・`test_two_staff_target_pair_unblock`・`test_ranking_by_total_delta`・`test_same_bucket_time_shift_opens_slot`・`test_same_bucket_retreat_reoccupy_target_slot_rejected`・`test_w13b_same_bucket_shift_one_course`・`test_w13b_two_staff_partner_course_included`・`test_w15_no_same_bucket_move_when_capacity`・`test_w15_same_bucket_shift_plan_frees_capacity_false`
- `tests/test_two_staff_pairing.py`: 補助 `_seed_scope_sandwich` の `movability="time_flexible"`（:675）→ シナリオの修正（S）。影響: `test_scope_single_staff_moves_baseline`・`test_scope_two_staff_protected_from_movable`
- `tests/test_auto_allocator_v2.py`
  - `test_backward_blame_pool_proposal_before_pinned_fixed`（V2Visit.is_pinned を使用）→ 書換か削除（S）
  - `test_g92_diff_add_g21_enabled_sets_fixed_pool_origin`（:8183 `is_pinned=True`）→ kwarg
- `tests/test_phase_g45_operating_weekdays.py`（補助 `_make_pfv(is_pinned=True が既定)`）: `test_run_v2_pipeline_skips_closed_weekday_pfv`・`test_run_v2_pipeline_cross_office_pfv_sub_office_overrides_closed_day`・`test_reset_visits_to_fixed_skips_closed_weekday` → kwarg（S で確認）
- `tests/test_reset_serving_office_and_ghost_cleanup.py`: `test_reset_cross_office_saturday_pfv_is_generated`・`test_reset_cross_office_pfv_skipped_when_serving_office_closed`・`test_single_office_reset_does_not_delete_cross_office_patient_visit` → kwarg（S で確認）

### 5-5. 今のテストの状態

上の赤ピン関係 32 ファイル（pin_api・patient_fixed_visits・suggestion_dismissals・pfv_validator・migration_0047/0050・g21_migration・change_scope_u0/u1/u2・g21_final_checks・movability_lock_fence・swap・unblock・scope_optimizer・scope_optimization_api・improvement_engine・g21_algorithm・layer3_g30-33・constraint_confirm×2・pool_bulk_apply・g45・reset_serving_office・reset_course_staff_sync・pfv_smart_master・two_staff_pairing・place_and_fix×2）を SQLite で実行した。**結果はすべて成功（skip 1 = `test_g21_migration.py::test_t1_8_pg_enum_rejects_invalid_pair_mode`。PG_TEST_URL 未設定のため）**。今の時点で失敗しているテストは無い。

---

## 6) 無関係と判定した箇所

- **青ピン（範囲外）**: `visits.week_pinned`。対象は `schedule_v2.py` の week-pin の 2 API（2424 / 2502）、青の 422 の蓋（1526-1545・1711・1966-1980・2316）、`visits.py:433, 976-986, 1154`、`copy_week` の `keep_pinned`、`alembic 0066`。テストでは `test_visit_week_pin.py`（18 本）・`test_week_pin_reset_protection.py`・`test_visits.py::test_visits_list_exposes_week_pinned`・`test_copy_week.py::test_replace_keeps_import_pinned_cancelled`・`test_course_move_weekday.py::test_move_with_week_pinned_visit_returns_422`・`test_staff_off_week.py::test_week_pinned_returns_422`・`test_visit_cancel_week.py`（2 本）・`test_visit_service_override.py::test_no_422_for_pinned_or_finished_visits`・`test_layer1.py::test_reset_v2_week_pinned_survives`・`test_patient_status_sync.py`（4 本。`week_pinned=True` を使用）
- **ロック類**: `users.locked_until`（auth.py / admin.py / scripts/create_admin.py）、advisory lock の `locked` 応答（admin_checkin・admin_geocoding・admin_visit_recordings・admin_visit_signatures とそのテスト = test_checkin_notify・test_checkin_purge・test_visit_signatures・test_admin_geocoding_audit）、`asyncio.Lock.locked()`（copy_week.py:278、schedule.py:1734/1859）、`pending_requests.py` の行ロック変数 `locked`
- **blocked 系**: same-address の `pair_mode='blocked'`、`acceptance_blocked`、`SnapshotRestoreBlockedError`、`ReplaceBlockedError`、イベントの `blocking`
- **place-and-fix の他のヒット**: `test_place_and_fix.py` と `test_place_and_fix_multi_staff.py` は、赤ピンの語を一度も使っていない（変更不要）。`schedule_op_log`・`special_visits.py:226`・`schemas/special_visit.py:137` も place-and-fix への言及だけ。
- **backend/scripts**: 赤ピン関係のヒット無し（`cleanup_w16_course_code_mismatch.py` は place-and-fix の言及だけ）。
- **0037 のうち same_address_links / office_feature_flags**: 別機能で現役。

---

## 7) 判断が必要な論点

1. **リリース順と `extra="forbid"`**: `PatientFixedVisitV2Base` と `ImprovementDismissRequest` は余分なキーを拒否する。どちらで行くか決める必要がある。
   - (a) FE を先に出して、`is_pinned` / `movability` / `promote_movability` を送るのをやめる
   - (b) BE は 1 リリースの間、これらを「受け取って捨てる」項目として残す
   - 推奨は (b)。本番は FE と BE を同時に入れ替えるが、PWA の古い chunk が残る問題（careflow-pwa-stale-chunk）があるため。
2. **movability をまるごと消すか、'locked' だけ消すか**: 仕様（pin-and-movability-spec.md §1.1）では `time_flexible` / `day_flexible` の本番実績は 0 件。一方、提案エンジン（improvement / swap / scope / unblock）は今も「unknown = 要確認 / flexible = 許可」の軸で動いており、テストの大半もその軸でシナリオを組んでいる。PO 決定（動かせる範囲 = 希望訪問スケジュール）に合わせて**列ごと消し、許可の根拠を `within_preference` に一本化する**のが筋。ただし services 担当の設計と合わせる必要がある。
3. **見送り時の昇格（dismiss の `promote_movability`）**: 昇格先が無くなるので削除する。却下の理由 `day_immovable` / `time_immovable` を「却下ラベル」として残すか、CHECK を変える migration で別の値に寄せるか。残すなら migration は不要。
4. **reset-to-fixed の `mode: legacy|auto`**: 説明が「pinned PFV のみ厳守」なので、赤ピンが無くなると auto の意味が変わる（全 PFV を厳守 = legacy と同じになる？）。mode を残すか、意味を決め直すかを services 担当と決める。
5. **完全固定が付いている本番の行**: drop する前に `movability='locked'`（と `is_pinned`）の件数と患者を出し、PO に「この患者は今後、希望訪問スケジュールの範囲で動きうる」と伝えるか。希望範囲が狭くなっていないかの確認も含む。
6. **使われていない validator の 422**: PUT・apply-swap・unblock/apply の `has_errors` 分岐は、V2 を撤廃した結果いま発火しない。`_check_pinned` / CODE_PINNED を消すなら、これらの分岐（と FE の errorMessage テスト用データ）もまとめて消すか。validator の error の仕組みそのものは、今後の別用途のために残すか。
7. **エンジンを守る 422 を消してよいか**: apply-swap・scope/unblock の apply は「エンジン経路は完全固定を動かさない」ための防御だった。撤去後は「希望範囲の外へは動かさない」ガードを apply 側にも置き直すか（今は手作りのペイロードを弾く役目）。services 側の判定と同じ関数で apply の時点にも再検証を入れる設計が望ましい。
8. **migration の段取り**: 1 段（コード変更と drop を同時）か、2 段（コードを先に出し、次のリリースで drop）か。推奨は 2 段。本番は alembic 0090・VPS は UTC。デプロイ手順の厳守ルール（careflow-deploy）に従う。
