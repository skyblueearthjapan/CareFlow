/**
 * VisitV2 zod schemas (Wave 0-C).
 *
 * 設計仕様書 v0.9 §3.3 / §4.5 に基づく訪問インスタンス v2。
 *
 * v1 → v2 の追加:
 *   - `course_id` FK NULL (コースに属する訪問)
 *   - `required_staff_count` (1 or 2) — §3.3 2 名体制対応
 *   - `visit_group_id` UUID NULL — 2 名体制の訪問をグルーピング
 *
 * v1 の `primary_staff_id` / `secondary_staff_id` は v2 でも残す
 * (visit_staff_assignments テーブルとの併用、移行期間の互換のため)。
 * 2 名体制の表現は **visit_staff_assignments (visit × staff M2M)** で行うのが正規。
 *
 * Backend `backend/app/schemas/v2/visit.py` の Pydantic schema と
 * **完全一致** させる。
 */
import { z } from 'zod';

/** 訪問種別 (v1 と同じ). */
export const VISIT_TYPE_V2_VALUES = ['regular', 'spot', 'training', 'other'] as const;
export const visitTypeV2Enum = z.enum(VISIT_TYPE_V2_VALUES);
export type VisitTypeV2 = z.infer<typeof visitTypeV2Enum>;

/**
 * 訪問状態:
 *   planned (予定) / completed (完了) / cancelled (キャンセル) / no_show (不在)
 */
export const VISIT_STATUS_V2_VALUES = ['planned', 'completed', 'cancelled', 'no_show'] as const;
export const visitStatusV2Enum = z.enum(VISIT_STATUS_V2_VALUES);
export type VisitStatusV2 = z.infer<typeof visitStatusV2Enum>;

/** 入力チャネル (v1 と同じ). */
export const VISIT_SOURCE_V2_VALUES = ['manual', 'auto', 'import', 'ai'] as const;
export const visitSourceV2Enum = z.enum(VISIT_SOURCE_V2_VALUES);
export type VisitSourceV2 = z.infer<typeof visitSourceV2Enum>;

// ─────────────────────────────────────────────────────────────────────────
// Helpers — date / time as ISO-ish strings (server returns them as strings)
// ─────────────────────────────────────────────────────────────────────────
const isoDateSchema = z.string(); // YYYY-MM-DD or full ISO; backend serializes date as string
const timeSchema = z.string(); // HH:MM[:SS] / backend Time

/** 訪問インスタンス v2 (§3.3 / §4.5). */
export const visitV2BaseSchema = z.object({
  patient_id: z.string().uuid(),
  visit_date: isoDateSchema,
  start_time: timeSchema,
  end_time: timeSchema,
  type: visitTypeV2Enum.default('regular'),
  status: visitStatusV2Enum.default('planned'),
  source: visitSourceV2Enum.default('manual'),
  note: z.string().nullable().optional(),
  kaipoke_id: z.string().max(64).nullable().optional(),

  // v2 追加 (§3.3 / §4.5)
  course_id: z.string().uuid().nullable().optional(),
  /** 必要スタッフ数 (§3.3). 1 = 通常, 2 = 2 名体制 */
  required_staff_count: z.number().int().min(1).max(2).default(1),
  /**
   * 2 名体制の訪問をグルーピングする論理キー (§3.3).
   * required_staff_count=2 のとき、同じ visit_group_id を持つ visit が 2 行存在する。
   */
  visit_group_id: z.string().uuid().nullable().optional(),

  // 移行期互換のため v1 の primary/secondary は残す (visit_staff_assignments と併用)
  primary_staff_id: z.string().uuid().nullable().optional(),
  secondary_staff_id: z.string().uuid().nullable().optional(),
  mentor_staff_id: z.string().uuid().nullable().optional(),
});
export type VisitV2Base = z.infer<typeof visitV2BaseSchema>;

/** POST /api/v1/visits リクエスト (W2-BE4). */
export const visitV2CreateSchema = visitV2BaseSchema;
export type VisitV2Create = z.infer<typeof visitV2CreateSchema>;

/** PATCH /api/v1/visits/{id} リクエスト (W2-BE4). */
export const visitV2UpdateSchema = z.object({
  patient_id: z.string().uuid().optional(),
  visit_date: isoDateSchema.optional(),
  start_time: timeSchema.optional(),
  end_time: timeSchema.optional(),
  type: visitTypeV2Enum.optional(),
  status: visitStatusV2Enum.optional(),
  source: visitSourceV2Enum.optional(),
  note: z.string().nullable().optional(),
  kaipoke_id: z.string().max(64).nullable().optional(),
  course_id: z.string().uuid().nullable().optional(),
  required_staff_count: z.number().int().min(1).max(2).optional(),
  visit_group_id: z.string().uuid().nullable().optional(),
  primary_staff_id: z.string().uuid().nullable().optional(),
  secondary_staff_id: z.string().uuid().nullable().optional(),
  mentor_staff_id: z.string().uuid().nullable().optional(),
});
export type VisitV2Update = z.infer<typeof visitV2UpdateSchema>;

/**
 * visit_staff_assignments テーブルの 1 行 (§4.5).
 * 1 visit に対して 1 or 2 行 (`required_staff_count` に応じて)。
 */
export const visitStaffAssignmentV2ReadSchema = z.object({
  visit_id: z.string().uuid(),
  staff_id: z.string().uuid(),
  assigned_at: z.string(),
});
export type VisitStaffAssignmentV2Read = z.infer<typeof visitStaffAssignmentV2ReadSchema>;

/** GET /api/v1/visits/{id} レスポンス (W2-BE4). */
export const visitV2ReadSchema = visitV2BaseSchema.extend({
  id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
  // 表示用の denormalized 名 (v1 と同じ)
  patient_name: z.string().nullable().optional(),
  staff_name: z.string().nullable().optional(),
  // visit_staff_assignments 経由で割り当てられたスタッフ全員 (§4.5)
  staff_assignments: z.array(visitStaffAssignmentV2ReadSchema).default([]),
});
export type VisitV2Read = z.infer<typeof visitV2ReadSchema>;
