/**
 * 今週の予定 (モバイル) — 患者ステータス連動の取消は一覧に出さない。
 *
 * design 2026-09-09 §7-4 (PO 決定): source='status_cancel' + status='cancelled'
 * は消す。「今週だけ取消」= manual_cancel は打ち消し線つきで残す (現場が
 * 「今週は行かない」を知る必要があるため)。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/lib/queries/me', () => ({
  useMyVisits: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  currentWeekStartIso: () => '2026-09-07',
}));

import { useMyVisits, type MyVisit } from '@/lib/queries/me';
import MobileThisWeekPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function makeVisit(over: Partial<MyVisit> & { id: string }): MyVisit {
  return {
    visit_date: '2026-09-07',
    start_time: '09:00:00',
    end_time: '10:00:00',
    status: 'planned',
    source: 'auto',
    patient_name: `患者${over.id}`,
    ...over,
  } as unknown as MyVisit;
}

beforeEach(() => {
  vi.clearAllMocks();
});

function renderWith(visits: MyVisit[]) {
  asMock(useMyVisits).mockImplementation(() => ({
    data: visits,
    isLoading: false,
    isError: false,
    error: null,
  }));
  return render(<MobileThisWeekPage />);
}

describe('今週の予定 — status_cancel は非表示', () => {
  it('status_cancel は出さず、manual_cancel と通常訪問は出す', () => {
    renderWith([
      makeVisit({ id: 'plain' }),
      makeVisit({
        id: 'manual',
        start_time: '11:00:00',
        status: 'cancelled',
        source: 'manual_cancel',
      }),
      makeVisit({
        id: 'status',
        start_time: '13:00:00',
        status: 'cancelled',
        source: 'status_cancel',
      }),
    ]);
    expect(screen.getByText('患者plain')).toBeInTheDocument();
    expect(screen.getByText('患者manual')).toBeInTheDocument();
    expect(screen.queryByText('患者status')).not.toBeInTheDocument();
    // 日付見出しの件数からも外れる (2 件)。
    expect(screen.getByText('2件')).toBeInTheDocument();
  });

  it('その日が status_cancel だけなら日付グループごと消える', () => {
    renderWith([
      makeVisit({
        id: 'status',
        visit_date: '2026-09-08',
        status: 'cancelled',
        source: 'status_cancel',
      }),
    ]);
    expect(screen.getByText('今週の訪問はありません')).toBeInTheDocument();
  });
});
