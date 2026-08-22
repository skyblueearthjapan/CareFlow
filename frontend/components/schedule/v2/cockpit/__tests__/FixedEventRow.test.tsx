/**
 * FixedEventRow — 盤面最上段「全員（固定）」帯 (週空間 Phase E)。
 *
 * ① 日ごとに 🔒 + 時刻 + タイトルの帯が 1 本にまとまる
 * ② 休みの人は「休:○○」で自動除外表示 (帯からは外れる)
 * ③ 今週だけ外した人 (cancelled_at) は「今週 ○○除外」
 * ④ クリック → 参加者を今週だけ外す / 戻す が onExclude(eventId, cancel) で飛ぶ
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { FixedEventRow } from '../FixedEventRow';
import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';
import type { CockpitEventRead } from '@/lib/schemas/v2/cockpit';
import type { StaffRead } from '@/lib/schemas/staff';

const S1 = '00000000-0000-4000-8000-000000000001';
const S2 = '00000000-0000-4000-8000-000000000002';
const S3 = '00000000-0000-4000-8000-000000000003';

const WEEK_START = new Date(2026, 7, 17); // 2026-08-17 (月)
const MON = '2026-08-17';

const staffMap = new Map<string, StaffRead>([
  [S1, { id: S1, name: '川名' } as StaffRead],
  [S2, { id: S2, name: '髙梨' } as StaffRead],
  [S3, { id: S3, name: '熊澤' } as StaffRead],
]);

function ev(id: string, staffId: string, over: Partial<CockpitEventRead> = {}): CockpitEventRead {
  return {
    id,
    staff_id: staffId,
    date: MON,
    title: '朝会',
    start_time: '08:30',
    end_time: '09:00',
    type: 'イベント',
    note: null,
    blocking: false,
    source: 'fixed',
    cancelled_at: null,
    ...over,
  } as CockpitEventRead;
}

const EV_1 = '00000000-0000-4000-8000-0000000000e1';
const EV_2 = '00000000-0000-4000-8000-0000000000e2';
const EV_3 = '00000000-0000-4000-8000-0000000000e3';

const offByStaffWeekday = new Map<string, WeekOverrideRead>([
  [
    `${S3}:0`,
    {
      id: 'o1',
      staff_id: S3,
      date: MON,
      weekday: 0,
      type: '休み',
      start_time: null,
      end_time: null,
      note: null,
    },
  ],
]);

function renderRow(events: CockpitEventRead[]) {
  const onExclude = vi.fn();
  render(
    <FixedEventRow
      events={events}
      staffMap={staffMap}
      offByStaffWeekday={offByStaffWeekday}
      weekStart={WEEK_START}
      onExclude={onExclude}
    />,
  );
  return onExclude;
}

describe('FixedEventRow', () => {
  it('休みの人は「休:」で自動除外、今週だけ外した人は「今週 ○○除外」', () => {
    renderRow([
      ev(EV_1, S1),
      ev(EV_2, S2, { cancelled_at: '2026-08-16T10:00:00+09:00' }),
      ev(EV_3, S3),
    ]);
    const band = screen.getByTestId('fixed-event-band-0-08:30');
    expect(band).toHaveTextContent('🔒');
    expect(band).toHaveTextContent('08:30');
    expect(band).toHaveTextContent('朝会');
    expect(band).toHaveTextContent('休:熊澤');
    expect(band).toHaveTextContent('今週 髙梨除外');
  });

  it('帯クリックで「今週だけ外す」「戻す」が onExclude へ', () => {
    const onExclude = renderRow([
      ev(EV_1, S1),
      ev(EV_2, S2, { cancelled_at: '2026-08-16T10:00:00+09:00' }),
    ]);
    fireEvent.click(screen.getByTestId('fixed-event-band-0-08:30'));

    // staffId は cancel-week の URL 構築に要る (eventId だけでは呼べない)
    fireEvent.click(screen.getByTestId(`fixed-event-exclude-${EV_1}`));
    expect(onExclude).toHaveBeenCalledWith(EV_1, S1, true);

    fireEvent.click(screen.getByTestId(`fixed-event-restore-${EV_2}`));
    expect(onExclude).toHaveBeenCalledWith(EV_2, S2, false);
  });

  it('午前休は午前のイベントだけ自動除外する', () => {
    const amOff = new Map<string, WeekOverrideRead>([
      [
        `${S1}:0`,
        {
          id: 'o2',
          staff_id: S1,
          date: MON,
          weekday: 0,
          type: '午前休',
          start_time: null,
          end_time: null,
          note: null,
        },
      ],
    ]);
    render(
      <FixedEventRow
        events={[
          ev(EV_1, S1), // 08:30 朝会 → 午前休にかかる
          ev(EV_3, S1, { id: EV_3, start_time: '15:00', end_time: '15:30', title: '夕会' }),
        ]}
        staffMap={staffMap}
        offByStaffWeekday={amOff}
        weekStart={WEEK_START}
        onExclude={vi.fn()}
      />,
    );
    expect(screen.getByTestId('fixed-event-band-0-08:30')).toHaveTextContent('休:川名');
    // 午後の帯は休み扱いにならず、参加者として残る
    const pm = screen.getByTestId('fixed-event-band-0-15:00');
    expect(pm).not.toHaveTextContent('休:');
    fireEvent.click(pm);
    expect(screen.getByTestId(`fixed-event-exclude-${EV_3}`)).toBeInTheDocument();
  });

  it('固定イベントの無い曜日は「—」', () => {
    renderRow([ev(EV_1, S1)]);
    expect(screen.getByTestId('fixed-event-day-1')).toHaveTextContent('—');
  });
});
