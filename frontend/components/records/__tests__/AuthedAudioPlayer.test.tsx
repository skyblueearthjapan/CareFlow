/**
 * AuthedAudioPlayer（PC 訪問記録の音声プレーヤー）の vitest。
 *
 * 縛る挙動:
 *   1. Bearer 付きで音声を取りに行く（素の `<audio src>` では 401 になる）
 *   2. `el.duration` が非有限（ストリーミング録音の WebM）でも、記録側の
 *      `durationSec` を長さとして使う（レビュー M-4）
 *   3. 410（保持期間切れ）は理由を出す
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('@/lib/queries/visit-recordings', () => ({
  recordingAudioUrl: (id: string) => `/api/v1/visit-recordings/${id}/audio`,
}));

import { AuthedAudioPlayer } from '../AuthedAudioPlayer';

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    blob: async () => new Blob(['audio'], { type: 'audio/webm' }),
  });
  vi.stubGlobal('fetch', fetchMock);
  // jsdom は objectURL を持たない。URL ごと差し替えると静的メソッドが落ちるので、
  // 必要な 2 本だけ生やす。
  URL.createObjectURL = vi.fn(() => 'blob:mock') as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** `<audio>` の duration は jsdom で書けないので、都度定義して差し込む。 */
function setDuration(el: HTMLElement, value: number) {
  Object.defineProperty(el, 'duration', { value, configurable: true });
}

describe('AuthedAudioPlayer', () => {
  it('Bearer 付きで音声を取りに行く', async () => {
    render(<AuthedAudioPlayer recordingId="rec-1" accessToken="tok" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/visit-recordings/rec-1/audio');
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      headers: { Authorization: 'Bearer tok' },
    });
  });

  it('el.duration が非有限なら durationSec を長さに使う (M-4)', async () => {
    render(<AuthedAudioPlayer recordingId="rec-1" accessToken="tok" durationSec={125} />);
    const el = await screen.findByTestId('records-audio-element');

    setDuration(el, Infinity);
    fireEvent.loadedMetadata(el);

    // 2:05 = 125 秒。シークバーの上限も同じ値になる。
    expect(screen.getByText(/\/ 2:05$/)).toBeInTheDocument();
    expect(screen.getByLabelText('再生位置')).toHaveAttribute('max', '125');
  });

  it('el.duration が読めれば そちらを使う (M-4 のフォールバックは保険)', async () => {
    render(<AuthedAudioPlayer recordingId="rec-1" accessToken="tok" durationSec={125} />);
    const el = await screen.findByTestId('records-audio-element');

    setDuration(el, 60);
    fireEvent.loadedMetadata(el);

    expect(screen.getByText(/\/ 1:00$/)).toBeInTheDocument();
  });

  it('durationSec も el.duration も無ければ長さを --:-- にする (M-4)', async () => {
    render(<AuthedAudioPlayer recordingId="rec-1" accessToken="tok" />);
    const el = await screen.findByTestId('records-audio-element');

    setDuration(el, NaN);
    fireEvent.loadedMetadata(el);

    expect(screen.getByText(/--:--/)).toBeInTheDocument();
  });

  it('410 は保持期間切れとして理由を出す', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 410, blob: async () => new Blob() });
    render(<AuthedAudioPlayer recordingId="rec-1" accessToken="tok" />);
    expect(await screen.findByTestId('records-audio-error')).toHaveTextContent(
      '保持期間を過ぎて削除されました',
    );
  });
});
