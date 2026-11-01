/**
 * 受け入れ枠「月で見る」— 応答の形と画面の小さな計算・文言。
 *
 * サーバー (GET /api/v1/acceptance-matrix/month) が曜日 × 30 分のマスを集計して返す
 * (backend/app/services/scheduling/acceptance_month.py)。担い手・休みの扱い・昼休みを
 * 変えたら問い合わせ直す。試算: docs/reports/agent/2026-11-受け入れ枠の月の埋まり.html。
 */
import { z } from 'zod';

import type { Mark } from '@/lib/acceptanceStrict';

const ratio = z.number().nullable();

const monthCellSchema = z.object({
  weekday: z.number(),
  slot: z.string(),
  mark: z.enum(['○', '△', '×', '－']),
  ok: z.number(),
  counted: z.number(),
  total: z.number(),
  per_week: z.array(z.number()),
  free_avg: ratio,
  work_min: z.number(),
  busy_min: z.number(),
  utilization: ratio,
  crowded: z.boolean(),
  starts: z.number(),
  starts_avg: ratio,
  busy_staff_avg: ratio,
  pairs: z.number(),
  tight_ratio: ratio,
  fixed_ratio: ratio,
  prev_utilization: ratio.optional(),
  trend_points: ratio.optional(),
  trend: z.enum(['▲', '▼']).nullable().optional(),
});

const peakSchema = z.object({ weekday: z.number(), slot: z.string(), value: z.number() });
const weekdaySummarySchema = z.object({
  weekday: z.number(),
  days: z.number(),
  total: z.number(),
  visits_per_day: ratio,
  utilization: ratio,
});

export const monthPayloadSchema = z.object({
  month: z.string(),
  prev_month: z.string(),
  office_id: z.string().nullable(),
  offices: z.array(z.object({ id: z.string(), name: z.string() })),
  today: z.string(),
  source: z.enum(['plan', 'record', 'mixed']),
  mode: z.enum(['real', 'ignore']),
  lunch: z.boolean(),
  weekdays: z.array(z.number()),
  slots: z.array(z.string()),
  rules: z.object({
    visit_min: z.number(),
    cap: z.number(),
    buffer_min: z.number(),
    travel_guess_min: z.number(),
    lunch_min: z.number(),
    lunch_from: z.string(),
    lunch_to: z.string(),
    lunch_considered: z.boolean(),
    slot_min: z.number(),
    crowded_utilization: z.number(),
    trend_points: z.number(),
    busy_day_visits: z.number(),
    open_day_min_visits: z.number(),
  }),
  staff: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      kind: z.enum(['staff', 'manager', 'trainee']),
      default_selected: z.boolean(),
      office_id: z.string().nullable().optional(),
    }),
  ),
  selected_staff_ids: z.array(z.string()),
  days: z.array(z.object({ date: z.string(), open: z.boolean() })),
  visit_count: z.number(),
  prev_utilization: ratio,
  utilization: ratio,
  cells: z.record(z.string(), monthCellSchema),
  weekday_summary: z.array(weekdaySummarySchema),
  /** 事業所全体 (マネージャー・新人を含む全員) の曜日の要約。 */
  weekday_summary_all: z.array(weekdaySummarySchema).default([]),
  peaks: z.object({ utilization: z.array(peakSchema), starts: z.array(peakSchema) }),
  counts: z.object({
    ok: z.number(),
    mid: z.number(),
    ng: z.number(),
    none: z.number(),
    crowded: z.number(),
  }),
  html: z.string().optional(),
});

export type MonthPayload = z.infer<typeof monthPayloadSchema>;
export type MonthCell = MonthPayload['cells'][string];

export const monthCellKey = (weekday: number, slot: string): string => `${weekday}-${slot}`;

/** ``YYYY-MM`` を ``delta`` か月ずらす。 */
export function shiftMonth(month: string, delta: number): string {
  const [y = 0, m = 1] = month.split('-').map(Number);
  const idx = y * 12 + (m - 1) + delta;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, '0')}`;
}

/** 今の月 (JST)。 */
export function currentMonthJst(now: Date = new Date()): string {
  const jst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  return `${jst.getUTCFullYear()}-${String(jst.getUTCMonth() + 1).padStart(2, '0')}`;
}

export function monthLabel(month: string): string {
  const [y = 0, m = 1] = month.split('-').map(Number);
  return `${y} 年 ${m} 月`;
}

export function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? '－' : `${Math.round(value * 100)}%`;
}

export function num(value: number | null | undefined): string {
  return value === null || value === undefined ? '－' : value.toFixed(1);
}

export type UtilBand = 'none' | 'u0' | 'u1' | 'u2' | 'u3' | 'u4';

/** 稼働率 → 背景の段 (A4 と同じ区切り)。u4 は混みの基準以上。 */
export function utilBand(value: number | null | undefined, crowdedAt: number): UtilBand {
  if (value === null || value === undefined) return 'none';
  if (value >= crowdedAt) return 'u4';
  if (value >= crowdedAt - 0.2) return 'u3';
  if (value >= crowdedAt - 0.4) return 'u2';
  if (value > 0) return 'u1';
  return 'u0';
}

export const SOURCE_TEXT: Record<MonthPayload['source'], string> = {
  plan: 'らく助の予定で数えています（先の月）。',
  record: 'らく助の訪問の記録で数えています（取消・削除を除く）。',
  mixed: '終わった日はらく助の訪問の記録、今日から先はらく助の予定で数えています。',
};

export const MARK_TEXT: Record<Mark, string> = {
  '○': 'その月のすべての週で入れる人がいた',
  '△': '4 週のうち 3 週・5 週のうち 4 週以上で入れる人がいた',
  '×': '入れる週が少ない',
  '－': '数えられる日が足りない',
};

/** 決まりの文章 (画面の「この表の見方」と A4 の注記を同じにする)。 */
export function monthRulesText(rules: MonthPayload['rules']): string {
  const lunch = rules.lunch_considered
    ? `昼休み ${rules.lunch_min} 分を ${rules.lunch_from}〜${rules.lunch_to} の間に取れること`
    : '昼休みは考えない';
  return (
    `新しい訪問 ${rules.visit_min} 分を、マスの ${rules.slot_min} 分のうちに始められるか` +
    `（1 日 ${rules.cap} 件まで・前後 ${rules.buffer_min + rules.travel_guess_min} 分の間・${lunch}）。` +
    `祝日は平日として数え、訪問が ${rules.open_day_min_visits} 件に満たない日は数えていません。`
  );
}
