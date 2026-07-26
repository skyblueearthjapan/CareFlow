/**
 * EventStrip — 全ビュー共通のイベント緑チップ表示 (PO確定 2026-07-26)。
 *
 * ① コースを持たないスタッフのイベント (休み) もスタッフ名つきで表示される
 * ② 📝ゼロ長メモは時刻なし・📝つきで表示される
 * ③ 週モード (weekdays=[0..5]) は日付ラベルつきで日ごとに行が分かれる
 * ④ イベントが1件も無ければ何も描かない
 */
import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { EventStrip } from '../EventStrip';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';

const WEEK_START = new Date(2026, 6, 20); // 2026-07-20 (月)
const STAFF_1 = '00000000-0000-4000-8000-000000000001';

const staffMap = new Map<string, StaffRead>([
  [STAFF_1, { id: STAFF_1, name: '宇田川　優莉' } as unknown as StaffRead],
]);

function ev(partial: Partial<EventRead> & { id: string; date: string }): EventRead {
  return {
    title: '',
    start_time: '09:00',
    end_time: '18:00',
    type: 'イベント',
    note: null,
    ...partial,
  } as EventRead;
}

const events = new Map<string, EventRead[]>([
  [
    STAFF_1,
    [
      ev({ id: '00000000-0000-4000-8000-00000000e001', date: '2026-07-21', title: '休み' }),
      ev({
        id: '00000000-0000-4000-8000-00000000e002',
        date: '2026-07-25',
        title: '清水様：歯科薬お渡し',
        start_time: '00:00',
        end_time: '00:00',
      }),
    ],
  ],
]);

describe('EventStrip', () => {
  it('① 日モード: スタッフ名+時刻+タイトルの緑チップが出る', () => {
    render(
      <EventStrip
        staffEventsByStaff={events}
        staffMap={staffMap}
        weekdays={[1]}
        weekStart={WEEK_START}
        testId="day-event-strip"
      />,
    );
    const strip = screen.getByTestId('day-event-strip');
    expect(within(strip).getByText('イベント')).toBeInTheDocument();
    expect(within(strip).getByText(/優莉/)).toBeInTheDocument();
    expect(within(strip).getByText(/09:00〜18:00/)).toBeInTheDocument();
    expect(within(strip).getByText('休み')).toBeInTheDocument();
  });

  it('② 📝ゼロ長メモは 📝 表示 (時刻なし)', () => {
    render(
      <EventStrip
        staffEventsByStaff={events}
        staffMap={staffMap}
        weekdays={[5]}
        weekStart={WEEK_START}
      />,
    );
    const chip = screen.getByTestId('event-strip-chip-00000000-0000-4000-8000-00000000e002');
    expect(within(chip).getByText('📝')).toBeInTheDocument();
    expect(within(chip).getByText(/歯科薬お渡し/)).toBeInTheDocument();
    expect(within(chip).queryByText(/00:00〜00:00/)).not.toBeInTheDocument();
  });

  it('③ 週モード: 日付ラベルつきで日ごとに行が分かれる', () => {
    render(
      <EventStrip
        staffEventsByStaff={events}
        staffMap={staffMap}
        weekdays={[0, 1, 2, 3, 4, 5]}
        weekStart={WEEK_START}
        testId="week-event-strip"
      />,
    );
    const strip = screen.getByTestId('week-event-strip');
    expect(within(strip).getByText('7/21（火）')).toBeInTheDocument();
    expect(within(strip).getByText('7/25（土）')).toBeInTheDocument();
    // イベントの無い日 (月) のラベルは出ない
    expect(within(strip).queryByText('7/20（月）')).not.toBeInTheDocument();
  });

  it('④ イベントゼロなら何も描かない', () => {
    const { container } = render(
      <EventStrip
        staffEventsByStaff={new Map()}
        staffMap={staffMap}
        weekdays={[0]}
        weekStart={WEEK_START}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
