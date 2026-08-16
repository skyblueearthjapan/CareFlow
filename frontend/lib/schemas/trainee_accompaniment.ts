/**
 * 同行 (accompaniment) zod schemas.
 *
 * 設計書 `docs/plans/general-accompaniment-design.md` §4 / 旧
 * `docs/plans/trainee-accompaniment-design.md` §6 の API 契約に 1:1 対応。
 * Backend `backend/app/api/v1/accompaniments.py` / `schemas` と一致させる。
 *
 * 概念:
 *   - 任意の active スタッフが他スタッフのコース / 患者訪問に「同行」として付く。
 *     新人 (staff.is_trainee=true) は kind='trainee'、それ以外は kind='support'
 *     (種別は BE が自動判定・FE からは送らない)。
 *   - 紐付けは週単位。target_type='course' (日単位コースインスタンス全体) or
 *     'visit' (患者個別) の 2 種を同一週で混在できる。
 *   - source='default' = 毎週の既定 (テンプレ層) 由来、'manual' = 画面からの手動追加。
 *
 * 互換: リクエスト/レスポンスのフィールド名は `trainee_staff_id` /
 * `trainee_staff_name` のまま (BE 契約が現行維持)。一覧の各行には `kind` と
 * `staff_name` が追加される (旧デプロイでは undefined)。
 */
import { z } from 'zod';

import { WEEKDAY_LABELS } from '@/lib/schemas/staff';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const TRAINEE_ACCOMPANIMENT_TARGET_TYPES = ['course', 'visit'] as const;
export type TraineeAccompanimentTargetType = (typeof TRAINEE_ACCOMPANIMENT_TARGET_TYPES)[number];

export const TRAINEE_ACCOMPANIMENT_SOURCES = ['default', 'manual'] as const;
export type TraineeAccompanimentSource = (typeof TRAINEE_ACCOMPANIMENT_SOURCES)[number];

/** 同行の種別 (BE が staff.is_trainee から自動判定・FE は表示のみ)。 */
export const ACCOMPANIMENT_KINDS = ['trainee', 'support'] as const;
export type AccompanimentKind = (typeof ACCOMPANIMENT_KINDS)[number];

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
  /** 一般化後の氏名フィールド。旧デプロイでは undefined → trainee_staff_name に落とす。 */
  staff_name: z.string().nullable().optional(),
  /** 一般化後の種別。旧デプロイでは undefined → 'trainee' 相当として扱う。 */
  kind: z.enum(ACCOMPANIMENT_KINDS).nullable().optional(),
  target_type: z.enum(TRAINEE_ACCOMPANIMENT_TARGET_TYPES),
  source: z.enum(TRAINEE_ACCOMPANIMENT_SOURCES),
  course: traineeAccompanimentCourseSchema.nullable().optional(),
  visit: traineeAccompanimentVisitSchema.nullable().optional(),
});
export type TraineeAccompanimentItem = z.infer<typeof traineeAccompanimentItemSchema>;

/** 訪問 (VisitRead / MyVisit) に載る同行スタッフ 1 名ぶん。 */
export interface VisitAccompanimentRef {
  staff_id: string;
  staff_name: string | null;
  kind?: AccompanimentKind | null;
}

/**
 * 訪問の同行スタッフを配列で取り出す (確定#5 複数名対応)。
 * `accompaniments[]` を優先し、無ければ旧単数 `accompaniment` に落とす
 * (BE 未デプロイ / 旧レスポンス互換)。
 */
export function visitAccompaniments(visit: {
  accompaniments?: readonly VisitAccompanimentRef[] | null;
  accompaniment?: VisitAccompanimentRef | null;
}): VisitAccompanimentRef[] {
  if (visit.accompaniments && visit.accompaniments.length > 0) {
    return [...visit.accompaniments];
  }
  return visit.accompaniment ? [visit.accompaniment] : [];
}

/** 表示用の同行スタッフ名 (新旧フィールドのフォールバック)。 */
export function accompanimentStaffName(item: TraineeAccompanimentItem): string {
  return item.staff_name ?? item.trainee_staff_name ?? '';
}

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

/**
 * PUT ボディ。同行者のキーは一般化後の `staff_id` (BE は旧 `trainee_staff_id` も
 * 受け付けるが、レガシーキー削除に向けて FE は新キーだけを送る)。
 *
 * TODO(Phase E): フック名 / React Query キー / data-testid に残る "trainee" 呼称
 * (`useTraineeAccompaniments` / `['trainee-accompaniments']` / GET のクエリ引数
 * `trainee_staff_id` など) の全面リネームは、BE の互換エイリアス削除と同時に行う。
 */
export const traineeAccompanimentsPutSchema = z.object({
  staff_id: z.string().uuid(),
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  course_ids: z.array(z.string().uuid()),
  visit_ids: z.array(z.string().uuid()),
  /** 省略/null/[] = 既定に触れない (§6.2 の曖昧性排除)。 */
  defaults: z.array(traineeAccompanimentDefaultInputSchema).nullable().optional(),
  /**
   * NG スタッフ / 性別制限の 422 を確認して通すときだけ true を足す
   * (`patient-ng-staff-design.md` §7-2 の acknowledge 方式・BE が管理者へ通知)。
   */
  acknowledge_constraint_warnings: z.literal(true).optional(),
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
export type TraineeAccompanimentOverlapSide = z.infer<typeof traineeAccompanimentOverlapSideSchema>;

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
// 422 重複 (一般化後・general-accompaniment-design.md 確定#1)
//   { detail: { code: 'accompaniment_overlap', conflicts: [{ date, weekday,
//     start, end, patient_name, course_label, reason }] } }
//
// 「なぜ登録できないか」を必ず言語化する (システム不具合と誤解させない)。
// ---------------------------------------------------------------------------

/** own_duty = 同行者自身の担当と重なる / accompaniment = 別の同行と重なる。 */
export const ACCOMPANIMENT_CONFLICT_REASONS = ['own_duty', 'accompaniment'] as const;
export type AccompanimentConflictReason = (typeof ACCOMPANIMENT_CONFLICT_REASONS)[number];

export const accompanimentConflictSchema = z.object({
  date: z.string(),
  weekday: z.number().int().min(0).max(6).nullable().optional(),
  start: z.string().nullable().optional(),
  end: z.string().nullable().optional(),
  patient_name: z.string().nullable().optional(),
  course_label: z.string().nullable().optional(),
  /** 未知の理由でも落とさず素通しし、文言は既定 (重複) に落とす。 */
  reason: z.string().nullable().optional(),
});
export type AccompanimentConflict = z.infer<typeof accompanimentConflictSchema>;

export const ACCOMPANIMENT_OVERLAP_CODE = 'accompaniment_overlap';

export const accompanimentConflictDetailSchema = z.object({
  code: z.literal(ACCOMPANIMENT_OVERLAP_CODE),
  message: z.string().nullable().optional(),
  conflicts: z.array(accompanimentConflictSchema),
});
export type AccompanimentConflictDetail = z.infer<typeof accompanimentConflictDetailSchema>;

/** ApiError.body (422) から一般化後の重複詳細を取り出す。形が違えば null。 */
export function parseAccompanimentConflictDetail(
  body: unknown,
): AccompanimentConflictDetail | null {
  if (typeof body !== 'object' || body === null) return null;
  const detail = (body as { detail?: unknown }).detail;
  const parsed = accompanimentConflictDetailSchema.safeParse(detail);
  return parsed.success ? parsed.data : null;
}

/** 'HH:MM:SS' / 'HH:MM' → 'HH:MM'。空は null。 */
function hhmm(value: string | null | undefined): string | null {
  if (!value) return null;
  return value.length >= 5 ? value.slice(0, 5) : value;
}

/** '2026-08-18' (+ weekday) → '8月18日(火)'。曜日が無ければ日付から導く。 */
export function formatConflictDate(date: string, weekday: number | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return date;
  const [, y, mo, d] = m;
  const wd =
    typeof weekday === 'number' && weekday >= 0 && weekday <= 6
      ? weekday
      : // JS の getDay() は 0=日 なので 0=月 の索引へ寄せる。
        (new Date(Number(y), Number(mo) - 1, Number(d)).getDay() + 6) % 7;
  return `${Number(mo)}月${Number(d)}日(${WEEKDAY_LABELS[wd] ?? ''})`;
}

/**
 * 1 件の重複を会話的な日本語にする (確定#1「拒否理由を明確に表示」)。
 *
 *   own_duty      : 8月18日(火) 10:00〜10:35 は 山田 太郎様（稲毛A・ご自身の担当）と
 *                   重なるため登録できません
 *   accompaniment : …は 山田 太郎様（稲毛A）の別の同行と重なるため登録できません
 */
export function formatAccompanimentConflict(c: AccompanimentConflict): string {
  const when = formatConflictDate(c.date, c.weekday);
  const start = hhmm(c.start);
  const end = hhmm(c.end);
  const span = start ? (end ? `${start}〜${end}` : start) : '';
  const patient = c.patient_name ?? '対象の患者';
  const course = c.course_label ?? null;
  if (c.reason === 'own_duty') {
    const paren = course ? `（${course}・ご自身の担当）` : '（ご自身の担当）';
    return `${when} ${span} は ${patient}様${paren}と重なるため登録できません`;
  }
  if (c.reason === 'accompaniment') {
    const paren = course ? `（${course}）` : '';
    return `${when} ${span} は ${patient}様${paren}の別の同行と重なるため登録できません`;
  }
  const paren = course ? `（${course}）` : '';
  return `${when} ${span} は ${patient}様${paren}と重なるため登録できません`;
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
export type TraineeAccompanimentDefaultRead = z.infer<typeof traineeAccompanimentDefaultReadSchema>;

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
  /**
   * 一般化後: このガードが適用対象か (BE が kind などから判定して載せる)。
   * 旧デプロイでは undefined。**表示分岐は従来どおり `count` を見る** —
   * BE 側で kind 非依存へ戻す修正が入るまで applicable は受理のみ (§4)。
   */
  applicable: z.boolean().optional(),
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

/** PUT /accompaniment-defaults — 全置換ボディ (キーは一般化後の `staff_id`)。 */
export const traineeAccompanimentDefaultsPutSchema = z.object({
  staff_id: z.string().uuid(),
  items: z.array(traineeAccompanimentDefaultInputSchema),
});
export type TraineeAccompanimentDefaultsPut = z.infer<typeof traineeAccompanimentDefaultsPutSchema>;
