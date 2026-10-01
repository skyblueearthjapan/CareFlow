/**
 * 統合プレビュー (訪問の読み込み) のバックグラウンド化 — useSmartInboundPreview の vitest
 * (契約 = docs/plans/smart-preview-async-2026-10-01.md)。
 *
 * 縛る挙動:
 *   1. start (202) → status を 3 秒ごとにポーリングし、completed で preview を返す
 *   2. failed なら BE の日本語の理由で失敗する
 *   3. resumeJobId を渡すと start せず、そのジョブの待ち受けだけをする (画面へ戻ったとき)
 *   4. 一時的なポーリング失敗は 2 回まで許す
 *   5. 画面を離れる (アンマウント) と待ち受けをやめ、detached の印で終わる
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { mockFetcher } = vi.hoisted(() => ({ mockFetcher: vi.fn() }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: (...a: unknown[]) => mockFetcher(...a) }));

import { isSmartPreviewDetached } from '@/lib/kaipokeOps';

import { useSmartInboundPreview } from '../integrations';

const JOB_ID = '11111111-1111-4111-8111-111111111111';
const WEEK = '2026-10-26';
const START_URL = '/api/v1/integrations/smart-inbound-preview/start';
const STATUS_URL = `/api/v1/integrations/smart-inbound-preview/status/${JOB_ID}`;

const PREVIEW = {
  weekStart: WEEK,
  weekEnd: '2026-10-31',
  protectedDays: [],
  replaceDays: [WEEK],
  sheetId: null,
  diffSummary: {},
  replace: null,
};

const running = { jobId: JOB_ID, weekStart: WEEK, status: 'running' };

let qc: QueryClient;

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function paths(): string[] {
  return mockFetcher.mock.calls.map((c) => c[0] as string);
}

beforeEach(() => {
  vi.useFakeTimers();
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  mockFetcher.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useSmartInboundPreview (start → status ポーリング)', () => {
  it('start で jobId を受け取り、3 秒ごとに status を見て completed の preview を返す', async () => {
    mockFetcher.mockImplementation(async (path: string) => {
      if (path === START_URL) return { jobId: JOB_ID, status: 'running' };
      const n = paths().filter((p) => p === STATUS_URL).length;
      return n < 3 ? running : { ...running, status: 'completed', preview: PREVIEW };
    });
    const { result } = renderHook(() => useSmartInboundPreview(), { wrapper });

    const p = result.current.mutateAsync({ weekStart: WEEK });
    await vi.advanceTimersByTimeAsync(0);
    expect(paths()).toEqual([START_URL, STATUS_URL]);
    const [, startInit] = mockFetcher.mock.calls[0] as [string, Record<string, unknown>];
    expect(startInit.method).toBe('POST');
    expect(JSON.parse(startInit.body as string)).toEqual({ weekStart: WEEK });

    await vi.advanceTimersByTimeAsync(2_999);
    expect(paths()).toHaveLength(2); // 3 秒経つまでは次を撃たない
    await vi.advanceTimersByTimeAsync(1);
    expect(paths()).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(3_000);

    await expect(p).resolves.toEqual(PREVIEW);
    expect(paths()).toEqual([START_URL, STATUS_URL, STATUS_URL, STATUS_URL]);
  });

  it('failed なら BE の理由で失敗する', async () => {
    mockFetcher.mockImplementation(async (path: string) =>
      path === START_URL
        ? { jobId: JOB_ID, status: 'running' }
        : {
            ...running,
            status: 'failed',
            error: 'カイポケが別の処理を実行中のため、読み込みを中断しました。',
          },
    );
    const { result } = renderHook(() => useSmartInboundPreview(), { wrapper });

    const p = result.current.mutateAsync({ weekStart: WEEK });
    const assertion = expect(p).rejects.toThrow('別の処理を実行中');
    await vi.advanceTimersByTimeAsync(0);
    await assertion;
  });

  it('start が 409 (二重起動・RPA 実行中) なら、そのまま失敗する', async () => {
    mockFetcher.mockRejectedValue(new Error('API 409 Conflict'));
    const { result } = renderHook(() => useSmartInboundPreview(), { wrapper });

    await expect(result.current.mutateAsync({ weekStart: WEEK })).rejects.toThrow('409');
    expect(paths()).toEqual([START_URL]);
  });

  it('resumeJobId を渡すと start せず、そのジョブの status だけを待つ', async () => {
    mockFetcher.mockResolvedValue({ ...running, status: 'completed', preview: PREVIEW });
    const { result } = renderHook(() => useSmartInboundPreview(), { wrapper });

    const p = result.current.mutateAsync({ weekStart: WEEK, resumeJobId: JOB_ID });
    await vi.advanceTimersByTimeAsync(0);
    await expect(p).resolves.toEqual(PREVIEW);
    expect(paths()).toEqual([STATUS_URL]);
  });

  it('一時的なポーリング失敗は 2 回まで許し、3 回続けば失敗にする', async () => {
    let n = 0;
    mockFetcher.mockImplementation(async (path: string) => {
      if (path === START_URL) return { jobId: JOB_ID, status: 'running' };
      n += 1;
      if (n <= 2) throw new Error('network');
      return { ...running, status: 'completed', preview: PREVIEW };
    });
    const { result } = renderHook(() => useSmartInboundPreview(), { wrapper });
    const p = result.current.mutateAsync({ weekStart: WEEK });
    await vi.advanceTimersByTimeAsync(6_000);
    await expect(p).resolves.toEqual(PREVIEW);

    mockFetcher.mockReset();
    mockFetcher.mockImplementation(async (path: string) => {
      if (path === START_URL) return { jobId: JOB_ID, status: 'running' };
      throw new Error('network down');
    });
    const p2 = result.current.mutateAsync({ weekStart: WEEK });
    const assertion = expect(p2).rejects.toThrow('network down');
    await vi.advanceTimersByTimeAsync(6_000);
    await assertion;
  });

  it('画面を離れる (アンマウント) と待ち受けをやめ、detached の印で終わる', async () => {
    mockFetcher.mockImplementation(async (path: string) =>
      path === START_URL ? { jobId: JOB_ID, status: 'running' } : running,
    );
    const { result, unmount } = renderHook(() => useSmartInboundPreview(), { wrapper });

    const p = result.current.mutateAsync({ weekStart: WEEK });
    const settled = p.then(
      () => null,
      (e: unknown) => e,
    );
    await vi.advanceTimersByTimeAsync(0);
    unmount();
    await vi.advanceTimersByTimeAsync(3_000);

    const err = await settled;
    expect(isSmartPreviewDetached(err)).toBe(true);
    // 離脱後は status を撃たない (サーバーのジョブはそのまま続く)。
    expect(paths()).toEqual([START_URL, STATUS_URL]);
  });
});
