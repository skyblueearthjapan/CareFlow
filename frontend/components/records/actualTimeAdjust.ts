/**
 * 「実績の時刻を合わせる」枠の純関数（設計 `pc-actual-time-edit-design-2026-10-06.md`）。
 *
 * 部品は 1 つ、入口は 2 つ（訪問モニターの詳細パネル・打刻履歴の詳細）。どちらの行も
 * `ActualTimeTarget` に揃えてから枠に渡す。時刻の範囲は BE が決める（ここでは複製しない）。
 *
 * 退出のひと押しの候補は **2 つだけ**: 予定の終わり／到着＋予定の長さ（最初に選ぶ）。
 * 「次の訪問の 10 分前」は外した（PO 決定 2026-10-06 Q2）。
 * まとめて退出を入れるときの決め方（Q1）: 到着＋予定の長さ（既定）／到着＋○分／予定の終わり。
 */

import { hmToMinutes, minutesToHm } from '@/lib/format/actualTime';
import type { VisitHistoryRow } from '@/lib/queries/visit-history';
import type { MonitorVisit } from '@/lib/schemas/monitor';

/** 枠に渡す 1 訪問（打刻履歴の行・モニターの訪問のどちらからでも作る）。 */
export interface ActualTimeTarget {
  visitId: string;
  /** 予定 `HH:MM`。予定外の訪問は null。 */
  plannedStart: string | null;
  plannedEnd: string | null;
  /** 実績時刻（ISO・UTC）。無ければ null。 */
  arrivalAt: string | null;
  departureAt: string | null;
  /** 読取時刻（ISO）。読み取りが無ければ null。 */
  arrivalReadAt: string | null;
  departureReadAt: string | null;
  arrivalAdjusted: boolean;
  departureAdjusted: boolean;
  /** 読み取りの無い到着 / 退出（手で入れた時刻）。 */
  arrivalManual: boolean;
  departureManual: boolean;
  /** 今のユーザーが合わせられるか（BE の判定）。項目の無い古い応答は null。 */
  adjustAllowed: boolean | null;
  /** 打刻なしの訪問に、到着・退出を手で入れられるか（管理者だけ・BE の判定）。 */
  manualArrivalAllowed: boolean;
  /**
   * 「未訪問」の記録があるか・その理由。管理者は未訪問の記録がある訪問にも到着を手で
   * 入れられる (PO 決定 2026-10-07) ので、枠に注意書きとして出す。
   */
  hasNoShow: boolean;
  noShowReason: string | null;
}

/** `HH:MM[:SS]` → `HH:MM`。空は null。 */
function hm5(t: string | null | undefined): string | null {
  return t ? t.slice(0, 5) : null;
}

export function targetFromHistoryRow(row: VisitHistoryRow): ActualTimeTarget {
  return {
    visitId: row.visit_id,
    plannedStart: hm5(row.start_time),
    plannedEnd: hm5(row.end_time),
    arrivalAt: row.arrival_at ?? null,
    departureAt: row.departure_at ?? null,
    arrivalReadAt: row.arrival_read_at ?? null,
    departureReadAt: row.departure_read_at ?? null,
    arrivalAdjusted: !!row.arrival_adjusted,
    departureAdjusted: !!row.departure_adjusted,
    arrivalManual: !!row.arrival_manual,
    departureManual: !!row.departure_manual,
    adjustAllowed: row.adjust_allowed ?? null,
    manualArrivalAllowed: !!row.manual_arrival_allowed,
    hasNoShow: !!row.has_no_show,
    noShowReason: row.no_show_reason ?? null,
  };
}

export function targetFromMonitorVisit(v: MonitorVisit): ActualTimeTarget {
  return {
    visitId: v.visit_id,
    // 予定外の訪問は予定欄が実績の写しなので、予定の長さを候補に使わない。
    plannedStart: v.is_unplanned ? null : hm5(v.start_time),
    plannedEnd: v.is_unplanned ? null : hm5(v.end_time),
    arrivalAt: v.arrival_at ?? v.arrival?.scanned_at ?? null,
    departureAt: v.departure_at ?? v.departure?.scanned_at ?? null,
    arrivalReadAt: v.arrival_read_at ?? null,
    departureReadAt: v.departure_read_at ?? null,
    arrivalAdjusted: !!v.arrival_adjusted,
    departureAdjusted: !!v.departure_adjusted,
    arrivalManual: !!v.arrival_manual,
    departureManual: !!v.departure_manual,
    adjustAllowed: v.adjust_allowed ?? null,
    manualArrivalAllowed: !!v.manual_arrival_allowed,
    hasNoShow: v.no_show != null,
    noShowReason: v.no_show?.reason ?? null,
  };
}

/** 予定の長さ（分）。予定が無い・読めないときは null。 */
export function plannedLength(start: string | null, end: string | null): number | null {
  const s = hmToMinutes(start);
  const e = hmToMinutes(end);
  return s == null || e == null || e <= s ? null : e - s;
}

/** `HH:MM` に分を足す。その日を越えるときは null（翌日の時刻は入れられない）。 */
export function addMinutesHm(base: string | null, minutes: number): string | null {
  const b = hmToMinutes(base);
  if (b == null || !Number.isFinite(minutes)) return null;
  const t = b + Math.round(minutes);
  return t < 0 || t > 23 * 60 + 59 ? null : minutesToHm(t);
}

export type DepartureCandidateKey = 'planned_end' | 'arrival_plus_len';

export interface DepartureCandidate {
  key: DepartureCandidateKey;
  label: string;
  time: string;
}

/** 最初に選んでおく候補（PO 決定 Q2）。 */
export const DEFAULT_DEPARTURE_CANDIDATE: DepartureCandidateKey = 'arrival_plus_len';

/**
 * 退出のひと押しの候補（2 つ・この順）。到着は JST の `HH:MM`。決められない候補は出さない。
 */
export function departureCandidates(
  arrival: string | null,
  plannedStart: string | null,
  plannedEnd: string | null,
): DepartureCandidate[] {
  const out: DepartureCandidate[] = [];
  if (plannedEnd) out.push({ key: 'planned_end', label: '予定の終わり', time: plannedEnd });
  const len = plannedLength(plannedStart, plannedEnd);
  const plus = len != null ? addMinutesHm(arrival, len) : null;
  if (plus) out.push({ key: 'arrival_plus_len', label: '到着＋予定の長さ', time: plus });
  return out;
}

/** 退出を入れる欄の最初の値: 到着＋予定の長さ → 予定の終わり → 空。 */
export function defaultDepartureTime(
  arrival: string | null,
  plannedStart: string | null,
  plannedEnd: string | null,
): string {
  const cands = departureCandidates(arrival, plannedStart, plannedEnd);
  return cands.find((c) => c.key === DEFAULT_DEPARTURE_CANDIDATE)?.time ?? cands[0]?.time ?? '';
}

// ---------------------------------------------------------------------------
// まとめて退出を入れる（D3・Q1）
// ---------------------------------------------------------------------------

export type BulkDepartureRule = 'len' | 'min' | 'end';

/** 到着＋○分の既定（分）。 */
export const BULK_DEFAULT_MINUTES = 35;
/** 到着＋○分で入れられる範囲（分）。 */
export const BULK_MIN_MINUTES = 1;
export const BULK_MAX_MINUTES = 600;

export interface BulkDeparturePlan {
  /** 入る退出の時刻（`HH:MM`）。決められなければ null。 */
  time: string | null;
  /** 決め方の説明（「到着＋35分」「予定の終わり」）、または決められない理由。 */
  why: string;
}

/**
 * まとめて入れるときの 1 件ぶんの退出時刻。`arrival` は JST の `HH:MM`。
 * 決められない（予定が無い・日をまたぐ）ときは `time = null` と理由。
 */
export function bulkDepartureTime(
  rule: BulkDepartureRule,
  row: { arrival: string | null; plannedStart: string | null; plannedEnd: string | null },
  minutes: number = BULK_DEFAULT_MINUTES,
): BulkDeparturePlan {
  if (!row.arrival) return { time: null, why: '到着の記録がありません' };
  if (rule === 'end') {
    return row.plannedEnd
      ? { time: row.plannedEnd, why: '予定の終わり' }
      : { time: null, why: '予定が無いため決められません' };
  }
  if (
    rule === 'min' &&
    !(Number.isInteger(minutes) && minutes >= BULK_MIN_MINUTES && minutes <= BULK_MAX_MINUTES)
  ) {
    return { time: null, why: `分は ${BULK_MIN_MINUTES}〜${BULK_MAX_MINUTES} で入れてください` };
  }
  const len = rule === 'len' ? plannedLength(row.plannedStart, row.plannedEnd) : minutes;
  if (len == null) return { time: null, why: '予定が無いため決められません' };
  const time = addMinutesHm(row.arrival, len);
  return time
    ? { time, why: `到着＋${len}分` }
    : { time: null, why: '日付をまたぐため決められません' };
}

/** 1 件ずつ記録した結果。 */
export interface BulkResult {
  visitId: string;
  ok: boolean;
  /** 入れた時刻（成功時）。 */
  time: string | null;
  /** 失敗の理由（BE の文言そのまま）。 */
  error: string | null;
}

/**
 * 1 件ずつ **順番に** 記録する（並べて投げない・既存の検証と監査がそのまま効く）。
 * 時刻の決まらない行は送らずに失敗として残す。1 件の失敗で止めない。
 */
export async function runBulkDepartures(
  items: ReadonlyArray<{ visitId: string; time: string | null; why: string }>,
  put: (visitId: string, time: string) => Promise<unknown>,
  errorMessage: (e: unknown) => string,
): Promise<BulkResult[]> {
  const results: BulkResult[] = [];
  for (const item of items) {
    if (!item.time) {
      results.push({ visitId: item.visitId, ok: false, time: null, error: item.why });
      continue;
    }
    try {
      await put(item.visitId, item.time);
      results.push({ visitId: item.visitId, ok: true, time: item.time, error: null });
    } catch (e) {
      results.push({ visitId: item.visitId, ok: false, time: item.time, error: errorMessage(e) });
    }
  }
  return results;
}
