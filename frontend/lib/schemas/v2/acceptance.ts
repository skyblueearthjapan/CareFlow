/**
 * AcceptanceCalendar v2 zod schemas (Wave 15 Phase 3 / W15-FE).
 *
 * Backend `backend/app/schemas/v2/acceptance.py` の Pydantic schema と
 * **完全一致** させる。
 *
 *   - weekday: 0=Mon ... 6=Sun
 *   - time_slot: HH:MM:SS (1 時間刻み運用想定だが任意の time)
 *   - status: 'available' (○) / 'consult' (△) / 'unavailable' (×)
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const ACCEPTANCE_STATUS_VALUES = ['available', 'consult', 'unavailable'] as const;
export const acceptanceStatusEnum = z.enum(ACCEPTANCE_STATUS_VALUES);
export type AcceptanceStatus = (typeof ACCEPTANCE_STATUS_VALUES)[number];

/** time_slot の表示用ラベル. */
export const ACCEPTANCE_STATUS_LABELS: Record<AcceptanceStatus, string> = {
  available: '○',
  consult: '△',
  unavailable: '×',
};

/** time_slot の表示用 long ラベル (凡例用). */
export const ACCEPTANCE_STATUS_LONG_LABELS: Record<AcceptanceStatus, string> = {
  available: '受入可',
  consult: '要相談',
  unavailable: '受入不可',
};

// ---------------------------------------------------------------------------
// Base / Read schemas
// ---------------------------------------------------------------------------

/** "HH:MM" or "HH:MM:SS" を許容する time validator. */
const timeStringSchema = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/, {
  message: 'time_slot は HH:MM[:SS] 形式である必要があります',
});

export const acceptanceCalendarEntrySchema = z.object({
  weekday: z.number().int().min(0).max(6),
  time_slot: timeStringSchema,
  status: acceptanceStatusEnum,
  notes: z.string().nullable().optional(),
});

export type AcceptanceCalendarEntry = z.infer<typeof acceptanceCalendarEntrySchema>;

export const acceptanceCalendarReadSchema = acceptanceCalendarEntrySchema.extend({
  id: z.string().uuid(),
  office_id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
});

export type AcceptanceCalendarRead = z.infer<typeof acceptanceCalendarReadSchema>;

// ---------------------------------------------------------------------------
// Bulk PUT
// ---------------------------------------------------------------------------

export const acceptanceCalendarBulkUpsertSchema = z.object({
  office_id: z.string().uuid(),
  entries: z.array(acceptanceCalendarEntrySchema).default([]),
});

export type AcceptanceCalendarBulkUpsert = z.infer<typeof acceptanceCalendarBulkUpsertSchema>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** "HH:MM:SS" / "HH:MM" → "HH:MM". */
export function normalizeTimeSlot(s: string): string {
  return s.length >= 5 ? s.slice(0, 5) : s;
}

/**
 * (weekday, time) → AcceptanceStatus の lookup を生成する。
 *
 * BE は 1 時間刻みで返す想定。time は "HH:MM" 形式に揃えた key で引く。
 * 該当エントリが無い場合は undefined。
 */
export function buildAcceptanceLookup(
  rows: AcceptanceCalendarRead[],
): Map<string, AcceptanceStatus> {
  const map = new Map<string, AcceptanceStatus>();
  for (const r of rows) {
    const key = `${r.weekday}:${normalizeTimeSlot(r.time_slot)}`;
    map.set(key, r.status);
  }
  return map;
}

/** "HH:MM" → 1 時間刻みに丸めた "HH:00". */
export function roundDownToHour(time: string): string {
  return `${time.slice(0, 2)}:00`;
}
