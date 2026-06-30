/** /monitor ページ: RBAC リダイレクト + 基本描画。
 *
 * Leaflet は jsdom 非対応のため MonitorMap (dynamic import 本体) をモックする。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
}));

const useSession = vi.fn();
vi.mock('next-auth/react', () => ({
  useSession: () => useSession(),
}));

const useMonitor = vi.fn();
const useNearbyPatients = vi.fn(() => ({ data: { items: [] } }));
const mutate = vi.fn();
const useReviewVisit = vi.fn(() => ({ mutate, isPending: false }));
const useUnreviewVisit = vi.fn(() => ({ mutate, isPending: false }));
vi.mock('@/lib/queries/monitor', () => ({
  useMonitor: (...a: unknown[]) => useMonitor(...a),
  useNearbyPatients: (...a: unknown[]) => useNearbyPatients(...a),
  useReviewVisit: () => useReviewVisit(),
  useUnreviewVisit: () => useUnreviewVisit(),
}));

// Leaflet を引き込む地図はモック。
vi.mock('@/components/monitor/MonitorMap', () => ({
  MonitorMap: () => <div data-testid="mock-map" />,
}));

import MonitorPage from '../page';

beforeEach(() => {
  replace.mockClear();
  useMonitor.mockReturnValue({ data: null, isLoading: false, isError: false });
});

describe('MonitorPage RBAC', () => {
  it('staff ロールは /dashboard へリダイレクトし本体を描画しない', () => {
    useSession.mockReturnValue({ data: { user: { role: 'staff' } }, status: 'authenticated' });
    const { container } = render(<MonitorPage />);
    expect(replace).toHaveBeenCalledWith('/dashboard');
    expect(container.textContent).toBe('');
  });

  it('manager ロールはモニター本体を描画する', () => {
    useSession.mockReturnValue({ data: { user: { role: 'manager' } }, status: 'authenticated' });
    useMonitor.mockReturnValue({
      data: {
        date: '2026-06-30',
        now: '2026-06-30T04:30:00Z',
        thresholds: {
          match_m: 100,
          review_m: 300,
          accuracy_m: 50,
          no_show_grace_min: 20,
          late_min: 15,
          max_inprogress_min: 240,
        },
        offices: [],
        staff: [],
      },
      isLoading: false,
      isError: false,
    });
    render(<MonitorPage />);
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText('訪問モニター')).toBeInTheDocument();
  });
});
