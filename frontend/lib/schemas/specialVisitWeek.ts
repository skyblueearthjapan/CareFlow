/**
 * 特別訪問週間 (Special Visit Week) zod schemas.
 *
 * 設計書: `docs/plans/special-visit-week-design.md` §4 (API 契約) と **1:1**。
 * BE は並行実装中のため、契約は設計書を正として型を起こす。
 *
 *   POST   /api/v1/special-visit-periods                    → PeriodRead
 *   GET    /api/v1/special-visit-periods?patient_id=…       → PeriodRead[]
 *   PATCH  /api/v1/special-visit-periods/{id}               → PeriodRead
 *   GET    /api/v1/special-visit-periods/{id}/calendar      → CalendarRead
 *   POST   /api/v1/special-visit-periods/{id}/marks         → MarkRead
 *   DELETE /api/v1/special-visit-marks/{id}                 → 204
 *   POST   /api/v1/special-visit-periods/{id}/displace      → MarkRead
 *   POST   /api/v1/special-visit-marks/{id}/restore         → 200
 *   GET    /api/v1/special-visit-marks/pool?iso_year&iso_week&office_id?
 *                                                           → PoolTicketRead[]
 *   POST   /api/v1/special-visit-marks/{id}/place           → PlaceResponse
 *
 * 寛容パース方針 (`v2/improvementSuggestion.ts` の流儀を踏襲):
 *   - 「無くても画面が死なない」補助情報 (note / staff_name / preferred / lat,lng など) は
 *     `.default()` / `.nullable()` を付け、旧 BE・部分実装でも壊れないようにする。
 *   - 値域が将来増え得るもの (status / kind / sex 系) は `.catch()` で既定値に落として
 *     カレンダー全体が落ちないようにする (未知値は「その他」相当として描画される)。
 *   - 配列は `.default([])` / `.catch([])`。
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// 値域 (enum)
// ---------------------------------------------------------------------------

/** 期間ステータス. 'active' は同一患者で同時 1 本のみ (BE がアプリ層で担保・422). */
export const SPECIAL_PERIOD_STATUSES = ['active', 'ended', 'cancelled'] as const;
export const specialPeriodStatusSchema = z.enum(SPECIAL_PERIOD_STATUSES);
export type SpecialPeriodStatus = z.infer<typeof specialPeriodStatusSchema>;

/** マーク種別. 'extra' = ○追加枠 / 'displaced' = 固定訪問の日単位退避. */
export const SPECIAL_MARK_KINDS = ['extra', 'displaced'] as const;
export const specialMarkKindSchema = z.enum(SPECIAL_MARK_KINDS);
export type SpecialMarkKind = z.infer<typeof specialMarkKindSchema>;

/** マークステータス. 'pool' = 未配置 / 'placed' = 配置済み / 'cancelled' = 取消済み. */
export const SPECIAL_MARK_STATUSES = ['pool', 'placed', 'cancelled'] as const;
export const specialMarkStatusSchema = z.enum(SPECIAL_MARK_STATUSES);
export type SpecialMarkStatus = z.infer<typeof specialMarkStatusSchema>;

/** 曜日 0=月..5=土 (日曜は対象外). */
export const specialWeekdaySchema = z.number().int().min(0).max(5);

const dateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, '日付は YYYY-MM-DD 形式で入力してください');

/**
 * BE が `str | None` で返すラベル系を、描画側で扱いやすい空文字に正規化する.
 * (`.default('')` は undefined にしか効かず null で parse が落ちるため transform を使う)
 */
const labelSchema = z
  .string()
  .nullable()
  .optional()
  .transform((v) => v ?? '');

// ---------------------------------------------------------------------------
// PeriodRead
// ---------------------------------------------------------------------------

/** 特別訪問週間の期間 1 本 (`special_visit_periods`). 設計書 §1 / §4. */
export const specialVisitPeriodSchema = z.object({
  id: z.string(),
  patient_id: z.string(),
  start_date: z.string(),
  /** 含む (inclusive). */
  end_date: z.string(),
  /** 「週 N 回以上」。期間で一律・週別調整なし. */
  weekly_target: z.number().int().min(1).catch(5),
  note: z.string().nullable().default(null),
  status: specialPeriodStatusSchema.catch('active'),
  created_at: z.string().nullable().default(null),
  updated_at: z.string().nullable().default(null),
});
export type SpecialVisitPeriod = z.infer<typeof specialVisitPeriodSchema>;

/** GET /special-visit-periods のレスポンス (list). */
export const specialVisitPeriodListSchema = z.array(specialVisitPeriodSchema).catch([]);

// ---------------------------------------------------------------------------
// MarkRead
// ---------------------------------------------------------------------------

/** 配置済みマークの配置先サマリ (●の小書きに使う). */
export const specialMarkPlacedSummarySchema = z.object({
  start_time: z.string(),
  course_label: labelSchema,
});
export type SpecialMarkPlacedSummary = z.infer<typeof specialMarkPlacedSummarySchema>;

/** `special_visit_marks` 1 行. 設計書 §4 の `MarkRead` と 1:1. */
export const specialVisitMarkSchema = z.object({
  id: z.string(),
  period_id: z.string(),
  patient_id: z.string(),
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  weekday: z.number().int(),
  kind: specialMarkKindSchema.catch('extra'),
  status: specialMarkStatusSchema.catch('pool'),
  placed_visit_id: z.string().nullable().default(null),
  placed_summary: specialMarkPlacedSummarySchema.nullable().default(null).catch(null),
});
export type SpecialVisitMark = z.infer<typeof specialVisitMarkSchema>;

// ---------------------------------------------------------------------------
// CalendarRead
// ---------------------------------------------------------------------------

/**
 * カレンダーセルに描く固定訪問 1 件。
 * 未生成週は PFV 投影なので `visit_id=null` / `generated=false` で返る。
 */
export const specialCalendarFixedVisitSchema = z.object({
  visit_id: z.string().nullable().default(null),
  start_time: z.string(),
  end_time: z.string().nullable().default(null),
  course_label: labelSchema,
  staff_name: z.string().nullable().default(null),
  generated: z.boolean().default(false),
});
export type SpecialCalendarFixedVisit = z.infer<typeof specialCalendarFixedVisitSchema>;

/** 希望訪問カレンダー由来の希望時間帯 (セル背景の薄敷きに使う). */
export const specialCalendarPreferredSchema = z.object({
  start: z.string(),
  end: z.string(),
});
export type SpecialCalendarPreferred = z.infer<typeof specialCalendarPreferredSchema>;

/** カレンダーの 1 セル (週 × 曜日). */
export const specialCalendarDaySchema = z.object({
  weekday: z.number().int(),
  date: z.string(),
  fixed_visits: z.array(specialCalendarFixedVisitSchema).catch([]),
  extra_mark: specialVisitMarkSchema.nullable().default(null).catch(null),
  displaced_mark: specialVisitMarkSchema.nullable().default(null).catch(null),
  preferred: z.array(specialCalendarPreferredSchema).catch([]),
});
export type SpecialCalendarDay = z.infer<typeof specialCalendarDaySchema>;

/** カレンダーの 1 行 (= 1 ISO 週). `total` / `target_met` は設計書 §3 の週合計. */
export const specialCalendarWeekSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  week_monday: z.string(),
  days: z.array(specialCalendarDaySchema).catch([]),
  total: z.number().int().catch(0),
  target_met: z.boolean().catch(false),
});
export type SpecialCalendarWeek = z.infer<typeof specialCalendarWeekSchema>;

/** GET /special-visit-periods/{id}/calendar レスポンス. */
export const specialVisitCalendarSchema = z.object({
  period: specialVisitPeriodSchema,
  weeks: z.array(specialCalendarWeekSchema).catch([]),
});
export type SpecialVisitCalendar = z.infer<typeof specialVisitCalendarSchema>;

// ---------------------------------------------------------------------------
// PoolTicketRead (§6-2 プール統合 — 別担当が import する)
// ---------------------------------------------------------------------------

/** プールチケットに載る患者サマリ. 性別カード UI / 距離計算に使う最小項目. */
export const specialPoolPatientSchema = z.object({
  id: z.string(),
  name: z.string().default(''),
  code: z.string().nullable().default(null),
  sex: z.string().nullable().default(null).catch(null),
  sex_restriction: z.string().nullable().default(null).catch(null),
  requires_multiple_staff: z.boolean().default(false),
  lat: z.number().nullable().default(null),
  lng: z.number().nullable().default(null),
  primary_office_id: z.string().nullable().default(null),
});
export type SpecialPoolPatient = z.infer<typeof specialPoolPatientSchema>;

/** プールチケットに載る期間サマリ (残り日数 / 目標の表示用). */
export const specialPoolPeriodSchema = z.object({
  id: z.string(),
  weekly_target: z.number().int().catch(5),
  end_date: z.string(),
});
export type SpecialPoolPeriod = z.infer<typeof specialPoolPeriodSchema>;

/**
 * 同一期間内の直近 placed マークの配置先 (参考ヒント・強制しない)。
 * 候補リストの先頭に「前回はここでした」カードとして出す。
 */
export const specialLastPlacementSchema = z.object({
  weekday: z.number().int(),
  start_time: z.string(),
  course_label: labelSchema,
  staff_name: z.string().nullable().default(null),
});
export type SpecialLastPlacement = z.infer<typeof specialLastPlacementSchema>;

/** GET /special-visit-marks/pool の 1 件. */
export const specialPoolTicketSchema = z.object({
  mark: specialVisitMarkSchema,
  patient: specialPoolPatientSchema,
  period: specialPoolPeriodSchema,
  last_placement: specialLastPlacementSchema.nullable().default(null).catch(null),
  // BE の正典所要分 (PFV優先・place と同一計算)。提案と実配置の枠長を一致させる。
  // 旧BE未送出 → null (FE はフォールバック計算を使う)。
  service_minutes: z.number().int().nullable().default(null).catch(null),
});
export type SpecialPoolTicket = z.infer<typeof specialPoolTicketSchema>;

/**
 * GET /special-visit-marks/pool レスポンス (list)。
 * 1 件パース不能でもプールセクション全体を落とさないよう、要素ごとに間引く。
 */
export const specialPoolTicketListSchema = z.preprocess((val) => {
  if (!Array.isArray(val)) return val;
  return val.filter((el) => specialPoolTicketSchema.safeParse(el).success);
}, z.array(specialPoolTicketSchema));

// ---------------------------------------------------------------------------
// リクエスト
// ---------------------------------------------------------------------------

/** POST /special-visit-periods. active 重複は BE が 422 を返す. */
export const specialVisitPeriodCreateSchema = z
  .object({
    patient_id: z.string().min(1),
    start_date: dateSchema,
    end_date: dateSchema,
    // BE は 1〜7 (週の日数上限) に制限している。
    weekly_target: z
      .number()
      .int()
      .min(1, '目標回数は 1 以上で入力してください')
      .max(7, '目標回数は 7 以下で入力してください'),
    note: z.string().max(500).nullable().optional(),
  })
  .refine((v) => v.start_date <= v.end_date, {
    message: '開始日は終了日より前にしてください',
    path: ['end_date'],
  });
export type SpecialVisitPeriodCreate = z.input<typeof specialVisitPeriodCreateSchema>;

/** PATCH /special-visit-periods/{id}. 目標変更 / 延長 / 終了 の 3 用途. */
export const specialVisitPeriodUpdateSchema = z.object({
  end_date: dateSchema.optional(),
  weekly_target: z.number().int().min(1).max(7).optional(),
  note: z.string().max(500).nullable().optional(),
  status: specialPeriodStatusSchema.optional(),
});
export type SpecialVisitPeriodUpdate = z.input<typeof specialVisitPeriodUpdateSchema>;

/** POST /special-visit-periods/{id}/marks (kind='extra'). 既に extra ありは 409. */
export const specialVisitMarkCreateSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  weekday: specialWeekdaySchema,
});
export type SpecialVisitMarkCreate = z.input<typeof specialVisitMarkCreateSchema>;

/** POST /special-visit-periods/{id}/displace (固定訪問の日単位退避). marks と同形. */
export const specialVisitDisplaceSchema = specialVisitMarkCreateSchema;
export type SpecialVisitDisplace = z.input<typeof specialVisitDisplaceSchema>;

/**
 * POST /special-visit-marks/{id}/place — その週の該当コース実体へ planned 訪問を作る.
 *
 * コースの指定は 2 通り (設計書 §4 / BE `PlaceRequest`):
 *   - `course_id` 直指定 (カレンダー等、コース実体を既に持っている呼出元)
 *   - `(office_id + course_code)` — propose-slots の候補は `course_id` を持たないため、
 *     こちらで指定する。週・曜日は mark 側が正なので BE が当該週のコース実体を解決する。
 * どちらも欠けている payload は BE (422) の前に FE で弾く。
 */
export const specialVisitPlaceSchema = z
  .object({
    course_id: z.string().min(1).optional(),
    office_id: z.string().min(1).optional(),
    course_code: z.string().min(1).optional(),
    /** "HH:MM". */
    start_time: z.string().regex(/^\d{2}:\d{2}$/, '時刻は HH:MM 形式で入力してください'),
    /**
     * NG スタッフ / 性別制限 (patient-ng-staff-design.md §7-2): 422
     * `constraint_confirmation_required` を確認ダイアログで通したときだけ true で
     * 再送する。省略時 BE default=false。
     */
    acknowledge_constraint_warnings: z.boolean().optional(),
  })
  .refine((v) => v.course_id != null || (v.office_id != null && v.course_code != null), {
    message: 'コースは course_id か (office_id + course_code) で指定してください',
    path: ['course_id'],
  });
export type SpecialVisitPlace = z.input<typeof specialVisitPlaceSchema>;

/** POST /special-visit-marks/{id}/place レスポンス. */
export const specialVisitPlaceResponseSchema = z.object({
  mark: specialVisitMarkSchema,
  visit_id: z.string().nullable().default(null),
});
export type SpecialVisitPlaceResponse = z.infer<typeof specialVisitPlaceResponseSchema>;
