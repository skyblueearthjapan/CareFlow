/**
 * 「休み・勤務表」の言葉と小さな計算 (lib/leave-sheet.ts) と、API のエラーの文 (leaveErrorMessage)。
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('next-auth/react', () => ({ useSession: () => ({ data: null, status: 'loading' }) }));

import { ApiError } from '@/lib/api-client';
import {
  addMonths,
  fmtClock,
  fmtDayJp,
  fmtMonthJp,
  fmtNum,
  groupPending,
  LEAVE_DESC,
  offCountIfAllApproved,
  parseMonthParam,
  upcomingLeaveMonth,
} from '@/lib/leave-sheet';
import { leaveErrorMessage } from '@/lib/queries/leave';
import type { LeaveMonthDay, LeavePendingItem } from '@/lib/schemas/leave';

function item(over: Partial<LeavePendingItem>): LeavePendingItem {
  return {
    id: 'x',
    staff_id: 's',
    staff_name: '',
    staff_order: 0,
    date: '2026-11-12',
    override_type: 'off',
    kind: 'normal',
    note: null,
    created_at: '2026-10-15T00:00:00Z',
    current_leave_type: null,
    past_day: false,
    move_visit_count: 0,
    off_count_now: 0,
    off_count_if_approved: 1,
    ...over,
  };
}

describe('月と日付', () => {
  it('月をずらす (年をまたぐ)・読めない値は null', () => {
    expect(addMonths('2026-12', 1)).toBe('2027-01');
    expect(addMonths('2026-01', -1)).toBe('2025-12');
    expect(parseMonthParam('2026-11')).toBe('2026-11');
    expect(parseMonthParam('2026-11-12')).toBe('2026-11');
    expect(parseMonthParam('2026-13')).toBeNull();
    expect(parseMonthParam('abc')).toBeNull();
    expect(parseMonthParam(null)).toBeNull();
  });

  it('いま組んでいる月は JST の来月', () => {
    // 2026-10-31 23:30 JST = 2026-10-31 14:30 UTC → 11 月
    expect(upcomingLeaveMonth(new Date('2026-10-31T14:30:00Z'))).toBe('2026-11');
    // 2026-12-31 15:30 UTC = 2027-01-01 0:30 JST → 2 月
    expect(upcomingLeaveMonth(new Date('2026-12-31T15:30:00Z'))).toBe('2027-02');
  });

  it('表示の形', () => {
    expect(fmtDayJp('2026-11-03')).toBe('11/3（火）');
    expect(fmtMonthJp('2026-11')).toBe('2026年11月');
    expect(fmtNum(10)).toBe('10');
    expect(fmtNum(10.5)).toBe('10.5');
    expect(fmtClock('09:00:00')).toBe('9:00');
    expect(fmtClock(null)).toBe('--');
  });

  it('半休の境目は lib/leave の値から', () => {
    expect(LEAVE_DESC.am_off).toBe('13:00 から勤務');
    expect(LEAVE_DESC.pm_off).toBe('12:30 から休み');
  });
});

describe('申請中のまとめ方', () => {
  const items = [
    item({ id: 'a', staff_id: 's2', staff_name: 'B', staff_order: 2, date: '2026-11-12' }),
    item({ id: 'b', staff_id: 's1', staff_name: 'A', staff_order: 1, date: '2026-11-14' }),
    item({ id: 'c', staff_id: 's1', staff_name: 'A', staff_order: 1, date: '2026-11-12' }),
  ];

  it('日付順は日ごと (同じ日は職員の順)', () => {
    const g = groupPending(items, 'date');
    expect(g.map((x) => x.key)).toEqual(['2026-11-12', '2026-11-14']);
    expect(g[0]!.items.map((i) => i.id)).toEqual(['c', 'a']);
  });

  it('職員順は人ごと (同じ人は日付の順)', () => {
    const g = groupPending(items, 'staff');
    expect(g.map((x) => x.staffName)).toEqual(['A', 'B']);
    expect(g[0]!.items.map((i) => i.id)).toEqual(['c', 'b']);
  });

  it('全部承認したあとのその日休む人 = いま＋申請ごとの増減', () => {
    const day = { off_count: 1 } as LeaveMonthDay;
    expect(
      offCountIfAllApproved(day, [
        item({ off_count_now: 1, off_count_if_approved: 2 }),
        // 勤務しない曜日・新人・取り消しのお願いなどは増えない (バックエンドの数をそのまま使う)
        item({ off_count_now: 1, off_count_if_approved: 1 }),
        item({ override_type: 'cancel', off_count_now: 1, off_count_if_approved: 0 }),
      ]),
    ).toBe(1);
  });
});

describe('leaveErrorMessage', () => {
  it('detail の文字列・まとめて承認の message・入力の検査の配列', () => {
    expect(leaveErrorMessage(new ApiError('x', 409, { detail: '申請中です' }))).toBe('申請中です');
    expect(
      leaveErrorMessage(
        new ApiError('x', 409, { detail: { request_id: 'r', message: '承認済みです' } }),
      ),
    ).toBe('承認済みです');
    expect(
      leaveErrorMessage(
        new ApiError('x', 422, { detail: [{ msg: '月は YYYY-MM' }, { msg: '日付が不正' }] }),
      ),
    ).toBe('月は YYYY-MM / 日付が不正');
  });

  it('detail が無ければ既定の文 (ApiError の英語の文は出さない)', () => {
    expect(leaveErrorMessage(new ApiError('API 500', 500, null), '失敗しました')).toBe(
      '失敗しました',
    );
    expect(leaveErrorMessage(new Error('ネットワークの問題'))).toBe('ネットワークの問題');
  });
});
