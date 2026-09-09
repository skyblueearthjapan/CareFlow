'use client';

/**
 * invalidateScheduleAll — 「予定が動いた」ときに一括で失効させる query キー群。
 *
 * 正典 = `docs/plans/patient-status-schedule-design-2026-09-09.md` §7-4
 * （`usePlaceSpecialMark` の invalidate 群を共通関数に切り出す）。
 *
 * 患者ステータス連動（`useChangePatientStatus`）は 1 回の API で
 * 「未来の予定の取消 / 特別訪問期間の終了 / 型からの再生成」までまとめて起きるため、
 * 盤面・タイムライン・週リスト・職員スケジュール・プール・モニター・モバイルの
 * どれが開いていても即座に最新化される必要がある。個別に列挙すると必ず漏れるので、
 * 「スケジュールに関わるルートキー」を 1 箇所に集約する。
 *
 * キーの出所（既存コードから採取・重複はまとめた）:
 *   - `lib/queries/visits.ts`            : ['visits']
 *   - `lib/queries/courses.ts`           : ['courses']
 *   - `lib/queries/fieldBoard.ts`        : ['field-board']
 *   - `lib/queries/integrations.ts`      : ['board']
 *   - `lib/queries/specialVisitWeek.ts`  : ['special-visit']（periods / calendar / pool の親）
 *   - `lib/queries/cockpit.ts`           : ['staff','events'] / [OP_LOG_STATE_KEY] /
 *                                          ['staff-overrides-week']
 *   - `lib/queries/monitor.ts`           : ['monitor']
 *   - `lib/queries/me.ts`                : ['me']（モバイルの「今日の訪問」）
 *   - `lib/queries/schedule*.ts`         : ['schedule'] / ['schedule-health']
 *   - `lib/queries/diffAdd.ts`           : ['diff-add']
 *   - `lib/queries/patient_fixed_visits.ts` : ['patient-fixed-visits'] / ['pfv-course-presence']
 *   - `lib/queries/pending_requests.ts`  : ['pending-requests']（非稼働化で自動却下される）
 *   - `lib/queries/notifications.ts`     : ['notifications']（連動処理が admin へ通知を作る）
 *   - `lib/queries/integrations.ts`      : ['integrations']（未送信サマリ / 突合 / 週予定）
 *   - `lib/queries/acceptance_matrix.ts` : ['acceptance-matrix']
 *   - `lib/queries/scheduleHealth.ts`    : ['schedule-health-trend']
 *   - `lib/queries/improvementSuggestions.ts` : [IMPROVEMENT_SUGGESTIONS_KEY]
 *   - `lib/queries/dashboard.ts`         : ['dashboard']
 *   - `lib/queries/visitAssignStaffWeek.ts` : ['visit-staff-assignments']
 *
 * 呼び出し元（`specialVisitWeek.ts` / `cockpit.ts` / `visits.ts`）の既存 invalidate は
 * 本レーンでは触らない（後追いで寄せる）。
 */
import type { QueryClient } from '@tanstack/react-query';

import { IMPROVEMENT_SUGGESTIONS_KEY } from '@/lib/queries/improvementSuggestions';
import { OP_LOG_STATE_KEY } from '@/lib/queries/opLog';

/** 予定が動いたときに失効させるルートキー。prefix 一致で子キーも巻き込む。 */
export const SCHEDULE_QUERY_KEYS: readonly (readonly unknown[])[] = [
  ['visits'],
  ['courses'],
  ['field-board'],
  ['board'],
  ['special-visit'],
  ['staff', 'events'],
  ['staff-overrides-week'],
  ['monitor'],
  ['me'],
  ['schedule'],
  ['schedule-health'],
  ['diff-add'],
  ['patient-fixed-visits'],
  ['pfv-course-presence'],
  [OP_LOG_STATE_KEY],
  // 予定が動くと一緒に動くもの（ステータス連動は申請の自動却下・通知作成まで行う）。
  ['pending-requests'],
  ['notifications'],
  ['integrations'],
  ['acceptance-matrix'],
  ['schedule-health-trend'],
  [IMPROVEMENT_SUGGESTIONS_KEY],
  ['dashboard'],
  ['visit-staff-assignments'],
];

/**
 * スケジュール系のキャッシュを一括失効させる。
 *
 * `['patients']` は **含めない**（患者マスタの失効は呼び出し側の責務。
 * 例: `useChangePatientStatus` は `['patients']` を自分で invalidate してから呼ぶ）。
 */
export function invalidateScheduleAll(qc: QueryClient): void {
  for (const key of SCHEDULE_QUERY_KEYS) {
    void qc.invalidateQueries({ queryKey: key as unknown[] });
  }
}
