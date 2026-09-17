/**
 * VisitRecordingLink（訪問モニター詳細パネルの「🎙 記録を見る」）の vitest。
 *
 * 縛る挙動:
 *   1. その訪問に記録が無ければ**何も描かない**（空のリンクで期待を持たせない）
 *   2. あれば 1 件目の詳細ダイアログを開ける
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { mockUseVisitRecordings } = vi.hoisted(() => ({ mockUseVisitRecordings: vi.fn() }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { role: 'admin', staffId: 'st-1' }, accessToken: 'tok' },
    status: 'authenticated',
  }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: (...a: unknown[]) => mockUseVisitRecordings(...a),
  useVisitRecording: () => ({ data: null, isLoading: false }),
  useUpdateRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRetryRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  recordingAudioUrl: (id: string) => `/api/v1/visit-recordings/${id}/audio`,
}));

vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: [] }, isLoading: false }),
}));

vi.mock('@/components/records/AuthedAudioPlayer', () => ({
  AuthedAudioPlayer: () => <div data-testid="records-audio-player" />,
}));

import { VisitRecordingLink } from '../VisitRecordingLink';

beforeEach(() => {
  mockUseVisitRecordings.mockReset();
});

describe('VisitRecordingLink', () => {
  it('記録が無ければ何も描かない', () => {
    mockUseVisitRecordings.mockReturnValue({ data: { items: [], total: 0 }, isLoading: false });
    const { container } = render(<VisitRecordingLink visitId="v-1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('記録があればリンクを出し、クリックで 1 件目の詳細を開く', () => {
    mockUseVisitRecordings.mockReturnValue({
      data: {
        items: [{ id: 'rec-1', recorded_at: '2026-09-17T05:08:00Z', status: 'summarized' }],
        total: 1,
      },
      isLoading: false,
    });
    render(<VisitRecordingLink visitId="v-1" />);
    const link = screen.getByTestId('monitor-recording-link');
    expect(mockUseVisitRecordings.mock.calls[0]?.[0]).toMatchObject({ visitId: 'v-1' });
    fireEvent.click(link);
    expect(screen.getByTestId('record-detail-dialog')).toBeInTheDocument();
  });
});
