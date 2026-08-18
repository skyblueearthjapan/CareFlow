/**
 * 月間出勤カレンダーの純関数 (staff-shift-confirmation-design.md §4).
 *
 * 素材は既存 API 2 本 (BE 追加なし):
 *   - GET /staff/{id}/shifts      … 7 曜日の is_on (weekday 0=月 … 6=日)
 *   - GET /staff/{id}/overrides   … その週だけの休み/時間変更 (日本語ラベル + date)
 *
 * 「日ごとの状態」への畳み込み規則:
 *   override あり: '休み'→off / '午前休'・'午後休'→partial / '時間変更'→custom
 *   override なし: shifts[weekday].is_on ? working : nonworking
 *
 * PC (/admin/staff-leave) とモバイル (/m/shifts) の両方が使う共有ロジック。
 */
import type { OverrideRead } from '@/lib/schemas/staff-overrides';
import type { StaffShiftItem } from '@/lib/schemas/staff-shifts';

export type ShiftDayKind = 'working' | 'off' | 'partial' | 'custom' | 'nonworking';

export interface ShiftDay {
  /** YYYY-MM-DD (ローカル基準) */
  date: string;
  kind: ShiftDayKind;
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

const OVERRIDE_KIND: Record<string, ShiftDayKind> = {
  休み: 'off',
  午前休: 'partial',
  午後休: 'partial',
  時間変更: 'custom',
};

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
    let kind: ShiftDayKind;
    if (override) {
      kind = OVERRIDE_KIND[override.type] ?? 'custom';
    } else {
      // shifts 欠損曜日は BE と同じく is_on=true 扱い (バックフィル規約)
      kind = (isOnByWeekday.get(toPyWeekday(d)) ?? true) ? 'working' : 'nonworking';
    }
    days.push({ date: iso, kind, override });
  }
  return days;
}

/** 集計: 出勤 (working+custom+partial) / 休み (off)。nonworking は勤務外で数えない。 */
export function summarizeShiftMonth(days: ShiftDay[]): { workDays: number; offDays: number } {
  let workDays = 0;
  let offDays = 0;
  for (const d of days) {
    if (d.kind === 'off') offDays++;
    else if (d.kind !== 'nonworking') workDays++;
  }
  return { workDays, offDays };
}
