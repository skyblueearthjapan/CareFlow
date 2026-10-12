# 青ピン（週のピン・`visits.week_pinned`）コード調査 B1 — 2026-10-12

- 種別: 読むだけの調査（コード・文書の変更なし／本番 DB・サーバーには触れていない）
- 対象: ブランチ `feature/agent-skills` の**作業中のツリー**（他の担当の未コミットの変更を含む。行番号はこの時点のもの）
- 出発点: `docs/plans/session-2026-10-12-HANDOFF.md` §3-2・§4-1・§4-2・§5-2／`docs/plans/pin-and-movability-spec.md` §5／`docs/plans/red-pin-removal/3-frontend.md` §5／`docs/plans/red-pin-simplify-design.md`
- 書き方: **【事実】= ファイル:行で確かめたこと**、**【推測】= コードから考えたが動かして確かめていないこと**。

---

## 0. 要約（3 行）

1. 【事実】「消さない・作り直さない」守りは、ほぼ **出所（`source`）だけで決まっている**。`manual_week`・`import`・`manual`・`manual_cancel` は、青ピンが無くても週を生成・固定枠戻し・全面最適化の反映で消えない。青ピン（フラグ）が**それ以上に**守るのは「`source='auto'`/`reset_v2` 等の訪問」だけ。
2. 【事実】青ピンが止めるのは、人の手の 1 件操作（今週だけ移動・削除・取消・PATCH の日時・職員休み・コースの曜日移動・コックピットのメニュー）と、週のコピーの置き換え。**止めないもの**: カイポケ取込（置換・差分）、改善提案・範囲最適化・詰まり解消の「今週だけ」反映、担当変更（1 件・コース担当 PATCH・一斉未割当・layer3）。
3. 【事実】画面の青い画鋲は `week_pinned || source='manual_week'`、蓋（ドラッグ不可・422）は `week_pinned` だけ。一括固定は `manual_week` を対象外にし、解除は `manual_week→auto` に変える。本番の写しでは `week_pinned=true` が 0 件（HANDOFF §4-2 B-1・本番 DB では未確認）。→ **今の青ピンは、実質「`manual_week` の別名の表示」＋「誰も刺していない蓋」**。

---

## 1. 読む／書く箇所の一覧（事実）

### 1-1. データと API の土台

| 場所 | 中身 |
|---|---|
| `backend/app/models/visit.py:148` | `week_pinned: bool NOT NULL default False`（migration 0066） |
| `backend/app/models/visit.py:59-80` | `VISIT_SOURCE_MANUAL_WEEK`／`VISIT_SOURCE_MANUAL_CANCEL` の保護規則の説明（週生成・固定枠戻しの両方で source により保護） |
| `backend/app/schemas/visit.py:47` | `VisitBase.week_pinned` → **POST /visits でフラグを立てられる** |
| `backend/app/schemas/visit.py:76-98` | `VisitUpdate` に `week_pinned` が**無い**（`extra="ignore"`）。一方で `source`・`status` は PATCH で自由に書ける |
| `backend/app/api/v1/visits.py:436` | 一覧・詳細の手書き dict に `week_pinned` を出す |
| `backend/app/api/v1/schedule_v2.py:2415-2421` | `_visit_is_week_pinned` = `week_pinned or source=='manual_week'`（和集合） |
| `backend/app/api/v1/schedule_v2.py:2424-2499` | 1 件の付け外し `PATCH /v2/visits/{id}/week-pin`（admin）。解除時 `manual_week→auto`（`:2473-2477`）。planned 以外 422 |
| `backend/app/api/v1/schedule_v2.py:2502-2582` | 一括 `POST /v2/visits/week-pin/bulk`。対象 = ISO 週 × planned。**拠点の絞り込み無し・過去日も含む**（`:2537-2542`）。固定は `week_pinned=False かつ source!='manual_week'` だけ（`:2545-2550`）、解除は `week_pinned or manual_week`（`:2552-2555`）で `manual_week→auto`（`:2566-2567`） |
| `backend/app/api/v1/agent.py:142` | エージェント向けの週の文脈に `week_pinned` を出す（読むだけ） |
| `backend/app/services/kaipoke/inbound_snapshot.py:53, 310` | 取込前スナップショットに `week_pinned` を保存・復元 |

### 1-2. 自動処理（エンジン・週の作り直し）

| 処理 | 青ピンの扱い | `manual_week`/`import` の扱い |
|---|---|---|
| 週を生成 `POST /generate-week-only`（`api/v1/schedule.py:1704` → `layer1_expander`） | 削除しない `layer1_expander.py:1329`／その (患者・日) の再生成を止める `:935` | 削除対象は `source∈(auto, reset_v2)` だけ（`:1325-1326`）→ 残る。日の再生成も止める `:933-934` |
| 固定枠戻し `reset-to-fixed`（`schedule_v2.py:1240`）・`sync-fixed-to-week`（`:1362`）→ `auto_allocator_v2.reset_visits_to_fixed`（`:9742`） | 削除しない `:9851`／日の再生成を止める `:9905-9912, 10302` | 削除は白リスト `_RESET_DELETABLE_SOURCES`（`:8717-8725`、auto 系のみ）→ 残る。日の再生成も止める `:9911` |
| 全面最適化の反映 `apply-week-only` → `apply_week_only`（`:8998`） | 削除しない `:9279` | 白リストで残る `:9277`。**日の再生成止めは無く、同じ（患者・日・開始時刻）の衝突だけ避ける**（`:9336-9355, 9650`） |
| コース担当の写し `course_staff_mirror.py:105`（`reset_visits_to_fixed :10181` から呼ぶ・プール一括も） | 書き換えない | 関係なし |
| 非稼働患者の掃除（週生成・固定枠戻しの削除に非稼働患者を足す） | **青ピンは消えない**（同じ削除条件） | 同上 |
| 患者ステータス連動の取消 `patient_status_sync.py:331-339` | **取消の対象外**（`excluded.week_pinned`） | 対象 |
| 改善提案・範囲最適化・詰まり解消・プール一括の**候補作り**（`improvement_engine.py`・`scope_optimizer.py`・`unblock_search.py`・`pool_bulk_inserter.py`） | **参照なし**（青ピンの訪問も動かす候補になる） | 参照なし |
| layer3（`layer3_assignment.py`） | 参照なし | 参照なし |

### 1-3. 人・道具の操作（API）

| 操作 | 青ピンで止まるか | 根拠 |
|---|---|---|
| 今週だけ移動 `visit-move-week-only` | **422** | `schedule_v2.py:1522-1545` |
| 改善提案の入れ替え（今週だけ） | 止まらない（そのまま `_apply_visit_move_week_only`） | `:5190-5215`。`:5505-5615` 内に確認なし |
| 範囲最適化の反映（今週だけ）・詰まり解消の反映 | 止まらない（`_validate_and_move_one` → 同上） | `:5615, 5694-5700, 5865/5913/5930, 6534` |
| 1 件の担当変更 `visit-assign-staff-week` | **通す**（説明文は「青ピン 422」） | 説明 `:1690`／実装 `:1711-1712` |
| 職員の休み `staff-off-week` | **422** | `:1966-1980` |
| コースごと曜日移動 `course-move-weekday-week-only` | **422** | `:2312-2316` |
| 今週だけ取消・取消を戻す `visit-cancel-week` | **422**（両方向） | `op_log_service.py:102-105` |
| サービス内容の上書き `visit-service-override` | 通す（意図どおり） | `:6880-6886` |
| `PATCH /visits/{id}` 日付・時刻 | **422** | `visits.py:974-987` |
| `PATCH /visits/{id}` 担当・コース・source・status | 通す | 同上（配置 3 項目だけ） |
| `PATCH /visits` の「`week_pinned:false` 同送なら通す」分岐 | **動かない**（`VisitUpdate` に項目が無い） | `visits.py:981`・`schemas/visit.py:76-98` |
| `DELETE /visits/{id}`（保留プールへ戻す） | **422** | `visits.py:1149-1155` |
| コース担当の変更 `PATCH /courses` | 通す（青ピンの訪問の担当も書き換える） | `courses.py:411-420`（絞りは manual_staff_override と planned だけ） |
| 一斉スタッフ未割当 `unassign-all-staff` | 通す | `schedule_v2.py:3158-3205`・`auto_allocator_v2.py:10732` |
| 週のコピー `copy-week` | 青ピンは残し、その（患者・日）への写しを止める | `copy_week.py:70, 239-240` |
| カイポケ置換（`replace-inbound`・smart-inbound の打刻なし日） | **守らない**（その日の全訪問を白紙化） | `replace_inbound.py:218-232, 355-358` |
| カイポケ差分（smart-inbound の打刻あり日） | **守らない**（取消 `inbound.py:1477-1484`・時刻/日付変更 `:1880-1896` に確認なし） | 同左 |
| 取り込み前に戻す | フラグも戻る | `inbound_snapshot.py:310` |
| カイポケへ送る（CSV/差分） | 参照なし（青ピンは送る内容に影響しない） | `services/kaipoke/csv_builder.py`・`local_diff.py` に `week_pinned` の参照なし |

### 1-4. 画面

| 場所 | 中身 |
|---|---|
| `TimelineDayBoard.tsx:526-528` | 画鋲 = `week_pinned || source==='manual_week'` |
| `TimelineDayBoard.tsx:236, 551, 571, 798, 842, 1250, 1295` | ドラッグ不可・× 無効 = `week_pinned === true` だけ（→ 表示と蓋の不一致 B-1） |
| `TimelineDayList.tsx:200` 表示は和集合／`:212-225` 蓋は `week_pinned` だけ | 同上 |
| `WeekTimelineBoard.tsx:235`・`CourseWeekOverview.tsx:661`・`courseGrid.ts:228` | 表示は和集合 |
| `CourseDayTablePanel.tsx:3158` | 削除の前に `week_pinned` で止める（画面側の二重防御） |
| `CourseDayTablePanel.tsx:4797` | 帯へのコース担当の割り当てを `week_pinned` で止める（**画面だけ**。API `PATCH /courses` は止めない） |
| `BulkWeekPinAllButton.tsx:82`（`CourseDayTablePanel.tsx:155` から使用） | 一括。0 件なら「既に今週は全件固定済みです」 |
| `VisitActionMenu.tsx:231, 285`（コックピット） | 青ピンなら全メニュー無効・**外す手段なし**（B-7） |
| `StaffTimelineView.tsx:1545-1584` | 動かせない理由に「青ピン」。画鋲の絵は無い |
| `SubstitutePanel.tsx:114, 185` | 急休代替で青ピンがあれば実行させない |
| `AddVisitAnywhereDialog.tsx:561, 668` | 「動かす元」から青ピンを外す |
| `MakeWeekDialog.tsx:445` | 週のコピー確認文「青ピン…は残し」。実際は**フラグだけ**残し、画面で青く見える `manual_week` は置き換える（B-6） |
| `lib/queries/visit_week_pin.ts:11` | 説明が古い（「実体は source='manual_week'」） |
| スマホ（`app/(mobile)`）・現場ボード（`app/(field)`） | **参照なし**（表示しない） |

### 1-5. エージェント・道具

| 場所 | 中身 |
|---|---|
| `agent/AGENTS.md:83` | 「今週全件解除」を既定で禁止（続けて週を生成すると手で動かした訪問まで消える） |
| `agent/AGENTS.md:100, 171` | 青ピンは動かせない（422）／「人の操作も止める」 |
| `agent/rk/ops/visits.py:331-332, 414-415, 504` | 移動・取消・削除の案の時点で「青ピン → 解除しないと…」と注意を出す |
| `agent/rk/ops/visits.py:198` | 担当変更は「青ピンでは止まらない」と正しく書いてある |
| `agent/rk/ops/staff.py:221-223` | 休みの案で青ピンを数えて注意 |
| `agent/rk/` 全体 | **青ピンの付け外しの操作は無い**（`week-pin` の参照なし）。週のコピーはある（`rk/ops/week.py:60-99`） |
| `agent/.claude/skills/rakusuke-kaipoke/SKILL.md:78-81` | 「置換は青ピンを守らない」と明記 |
| `docs/tools/week-leveling/extract.py:25` | `week_pinned` を読むが、`solver.py` は使わない（`:161-169` は `source='manual_week'` と赤で判断） |
| `docs/tools/week-leveling/apply/generic/README.md` | 反映は `DELETE /visits`・`visit-move-week-only`・`PATCH /courses` → **青ピンが 1 件でもあると前 2 つは 422 で止まり、`PATCH /courses` は青ピンの担当も変える** |
| `agent/tools/courseplan/*` | 青ピンの参照なし（赤だけ） |

### 1-6. `source='manual_week'` を作る経路（事実）

`schedule_v2.py:2350`（コースの曜日移動）・`:5610`（今週だけ移動とその共通処理）・`special_visits.py:1318`（特別訪問の配置）・`inbound.py:1278, 1895`（取込の時刻/日付/担当の直し・予定外の昇格）・`op_log_service.py:1197, 1243`（取消を戻す等）・`copy_week.py:791`（週のコピーで写した訪問）・`frontend/lib/scheduling/addVisitExecutor.ts:357`（今週だけ追加）。ならしの反映も `visit-move-week-only` 経由で `manual_week` になる。

【事実】取込の差分で直した `import` の訪問は `manual_week` に変わる（`inbound.py:1895`）。週のコピーは `import` を残すが `manual_week` は置き換える（`copy_week.py:236-243`）。

---

## 2. 操作 × 結果のマトリクス

凡例: ◯ = 残る／✕ = 消える・書き換わる／422 = 止まる／— = 関係なし

| 操作 | 青ピン(フラグ)が止めるか | フラグ付きの訪問 | `manual_week`（フラグ無し） | `import`（フラグ無し） | 操作後の `week_pinned` |
|---|---|---|---|---|---|
| 週を生成 | 止めない | ◯（日の再生成も止まる） | ◯（同） | ◯（同） | そのまま |
| 固定枠戻し・型→週同期 | 止めない | ◯（同） | ◯（同） | ◯（同） | そのまま |
| 全面最適化の反映 | 止めない | ◯（日は止まらない・重複の恐れ【推測】） | ◯ | ◯ | そのまま |
| 週のコピー（置き換え） | 写しを止める | ◯ | **✕ 置き換え** | ◯ | 残した行はそのまま。**写した行は false**（元の週のフラグは写らない） |
| 週のコピー（打刻のある週 = 足すだけ） | 写しを止める | ◯ | ◯ | ◯ | 同上 |
| カイポケ置換（打刻なしの日） | **止めない** | **✕ 白紙化** | ✕ | ✕→import で作り直し | **消える（新しい行は false）** |
| カイポケ差分（打刻ありの日） | **止めない** | 取消・時刻変更される | 同左 | 直すと `manual_week` へ | そのまま |
| 取り込み前に戻す | — | 戻る | 戻る | 戻る | 戻る |
| 青ピン 1 件解除・今週全件解除 | — | フラグ false | **`auto` に変わる** → 次の週生成で消える | フラグ false のみ（source で守られ続ける） | false |
| 今週全件固定 | — | — | **対象外**（立たない） | 立つ | true（manual_week 以外） |
| 今週だけ移動（画面・API） | **422** | — | 動く | 動く | — |
| 改善提案・範囲最適化・詰まり解消（今週だけ） | **止めない** | 動かされ `manual_week` に | 動く | 動く | そのまま（true のまま別の位置へ）【推測：行を探す条件に `week_pinned` が無いため】 |
| 削除（保留プールへ）・今週だけ取消 | **422** | — | 消せる | 消せる | — |
| 職員の休み・コースの曜日移動 | **422** | — | 動く | 動く | — |
| 1 件の担当変更・コース担当 PATCH・一斉未割当 | 止めない | 担当が変わる | 同左 | 同左 | そのまま |
| 週生成内のコース担当の写し | 書き換えない | ◯ | 書き換わる | 書き換わる | そのまま |
| 患者を非稼働に（予定も消す） | 取消しない | **残り続ける**（掃除でも消えない） | 取消 | 取消 | そのまま |
| カイポケへ送る | 影響なし | 送られる | 送られる | 送られる | — |

---

## 3. 青ピンの目的と、今それが果たせているか

| 目的（出典） | 今の状態【事実】 | 他のもので足りているか【推測を含む】 |
|---|---|---|
| ① 週を生成で消されない（spec §5.2 保護 1・2） | `auto` の訪問だけは青ピンでしか守れない。`manual_week`/`import` は source で守られる。本番の写しで青ピン 0 件 → 実際に守っているのは source | 手で動かした・コピーした・取り込んだ・ならした訪問は全部 source で守られている。週42 は「週を生成を押さない」という口約束（HANDOFF §4-2 末尾）で守った。エージェントは週を生成が既定で禁止（`AGENTS.md:83` の表）。週のコピーは前にスナップショット（`api/v1/copy_week.py` で `snapshot_week`）を取る |
| ② 今の形のまま提案を出す（spec §5.1） | **果たせていない**。提案系 4 つは参照なし、今週だけ反映も止めない（B-4） | 何も無い。エージェントの「案 → 了承 → 反映」は人が案を見て断れるので、実際の守りはそこにある |
| ③ その週限りの変更を守る（spec §5.1「動かして青を刺す」） | 動かすと自動で `manual_week` になり ① と同じく守られる。青を刺す必要は無い | `manual_week`（＝画面の「今週のみ」チップ）で足りている |
| ④ 人の手による変更を止める蓋（spec §5.3） | 刺した訪問は人の 1 件操作の大半で 422。ただし担当の変更は通る。誰も刺していない | 蓋が欲しい場面の記録が運用に無い（HANDOFF §4-2 末尾） |
| ⑤ カイポケ取込から守る | **守らない**（B-3）。仕様にも書いていない | カイポケが正（置換の設計 `replace_inbound.py:3-8`）という原則と、取込前のスナップショット 5 世代 |

まとめ【推測】: 目的 ①③ は `source` がすでに担っている。② は一度も実装されていない。④ だけが青ピン固有だが、使われておらず、表示との不一致（B-1）と一括解除の罠（B-2）のほうが害が大きい。

---

## 4. テスト

### 4-1. 青ピンを扱うテスト（ファイルと主な名前）

- `backend/tests/test_visit_week_pin.py` — `test_week_pin_sets_manual_week`（:92・**名前が古い**、中身はフラグ）／`..._works_even_when_diverged_from_master`／`..._release_restores_auto_without_moving`（:158・解除で manual_week→auto を**固定している**）／`..._rejects_non_planned`／`..._allows_import_and_manual_keeping_source`／`..._writes_audit_log`／`..._forbidden_for_staff`／`..._is_idempotent`／`..._404_for_unknown_visit`／`test_bulk_week_pin_pins_all_planned_including_import`／`..._unpin_releases_flag_and_keeps_provenance`／`..._dry_run_counts_without_changing`／`..._writes_single_audit_row`／`..._forbidden_for_staff`／`..._other_week_untouched`／`test_blue_lid_blocks_week_only_move`／`test_blue_lid_blocks_delete`／`test_blue_lid_blocks_placement_patch_but_allows_meta`
- `backend/tests/test_week_pin_reset_protection.py` — `test_week_pinned_auto_visit_survives_reset_and_blocks_regeneration`／`test_import_day_blocks_regeneration`／`test_week_pinned_survives_generate_week_only`
- 青ピンを条件に使う他のテスト: `test_layer1.py:1370 test_reset_v2_week_pinned_survives`／`test_copy_week.py:253, 377, 477`（残す・写した行は false）／`test_course_move_weekday.py:171 test_move_with_week_pinned_visit_returns_422`／`test_staff_off_week.py:321 test_week_pinned_returns_422`／`test_visit_cancel_week.py:225`／`test_visit_service_override.py:211-219`（青ピンでも通す）／`test_patient_status_sync.py:157-168`（取消の対象外）／`test_pool_bulk_apply.py:687-719`（写しで書き換えない）／`test_inbound_snapshot.py:97, 183`（復元）／`test_visits.py:43 test_visits_list_exposes_week_pinned`／`test_agent_week_context.py:113, 291`／`test_change_scope_u2.py:430`（説明のみ）
- 画面: `TimelineDayBoard-week-pin.test.tsx`（6 本）／`BulkWeekPinAllButton.test.tsx`（4 本・「次の週生成で戻る」警告を固定）／`VisitActionMenu.test.tsx`／`StaffTimelineView.test.tsx`／`SubstitutePanel.test.tsx`／`AddVisitAnywhereDialog.test.tsx`／`CourseDayTablePanel-cockpit.test.tsx`／`TimelineDayBoard.test.tsx`／`addVisitPlan.test.ts`／`addVisitExecutor.test.ts`／`patientStatus.test.ts`／`FixedVisitScopeConfirmDialog.test.tsx`・`PatientFixedVisitsPanel.test.tsx`
- エージェント: `agent/tests/test_ops_visits.py:188`／`agent/tests/test_ops_staff.py:112`

### 4-2. この調査で流した結果（2026-10-12・作業中のツリー）

- 画面: `TimelineDayBoard-week-pin`・`BulkWeekPinAllButton`・`VisitActionMenu` → **32 本すべて通過**（以前の調査にあった赤の 4 本は、作業中の赤の整理で外れている模様）。
- サーバー: `test_visit_week_pin`・`test_week_pin_reset_protection`・`test_copy_week`・`test_staff_off_week`・`test_course_move_weekday`・`test_visit_cancel_week`・`test_inbound_snapshot` を `-k "pin or copy"` で → **通過（1 件スキップ）**。
- 既知の失敗: 仕様書 §6 の `test_schedule_v2_api.py` の `reset_to_fixed` 系 2 件（ベースラインで失敗・今回は流していない）。
- **テストが無いもの**: 表示と蓋の一致／提案系の今週だけ反映が青ピンを動かすこと／カイポケ置換・差分での青ピン／担当変更 1 件が青ピンを通すこと／一括 API の拠点・過去日。

---

## 5. 選択肢と影響

前提【事実】: 赤の整理（`red-pin-simplify-design.md`）は「青は範囲外」。赤のトグルが消えて右下が `[青][×]` になる（`red-pin-removal/3-frontend.md §5`）ので、青の UI は赤の作業と同じ部品（`TimelineDayBoard.tsx` の `CardPinControls`、`push-pin.tsx`）を触る。

### (A) 青ピンを廃止する（UI・API・フラグを外し、週は source と「週を生成」の安全化で守る）

- 変えるもの
  - サーバー: `week-pin` API 2 本を削除（または 410）。422 の 8 か所（`schedule_v2.py:1522-1545, 1966-1980, 2312-2316`・`op_log_service.py:102-105`・`visits.py:974-987, 1149-1155`）を外す。週生成・固定枠戻し・全面最適化・写し・コピーの `week_pinned` 条件は、**フラグが全部 false なら外しても挙動は同じ**。`_visit_is_week_pinned` 削除。`schemas/visit.py:47` 削除。患者ステータス連動の `excluded.week_pinned` 削除。
  - 画面: 画鋲・トグル・一括ボタン・コックピットの理由表示・急休代替の確認・`MakeWeekDialog.tsx:445` の文言。`manual_week` は既存の「今週のみ」チップだけで示す。
  - 道具・エージェント: `AGENTS.md`・スキル 4 本・`rk/ops/visits.py`/`staff.py` の注意文、`agent/sql/*.sql`・leveling `extract.py:25` の列。
  - 「週を生成」の安全化（代わりの守り）: 実行前に「消える `auto` の件数・手で直した訪問 0 件」を出す確認（dry-run は固定枠戻しに既にある）、`snapshot_week` を週生成の前にも取る、など。
- 移行: 本番の `week_pinned=true` を読むだけの SQL で 0 件と確かめる → 0 件なら列はしばらく残して読まない（1 リリース後に migration で DROP）。1 件以上ならその訪問を `manual_week` に変えてから。
- リスク: `auto` の訪問を「週を生成でも消さない」手段が無くなる（必要なら手で 1 回動かして `manual_week` にすれば同じ）。人の蓋が無くなる（使われた記録なし）。
- 手間: 中（サーバー小・画面中・テストの書き換えが多い：§4-1 の約 30 本）。
- エージェント: 「了承 → 反映」が唯一の守りになる。今と変わらない（エージェントは青を付け外ししない）。
- 週のコピー: `keep_pinned` が消え、コピーは `import`・打刻・取消・過去日だけ残す（今と実質同じ）。
- カイポケ取込: 論点 P-3 自体が消える。
- 赤の整理: 右下が `[×]` だけになり、`push-pin.tsx` の青の上書きも不要 → 赤の作業と同時にやると UI の手戻りが一番少ない。

### (B) 人の「蓋」として残し、表示＝蓋にそろえて B-1〜B-8 を直す

- 変えるもの: 表示を `week_pinned` だけに（P-1 a）。解除で source を変えない（P-2 a・`schedule_v2.py:2473-2477, 2566-2567` 削除・`test_week_pin_release_restores_auto_without_moving` と一括解除テストの書き換え）。一括の対象を拠点・今日以降に絞り、`manual_week` も固定対象に。提案系の今週だけ反映（`_apply_visit_move_week_only` の中）に 422 を足す。担当変更 1 件・`PATCH /courses`・一斉未割当をどうするか決める（止めるか、説明を直すか）。取込の置換・差分の扱いを決めて実装（守るなら置換で日を止める＝`manual_cancel` と同じ形）。コックピットで外せるように。コピー確認文を直す。ペアカード・スマホ・現場ボードの表示。
- 移行: なし（フラグは今 0 件の見込み）。
- リスク: 和集合をやめると画面の青が週43・44 で一気に消える → 利用者には「青が消えた」と見える（説明が要る）。直す箇所が多く、蓋の穴（担当変更・取込）を全部ふさぐと、取込・ならし・休みの反映が青ピン 1 件で止まる場面が増える。
- 手間: 大（サーバー 8〜10 か所・画面 6〜8 部品・テスト新規 6 本以上）。
- エージェント: 青を外す操作を `rk` に足すか、「人に外してもらう」を手順に入れる必要。ならしの反映（`DELETE`/移動が 422）も同じ。
- 週のコピー・カイポケ取込: 「青は残す」を全経路で本当にする必要。取込を止めると「カイポケが正」の原則と衝突。
- 赤の整理: 右下 `[青][×]` は残る。赤の作業の後に別リリース。

### (C) エージェント・エンジン用の「囲い」だけに残す（人の蓋はやめる）

- 変えるもの: 人の 1 件操作の 422（8 か所）を外す。代わりに提案系・ならしの道具・エージェントの案作りが `week_pinned` を「動かさない」として読む（`improvement_engine`・`scope_optimizer`・`unblock_search`・`pool_bulk_inserter`・leveling `solver.py`・`agent/tools/courseplan`）。付け外しは画面に残すか、エージェントの案の中で「この訪問は動かさない」として持つか。
- 移行: なし。
- リスク: 赤（K-5「ならしの道具も赤を守る」）と役割が重なる。違いは「型（毎週）か、今週の 1 訪問か」だけ。使い分けを利用者が覚える必要。
- 手間: 中〜大（エンジン 4 つ＋道具 2 つに読み込みを足す）。
- エージェント: 案作りの入力が 1 つ増える。了承の画面に「青なので動かさなかった」を出す必要。実はエージェントの案は人が了承するので、「これは動かさないで」は案の中の指示（その場限り）で足りる可能性が高い【推測】。
- 週のコピー・取込: 人の蓋ではないので、取込は今どおり上書きでよい（エンジンの囲いだけ）。
- 赤の整理: 赤と青の両方が「エンジンだけを縛る」ことになり、概念は近づく。

### (D) 「今週だけ」の印（`manual_week`・「今週のみ」チップ）に統合する

- 変えるもの: 青の画鋲を廃止し、表示は「今週のみ」チップだけ（もともと同じ訪問に両方出ている）。フラグは (A) と同じく外すか、`manual_week` を立てる操作（「今週だけ固定にする」＝source を `manual_week` に変えるだけ）に置き換える。一括固定は「今週の `auto` を全部 `manual_week` にする」になる。解除は「型に戻す」（`manual_week→auto`）＝チップの「固定へ昇格」と並ぶ「型に戻す」ボタン。
- 移行: (A) と同じ。
- リスク: 旧方式（source 書き換え）に戻る面がある。spec §5.2 がやめた理由は「`import` の出所が失われ、一括解除で取込訪問が消え得た」こと → **`import` は対象外にする**（`import` はもともと守られている）なら再発しない。人の蓋は無くなる。
- 手間: 小〜中（画面は「今週のみ」チップに寄せる。サーバーは一括 API の中身を差し替えるか削除）。
- エージェント: 「今週だけ／今後ずっと」の 2 択（`AGENTS.md` §4-3）とそのまま一致し、説明が一番簡単。
- 週のコピー: **注意** — コピーは `manual_week` を置き換える（`copy_week.py:236-243`）。「今週だけ」に統合するなら、コピーで `manual_week` も残すか、今どおり置き換えるか（＝コピーは週を作り直す操作）を決める必要。
- カイポケ取込: 今どおり（`manual_week` も置換で消える。差分で直すと `manual_week` になる）。
- 赤の整理: (A) と同じく右下の画鋲が消える。

### 比較（推測を含む）

| | 人の蓋 | 提案から守る | 週生成から守る | 取込 | 手間 | 赤の作業との相性 |
|---|---|---|---|---|---|---|
| A 廃止 | 無し | 了承で守る | source＋確認 | 論点が消える | 中 | ◎（同時に UI を片付け） |
| B 直して残す | 有り（全経路をふさぐ） | 有り | フラグ＋source | 決める必要 | 大 | △（別リリース） |
| C 囲いのみ | 無し | 有り | フラグ＋source | 上書き | 中〜大 | ○ |
| D 今週だけに統合 | 無し | 了承で守る | source | 今どおり | 小〜中 | ◎ |

---

## 6. 決める前に確かめること（未確認）

1. 本番 DB の `select count(*) from visits where week_pinned and deleted_at is null`（読むだけ）。写しでは 0 件（HANDOFF §4-2 B-1）。
2. 週42〜44 の実画面で、青い画鋲がほぼ全件に出ているか（HANDOFF §5-2・推測のまま）。
3. 終了した患者の青ピン訪問がカイポケへ送られるか（`csv_builder.py` に患者ステータスの絞りが見当たらない・未確認）。
4. 全面最適化の反映（`apply_week_only`）で、青ピンの日に別時刻の訪問が重ねて作られるか（日の再生成止めが無い・推測）。
5. 提案系の今週だけ反映で、青ピンの訪問がフラグ付きのまま別の位置へ動くか（`_apply_visit_move_week_only` に確認が無い・テスト無し）。
6. PO が「人の蓋」を今後使う場面を想定しているか（使われた記録は無い）。
