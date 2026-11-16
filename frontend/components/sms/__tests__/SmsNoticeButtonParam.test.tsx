/**
 * ［予定のお知らせ］ボタン — ベルから ?sms=changes / ?sms=week で開かれたとき。
 * そのタブでダイアログを開き、読み込み直しで開き直さないよう URL から sms を外す。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('next/navigation', () => ({ useSearchParams: vi.fn() }));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import { useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

import { SmsNoticeButton } from '../SmsNoticeButton';

const STATUS = {
  enabled: true,
  provider: 'kddi',
  send_mode: 'live',
  test_allowlist_count: 0,
  within_send_hours: true,
  send_hours: '8:00〜20:00',
  pending_count: 0,
  urgent_count: 0,
  failed_recent_count: 0,
};

function renderButton() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SmsNoticeButton weekStart="2026-11-30" />
    </QueryClientProvider>,
  );
}

function setParams(qs: string) {
  (useSearchParams as Mock).mockReturnValue(new URLSearchParams(qs));
  window.history.replaceState(null, '', `/schedule?${qs}`);
}

beforeEach(() => {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: 'admin' }, accessToken: 't', refreshToken: 'r' },
    status: 'authenticated',
  });
  (fetcher as Mock).mockImplementation(async (path: string) => {
    if (path.startsWith('/api/v1/sms/status')) return STATUS;
    if (path.startsWith('/api/v1/sms/changes/pending')) {
      return { within_send_hours: true, send_hours: '8:00〜20:00', count: 0, items: [] };
    }
    if (path.startsWith('/api/v1/sms/week-notices/preview')) {
      return {
        week_start: '2026-11-30',
        week_label: '来週の',
        within_send_hours: true,
        send_hours: '8:00〜20:00',
        rows: [],
        not_sendable: [],
        totals: { people: 0, messages: 0, segments: 0, yen: 0 },
      };
    }
    throw new Error(`unexpected ${path}`);
  });
});

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/');
});

describe('［予定のお知らせ］ボタン — URL の sms', () => {
  it('?sms=changes なら「変更のお知らせ」で開き、URL から sms を外す', async () => {
    setParams('unit=staff&sms=changes');
    renderButton();
    expect(await screen.findByTestId('sms-notice-dialog')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /変更のお知らせ/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(window.location.search).toBe('?unit=staff');
  });

  it('?sms=week なら「週のお知らせ」で開く', async () => {
    setParams('sms=week');
    renderButton();
    expect(await screen.findByTestId('sms-notice-dialog')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '週のお知らせ' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(window.location.search).toBe('');
  });

  it('sms が無いか知らない値なら開かない', async () => {
    setParams('sms=other');
    renderButton();
    expect(await screen.findByTestId('sms-notice-button')).toBeInTheDocument();
    expect(screen.queryByTestId('sms-notice-dialog')).not.toBeInTheDocument();
  });
});
