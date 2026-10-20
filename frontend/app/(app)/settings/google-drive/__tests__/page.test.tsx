/**
 * /settings/google-drive (Google ドライブの接続・休み・勤務表の設計 §7-3) の vitest テスト.
 *
 * 1. 未設定なら準備の案内を出し、接続ボタンは押せない
 * 2. 未接続なら「Google ドライブに接続」で許可の画面へ移る
 * 3. 接続中は保存先フォルダと切断を出す・Picker 未設定の案内
 * 4. 接続し直しが要るときは帯と「もう一度接続する」
 * 5. 戻りの結果 (?result=error&reason=state) の文を出す
 * 6. 管理者以外は使えない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('next/navigation', () => ({ useSearchParams: vi.fn() }));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/brand/Rakusuke', () => ({
  RakusukeTitle: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock('@/lib/googlePicker', () => ({ pickDriveFolder: vi.fn() }));
vi.mock('@/lib/queries/googleDrive', () => ({
  googleDriveErrorMessage: (_e: unknown, fallback: string) => fallback,
  useGoogleDriveStatus: vi.fn(),
  useStartGoogleDriveConnect: vi.fn(),
  useGoogleDrivePickerToken: vi.fn(),
  useSaveGoogleDriveFolder: vi.fn(),
  useDisconnectGoogleDrive: vi.fn(),
}));

import { useSession } from 'next-auth/react';
import { useSearchParams } from 'next/navigation';
import {
  useDisconnectGoogleDrive,
  useGoogleDrivePickerToken,
  useGoogleDriveStatus,
  useSaveGoogleDriveFolder,
  useStartGoogleDriveConnect,
} from '@/lib/queries/googleDrive';
import type { GoogleDriveStatus } from '@/lib/schemas/googleDrive';

import GoogleDriveSettingsPage from '../page';

const BASE: GoogleDriveStatus = {
  configured: true,
  picker_configured: true,
  status: 'not_connected',
  folder_id: null,
  folder_name: null,
  folder_missing: false,
  shared_drive_id: null,
  connected_by_name: null,
  last_error: null,
  updated_at: null,
};

const connectMutate = vi.fn();

function setup(status: Partial<GoogleDriveStatus>, opts: { role?: string; query?: string } = {}) {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: opts.role ?? 'admin' }, accessToken: 't' },
    status: 'authenticated',
  });
  (useSearchParams as Mock).mockReturnValue(new URLSearchParams(opts.query ?? ''));
  (useGoogleDriveStatus as Mock).mockReturnValue({
    data: { ...BASE, ...status },
    isLoading: false,
    isError: false,
  });
  (useStartGoogleDriveConnect as Mock).mockReturnValue({
    mutateAsync: connectMutate,
    isPending: false,
  });
  (useGoogleDrivePickerToken as Mock).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  (useSaveGoogleDriveFolder as Mock).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  (useDisconnectGoogleDrive as Mock).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
}

describe('GoogleDriveSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 未設定なら準備の案内を出し、接続ボタンは押せない', () => {
    setup({ configured: false, picker_configured: false });
    render(<GoogleDriveSettingsPage />);
    expect(screen.getByTestId('not-configured')).toHaveTextContent('準備ができていません');
    expect(screen.getByTestId('not-configured')).toHaveTextContent('google-drive-setup.md');
    expect(screen.getByTestId('connect')).toBeDisabled();
    expect(screen.getByTestId('drive-status')).toHaveTextContent('未接続');
  });

  it('2. 未接続なら「Google ドライブに接続」で許可の画面へ移る', async () => {
    setup({});
    const assign = vi.fn();
    const original = window.location;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...original, assign },
    });
    connectMutate.mockResolvedValue('https://accounts.google.com/o/oauth2/v2/auth?x=1');
    render(<GoogleDriveSettingsPage />);
    expect(screen.queryByTestId('not-configured')).toBeNull();
    const button = screen.getByTestId('connect');
    expect(button).toHaveTextContent('Google ドライブに接続');
    await act(async () => {
      fireEvent.click(button);
    });
    expect(assign).toHaveBeenCalledWith('https://accounts.google.com/o/oauth2/v2/auth?x=1');
    Object.defineProperty(window, 'location', { configurable: true, value: original });
  });

  it('3. 接続中は保存先フォルダと切断を出す・Picker 未設定の案内', () => {
    setup({
      status: 'connected',
      picker_configured: false,
      folder_id: 'f1',
      folder_name: '勤務表',
      shared_drive_id: 'd1',
      connected_by_name: '管理者',
      updated_at: '2026-10-21T00:00:00Z',
    });
    render(<GoogleDriveSettingsPage />);
    expect(screen.getByTestId('drive-status')).toHaveTextContent('接続中');
    expect(screen.getByTestId('folder')).toHaveTextContent('勤務表');
    expect(screen.getByTestId('folder')).toHaveTextContent('共有ドライブ');
    expect(screen.getByTestId('pick-folder')).toBeDisabled();
    expect(screen.getByTestId('picker-not-configured')).toBeInTheDocument();
    expect(screen.getByTestId('disconnect')).toBeInTheDocument();
    expect(screen.queryByTestId('connect')).toBeNull();
  });

  it('3b. フォルダがドライブに見つからないときは選び直しを促す', () => {
    setup({ status: 'connected', folder_id: 'f1', folder_missing: true });
    render(<GoogleDriveSettingsPage />);
    expect(screen.getByTestId('folder-missing')).toBeInTheDocument();
    expect(screen.getByTestId('pick-folder')).toHaveTextContent('別のフォルダを選ぶ');
  });

  it('4. 接続し直しが要るときは帯と「もう一度接続する」', () => {
    setup({ status: 'needs_reconnect', last_error: 'invalid_grant' });
    render(<GoogleDriveSettingsPage />);
    expect(screen.getByTestId('needs-reconnect')).toHaveTextContent(
      'Google ドライブにもう一度接続してください',
    );
    expect(screen.getByTestId('connect')).toHaveTextContent('もう一度接続する');
    expect(screen.queryByTestId('pick-folder')).toBeNull();
  });

  it('5. 戻りの結果の文を出す', () => {
    setup({}, { query: 'result=error&reason=state' });
    render(<GoogleDriveSettingsPage />);
    expect(screen.getByTestId('callback-error')).toHaveTextContent('別のブラウザー');
  });

  it('6. 管理者以外は使えない', () => {
    setup({}, { role: 'staff' });
    render(<GoogleDriveSettingsPage />);
    expect(screen.getByText('この設定は管理者だけが使えます。')).toBeInTheDocument();
    expect(screen.queryByTestId('connect')).toBeNull();
  });
});
