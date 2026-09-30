'use client';

/**
 * 前の週をコピーして週を作る (docs/plans/copy-week-design-2026-09-30.md §4-7).
 *
 *   GET  /api/v1/schedule/v2/copy-week/sources?target_week_start=  写す元の候補週
 *   POST /api/v1/schedule/v2/copy-week/preview                    確認画面 (DB 不変)
 *   POST /api/v1/schedule/v2/copy-week                            実行 (confirm: true)
 *
 * 戻すのは既存のスナップショット復元 (useRestoreInboundSnapshot) を使う。
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';
import type { AssignStaffOnlyResponse } from '@/lib/queries/assign_staff_only';

const BASE = '/api/v1/schedule/v2/copy-week';

export const holidaySchema = z.object({ date: z.string(), name: z.string() });
export type Holiday = z.infer<typeof holidaySchema>;

export const copyWeekSourceItemSchema = z.object({
  week_start: z.string(),
  visits: z.number().int(),
  patients: z.number().int(),
  cancelled: z.number().int(),
  unplanned: z.number().int(),
  qr_arrivals: z.number().int(),
  holidays: z.array(holidaySchema),
});
export type CopyWeekSourceItem = z.infer<typeof copyWeekSourceItemSchema>;

export const copyWeekSourcesSchema = z.object({
  target_week_start: z.string(),
  items: z.array(copyWeekSourceItemSchema),
});
export type CopyWeekSources = z.infer<typeof copyWeekSourcesSchema>;

export const copySkipCountsSchema = z.object({
  cancelled: z.number().int(),
  unplanned: z.number().int(),
  special_extra: z.number().int(),
  inactive_patient: z.number().int(),
  user_excluded: z.number().int(),
  kept_conflict: z.number().int(),
  occupied_day: z.number().int(),
  past_day: z.number().int(),
});
export type CopySkipCounts = z.infer<typeof copySkipCountsSchema>;

export const notInFixedItemSchema = z.object({
  visit_ids: z.array(z.string()),
  patient_id: z.string(),
  patient_name: z.string(),
  weekday: z.number().int(),
  target_date: z.string(),
  start_time: z.string(),
  end_time: z.string(),
  excluded: z.boolean(),
});
export type NotInFixedItem = z.infer<typeof notInFixedItemSchema>;

export const missingFixedItemSchema = z.object({
  patient_id: z.string(),
  patient_name: z.string(),
  weekday: z.number().int(),
  target_date: z.string(),
  start_time: z.string(),
  end_time: z.string(),
  visits: z.number().int(),
});
export type MissingFixedItem = z.infer<typeof missingFixedItemSchema>;

export const existingCountsSchema = z.object({
  total: z.number().int(),
  replace: z.number().int(),
  keep_checked_in: z.number().int(),
  keep_import: z.number().int(),
  keep_pinned: z.number().int(),
  keep_cancelled: z.number().int(),
  keep_past: z.number().int(),
  keep_other: z.number().int(),
});
export type ExistingCounts = z.infer<typeof existingCountsSchema>;

export const copyWeekPreviewSchema = z.object({
  source_week_start: z.string(),
  target_week_start: z.string(),
  mode: z.enum(['replace', 'add_only']),
  copy_count: z.number().int(),
  fill_count: z.number().int(),
  patients: z.number().int(),
  by_weekday: z.array(
    z.object({ weekday: z.number().int(), date: z.string(), count: z.number().int() }),
  ),
  skipped: copySkipCountsSchema,
  temp_course_count: z.number().int(),
  not_in_fixed: z.array(notInFixedItemSchema),
  missing_fixed: z.array(missingFixedItemSchema),
  missing_fixed_count: z.number().int(),
  missing_patients_without_visits: z.number().int(),
  existing: existingCountsSchema,
  source_holidays: z.array(holidaySchema),
  target_holidays: z.array(holidaySchema),
});
export type CopyWeekPreview = z.infer<typeof copyWeekPreviewSchema>;

export const copyWeekResultSchema = z.object({
  source_week_start: z.string(),
  target_week_start: z.string(),
  mode: z.enum(['replace', 'add_only']),
  created: z.number().int(),
  filled: z.number().int(),
  replaced: z.number().int(),
  skipped: copySkipCountsSchema,
  courses_created: z.number().int(),
  courses_cleared: z.number().int(),
  staff_mirrored: z.number().int(),
  snapshot_id: z.string(),
  restorable: z.boolean(),
  // 中身の形は useAssignStaffOnly と同じ (表示は同じ処理に渡す)。ここでは実行時の
  // 依存を増やさないため型だけ借りる (パネルのテストが assign_staff_only を丸ごと mock する)。
  assign_result: z
    .custom<AssignStaffOnlyResponse>((v) => typeof v === 'object' && v !== null)
    .nullable()
    .optional(),
});
export type CopyWeekResult = z.infer<typeof copyWeekResultSchema>;

export interface CopyWeekOptions {
  sourceWeekStart: string;
  targetWeekStart: string;
  excludeVisitIds: string[];
  fillFromFixed: boolean;
}

function useAuth() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAdmin: status === 'authenticated' && session?.user?.role === 'admin',
  };
}

/** 写す元の候補週 (写す先の前 8 週のうち訪問のある週・新しい順)。 */
export function useCopyWeekSources(targetWeekStart: string, enabled: boolean) {
  const { accessToken, refreshToken, isAdmin } = useAuth();
  return useQuery<CopyWeekSources>({
    queryKey: ['copy-week', 'sources', targetWeekStart],
    queryFn: async () =>
      copyWeekSourcesSchema.parse(
        await fetcher<unknown>(`${BASE}/sources?target_week_start=${targetWeekStart}`, {
          accessToken,
          refreshToken,
        }),
      ),
    enabled: enabled && isAdmin,
  });
}

/** 確認画面の内容。選んだオプションが変わるたびに取り直す (実行と同じ計算)。 */
export function useCopyWeekPreview(opts: CopyWeekOptions | null) {
  const { accessToken, refreshToken, isAdmin } = useAuth();
  return useQuery<CopyWeekPreview>({
    queryKey: [
      'copy-week',
      'preview',
      opts?.sourceWeekStart,
      opts?.targetWeekStart,
      [...(opts?.excludeVisitIds ?? [])].sort().join(','),
      opts?.fillFromFixed,
    ],
    queryFn: async () => {
      if (!opts) throw new Error('copy-week preview: options are required');
      return copyWeekPreviewSchema.parse(
        await fetcher<unknown>(`${BASE}/preview`, {
          method: 'POST',
          body: JSON.stringify({
            source_week_start: opts.sourceWeekStart,
            target_week_start: opts.targetWeekStart,
            exclude_visit_ids: opts.excludeVisitIds,
            fill_from_fixed: opts.fillFromFixed,
          }),
          accessToken,
          refreshToken,
        }),
      );
    },
    enabled: opts !== null && isAdmin,
    placeholderData: keepPreviousData,
  });
}

/** 実行。盤面が入れ替わるので訪問・コース・スナップショット一覧を取り直す。 */
export function useCopyWeek() {
  const { accessToken, refreshToken } = useAuth();
  const qc = useQueryClient();
  return useMutation<CopyWeekResult, Error, CopyWeekOptions & { assignStaff: boolean }>({
    mutationFn: async (o) =>
      copyWeekResultSchema.parse(
        await fetcher<unknown>(BASE, {
          method: 'POST',
          body: JSON.stringify({
            source_week_start: o.sourceWeekStart,
            target_week_start: o.targetWeekStart,
            exclude_visit_ids: o.excludeVisitIds,
            fill_from_fixed: o.fillFromFixed,
            assign_staff: o.assignStaff,
            confirm: true,
          }),
          accessToken,
          refreshToken,
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['schedule'] });
      void qc.invalidateQueries({ queryKey: ['copy-week'] });
      void qc.invalidateQueries({ queryKey: ['integrations', 'inbound-snapshots'] });
    },
  });
}
