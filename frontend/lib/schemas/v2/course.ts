/**
 * CourseV2 zod schemas (Wave 0-C).
 *
 * 設計仕様書 v0.9 §4.5 に基づくコース (Course) マスタ。
 * Layer 2 (§3.6.3 / §5.3) で生成される患者グループ。
 *
 * 識別子: (iso_year, iso_week, weekday, code) UNIQUE
 *
 * 状態遷移 (`course_status` enum):
 *   proposed → course_fixed → staff_assigned
 *
 * タイムスタンプ補助カラム: course_fixed_at, staff_assigned_at
 *
 * Backend `backend/app/schemas/v2/course.py` の Pydantic schema と
 * **完全一致** させる。
 */
import { z } from 'zod';
import { courseStatusEnum } from './enums';

/**
 * コース記号 (§3.6.5):
 *   A〜D = 通常コース (4 つ、稼働スタッフ数に応じる)
 *   M = マネージャー枠 (4 コース外のオーバーフロー専用)
 */
export const COURSE_CODE_V2_VALUES = ['A', 'B', 'C', 'D', 'M'] as const;
export const courseCodeV2Enum = z.enum(COURSE_CODE_V2_VALUES);
export type CourseCodeV2 = z.infer<typeof courseCodeV2Enum>;

/**
 * コース v2 (§4.5).
 *
 * 対象期間: 1 週間 × 1 曜日。1 コースあたり最大 6 名の患者。
 */
export const courseV2BaseSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  /** 曜日 (0=月, 6=日) */
  weekday: z.number().int().min(0).max(6),
  code: courseCodeV2Enum,
  course_status: courseStatusEnum.default('proposed'),
  /** Layer 3 で決定する担当スタッフ (§3.6.4) */
  assigned_staff_id: z.string().uuid().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type CourseV2Base = z.infer<typeof courseV2BaseSchema>;

/** POST /api/v1/courses リクエスト (W2-BE4). */
export const courseV2CreateSchema = courseV2BaseSchema;
export type CourseV2Create = z.infer<typeof courseV2CreateSchema>;

/** PATCH /api/v1/courses/{id} リクエスト (W2-BE4). */
export const courseV2UpdateSchema = z.object({
  course_status: courseStatusEnum.optional(),
  assigned_staff_id: z.string().uuid().nullable().optional(),
  note: z.string().nullable().optional(),
  course_fixed_at: z.string().nullable().optional(),
  staff_assigned_at: z.string().nullable().optional(),
});
export type CourseV2Update = z.infer<typeof courseV2UpdateSchema>;

/** GET /api/v1/courses/{id} レスポンス (W2-BE4). */
export const courseV2ReadSchema = courseV2BaseSchema.extend({
  id: z.string().uuid(),
  /** 構成固定日時 (§4.5) */
  course_fixed_at: z.string().nullable().optional(),
  /** スタッフ割当日時 (§4.5) */
  staff_assigned_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
});
export type CourseV2Read = z.infer<typeof courseV2ReadSchema>;
