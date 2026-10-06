/**
 * 訪問モニター — サインで記録した退出の印と「サインを見る」
 * (signature-checkin-design-2026-10-06 §5-1)。
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: () => ({ data: { items: [], total: 0 }, isLoading: false }),
  useVisitRecording: () => ({ data: null, isLoading: false }),
  useUpdateRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRetryRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  recordingAudioUrl: (id: string) => `/api/v1/visit-recordings/${id}/audio`,
}));
vi.mock('@/components/records/SignatureViewer', () => ({
  SignatureViewer: ({ signatureId }: { signatureId: string | null }) =>
    signatureId ? <div data-testid="signature-viewer">{signatureId}</div> : null,
}));

import { MonitorDetailPanel } from '../MonitorDetailPanel';
import { makeRow, makeVisit } from './fixtures';

function checkin(kind: 'arrival' | 'departure', source: string, signatureId: string | null = null) {
  return {
    kind,
    scanned_at: kind === 'arrival' ? '2026-10-06T04:02:00Z' : '2026-10-06T04:38:00Z',
    match_status: 'match',
    is_override: false,
    checkin_source: source,
    signature_id: signatureId,
  };
}

describe('MonitorDetailPanel — サイン', () => {
  it('退出がサインなら記録の方法と「サインを見る」(押したときだけ開く)', () => {
    const v = makeVisit({
      arrival: checkin('arrival', 'manual'),
      departure: checkin('departure', 'signature', 'sig-9'),
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-detail-visit')).toHaveTextContent(
      '到着 QRなし（サイン）・退出 サイン',
    );
    expect(screen.queryByTestId('signature-viewer')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('monitor-view-signature'));
    expect(screen.getByTestId('signature-viewer')).toHaveTextContent('sig-9');
  });

  it('到着が QR なら「QR」、到着の記録が無ければ「記録なし」', () => {
    const qr = makeVisit({
      arrival: checkin('arrival', 'qr'),
      departure: checkin('departure', 'signature', 'sig-1'),
    });
    const { unmount } = render(
      <MonitorDetailPanel visit={qr} row={makeRow({ visits: [qr] })} onSelectVisit={vi.fn()} />,
    );
    expect(screen.getByTestId('monitor-detail-visit')).toHaveTextContent('到着 QR・退出 サイン');
    unmount();
    const none = makeVisit({
      arrival: null,
      departure: checkin('departure', 'signature', 'sig-2'),
    });
    render(
      <MonitorDetailPanel visit={none} row={makeRow({ visits: [none] })} onSelectVisit={vi.fn()} />,
    );
    expect(screen.getByTestId('monitor-detail-visit')).toHaveTextContent(
      '到着 記録なし・退出 サイン',
    );
  });

  it('QR の退出には出さない', () => {
    const v = makeVisit({
      arrival: checkin('arrival', 'qr'),
      departure: checkin('departure', 'qr'),
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.queryByTestId('monitor-view-signature')).not.toBeInTheDocument();
    expect(screen.getByTestId('monitor-detail-visit')).not.toHaveTextContent('退出 サイン');
  });
});
