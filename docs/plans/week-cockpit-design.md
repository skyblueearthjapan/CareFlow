# 職員スケジュール「今週の運転席」Phase E 設計書 (2026-08-22)

正典: `weekly-space-design.md`(憲法5条) の Phase E として追加。調査報告 = `week-cockpit-investigation.md`。
モック = `docs/mockups/staff-schedule-week-cockpit-mock.html`。
**本書は並行実装の契約書**: API の入出力・担当ファイル・決定事項を固定する。変更が必要ならディレクターへ戻す。

## 1. 決定事項(ディレクター判断・PO未確認は★)

| # | 論点 | 決定 |
|---|---|---|
| D1 | 訪問の「今週だけ取消」 | `visits.status='cancelled'` + **`source='manual_cancel'`**(取込 delete 由来の cancelled と区別するため・2026-08-22 追補: 取込 add は取込由来の cancelled だけ復活させ、manual_cancel は failed で止める)。csv_builder が除外するので送信差分は delete。undo op `cancel_visit` を新設(元 source は payload で保持) |
| D2 | 固定イベント(朝会)の「今週だけ外す」 | `staff_events.cancelled_at`(新列・mig 0075)。行は残す→展開の冪等キーに当たり再生成されない。events_outbound / 盤面 / Layer3 の blocking 判定は cancelled を除外 |
| D3 | ●未送信 | `kaipoke_csv_snapshots`(mig 0076)に export 成功時の CSV を保存し、`build_local_diff(current_csv=保存分)` を **RPA なし**で実行する `POST /integrations/unsent-summary`。イベントは `source!='kaipoke' and external_id is null and cancelled_at is null` |
| D4 | ⇧上書き(らく助が正) | inbound シート(before=らく助/after=カイポケ)の各 item を反転して outbound シートを生成し既存 `/integrations/apply` に流す。`POST /integrations/correction-sheets/{id}/reverse` |
| D5 | タイムライン DnD | 横=時刻(15分スナップ・`visit-move-week-only`)、縦=担当(`visit-assign-staff-week`)。曜日跨ぎはリスト側のみ |
| D6 | ＋訪問(盤面から) | 既存 `POST /visits` に `source='manual_week'` を渡す(PFV不変・週生成保護済み)。コースは `course_id` 省略可(臨時扱い) ★PO: 臨時コース「臨」へ自動所属させるかは後続 |
| D7 | 全件ボタン | FE 2段クリック(3秒)。送信は当日以前を BE が自動スキップ(既存)。取込全件 = include 全 true で apply-inbound |
| D8 | 代替候補 | read-only API。付替の実行は既存 `PATCH /courses/{id}`(コース丸ごと) / `visit-assign-staff-week`(1件) を FE が呼ぶ。休み登録は既存 `POST /staff/{id}/overrides` |

## 2. API 契約(新規/拡張)

### 2-1. `POST /api/v1/schedule/v2/substitute-candidates` (BE-1)
req: `{ "staff_id": UUID, "date": "YYYY-MM-DD", "course_id": UUID|null }`
res:
```json
{
  "absent_staff": {"id": UUID, "name": str},
  "date": "YYYY-MM-DD", "weekday": 0-5,
  "groups": [                                  // course ごと(course_id null は臨時/未所属をまとめる)
    {"course_id": UUID|null, "course_label": str,
     "visits": [{"visit_id": UUID, "patient_id": UUID, "patient_name": str, "start_time": "HH:MM", "end_time": "HH:MM", "week_pinned": bool}],
     "candidates": [
       {"staff_id": UUID, "name": str, "sex": "male|female|null", "office_name": str|null,
        "status": "ok|warn|ng",              // ◎ / △ / ×
        "reasons": [{"code": str, "message": str, "visit_id": UUID|null}],
        // code: off | ng_staff | gender | trainee | office | event_overlap | time_overlap | not_working_day | accompaniment
        //   (accompaniment = 同行(メンター)で拘束・warn 扱い。2026-08-22 レビュー追補)
        "score": float,                       // 高いほど推奨。_cost_single_cell 由来(継続性+/移動-/負荷-)
        "load_today": int                     // その日の既存訪問数
       }
     ]}
  ],
  "warnings": [str]
}
```
規則: ok=ハード制約すべてOKかつ時間重なり無し / warn=時間重なり(`time_overlap`)・イベント重なり(`event_overlap`)・同行拘束(`accompaniment`)のみ。時間重なりは既存 `accompaniment._overlaps` / `pfv_validator._find_conflict` と同じ規則(移動時間+バッファ・同住所ペア免除)。score は継続性(馴染み・患者ごとに**合算**)+/負荷-(移動項は G-90 で撤去済みのため無し) / ng=休み・非勤務日・NG・性別・新人(2名必要枠で単独不可)・拠点不可。candidates は status(ok→warn→ng)→score desc で整列。対象スタッフ自身と（担当なし）は含めない。admin のみ。
流用: `layer3_assignment.load_active_staff / _staff_satisfies_gender / _staff_satisfies_ng / _has_event_overlap_with_buffer / _cost_single_cell / StaffInfo.effective_office_for_weekday`, `pfv_validator._find_conflict`。cancelled 訪問・deleted 訪問は対象外。

### 2-2. `POST /api/v1/schedule/v2/visit-cancel-week` (BE-3)
req: `{ "visit_id": UUID, "cancel": bool, "reason": str|null }` → res: VisitRead。
`cancel=true`: status planned→cancelled(当日以前・打刻あり・in_progress/completed は 422)。`false`: cancelled→planned(過去日ガード無し=送信対象外なので安全側)。青ピンは両方向 422。reason は `visits.note` に「今週取消: …」を1行刻む(重複抑止・undo では戻さない)。2名体制ペア(visit_group_id)は一緒に。op_log `cancel_visit`(inverse は逆フラグ)。admin。

### 2-3. `POST /api/v1/staff/{staff_id}/events/{event_id}/cancel-week` (BE-3)
req: `{ "cancel": bool }` → res: EventRead(`cancelled_at` を含む)。source 不問(fixed/manual/kaipoke)。`GET /staff/{id}/events` と週一括取得は cancelled 行も返し `cancelled_at` を露出(FE が打消線)。`expand_staff_event_defaults` は既存ロジックのまま(external_id キー一致で skip=再生成されない)。`events_outbound.build_outbound_plan` と Layer3 の blocking 収集は `cancelled_at is null` のみ。

### 2-4. `POST /api/v1/integrations/unsent-summary` (BE-2)
req: `{ "week_start": "YYYY-MM-DD" }` → res:
```json
{"week_start": "...", "snapshot": {"fetched_at": iso|null, "month": "YYYY-MM", "row_count": int}|null,
 "sheet_id": UUID|null,                      // 生成した outbound シート(direction='outbound', origin='cached')
 "items": [CorrectionItemRead...],           // 既存型。date は実日付 "YYYY-MM-DD" も付与(date_iso)
 "events": [{"id": UUID, "staff_id": UUID, "staff_name": str, "date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM", "title": str, "kind": "add|delete"}],
 "sendable_count": int, "past_count": int}
```
snapshot が無ければ `snapshot=null, items=[]`(FE は「🔄突合でカイポケ現況を取得してください」)。RPA は呼ばない。`kaipoke_csv_snapshots(id, office_id|null, month, week_start|null, fetched_at, csv_text TEXT, row_count, source_op)`: 既存の export を行う全経路(diff-local / smart-inbound / diff-inbound / master-reconcile / export_current_week_csv)で成功時に upsert(同 office×month は最新1件に置換)。

### 2-5. `POST /api/v1/integrations/correction-sheets/{sheet_id}/reverse` (BE-2)
inbound シートの include=true な item を反転(add↔delete・edit/date_change は before/after 入替)した新 outbound シートを作り `{ "sheet_id": UUID, "item_count": int }`。その後 FE は既存 `/integrations/apply` を呼ぶ。

### 2-6. 既存の利用(変更なし)
`PATCH /courses/{id}`(assigned_staff_id) / `visit-assign-staff-week` / `visit-move-week-only` / `course-move-weekday-week-only` / `POST /visits`(source='manual_week') / `POST /staff/{id}/events` / `POST /staff/{id}/overrides` / `/integrations/apply {itemIds?}` / `apply-inbound` / `apply-events-inbound` / `diff-local` / `diff-inbound` / `events-inbound-preview`。

## 3. FE 構成(新規ファイルのみで作り、最後に結線)

`frontend/components/schedule/v2/cockpit/` 配下(新規):
- `VisitActionMenu.tsx` — 訪問クリックのポップオーバー: 今週だけ取消/取消をやめる・担当変更(select)・時刻変更(15分)・曜日移動・型も変える…(ChangeScopeChoice へ委譲)。出所・同期状態・過去日警告を表示。props でコールバックを受け、API は呼ばない(結線は親)。
- `SubstitutePanel.tsx` — 急休パネル: `useSubstituteCandidates` を呼び、group ごとに候補(◎△×+理由+score)を表示。選択→`onApply({staffId:'none'|UUID, groups})`。「休みにする」も同パネルで(`useCreateOverride`)。
- `AddVisitDialog.tsx` / 既存 `TimelineEventAddDialog` 流用でイベント。全員イベントは FE で複数 POST。
- `FixedEventRow.tsx` — 盤面最上段「全員（固定）」帯(StaffWeekBoard 外で描き、StaffWeekBoard の上に置く)。source='fixed' を staff×日で集約・休みの人除外・cancelled は「今週 ○○除外」。クリック→個別除外。
- `SyncBar.tsx` — 同期バー: 左=●未送信(`useUnsentSummary`・select+1件/全件)・右=🔄突合(既存 KaipokeReconcilePanel のロジックを hook 化した `useKaipokeReconcile` を呼ぶ)+⇩1件/⇩全件/⇧上書き/⇧上書き全件。作業中は既存の rakusuke-bob 演出。`DiffDetailCard.tsx`(何から何へ)。
- `StaffTimelineView.tsx` — 曜日1つ×スタッフ行×8:00-19:00 横バー。pointer DnD(横15分/縦担当)。ゴースト描画は `ReconcileMarker` を受け取る。
- `lib/queries/cockpit.ts` — `useSubstituteCandidates / useVisitCancelWeek / useEventCancelWeek / useUnsentSummary / useReverseSheet` + `lib/schemas/v2/cockpit.ts`(zod)。
- `ReconcileMarker` を訪問にも拡張(`kind:'visit'|'event'`, patient_name, course_label)。

結線(FE-C・最後): `CourseDayTablePanel.tsx` の staff タブに SyncBar / FixedEventRow / 表示切替(リスト|タイムライン) / VisitActionMenu / SubstitutePanel を組み込み、`StaffWeekBoard` に `onVisitClick`・セルアクション(休みにする/＋訪問/＋イベント)・cancelled 打消線・●未送信ドット・出所チップ集計を追加。既存 KaipokeReconcilePanel は SyncBar に吸収(テストは移植)。

## 4. 担当ファイルの分割(並行時の衝突回避)

| ストリーム | 触ってよいファイル | 触らない |
|---|---|---|
| BE-1 代替候補 | `services/scheduling/substitute_candidates.py`(新) / `api/v1/substitute_candidates.py`(新) / `schemas/substitute.py`(新) / `api/v1/__init__.py`(include 1行追記のみ) / `tests/test_substitute_candidates.py`(新) / `services/accompaniment.py`(一括版 has_accompaniment 追加のみ・レビュー追補) | layer3_assignment.py の既存関数は import のみ(変更不可) |
| BE-2 同期 | `alembic/versions/0076_kaipoke_csv_snapshots.py`(down_revision='0075_staff_events_cancelled_at') / `models/kaipoke_csv_snapshot.py`(新)+`models/__init__.py` / `services/kaipoke/csv_snapshot.py`(新) / `services/kaipoke/local_diff.py` / `api/v1/integrations.py` / `schemas/integration.py` / `tests/test_unsent_summary.py`(新) | schedule_v2.py / visits.py / staff_events.py |
| BE-3 取消・固定除外 | `alembic/versions/0075_staff_events_cancelled_at.py`(revision='0075_staff_events_cancelled_at', down_revision='0074_staff_event_defaults') / `models/staff.py`(StaffEvent に cancelled_at) / `api/v1/schedule_v2.py`(visit-cancel-week 追記) / `api/v1/staff_events.py` / `schemas/staff_events*.py` / `schemas/visit.py` / `services/op_log_service.py`(cancel_visit) / `services/staff_event_defaults.py` / `services/kaipoke/events_outbound.py`(cancelled 除外) / `services/scheduling/layer3_assignment.py`(イベント収集で cancelled 除外・1箇所) / `services/scheduling/propose_slots_service.py`・`api/v1/schedule.py`(cancelled 除外 各1行・実装時追補) / tests | integrations.py / local_diff.py |
| FE-A 部品 | `components/schedule/v2/cockpit/{VisitActionMenu,SubstitutePanel,AddVisitDialog,FixedEventRow,SyncBar,DiffDetailCard}.tsx` + `__tests__` / `lib/queries/cockpit.ts` / `lib/schemas/v2/cockpit.ts` / `lib/queries/integrations.ts`(hook 追加のみ) | CourseDayTablePanel.tsx / StaffWeekBoard.tsx / KaipokeReconcilePanel.tsx |
| FE-B タイムライン | `components/schedule/v2/cockpit/StaffTimelineView.tsx` + test / `lib/scheduling/timeline.ts`(export 追加のみ) | 同上 |
| FE-C 結線(後続・単独) | CourseDayTablePanel.tsx / StaffWeekBoard.tsx / KaipokeReconcilePanel.tsx(撤去or薄化) / 既存テスト | — |

共通ルール: **git commit / stash / checkout 禁止**(ディレクターが行う)。担当外ファイルは読むだけ。pre-commit 相当(ruff format/check, prettier, tsc, vitest/pytest)を自分の範囲で通す。backend テストは `python -m pytest <files> -q`(sqlite in-memory・並行実行可)。frontend は `pnpm vitest run <files>` / `pnpm tsc --noEmit`。正式名称は「らく助」。

## 5. 受け入れ基準(レビュー観点)
- 憲法1: 盤面操作が PFV/テンプレ/イベント既定を変更しない(テストで PFV 不変を確認)。
- 取消・固定除外が週生成/固定枠戻し/展開で復活しない。
- unsent-summary が RPA を呼ばない(kaipoke client をモックして呼ばれないことをテスト)。
- 過去日(JST)の送信除外が BE/FE 両方で一致。
- admin 以外 403。
- 既存テストのベースライン fail(test_integration_kaipoke 9件ほか)以外を増やさない。

## 6. 実装後の既知制約・PO 確認事項(2026-08-22 最終レビューより)
- 送信済み(source='kaipoke' に昇格)イベントを「今週だけ外す」と、カイポケ側には残る(outbound に delete 方向が無い)。FE はトーストで注意。恒久対応=イベント outbound の delete 対応。
- 昇格後に時刻/名称を編集した固定イベントを外すと、内容一致キーも外れ次の週生成で復活し得る(恒久対応=展開元 default_id を保持する列)。
- 月跨ぎ週の ●未送信 はフェイルクローズ(🔄突合へ誘導)。本筋は月2回差分。
- 未送信イベントは「らく助で消した」方向(delete)は未対応。
- 取消した患者枠は保留プールに再浮上する(auto_allocator の未配置判定)★PO 判断。
- 代替候補の kana タイブレーク未対応(名前コードポイント順)。
- 急休候補スコア=馴染み(直近担当)加点・患者ごと合算、新人は常に×、取消解除は過去日でも可、青ピンは両方向 422 ★PO 確認。
