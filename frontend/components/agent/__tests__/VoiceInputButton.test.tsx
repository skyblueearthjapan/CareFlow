/**
 * 🎙 声で入力: 使えない時は出さない・押して録音 → もう一度押すと文字にして入力欄へ(送らない)・
 * ✕ でやめる・マイクの許可なし/非対応/失敗の案内・60 秒で自動で止める。
 */
import * as React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));

import { useSession } from 'next-auth/react';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';

import { Composer } from '../parts';
import { TRANSCRIBE_PATH, VoiceInputButton, resetVoiceAvailability } from '../VoiceInputButton';

class FakeRecorder {
  static instances: FakeRecorder[] = [];
  static isTypeSupported = vi.fn((m: string) => m === 'audio/mp4');
  state: 'inactive' | 'recording' = 'inactive';
  mimeType: string;
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(
    public stream: unknown,
    opts: { mimeType?: string } = {},
  ) {
    this.mimeType = opts.mimeType ?? '';
    FakeRecorder.instances.push(this);
  }
  start() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob(['abc'], { type: this.mimeType }) });
    this.onstop?.();
  }
}

const trackStop = vi.fn();
const getUserMedia = vi.fn();

function setSession() {
  (useSession as Mock).mockReturnValue({
    data: { user: { id: 'u-1', role: 'admin' }, accessToken: 't', refreshToken: 'r' },
    status: 'authenticated',
  });
}

function available(v = true) {
  (fetcher as Mock).mockImplementation((path: string, init?: { method?: string }) => {
    if (path === TRANSCRIBE_PATH && !init?.method) return Promise.resolve({ available: v });
    return Promise.resolve({ text: '田中さんが明日お休みです。' });
  });
}

beforeEach(() => {
  resetVoiceAvailability();
  setSession();
  FakeRecorder.instances = [];
  (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder;
  getUserMedia.mockResolvedValue({ getTracks: () => [{ stop: trackStop }] });
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  delete (globalThis as unknown as { MediaRecorder?: unknown }).MediaRecorder;
});

async function startRecording() {
  fireEvent.click(await screen.findByRole('button', { name: '声で入力' }));
  return screen.findByRole('button', { name: /話し終えたら押してください/ });
}

describe('VoiceInputButton', () => {
  it('サーバーが使えないと言う間は出さない', async () => {
    available(false);
    render(<VoiceInputButton onText={() => {}} />);
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: '声で入力' })).toBeNull();
  });

  it('押して録音 → もう一度押すと文字にして入力欄に入れる(自動で送らない)', async () => {
    available(true);
    const onSend = vi.fn();
    render(<Composer voice onSend={onSend} />);
    const box = screen.getByRole('textbox', { name: 'らく助に相談' }) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: '確認です' } });

    const stopBtn = await startRecording();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(FakeRecorder.instances[0]?.mimeType).toBe('audio/mp4'); // iPhone は mp4
    expect(screen.getByText(/聞いています/)).toBeInTheDocument();
    expect(stopBtn).toHaveAttribute('aria-pressed', 'true');

    // 押し間違いでないよう少し待ってから止める
    await act(async () => {
      await new Promise((r) => setTimeout(r, 650));
    });
    fireEvent.click(stopBtn);

    await waitFor(() => expect(box.value).toBe('確認です 田中さんが明日お休みです。'));
    expect(onSend).not.toHaveBeenCalled();
    const call = (fetcher as Mock).mock.calls.find((c) => c[1]?.method === 'POST');
    expect(call?.[0]).toBe(TRANSCRIBE_PATH);
    const form = call?.[1].body as FormData;
    expect(form.get('audio')).toBeInstanceOf(Blob);
    expect((form.get('audio') as File).name).toBe('voice.m4a');
    expect(call?.[1].accessToken).toBe('t');
    expect(trackStop).toHaveBeenCalled(); // マイクを手放す
    expect(screen.getByRole('button', { name: '声で入力' })).toBeEnabled();
  });

  it('✕ でやめると送らない', async () => {
    available(true);
    const onText = vi.fn();
    render(<VoiceInputButton onText={onText} />);
    await startRecording();
    fireEvent.click(screen.getByRole('button', { name: '録音をやめる' }));
    expect(screen.getByRole('button', { name: '声で入力' })).toBeInTheDocument();
    expect((fetcher as Mock).mock.calls.some((c) => c[1]?.method === 'POST')).toBe(false);
    expect(onText).not.toHaveBeenCalled();
    expect(trackStop).toHaveBeenCalled();
  });

  it('短すぎる録音は送らずに案内する', async () => {
    available(true);
    render(<VoiceInputButton onText={() => {}} />);
    fireEvent.click(await startRecording());
    expect(await screen.findByRole('alert')).toHaveTextContent('短すぎました');
    expect((fetcher as Mock).mock.calls.some((c) => c[1]?.method === 'POST')).toBe(false);
  });

  it('マイクが許可されていない時は設定の案内を出す', async () => {
    available(true);
    getUserMedia.mockRejectedValue(Object.assign(new Error('denied'), { name: 'NotAllowedError' }));
    render(<VoiceInputButton onText={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: '声で入力' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('マイクが許可されていません');
  });

  it('録音できないブラウザーではその旨を出す', async () => {
    available(true);
    delete (globalThis as unknown as { MediaRecorder?: unknown }).MediaRecorder;
    render(<VoiceInputButton onText={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: '声で入力' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('録音できません');
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it('文字にできなかった時はサーバーの言葉で案内する', async () => {
    (fetcher as Mock).mockImplementation((_p: string, init?: { method?: string }) =>
      init?.method
        ? Promise.reject(
            new ApiError('x', 429, {
              detail: '続けて使われたので、少し休ませてください（10 分に 30 回まで）。',
            }),
          )
        : Promise.resolve({ available: true }),
    );
    render(<VoiceInputButton onText={() => {}} />);
    const stopBtn = await startRecording();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 650));
    });
    fireEvent.click(stopBtn);
    expect(await screen.findByRole('alert')).toHaveTextContent('10 分に 30 回');
  });

  it('60 秒で自動で止めて文字にする', async () => {
    available(true);
    const onText = vi.fn();
    render(<VoiceInputButton onText={onText} />);
    await screen.findByRole('button', { name: '声で入力' });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await startRecording();
    await act(async () => {
      vi.advanceTimersByTime(60_500);
    });
    vi.useRealTimers();
    await waitFor(() => expect(onText).toHaveBeenCalledWith('田中さんが明日お休みです。'));
  });
});
