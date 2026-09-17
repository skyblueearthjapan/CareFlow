/**
 * VisitRecordsCard（患者詳細・スタッフ詳細の「訪問記録」カード）の vitest。
 *
 * 縛る挙動:
 *   1. 直近 5 件だけ引く（新しい順）
 *   2. 患者から使えば `/records?patient=`、スタッフから使えば `/records?staff=`
 *   3. 0 件は らく助の空状態
 *   4. 行クリックで詳細ダイアログが開く
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

// 実モジュールを土台に、フックだけ差し替える（共有ファクトリ・レビュー H-1）。
vi.mock('@/lib/queries/visit-recordings', async (importOriginal) => {
  const { visitRecordingsMock } = await import('./visitRecordingsMock');
  return visitRecordingsMock(importOriginal, {
    useVisitRecordings: (...a: unknown[]) => mockUseVisitRecordings(...a),
  });
});

vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: [] }, isLoading: false }),
}));

vi.mock('@/components/records/AuthedAudioPlayer', () => ({
  AuthedAudioPlayer: () => <div data-testid="records-audio-player" />,
}));

import { VisitRecordsCard } from '../VisitRecordsCard';

const REC = {
  id: 'rec-1',
  patient_id: 'p-1',
  patient_name: '山田 花子',
  staff_id: 'st-1',
  staff_name: '川名 幸子',
  recorded_at: '2026-09-17T05:08:00Z',
  status: 'summarized',
  summary_text: '「足が重い」との訴え。',
  has_audio: true,
  reviewed_at: null,
};

beforeEach(() => {
  mockUseVisitRecordings.mockReset();
  mockUseVisitRecordings.mockReturnValue({ data: { items: [REC], total: 12 }, isLoading: false });
});

describe('VisitRecordsCard', () => {
  it('患者詳細: 直近 5 件を新しい順で引き、すべて見るは /records?patient=', () => {
    render(<VisitRecordsCard patientId="p-1" />);
    const params = mockUseVisitRecordings.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(params.patientId).toBe('p-1');
    expect(params.staffId).toBeNull();
    expect(params.limit).toBe(5);
    expect(params.order).toBe('recorded_at_desc');
    expect(screen.getByTestId('visit-records-see-all')).toHaveAttribute(
      'href',
      '/records?patient=p-1',
    );
    expect(screen.getByTestId('visit-records-see-all')).toHaveTextContent('すべて見る（12件）');
  });

  it('スタッフ詳細: staffId で引き、すべて見るは /records?staff=', () => {
    render(<VisitRecordsCard staffId="st-1" />);
    const params = mockUseVisitRecordings.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(params.staffId).toBe('st-1');
    expect(params.patientId).toBeNull();
    expect(screen.getByTestId('visit-records-see-all')).toHaveAttribute(
      'href',
      '/records?staff=st-1',
    );
  });

  it('患者詳細は要約 1 行目、スタッフ詳細は患者名を出す', () => {
    const { unmount } = render(<VisitRecordsCard patientId="p-1" />);
    expect(screen.getByText('「足が重い」との訴え。')).toBeInTheDocument();
    unmount();

    render(<VisitRecordsCard staffId="st-1" />);
    expect(screen.getByText('山田 花子')).toBeInTheDocument();
  });

  it('0 件は らく助の空状態', () => {
    mockUseVisitRecordings.mockReturnValue({ data: { items: [], total: 0 }, isLoading: false });
    render(<VisitRecordsCard patientId="p-1" />);
    expect(screen.getByText('まだ訪問記録はありません')).toBeInTheDocument();
  });

  it('行クリックで詳細ダイアログが開く', () => {
    render(<VisitRecordsCard patientId="p-1" />);
    expect(screen.queryByTestId('record-detail-dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('visit-records-row-rec-1'));
    expect(screen.getByTestId('record-detail-dialog')).toBeInTheDocument();
  });
});
