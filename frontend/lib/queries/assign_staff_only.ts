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

// ---------------------------------------------------------------------------
// Phase G-91: 確認レビューフロー review_items — BE スキーマと厳密ミラー
// ---------------------------------------------------------------------------

/** レビューカード内の 1 visit (= コース所属の 1 患者訪問). */
export const reviewVisitSchema = z.object({
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  start_time: z.string(),
  sex_restriction: z.string().nullable().optional(),
  // 当該 review item の原因患者 (連続になる患者 / 性別 NG 患者) なら true.
  is_cause: z.boolean(),
});

export type ReviewVisit = z.infer<typeof reviewVisitSchema>;

/** 確認レビューフローの 1 カード (= レビュー対象の 1 コース). */
export const reviewItemSchema = z.object({
  course_id: z.string().uuid(),
  office_name: z.string(),
  course_code: z.string(),
  weekday: z.number().int().min(0).max(6),
  // 'consecutive' (🟡 連続/軽度) | 'gender' (🔴 性別/重度).
  reason: z.enum(['consecutive', 'gender']),
  candidate_staff_id: z.string().uuid(),
  candidate_staff_name: z.string(),
  candidate_staff_sex: z.string().nullable().optional(),
  visits: z.array(reviewVisitSchema).default([]),
  // 2 名体制で連動する partner course_id 一覧 (= apply 時に一緒に反映される).
  linked_course_ids: z.array(z.string().uuid()).default([]),
});

export type ReviewItem = z.infer<typeof reviewItemSchema>;

// ---------------------------------------------------------------------------
// Wave N-2: 体制上不可避な連続の「お知らせ」(自動確定済み) — BE スキーマとミラー
// ---------------------------------------------------------------------------

/**
 * 不可避連続 1 件のお知らせ.
 * reason_kind は将来拡張を考慮して z.string() で寛容に受け取る
 * (未知の kind でもセクション全滅しない).
 */
export const autoCommittedNoticeSchema = z.object({
  course_id: z.string(),
  course_code: z.string(),
  weekday: z.number().int().min(0).max(6),
  office_name: z.string().nullable().optional(),
  staff_name: z.string(),
  cause_patient_names: z.array(z.string()).default([]),
  // 'single_staff' | 'all_recent' — 未知値も弾かず z.string() で受容
  reason_kind: z.string(),
  reason_text: z.string(),
});

export type AutoCommittedNotice = z.infer<typeof autoCommittedNoticeSchema>;

// ---------------------------------------------------------------------------
// W-11: 性別制約を満たす候補ゼロで残った違反の警告 — BE スキーマとミラー
// ---------------------------------------------------------------------------

/**
 * 性別残留違反 1 件の警告.
 * 性別ブロック未割当 + override 候補ゼロ + 現担当が性別制約違反のコースに出る。
 * 承認/確定ではなくアクション不能の警告 (= 管理者に手動調整を促す)。
 */
export const unresolvedGenderWarningSchema = z.object({
  course_id: z.string(),
  course_code: z.string(),
  weekday: z.number().int().min(0).max(6),
  // BE は常に string を送る ("" フォールバック込み)。null は将来変更への防御のみ。
  office_name: z.string().nullable(),
  current_staff_name: z.string(),
  reason_text: z.string(),
});

export type UnresolvedGenderWarning = z.infer<typeof unresolvedGenderWarningSchema>;

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
  // Phase G-91: 確認レビューフロー (= 連続 index0 / 性別ブロック) のカード一覧.
  review_items: z.array(reviewItemSchema).default([]),
  // Wave N-2: 不可避連続のお知らせ (自動確定済み / レビュー不要).
  // 旧 BE は本フィールドを返さないため .default([]).catch([]) で寛容に受け取る.
  auto_committed_notices: z.array(autoCommittedNoticeSchema).default([]).catch([]),
  // W-11: 性別残留違反の警告 (候補ゼロ・手動調整を促す).
  // 旧 BE は本フィールドを返さないため .default([]).catch([]) で寛容に受け取る.
  unresolved_warnings: z.array(unresolvedGenderWarningSchema).default([]).catch([]),
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

// ---------------------------------------------------------------------------
// Phase G-91 (修正1): POST /apply-staff-review — 確認レビュー承認カードを反映
// ---------------------------------------------------------------------------

const APPLY_STAFF_REVIEW_PATH = '/api/v1/schedule/apply-staff-review';

/** apply-staff-review リクエスト 1 件 (= 1 コース). */
export const applyStaffReviewItemSchema = z.object({
  course_id: z.string().uuid(),
  staff_id: z.string().uuid(),
});

export type ApplyStaffReviewItem = z.infer<typeof applyStaffReviewItemSchema>;

export const applyStaffReviewRequestSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  items: z.array(applyStaffReviewItemSchema).min(1),
});

export type ApplyStaffReviewRequest = z.infer<typeof applyStaffReviewRequestSchema>;

export const applyStaffReviewResultItemSchema = z.object({
  course_id: z.string().uuid(),
  ok: z.boolean(),
  error: z.string().nullable().optional(),
});

export type ApplyStaffReviewResultItem = z.infer<typeof applyStaffReviewResultItemSchema>;

export const applyStaffReviewResponseSchema = z.object({
  applied_count: z.number().int(),
  results: z.array(applyStaffReviewResultItemSchema).default([]),
  message: z.string(),
});

export type ApplyStaffReviewResponse = z.infer<typeof applyStaffReviewResponseSchema>;

/**
 * POST /api/v1/schedule/apply-staff-review — 確認レビュー承認カードを反映する mutation.
 *
 * 自動スタッフ割付と **同一の ``_persist`` 経路** で DB 反映する (= VSA INSERT /
 * course_status / primary・secondary 同期 / 2 名体制 を全て実施.
 * 旧 trainee companion 注入は新人同行 Phase 2 で撤去済み).
 * 従来の PATCH /courses ループ (assigned_staff_id のみ) では起きていた
 * 「apply 済コースの visit が未割当表示・子アプリにスタッフが見えない」 リグレッションを解消.
 *
 * onSuccess で courses / visits を invalidate する。
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 */
export function useApplyStaffReview(): UseMutationResult<
  ApplyStaffReviewResponse,
  Error,
  ApplyStaffReviewRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ApplyStaffReviewResponse, Error, ApplyStaffReviewRequest>({
    mutationFn: async (raw) => {
      const payload = applyStaffReviewRequestSchema.parse(raw);
      return fetcher<ApplyStaffReviewResponse>(APPLY_STAFF_REVIEW_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      // courses は assigned_staff_id が変わるので必ず再取得
      void qc.invalidateQueries({ queryKey: ['courses'] });
      // visits は primary/secondary_staff_id が同期される
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
