/**
 * BulkPinAllPfvsButton テスト (Phase G-34).
 *
 * カバーケース:
 *   1. canEdit=false (staff) → ボタン非表示
 *   2. canEdit=true → 「全件ピン留め」 ボタン描画
 *   3. 全 PFV が既にピン留め状態のとき click → toast.info 出て dialog 開かない
 *   4. canEdit=true → click → 2 段階 dialog → 「実行」 で bulk POST + toast.success
 *   5. canEdit=true → 「全件ピン留め解除」 click → is_pinned=false で POST される
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockMutateAsync, mockToast, mockFetcher } = vi.hoisted(() => ({
  mockMutateAsync: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  mockFetcher: vi.fn(),
}));

vi.mock('@/lib/queries/g21', () => ({
  useBulkPinPfvs: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
}));

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: (...args: unknown[]) => mockFetcher(...args),
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

// shadcn/ui Dialog (open のときだけ children を描画する簡易 mock)
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    open,
    onOpenChange,
    children,
  }: {
    open: boolean;
    onOpenChange?: (v: boolean) => void;
    children: React.ReactNode;
  }) =>
    open ? (
      <div data-testid="dialog" data-onopenchange={onOpenChange ? 'set' : 'unset'}>
        {children}
      </div>
    ) : null,
  DialogContent: ({
    children,
  }: {
    children: React.ReactNode;
    className?: string;
    'aria-describedby'?: string;
    'data-testid'?: string;
  }) => <div data-testid="dialog-content">{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children, id }: { children: React.ReactNode; id?: string }) => (
    <p id={id}>{children}</p>
  ),
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    'aria-label': ariaLabel,
    'data-testid': dataTestid,
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    variant?: string;
    size?: string;
    className?: string;
    type?: string;
    'aria-label'?: string;
    'data-testid'?: string;
  }) => (
    <button onClick={onClick} disabled={disabled} aria-label={ariaLabel} data-testid={dataTestid}>
      {children}
    </button>
  ),
}));

vi.mock('lucide-react', () => ({
  Lock: () => <span data-testid="icon-lock" />,
  Unlock: () => <span data-testid="icon-unlock" />,
  Loader2: () => <span data-testid="icon-loader" />,
}));

// ─── Import ───────────────────────────────────────────────────────────────────

import { BulkPinAllPfvsButton } from '../BulkPinAllPfvsButton';

// ─── Fixtures ────────────────────────────────────────────────────────────────

const patientActive = {
  id: '11111111-1111-1111-1111-111111111111',
  status: 'active',
};
const patientInactive = {
  id: '22222222-2222-2222-2222-222222222222',
  status: 'inactive',
};

const pfvUnpinned = {
  id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  patient_id: patientActive.id,
  weekday: 0,
  start_time: '10:00',
  duration_min: 30,
  mode: 'normal',
  slot_index: 0,
  is_pinned: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};
const pfvPinned = {
  id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
  patient_id: patientActive.id,
  weekday: 1,
  start_time: '11:00',
  duration_min: 30,
  mode: 'normal',
  slot_index: 0,
  is_pinned: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * `fetcher(path)` の呼び出しを path で振り分けて返却する mock 設定.
 */
function setupFetcher(opts: { patients: unknown[]; pfvsByPatient: Record<string, unknown[]> }) {
  mockFetcher.mockImplementation((path: string) => {
    if (path.startsWith('/api/v1/patients?')) {
      return Promise.resolve(opts.patients);
    }
    const m = path.match(/^\/api\/v1\/patients\/([^/]+)\/fixed-visits$/);
    if (m) {
      const pid = m[1] ?? '';
      return Promise.resolve(opts.pfvsByPatient[pid] ?? []);
    }
    return Promise.reject(new Error(`unexpected path: ${path}`));
  });
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('BulkPinAllPfvsButton (Phase G-34)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. canEdit=false のときボタンが描画されない', () => {
    const { container } = render(<BulkPinAllPfvsButton canEdit={false} />);
    expect(container.firstChild).toBeNull();
  });

  it('2. canEdit=true → 全件ピン留め / 全件ピン留め解除 ボタンが描画される', () => {
    render(<BulkPinAllPfvsButton canEdit />);
    expect(
      screen.getByRole('button', { name: '全患者の固定枠を一括ピン留め' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '全患者の固定枠を一括ピン留め解除' }),
    ).toBeInTheDocument();
  });

  it('3. 全 PFV が既にピン留め状態のとき click → toast.info 出て dialog 開かない', async () => {
    setupFetcher({
      patients: [patientActive],
      pfvsByPatient: { [patientActive.id]: [pfvPinned] },
    });

    render(<BulkPinAllPfvsButton canEdit />);
    fireEvent.click(screen.getByRole('button', { name: '全患者の固定枠を一括ピン留め' }));

    await waitFor(() => {
      expect(mockToast.info).toHaveBeenCalledWith('既に全件ピン留め状態です');
    });
    expect(screen.queryByTestId('dialog')).not.toBeInTheDocument();
    expect(mockMutateAsync).not.toHaveBeenCalled();
  });

  it('4. canEdit=true → click → 2 段階 dialog → 「実行」で bulk POST + success toast', async () => {
    setupFetcher({
      patients: [patientActive, patientInactive],
      pfvsByPatient: { [patientActive.id]: [pfvUnpinned, pfvPinned] },
    });
    mockMutateAsync.mockResolvedValueOnce(undefined);

    render(<BulkPinAllPfvsButton canEdit />);
    fireEvent.click(screen.getByRole('button', { name: '全患者の固定枠を一括ピン留め' }));

    // 1 段目ダイアログが開く (= unpinned が 1 件あるので)
    await waitFor(() => {
      expect(screen.getByTestId('dialog')).toBeInTheDocument();
    });

    // 1 段目「続行」
    fireEvent.click(screen.getByTestId('bulk-pin-step1-confirm-button'));

    // 2 段目「実行」
    const executeBtn = await screen.findByTestId('bulk-pin-step2-confirm-button');
    fireEvent.click(executeBtn);

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledOnce();
    });
    // is_pinned=true で、 unpinned だった pfvUnpinned のみ payload に入る
    expect(mockMutateAsync).toHaveBeenCalledWith([{ pfv_id: pfvUnpinned.id, is_pinned: true }]);
    expect(mockToast.success).toHaveBeenCalledWith('1 件をピン留めしました');
    // inactive patient の PFV は fetch されないことを確認
    expect(mockFetcher).not.toHaveBeenCalledWith(
      expect.stringContaining(`/api/v1/patients/${patientInactive.id}/fixed-visits`),
      expect.anything(),
    );
  });

  it('5. canEdit=true → 「全件ピン留め解除」 click → is_pinned=false で POST', async () => {
    setupFetcher({
      patients: [patientActive],
      pfvsByPatient: { [patientActive.id]: [pfvPinned] },
    });
    mockMutateAsync.mockResolvedValueOnce(undefined);

    render(<BulkPinAllPfvsButton canEdit />);
    fireEvent.click(screen.getByRole('button', { name: '全患者の固定枠を一括ピン留め解除' }));

    await waitFor(() => {
      expect(screen.getByTestId('dialog')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('bulk-pin-step1-confirm-button'));
    fireEvent.click(await screen.findByTestId('bulk-pin-step2-confirm-button'));

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith([{ pfv_id: pfvPinned.id, is_pinned: false }]);
    });
    expect(mockToast.success).toHaveBeenCalledWith('1 件をピン留め解除しました');
  });
});
