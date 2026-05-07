/**
 * place-and-fix v2 zod schemas (Wave 15 Phase 3 / W15-FE).
 *
 * BE 側 (Phase 2 C-1 で並行実装中) の POST /api/v1/schedule/place-and-fix
 * リクエスト/レスポンス契約を Zod で先行ミラー。BE 完了後に差分が出たら
 * 追従修正する想定。
 *
 * 仕様 (要件):
 *   - プールの患者 → セル (course_template_id × weekday × HH:MM) にドロップ
 *   - 1 トランザクションで「Visit 新規作成 + 固定枠化 (PatientFixedVisit upsert)」
 *
 * リクエストは可変長 list で同時に複数の patient を投入可能。
 * MVP として本クライアントは 1 件投入する形で利用する。
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Request schemas
// ---------------------------------------------------------------------------

const timeString = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/);

export const placeAndFixEntrySchema = z.object({
  patient_id: z.string().uuid(),
  course_template_id: z.string().uuid(),
  weekday: z.number().int().min(0).max(6),
  start_time: timeString,
  /** デフォルト 60 分. BE 側で patient.weekly_pattern.service_minutes を採用する想定. */
  service_minutes: z.number().int().min(15).max(480).default(60),
  staff_count: z.union([z.literal(1), z.literal(2)]).default(1),
});

export type PlaceAndFixEntry = z.infer<typeof placeAndFixEntrySchema>;

export const placeAndFixRequestSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  entries: z.array(placeAndFixEntrySchema).min(1).max(50),
});

export type PlaceAndFixRequest = z.infer<typeof placeAndFixRequestSchema>;

// ---------------------------------------------------------------------------
// Response schemas
// ---------------------------------------------------------------------------

export const placeAndFixResultSchema = z.object({
  patient_id: z.string().uuid(),
  visit_id: z.string().uuid().nullable().optional(),
  patient_fixed_visit_id: z.string().uuid().nullable().optional(),
  course_id: z.string().uuid().nullable().optional(),
});

export type PlaceAndFixResult = z.infer<typeof placeAndFixResultSchema>;

export const placeAndFixResponseSchema = z.object({
  results: z.array(placeAndFixResultSchema).default([]),
});

export type PlaceAndFixResponse = z.infer<typeof placeAndFixResponseSchema>;
