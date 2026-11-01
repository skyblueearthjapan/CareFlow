/**
 * 今週の予定 (モバイル) — 来週への切り替えと先の日の見え方 (mobile-future-days・PO 2026-11-01)。
 *
 * 先回りして来週まで見られる。先の日の訪問は開けるが見るだけ (詳細で操作は出ない)。
 * 今日 = 2026-10-06 (火)・今週 = 10/5〜10/11・来週 = 10/12〜10/18。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const EMPTY_QUERY = { data: [], isLoading: false, isError: false, error: null };

// `?week=last` は useSearchParams で読む (初回描画で window.location を読まない)。
const { searchWeek } = vi.hoisted(() => ({ searchWeek: { value: null as string | null } }));
vi.mock('next/navigation', () => ({
  useSearchParams: () => ({ get: (key: string) => (key === 'week' ? searchWeek.value : null) }),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: vi.fn(() => ({ data: { items: [], total: 0 } })),
}));

vi.mock('@/lib/queries/me', () => ({
  useMyVisits: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  useMyStaffEvents: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  useMyOverrides: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  currentWeekStartIso: () => '2026-10-05',
  todayIso: () => '2026-10-06',
  addDays: (iso: string, days: number) => {
    const [y, m, d] = iso.split('-').map(Number);
    const dt = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
    dt.setDate(dt.getDate() + days);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
  },
}));

import { useMyStaffEvents, useMyVisits, type MyVisit } from '@/lib/queries/me';
import MobileThisWeekPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function planned(id: string, date: string, name: string): MyVisit {
  return {
    id,
    visit_date: date,
    start_time: '10:00:00',
    end_time: '10:30:00',
    status: 'planned',
    source: 'auto',
    patient_name: name,
    actual_arrival_at: null,
    actual_departure_at: null,
  } as unknown as MyVisit;
}

beforeEach(() => {
  vi.clearAllMocks();
  searchWeek.value = null;
  window.history.replaceState({}, '', '/m/this-week');
  asMock(useMyStaffEvents).mockImplementation(() => EMPTY_QUERY);
  asMock(useMyVisits).mockImplementation((params: { weekStart?: string }) => ({
    ...EMPTY_QUERY,
    data:
      params.weekStart === '2026-10-12'
        ? [planned('nw', '2026-10-13', '来週の患者')]
        : params.weekStart === '2026-10-05'
          ? [
              planned('past', '2026-10-05', '昨日の患者'),
              planned('td', '2026-10-06', '今日の患者'),
              planned('fu', '2026-10-08', '木曜の患者'),
            ]
          : [],
  }));
});

describe('今週の予定 — 来週と先の日', () => {
  it('切り替えは 先週・今週・来週 の 3 つ', () => {
    render(<MobileThisWeekPage />);
    expect(screen.getByTestId('this-week-switch-last')).toHaveTextContent('先週');
    expect(screen.getByTestId('this-week-switch-this')).toHaveTextContent('今週');
    expect(screen.getByTestId('this-week-switch-next')).toHaveTextContent('来週');
    expect(screen.queryByTestId('this-week-next-notice')).toBeNull();
  });

  it('「来週」で次の週を引き、注意と色を出し、先の日の訪問を開ける (来週へ戻る)', () => {
    render(<MobileThisWeekPage />);
    fireEvent.click(screen.getByTestId('this-week-switch-next'));
    expect(screen.getByText('来週の予定')).toBeInTheDocument();
    expect(asMock(useMyVisits)).toHaveBeenLastCalledWith({ weekStart: '2026-10-12' });
    expect(window.location.search).toBe('?week=next');
    expect(screen.getByTestId('this-week-next-notice')).toHaveTextContent(
      '来週の予定です。今日ではありません',
    );
    expect(screen.getByTestId('this-week-section')).toHaveAttribute('data-week', 'next');
    expect(screen.getByTestId('this-week-visit-link-nw')).toHaveAttribute(
      'href',
      '/m/today/nw?from=nextweek',
    );
    expect(screen.getByTestId('this-week-day-2026-10-13')).toHaveTextContent('見るだけ');
  });

  it('URL の ?week=next から開くと来週のまま', () => {
    searchWeek.value = 'next';
    render(<MobileThisWeekPage />);
    expect(screen.getByTestId('this-week-switch-next')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('来週の患者')).toBeInTheDocument();
  });

  it('知らない ?week= は今週', () => {
    searchWeek.value = 'next2';
    render(<MobileThisWeekPage />);
    expect(asMock(useMyVisits)).toHaveBeenLastCalledWith({ weekStart: '2026-10-05' });
  });

  it('今週の中: 今日と先の日は開け、見出しで区別する。過ぎた日の実績なしは押せない', () => {
    render(<MobileThisWeekPage />);
    expect(screen.getByTestId('this-week-visit-link-td')).toHaveAttribute(
      'href',
      '/m/today/td?from=week',
    );
    expect(screen.getByTestId('this-week-visit-link-fu')).toHaveAttribute(
      'href',
      '/m/today/fu?from=week',
    );
    expect(screen.queryByTestId('this-week-visit-link-past')).toBeNull();
    const todayHeader = screen.getByTestId('this-week-day-2026-10-06').querySelector('header');
    const futureHeader = screen.getByTestId('this-week-day-2026-10-08').querySelector('header');
    expect(todayHeader).toHaveAttribute('data-day-tone', 'today');
    expect(todayHeader).toHaveTextContent('今日');
    expect(futureHeader).toHaveAttribute('data-day-tone', 'future');
    expect(futureHeader).toHaveTextContent('見るだけ');
  });
});
