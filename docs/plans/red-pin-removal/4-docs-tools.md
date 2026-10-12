# 赤ピン（完全固定）廃止 — backend/app・frontend 本体以外の棚卸し（読むだけ）

調査日 2026-10-12。ファイルは何も変えていない。本番にもつないでいない。

---

## 1) 要約

- **道具（docs/tools/**）は赤を読みも書きもしない。** `is_pinned`・`movability` の列を消しても、ならしの道具・kaipoke-ops・visit-history・voice-test は壊れない。`extract.py` が読むのは青ピンの `visits.week_pinned` だけで、しかも後段では使っていない。
- **ならしの道具はもう「希望の時間帯で動かす範囲を決める」作りになっている。** `solver.py` の `pref_window()` は `time_type="固定"` なら幅ゼロ（今の時刻のまま）、`時間帯/午前/午後/終日` ならその範囲。PO は 10/3 に判断 D-1 で「locked でも希望の時間帯の中なら動かしてよい（固定の 31 名は動かさない）」と決めている。つまり今回の方針は、実際の運用ではもう始まっている。
- **CI・e2e・seed で赤を設定しているものは無い。** 影響は CI の `alembic upgrade head → downgrade base → upgrade head` だけ。列を消す migration には、元に戻せる downgrade を書く必要がある。backend の migration テスト（`test_migration_0047/0050`）は backend 側の範囲。
- **改訂が必要な文書は 7 つ**（仕様の正典・マニュアル 2 つ・道具の README・スキル・最新の引き継ぎ書・管理者マニュアルへの追記の検討）。そのほかの設計書・引き継ぎ書は履歴として残す。
- **希望訪問スケジュールで幅ゼロは表せる**（`time_type="固定"` ＋ `preferred_start`。ほかに `時間帯` で start と end を同じにする書き方もある）。ただし注意点が 3 つある。①要約形式は時刻が 1 つで全曜日に共通。②曜日ごとの `entries` を道具は読まない。③希望が無い患者の扱いが、道具（終日＝自由）とアプリ（要確認＝動かさない）で逆になっている。
- **「136/143 が locked なのは migration の名残」は、コードからは裏付けられない。** migration の名残は 0065 で一度すべて `unknown` に戻した。0067 の時点で残っていた赤は「本番 1 件」とされている。そのあと 10/2 に 161 枠中 114、10/3 に川名さんが 138 枠を作り直した後で 143 枠中 136 と、作り直した後に赤の割合が増えた。新しい行の既定値は `unknown` なので、**人が「完全固定」のチェックや「全曜日を完全固定」を使った可能性が高い**。確定させるには本番を読む SQL が要る（§7）。

---

## 2) 改訂が必要な文書（今も使っている仕様・マニュアル・道具の説明）

| 文書 | 該当箇所 | 直すこと |
|---|---|---|
| `docs/plans/pin-and-movability-spec.md`（仕様の正典） | §1（1.1〜1.4 完全固定の実体・守る/守らない・設定経路・盤面の規約）、§2（希望訪問スケジュールとの関係：「該当曜日のエントリが無い場合のみ可動域ルールへフォールバック」）、§6 の残課題 | §1 を「廃止」に書き換える。§2 を「動かせる範囲は希望訪問スケジュールだけで決める。幅ゼロは `time_type=固定`。エントリが無い場合の扱い」に書き換えて主役にする。§3（型とのズレの表示）と §5（青ピン）は残す。タイトルと冒頭の表も直す |
| `docs/manual/週次スケジュール準備ガイド.html`（git 管理・最終更新 2026-07-04） | 第 5 章「ピン留め・手動確定の保護」、「ピン留め（完全固定）していない訪問は、再生成で上書きされます」「🔒 ピン留め（完全固定）を設定してください」など 10 か所 | 赤ピンの章を外し、守る手段を「青ピン（今週）／希望訪問スケジュールの幅ゼロ（毎週）」に置き換える。なお 7 月の版のため、ほかの記述も今と合っていない可能性がある |
| `docs/manual/スケジュール運用マニュアル（新規・既存）.html`（git 管理） | 「🔒 完全固定 動かせないよう固定（この曜日のみ／…）」「動かしたくない予定を守る → 🔒 完全固定」の表、カードの 🔒 の説明など 5 か所 | 同上。🔒 マークの説明も消す |
| `docs/tools/week-leveling/README.md` L45 | 「分かっている限界：可動域 `locked`（完全固定）も見ない … PO 判断待ち（D-1）」 | 限界ではなくなる。「動かせる範囲は希望訪問スケジュールで決める（固定＝幅ゼロ）」と書き直す。あわせて §4 の「entries を読まない」「希望なし＝終日」も明記する |
| `.claude/skills/week-leveling/SKILL.md` L15（リポジトリで git 管理） | 「道具は可動域 `locked` を見ない（同 §2-4・判断 D-1）」 | この 1 文を削るか、「希望訪問スケジュールの範囲で動かす（固定は動かさない）」に置き換える |
| `docs/plans/session-2026-10-12-HANDOFF.md`（未追跡・最新の引き継ぎ） | 論点 P-4「赤ピンの位置づけ」、§4-3 の R-1〜R-9（赤の不整合）、「赤の最小修正」の手順、赤のテスト一覧 | P-4 に決定（廃止）を書く。R-1〜R-5 の「直す」案は、廃止すれば不要になる旨を注記する。次の作業を「列を消す migration・エンジンのフェンス撤去」に差し替える |
| `docs/manuals/admin-quick-manual.html`（未追跡） | 赤／完全固定の記述は **0 件**。L349 に「固定訪問パターン」カードの説明あり | 直す必要は無い。ただし廃止後の守り方（希望訪問スケジュールを「固定」にすれば動かない）を 1 行足すかどうかは要検討 |

※ `docs/HANDOFF.md`（未追跡）と `docs/RELEASE-NOTES-v2.md` は赤に関する記述が 0 件（`🔒` は別の意味）。

---

## 3) 履歴として残す文書（直さない。必要なら冒頭に「赤は 2026-10 に廃止」の 1 行だけ）

**設計書（当時の決定の記録）**
- `docs/plans/p2-improvement-mvp-design.md`（25 か所。可動域 4 値の誕生。migration 0047 の根拠）
- `docs/plans/schedule-advisor-design.md`（7 か所）／`schedule-advisor-HANDOFF.md`（11 か所）
- `docs/plans/scope-optimization-design.md`（6 か所。D-1「pinned / locked は動かさない」）
- `docs/plans/unblock-consult-design.md`（3 か所）／`two-staff-pairing-design.md`（「ピン/locked 不可侵」）
- `docs/plans/p0-2-apply-safety-net-design.md`（4 か所）
- `docs/plans/change-scope-unification-design.md`／`change-scope-unification-HANDOFF.md`（from-week-bulk でピンが消える問題の修正）
- `docs/plans/weekly-space-design.md`（「apply-individual 赤ピン 422 ガードあり」）
- `docs/plans/base-visit-minutes-design.md`・`add-visit-anywhere-design.md`・`patient-end-cleanup-design-2026-10-09.md`（言及が 1 か所ずつ。patient-end は監査ログに「可動域」を残すという記述で、列を消したら `before.rows` に出なくなるだけ）
- `docs/plans/schedule-timeline-redesign-design.md`・`trainee-accompaniment-design.md`（「ピン留め」の UI 配置）
- 調査報告：`proposal-source-investigation-2026-08-31.md`（11 か所）・`pool-placement-blockers-investigation-2026-08-31.md`・`accompaniment-overlap-investigation-2026-08-31.md`・`week-cockpit-investigation.md`

**引き継ぎ書（年表）**
- `session-2026-07-08-HANDOFF.md`・`07-08b`・`07-26`・`07-29`・`08-10`（ピンモデルの統合）・`08-25`・`09-07`・`10-02`（locked 114/161）・`10-06`（D-1 の決定）

**手順書**
- `docs/plans/week42-final-leveling-runbook-2026-10-03.md`（12 か所。§2-4-1、手順 2 の locked を数える SQL、§5 D-1 とその決定 L254、§7 の実施記録「locked 136」）。週42 の実施記録の正典なので残す。ただし手順 2 の SQL は `movability` を読むので、**列を消した後に流用すると失敗する**。次の週に流用するときは直すこと。
- `docs/runbook/g21_canary_deploy.md`（`is_pinned` の巻き戻し手順。G-21 のカナリア公開用で、役目は終わっている）

**モックアップ・レビュー**
- `docs/mockups/timeline-mock.html`・`timeline-comparison-mock.html`・`staff-schedule-week-cockpit-mock.html`（🔒／ピン）
- `docs/mockups/event-*-mock.html`・`docs/design/04-screen-login.md`・`docs/manual/初回ログイン…html` の 🔒 は**別の意味**（予定のブロック・パスワード）なので対象外
- `docs/manual/旧/スケジュール変更／既存患者様編.html`（「旧」フォルダ）
- `docs/review/wave1/W1-F-critic.md`

**docs/reports（git 管理外。中身は列挙しない）**
- `is_pinned`／`movability` を読む道具は **0 件**。
- `week_pinned`（青）を含むのは `2026-10-04-week42-plan-v4/w42-restore-20261004-2332.sql`（週42 の戻し用。もう使わない）と `2026-10-07-w42-in-v4/make_input.py` だけ。赤には関係しない。

---

## 4) 道具・スクリプト

| 道具 | 赤（is_pinned/movability）の読み書き | 列を消したとき |
|---|---|---|
| `docs/tools/week-leveling/extract.py` | 読まない。`v.week_pinned`（青）を SELECT するだけ（L25）。PFV は `duration_min` だけ読む（L40） | 壊れない（青を消すなら L25 を直す） |
| `solver.py`／`level.py`／`report.py`／`diagnose.py`／`app_yardstick.py` | 読まない。動かせる範囲は `weekly_pattern` の `time_type`・`preferred_start/end` だけで決める（`solver.py` L131-154 `pref_window`／`join_fixed`、L234-238） | 壊れない |
| `apply/`（w42 用と generic） | 読まない・書かない（`week_pinned` も無い） | 壊れない |
| `docs/tools/kaipoke-ops/*`（`.py`・`.sql`） | 該当なし | 壊れない |
| `docs/tools/visit-history/*`・`voice-test/*` | 該当なし | 壊れない |
| `backend/scripts/*`（52 本） | 該当なし（`import_weekly_pattern.py` などは weekly_pattern だけ） | 壊れない |

**道具が希望の時間帯をどう使うか（`solver.py`）**
- `time_type` が無い、または `weekly_pattern` が null のときは **「終日」**（9:00〜営業終了−所要）として扱い、**自由に動かす**。
- `固定` → `None` を返し、その訪問の今の時刻だけを使う（幅ゼロ）。基準は `preferred_start` ではなく**訪問の開始時刻**。
- `時間帯` → `[preferred_start, preferred_end]`。`午前` → 9:00〜11:55。`午後` → 13:00〜。
- 今の時刻が範囲の外なら、30 分以内のときだけ範囲をつなぐ（`join_fixed`）。
- **読むのは要約形式（最上位のキー）だけ**。曜日ごとの `entries` は読まない。
- 曜日は動かさない（1 日の中で直すだけ）。

---

## 5) CI・e2e・seed

- `.github/workflows/*.yml`（backend-ci・ci・deploy・frontend-ci・security）：赤を直接扱う所は無い。ただし `backend-ci.yml` L196-202 が **`alembic upgrade head → downgrade base → upgrade head`** を流す。列を消す migration の downgrade で列（と CHECK `ck_pfv_movability`・部分 index `ix_pfv_pinned_patient`）を作り直せないと、CI が落ちる。`ci.yml` L212- の「head が 1 つだけ」の確認も通す必要がある。`deploy.yml` も `alembic upgrade head` を流す。
- `frontend/e2e/*.spec.ts`（8 本。`v2-patient-fixed-visits.spec.ts` を含む）と `frontend/e2e/fixtures`：`is_pinned`・`movability`・`完全固定` は **0 件**。
- seed／fixture：`backend/scripts`・SQL・JSON に **0 件**（見つかったのは `.venv` の外部ライブラリと `.omc` の状態ファイルだけで、関係ない）。
- 参考（範囲外の backend テスト）：`test_g21_pfv_pin_api.py`・`test_movability_lock_fence.py`・`test_migration_0047.py`・`test_migration_0050.py`・`test_suggestion_dismissals.py` ほか。FE は `PinScopeMenu.test.tsx`・`movabilityMark.test.ts`（10-12 の引き継ぎ書 §153 による）。

---

## 6) 希望訪問スケジュール（`patients.weekly_pattern`）の構造と、幅ゼロを表せるか

**保存先**：`backend/app/models/patient.py` L107 `weekly_pattern: JSONBish（null 可）`。スキーマは `backend/app/schemas/v2/patient.py` L74-140。

```
WeeklyPatternV2 (extra=allow)
  frequency_per_week, visit_frequency, visit_weeks,
  preferred_weekdays: ["Mon",...],
  service_minutes, time_type, preferred_start, preferred_end,   ← 要約形式（全曜日で共通）
  entries: [ WeeklyPatternEntryV2 {weekday, time_type, preferred_start, preferred_end,
                                    service_minutes, staff_count} ]  ← 曜日ごとの形式
TimeTypeV2 = "固定" | "時間帯" | "午前" | "午後" | "終日"
```

**エンジンの読み方**（正典は `auto_allocator_v2._extract_weekly_entries` L1756-1840。判定は `proposal_solver._time_type_allows` L350-395）
- `entries` が空でなければ、**`entries` だけを使い、要約形式は無視する**（曜日ごとの値が無いキーだけ要約形式で補う）。
- `entries` が無ければ、`preferred_weekdays` を曜日ごとに展開する（**時刻と time_type は全曜日で共通**）。
- `固定`：`start == preferred_start` のときだけ範囲内（**幅ゼロ**）。`preferred_start` が無いときは常に範囲外。
- `時間帯`：`preferred_start ≤ start ≤ preferred_end`（両端を含むので、start と end を同じにすれば幅ゼロになる）。
- `午前`：終了が 12:00 以前。`午後`：開始が 13:00 以降。`終日`／None：その曜日ならどこでも。
- `improvement_engine.within_preference`（L210-258）が「層 2」。範囲内なら赤でなければ提案でき、患者への確認も不要。**該当する曜日のエントリが無い（weekly_pattern が null を含む）ときは False** になり、層 3（movability。実質 `unknown` = 要確認）へ回る。
- 曜日をまたぐ移動：希望に含まれる曜日（`preferred_weekdays`／`entries`）の中なら「範囲内」と判定される。幅ゼロにするには、**時刻だけでなく曜日も、実際に訪問する曜日だけに絞る**必要がある。
- そのほか：古い v1 の `allocate.py` L132 と `layer1_expander.py` L1390 は、`time_type` が無いとき **「固定」とみなす**。新しいエンジン（`_time_type_allows`）は None を「終日」とみなす。**既定値が逆**になっている。

**結論：幅ゼロは表せる**（`time_type="固定"` ＋ `preferred_start` ＋ その曜日だけ）。ただし次の注意点がある。
1. **本番の希望は「利用者ごとに 1 つ・曜日ごとの設定なし」**（runbook §7 10/3）。曜日ごとに時刻が違う人（例：月 10:00／木 14:00）を幅ゼロにするには `entries` が要る。`entries` を書けるのは `POST /schedule/v2/update-fixed-time-master`（`schedule_v2.py` L2813-2870）だけで、Excel の取込が扱うのは要約形式の 8 キーだけ（`entries` は残すが書かない。`importer.py` L2027-2043）。
2. **ならしの道具は `entries` を読まない**。`entries` を使い始めると、道具とアプリで範囲が食い違う。
3. **「固定」の基準が違う**：アプリは `preferred_start` と一致するかで判定し、道具は今の訪問の時刻を使う。固定訪問の枠（PFV）の時刻と `preferred_start` がずれていると、アプリでは毎週「希望外」になり要確認へ回る（PFV と希望の二重持ち。base-visit-minutes-design を参照）。
4. **希望が無い患者の扱いが逆**：道具は終日（自由）、アプリは層 3（unknown = 要確認＝提案しない）。赤を消して層 3 を何にするかで、アプリの挙動が決まる。
5. 規模：10/3 時点で、PO の D-1 の記録では「時間帯 34 名（幅の中央 167 分・4 時間以上 14 名）／固定 31 名」。稼働中は 81 名なので、残りの約 16 名は午前・午後・終日・未設定のどれか（内訳は未確認。本番を読めば分かる）。

---

## 7) 赤が大量にある理由（migration の根拠）

| migration | 日付 | 中身 |
|---|---|---|
| `0037_g21_pfv_is_pinned_and_same_address_links` | 2026-05-22 | `is_pinned` を **`server_default=true`** の NOT NULL で追加 → **当時あった全 PFV（本番想定 134 行）が true** になる。その直後に既定値を false に切り替え。目的は全行 UPDATE のロック時間を避けることで、運用上の意図は無い |
| `0047_movability_and_suggestion_dismissals` | 2026-07-03 | `movability` を `server_default='unknown'` で追加し、**`is_pinned=true` の行をすべて `locked` に backfill** |
| `0050_release_pin_movability_locked` | 2026-07-03 | `is_pinned=false` なのに `locked` の行（ピン解除の残骸）を `unknown` に戻す |
| `0065_reset_machine_set_movability_locked` | 2026-08-08 | **`is_pinned=true AND locked` の行を `unknown` に戻す**。理由として「機械が付けた値で、現場の判断ではない」と明記している（`is_pinned` 自体は残す） |
| `0067_pin_mirrors_locked` | 2026-08-09 | `is_pinned=true` の行を `locked` に上げ、`is_pinned := (movability='locked')` にそろえる。docstring には「残っていた赤は**本番 1 件**」とある（0065 と 0067 の間に一括ピン解除があったと読める） |

**判定**：migration が付けた赤（0037→0047）は、**0065 でいったんすべて消えている**。0067 時点の赤は 1 件。にもかかわらず、10/2 は 161 枠中 114、10/3 は川名さんが 138 枠を作り直した後で 143 枠中 136 になっている。
- 新しい行の既定値は `unknown`（FE `PatientFixedVisitsPanel.tsx` L240、BE `_add_one_pfv` L366-373、DB の server_default）。
- 赤を書く経路は人の操作だけ：患者画面の「完全固定」チェック（L830）、「全曜日を完全固定」（L1044-1062）、盤面の赤トグル（`PATCH .../pin`。`audit_logs` に `pfv_pin_toggle` が残る）、見送りの昇格「この時刻は動かせない」→ `locked`（`schedule_v2.py` L4845-4851。`is_pinned` は立たない）。
- 作り直しで既存の値を引き継ぐ経路：FE の「希望から自動生成」（L448）、from-week（L360）、place-and-fix。
- → **「136/143 は migration の名残」はコードと migration の記録からは支持できない**。runbook §2-4 の「locked の多くは、昔の移行で一律に入った値」は 0065 と矛盾する。最も考えやすいのは、**現場の人が「固定訪問＝完全固定」と受け取ってチェックを付けた**こと。あるいは 0067 の「1 件」の記録が実態と違う。
- **確かめ方（本番を読むだけ。今回は実行していない）**：
  ```sql
  begin transaction read only;
  select movability, is_pinned, date(created_at) d, date(updated_at) u, count(*)
    from patient_fixed_visits where mode='normal' group by 1,2,3,4 order by 3,4;
  select date(created_at), count(*) from audit_logs where action='pfv_pin_toggle' group by 1 order by 1;
  select count(*) from suggestion_dismissals where reason='time_immovable';
  commit;
  ```
  `created_at` が 0037（2026-05-22）より前の行が多く locked なら、名残説が当たっている。10/3 に作ったり更新したりした行が locked なら、人が付けたもの。

---

## 8) 判断が必要な論点

1. **希望が無い患者（または該当曜日のエントリが無い患者）を、廃止後にどう扱うか。** (a) 動かさない（今のアプリ。安全側）／(b) 終日として自由に動かす（今の道具）。どちらかに統一しないと、道具とアプリで答えが割れる。
2. **曜日ごとに時刻が違う人の幅ゼロ**：`entries` を正式に使うか（道具の対応と、Excel・患者画面で `entries` を編集できるようにすることが必要）、要約形式のままにするか。
3. **「固定」の基準は `preferred_start` と PFV の時刻のどちらか。** ずれている人をどう扱うか（廃止の前に、ずれている件数を本番で数えるべき）。
4. **「136 枠の赤」をどう移すか**：人が付けた判断なら、廃止の前にそれぞれの患者の希望を「固定（幅ゼロ）」に移すべきかもしれない（少なくとも、今 locked で希望が `時間帯/終日` の約 63 枠＝10/2 時点は、PO が D-1 で「動かしてよい」と決めた分なので移す必要は無い）。移す・移さないの一覧を作るかどうか。
5. **見送りの記憶（`suggestion_dismissals` の `time_immovable`・`day_immovable`）**：今は昇格で movability を書き換えている。廃止後は希望を「固定」に書き換えるのか、見送りの記憶だけにするのか。
6. **列を消す時期**：まずエンジンのフェンスを外して読むのをやめ、列は残す（ミラーを止めるだけ）。そのあと別の migration で DROP する二段構えにするか。CI の downgrade の往復を通す必要がある。
7. **マニュアル 2 本（`docs/manual/` の 7 月版）**：赤の章を直すだけにするか、7 月版をまとめて「旧」へ移し、今の `docs/manuals/admin-quick-manual.html` を正にするか。
