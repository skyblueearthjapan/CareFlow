/**
 * `/q/{token}` ランディングページ — ディープリンク解決のレンダーテスト.
 *
 *   1. 単一候補 → `/m/today/{visitId}?qr={token}` へ replace (extractQrToken が
 *      パス断片の生トークンをそのまま通すことの結線確認)。
 *   2. 候補ゼロ → 案内表示 (患者情報なし)。
 *   3. 410 (失効) → 「QRが更新されています」。
 *   4. pickQrCandidate 単体 — completed スキップの選択規則。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError } from '@/lib/api-client';
import { pickQrCandidate, type QrResolveCandidate } from '@/lib/queries/qrResolve';

// --- module mocks ----------------------------------------------------------
let tokenParam = 'TOK123';
const routerReplace = vi.fn();
vi.mock('next/navigation', () => ({
  useParams: () => ({ token: tokenParam }),
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1', role: 'staff' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

import { fetcher } from '@/lib/api/fetcher';
import QrLandingPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <QrLandingPage />
    </QueryClientProvider>,
  );
}

function cand(visit_id: string, status = 'planned'): QrResolveCandidate {
  return { visit_id, start_time: '09:00:00', end_time: '10:00:00', status };
}

beforeEach(() => {
  vi.clearAllMocks();
  tokenParam = 'TOK123';
});

describe('/q/{token} ランディング', () => {
  it('単一候補は /m/today/{visitId}?qr={token} へ replace する', async () => {
    asMock(fetcher).mockResolvedValue({ candidates: [cand('visit-9')] });
    renderPage();
    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-9?qr=TOK123'));
    // API へはパス断片のトークンがそのまま渡る (extractQrToken 結線)。
    expect(asMock(fetcher).mock.calls[0][0]).toBe('/api/v1/visits/resolve-qr/TOK123');
  });

  it('候補ゼロは案内を表示し遷移しない', async () => {
    asMock(fetcher).mockResolvedValue({ candidates: [] });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('本日の担当訪問が見つかりません')).toBeInTheDocument(),
    );
    expect(routerReplace).not.toHaveBeenCalled();
  });

  it('410 (ローテ済) は「QRが更新されています」を表示する', async () => {
    asMock(fetcher).mockRejectedValue(new ApiError('gone', 410, null));
    renderPage();
    await waitFor(() => expect(screen.getByText('QRが更新されています')).toBeInTheDocument());
    expect(routerReplace).not.toHaveBeenCalled();
  });
});

describe('pickQrCandidate', () => {
  it('completed 以外の最初 → 全 completed なら先頭 → 空は null', () => {
    expect(pickQrCandidate([])).toBeNull();
    expect(pickQrCandidate([cand('a', 'completed'), cand('b'), cand('c')])?.visit_id).toBe('b');
    expect(pickQrCandidate([cand('a', 'completed'), cand('b', 'completed')])?.visit_id).toBe('a');
  });
});
