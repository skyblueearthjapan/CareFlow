/**
 * 打刻履歴の詳細 — サインで記録 (signature-checkin-design-2026-10-06 §5-1・モック場面 5)。
 *
 * - 退出がサインの訪問: 到着 =「QRなし（サイン）」・退出 =「サイン」・「サインを見る」。
 * - ただの QR なし (QR 忘れ) は今までどおり「手入力の時刻」「QR なし（手入力）」。
 * - 「サインを見る」は押したときだけ画像を取りに行く (見るたびに監査ログに残る)。
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import type { VisitHistoryRow } from '@/lib/queries/visit-history';

vi.mock('@/lib/queries/checkinSettings', () => ({
  useCheckinSettingsPublic: () => ({ data: undefined }),
}));
vi.mock('@/components/records/ActualTimeAdjustBox', () => ({
  ActualTimeAdjustBox: () => <div data-testid="adjust-box" />,
}));
vi.mock('@/components/records/SignatureViewer', () => ({
  SignatureViewer: ({ signatureId, title }: { signatureId: string | null; title: string }) =>
    signatureId ? (
      <div data-testid="signature-viewer">
        {title}:{signatureId}
      </div>
    ) : null,
}));

import { VisitHistoryDetailDialog } from '../_components/VisitHistoryDetailDialog';

function row(overrides: Partial<VisitHistoryRow> = {}): VisitHistoryRow {
  return {
    visit_id: 'v-1',
    visit_date: '2026-10-06',
    office_id: null,
    office_name: '稲毛',
    patient_id: 'p-1',
    patient_name: '山田 花子',
    start_time: '13:00',
    end_time: '13:40',
    planned_staff_id: 's-1',
    planned_staff_name: '佐々木 美咲',
    actual_staff_id: 's-1',
    actual_staff_name: '佐々木 美咲',
    arrival_at: '2026-10-06T04:02:00Z',
    departure_at: '2026-10-06T04:38:00Z',
    arrival_read_at: '2026-10-06T04:02:00Z',
    departure_read_at: '2026-10-06T04:38:00Z',
    arrival_adjusted: false,
    departure_adjusted: false,
    adjustments: [],
    stay_minutes: 36,
    checkin_source: 'manual',
    match_status: 'match',
    departure_source: 'signature',
    signature_id: 'sig-1',
    is_substitute: false,
    is_unplanned: false,
    is_cancelled: false,
    state: 'done',
    remarks: ['QRなし（サイン）', 'サイン'],
    ...overrides,
  } as VisitHistoryRow;
}

describe('VisitHistoryDetailDialog — サイン', () => {
  it('到着 =「QRなし（サイン）」・退出 =「サイン」・備考の印', () => {
    render(<VisitHistoryDetailDialog row={row()} onClose={vi.fn()} />);
    const dialog = screen.getByTestId('history-detail-dialog');
    expect(dialog).toHaveTextContent('QRなし（サイン）');
    expect(dialog).toHaveTextContent('到着 QRなし（サイン）・退出 サイン');
    expect(screen.getByTestId('history-detail-remarks')).toHaveTextContent(
      'QRなし（サイン）、サイン',
    );
  });

  it('「サインを見る」は押したときだけ開く', () => {
    render(<VisitHistoryDetailDialog row={row()} onClose={vi.fn()} />);
    expect(screen.queryByTestId('signature-viewer')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('history-detail-view-signature'));
    expect(screen.getByTestId('signature-viewer')).toHaveTextContent('sig-1');
    expect(screen.getByTestId('signature-viewer')).toHaveTextContent('山田 花子 様');
  });

  it('別の行に切り替えても、その行の画像を勝手に開かない (見た記録を増やさない)', () => {
    const { rerender } = render(<VisitHistoryDetailDialog row={row()} onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId('history-detail-view-signature'));
    expect(screen.getByTestId('signature-viewer')).toHaveTextContent('sig-1');
    rerender(
      <VisitHistoryDetailDialog
        row={row({ visit_id: 'v-2', signature_id: 'sig-2' })}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('signature-viewer')).not.toBeInTheDocument();
  });

  it('ただの QR なしは今までどおり (サインの印も「サインを見る」も出ない)', () => {
    render(
      <VisitHistoryDetailDialog
        row={row({
          departure_source: 'qr',
          signature_id: null,
          remarks: ['QRなし'],
        })}
        onClose={vi.fn()}
      />,
    );
    const dialog = screen.getByTestId('history-detail-dialog');
    expect(dialog).toHaveTextContent('手入力の時刻');
    expect(dialog).toHaveTextContent('QR なし（手入力）');
    expect(dialog).not.toHaveTextContent('QRなし（サイン）');
    expect(screen.queryByTestId('history-detail-view-signature')).not.toBeInTheDocument();
  });
});
