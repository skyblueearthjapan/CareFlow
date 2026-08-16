/**
 * `/q/{token}` ランディングページ — ディープリンク解決のレンダーテスト.
 *
 *   1. 自分の担当候補 → `/m/today/{visitId}?qr={token}` へ replace (extractQrToken が
 *      パス断片の生トークンをそのまま通すことの結線確認)。
 *   2. 候補ゼロ / 担当外 → 代行・予定外の選択画面 (患者氏名 + 当日予定)。
 *   3. 代行選択 → `/m/today/{visitId}?qr={token}`・訪問中は「退出の記録へ」。
 *   4. 予定外 → GPS プレビュー → adhoc-checkin POST → 生成 visit へ遷移。
 *   5. 圏外 (通信断) → 「予定外の訪問として記録」で退避 (adhoc_arrival)。
 *   6. 404 / 410 / 403 の案内。
 *   7. pickQrCandidate 単体 — is_mine + completed スキップの選択規則。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError } from '@/lib/api-client';
import { pickQrCandidate, type QrResolveCandidate } from '@/lib/queries/qrResolve';

// --- module mocks ----------------------------------------------------------
let tokenParam = 'TOK123';
const routerReplace = vi.fn();
vi.mock('next/navigation', () => ({
  useParams: () => ({ token: tokenParam }),
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1', role: 'staff' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import { fetcher } from '@/lib/api/fetcher';
import { toast } from '@/components/ui/sonner';
import QrLandingPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

const RESOLVE_PATH = '/api/v1/visits/resolve-qr/TOK123';
const ADHOC_PATH = '/api/v1/visits/adhoc-checkin';
const GEO = { lat: 35.1, lng: 140.1, accuracy: 12 };

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <QrLandingPage />
    </QueryClientProvider>,
  );
}

function cand(visit_id: string, overrides: Partial<QrResolveCandidate> = {}): QrResolveCandidate {
  return {
    visit_id,
    start_time: '09:00:00',
    end_time: '10:00:00',
    status: 'planned',
    planned_staff_name: '山田 花子',
    is_mine: false,
    is_unplanned: false,
    ...overrides,
  };
}

/**
 * `fetcher` を「パス別」に応答させる。resolve は既定で担当外 1 件、
 * checkin-settings/public は既定しきい値、adhoc は生成 visit を返す。
 */
function mockApi(options: {
  resolve?: unknown;
  resolveError?: unknown;
  adhoc?: unknown;
  adhocError?: unknown;
}) {
  asMock(fetcher).mockImplementation(async (path: string) => {
    if (path.startsWith('/api/v1/visits/resolve-qr/')) {
      if (options.resolveError) throw options.resolveError;
      return options.resolve ?? { patient_name: '田中 太郎', candidates: [] };
    }
    if (path === ADHOC_PATH) {
      if (options.adhocError) throw options.adhocError;
      return options.adhoc ?? { id: 'visit-adhoc-1' };
    }
    if (path.startsWith('/api/v1/checkin-settings')) {
      return { match_m: 100, review_m: 300, accuracy_m: 50 };
    }
    return {};
  });
}

/** 測位モック (成功 / 拒否)。 */
function mockGeolocation(mode: 'ok' | 'deny' = 'ok') {
  Object.defineProperty(global.navigator, 'geolocation', {
    configurable: true,
    value: {
      getCurrentPosition: (success: PositionCallback, error?: PositionErrorCallback) => {
        if (mode === 'deny') {
          error?.({ code: 1, message: 'denied' } as GeolocationPositionError);
          return;
        }
        success({
          coords: { latitude: GEO.lat, longitude: GEO.lng, accuracy: GEO.accuracy },
        } as GeolocationPosition);
      },
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  tokenParam = 'TOK123';
  window.localStorage.clear();
  mockGeolocation('ok');
  mockApi({});
});

describe('/q/{token} ランディング — 自分の担当', () => {
  it('担当候補は /m/today/{visitId}?qr={token} へ replace する', async () => {
    mockApi({
      resolve: { patient_name: '田中 太郎', candidates: [cand('visit-9', { is_mine: true })] },
    });
    renderPage();
    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-9?qr=TOK123'));
    // API へはパス断片のトークンがそのまま渡る (extractQrToken 結線)。
    expect(asMock(fetcher).mock.calls[0][0]).toBe(RESOLVE_PATH);
  });
});

describe('/q/{token} ランディング — 担当外 (代行 / 予定外の選択)', () => {
  it('担当外は患者氏名と当日予定を出し、自動遷移しない', async () => {
    mockApi({
      resolve: {
        patient_name: '田中 太郎',
        candidates: [cand('visit-1'), cand('visit-2', { planned_staff_name: null })],
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('qr-substitute-choice')).toBeInTheDocument());
    expect(screen.getByText('田中 太郎')).toBeInTheDocument();
    expect(screen.getByText('予定の担当: 山田 花子')).toBeInTheDocument();
    expect(screen.getByText('予定の担当: 未割当')).toBeInTheDocument();
    expect(screen.getAllByTestId('qr-candidate')).toHaveLength(2);
    expect(routerReplace).not.toHaveBeenCalled();
  });

  it('「この予定の代行として記録」は /m/today/{visitId}?qr={token} へ遷移する', async () => {
    mockApi({ resolve: { patient_name: '田中 太郎', candidates: [cand('visit-1')] } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('qr-substitute-choice')).toBeInTheDocument());
    fireEvent.click(screen.getByText('この予定の代行として記録'));
    expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-1?qr=TOK123');
  });

  it('訪問中の予定外 visit には「退出の記録へ」を出す (再スキャン退出の一周)', async () => {
    mockApi({
      resolve: {
        patient_name: '田中 太郎',
        candidates: [
          cand('visit-u', {
            status: 'in_progress',
            is_unplanned: true,
            planned_staff_name: '鈴木 次郎',
          }),
        ],
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('退出の記録へ')).toBeInTheDocument());
    expect(screen.getByText('予定外の訪問（記録者: 鈴木 次郎）')).toBeInTheDocument();
    fireEvent.click(screen.getByText('退出の記録へ'));
    expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-u?qr=TOK123');
  });

  it('当日予定ゼロでも予定外として記録できる案内を出す', async () => {
    mockApi({ resolve: { patient_name: '田中 太郎', candidates: [] } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('qr-substitute-choice')).toBeInTheDocument());
    expect(
      screen.getByText('本日の予定はありません。下のボタンで予定外の訪問として記録できます。'),
    ).toBeInTheDocument();
  });
});

describe('/q/{token} ランディング — 予定外の記録', () => {
  it('GPS プレビューを挟んでから adhoc-checkin し、生成 visit へ遷移する', async () => {
    mockApi({
      resolve: { patient_name: '田中 太郎', candidates: [cand('visit-1')] },
      adhoc: { id: 'visit-adhoc-1' },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('qr-substitute-choice')).toBeInTheDocument());

    // 予定外を選ぶ → GPS 取得 → プレビュー (まだ POST しない)。
    fireEvent.click(screen.getByText('予定外の訪問として記録'));
    await waitFor(() => expect(screen.getByTestId('qr-adhoc-preview')).toBeInTheDocument());
    expect(screen.getByText('位置情報を取得しました')).toBeInTheDocument();
    expect(asMock(fetcher).mock.calls.some((c) => c[0] === ADHOC_PATH)).toBe(false);

    // 「記録する」で単一 POST → 生成された visit へ。
    fireEvent.click(screen.getByText('予定外の訪問として記録する'));
    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-adhoc-1'));
    const call = asMock(fetcher).mock.calls.find((c) => c[0] === ADHOC_PATH);
    expect(call).toBeTruthy();
    const body = JSON.parse((call![1] as { body: string }).body) as Record<string, unknown>;
    expect(body).toMatchObject({ qr_token: 'TOK123', lat: 35.1, lng: 140.1, accuracy: 12 });
  });

  it('測位できなくてもプレビューを経て座標なしで記録できる', async () => {
    mockGeolocation('deny');
    mockApi({ resolve: { patient_name: '田中 太郎', candidates: [] } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('qr-substitute-choice')).toBeInTheDocument());
    fireEvent.click(screen.getByText('予定外の訪問として記録'));
    await waitFor(() => expect(screen.getByText('測位不良')).toBeInTheDocument());
    fireEvent.click(screen.getByText('予定外の訪問として記録する'));
    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-adhoc-1'));
    const call = asMock(fetcher).mock.calls.find((c) => c[0] === ADHOC_PATH);
    const body = JSON.parse((call![1] as { body: string }).body) as Record<string, unknown>;
    expect(body).not.toHaveProperty('lat');
    expect(body).toMatchObject({ qr_token: 'TOK123' });
  });

  it('POST が圏外で失敗したら adhoc_arrival として退避する', async () => {
    mockApi({
      resolve: { patient_name: '田中 太郎', candidates: [] },
      adhocError: new TypeError('Failed to fetch'),
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('qr-substitute-choice')).toBeInTheDocument());
    fireEvent.click(screen.getByText('予定外の訪問として記録'));
    await waitFor(() => expect(screen.getByTestId('qr-adhoc-preview')).toBeInTheDocument());
    fireEvent.click(screen.getByText('予定外の訪問として記録する'));

    await waitFor(() => expect(asMock(toast.warning)).toHaveBeenCalled());
    const raw = window.localStorage.getItem('checkin-pending:staff-1');
    const entries = JSON.parse(raw ?? '[]') as Array<{
      kind: string;
      payload: { qr_token: string };
    }>;
    expect(entries).toHaveLength(1);
    expect(entries[0]?.kind).toBe('adhoc_arrival');
    expect(entries[0]?.payload.qr_token).toBe('TOK123');
    expect(routerReplace).toHaveBeenCalledWith('/m/today');
  });
});

describe('/q/{token} ランディング — 圏外 (resolve 失敗)', () => {
  it('通信断では再試行と「予定外の訪問として記録」の導線を出す', async () => {
    mockApi({
      resolveError: new TypeError('Failed to fetch'),
      adhocError: new TypeError('Failed to fetch'),
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('読み込みに失敗しました')).toBeInTheDocument());

    fireEvent.click(screen.getByText('予定外の訪問として記録'));
    await waitFor(() => expect(screen.getByTestId('qr-adhoc-preview')).toBeInTheDocument());
    fireEvent.click(screen.getByText('予定外の訪問として記録する'));

    await waitFor(() => expect(asMock(toast.warning)).toHaveBeenCalled());
    const entries = JSON.parse(
      window.localStorage.getItem('checkin-pending:staff-1') ?? '[]',
    ) as Array<{ kind: string }>;
    expect(entries).toHaveLength(1);
    expect(entries[0]?.kind).toBe('adhoc_arrival');
  });
});

describe('/q/{token} ランディング — エラー案内', () => {
  it('410 (ローテ済) は「QRが更新されています」を表示する', async () => {
    mockApi({ resolveError: new ApiError('gone', 410, null) });
    renderPage();
    await waitFor(() => expect(screen.getByText('QRが更新されています')).toBeInTheDocument());
    expect(routerReplace).not.toHaveBeenCalled();
  });

  it('404 (未知トークン) は「このQRは無効です」を表示する', async () => {
    mockApi({ resolveError: new ApiError('not found', 404, null) });
    renderPage();
    await waitFor(() => expect(screen.getByText('このQRは無効です')).toBeInTheDocument());
    expect(routerReplace).not.toHaveBeenCalled();
  });

  it('403 (staff 未紐付け) は再ログインを案内する', async () => {
    mockApi({ resolveError: new ApiError('forbidden', 403, null) });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('このアカウントでは打刻できません')).toBeInTheDocument(),
    );
    expect(
      screen.getByText('スタッフに紐付いたアカウントでログインし直してください。'),
    ).toBeInTheDocument();
    expect(routerReplace).not.toHaveBeenCalled();
  });
});

describe('pickQrCandidate', () => {
  it('自分の担当のみを対象に completed 以外の最初 → 全 completed なら先頭 → 空は null', () => {
    expect(pickQrCandidate([])).toBeNull();
    // 担当外は候補にしない (代行/予定外の選択画面へ回す)。
    expect(pickQrCandidate([cand('a'), cand('b')])).toBeNull();
    expect(
      pickQrCandidate([
        cand('a', { is_mine: true, status: 'completed' }),
        cand('b', { is_mine: true }),
        cand('c', { is_mine: true }),
      ])?.visit_id,
    ).toBe('b');
    expect(
      pickQrCandidate([
        cand('a', { is_mine: true, status: 'completed' }),
        cand('b', { is_mine: true, status: 'completed' }),
      ])?.visit_id,
    ).toBe('a');
    // 担当外が先に並んでいても、担当分だけを見る。
    expect(pickQrCandidate([cand('x'), cand('y', { is_mine: true })])?.visit_id).toBe('y');
  });
});
