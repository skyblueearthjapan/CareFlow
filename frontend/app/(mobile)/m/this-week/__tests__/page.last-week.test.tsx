/**
 * 今週の予定 (モバイル) — 先週への切り替え (pc-actual-time-edit-design-2026-10-06 Q5)。
 *
 * 本人が 7 日以内の訪問の時刻を合わせられるように、先週の訪問も開けるようにする。
 * 今週の表示は従来どおり。
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

const lastWeekVisit = {
  id: 'lw',
  visit_date: '2026-10-02',
  start_time: '13:00:00',
  end_time: '13:35:00',
  status: 'in_progress',
  source: 'auto',
  patient_name: '先週の患者',
  actual_arrival_at: '2026-10-02T04:13:00Z',
  actual_departure_at: null,
} as unknown as MyVisit;

beforeEach(() => {
  vi.clearAllMocks();
  searchWeek.value = null;
  window.history.replaceState({}, '', '/m/this-week');
  asMock(useMyStaffEvents).mockImplementation(() => EMPTY_QUERY);
  asMock(useMyVisits).mockImplementation((params: { weekStart?: string }) => ({
    ...EMPTY_QUERY,
    data: params.weekStart === '2026-09-28' ? [lastWeekVisit] : [],
  }));
});

describe('今週の予定 — 先週への切り替え', () => {
  it('既定は今週 (従来どおり)', () => {
    render(<MobileThisWeekPage />);
    expect(screen.getByText('今週の予定')).toBeInTheDocument();
    expect(screen.getByTestId('this-week-switch-this')).toHaveAttribute('aria-pressed', 'true');
    expect(asMock(useMyVisits)).toHaveBeenLastCalledWith({ weekStart: '2026-10-05' });
    expect(screen.getByText('今週の訪問はありません')).toBeInTheDocument();
  });

  it('「先週」で前の週を引き、終わった訪問から詳細 (先週へ戻る) を開ける', () => {
    render(<MobileThisWeekPage />);
    fireEvent.click(screen.getByTestId('this-week-switch-last'));
    expect(screen.getByText('先週の予定')).toBeInTheDocument();
    expect(asMock(useMyVisits)).toHaveBeenLastCalledWith({ weekStart: '2026-09-28' });
    expect(window.location.search).toBe('?week=last');
    // 過ぎた日の到着だけの訪問は「退出なし」で、押すと詳細へ (7 日以内なら合わせられる)。
    expect(screen.getByTestId('this-week-actual-lw')).toHaveTextContent('退出なし');
    expect(screen.getByTestId('this-week-visit-link-lw')).toHaveAttribute(
      'href',
      '/m/today/lw?from=lastweek',
    );

    fireEvent.click(screen.getByTestId('this-week-switch-this'));
    expect(screen.getByText('今週の予定')).toBeInTheDocument();
    expect(window.location.search).toBe('');
  });

  it('URL の ?week=last から開くと先週のまま', () => {
    searchWeek.value = 'last';
    render(<MobileThisWeekPage />);
    expect(screen.getByTestId('this-week-switch-last')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('先週の患者')).toBeInTheDocument();
  });
});
