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

vi.mock('@/lib/queries/me', () => ({
  useMyVisits: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  todayIso: () => '2026-08-16',
}));

import { fetcher } from '@/lib/api/fetcher';
import { toast } from '@/components/ui/sonner';
import { enqueuePending } from '@/lib/checkin-queue';
import { useMyVisits, type MyVisit } from '@/lib/queries/me';
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
  window.localStorage.clear();
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
    await waitFor(() => expect(screen.getByText('本日の訪問はありません')).toBeInTheDocument());
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
    expect(screen.getByText('本日の訪問はありません')).toBeInTheDocument();
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
