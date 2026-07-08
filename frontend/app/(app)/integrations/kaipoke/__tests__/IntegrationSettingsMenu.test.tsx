/**
 * IntegrationSettingsMenu — 「設定」→「接続設定」→ ダイアログ の二段階配置を施錠する。
 *
 * PO要望 (2026-07-09): カイポケの認証情報 (法人ID/ユーザーID/パスワード) を
 * ページ表側に出さない。閉じている間はフォームが DOM に存在しないことを担保する。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const credentials = vi.fn();
vi.mock('@/lib/queries/integrations', () => ({
  useKaipokeCredentials: () => credentials(),
  useSaveKaipokeCredentials: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTestKaipokeCredentials: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { IntegrationSettingsMenu } from '../_components/IntegrationSettingsMenu';

beforeEach(() => {
  credentials.mockReturnValue({
    data: { configured: true, corpId: 'C000001', userId: 'user01', updatedAt: null },
    isLoading: false,
    isSuccess: true,
  });
});

describe('IntegrationSettingsMenu', () => {
  it('初期表示ではパスワード入力も法人IDも DOM に出ない (表側に晒さない)', () => {
    render(<IntegrationSettingsMenu />);
    expect(screen.queryByLabelText('パスワード')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('法人ID')).not.toBeInTheDocument();
    expect(screen.queryByText('接続テスト')).not.toBeInTheDocument();
  });

  it('「設定」→「接続設定」でダイアログが開き、そこで初めてフォームが現れる', async () => {
    render(<IntegrationSettingsMenu />);
    fireEvent.click(screen.getByTestId('integration-settings-menu'));
    fireEvent.click(await screen.findByTestId('open-connection-settings'));

    // 設定済みなのでコンパクト表示 + 接続テスト。フォームは「変更する」で展開。
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('接続設定')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '接続テスト' })).toBeInTheDocument();
    expect(screen.getByText('C000001')).toBeInTheDocument();
  });

  it('未設定のときは注意ドットと「未設定」ラベルで気づける', async () => {
    credentials.mockReturnValue({
      data: { configured: false },
      isLoading: false,
      isSuccess: true,
    });
    render(<IntegrationSettingsMenu needsAttention />);
    fireEvent.click(screen.getByTestId('integration-settings-menu'));
    expect(await screen.findByText('未設定')).toBeInTheDocument();
  });
});
