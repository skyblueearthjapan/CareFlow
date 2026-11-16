/**
 * 予定のお知らせ (SMS) のダイアログ (sms-notify-design §5-1・§5-2・決まり §2〜§4)。
 *
 * - 週のお知らせ: 既定の印 (送信済みは外す)・合計が印に付いてくる・確かめてから送る・
 *   送るのは ID と expected_count だけ (本文は送らない)・時間の外は押せない・試験の状態の帯・409。
 * - 変更のお知らせ: 選んだ方に送る (items + expected_count)・送らずに済みにする (resolve の中身)。
 * - ボタン: 旗が OFF・管理者以外では出さない・急ぎで強い色。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import { useSession } from 'next-auth/react';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import type { SmsStatus } from '@/lib/schemas/sms';

import { SmsNoticeButton } from '../SmsNoticeButton';
import { SmsNoticeDialog } from '../SmsNoticeDialog';

const P1 = '11111111-1111-1111-1111-111111111111';
const P2 = '22222222-2222-2222-2222-222222222222';
const P3 = '33333333-3333-3333-3333-333333333333';
const P4 = '44444444-4444-4444-4444-444444444444';

function dest(last4: string, label = 'ご本人') {
  return {
    contact_id: `c-${last4}`,
    relation: 'self',
    relation_label: label,
    phone_last4: last4,
  };
}

function preview(over: Record<string, unknown> = {}) {
  return {
    week_start: '2026-11-30',
    week_label: '11/30 からの週',
    within_send_hours: true,
    send_hours: '8:00〜20:00',
    rows: [
      {
        patient_id: P1,
        patient_name: '見本 太郎',
        surname: '見本',
        state: 'new',
        destinations: [dest('0001')],
        visits: [{ date: '2026-12-01', start: '10:00' }],
        body: '訪問看護ステーション 見本 です。\n12/1(火)10:00\n時刻は前後することがあります。',
        char_count: 64,
        segments_est: 1,
        message_count: 1,
        est_yen: 11,
        token: 'tok-w1',
      },
      {
        patient_id: P2,
        patient_name: '試験 花子',
        surname: '試験',
        state: 'changed_since_sent',
        destinations: [dest('0002'), dest('0003', 'ご家族')],
        visits: [{ date: '2026-12-02', start: '14:00' }],
        body: '本文その2',
        char_count: 120,
        segments_est: 2,
        message_count: 2,
        est_yen: 44,
        token: 'tok-w2',
      },
      {
        patient_id: P3,
        patient_name: '送済 次郎',
        surname: '送済',
        state: 'already_sent_same',
        destinations: [dest('0004')],
        visits: [{ date: '2026-12-03', start: '9:30' }],
        body: '本文その3',
        char_count: 60,
        segments_est: 1,
        message_count: 1,
        est_yen: 11,
        token: 'tok-w3',
      },
    ],
    not_sendable: [
      {
        patient_id: P4,
        patient_name: '番号 なし',
        reason: 'no_contact',
        reason_label: '送り先がありません',
        visits: [{ date: '2026-12-04', start: '11:00' }],
      },
    ],
    totals: { people: 2, messages: 3, segments: 5, yen: 55 },
    ...over,
  };
}

function pendingList(over: Record<string, unknown> = {}) {
  return {
    within_send_hours: true,
    send_hours: '8:00〜20:00',
    count: 2,
    urgent_count: 1,
    items: [
      {
        patient_id: P1,
        patient_name: '見本 太郎',
        week_start: '2026-11-30',
        urgent: true,
        detected_at: '2026-11-30T01:05:00Z',
        waiting_minutes: 42,
        changes: [
          {
            kind: 'time',
            from: { date: '2026-12-03', start: '14:00' },
            to: { date: '2026-12-03', start: '15:30' },
            label: '12/3(木)14:00→15:30',
          },
        ],
        sendable: true,
        not_sendable_reason: null,
        not_sendable_label: null,
        destinations: [dest('0001')],
        call_numbers: [{ relation_label: 'ご本人', phone_display: '090-0000-0001' }],
        body: '【見本】見本様 ご訪問の変更です。',
        char_count: 40,
        segments_est: 1,
        est_yen: 11,
        token: 'tok-c1',
        change_token: 'chg-c1',
      },
      {
        patient_id: P4,
        patient_name: '番号 なし',
        week_start: '2026-11-30',
        urgent: false,
        detected_at: '2026-11-30T01:05:00Z',
        waiting_minutes: 90,
        changes: [
          {
            kind: 'cancel',
            from: { date: '2026-12-04', start: '11:00' },
            to: null,
            label: '12/4(金)11:00 お休み',
          },
        ],
        sendable: false,
        not_sendable_reason: 'no_contact',
        not_sendable_label: '送り先がありません',
        destinations: [],
        call_numbers: [],
        body: null,
        char_count: null,
        segments_est: null,
        est_yen: null,
        token: null,
        change_token: 'chg-c4',
      },
    ],
    ...over,
  };
}

const STATUS_LIVE: SmsStatus = {
  enabled: true,
  provider: 'kddi',
  send_mode: 'live',
  test_allowlist_count: 0,
  within_send_hours: true,
  send_hours: '8:00〜20:00',
  pending_count: 2,
  urgent_count: 1,
  failed_recent_count: 0,
};

type Handler = (path: string, init?: { method?: string; body?: string }) => unknown;
let handler: Handler;

function route(over: Partial<Record<string, Handler>> = {}): Handler {
  return (path, init) => {
    for (const [prefix, h] of Object.entries(over)) {
      if (path.startsWith(prefix) && h) return h(path, init);
    }
    if (path.startsWith('/api/v1/sms/status')) return STATUS_LIVE;
    if (path.startsWith('/api/v1/sms/week-notices/preview')) return preview();
    if (path.startsWith('/api/v1/sms/changes/pending')) return pendingList();
    throw new Error(`unexpected ${path}`);
  };
}

function bodyOf(path: string): unknown {
  const call = (fetcher as Mock).mock.calls.find((c) => c[0] === path);
  return call ? JSON.parse((call[1] as { body: string }).body) : undefined;
}

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const r = render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
  return { ...r, qc };
}

/** 手で終わらせる約束 (送っている最中を作るため)。 */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function sendCalls(path: string) {
  return (fetcher as Mock).mock.calls.filter((c) => c[0] === path);
}

const WEEK_SEND = '/api/v1/sms/week-notices/send';
const CHANGE_SEND = '/api/v1/sms/changes/send';

const WEEK_SENT = {
  batch_id: 'b1',
  week_start: '2026-11-30',
  patient_count: 2,
  message_count: 3,
  segments_est_total: 5,
};

const P5 = '55555555-5555-5555-5555-555555555555';

function extraRow() {
  return {
    patient_id: P5,
    patient_name: '新規 五郎',
    surname: '新規',
    state: 'new',
    destinations: [dest('0005')],
    visits: [{ date: '2026-12-05', start: '10:00' }],
    body: '本文その5',
    char_count: 50,
    segments_est: 1,
    message_count: 1,
    est_yen: 11,
    token: 'tok-w5',
  };
}

function setRole(role: string) {
  (useSession as Mock).mockReturnValue({
    data: { user: { role }, accessToken: 't', refreshToken: 'r' },
    status: 'authenticated',
  });
}

beforeEach(() => {
  setRole('admin');
  handler = route();
  (fetcher as Mock).mockImplementation(
    async (path: string, init?: { method?: string; body?: string }) => handler(path, init),
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

function openWeek(status: SmsStatus = STATUS_LIVE) {
  return renderWithClient(
    <SmsNoticeDialog
      open
      onOpenChange={() => undefined}
      weekStart="2026-11-30"
      status={status}
      initialTab="week"
    />,
  );
}

describe('週のお知らせ', () => {
  it('既定で印を付け、送信済みの方は外す・合計は印に付いてくる', async () => {
    openWeek();
    await screen.findByTestId('sms-week-tab');
    const box = (name: string) => screen.getByRole('checkbox', { name: `${name} 様に送る` });
    expect(box('見本 太郎')).toBeChecked();
    expect(box('試験 花子')).toBeChecked();
    expect(box('送済 次郎')).not.toBeChecked();
    expect(screen.getByTestId(`sms-week-state-${P3}`)).toHaveTextContent('送信済み');
    expect(screen.getByTestId(`sms-week-state-${P2}`)).toHaveTextContent('前回と内容が違います');
    // 本文は届く形のまま (改行を残す)
    expect(screen.getByTestId(`sms-week-body-${P1}`).textContent).toContain('\n12/1(火)10:00\n');

    const totals = screen.getByTestId('sms-week-totals');
    expect(totals).toHaveTextContent('送る方 2 名・3 件（5 通分）・見込み 55 円');
    fireEvent.click(box('見本 太郎'));
    expect(totals).toHaveTextContent('送る方 1 名・2 件（4 通分）・見込み 44 円');
    expect(screen.getByTestId('sms-week-send')).toHaveTextContent('1 名に送る');
  });

  it('確かめてから送る: 送るのは ID と件数だけ (本文は送らない)', async () => {
    handler = route({
      '/api/v1/sms/week-notices/send': () => ({
        batch_id: 'b1',
        week_start: '2026-11-30',
        patient_count: 3,
        message_count: 4,
        segments_est_total: 6,
      }),
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    // 送信済みの方にももう一度送る
    fireEvent.click(screen.getByRole('checkbox', { name: '送済 次郎 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-week-send'));

    const confirm = screen.getByTestId('sms-week-confirm');
    expect(within(confirm).getByTestId('sms-week-confirm-people')).toHaveTextContent('3 名');
    expect(within(confirm).getByTestId('sms-week-confirm-messages')).toHaveTextContent(
      '4 件（6 通分）',
    );
    expect(within(confirm).getByTestId('sms-week-confirm-yen')).toHaveTextContent('66 円');
    expect(confirm).toHaveTextContent('送ったあとは取り消せません');
    // まだ送っていない
    expect(bodyOf('/api/v1/sms/week-notices/send')).toBeUndefined();

    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    await waitFor(() => expect(bodyOf('/api/v1/sms/week-notices/send')).toBeDefined());
    const sent = bodyOf('/api/v1/sms/week-notices/send') as Record<string, unknown>;
    expect(sent).toEqual({
      week_start: '2026-11-30',
      patient_ids: [P1, P2, P3],
      expected_count: 4,
      resend: true,
      tokens: { [P1]: 'tok-w1', [P2]: 'tok-w2', [P3]: 'tok-w3' },
    });
    expect(JSON.stringify(sent)).not.toContain('本文');
    expect(Object.keys(sent)).not.toContain('body');
    // 送るのは 1 回だけ
    expect(
      (fetcher as Mock).mock.calls.filter((c) => c[0] === '/api/v1/sms/week-notices/send'),
    ).toHaveLength(1);
  });

  it('送信済みを外したままなら resend は false', async () => {
    handler = route({
      '/api/v1/sms/week-notices/send': () => ({
        batch_id: 'b1',
        week_start: '2026-11-30',
        patient_count: 2,
        message_count: 3,
        segments_est_total: 5,
      }),
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    await waitFor(() => expect(bodyOf('/api/v1/sms/week-notices/send')).toBeDefined());
    expect(bodyOf('/api/v1/sms/week-notices/send')).toMatchObject({
      patient_ids: [P1, P2],
      expected_count: 3,
      resend: false,
    });
  });

  it('送れる時間の外では押せず、理由を出す', async () => {
    handler = route({
      '/api/v1/sms/week-notices/preview': () => preview({ within_send_hours: false }),
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    expect(screen.getByTestId('sms-week-send')).toBeDisabled();
    expect(screen.getByTestId('sms-week-hours-reason')).toHaveTextContent(
      'お知らせを送れるのは 8:00〜20:00（日本時間）です',
    );
  });

  it('409 なら「一覧を開き直してください」を出し、一覧を読み込む', async () => {
    let previews = 0;
    handler = route({
      '/api/v1/sms/week-notices/preview': () => {
        previews += 1;
        return preview();
      },
      '/api/v1/sms/week-notices/send': () => {
        throw new ApiError('API 409', 409, {
          detail: '送る件数が変わりました（確認 3 件・いま 4 件）。一覧を開き直してください',
        });
      },
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    expect(previews).toBe(1);
    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    expect(await screen.findByTestId('sms-week-send-error')).toHaveTextContent(
      '一覧を開き直してください',
    );
    await waitFor(() => expect(previews).toBe(2));
  });

  it('422 はサーバーの文をそのまま出す', async () => {
    handler = route({
      '/api/v1/sms/week-notices/send': () => {
        throw new ApiError('API 422', 422, { detail: '送るお知らせがありません' });
      },
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    expect(await screen.findByTestId('sms-week-send-error')).toHaveTextContent(
      '送るお知らせがありません',
    );
  });

  it('送れない方は理由つきで別の段に出す', async () => {
    openWeek();
    const section = await screen.findByTestId('sms-week-not-sendable');
    expect(section).toHaveTextContent('SMS でお知らせできない方（1 名）');
    fireEvent.click(within(section).getByRole('button', { name: /お知らせできない方/ }));
    expect(section).toHaveTextContent('番号 なし 様');
    expect(section).toHaveTextContent('送り先がありません');
  });

  it('試験の状態の帯 (決めた番号の件数・記録だけのとき)', async () => {
    openWeek({ ...STATUS_LIVE, send_mode: 'test', provider: 'mock', test_allowlist_count: 2 });
    const banner = await screen.findByTestId('sms-test-banner');
    expect(banner).toHaveTextContent('試験の状態です。決めた番号（2 件）にだけ送ります');
    expect(banner).toHaveTextContent('いまは記録するだけで、実際には送りません');
  });

  it('本番の状態なら帯を出さない', async () => {
    openWeek();
    await screen.findByTestId('sms-week-tab');
    expect(screen.queryByTestId('sms-test-banner')).not.toBeInTheDocument();
  });
});

describe('変更のお知らせ', () => {
  function openChange() {
    return renderWithClient(
      <SmsNoticeDialog
        open
        onOpenChange={() => undefined}
        weekStart="2026-11-30"
        status={STATUS_LIVE}
        initialTab="change"
      />,
    );
  }

  it('急ぎの方に電話の番号を出し、選んだ方に送る (items と件数だけ)', async () => {
    handler = route({
      '/api/v1/sms/changes/send': () => ({
        batch_id: 'b2',
        patient_count: 1,
        message_count: 1,
        segments_est_total: 1,
      }),
    });
    openChange();
    await screen.findByTestId('sms-change-tab');
    expect(screen.getByRole('tab', { name: '変更のお知らせ（2）' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const row = screen.getByTestId(`sms-change-row-${P1}`);
    expect(row).toHaveTextContent('急ぎ（今日・明日）');
    expect(row).toHaveTextContent('気づいてから 42 分');
    expect(row).toHaveTextContent('12/3(木)14:00→15:30');
    expect(screen.getByTestId(`sms-change-call-${P1}`)).toHaveTextContent(
      'お電話でもお伝えください',
    );
    expect(screen.getByTestId(`sms-change-call-${P1}`)).toHaveTextContent('090-0000-0001');

    // 送れない方は印を付けられず、理由を出す
    const ng = screen.getByRole('checkbox', { name: '番号 なし 様に送る' });
    expect(ng).toBeDisabled();
    expect(screen.getByTestId(`sms-change-row-${P4}`)).toHaveTextContent('送り先がありません');

    expect(screen.getByTestId('sms-change-send')).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: '見本 太郎 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-change-send'));
    expect(screen.getByTestId('sms-change-confirm')).toHaveTextContent(
      '送ったあとは取り消せません',
    );
    fireEvent.click(screen.getByTestId('sms-change-confirm-ok'));
    await waitFor(() => expect(bodyOf('/api/v1/sms/changes/send')).toBeDefined());
    expect(bodyOf('/api/v1/sms/changes/send')).toEqual({
      items: [{ patient_id: P1, week_start: '2026-11-30', token: 'tok-c1' }],
      expected_count: 1,
    });
  });

  it('送らずに済みにする: 手段・理由・メモを送る (その他はメモが要る)', async () => {
    handler = route({ '/api/v1/sms/changes/resolve': () => ({ ok: true, version: 3 }) });
    openChange();
    await screen.findByTestId('sms-change-tab');
    fireEvent.click(screen.getByTestId(`sms-change-resolve-open-${P4}`));
    const form = screen.getByTestId(`sms-change-resolve-${P4}`);
    fireEvent.click(within(form).getByLabelText('お知らせしない'));
    fireEvent.click(within(form).getByLabelText('その他'));
    const save = screen.getByTestId(`sms-change-resolve-save-${P4}`);
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByTestId(`sms-change-resolve-note-${P4}`), {
      target: { value: '施設の方へ口頭で' },
    });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);
    await waitFor(() => expect(bodyOf('/api/v1/sms/changes/resolve')).toBeDefined());
    expect(bodyOf('/api/v1/sms/changes/resolve')).toEqual({
      patient_id: P4,
      week_start: '2026-11-30',
      channel: 'none',
      reason: 'other',
      note: '施設の方へ口頭で',
      change_token: 'chg-c4',
    });
  });

  it('送れる時間の外では押せない', async () => {
    handler = route({
      '/api/v1/sms/changes/pending': () => pendingList({ within_send_hours: false }),
    });
    openChange();
    await screen.findByTestId('sms-change-tab');
    fireEvent.click(screen.getByRole('checkbox', { name: '見本 太郎 様に送る' }));
    expect(screen.getByTestId('sms-change-send')).toBeDisabled();
    expect(screen.getByTestId('sms-change-hours-reason')).toBeInTheDocument();
  });
});

describe('送るときの守り', () => {
  it('「送る」を続けて押しても、送るのは 1 回だけ', async () => {
    const d = deferred<unknown>();
    handler = route({ [WEEK_SEND]: () => d.promise });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByTestId('sms-week-send'));
    const ok = screen.getByTestId('sms-week-confirm-ok');
    fireEvent.click(ok);
    fireEvent.click(ok);
    fireEvent.click(ok);
    await waitFor(() => expect(sendCalls(WEEK_SEND)).toHaveLength(1));
    await act(async () => {
      d.resolve(WEEK_SENT);
      await d.promise;
    });
    expect(sendCalls(WEEK_SEND)).toHaveLength(1);
  });

  it('確かめを開いたら「戻る」に合わせ、送信済みの方をふくむと添える', async () => {
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByRole('checkbox', { name: '送済 次郎 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-week-send'));
    expect(screen.getByTestId('sms-week-confirm-back')).toHaveFocus();
    expect(screen.getByTestId('sms-week-confirm-resend')).toHaveTextContent(
      '送信済みの方を 1 名ふくみます（もう一度送ります）',
    );
  });

  it('送信済みをふくまないときは添えない', async () => {
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByTestId('sms-week-send'));
    expect(screen.queryByTestId('sms-week-confirm-resend')).not.toBeInTheDocument();
  });

  it('通信が切れたら「送れたかどうか分かりません」・印を既定に戻す・一覧を読み込む', async () => {
    let previews = 0;
    handler = route({
      '/api/v1/sms/week-notices/preview': () => {
        previews += 1;
        return preview();
      },
      [WEEK_SEND]: () => {
        throw new TypeError('Failed to fetch');
      },
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    // 送信済みの方にも印を付けてから送る
    fireEvent.click(screen.getByRole('checkbox', { name: '送済 次郎 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    const err = await screen.findByTestId('sms-week-send-error');
    expect(err).toHaveTextContent(
      '送れたかどうか分かりません。一覧が新しくなったら「送信済み」を確かめてください',
    );
    expect(err).not.toHaveTextContent('送れませんでした');
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('送れたかどうか分かりません'));
    await waitFor(() => expect(previews).toBe(2));
    // 人が付けた印は消え、送信済みの方は既定どおり外れる
    expect(screen.getByRole('checkbox', { name: '送済 次郎 様に送る' })).not.toBeChecked();
    expect(screen.queryByTestId('sms-week-confirm')).not.toBeInTheDocument();
  });

  it('5xx も「送れたかどうか分かりません」', async () => {
    handler = route({
      [WEEK_SEND]: () => {
        throw new ApiError('API 502', 502, 'Bad Gateway');
      },
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    expect(await screen.findByTestId('sms-week-send-error')).toHaveTextContent(
      '送れたかどうか分かりません',
    );
  });

  it('送っているあいだは閉じられず、終わったら結果を出す (画面と toast)', async () => {
    const d = deferred<unknown>();
    handler = route({ [WEEK_SEND]: () => d.promise });
    const onOpenChange = vi.fn();
    renderWithClient(
      <SmsNoticeDialog
        open
        onOpenChange={onOpenChange}
        weekStart="2026-11-30"
        status={STATUS_LIVE}
        initialTab="week"
      />,
    );
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    await screen.findByTestId('sms-dialog-busy');

    const dialog = screen.getByTestId('sms-notice-dialog');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    await act(async () => {
      d.resolve(WEEK_SENT);
      await d.promise;
    });
    expect(await screen.findByTestId('sms-week-send-done')).toHaveTextContent(
      '2 名へのお知らせ（3 件）を受け付けました',
    );
    expect(toast.success).toHaveBeenCalledWith('2 名へのお知らせ（3 件）を受け付けました');
    await waitFor(() => expect(screen.queryByTestId('sms-dialog-busy')).not.toBeInTheDocument());

    fireEvent.keyDown(screen.getByTestId('sms-notice-dialog'), { key: 'Escape' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('送れなかったときも toast で知らせる', async () => {
    handler = route({
      [WEEK_SEND]: () => {
        throw new ApiError('API 422', 422, { detail: '送るお知らせがありません' });
      },
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    await screen.findByTestId('sms-week-send-error');
    expect(toast.error).toHaveBeenCalledWith('送るお知らせがありません');
  });

  it('確かめているあいだに一覧が新しくなったら、確かめを閉じる・新しく出た方に印を付けない', async () => {
    let second = false;
    handler = route({
      '/api/v1/sms/week-notices/preview': () =>
        second ? preview({ rows: [...preview().rows, extraRow()] }) : preview(),
    });
    const { qc } = openWeek();
    await screen.findByTestId('sms-week-tab');
    // 人が印を触る
    fireEvent.click(screen.getByRole('checkbox', { name: '試験 花子 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-week-send'));
    expect(screen.getByTestId('sms-week-confirm-people')).toHaveTextContent('1 名');

    second = true;
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ['sms', 'week-preview'] });
    });
    await waitFor(() => expect(screen.queryByTestId('sms-week-confirm')).not.toBeInTheDocument());
    expect(screen.getByTestId('sms-week-send-error')).toHaveTextContent(
      '一覧が新しくなりました。もう一度確かめてください',
    );
    expect(screen.getByRole('checkbox', { name: '新規 五郎 様に送る' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: '見本 太郎 様に送る' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '試験 花子 様に送る' })).not.toBeChecked();
    expect(sendCalls(WEEK_SEND)).toHaveLength(0);
  });

  it('一覧を読み込んでいるあいだは送れない', async () => {
    let n = 0;
    const d = deferred<unknown>();
    handler = route({
      '/api/v1/sms/week-notices/preview': () => {
        n += 1;
        return n === 1 ? preview() : d.promise;
      },
    });
    const { qc } = openWeek();
    await screen.findByTestId('sms-week-tab');
    expect(screen.getByTestId('sms-week-send')).not.toBeDisabled();
    act(() => {
      void qc.invalidateQueries({ queryKey: ['sms', 'week-preview'] });
    });
    await waitFor(() => expect(screen.getByTestId('sms-week-send')).toBeDisabled());
    await act(async () => {
      d.resolve(preview());
      await d.promise;
    });
    await waitFor(() => expect(screen.getByTestId('sms-week-send')).not.toBeDisabled());
  });

  it('409 の後: 確かめを閉じ、人の選び方を残し、新しく出た方に印を付けない', async () => {
    let afterConflict = false;
    handler = route({
      '/api/v1/sms/week-notices/preview': () =>
        afterConflict ? preview({ rows: [...preview().rows, extraRow()] }) : preview(),
      [WEEK_SEND]: () => {
        afterConflict = true;
        throw new ApiError('API 409', 409, {
          detail: '予定か連絡先が変わりました。一覧を開き直してください',
        });
      },
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByRole('checkbox', { name: '送済 次郎 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    expect(await screen.findByTestId('sms-week-send-error')).toHaveTextContent(
      '予定か連絡先が変わりました。一覧を開き直してください',
    );
    await screen.findByRole('checkbox', { name: '新規 五郎 様に送る' });
    expect(screen.queryByTestId('sms-week-confirm')).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '新規 五郎 様に送る' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: '送済 次郎 様に送る' })).toBeChecked();
    expect(toast.error).toHaveBeenCalled();
  });

  it('一部の欄が null・欠けていても一覧を出す', async () => {
    const rows = preview().rows;
    handler = route({
      '/api/v1/sms/week-notices/preview': () =>
        preview({
          rows: [{ ...rows[0], body: null, char_count: null, est_yen: undefined }],
          not_sendable: [
            {
              patient_id: P4,
              patient_name: '番号 なし',
              reason: 'no_contact',
              reason_label: null,
              visits: null,
            },
          ],
          totals: null,
        }),
    });
    openWeek();
    expect(await screen.findByTestId('sms-week-tab')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '見本 太郎 様に送る' })).toBeChecked();
  });
});

describe('変更のお知らせの守り', () => {
  function openChange() {
    return renderWithClient(
      <SmsNoticeDialog
        open
        onOpenChange={() => undefined}
        weekStart="2026-11-30"
        status={STATUS_LIVE}
        initialTab="change"
      />,
    );
  }

  it('待ちの分が小数でも丸めて出す', async () => {
    const base = pendingList();
    handler = route({
      '/api/v1/sms/changes/pending': () =>
        pendingList({ items: [{ ...base.items[0], waiting_minutes: 42.6 }, base.items[1]] }),
    });
    openChange();
    await screen.findByTestId('sms-change-tab');
    expect(screen.getByTestId(`sms-change-row-${P1}`)).toHaveTextContent('気づいてから 43 分');
  });

  it('確かめているあいだに一覧が新しくなったら閉じ、送らない', async () => {
    const { qc } = openChange();
    await screen.findByTestId('sms-change-tab');
    fireEvent.click(screen.getByRole('checkbox', { name: '見本 太郎 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-change-send'));
    expect(screen.getByTestId('sms-change-confirm')).toBeInTheDocument();
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ['sms', 'changes', 'pending'] });
    });
    await waitFor(() => expect(screen.queryByTestId('sms-change-confirm')).not.toBeInTheDocument());
    expect(screen.getByTestId('sms-change-send-error')).toHaveTextContent(
      '一覧が新しくなりました。もう一度確かめてください',
    );
    expect(sendCalls(CHANGE_SEND)).toHaveLength(0);
  });

  it('通信が切れたら「送れたかどうか分かりません」で印を外す', async () => {
    handler = route({
      [CHANGE_SEND]: () => {
        throw new TypeError('Failed to fetch');
      },
    });
    openChange();
    await screen.findByTestId('sms-change-tab');
    fireEvent.click(screen.getByRole('checkbox', { name: '見本 太郎 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-change-send'));
    fireEvent.click(screen.getByTestId('sms-change-confirm-ok'));
    expect(await screen.findByTestId('sms-change-send-error')).toHaveTextContent(
      '送れたかどうか分かりません',
    );
    expect(screen.getByRole('checkbox', { name: '見本 太郎 様に送る' })).not.toBeChecked();
  });

  it('済みにするときは、フォームを開いた時点の変更の印を送り、409 はサーバーの言葉を出す', async () => {
    let second = false;
    const base = pendingList();
    handler = route({
      '/api/v1/sms/changes/pending': () =>
        second
          ? pendingList({ items: [base.items[0], { ...base.items[1], change_token: 'chg-new' }] })
          : base,
      '/api/v1/sms/changes/resolve': () => {
        throw new ApiError('API 409', 409, {
          detail: '予定か連絡先が変わりました。一覧を開き直してください',
        });
      },
    });
    const { qc } = openChange();
    await screen.findByTestId('sms-change-tab');
    fireEvent.click(screen.getByTestId(`sms-change-resolve-open-${P4}`));
    second = true;
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ['sms', 'changes', 'pending'] });
    });
    fireEvent.click(screen.getByTestId(`sms-change-resolve-save-${P4}`));
    await waitFor(() => expect(bodyOf('/api/v1/sms/changes/resolve')).toBeDefined());
    expect(bodyOf('/api/v1/sms/changes/resolve')).toMatchObject({ change_token: 'chg-c4' });
    const form = screen.getByTestId(`sms-change-resolve-${P4}`);
    expect(await within(form).findByRole('alert')).toHaveTextContent(
      '予定か連絡先が変わりました。一覧を開き直してください',
    );
    expect(toast.error).toHaveBeenCalled();
  });
});

describe('タブ', () => {
  it('矢印のキーで隣のタブへ動く', async () => {
    openWeek();
    await screen.findByTestId('sms-week-tab');
    const weekTab = screen.getByRole('tab', { name: '週のお知らせ' });
    expect(weekTab).toHaveAttribute('tabindex', '0');
    weekTab.focus();
    fireEvent.keyDown(weekTab, { key: 'ArrowRight' });
    const changeTab = screen.getByRole('tab', { name: /変更のお知らせ/ });
    expect(changeTab).toHaveAttribute('aria-selected', 'true');
    expect(changeTab).toHaveFocus();
    expect(weekTab).toHaveAttribute('tabindex', '-1');
    fireEvent.keyDown(changeTab, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: '週のお知らせ' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });
});

describe('［予定のお知らせ］ボタン', () => {
  it('旗が OFF なら出さない', async () => {
    handler = route({ '/api/v1/sms/status': () => ({ enabled: false }) });
    renderWithClient(<SmsNoticeButton weekStart="2026-11-30" />);
    await waitFor(() =>
      expect(fetcher as Mock).toHaveBeenCalledWith('/api/v1/sms/status', expect.anything()),
    );
    expect(screen.queryByTestId('sms-notice-button')).not.toBeInTheDocument();
  });

  it('管理者でなければ出さず、問い合わせもしない', () => {
    setRole('staff');
    renderWithClient(<SmsNoticeButton weekStart="2026-11-30" />);
    expect(screen.queryByTestId('sms-notice-button')).not.toBeInTheDocument();
    expect(fetcher as Mock).not.toHaveBeenCalled();
  });

  it('お知らせ待ちの件数を出し、急ぎがあれば強い色にする', async () => {
    renderWithClient(<SmsNoticeButton weekStart="2026-11-30" />);
    const badge = await screen.findByTestId('sms-notice-badge');
    expect(badge).toHaveTextContent('2');
    expect(badge).toHaveAttribute('data-urgent', 'true');
    expect(badge.className).toContain('bg-red-600');
  });

  it('急ぎがなければふつうの色', async () => {
    handler = route({
      '/api/v1/sms/status': () => ({ ...STATUS_LIVE, pending_count: 1, urgent_count: 0 }),
    });
    renderWithClient(<SmsNoticeButton weekStart="2026-11-30" />);
    const badge = await screen.findByTestId('sms-notice-badge');
    expect(badge).toHaveAttribute('data-urgent', 'false');
  });
});

describe('レビューで決めた直し (決まり §13)', () => {
  function stateRows() {
    const base = preview().rows;
    return [
      base[0],
      {
        ...base[1],
        patient_id: P5,
        patient_name: '途中 六郎',
        state: 'sending',
        token: 'tok-w5',
      },
      {
        ...base[2],
        patient_id: P2,
        patient_name: '未着 花子',
        state: 'not_delivered',
        last_attempt: {
          at: '2026-11-30T01:00:00Z',
          result: 'failed',
          label: '届きませんでした',
        },
        surname_uncertain: true,
        token: 'tok-w2',
      },
      { ...base[2], patient_id: P3, state: 'something_new', token: 'tok-w3' },
    ];
  }

  it('送っている途中の方は選べず、送る中身に入れない・知らない状態も同じ', async () => {
    handler = route({
      '/api/v1/sms/week-notices/preview': () => preview({ rows: stateRows() }),
      '/api/v1/sms/week-notices/send': () => WEEK_SENT,
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    const sending = screen.getByRole('checkbox', { name: '途中 六郎 様に送る' });
    expect(sending).not.toBeChecked();
    expect(sending).toBeDisabled();
    fireEvent.click(sending);
    expect(sending).not.toBeChecked();
    expect(screen.getByTestId(`sms-week-state-${P5}`)).toHaveTextContent('送っている途中です');
    // 知らない状態は「送っている途中」とみなす
    const unknown = screen.getByRole('checkbox', { name: '送済 次郎 様に送る' });
    expect(unknown).toBeDisabled();
    expect(unknown).not.toBeChecked();

    fireEvent.click(screen.getByTestId('sms-week-send'));
    fireEvent.click(screen.getByTestId('sms-week-confirm-ok'));
    await waitFor(() => expect(bodyOf(WEEK_SEND)).toBeDefined());
    const sent = bodyOf(WEEK_SEND) as { patient_ids: string[]; tokens: Record<string, string> };
    expect(sent.patient_ids).toEqual([P1, P2]);
    expect(Object.keys(sent.tokens)).not.toContain(P5);
    expect(sent).toMatchObject({ resend: false });
  });

  it('前回届かなかった方は既定で印を付け、理由と時刻を出し、確かめで人数を添える', async () => {
    handler = route({
      '/api/v1/sms/week-notices/preview': () => preview({ rows: stateRows() }),
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    expect(screen.getByRole('checkbox', { name: '未着 花子 様に送る' })).toBeChecked();
    expect(screen.getByTestId(`sms-week-state-${P2}`)).toHaveTextContent('前回は届いていません');
    const last = screen.getByTestId(`sms-week-last-attempt-${P2}`);
    expect(last).toHaveTextContent('届きませんでした');
    expect(last).toHaveTextContent('11/30');
    expect(last).toHaveTextContent('10:00');
    expect(screen.getByTestId(`sms-week-surname-note-${P2}`)).toHaveTextContent(
      'お名前に空白が無いため、姓の代わりにお名前全体を入れています',
    );
    expect(screen.queryByTestId(`sms-week-surname-note-${P1}`)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('sms-week-send'));
    expect(screen.getByTestId('sms-week-confirm-not-delivered')).toHaveTextContent(
      '前回届かなかった方を 1 名ふくみます',
    );
    expect(screen.queryByTestId('sms-week-confirm-resend')).not.toBeInTheDocument();
  });

  it('届かなかった方を外すと、確かめで添えない', async () => {
    handler = route({
      '/api/v1/sms/week-notices/preview': () => preview({ rows: stateRows() }),
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByRole('checkbox', { name: '未着 花子 様に送る' }));
    fireEvent.click(screen.getByTestId('sms-week-send'));
    expect(screen.queryByTestId('sms-week-confirm-not-delivered')).not.toBeInTheDocument();
  });

  it('お電話でお伝えする方の印刷に、前回届かなかった方も載せる', async () => {
    const createObjectURL = vi.fn(() => 'blob:x');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const win = { location: { href: '' }, opener: {} };
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    handler = route({
      '/api/v1/sms/week-notices/preview': () => preview({ rows: stateRows() }),
    });
    openWeek();
    await screen.findByTestId('sms-week-tab');
    fireEvent.click(screen.getByTestId('sms-week-print'));
    expect(openSpy).toHaveBeenCalled();
    const blob = (createObjectURL.mock.calls[0] as unknown as [Blob])[0];
    const html = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blob);
    });
    expect(html).toContain('番号 なし 様');
    expect(html).toContain('未着 花子 様');
    expect(html).toContain('前回のお知らせが届いていません');
    openSpy.mockRestore();
  });

  it('送れない理由はサーバーの言葉を使い、無いときは決まりの言葉にする', async () => {
    handler = route({
      '/api/v1/sms/week-notices/preview': () =>
        preview({
          not_sendable: [
            {
              patient_id: P4,
              patient_name: '番号 なし',
              reason: 'unsupported_char',
              reason_label: null,
              visits: [],
            },
          ],
        }),
      '/api/v1/sms/changes/pending': () => {
        const base = pendingList();
        return {
          ...base,
          items: [
            base.items[0],
            { ...base.items[1], not_sendable_reason: 'patient_inactive', not_sendable_label: null },
          ],
        };
      },
    });
    openWeek();
    const section = await screen.findByTestId('sms-week-not-sendable');
    fireEvent.click(within(section).getByRole('button', { name: /お知らせできない方/ }));
    expect(section).toHaveTextContent('お名前などに、SMS で送れない文字があります');

    fireEvent.click(screen.getByRole('tab', { name: /変更のお知らせ/ }));
    await screen.findByTestId('sms-change-tab');
    expect(screen.getByTestId(`sms-change-row-${P4}`)).toHaveTextContent(
      'いまは稼働中でない方です（お電話でお伝えください）',
    );
  });

  it('SMS の設定に誤りがあるか足りないときは帯を出し、送れなくする', async () => {
    openWeek({ ...STATUS_LIVE, provider_ok: false });
    await screen.findByTestId('sms-week-tab');
    expect(screen.getByTestId('sms-provider-banner')).toHaveTextContent(
      'SMS の設定に誤りがあるか、足りないため、いまは送れません。管理者に確かめてください',
    );
    expect(screen.getByTestId('sms-week-send')).toBeDisabled();
    fireEvent.click(screen.getByRole('tab', { name: /変更のお知らせ/ }));
    await screen.findByTestId('sms-change-tab');
    fireEvent.click(screen.getByRole('checkbox', { name: '見本 太郎 様に送る' }));
    expect(screen.getByTestId('sms-change-send')).toBeDisabled();
  });

  it('設定がそろっていれば帯を出さない・届いていない件数を記録へのリンクで出す', async () => {
    openWeek({ ...STATUS_LIVE, provider_ok: true, undelivered_count: 3 });
    await screen.findByTestId('sms-week-tab');
    expect(screen.queryByTestId('sms-provider-banner')).not.toBeInTheDocument();
    const link = screen.getByTestId('sms-undelivered-link');
    expect(link).toHaveTextContent('届いていないお知らせ 3 件');
    expect(link.getAttribute('href')).toBe('/sms-notices?tab=messages&undelivered=1');
  });

  it('届いていない件数が 0 ならリンクを出さない', async () => {
    openWeek({ ...STATUS_LIVE, undelivered_count: 0 });
    await screen.findByTestId('sms-week-tab');
    expect(screen.queryByTestId('sms-undelivered-link')).not.toBeInTheDocument();
  });

  it('済みにする: 手段で選べる理由が決まる (お電話 ↔ お電話でお伝えした)', async () => {
    handler = route({ '/api/v1/sms/changes/resolve': () => ({ ok: true, version: 3 }) });
    renderWithClient(
      <SmsNoticeDialog
        open
        onOpenChange={() => undefined}
        weekStart="2026-11-30"
        status={STATUS_LIVE}
        initialTab="change"
      />,
    );
    await screen.findByTestId('sms-change-tab');
    fireEvent.click(screen.getByTestId(`sms-change-resolve-open-${P4}`));
    const form = screen.getByTestId(`sms-change-resolve-${P4}`);
    const reasons = () =>
      within(form.querySelectorAll('fieldset')[1] as HTMLElement)
        .getAllByRole('radio')
        .map((r) => r.parentElement?.textContent);

    // 既定はお電話: 理由は「お電話でお伝えした」だけ
    expect(reasons()).toEqual(['お電話でお伝えした']);

    // お知らせしない: お申し出・その他だけ。理由はお申し出に替わる
    fireEvent.click(within(form).getByLabelText('お知らせしない'));
    expect(reasons()).toEqual(['ご本人・ご家族からのお申し出', 'その他']);
    expect(within(form).getByLabelText('ご本人・ご家族からのお申し出')).toBeChecked();

    // その他を選んでからお電話に戻すと、理由はお電話でお伝えしたに戻る
    fireEvent.click(within(form).getByLabelText('その他'));
    fireEvent.click(within(form).getAllByLabelText('お電話でお伝えした')[0] as HTMLElement);
    expect(reasons()).toEqual(['お電話でお伝えした']);

    fireEvent.click(screen.getByTestId(`sms-change-resolve-save-${P4}`));
    await waitFor(() => expect(bodyOf('/api/v1/sms/changes/resolve')).toBeDefined());
    expect(bodyOf('/api/v1/sms/changes/resolve')).toMatchObject({
      channel: 'phone',
      reason: 'phoned',
    });
  });

  it('お知らせしない＋お申し出で済みにする', async () => {
    handler = route({ '/api/v1/sms/changes/resolve': () => ({ ok: true, version: 3 }) });
    renderWithClient(
      <SmsNoticeDialog
        open
        onOpenChange={() => undefined}
        weekStart="2026-11-30"
        status={STATUS_LIVE}
        initialTab="change"
      />,
    );
    await screen.findByTestId('sms-change-tab');
    fireEvent.click(screen.getByTestId(`sms-change-resolve-open-${P4}`));
    const form = screen.getByTestId(`sms-change-resolve-${P4}`);
    fireEvent.click(within(form).getByLabelText('お知らせしない'));
    fireEvent.click(screen.getByTestId(`sms-change-resolve-save-${P4}`));
    await waitFor(() => expect(bodyOf('/api/v1/sms/changes/resolve')).toBeDefined());
    expect(bodyOf('/api/v1/sms/changes/resolve')).toMatchObject({
      channel: 'none',
      reason: 'patient_request',
    });
  });
});
