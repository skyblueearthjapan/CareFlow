/**
 * 「QRの管理」ダイアログ (設計 qr-manage-reissue-guard-2026-10-14 §3-2・§6)。
 *
 * - 今の版・最後に読めた日 (JST)・猶予中の古い QR を出す
 * - 再発行はたたまれていて、理由とチェックがそろうまで赤いボタンは押せない
 * - 最後に読めた日が 7 日以内なら黄色の注意
 * - 実行すると理由つきで再発行し、印刷画面へ移る
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mockPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: vi.fn(() => ({ push: mockPush })),
}));

vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock('@/lib/queries/patientQr', () => ({
  usePatientQrStatus: vi.fn(),
  useRegeneratePatientQr: vi.fn(),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { usePatientQrStatus, useRegeneratePatientQr } from '@/lib/queries/patientQr';
import { PatientQrManageDialog } from '../PatientQrManageDialog';

const NOW = new Date('2026-10-14T01:00:00Z'); // JST 10/14 10:00

interface StatusOverrides {
  version?: number;
  issued_at?: string | null;
  last_scanned_at?: string | null;
  last_scanned_staff_name?: string | null;
  grace?: { version: number; valid_until: string }[];
}

function setup(status: StatusOverrides = {}) {
  const mutateAsync = vi.fn().mockResolvedValue({ token: 'new', version: 4 });
  (usePatientQrStatus as Mock).mockReturnValue({
    data: {
      version: 3,
      issued_at: '2026-10-07T03:46:51Z',
      last_scanned_at: null,
      last_scanned_staff_name: null,
      grace: [],
      ...status,
    },
    isLoading: false,
    isError: false,
  });
  (useRegeneratePatientQr as Mock).mockReturnValue({ mutateAsync, isPending: false });
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
  render(
    <PatientQrManageDialog
      open
      onOpenChange={vi.fn()}
      patient={{ id: 'patient-1', name: '利用者G 明美', code: 'P-0123' }}
    />,
  );
  return { user, mutateAsync };
}

describe('PatientQrManageDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('今の版・発行日・最後に読めた日 (JST・職員名)・印刷への導線を出す', () => {
    setup({ last_scanned_at: '2026-10-13T00:35:00Z', last_scanned_staff_name: '職員L' });

    expect(screen.getByText(/利用者G 明美 様/)).toBeInTheDocument();
    expect(screen.getByTestId('qr-manage-version')).toHaveTextContent('第3版（10/7 発行）');
    expect(screen.getByTestId('qr-manage-last-scan')).toHaveTextContent('10/13 09:35 職員Lさん');
    expect(screen.getByRole('link', { name: 'QRを印刷する' })).toHaveAttribute(
      'href',
      '/patients/qr-print?mode=single&patient=patient-1',
    );
    expect(screen.queryByTestId('qr-manage-grace')).not.toBeInTheDocument();
  });

  it('読まれていなければ「まだ読まれていません」・初回発行は発行日を出さない', () => {
    setup({ version: 1, issued_at: null });

    expect(screen.getByTestId('qr-manage-version')).toHaveTextContent(/^第1版$/);
    expect(screen.getByTestId('qr-manage-last-scan')).toHaveTextContent('まだ読まれていません');
  });

  it('猶予中の古い QR は「第N版は M/D まで使えます」と出す', () => {
    setup({ grace: [{ version: 2, valid_until: '2026-10-21T03:46:51Z' }] });

    expect(screen.getByTestId('qr-manage-grace')).toHaveTextContent('第2版は 10/21 まで使えます');
  });

  it('再発行はたたまれていて、理由とチェックがそろうまで赤いボタンは押せない', async () => {
    const { user, mutateAsync } = setup();

    expect(screen.queryByTestId('qr-manage-regenerate')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('qr-manage-reissue-toggle'));

    const submit = screen.getByTestId('qr-manage-regenerate');
    expect(submit).toBeDisabled();

    await user.click(screen.getByTestId('qr-manage-reason-damaged'));
    expect(submit).toBeDisabled();

    await user.click(screen.getByTestId('qr-manage-confirm'));
    expect(submit).toBeEnabled();

    // チェックを外すとまた押せない。
    await user.click(screen.getByTestId('qr-manage-confirm'));
    expect(submit).toBeDisabled();

    // チェックだけ (理由なし) でも押せない。
    await user.click(screen.getByRole('button', { name: 'やめる' }));
    await user.click(screen.getByTestId('qr-manage-reissue-toggle'));
    await user.click(screen.getByTestId('qr-manage-confirm'));
    expect(screen.getByTestId('qr-manage-regenerate')).toBeDisabled();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('作り直すと理由つきで再発行し、印刷画面へ移る', async () => {
    const { user, mutateAsync } = setup();

    await user.click(screen.getByTestId('qr-manage-reissue-toggle'));
    await user.click(screen.getByTestId('qr-manage-reason-lost'));
    await user.click(screen.getByTestId('qr-manage-confirm'));
    await user.click(screen.getByTestId('qr-manage-regenerate'));

    expect(mutateAsync).toHaveBeenCalledWith('lost');
    expect(mockPush).toHaveBeenCalledWith('/patients/qr-print?mode=single&patient=patient-1');
  });

  it('最後に読めた日が 7 日以内なら黄色の注意を出す', async () => {
    const { user } = setup({ last_scanned_at: '2026-10-13T00:35:00Z' });

    await user.click(screen.getByTestId('qr-manage-reissue-toggle'));

    expect(screen.getByTestId('qr-manage-recent-warning')).toHaveTextContent(
      '10/13 に QR が読めています。本当に作り直しますか？',
    );
  });

  it('最後に読めた日が 7 日より前なら注意は出さない', async () => {
    const { user } = setup({ last_scanned_at: '2026-10-01T00:35:00Z' });

    await user.click(screen.getByTestId('qr-manage-reissue-toggle'));

    expect(screen.queryByTestId('qr-manage-recent-warning')).not.toBeInTheDocument();
  });
});
