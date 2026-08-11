/**
 * place-and-fix v2 zod schemas (Wave 15 Phase 3 / W15-FE / W37 Phase 2-A / 3-A).
 *
 * BE 側 POST /api/v1/schedule/place-and-fix のリクエスト/レスポンス契約を
 * Zod でミラー。BE 実装仕様 (フラット単一オブジェクト) に整合済み。
 *
 * W37 Phase 2-A:
 *   - staff_count: 1 | 2
 *   - course_template_ids: UUID[] (1 or 2 件、staff_count と一致)
 *   - course_template_id (旧, staff_count=1 のみ) は後方互換のため残す
 *   - レスポンスに visits[] / fixed_visits[] / visit_group_id を追加
 *
 * バリデーション (FE 側; BE と整合):
 *   - 旧形式 + 新形式の同時指定 → エラー
 *   - どちらも未指定 → エラー
 *   - 件数 != staff_count → エラー
 *   - staff_count=2 で同一 UUID → エラー
 *   - 旧形式 (course_template_id) + staff_count=2 → エラー
 */
import { z } from 'zod';

import { visitV2ReadSchema } from './visit';
import { patientFixedVisitV2ReadSchema } from './patient_fixed_visit';

// ---------------------------------------------------------------------------
// Request schema — フラット単一オブジェクト (BE 仕様)
// ---------------------------------------------------------------------------

/**
 * W37 Phase 2-A: course_template_id (旧) と course_template_ids (新) を両対応。
 * BE 側のバリデーション (5 項目) を `superRefine` でミラーする。
 */
export const placeAndFixRequestSchema = z
  .object({
    patient_id: z.string().uuid(),
    /**
     * W15-codex-fix (1) / W37 旧形式: 単一の course_template_id (UUID).
     * `staff_count=1` のときのみ有効 (後方互換)。
     * `staff_count=2` のときは `course_template_ids` を使うこと。
     */
    course_template_id: z.string().uuid().nullable().optional(),
    /**
     * W37 Phase 2-A 新形式: 1 or 2 件の course_template_ids (UUID 配列).
     * `staff_count=2` のときに 2 件指定すると visit_group_id 共有で 2 visit を作成する。
     * 件数は staff_count と一致しなければならない。
     */
    course_template_ids: z.array(z.string().uuid()).min(1).max(2).nullable().optional(),
    iso_year: z.number().int(),
    iso_week: z.number().int().min(1).max(53),
    weekday: z.number().int().min(0).max(6),
    start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/),
    duration_min: z.number().int().min(1).max(480),
    staff_count: z.union([z.literal(1), z.literal(2)]).default(1),
    fix_pattern: z.boolean().default(true),
    // 方式b: 定員超過採用時の管理者判断理由 (最大500字)。通常候補では null/省略。旧BE互換。
    capacity_override_reason: z.string().max(500).nullish(),
    /**
     * Wave U-3: 1 ユーザー操作 = 1 UUID。同一操作の複数 API コール（place-and-fix +
     * visit DELETE 等）に同値を送ることで BE 側で op-log をグループ化する。省略可 (旧 BE 互換)。
     */
    op_group_id: z.string().uuid().nullish(),
    /**
     * NG スタッフ / 性別制限 (§7-2): 422 `constraint_confirmation_required` を
     * 確認ダイアログで通したときだけ true で再送する。省略時 BE default=false。
     */
    acknowledge_constraint_warnings: z.boolean().optional(),
  })
  .superRefine((data, ctx) => {
    const hasSingle = data.course_template_id !== null && data.course_template_id !== undefined;
    const hasList = data.course_template_ids !== null && data.course_template_ids !== undefined;

    // (1) 両方指定はエラー
    if (hasSingle && hasList) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['course_template_ids'],
        message: 'course_template_id と course_template_ids は同時に指定できません',
      });
      return;
    }
    // (2) どちらも未指定はエラー
    if (!hasSingle && !hasList) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['course_template_ids'],
        message: 'course_template_id または course_template_ids のいずれかを指定してください',
      });
      return;
    }
    // (3) 旧形式 + staff_count=2 は不可
    if (hasSingle && data.staff_count !== 1) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['course_template_id'],
        message:
          'course_template_id (旧形式) は staff_count=1 のみ. staff_count=2 のときは course_template_ids を使ってください',
      });
      return;
    }
    // (4) 新形式: 件数 == staff_count
    if (hasList) {
      const ids = data.course_template_ids as string[];
      if (ids.length !== data.staff_count) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['course_template_ids'],
          message: `course_template_ids の件数 (${ids.length}) が staff_count (${data.staff_count}) と一致しません`,
        });
      }
      // (5) staff_count=2 で同一 UUID は不可
      if (data.staff_count === 2 && new Set(ids).size !== 2) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['course_template_ids'],
          message: 'staff_count=2 では course_template_ids は 2 つの異なる UUID を指定してください',
        });
      }
    }
  });

export type PlaceAndFixRequest = z.infer<typeof placeAndFixRequestSchema>;

// ---------------------------------------------------------------------------
// Response schema — W37 Phase 2-A 拡張 (visits[] / fixed_visits[] / visit_group_id)
// ---------------------------------------------------------------------------

/**
 * W37 Phase 2-A:
 *   - 単数 `visit` / `fixed_visit` は後方互換 (staff_count=1 の旧クライアント向け;
 *     staff_count=2 のときも 1 件目を入れる)
 *   - `visits[]` / `fixed_visits[]` は staff_count に一致する件数 (1 or 2 件)
 *   - `visit_group_id` は staff_count=2 のときのみ付与 (1 名体制では null)
 */
export const placeAndFixResponseSchema = z.object({
  visit: visitV2ReadSchema,
  fixed_visit: patientFixedVisitV2ReadSchema.nullable(),
  /** W37 Phase 2-A: 配列形式 (staff_count に一致する件数). */
  visits: z.array(visitV2ReadSchema).default([]),
  /** W37 Phase 2-A: 配列形式 (staff_count に一致する件数). */
  fixed_visits: z.array(patientFixedVisitV2ReadSchema).default([]),
  /** W37 Phase 2-A: 2 名体制で 2 visit が共有する UUID (1 名体制では null). */
  visit_group_id: z.string().uuid().nullable().default(null),
});

export type PlaceAndFixResponse = z.infer<typeof placeAndFixResponseSchema>;
