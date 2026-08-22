# 職員スケジュール「今週の運転席」— 本実装前の現状調査報告 (2026-08-22)

対象 HEAD: `d80d630` (本番 `936504c` + docs) / DB migration 0074 / RPA `a12c54e`
モック: `docs/mockups/staff-schedule-week-cockpit-mock.html` (同期バー・急休代替・訪問メニュー・固定帯・タイムライン)
前提の正典: `docs/plans/weekly-space-design.md` (憲法5条・Phase A1〜M 完了)

目的: モックで合意した動きを、**既存資産のどこに載せ、何を新規に作るか**を事実ベースで確定する。
調査は FE盤面 / BE週空間API / カイポケ同期 / 代替割当エンジン の4領域を並列で行い、食い違いは原本を再確認した。

---

## 0. 結論(先に)

| モックの機能 | 既存で足りる | 新規/拡張 | 規模 |
|---|---|---|---|
| 訪問メニュー: 担当変更 / 時刻 / 曜日 | ✓ `visit-assign-staff-week` / `visit-move-week-only` | FE メニューのみ | 小 |
| 訪問メニュー: 今週だけ取消 | ✓ `visits.status='cancelled'` (取込の delete が既に使用・FEは打消線表示済み) | `PATCH /visits/{id}` で status を書く経路の確認 + undo op | 小 |
| ＋訪問(週のみ・PFVを作らない) | △ 空き枠登録/臨時コース/QR予定外 の3経路が個別 | 盤面用の薄い作成API or 既存 `POST /visits` の source='manual_week' 利用 | 小〜中 |
| ＋イベント(個人/全員) | ✓ `POST /staff/{id}/events` | 「全員(休み除く)」はFEで一括POST | 小 |
| 急休 → 代替候補 → 付替 | ✗ 候補API無し(設計書 p3-1/p5 のみ) / 付替は ✓ `course-assign`系 | **`POST /schedule/v2/substitute-candidates` 新規**(Layer3の判定関数を流用) + 休み登録 `POST /staff/{id}/overrides` と付替をFEで連結 | 中 |
| 固定イベント(朝会)の「今週だけ外す」 | ✗ 個別除外の手段なし(展開は冪等・再展開で復活) | `staff_events` の週内除外印(論理削除 or `excluded` 相当) + 展開側の尊重 | 小〜中 |
| ●未送信(らく助側の変更をRPAなしで即把握) | ✗ 同期時刻の刻印なし・最終取得CSVも未保存 | **最終取得CSVの保存(新テーブル)** + `build_local_diff(current_csv=保存分)` をローカル実行 | 中 |
| 🔄突合 ⇩1件/⇧1件 | ✓ C1/C2 実装済 | — | — |
| ⇩全件 / ⇧全件 / ⇧上書き全件 | △ apply は itemIds 省略=全件 / 取込は include 全部 true で全件 | FE の2段クリックボタン + 上書き送信(inboundシートを outbound へ反転)の整理 | 小〜中 |
| 訪問差分の盤面ゴースト | ✗ イベントのみ | `ReconcileMarker` を訪問にも拡張(correction_items の before/after から生成) | 小 |
| タイムライン(スタッフ×時刻・横バーDnD) | △ `lib/scheduling/timeline.ts` の座標/パレット/レーン計算は流用可 | 横向きレンダラ + ドラッグ→`visit-move-week-only`/`visit-assign-staff-week` | 中 |
| 出所チップ(型どおり/今週だけ/カイポケ由来/🔒固定) | ✓ `visits.source`(auto/manual_week/import/manual) + `staff_events.source`(fixed/kaipoke/manual) | FE集計のみ | 小 |

migration は **1本**(最終取得CSVの保存)で済む見込み。代替候補APIは read-only なので DB 変更なし。

---

## 1. FE 盤面の現状 (frontend/components/schedule/v2)

### 1-1. 構造
- `CourseDayTablePanel.tsx` (4812行) が親。`activeTab==='staff'` で `KaipokeReconcilePanel`(開いている時・盤面の上) と `StaffWeekBoard` を描く (L4110-4180)。右ペインに保留プール `PoolOverviewPane` (sticky・L4368-)。
- props 供給: `overviewVisits`(L1288-1320) / `assignedStaffByTemplateWeekday`(L1424-1432) / `courseIdByTemplateWeekday`(L2879-2890) / `offByStaffWeekday`(L2866-2875・`useWeekStaffOverrides`) / `staffEventsByStaff`(L613-619・`useWeekStaffEvents`)。
- ハンドラ: `handleCourseDropOnStaff`(L2895-2945: `useUpdateCourse` or `useCourseMoveWeekdayWeekOnly`) / `handleVisitDropOnStaff`(L3060-3143: `useVisitMoveWeekOnly`+`useVisitAssignStaffWeek`・422→確認ダイアログ→acknowledge 再送 L2834-2851) / `handleCourseBandUnassign`(L3026-3057) / `handleVisitUnassignDrop`(L3146-3153) / `onAddEvent→setSlotEventState`(L4140-4149) / `onEventClick→setTlEventEdit`(L4151)。
- `StaffWeekBoard.tsx` (685行): 行=スタッフ(訪問の primary_staff_id 優先→コース担当→（担当なし）L166-223)。セル=コース帯+訪問行+イベント帯(L440-475)+休み網掛け(L393-404)+ゴースト(L407-437)。DnD は `courseDnd.ts` の MIME payload。**イベント source='fixed' の描き分けは無い**。訪問行クリックは `onPatientClick`(患者詳細)のみ。
- undo: `lib/queries/opLog.ts` (`useOpLogState/useUndoOpLog/useRedoOpLog/useInvalidateOpLog`)。BE op 種は `op_log_service.py` (set_visit_staff / move_course_weekday / delete_visit ほか)。
- 再利用できる部品: `ChangeScopeChoice`(毎週型/この週のみ) / constraint confirm フロー / `timeline.ts`(`TL_DAY_START_MIN=540, TL_DAY_END_MIN=1080, TL_ROW_PX=52, minutesToY, durationToHeight, snapYOffsetToMinutes(15分), genderPalette, assignLanes`) / `SlotRegisterDialog` / `TimelineEventAddDialog`。
- テスト: `__tests__/StaffWeekBoard.test.tsx` `__tests__/KaipokeReconcilePanel.test.tsx`(345行・「来週」を動的計算する過去日テストの作法)。

### 1-2. KaipokeReconcilePanel (912行)
- Phase: idle→events→visits→ready|error (L127)。hooks: `useEventsInboundPreview / useSmartInboundPreview / useCorrectionItems / useApplyEventsInbound / useApplyInbound / useUpdateCorrectionItem / useBulkUpdateItems / useStartDiffLocal / useStartApply / useStartDiffInbound / useMasterReconcile / useKaipokeLive` (L140-145)。
- ゴースト生成 L315-330 (イベントのみ)。過去日判定 L717-723 (`Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo'})`)。
- 全件: イベントは `applyEventChanges(changes)`(L353-385) で全件可。**訪問は include 排他で1件ずつのみ**(bulkItemsMut)。

### 1-3. FE に無いもの
訪問クリックの編集メニュー / 急休パネル / 取消UI / 固定イベント帯 / 横バータイムライン / 未送信バッジと同期バー / 出所チップ / 訪問ゴースト。

---

## 2. BE 週空間 API の現状 (backend/app/api/v1)

| API | 書込対象 | 制約 | undo |
|---|---|---|---|
| `POST /schedule/v2/visit-assign-staff-week` | `visits.primary_staff_id` + `manual_staff_override` + VSA 置換 | admin / 青ピン422 | set_visit_staff |
| `POST /schedule/v2/visit-move-week-only` | `visit_date` + `start_time` + `source='manual_week'` | admin / 青ピン422 | あり |
| `POST /schedule/v2/course-move-weekday-week-only` | `courses.weekday` + 配下 visits 一括 | 移動先同code 422 / 青ピン配下 422 | move_course_weekday |
| `PATCH /courses/{id}` (assigned_staff_id) | コース担当 + 配下 visits.primary_staff_id 同期(manual_staff_override=False のみ) | admin | set_course_staff |
| `DELETE /visits/{id}` | `deleted_at` (soft)・`cascade_fixed_visit` は 422 封鎖済 | 青ピン422・cascade_partner 既定 true | delete_visit |
| `POST /schedule/v2/reset-to-fixed` | whitelist 削除+PFV再展開(manual_week/import 保護) | admin | — |

- **取消**: `visits.status` は `planned/in_progress/completed/cancelled` (`models/visit.py:51`)。カイポケ取込 delete は `status='cancelled'`(履歴保持・`services/kaipoke/inbound.py:12,352`)。csv_builder は cancelled を除外 (`csv_builder.py:205`) → **取消した訪問は送信差分で「delete」になる**(モックの動き通り)。FE も cancelled を打消線表示済み (`CourseDayTablePanel.tsx:1103`)。→ 「今週だけ取消」は soft-delete ではなく **status='cancelled'** に統一するのが筋。
- **新規作成(週のみ)**: 空き枠登録(propose-slots→登録)/臨時コース「臨」(courses.py・取込時)/QR予定外(`POST /visits/adhoc-checkin`, source='manual')。盤面からの「＋訪問」は `POST /visits` に source='manual_week' を与える薄い経路で足りる(PFV不変・週生成保護される: `models/visit.py:60-66`)。
- **固定イベント**: `staff_event_defaults`(mig 0074) → 週生成3地点で `expand_staff_event_defaults` が `staff_events(source='fixed')` へ冪等展開。**週内で1人だけ外す手段が無い**(削除しても再展開で復活)。
- **休み**: `StaffWeeklyOverride(override_type: off|custom_time, iso_year/iso_week/weekday)`。API `GET /staff/overrides-week` / `POST/PATCH/DELETE /staff/{id}/overrides`。**登録しても当日の訪問/コース担当へは連鎖しない**。am_off/pm_off はエンジン未対応(既知)。
- RBAC: 上記は全て `require_role("admin")`。

---

## 3. カイポケ同期基盤の現状 (backend/app/api/v1/integrations.py, services/kaipoke)

### 3-1. 送信 (outbound)
- `POST /integrations/diff-local`(L1151-1259): **RPA export(同期・~50s)で現況CSVを取得** → らく助生成CSV と `compare_schedules_from_content`(normalize_names=True・include_unassigned=True) → `correction_sheets(direction='outbound')` + items。`build_local_diff(current_csv=...)` は **CSV注入可**(`local_diff.py:76,99`)。
- `POST /integrations/apply {sheetId, itemIds?, dryRun}`(L1334-1505): itemIds 指定=部分(送信済 include=False 化・再指定422) / 省略=全件(include=True 全部・sheet→applying)。**過去日(JST当日以前)は週スコープで自動スキップ**(L1388-1425)。RPA へは `item_to_kaipoke_correction` の平坦形式。結果 `kaipoke_jobs.result_summary`(correction_count/skipped_past/log_tail)。
- items.before/after JSONB: `user_name, date(日), start_time, end_time, staff1, staff2, service_type, business_type, remarks`。action: add/update/delete/date_change/companion_change。
- 訪問送信成功時の **`visits.kaipoke_id` 刻印は無し**(イベントは `promote_sent_events` で source='kaipoke'+external_id 刻印あり `events_outbound.py:163-206`)。

### 3-2. 取込 (inbound)
- `diff-inbound`(L1800-1896: before=らく助/after=カイポケ・全週可) → `apply-inbound`(L1924-2039: delete→cancelled / edit,date_change→manual_week / add→import・実適用前に `snapshot_week`・include で1件ずつ)。
- イベント: `events-inbound-preview`(start→status ポーリング) → `apply-events-inbound`(部分適用可)。
- 復元: `restore_inbound_snapshot`(打刻あり週は409)。

### 3-3. ジョブ/RPA
- `kaipoke_jobs(job_type fetch|push, status, week_start, params{op}, result_summary)`。`GET /integrations/live` が idle 観測で遅延確定(`_reconcile_latest_job` L446-525・自己完結 op は30分スキップ)。RPA は単一スロット。所要: export ~50s / diff-local ~1分 / events-preview 2-3分 / apply 数分〜(件数依存)。
- **export した `csv_content` は永続化していない**(`integrations.py:496` で保存時に意図的に除去)。

### 3-4. 「●未送信」の実現方式(比較)
| 案 | 内容 | migration | 長所 | 短所 |
|---|---|---|---|---|
| a. 同期刻印 | visits/staff_events に `synced_at/sync_hash` | 2テーブル×2列 | 判定が軽い | 刻む責務が apply/inbound/replace の3経路に分散・置換取込時の定義が曖昧・訪問の kaipoke_id 刻印が現状無い |
| **b. 最終取得CSVの保存 + ローカル差分**(推奨) | export 成功時に `kaipoke_csv_snapshots(office, month, week_start, fetched_at, csv)` へ保存。未送信= `build_local_diff(current_csv=保存分)` を **RPAなし**で実行(純粋CSV比較・秒単位) | 1テーブル | 既存差分エンジンをそのまま使う(氏名正規化・'-'行・date_change ペアリング込み)/ 「最終同期状態」が1箇所 / 取込(apply-inbound)後も同じ式で整合 | 保存CSVが古いと「カイポケ側が違う」と混ざる → バーに fetched_at を出し、🔄突合で更新する運用(モック通り) |

案 b は `trigger_diff_local` に `use_cached_csv=True` を足すだけで「未送信の計算」になり、🔄突合(=RPA export で CSV を更新してから同じ計算)と**同一経路**になる。イベント側は `staff_events.source!='kaipoke'`(manual/fixed) かつ external_id 無し = 未送信 で既に判定可。

---

## 4. 代替候補エンジン (backend/app/services/scheduling/layer3_assignment.py)

- 単日×単コース/訪問の候補を返す **公開APIは無い**(p3-1 `staff-substitute/candidates` / p5 course-substitute は設計のみ・grep で未実装確認)。
- 流用できる判定関数(全て実装済み):
  `load_active_staff`(L3185・休み off/custom_time の反映 L3230-3244) / `_staff_satisfies_gender`(L708) / `_staff_satisfies_ng`(L724) / `_has_event_overlap_with_buffer`(L768・±15分) / `_cost_single_cell`(継続性/移動/負荷のスコア) / `pfv_validator._find_conflict`(L201・同コース他患者の時間衝突) / `StaffInfo.effective_office_for_weekday`(L247-281)。
- 新設 `POST /schedule/v2/substitute-candidates {staff_id, date, course_id?}` → `{absent_visits:[{visit_id, patient_name, start,end, course_id, candidates:[{staff_id,name,status:'◎|△|×',reason,score}]}], warnings}`。◎=全ハードOK / △=時間重なりのみ / ×=休み・NG・性別・拠点・新人。BE コア ~300行 + API ~200行 + テスト。read-only。
- 付替の実行は既存 `PATCH /courses/{id}`(コース丸ごと) / `visit-assign-staff-week`(1件) で足りる。manual_staff_override=True の行は Layer3 が以後動かさない(保護済)。

---

## 5. 設計判断が必要な点(POへ)

1. **取消の表現**: `status='cancelled'`(推奨・取込と統一・履歴残る・送信で delete になる) か `deleted_at` か。
2. **固定イベントの個別除外**: (a) その週の `staff_events` 行に `excluded=true`(新列) を立て展開が尊重 / (b) 行を soft-delete し展開側が `deleted_at` 行を「展開済み」とみなして再生成しない。(b) は列追加不要だが「復活させたい」時の導線が要る。
3. **未送信の方式**: 上記 3-4 案 b で進めてよいか(migration 1本)。
4. **「⇧上書き全件」の意味**: 突合で「カイポケ側が違う」N件をらく助正で上書き = inbound シートの before/after を反転した outbound シートを作って apply。既存に反転ユーティリティは無いので小実装が要る。
5. **タイムラインのDnD範囲**: 横=時刻(15分)・縦=担当 まで(曜日跨ぎはリストで)。

---

## 6. 推奨 Phase 分割 (Phase E = 運転席)

| Phase | 内容 | 依存 | 規模 |
|---|---|---|---|
| E1 | 訪問メニュー(担当/時刻/曜日/今週だけ取消/型も変える…)+ ＋訪問/＋イベント + 出所チップ | 既存API | FE中・BE小(status書込+undo op) |
| E2 | 急休パネル: `substitute-candidates` 新設 + 休み登録→付替の連結 | E1 | BE中・FE中 |
| E3 | 固定イベント帯(全員行)+ 今週だけ外す | 判断2 | BE小・FE小 |
| E4 | ●未送信: `kaipoke_csv_snapshots`(mig 0075)+ diff-local のキャッシュ実行 + 同期バー(未送信/突合の2段)+ 全件ボタン + 訪問ゴースト | 判断3,4 | BE中・FE中 |
| E5 | 横バータイムライン + DnD | E1 | FE中 |

E1→E2→E4 の順で「急休→付替→送信」が最短で回る。E3/E5 は独立。

---

## 7. 既知の注意点(調査で再確認)
- 送信は未来日のみ(当日以前は実績保護で自動スキップ)。FE も同基準(Asia/Tokyo)で灰色化すること。
- RPA 単一スロット: 送信/突合/取込は同時に走らせない(`useKaipokeLive.running` でガード)。
- SQLAlchemy VSA 置換は ORM delete+先 flush(op_log_service/schedule_v2 のコメント)。
- 氏名正規化は `master_reconcile.normalize_person_name` 単一ソース。
- 職員スケジュールの操作は admin のみ。staff は閲覧。
- `test_integration_kaipoke.py` の9件 fail はベースライン(触らない)。
