/**
 * 認証付き音声プレーヤー (`AuthedAudio`) のテスト。
 *
 * 要点は 1 つ (レビュー M-2): **トークンが変わっても取り直さない**。
 * NextAuth は 55 分ごとにセッションを更新するので、`accessToken` を effect の
 * 依存に入れると再生中に blob を取り直し、再生位置が頭に戻る。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('@/lib/queries/visit-recordings', () => ({
  recordingAudioUrl: (id: string) => `/api/v1/visit-recordings/${id}/audio`,
}));

import { AuthedAudio } from '@/components/mobile/AuthedAudio';

function mockFetch(status = 200) {
  const fn = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    blob: async () => new Blob(['audio'], { type: 'audio/webm' }),
  }));
  (globalThis as unknown as { fetch: unknown }).fetch = fn;
  return fn;
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom は objectURL を持たない。
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:mock'),
  });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
});

describe('AuthedAudio', () => {
  it('Authorization 付きで取得して再生できる形にする', async () => {
    const fetchMock = mockFetch();
    render(<AuthedAudio recordingId="rec-1" accessToken="token-1" />);

    await waitFor(() => expect(screen.getByTestId('authed-audio')).toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/visit-recordings/rec-1/audio');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-1');
  });

  it('トークンが更新されても取り直さない (M-2)', async () => {
    const fetchMock = mockFetch();
    const { rerender } = render(<AuthedAudio recordingId="rec-1" accessToken="token-1" />);
    await waitFor(() => expect(screen.getByTestId('authed-audio')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(1);

    rerender(<AuthedAudio recordingId="rec-1" accessToken="token-2" />);

    await waitFor(() => expect(screen.getByTestId('authed-audio')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('別の録音に変われば取り直す', async () => {
    const fetchMock = mockFetch();
    const { rerender } = render(<AuthedAudio recordingId="rec-1" accessToken="token-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    rerender(<AuthedAudio recordingId="rec-2" accessToken="token-1" />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('保持期間を過ぎた音声 (410) は理由を出す', async () => {
    mockFetch(410);
    render(<AuthedAudio recordingId="rec-1" accessToken="token-1" />);

    expect(await screen.findByText('音声は保持期間を過ぎて削除されました')).toBeInTheDocument();
  });
});
