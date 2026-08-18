/**
 * lib/shift-calendar — 月間出勤カレンダー畳み込みの純関数テスト.
 *
 * 規則 (staff-shift-confirmation-design.md §4):
 *   override '休み'→off / '午前休'・'午後休'→partial / '時間変更'→custom
 *   override なし → shifts[weekday].is_on ? working : nonworking (欠損曜日は true)
 *   overrides は ISO週粒度 API のため隣月分が混ざる → 月一致でフィルタ
 */
import { describe, expect, it } from 'vitest';

import type { OverrideRead } from '@/lib/schemas/staff-overrides';
import {
  buildShiftMonth,
  fmtIsoLocal,
  summarizeShiftMonth,
  toPyWeekday,
} from '@/lib/shift-calendar';

const SHIFTS_MON_TO_FRI = Array.from({ length: 7 }, (_, weekday) => ({
  weekday,
  is_on: weekday <= 4, // 月〜金 出勤
  start_time: weekday <= 4 ? '09:00' : null,
  end_time: weekday <= 4 ? '18:00' : null,
}));

function ov(id: string, date: string, type: OverrideRead['type']): OverrideRead {
  return { id: `00000000-0000-0000-0000-00000000000${id}`, date, type };
}

describe('shift-calendar', () => {
  it('toPyWeekday: JS 日曜=0 を backend 月曜=0 へ変換する', () => {
    expect(toPyWeekday(new Date('2026-09-07T00:00:00'))).toBe(0); // 月曜
    expect(toPyWeekday(new Date('2026-09-06T00:00:00'))).toBe(6); // 日曜
  });

  it('fmtIsoLocal はローカル日付 (UTCズレなし)', () => {
    expect(fmtIsoLocal(new Date(2026, 8, 1))).toBe('2026-09-01');
  });

  it('2026年9月: 平日出勤ベース + override の畳み込み', () => {
    const days = buildShiftMonth({
      month: new Date(2026, 8, 1), // 2026-09
      shifts: SHIFTS_MON_TO_FRI,
      overrides: [
        ov('1', '2026-09-07', '休み'),
        ov('2', '2026-09-08', '午前休'),
        ov('3', '2026-09-09', '時間変更'),
        ov('4', '2026-08-31', '休み'), // 隣月 (ISO週の混入) → 無視される
      ],
    });
    expect(days).toHaveLength(30);
    const byDate = new Map(days.map((d) => [d.date, d]));
    expect(byDate.get('2026-09-07')?.kind).toBe('off');
    expect(byDate.get('2026-09-08')?.kind).toBe('partial');
    expect(byDate.get('2026-09-09')?.kind).toBe('custom');
    expect(byDate.get('2026-09-10')?.kind).toBe('working'); // 木曜・override なし
    expect(byDate.get('2026-09-05')?.kind).toBe('nonworking'); // 土曜
    expect(byDate.get('2026-09-06')?.kind).toBe('nonworking'); // 日曜
    expect(days.some((d) => d.date === '2026-08-31')).toBe(false);
  });

  it('shifts 欠損曜日は is_on=true 扱い (BE バックフィル規約)', () => {
    const days = buildShiftMonth({
      month: new Date(2026, 8, 1),
      shifts: [],
      overrides: [],
    });
    expect(days.every((d) => d.kind === 'working')).toBe(true);
  });

  it('summarizeShiftMonth: off=休み・nonworking は数えない・他は出勤', () => {
    const days = buildShiftMonth({
      month: new Date(2026, 8, 1),
      shifts: SHIFTS_MON_TO_FRI,
      overrides: [ov('1', '2026-09-07', '休み'), ov('2', '2026-09-08', '午前休')],
    });
    const { workDays, offDays } = summarizeShiftMonth(days);
    // 2026-09 は平日22日。休み1日を引き、午前休は出勤扱い。
    expect(offDays).toBe(1);
    expect(workDays).toBe(21);
  });
});
