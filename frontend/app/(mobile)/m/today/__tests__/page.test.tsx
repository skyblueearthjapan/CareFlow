/**
 * 今日の訪問 (一覧) — 未送信打刻の自動再送のテスト.
 *
 * 圏外で退避した打刻 (訪問詳細の到着/退出・/q の予定外) は、この一覧が
 * マウントされたときと `online` イベントで再送される。「電波が戻り次第、
 * 自動で送信します」の約束を実装で担保している箇所なので回帰させない。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';

import type * as ReactQueryModule from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1', role: 'staff' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

const qcStub = { invalidateQueries: vi.fn() };
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof ReactQueryModule>();
  return { ...actual, useQueryClient: () => qcStub };
});

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/lib/queries/me', () => ({
  useMyVisits: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  todayIso: () => '2026-08-16',
}));

import { fetcher } from '@/lib/api/fetcher';
import { enqueuePending } from '@/lib/checkin-queue';
import MobileTodayPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function enqueueOne() {
  enqueuePending('staff-1', {
    visit_id: 'visit-1',
    kind: 'arrival',
    payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK' },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe('今日の訪問 — 未送信の再送', () => {
  it('保留があればマウント時に再送し、送信できれば残らない', async () => {
    enqueueOne();
    asMock(fetcher).mockResolvedValue({});
    render(<MobileTodayPage />);
    await waitFor(() =>
      expect(asMock(fetcher)).toHaveBeenCalledWith(
        '/api/v1/visits/visit-1/checkin',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    await waitFor(() => expect(window.localStorage.getItem('checkin-pending:staff-1')).toBeNull());
  });

  it('圏外のままなら未送信バナーを出し、online で再送する', async () => {
    enqueueOne();
    // マウント時は届かない → 保留のまま (バナー表示)。
    asMock(fetcher).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    render(<MobileTodayPage />);
    await waitFor(() => expect(screen.getByTestId('today-pending-banner')).toBeInTheDocument());
    expect(screen.getByText('未送信 1 件・電波が戻ると自動で送信します')).toBeInTheDocument();

    // 電波復帰 → 自動で再送し、バナーが消える。
    asMock(fetcher).mockResolvedValue({});
    await act(async () => {
      window.dispatchEvent(new Event('online'));
    });
    await waitFor(() =>
      expect(screen.queryByTestId('today-pending-banner')).not.toBeInTheDocument(),
    );
    expect(asMock(fetcher)).toHaveBeenCalledTimes(2);
  });

  it('保留が無ければバナーを出さない', async () => {
    asMock(fetcher).mockResolvedValue({});
    render(<MobileTodayPage />);
    await waitFor(() => expect(screen.getByText('本日の訪問はありません')).toBeInTheDocument());
    expect(screen.queryByTestId('today-pending-banner')).not.toBeInTheDocument();
  });
});
