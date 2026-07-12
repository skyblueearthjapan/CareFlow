/**
 * 新人同行 (trainee accompaniment) zod schemas.
 *
 * 設計書 `docs/plans/trainee-accompaniment-design.md` §6 の API 契約に 1:1 対応。
 * Backend `backend/app/api/v1/trainee_accompaniments.py` / `schemas` と一致させる。
 *
 * 概念 (§1):
 *   - 新人 (staff.is_trainee=true) が先輩のコース / 患者訪問に「同行」として付く。
 *   - 紐付けは週単位。target_type='course' (日単位コースインスタンス全体) or
 *     'visit' (患者個別) の 2 種を同一週で混在できる。
 *   - source='default' = 毎週の既定 (テンプレ層) 由来、'manual' = 画面からの手動追加。
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const TRAINEE_ACCOMPANIMENT_TARGET_TYPES = ['course', 'visit'] as const;
export type TraineeAccompanimentTargetType =
  (typeof TRAINEE_ACCOMPANIMENT_TARGET_TYPES)[number];

export const TRAINEE_ACCOMPANIMENT_SOURCES = ['default', 'manual'] as const;
export type TraineeAccompanimentSource = (typeof TRAINEE_ACCOMPANIMENT_SOURCES)[number];

// ---------------------------------------------------------------------------
// GET /trainee-accompaniments — response item (§6.1)
// ---------------------------------------------------------------------------

/** コースリンク先の解決済みコース (日単位インスタンス)。 */
export const traineeAccompanimentCourseSchema = z.object({
  id: z.string().uuid(),
  weekday: z.number().int().min(0).max(6),
  code: z.string(),
  office_id: z.string().uuid().nullable().optional(),
  template_id: z.string().uuid().nullable().optional(),
});
export type TraineeAccompanimentCourse = z.infer<typeof traineeAccompanimentCourseSchema>;

/** 個別リンク先の解決済み訪問。 */
export const traineeAccompanimentVisitSchema = z.object({
  id: z.string().uuid(),
  date: z.string(),
  start: z.string().nullable().optional(),
  patient_name: z.string().nullable().optional(),
});
export type TraineeAccompanimentVisit = z.infer<typeof traineeAccompanimentVisitSchema>;

export const traineeAccompanimentItemSchema = z.object({
  id: z.string().uuid(),
  trainee_staff_id: z.string().uuid(),
  trainee_staff_name: z.string().nullable().optional(),
  target_type: z.enum(TRAINEE_ACCOMPANIMENT_TARGET_TYPES),
  source: z.enum(TRAINEE_ACCOMPANIMENT_SOURCES),
  course: traineeAccompanimentCourseSchema.nullable().optional(),
  visit: traineeAccompanimentVisitSchema.nullable().optional(),
});
export type TraineeAccompanimentItem = z.infer<typeof traineeAccompanimentItemSchema>;

export const traineeAccompanimentsResponseSchema = z.object({
  items: z.array(traineeAccompanimentItemSchema),
});
export type TraineeAccompanimentsResponse = z.infer<typeof traineeAccompanimentsResponseSchema>;

// ---------------------------------------------------------------------------
// PUT /trainee-accompaniments — request (§6.2, 週単位の一括置換)
// ---------------------------------------------------------------------------

/** 「毎週の既定にする」チェック分 (任意)。省略/null/[] = 既定に一切触れない。 */
export const traineeAccompanimentDefaultInputSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  course_template_id: z.string().uuid(),
});
export type TraineeAccompanimentDefaultInput = z.infer<
  typeof traineeAccompanimentDefaultInputSchema
>;

export const traineeAccompanimentsPutSchema = z.object({
  trainee_staff_id: z.string().uuid(),
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  course_ids: z.array(z.string().uuid()),
  visit_ids: z.array(z.string().uuid()),
  /** 省略/null/[] = 既定に触れない (§6.2 の曖昧性排除)。 */
  defaults: z.array(traineeAccompanimentDefaultInputSchema).nullable().optional(),
});
export type TraineeAccompanimentsPut = z.infer<typeof traineeAccompanimentsPutSchema>;

// ---------------------------------------------------------------------------
// 422 時間重複レスポンス (§6.2)
//   { detail: { message, overlaps: [{ date, a:{...}, b:{...} }] } }
// ---------------------------------------------------------------------------

export const traineeAccompanimentOverlapSideSchema = z.object({
  visit_id: z.string().uuid(),
  patient_name: z.string().nullable().optional(),
  start: z.string().nullable().optional(),
  end: z.string().nullable().optional(),
  course_code: z.string().nullable().optional(),
});
export type TraineeAccompanimentOverlapSide = z.infer<
  typeof traineeAccompanimentOverlapSideSchema
>;

export const traineeAccompanimentOverlapSchema = z.object({
  date: z.string(),
  a: traineeAccompanimentOverlapSideSchema,
  b: traineeAccompanimentOverlapSideSchema,
});
export type TraineeAccompanimentOverlap = z.infer<typeof traineeAccompanimentOverlapSchema>;

export const traineeAccompanimentOverlapDetailSchema = z.object({
  message: z.string().nullable().optional(),
  overlaps: z.array(traineeAccompanimentOverlapSchema),
});
export type TraineeAccompanimentOverlapDetail = z.infer<
  typeof traineeAccompanimentOverlapDetailSchema
>;

/**
 * ApiError.body (422) から重複詳細を安全に取り出す。BE は
 * `{ detail: { message, overlaps: [...] } }` で返す。形が違えば null。
 */
export function parseOverlapDetail(body: unknown): TraineeAccompanimentOverlapDetail | null {
  if (typeof body !== 'object' || body === null) return null;
  const detail = (body as { detail?: unknown }).detail;
  const parsed = traineeAccompanimentOverlapDetailSchema.safeParse(detail);
  return parsed.success ? parsed.data : null;
}

// ---------------------------------------------------------------------------
// GET/PUT /trainee-accompaniment-defaults (§6.3)
// ---------------------------------------------------------------------------

export const traineeAccompanimentDefaultReadSchema = z.object({
  id: z.string().uuid(),
  trainee_staff_id: z.string().uuid(),
  weekday: z.number().int().min(0).max(6),
  course_template_id: z.string().uuid(),
  /** §7.5 サマリ用に BE が解決して載せるテンプレ情報 (任意)。 */
  course_template_label: z.string().nullable().optional(),
  office_id: z.string().uuid().nullable().optional(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});
export type TraineeAccompanimentDefaultRead = z.infer<
  typeof traineeAccompanimentDefaultReadSchema
>;

// ---------------------------------------------------------------------------
// §8-4: 新人の「今週以降のコース担当」ガード (is_trainee ON 警告用)
// ---------------------------------------------------------------------------

export const traineeCourseGuardCourseSchema = z.object({
  id: z.string().uuid(),
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  weekday: z.number().int().min(0).max(6),
  code: z.string(),
});
export type TraineeCourseGuardCourse = z.infer<typeof traineeCourseGuardCourseSchema>;

export const traineeCourseGuardResponseSchema = z.object({
  trainee_staff_id: z.string().uuid(),
  count: z.number().int(),
  courses: z.array(traineeCourseGuardCourseSchema),
});
export type TraineeCourseGuardResponse = z.infer<typeof traineeCourseGuardResponseSchema>;

// ---------------------------------------------------------------------------
// §7.5: is_trainee OFF 時の将来リンク + 既定の一括削除レスポンス
// ---------------------------------------------------------------------------

export const traineeAccompanimentFutureDeleteResponseSchema = z.object({
  trainee_staff_id: z.string().uuid(),
  deleted_links: z.number().int(),
  deleted_defaults: z.number().int(),
});
export type TraineeAccompanimentFutureDeleteResponse = z.infer<
  typeof traineeAccompanimentFutureDeleteResponseSchema
>;

/** PUT /trainee-accompaniment-defaults — 全置換ボディ。 */
export const traineeAccompanimentDefaultsPutSchema = z.object({
  trainee_staff_id: z.string().uuid(),
  items: z.array(traineeAccompanimentDefaultInputSchema),
});
export type TraineeAccompanimentDefaultsPut = z.infer<
  typeof traineeAccompanimentDefaultsPutSchema
>;
