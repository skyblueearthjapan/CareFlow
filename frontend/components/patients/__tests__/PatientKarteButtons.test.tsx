/**
 * PatientKarteButtons テスト (個別カルテ UI).
 *
 * カバーケース:
 *   1. 「🗒 空白カルテ」「📄 カルテ取込」ボタンが描画される
 *   2. 空白カルテ click → downloadKarteTemplate → triggerBlobDownload(template名)
 *   3. ファイル選択 → importPatientKarte(dry_run=true) でプレビュー取得
 *   4. 反映 (window.confirm OK) → importPatientKarte(dry_run=false) + success toast
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

// ─── モック ──────────────────────────────────────────────────────────────────

const {
  mockDownloadKarteTemplate,
  mockImportPatientKarte,
  mockTriggerBlobDownload,
  mockToast,
  mockInvalidateQueries,
} = vi.hoisted(() => ({
  mockDownloadKarteTemplate: vi.fn(),
  mockImportPatientKarte: vi.fn(),
  mockTriggerBlobDownload: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  mockInvalidateQueries: vi.fn(),
}));

vi.mock('@/lib/api/patientsExcel', () => ({
  downloadKarteTemplate: (...args: unknown[]) => mockDownloadKarteTemplate(...args),
  importPatientKarte: (...args: unknown[]) => mockImportPatientKarte(...args),
  triggerBlobDownload: (...args: unknown[]) => mockTriggerBlobDownload(...args),
}));

vi.mock('sonner', () => ({
  toast: mockToast,
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    variant?: string;
    size?: string;
  }) => (
    <button onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
}));

// ImportPreviewModal: open のとき「反映」ボタンを描画する簡易 mock.
vi.mock('../ImportPreviewModal', () => ({
  ImportPreviewModal: ({ open, onApply }: { open: boolean; onApply: () => void }) =>
    open ? <button onClick={onApply}>反映実行(mock)</button> : null,
}));

// ─── Import ───────────────────────────────────────────────────────────────────

import { PatientKarteButtons } from '../PatientKarteButtons';

const emptyResponse = {
  summary: {
    patients_new: 0,
    patients_update: 1,
    patients_delete: 0,
    patients_error: 0,
    patients_noop: 0,
    pfv_new: 0,
    pfv_update: 0,
    pfv_delete: 0,
    pfv_error: 0,
    pfv_noop: 0,
  },
  patient_rows: [],
  pfv_rows: [],
  transaction_applied: false,
};

function makeXlsxFile(name = 'patient_0001_山田太郎.xlsx'): File {
  return new File(['dummy'], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('PatientKarteButtons', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 空白カルテ / カルテ取込 ボタンが描画される', () => {
    render(<PatientKarteButtons />);
    expect(screen.getByRole('button', { name: '🗒 空白カルテ' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '📄 カルテ取込' })).toBeInTheDocument();
  });

  it('2. 空白カルテ click → downloadKarteTemplate → triggerBlobDownload', async () => {
    const blob = new Blob(['x']);
    mockDownloadKarteTemplate.mockResolvedValueOnce(blob);

    render(<PatientKarteButtons />);
    fireEvent.click(screen.getByRole('button', { name: '🗒 空白カルテ' }));

    await waitFor(() => {
      expect(mockDownloadKarteTemplate).toHaveBeenCalledWith({
        accessToken: 'tok',
        refreshToken: 'ref',
      });
    });
    expect(mockTriggerBlobDownload).toHaveBeenCalledWith(blob, 'patient_karte_template.xlsx');
  });

  it('3. ファイル選択 → importPatientKarte(dry_run=true) でプレビュー', async () => {
    mockImportPatientKarte.mockResolvedValueOnce(emptyResponse);

    const { container } = render(<PatientKarteButtons />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeXlsxFile()] } });

    await waitFor(() => {
      expect(mockImportPatientKarte).toHaveBeenCalledWith(
        expect.objectContaining({ dryRun: true, accessToken: 'tok', refreshToken: 'ref' }),
      );
    });
    // プレビューモーダル (mock) が開く
    expect(await screen.findByRole('button', { name: '反映実行(mock)' })).toBeInTheDocument();
  });

  it('4. 反映 (confirm OK) → importPatientKarte(dry_run=false) + success toast', async () => {
    mockImportPatientKarte.mockResolvedValue(emptyResponse);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    const { container } = render(<PatientKarteButtons />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeXlsxFile()] } });

    const applyBtn = await screen.findByRole('button', { name: '反映実行(mock)' });
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(mockImportPatientKarte).toHaveBeenCalledWith(
        expect.objectContaining({ dryRun: false }),
      );
    });
    expect(mockToast.success).toHaveBeenCalled();
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['patients'] });
    confirmSpy.mockRestore();
  });
});
