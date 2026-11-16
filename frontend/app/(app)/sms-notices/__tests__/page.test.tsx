/**
 * お知らせの記録 (/sms-notices・sms-notify-design §5-4)。
 * - 旗が OFF (enabled:false) なら中身を読まずに案内だけ。
 * - 状態は決まり §11 の言葉で出す (記号のまま出さない)。送っていない理由を添える。
 * - 行を開くと詳細 (本文・経過・問い合わせ用の番号)。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));
const replace = vi.fn();
const nav = vi.hoisted(() => ({ search: new URLSearchParams() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => nav.search,
}));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: [] } }),
}));

import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

import SmsNoticesPage from '../page';

function msg(id: string, status: string, over: Record<string, unknown> = {}) {
  return {
    id,
    batch_id: 'b1',
    kind: 'week',
    week_start: '2026-11-30',
    patient_id: 'p1',
    patient_name: '見本 太郎',
    relation: 'self',
    relation_label: 'ご本人',
    phone_last4: '0001',
    status,
    status_raw: null,
    skip_reason: null,
    error_code: null,
    char_count: 64,
    segments_est: 1,
    split_num: null,
    carrier: 'au',
    created_by_user_id: 'u1',
    created_by_name: '事務 一郎',
    created_at: '2026-11-30T01:00:00Z',
    sent_at: null,
    accepted_at: null,
    status_decided_at: null,
    ...over,
  };
}

let statusBody: unknown = { enabled: true, provider: 'mock', send_mode: 'test' };

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SmsNoticesPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  nav.search = new URLSearchParams();
  statusBody = { enabled: true, provider: 'mock', send_mode: 'test' };
  (useSession as Mock).mockReturnValue({
    data: { user: { role: 'admin' }, accessToken: 't', refreshToken: 'r' },
    status: 'authenticated',
  });
  (fetcher as Mock).mockImplementation(async (path: string) => {
    if (path.startsWith('/api/v1/sms/status')) return statusBody;
    if (path.startsWith('/api/v1/sms/messages/m2')) {
      return {
        ...msg('m2', 'failed'),
        phone: '090-0000-0001',
        body: '本文\nその2',
        provider: 'kddi',
        provider_msg_id: 'MSG-123',
        transaction_id: 'TX-456',
        error_message: '受信の設定で受け取れませんでした',
        fail_reason_raw: 'ESME_REGULATED',
        not_before: null,
        attempt_count: 1,
        notice_version: 1,
        events: [
          { at: '2026-11-30T01:00:05Z', status: 'accepted', raw_status: null, detail: null },
          { at: '2026-11-30T01:01:05Z', status: 'failed', raw_status: 'failed', detail: null },
        ],
      };
    }
    if (path.startsWith('/api/v1/sms/messages')) {
      return {
        items: [
          msg('m1', 'delivered'),
          msg('m2', 'failed'),
          msg('m3', 'skipped', { skip_reason: 'test_mode' }),
          msg('m4', 'accepted'),
          msg('m5', 'something_new'),
          msg('m6', 'delivered', { provider: 'mock' }),
        ],
        total: 6,
        limit: 50,
        offset: 0,
      };
    }
    throw new Error(`unexpected ${path}`);
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('お知らせの記録', () => {
  it('旗が OFF なら案内だけを出し、記録は読まない', async () => {
    statusBody = { enabled: false };
    renderPage();
    expect(await screen.findByTestId('sms-records-disabled')).toHaveTextContent(
      '予定のお知らせ（SMS）は、まだ使っていません',
    );
    const paths = (fetcher as Mock).mock.calls.map((c) => c[0] as string);
    expect(paths.some((p) => p.startsWith('/api/v1/sms/messages'))).toBe(false);
  });

  it('状態は決まりの言葉で出す (記号のまま出さない)', async () => {
    renderPage();
    const table = await screen.findByTestId('sms-messages-table');
    expect(screen.getByTestId('sms-message-row-m1')).toHaveTextContent('届きました');
    expect(screen.getByTestId('sms-message-row-m2')).toHaveTextContent('届きませんでした');
    expect(screen.getByTestId('sms-message-row-m3')).toHaveTextContent(
      '送っていません（試験の状態のため）',
    );
    expect(screen.getByTestId('sms-message-row-m4')).toHaveTextContent('受け付け済み');
    expect(screen.getByTestId('sms-message-row-m5')).toHaveTextContent('分かりません');
    expect(table).not.toHaveTextContent('delivered');
    expect(table).not.toHaveTextContent('skipped');
    expect(table).not.toHaveTextContent('something_new');
    expect(screen.getByTestId('sms-message-row-m1')).toHaveTextContent('ご本人（末尾 0001）');
  });

  it('模擬の窓口の行には「模擬（実際には送っていません）」を添える', async () => {
    renderPage();
    await screen.findByTestId('sms-messages-table');
    expect(screen.getByTestId('sms-message-row-m6')).toHaveTextContent(
      '模擬（実際には送っていません）',
    );
    expect(screen.getByTestId('sms-message-row-m1')).not.toHaveTextContent('模擬');
  });

  it('行を開くと本文・経過・問い合わせ用の番号を出す', async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId('sms-message-row-m2'));
    const detail = await screen.findByTestId('sms-message-detail');
    await waitFor(() => expect(detail).toHaveTextContent('TX-456'));
    expect(detail).toHaveTextContent('MSG-123');
    expect(detail).toHaveTextContent('090-0000-0001');
    expect(detail).toHaveTextContent('受信の設定で受け取れませんでした');
    expect(screen.getByTestId('sms-message-events')).toHaveTextContent(
      '受け付けられました（まだ届いていません）',
    );
  });

  it('矢印のキーで見方を切り替える', async () => {
    renderPage();
    await screen.findByTestId('sms-messages-table');
    const first = screen.getByRole('tab', { name: '送信の一覧' });
    expect(first).toHaveAttribute('tabindex', '0');
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    const second = screen.getByRole('tab', { name: '患者様ごと' });
    expect(second).toHaveAttribute('aria-selected', 'true');
    expect(second).toHaveFocus();
    fireEvent.keyDown(second, { key: 'End' });
    expect(screen.getByRole('tab', { name: '気になる番号' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    fireEvent.keyDown(screen.getByRole('tab', { name: '気になる番号' }), { key: 'Home' });
    expect(first).toHaveAttribute('aria-selected', 'true');
  });

  it('?undelivered=1 で開くと「届いていないもの」で絞る (undelivered=true で読む)', async () => {
    nav.search = new URLSearchParams('tab=messages&undelivered=1');
    renderPage();
    await screen.findByTestId('sms-messages-table');
    const paths = (fetcher as Mock).mock.calls
      .map((c) => c[0] as string)
      .filter((p) => p.startsWith('/api/v1/sms/messages'));
    expect(paths.length).toBeGreaterThan(0);
    expect(paths.every((p) => p.includes('undelivered=true') && !p.includes('status='))).toBe(true);
    expect(screen.getByTestId('sms-filter-status')).toHaveValue('undelivered');
  });

  it('カンマ区切りの ?status= はそのまま状態で絞る', async () => {
    nav.search = new URLSearchParams('tab=messages&status=failed,unknown');
    renderPage();
    await screen.findByTestId('sms-messages-table');
    const last = (fetcher as Mock).mock.calls
      .map((c) => c[0] as string)
      .filter((p) => p.startsWith('/api/v1/sms/messages'))
      .at(-1);
    expect(last).toContain('status=failed%2Cunknown');
    expect(last).not.toContain('undelivered');
    expect(screen.getByTestId('sms-filter-status')).toHaveValue('failed,unknown');
  });

  it('状態の「届いていないもの」を選ぶと undelivered=true で読む', async () => {
    renderPage();
    await screen.findByTestId('sms-messages-table');
    fireEvent.change(screen.getByTestId('sms-filter-status'), {
      target: { value: 'undelivered' },
    });
    await waitFor(() => {
      const last = (fetcher as Mock).mock.calls
        .map((c) => c[0] as string)
        .filter((p) => p.startsWith('/api/v1/sms/messages'))
        .at(-1);
      expect(last).toContain('undelivered=true');
    });
  });

  it('管理者でなければダッシュボードへ戻し、記録は読まない', async () => {
    (useSession as Mock).mockReturnValue({
      data: { user: { role: 'staff' } },
      status: 'authenticated',
    });
    renderPage();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/dashboard'));
    expect(fetcher as Mock).not.toHaveBeenCalled();
  });
});
