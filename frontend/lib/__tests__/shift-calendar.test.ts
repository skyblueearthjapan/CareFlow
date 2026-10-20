/**
 * lib/shift-calendar — 月の出勤カレンダーの畳み込みと休みの数え方の純関数テスト.
 *
 * 規則 (leave-and-kinmu-sheet-design §2-2):
 *   override '休み'→off / '午前休'→am_off / '午後休'→pm_off / '有給'→paid_off / '時間変更'→custom
 *   override なし → shifts[weekday].is_on ? working : nonworking (欠損曜日は true)
 *   overrides は ISO週粒度 API のため隣月分が混ざる → 月一致でフィルタ
 *   休日数: 公休 1・半休 0.5 (型で勤務しない曜日なら 1)・有給 0 (別に数える)・型で勤務しない曜日 1
 */
import { describe, expect, it } from 'vitest';

import type { OverrideRead } from '@/lib/schemas/staff-overrides';
import {
  buildShiftMonth,
  fmtIsoLocal,
  leaveDayWeight,
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

  it('2026年9月: 平日出勤ベース + override の畳み込み (午前と午後を分ける)', () => {
    const days = buildShiftMonth({
      month: new Date(2026, 8, 1), // 2026-09
      shifts: SHIFTS_MON_TO_FRI,
      overrides: [
        ov('1', '2026-09-07', '休み'),
        ov('2', '2026-09-08', '午前休'),
        ov('3', '2026-09-09', '時間変更'),
        ov('5', '2026-09-10', '午後休'),
        ov('6', '2026-09-11', '有給'),
        ov('4', '2026-08-31', '休み'), // 隣月 (ISO週の混入) → 無視される
      ],
    });
    expect(days).toHaveLength(30);
    const byDate = new Map(days.map((d) => [d.date, d]));
    expect(byDate.get('2026-09-07')?.kind).toBe('off');
    expect(byDate.get('2026-09-08')?.kind).toBe('am_off');
    expect(byDate.get('2026-09-09')?.kind).toBe('custom');
    expect(byDate.get('2026-09-10')?.kind).toBe('pm_off');
    expect(byDate.get('2026-09-11')?.kind).toBe('paid_off');
    expect(byDate.get('2026-09-14')?.kind).toBe('working'); // 月曜・override なし
    expect(byDate.get('2026-09-05')?.kind).toBe('nonworking'); // 土曜
    expect(byDate.get('2026-09-05')?.patternWorking).toBe(false);
    expect(byDate.get('2026-09-07')?.patternWorking).toBe(true);
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

  it('leaveDayWeight: 公休 1・半休 0.5 (勤務しない曜日なら 1)・有給 0', () => {
    expect(leaveDayWeight('off', true)).toBe(1);
    expect(leaveDayWeight('am_off', true)).toBe(0.5);
    expect(leaveDayWeight('pm_off', true)).toBe(0.5);
    expect(leaveDayWeight('pm_off', false)).toBe(1);
    expect(leaveDayWeight('paid_off', true)).toBe(0);
  });

  it('summarizeShiftMonth: 型の休み + 公休 1 + 半休 0.5・有給は休日に数えず別に数える', () => {
    const days = buildShiftMonth({
      month: new Date(2026, 8, 1),
      shifts: SHIFTS_MON_TO_FRI,
      overrides: [
        ov('1', '2026-09-07', '休み'),
        ov('2', '2026-09-08', '午前休'),
        ov('3', '2026-09-09', '時間変更'),
        ov('5', '2026-09-10', '午後休'),
        ov('6', '2026-09-11', '有給'),
        ov('7', '2026-09-12', '午前休'), // 土曜 (型で勤務しない曜日) の半休は 1
      ],
    });
    const { holidays, paidDays } = summarizeShiftMonth(days);
    // 2026-09 の土日は 8 日 (うち 9/12 は午前休の登録 → 1 のまま)。公休 1・半休 0.5×2。
    expect(holidays).toBe(8 + 1 + 0.5 + 0.5);
    expect(paidDays).toBe(1);
  });
});
