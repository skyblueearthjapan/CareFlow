/**
 * MemoChipsRow — 📝 メモチップ (ゼロ長イベント) の描画契約 (E-3)。
 *
 * ① start==end のイベントだけがチップ化される (通常イベントは帯側の責務)
 * ② タイトルとツールチップ (title 属性) に全文が入る
 * ③ メモが無ければ何も描かない
 */
import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { MemoChipsRow } from '../TimelineDayBoard';
import type { EventRead } from '@/lib/schemas/staff-events';

function ev(partial: Partial<EventRead> & { id: string }): EventRead {
  return {
    date: '2026-07-25',
    title: '',
    start_time: '09:00',
    end_time: '10:00',
    type: 'イベント',
    note: null,
    ...partial,
  } as EventRead;
}

describe('MemoChipsRow', () => {
  it('① ゼロ長イベントのみチップ化する', () => {
    render(
      <MemoChipsRow
        colKey="c1"
        events={[
          ev({
            id: '11111111-1111-4111-8111-111111111111',
            title: '清水様：歯科薬お渡し',
            start_time: '00:00',
            end_time: '00:00',
            note: 'カイポケ個別業務取込 2026-07-25',
          }),
          ev({ id: '22222222-2222-4222-8222-222222222222', title: '通常イベント' }),
        ]}
      />,
    );
    expect(screen.getByTestId('tl-memos-c1')).toBeInTheDocument();
    expect(screen.getByText('清水様：歯科薬お渡し')).toBeInTheDocument();
    // 通常イベント (帯側で描画される) はチップにしない
    expect(screen.queryByText('通常イベント')).not.toBeInTheDocument();
  });

  it('② ツールチップに全文と出所が入る', () => {
    render(
      <MemoChipsRow
        colKey="c1"
        events={[
          ev({
            id: '11111111-1111-4111-8111-111111111111',
            title: '渡辺様：COCOLO3号館',
            start_time: '00:00',
            end_time: '00:00',
          }),
        ]}
      />,
    );
    const chip = screen.getByTestId('tl-memo-c1-11111111-1111-4111-8111-111111111111');
    expect(chip.getAttribute('title')).toContain('渡辺様：COCOLO3号館');
    expect(chip.getAttribute('title')).toContain('時間を持たない予定');
  });

  it('③ メモが無ければ何も描かない', () => {
    const { container } = render(
      <MemoChipsRow
        colKey="c1"
        events={[ev({ id: '22222222-2222-4222-8222-222222222222', title: '通常イベント' })]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
