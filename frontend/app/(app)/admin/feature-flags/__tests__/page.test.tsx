/**
 * Phase G-21 T4 reviewer C1 + H4 — feature-flags page tests.
 *
 * カバー:
 *   C1: BE は `enabled` を返さないため、`enabled_at != null` を「有効」と判定.
 *       enabled_at=ISO → Switch が ON / enabled_at=null → Switch が OFF.
 *   H4: 削除ボタンが window.confirm の代わりに Dialog (2 択) を開く.
 *       「無効化のみ」「履歴ごと削除」「キャンセル」の 3 ボタンが存在する.
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ─── Mocks ──────────────────────────────────────────────────────────────
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock('@/lib/queries/g21', () => ({
  useOfficeFeatureFlags: vi.fn(),
  useSetOfficeFeatureFlag: vi.fn(),
  useDeleteOfficeFeatureFlag: vi.fn(),
}));

vi.mock('@/lib/queries/offices', () => ({
  useOffices: vi.fn(),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { useSession } from 'next-auth/react';
import {
  useDeleteOfficeFeatureFlag,
  useOfficeFeatureFlags,
  useSetOfficeFeatureFlag,
} from '@/lib/queries/g21';
import { useOffices } from '@/lib/queries/offices';

import AdminFeatureFlagsPage from '../page';

const OFFICE_ID = '00000000-0000-0000-0000-000000000010';

function makeMutation(mutateAsync: Mock = vi.fn().mockResolvedValue(undefined)) {
  return { mutateAsync, isPending: false, isError: false };
}

interface SetupOpts {
  role?: 'admin' | 'manager' | 'staff';
  flags?: Array<{
    office_id: string;
    feature_key: string;
    enabled_at: string | null;
    enabled_by_user_id?: string | null;
    note?: string | null;
  }>;
  setFn?: Mock;
  deleteFn?: Mock;
}

function setupMocks(opts: SetupOpts = {}) {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: opts.role ?? 'admin' } },
    status: 'authenticated',
  });
  (useOfficeFeatureFlags as Mock).mockReturnValue({
    data: opts.flags ?? [],
    isLoading: false,
    isError: false,
    error: null,
  });
  (useOffices as Mock).mockReturnValue({
    offices: [{ id: OFFICE_ID, name: '稲毛事業所' }],
    allOffices: [{ id: OFFICE_ID, name: '稲毛事業所' }],
    isLoading: false,
    isError: false,
  });
  (useSetOfficeFeatureFlag as Mock).mockReturnValue(makeMutation(opts.setFn));
  (useDeleteOfficeFeatureFlag as Mock).mockReturnValue(makeMutation(opts.deleteFn));
}

describe('AdminFeatureFlagsPage (Phase G-21 T4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('C1: enabled_at=null → Switch が OFF + 削除ボタン非表示', () => {
    setupMocks({ flags: [] });
    render(<AdminFeatureFlagsPage />);
    const sw = screen.getByTestId(`feature-flag-toggle-${OFFICE_ID}-g21_new_algorithm`);
    expect(sw).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText('無効')).toBeInTheDocument();
    // flag レコードが無い場合は削除ボタンも出ない
    expect(
      screen.queryByTestId(`feature-flag-delete-${OFFICE_ID}-g21_new_algorithm`),
    ).not.toBeInTheDocument();
  });

  it('C1: enabled_at=ISO 文字列 → Switch が ON (BE の enabled フィールド非依存)', () => {
    setupMocks({
      flags: [
        {
          office_id: OFFICE_ID,
          feature_key: 'g21_new_algorithm',
          enabled_at: '2026-05-22T10:00:00Z',
          enabled_by_user_id: '00000000-0000-0000-0000-000000000099',
          note: null,
        },
      ],
    });
    render(<AdminFeatureFlagsPage />);
    const sw = screen.getByTestId(`feature-flag-toggle-${OFFICE_ID}-g21_new_algorithm`);
    expect(sw).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('有効')).toBeInTheDocument();
  });

  it('H4: 削除ボタンクリックで Dialog 2 択 (無効化 / 履歴ごと削除) が開く', async () => {
    setupMocks({
      flags: [
        {
          office_id: OFFICE_ID,
          feature_key: 'g21_new_algorithm',
          enabled_at: '2026-05-22T10:00:00Z',
          enabled_by_user_id: '00000000-0000-0000-0000-000000000099',
        },
      ],
    });
    render(<AdminFeatureFlagsPage />);

    fireEvent.click(screen.getByTestId(`feature-flag-delete-${OFFICE_ID}-g21_new_algorithm`));

    await waitFor(() => {
      expect(screen.getByTestId('feature-flag-confirm-dialog')).toBeInTheDocument();
    });
    expect(screen.getByTestId('feature-flag-confirm-cancel')).toBeInTheDocument();
    expect(screen.getByTestId('feature-flag-confirm-disable')).toBeInTheDocument();
    expect(screen.getByTestId('feature-flag-confirm-delete')).toBeInTheDocument();
    // 注意喚起テキストが含まれる
    expect(
      screen.getByText(/audit レコードも完全に削除|過去の有効化情報を二度と確認/),
    ).toBeInTheDocument();
  });

  it('H4: 「無効化のみ」 → setFlag(enabled=false) (= 履歴は残す)', async () => {
    const setFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({
      setFn,
      flags: [
        {
          office_id: OFFICE_ID,
          feature_key: 'g21_new_algorithm',
          enabled_at: '2026-05-22T10:00:00Z',
        },
      ],
    });
    render(<AdminFeatureFlagsPage />);

    fireEvent.click(screen.getByTestId(`feature-flag-delete-${OFFICE_ID}-g21_new_algorithm`));
    await screen.findByTestId('feature-flag-confirm-dialog');
    fireEvent.click(screen.getByTestId('feature-flag-confirm-disable'));

    await waitFor(() => expect(setFn).toHaveBeenCalledTimes(1));
    expect(setFn).toHaveBeenCalledWith({
      office_id: OFFICE_ID,
      feature_key: 'g21_new_algorithm',
      enabled: false,
    });
  });

  it('H4: 「履歴ごと削除」 → deleteFlag (= レコード物理削除)', async () => {
    const deleteFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({
      deleteFn,
      flags: [
        {
          office_id: OFFICE_ID,
          feature_key: 'g21_new_algorithm',
          enabled_at: '2026-05-22T10:00:00Z',
        },
      ],
    });
    render(<AdminFeatureFlagsPage />);

    fireEvent.click(screen.getByTestId(`feature-flag-delete-${OFFICE_ID}-g21_new_algorithm`));
    await screen.findByTestId('feature-flag-confirm-dialog');
    fireEvent.click(screen.getByTestId('feature-flag-confirm-delete'));

    await waitFor(() => expect(deleteFn).toHaveBeenCalledTimes(1));
    expect(deleteFn).toHaveBeenCalledWith({
      office_id: OFFICE_ID,
      feature_key: 'g21_new_algorithm',
    });
  });

  it('H4: 「キャンセル」 → 何も実行されない', async () => {
    const setFn = vi.fn();
    const deleteFn = vi.fn();
    setupMocks({
      setFn,
      deleteFn,
      flags: [
        {
          office_id: OFFICE_ID,
          feature_key: 'g21_new_algorithm',
          enabled_at: '2026-05-22T10:00:00Z',
        },
      ],
    });
    render(<AdminFeatureFlagsPage />);

    fireEvent.click(screen.getByTestId(`feature-flag-delete-${OFFICE_ID}-g21_new_algorithm`));
    await screen.findByTestId('feature-flag-confirm-dialog');
    fireEvent.click(screen.getByTestId('feature-flag-confirm-cancel'));

    expect(setFn).not.toHaveBeenCalled();
    expect(deleteFn).not.toHaveBeenCalled();
  });
});
