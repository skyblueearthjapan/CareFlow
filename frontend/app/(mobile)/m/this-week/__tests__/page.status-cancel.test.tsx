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

  it('非稼働患者の予定が残っていたらバッジ + 薄色で見せる (§3-4)', () => {
    // バッジはステータスを変えた日以降にだけ出す (PO 2026-09-10)。この訪問日 (9/7) と
    // 同日に入院中へ変えた前提。
    renderWith([
      makeVisit({ id: 'residue', patient_status: 'admitted', patient_status_since: '2026-09-07' }),
    ]);
    expect(screen.getByTestId('this-week-inactive-badge-residue')).toHaveTextContent('入院中');
  });

  it('ステータス変更日より前の予定にはバッジを出さない (PO 2026-09-10)', () => {
    // 9/8 に入院中へ変えたなら、9/7 は実際に訪問した日なので従来表示のまま。
    renderWith([
      makeVisit({ id: 'past', patient_status: 'admitted', patient_status_since: '2026-09-08' }),
    ]);
    expect(screen.getByText('患者past')).toBeInTheDocument();
    expect(screen.queryByTestId('this-week-inactive-badge-past')).not.toBeInTheDocument();
  });

  it('稼働中の予定にはバッジを出さない', () => {
    renderWith([makeVisit({ id: 'plain', patient_status: 'active' })]);
    expect(screen.queryByTestId('this-week-inactive-badge-plain')).not.toBeInTheDocument();
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
