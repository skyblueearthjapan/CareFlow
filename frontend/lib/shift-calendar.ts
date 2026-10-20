/**
 * 月の出勤カレンダーの純関数 (スマホの「休み」ページ・設計 leave-and-kinmu-sheet §2-2・§6).
 *
 * 素材は既存 API 2 本:
 *   - GET /staff/{id}/shifts      … 7 曜日の is_on (weekday 0=月 … 6=日)
 *   - GET /staff/{id}/overrides   … その日だけの休み/時間変更 (日本語ラベル + date)
 *
 * 「日ごとの状態」への畳み込み規則:
 *   override あり: '休み'→off / '午前休'→am_off / '午後休'→pm_off / '有給'→paid_off / '時間変更'→custom
 *   override なし: shifts[weekday].is_on ? working : nonworking
 *
 * 休日数 (§2-2): 公休 1・半休 0.5 (型で勤務しない曜日なら 1)・有給 0 (別に数える)・
 * 登録なしで型で勤務しない曜日 1。
 */
import type { OverrideRead, OverrideType } from '@/lib/schemas/staff-overrides';
import type { StaffShiftItem } from '@/lib/schemas/staff-shifts';
import type { LeaveType } from '@/lib/leave';

export type ShiftDayKind =
  | 'working'
  | 'off'
  | 'am_off'
  | 'pm_off'
  | 'paid_off'
  | 'custom'
  | 'nonworking';

export interface ShiftDay {
  /** YYYY-MM-DD (ローカル基準) */
  date: string;
  kind: ShiftDayKind;
  /** 型 (staff_shifts) でその曜日に勤務するか。 */
  patternWorking: boolean;
  /** その日に適用されている override (なければ null) */
  override: OverrideRead | null;
}

/** ローカル時刻基準の YYYY-MM-DD (toISOString は UTC ズレするため使わない)。 */
export function fmtIsoLocal(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

/** month (月内の任意日) の月初日を返す。 */
export function startOfMonth(month: Date): Date {
  return new Date(month.getFullYear(), month.getMonth(), 1);
}

/** month の月末日を返す。 */
export function endOfMonth(month: Date): Date {
  return new Date(month.getFullYear(), month.getMonth() + 1, 0);
}

/** JS Date.getDay() (0=日) → backend weekday (0=月)。 */
export function toPyWeekday(d: Date): number {
  return (d.getDay() + 6) % 7;
}

/** override の日本語ラベル → 日の状態。 */
const OVERRIDE_KIND: Record<OverrideType, ShiftDayKind> = {
  休み: 'off',
  午前休: 'am_off',
  午後休: 'pm_off',
  有給: 'paid_off',
  時間変更: 'custom',
};

/**
 * 休みの種類 1 日ぶんの休日数。有給は数えない (0)。
 * 半休は 0.5、ただし型で勤務しない曜日なら 1。
 */
export function leaveDayWeight(type: LeaveType, patternWorking: boolean): number {
  if (type === 'paid_off') return 0;
  if (type === 'off') return 1;
  return patternWorking ? 0.5 : 1;
}

/**
 * 月の全日を状態つきで返す。
 * overrides は API が ISO 週粒度でフィルタするため月境界で隣月分が混ざる —
 * ここで month 一致にフィルタして畳み込む。
 */
export function buildShiftMonth({
  month,
  shifts,
  overrides,
}: {
  month: Date;
  shifts: StaffShiftItem[];
  overrides: OverrideRead[];
}): ShiftDay[] {
  const first = startOfMonth(month);
  const last = endOfMonth(month);
  const prefix = fmtIsoLocal(first).slice(0, 7); // YYYY-MM

  const isOnByWeekday = new Map<number, boolean>();
  for (const s of shifts) {
    isOnByWeekday.set(s.weekday, s.is_on);
  }
  const overrideByDate = new Map<string, OverrideRead>();
  for (const o of overrides) {
    if (o.date.startsWith(prefix)) {
      overrideByDate.set(o.date, o);
    }
  }

  const days: ShiftDay[] = [];
  for (let dayNum = 1; dayNum <= last.getDate(); dayNum++) {
    const d = new Date(first.getFullYear(), first.getMonth(), dayNum);
    const iso = fmtIsoLocal(d);
    const override = overrideByDate.get(iso) ?? null;
    // shifts 欠損曜日は BE と同じく is_on=true 扱い (バックフィル規約)
    const patternWorking = isOnByWeekday.get(toPyWeekday(d)) ?? true;
    let kind: ShiftDayKind;
    if (override) {
      kind = OVERRIDE_KIND[override.type] ?? 'custom';
    } else {
      kind = patternWorking ? 'working' : 'nonworking';
    }
    days.push({ date: iso, kind, patternWorking, override });
  }
  return days;
}

/**
 * 集計: 休日数 (公休 1・半休 0.5・型で勤務しない曜日 1) と有給の日数。
 * 有給は休日数に数えない。時間変更・出勤は 0。
 */
export function summarizeShiftMonth(days: ShiftDay[]): { holidays: number; paidDays: number } {
  let holidays = 0;
  let paidDays = 0;
  for (const d of days) {
    if (d.kind === 'nonworking') holidays += 1;
    else if (d.kind === 'off' || d.kind === 'am_off' || d.kind === 'pm_off') {
      holidays += leaveDayWeight(d.kind, d.patternWorking);
    } else if (d.kind === 'paid_off') paidDays += 1;
  }
  return { holidays, paidDays };
}
