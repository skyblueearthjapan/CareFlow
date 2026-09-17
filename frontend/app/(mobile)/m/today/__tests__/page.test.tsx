/**
 * 今日の訪問 (一覧) — 未送信打刻の自動再送 + 独立 QR 読取入口のテスト.
 *
 * 圏外で退避した打刻 (訪問詳細の到着/退出・/q の予定外) は、この一覧が
 * マウントされたときと `online` イベントで再送される。「電波が戻り次第、
 * 自動で送信します」の約束を実装で担保している箇所なので回帰させない。
 *
 * QR 読取入口は「読み取って /q/{token} へ飛ばす」だけの導線。振り分け
 * (担当 visit 直行/代行/予定外/エラー) は /q 側の責務なので、ここでは
 * 表示・起動・遷移先・不正 QR で遷移しないことだけを担保する。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

import type * as ReactQueryModule from '@tanstack/react-query';

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
}));

// 実カメラ (html5-qrcode) は jsdom で動かないためコンポーネントごと差し替える。
// 有効な QR (アプリの /q/ URL) と別サイトの QR を撃ち分けられるようにする。
vi.mock('@/components/mobile/QrScanner', () => ({
  QrScanner: ({
    onScan,
    onManual,
    onCancel,
  }: {
    onScan: (t: string) => void;
    onManual?: () => void;
    onCancel: () => void;
  }) => (
    <div data-testid="qr-scanner">
      <button onClick={() => onScan('https://rakusuke.example/q/TESTTOKEN')}>__scan__</button>
      <button onClick={() => onScan('https://example.com/not-rakusuke')}>__scan-other__</button>
      {onManual && <button onClick={onManual}>__manual__</button>}
      <button onClick={onCancel}>__cancel__</button>
    </div>
  ),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1', role: 'staff' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

const qcStub = { invalidateQueries: vi.fn() };
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof ReactQueryModule>();
  return { ...actual, useQueryClient: () => qcStub };
});

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// 音声記録 (設計 2026-09-17): 🎙 マーク用の一覧。既定は 0 件。
vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: vi.fn(() => ({ data: { items: [], total: 0 } })),
}));

// 音声の未送信 / 送れなかった録音 (レビュー C-3)。件数はテストごとに差し替える。
const voiceFlushStub = {
  pendingCount: 0,
  failedCount: 0,
  flushNow: vi.fn(async () => undefined),
  refreshPending: vi.fn(async () => undefined),
};
vi.mock('@/lib/voice/queue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/voice/queue')>();
  return { ...actual, useVoiceFlush: () => voiceFlushStub };
});

vi.mock('@/lib/queries/me', () => ({
  useMyVisits: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  // 職員イベント / 休み・時間変更 (design 2026-09-16 §3 C-1)。既定は 0 件。
  useMyStaffEvents: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  useMyOverrides: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  todayIso: () => '2026-08-16',
}));

import { fetcher } from '@/lib/api/fetcher';
import { toast } from '@/components/ui/sonner';
import { enqueuePending } from '@/lib/checkin-queue';
import { useMyOverrides, useMyStaffEvents, useMyVisits, type MyVisit } from '@/lib/queries/me';
import MobileTodayPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

const NO_VISITS = { data: [], isLoading: false, isError: false, error: null };

function enqueueOne() {
  enqueuePending('staff-1', {
    visit_id: 'visit-1',
    kind: 'arrival',
    payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK' },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  // clearAllMocks は implementation を消さないため、訪問ありに差し替えたテストの
  // 影響が後続へ漏れないよう毎回「0 件」へ戻す (既存の防衛パターン)。
  asMock(useMyVisits).mockImplementation(() => NO_VISITS);
  asMock(useMyStaffEvents).mockImplementation(() => NO_VISITS);
  asMock(useMyOverrides).mockImplementation(() => NO_VISITS);
  window.localStorage.clear();
  voiceFlushStub.pendingCount = 0;
  voiceFlushStub.failedCount = 0;
});

describe('今日の訪問 — 未送信の再送', () => {
  it('保留があればマウント時に再送し、送信できれば残らない', async () => {
    enqueueOne();
    asMock(fetcher).mockResolvedValue({});
    render(<MobileTodayPage />);
    await waitFor(() =>
      expect(asMock(fetcher)).toHaveBeenCalledWith(
        '/api/v1/visits/visit-1/checkin',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    await waitFor(() => expect(window.localStorage.getItem('checkin-pending:staff-1')).toBeNull());
  });

  it('圏外のままなら未送信バナーを出し、online で再送する', async () => {
    enqueueOne();
    // マウント時は届かない → 保留のまま (バナー表示)。
    asMock(fetcher).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    render(<MobileTodayPage />);
    await waitFor(() => expect(screen.getByTestId('today-pending-banner')).toBeInTheDocument());
    expect(screen.getByText('未送信 1 件・電波が戻ると自動で送信します')).toBeInTheDocument();

    // 電波復帰 → 自動で再送し、バナーが消える。
    asMock(fetcher).mockResolvedValue({});
    await act(async () => {
      window.dispatchEvent(new Event('online'));
    });
    await waitFor(() =>
      expect(screen.queryByTestId('today-pending-banner')).not.toBeInTheDocument(),
    );
    expect(asMock(fetcher)).toHaveBeenCalledTimes(2);
  });

  it('保留が無ければバナーを出さない', async () => {
    asMock(fetcher).mockResolvedValue({});
    render(<MobileTodayPage />);
    await waitFor(() => expect(screen.getByText('本日の患者訪問はありません')).toBeInTheDocument());
    expect(screen.queryByTestId('today-pending-banner')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 独立 QR 読取入口 — 本日の訪問が無い患者 (担当外・予定外) の QR を読むための導線。
// ---------------------------------------------------------------------------
function makeVisit(): MyVisit {
  return {
    id: 'visit-1',
    visit_date: '2026-08-16',
    start_time: '09:00:00',
    end_time: '10:00:00',
    status: 'planned',
    patient_name: '山田 花子',
  } as unknown as MyVisit;
}

function openScanner() {
  fireEvent.click(screen.getByRole('button', { name: 'QRを読み取る' }));
}

/**
 * マウント時の未送信フラッシュ (useCheckinFlush) が非同期に state を更新するため、
 * render 直後に一度 flush しておかないと後続の操作が act 警告を出す。
 */
async function renderToday() {
  const view = render(<MobileTodayPage />);
  await act(async () => {});
  return view;
}

describe('今日の訪問 — QR読取の入口', () => {
  it('訪問が0件でも訪問があってもボタンが出る', async () => {
    const { unmount } = await renderToday();
    expect(screen.getByText('本日の患者訪問はありません')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'QRを読み取る' })).toBeInTheDocument();
    unmount();

    asMock(useMyVisits).mockImplementation(() => ({
      data: [makeVisit()],
      isLoading: false,
      isError: false,
      error: null,
    }));
    await renderToday();
    expect(screen.getByRole('button', { name: 'QRを読み取る' })).toBeInTheDocument();
  });

  it('タップでスキャナが開き、閉じると一覧へ戻る', async () => {
    await renderToday();
    expect(screen.queryByTestId('qr-scanner')).not.toBeInTheDocument();

    openScanner();
    expect(screen.getByTestId('qr-scanner')).toBeInTheDocument();
    // 担当外は QR 必須 — 手動フォールバックは出さない (設計 決定#6)。
    expect(screen.queryByText('__manual__')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('__cancel__'));
    expect(screen.queryByTestId('qr-scanner')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'QRを読み取る' })).toBeInTheDocument();
  });

  it('読取成功で /q/{token} へ遷移する（振り分けは /q 側の責務）', async () => {
    await renderToday();
    openScanner();
    fireEvent.click(screen.getByText('__scan__'));
    expect(routerPush).toHaveBeenCalledWith('/q/TESTTOKEN');
    expect(screen.queryByTestId('qr-scanner')).not.toBeInTheDocument();
  });

  it('らく助以外のQRでは遷移せず、案内だけ出す', async () => {
    await renderToday();
    openScanner();
    fireEvent.click(screen.getByText('__scan-other__'));
    expect(routerPush).not.toHaveBeenCalled();
    expect(asMock(toast.error)).toHaveBeenCalledWith('らく助のQRではありません', expect.anything());
  });
});

// ---------------------------------------------------------------------------
// 職員イベント / 休み・時間変更の混在表示 (design 2026-09-16 §3 C-3)
// ---------------------------------------------------------------------------
function makeEvent(over: Record<string, unknown> & { id: string }) {
  return {
    staff_id: 'staff-1',
    date: '2026-08-16',
    title: '朝会',
    start_time: '08:30',
    end_time: '09:00',
    type: 'イベント',
    source: 'manual',
    blocking: false,
    cancelled_at: null,
    ...over,
  };
}

function withData(data: unknown[]) {
  return { data, isLoading: false, isError: false, error: null };
}

describe('今日の訪問 — 職員イベントの混在', () => {
  it('訪問カードとイベントチップを開始時刻順に混ぜる (件数は訪問のまま)', async () => {
    asMock(useMyVisits).mockImplementation(() => withData([makeVisit()])); // 09:00
    asMock(useMyStaffEvents).mockImplementation(() =>
      withData([makeEvent({ id: 'ev1', start_time: '08:00', end_time: '08:30' })]),
    );
    await renderToday();

    const chip = screen.getByTestId('mobile-event-chip-ev1');
    expect(chip).toHaveTextContent('朝会');
    // 08:00 のイベントが 09:00 の訪問より前に並ぶ。
    expect(chip.compareDocumentPosition(screen.getByText('山田 花子'))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    // subtitle の件数は訪問件数のまま (イベントは数えない)。
    expect(screen.getByText('2026-08-16 ・ 1件')).toBeInTheDocument();
  });

  it('二重登録のイベントは 1 件に畳む (カイポケ優先)', async () => {
    asMock(useMyStaffEvents).mockImplementation(() =>
      withData([
        makeEvent({ id: 'manual-row', source: 'manual' }),
        makeEvent({ id: 'kaipoke-row', source: 'kaipoke' }),
      ]),
    );
    await renderToday();
    expect(screen.getByTestId('mobile-event-chip-kaipoke-row')).toBeInTheDocument();
    expect(screen.queryByTestId('mobile-event-chip-manual-row')).not.toBeInTheDocument();
  });

  it('cancelled_at のイベントは「今週除外」バッジつきで描く', async () => {
    asMock(useMyStaffEvents).mockImplementation(() =>
      withData([makeEvent({ id: 'off', cancelled_at: '2026-08-15T00:00:00Z' })]),
    );
    await renderToday();
    const chip = screen.getByTestId('mobile-event-chip-off');
    expect(chip).toHaveTextContent('今週除外');
    expect(chip.querySelector('.line-through')).not.toBeNull();
  });

  it('休みは見出しの右にバッジを出す', async () => {
    asMock(useMyOverrides).mockImplementation(() =>
      withData([{ id: 'o1', date: '2026-08-16', type: '休み' }]),
    );
    await renderToday();
    expect(screen.getByTestId('today-override-badge')).toHaveTextContent('🛌休み');
  });

  it('訪問 0 件でもイベントがあれば空カードを出さない (2026-09-16 MEDIUM-9)', async () => {
    asMock(useMyStaffEvents).mockImplementation(() => withData([makeEvent({ id: 'ev-only' })]));
    await renderToday();
    expect(screen.getByTestId('mobile-event-chip-ev-only')).toBeInTheDocument();
    expect(screen.queryByText('本日の患者訪問はありません')).not.toBeInTheDocument();
  });

  it('時刻が欠けたイベント行が混ざっても訪問と正常なイベントは描かれる', async () => {
    asMock(useMyVisits).mockImplementation(() => withData([makeVisit()]));
    asMock(useMyStaffEvents).mockImplementation(() =>
      withData([
        // BE / 旧デプロイ由来の壊れた行 (start_time 欠損)。me.ts の safeParse で
        // 本来は落ちるが、素通りしても画面を道連れにしないことをここで担保する。
        makeEvent({ id: 'broken', start_time: undefined, title: '壊れた行' }),
        makeEvent({ id: 'ok', start_time: '08:00', end_time: '08:30' }),
      ]),
    );
    await renderToday();
    expect(screen.getByText('山田 花子')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-event-chip-ok')).toHaveTextContent('朝会');
  });

  it('イベント取得に失敗しても Alert を出さず訪問だけ描く', async () => {
    asMock(useMyVisits).mockImplementation(() => withData([makeVisit()]));
    asMock(useMyStaffEvents).mockImplementation(() => ({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('boom'),
    }));
    await renderToday();
    expect(screen.getByText('山田 花子')).toBeInTheDocument();
    expect(screen.queryByText('取得に失敗しました')).not.toBeInTheDocument();
  });
});

describe('今日の訪問 — 送れなかった録音 (C-3)', () => {
  it('0 件のときはバナーを出さない', () => {
    render(<MobileTodayPage />);

    expect(screen.queryByTestId('today-voice-failed-banner')).not.toBeInTheDocument();
  });

  it('件数を出し、タップで一覧を開ける', async () => {
    voiceFlushStub.failedCount = 2;
    render(<MobileTodayPage />);

    const banner = screen.getByTestId('today-voice-failed-banner');
    expect(banner).toHaveTextContent('送れなかった録音 2 件');

    fireEvent.click(banner);

    expect(await screen.findByTestId('voice-failed-sheet')).toBeInTheDocument();
  });
});
