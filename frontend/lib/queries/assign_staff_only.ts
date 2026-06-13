'use client';

/**
 * useAssignStaffOnly — Wave 17 Phase B-3 (Layer 3 のみ).
 *
 * POST /api/v1/schedule/assign-staff-only
 *
 * 当該週の course (course_fixed) に対してスタッフ自動割付を実行する。
 * Layer 1 (visits 再構築) は事前に `useGenerateWeekOnly` で済ませてある前提。
 *
 * Wave 16 までの `generate-and-assign` (1 ボタン) を分離したうちの後半。
 * UI は「週を生成」「自動割付」の 2 ボタンに分かれている (Excel 準拠)。
 *
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 *
 * BE 仕様 (Phase A 並行実装):
 *   - AssignStaffOnlyRequest: { iso_year, iso_week, office_id? }
 *   - AssignStaffOnlyResponse: { iso_year, iso_week, courses_assigned, message }
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { toast } from 'sonner';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

const ASSIGN_STAFF_ONLY_PATH = '/api/v1/schedule/assign-staff-only';

// ---------------------------------------------------------------------------
// Zod schemas — BE AssignStaffOnlyRequest / Response とミラー
// ---------------------------------------------------------------------------

export const assignStaffOnlyRequestSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  office_id: z.string().uuid().nullable().optional(),
});

export type AssignStaffOnlyRequest = z.infer<typeof assignStaffOnlyRequestSchema>;

// ---------------------------------------------------------------------------
// Warning schema — Wave 27 Phase B: event conflict warnings from BE
// ---------------------------------------------------------------------------

export const assignWarningSchema = z.object({
  staff_id: z.string().uuid(),
  staff_name: z.string().nullable().optional(),
  event_id: z.string().uuid(),
  event_type: z.string(),
  visit_id: z.string().uuid(),
  visit_start_time: z.string(),
  message: z.string(),
});

export type AssignWarning = z.infer<typeof assignWarningSchema>;

// ---------------------------------------------------------------------------
// Phase G-89: ローテ衝突 / 未割当コース警告 — BE スキーマと厳密ミラー
// ---------------------------------------------------------------------------

/** 🔴 ローテ衝突 (= 人手不足で直近担当者を再割り当てした) 警告. */
export const rotationConflictWarningSchema = z.object({
  patient_id: z.string().uuid(),
  patient_name: z.string().nullable().optional(),
  staff_id: z.string().uuid(),
  staff_name: z.string().nullable().optional(),
  course_id: z.string().uuid(),
  course_code: z.string(),
  weekday: z.number().int().min(0).max(6),
  visit_start_time: z.string().nullable().optional(),
  // working_recent index. 0 = 1 つ前と同じ (= 連続), 1 = 2 個前と同じ.
  recent_index: z.number().int().nonnegative(),
  is_consecutive: z.boolean(),
});

export type RotationConflictWarning = z.infer<typeof rotationConflictWarningSchema>;

/** 🟠 未割当 (= 人手不足で担当を確保できなかった) コース警告. */
export const unassignedCourseWarningSchema = z.object({
  course_id: z.string().uuid(),
  course_code: z.string(),
  weekday: z.number().int().min(0).max(6),
  visit_start_time: z.string().nullable().optional(),
  patient_ids: z.array(z.string().uuid()).default([]),
  patient_names: z.array(z.string()).default([]),
});

export type UnassignedCourseWarning = z.infer<typeof unassignedCourseWarningSchema>;

export const assignStaffOnlyResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  courses_assigned: z.number().int().nonnegative(),
  message: z.string(),
  warnings: z.array(assignWarningSchema).default([]),
  // Phase G-89: ローテ衝突 (埋めたが直近担当者を再割り当て)
  rotation_warnings: z.array(rotationConflictWarningSchema).default([]),
  // Phase G-89: 未割当 (担当を確保できなかった)
  unassigned_warnings: z.array(unassignedCourseWarningSchema).default([]),
});

export type AssignStaffOnlyResponse = z.infer<typeof assignStaffOnlyResponseSchema>;

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * POST /api/v1/schedule/assign-staff-only — Layer 3 のみを実行する mutation.
 *
 * onSuccess で courses / visits を invalidate し、
 * 担当スタッフ dropdown / コーステーブル下のヘッダーが最新化される。
 */
export function useAssignStaffOnly(): UseMutationResult<
  AssignStaffOnlyResponse,
  Error,
  AssignStaffOnlyRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<AssignStaffOnlyResponse, Error, AssignStaffOnlyRequest>({
    mutationFn: async (raw) => {
      const payload = assignStaffOnlyRequestSchema.parse(raw);
      const body: Record<string, unknown> = {
        iso_year: payload.iso_year,
        iso_week: payload.iso_week,
      };
      if (payload.office_id) body.office_id = payload.office_id;
      return fetcher<AssignStaffOnlyResponse>(ASSIGN_STAFF_ONLY_PATH, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: (data) => {
      // courses は assigned_staff_id が変わるので必ず再取得
      void qc.invalidateQueries({ queryKey: ['courses'] });
      // visits は primary_staff_id が同期される可能性
      void qc.invalidateQueries({ queryKey: ['visits'] });
      // Wave 27 Phase B-4: event conflict warnings をトーストで通知
      if (data.warnings && data.warnings.length > 0) {
        for (const w of data.warnings) {
          toast.warning(
            `${w.staff_name ?? w.staff_id} は ${w.event_type} 予定。${w.visit_start_time} の訪問と重複`,
          );
        }
      }
    },
  });
}
