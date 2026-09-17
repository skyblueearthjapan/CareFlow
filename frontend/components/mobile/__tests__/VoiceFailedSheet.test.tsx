/**
 * 送れなかった録音の一覧 (`VoiceFailedSheet`) のテスト。
 *
 * 守りたい約束 (レビュー C-3): 4xx で外した録音は**消えていない**。
 *   - 理由が読める
 *   - 「再送」でキューへ戻して送り直せる
 *   - 「削除」は確認を挟む (唯一の不可逆操作)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1' }, accessToken: 'token', refreshToken: 'refresh' },
    status: 'authenticated',
  }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/lib/voice/queue', () => ({
  audioFileName: () => 'recording.webm',
  listFailedVoice: vi.fn(),
  removeFailedVoice: vi.fn(async () => undefined),
  requeueFailedVoice: vi.fn(async () => true),
  flushVoiceQueue: vi.fn(async () => ({ sent: 1, remaining: 0, dropped: [], failed: 0 })),
}));

import {
  flushVoiceQueue,
  listFailedVoice,
  removeFailedVoice,
  requeueFailedVoice,
  type FailedVoice,
} from '@/lib/voice/queue';
import { VoiceFailedSheet } from '@/components/mobile/VoiceFailedSheet';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function makeFailed(over: Partial<FailedVoice> = {}): FailedVoice {
  return {
    id: 'fail-1',
    staffId: 'staff-1',
    visitId: 'visit-1',
    patientId: 'pat-1',
    recordedAt: '2026-09-17T14:08:00.000Z',
    durationSec: 600,
    mimeType: 'audio/webm;codecs=opus',
    blob: new Blob(['audio']),
    consent: true,
    attempts: 1,
    queuedAt: Date.parse('2026-09-17T14:10:00.000Z'),
    droppedAt: Date.parse('2026-09-17T14:12:00.000Z'),
    reason: '対象の訪問が見つからないため',
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  asMock(listFailedVoice).mockImplementation(async () => [makeFailed()]);
  asMock(requeueFailedVoice).mockImplementation(async () => true);
  asMock(flushVoiceQueue).mockImplementation(async () => ({
    sent: 1,
    remaining: 0,
    dropped: [],
    failed: 0,
  }));
});

function renderSheet(onChanged = vi.fn()) {
  render(<VoiceFailedSheet open onOpenChange={vi.fn()} onChanged={onChanged} />);
  return onChanged;
}

describe('VoiceFailedSheet', () => {
  it('理由と操作を出す', async () => {
    renderSheet();

    expect(await screen.findByTestId('voice-failed-fail-1')).toBeInTheDocument();
    expect(screen.getByText('対象の訪問が見つからないため')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /再送/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /端末に保存/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /削除/ })).toBeInTheDocument();
  });

  it('「再送」でキューへ戻して送る', async () => {
    const onChanged = renderSheet();
    fireEvent.click(await screen.findByRole('button', { name: /再送/ }));

    await waitFor(() => expect(requeueFailedVoice).toHaveBeenCalledWith('fail-1'));
    expect(flushVoiceQueue).toHaveBeenCalledWith('staff-1', {
      accessToken: 'token',
      refreshToken: 'refresh',
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('「削除」は確認を挟む (一度目では消えない)', async () => {
    renderSheet();
    fireEvent.click(await screen.findByRole('button', { name: /削除$/ }));

    expect(
      await screen.findByText('削除すると元に戻せません。よろしいですか？'),
    ).toBeInTheDocument();
    expect(removeFailedVoice).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '削除する' }));
    await waitFor(() => expect(removeFailedVoice).toHaveBeenCalledWith('fail-1'));
  });

  it('1 件も無ければその旨を出す', async () => {
    asMock(listFailedVoice).mockImplementation(async () => []);
    renderSheet();

    expect(await screen.findByText('送れなかった録音はありません')).toBeInTheDocument();
  });
});
