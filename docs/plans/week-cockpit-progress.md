# 今週の運転席(Phase E) 実装進捗 — 再開用メモ

最終更新: 2026-08-22 夕 (全ストリーム完了・最終レビュー是正済・最終検証済)。
契約書 = `week-cockpit-design.md` / 調査 = `week-cockpit-investigation.md` / モック = `docs/mockups/staff-schedule-week-cockpit-mock.html`。
進め方 = ディレクター(メイン)が Opus executor に並行実装させ、code-reviewer でストリームごとにレビュー→差し戻し→承認。**git commit/stash/checkout はディレクターのみ**(BE-2 が stash を1回誤実行→即 pop・復元確認済み)。

## ストリーム状況

| ストリーム | 状態 | 備考 |
|---|---|---|
| BE-1 代替候補 API (`substitute_candidates.py` 他) | ✅ 承認(レビュー是正済・20 tests) | reason code に accompaniment 追加済み。kana タイブレークは見送り |
| FE-B タイムライン (`cockpit/StaffTimelineView.tsx`) | ✅ 承認(是正済・27 tests) | onVisitMove payload = {visit, fromStart, toStart|null, toStaffId?} |
| FE-A 部品 (`cockpit/*`, `lib/queries/cockpit.ts`, `lib/schemas/v2/cockpit.ts`) | ✅ 承認(是正済・92 tests) | SyncBar は busyKey/rpaRunning ガード・失敗 toast・failed>0 表示・突合後 unsent 再計算・マスタ突合/全曜日差分 復元済み |
| BE-2 CSVスナップショット/未送信/反転 (mig 0076, `csv_snapshot.py`, `integrations.py`) | ✅ 承認(是正済・27 tests・partial status 導入で test_integration_kaipoke の1アサーション更新) | レビュー HIGH: ①月跨ぎ週はフェイルクローズ ②cached シート掃除が include=False 再送ガードを消す(→partial status+除外) / MED: applied 時 snapshot 削除・upsert キーに week_start・_unsent_events を build_outbound_plan の sendable に寄せる・reverse の applied 422+置換 / テスト追加 |
| BE-3 取消/固定除外 (mig 0075, visit-cancel-week, events cancel-week) | ✅ 承認(是正済: layer1 再INSERT根治・取込復活は source='manual_cancel' で識別・replace_inbound 422・inbound 占有チェック・undo ガード共用・course-move で cancelled も移動) | レビュー HIGH: ①`layer1_expander._fetch_manual_conflict_keys` に `or_(source!=auto, status==cancelled)`(取消枠の再INSERT→UNIQUE衝突で週生成500の根治) ②`inbound.py` 復活分岐を pending_cancelled(同一実行内)のみに / MED: reason を note に書かず op_log label へ・昇格後 fixed のテスト / LOW-7 commit の try / LOW-8 csv_builder 定数 |
| FE-C 結線 (`CourseDayTablePanel.tsx`, `StaffWeekBoard.tsx`, `KaipokeReconcilePanel.tsx`) | ✅ 承認(最終レビュー是正済: ●未送信ドット本筋・staff-overrides-week 失効・cancelled_at 4経路除外・週切替リセット・固定帯の二重表示解消・onMarkOff await 順序バグ修正) | 指示内容は本ファイル末尾「FE-C 指示」参照。中断時は `git diff --stat` で着手範囲を確認してから再開 |

## 最終検証(2026-08-22・ディレクター実施)
- BE: 全件2分割。fail 32 件は全て HEAD(d80d630 の git archive 複製)でも同一 = **新規 fail ゼロ**(accompaniment revive 3件の一時回帰は manual_cancel 方式で解消)。ruff check/format 緑。
- FE: tsc 0 / vitest 1666 pass・fail 2 = 既知(middleware manager 残骸・BulkPoolInsertDialog フレーク)・e2e/*.spec は Playwright 収集エラーで従来どおり。prettier 緑。
- 本番投入: コード上のブロッカーは解消。**デプロイは PO 確認後**(mig 0075/0076・build --no-cache・pg_dump・実機は 1件→目視→全件 の順)。

## 再開手順(完了済み・参考)
1. `git status --short` / `git diff --stat` で現在の変更範囲を確認(未追跡: alembic 0075/0076・substitute_*・csv_snapshot・cockpit/・tests 4本)。
2. BE-2/BE-3 の是正が途中なら、上表の指摘を再度 executor(opus) に渡して完了させる。完了判定 = 各テスト緑 + ruff。
   - BE 全件ベースライン(HEAD d80d630 でも同一の fail 25件): test_integration_kaipoke 9 / manager ロール廃止起因 RBAC 11 / test_schedule_v2_api reset-to-fixed 2 + test_inactive_patient_visit_cleanup 1 / sqlite フレーク 2。これ以外の fail が出たら新規。
3. FE-C を完了させる(指示は下)。完了判定 = tsc 0 / `pnpm vitest run components/schedule/v2 lib/queries lib/schemas` 緑 / lint。
4. 最終レビュー(code-reviewer opus): FE-C 結線 + BE-2/BE-3 再レビュー(HIGH の再発防止テストが入っているか)。
5. ドキュメント: weekly-space-design.md §8 に Phase E 行を追加、week-cockpit-design.md の逸脱記録を反映、HANDOFF 更新。
6. コミット(pre-commit 通す・--no-verify 禁止)。**デプロイは PO/ユーザー確認後**(migration 0075/0076 あり → build --no-cache・pg_dump 必須)。

## 既知の設計判断(PO 未確認 ★)
- 急休の候補スコア = 馴染み(直近担当)ほど高得点・患者ごとに合算。新人は常に×。
- 取消の解除(cancel=false)は過去日でも可。青ピンは両方向 422。
- source='kaipoke' に昇格済みイベントを「今週だけ外す」とカイポケ側には残る(FE でトースト注意)。
- 月跨ぎ週の ●未送信 はフェイルクローズ(🔄突合に誘導)。本筋の月2回差分は後続。
- 未送信イベントは「らく助で消した(delete)」方向は未対応(kind='add' のみ)。

## FE-C 指示(再投入用・要約)
SyncBar で KaipokeReconcilePanel を置換(訪問もゴースト・矢印任意) / Row2 に [リスト|タイムライン] 切替 → StaffTimelineView(alwaysShowUnassignedRow, onVisitMove は toStart/toStaffId の有る方だけ呼ぶ・同一 op_group_id) / 最上段 FixedEventRow(onExclude→useEventCancelWeek・kaipoke 由来は注意トースト) / StaffWeekBoard: 訪問クリック→VisitActionMenu(取消=useVisitCancelWeek・担当=useVisitAssignStaffWeek・時刻/曜日=useVisitMoveWeekOnly・型も変える=ChangeScopeChoice)、cancelled 打消線、セル hover「休みにする/＋訪問/＋イベント」、cancelled_at イベント打消線 / 休みにする→SubstitutePanel(onMarkOff=useCreateOverride, onApply=courseId なら useUpdateCourse・無ければ visitIds を useVisitAssignStaffWeek) / ＋訪問→AddVisitDialog→POST /visits source='manual_week' / 出所チップ集計 / 全操作後 invalidate(op-log/visits/courses/staff-events) / UNASSIGNED キーを単一定数に / useWeekStaffEvents を cockpitEventReadSchema で parse / テスト(メニュー→取消 mutation・休み→override+assign 呼び順・表示切替・訪問ゴースト)。
