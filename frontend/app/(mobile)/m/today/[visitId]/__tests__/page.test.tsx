/**
 * QR 訪問チェックイン モバイル (Phase 2) — 画面フローのレンダーテスト.
 *
 * クロスレビュー反映後のフロー (スキャン → GPS取得 → クライアント距離プレビュー
 * → 「記録する」で単一 POST) を検証する。
 *   1. 到着 → 訪問中 → 退出 の一連フロー (プレビュー一致 → 単一 POST)
 *   2. 手入力フォールバック (qr_token 無しで checkin)
 *   3. mismatch (不一致) プレビューで理由必須 + is_override の単一 POST
 *   4. no-show (訪問できなかった) — 理由付きで記録
 *   5. GPS 拒否 → 座標なし POST
 *   6. 404 = 無効QR / 409 = 別患者 のトースト (退避しない)
 *   7. ネットワーク障害 / 5xx → localStorage + pending キューへ退避
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import type * as ReactQueryModule from '@tanstack/react-query';
import type { MyVisit, LatestCheckin } from '@/lib/queries/me';
import { ApiError } from '@/lib/api-client';

// --- module mocks ----------------------------------------------------------
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

// useSearchParams: 既定は ?qr= 無し (通常フロー)。ディープリンク系テストは
// searchParamsGet を差し替える。
const searchParamsGet = vi.fn<(key: string) => string | null>(() => null);
const routerReplace = vi.fn();
const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  useParams: () => ({ visitId: 'visit-1' }),
  useSearchParams: () => ({ get: searchParamsGet }),
  useRouter: () => ({ replace: routerReplace, push: routerPush }),
}));

// Stable QueryClient stub so the page's useQueryClient() works without a provider.
const qcStub = { invalidateQueries: vi.fn() };
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof ReactQueryModule>();
  return { ...actual, useQueryClient: () => qcStub };
});

// fetcher is only used by the pending re-send queue; stub it so no real network.
vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(async () => ({})),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// Scanner stub — exposes buttons to drive onScan / onManual / onCancel.
// onManual は省略可能 (代行モードでは手動フォールバックを出さない) なので、
// 渡されたときだけボタンを描画して本番と同じ見え方にする。
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
      <button onClick={() => onScan('TESTTOKEN')}>__scan__</button>
      {onManual && <button onClick={onManual}>__manual__</button>}
      <button onClick={onCancel}>__cancel__</button>
    </div>
  ),
}));

vi.mock('@/lib/queries/me', () => ({
  useMyVisit: vi.fn(),
  useCheckIn: vi.fn(),
  useCheckOut: vi.fn(),
  useNoShow: vi.fn(),
}));

vi.mock('@/lib/queries/visit-photos', () => ({
  useVisitPhotos: vi.fn(),
  useUploadPhoto: vi.fn(),
}));

// 距離プレビューの public しきい値取得 (Phase 4)。既定では 100/300/50 を返す。
vi.mock('@/lib/queries/checkinSettings', () => ({
  useCheckinSettingsPublic: vi.fn(),
}));

import { useSession } from 'next-auth/react';
import { useMyVisit, useCheckIn, useCheckOut, useNoShow } from '@/lib/queries/me';
import { useVisitPhotos, useUploadPhoto } from '@/lib/queries/visit-photos';
import { useCheckinSettingsPublic } from '@/lib/queries/checkinSettings';
import { toast } from '@/components/ui/sonner';
import MobileVisitDetailPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

// GPS fix used by the geolocation mock.
const GEO = { lat: 35.1, lng: 140.1, accuracy: 20 };

function makeVisit(
  status = 'planned',
  coords: { lat: number | null; lng: number | null } = { lat: 35.1, lng: 140.1 },
): MyVisit {
  return {
    id: 'visit-1',
    patient_id: 'pat-1',
    primary_staff_id: 'staff-1',
    secondary_staff_id: null,
    mentor_staff_id: null,
    visit_date: '2026-06-30',
    start_time: '09:30:00',
    end_time: '10:30:00',
    type: 'normal',
    status,
    source: 'manual',
    note: null,
    patient_name: '山田 花子',
    staff_name: '佐藤 Ns',
    patient_lat: coords.lat,
    patient_lng: coords.lng,
  };
}

function makeCheckin(
  kind: LatestCheckin['kind'],
  match_status: LatestCheckin['match_status'],
  distance_m: number | null,
): LatestCheckin {
  return {
    id: 'ci-1',
    kind,
    match_status,
    distance_m,
    accuracy_m: 20,
    scanned_at: '2026-06-30T00:30:00Z',
    checkin_source: 'qr',
    reason: null,
    is_override: false,
  };
}

let checkInResult: MyVisit & { latest_checkin: LatestCheckin };
let checkOutResult: MyVisit & { latest_checkin: LatestCheckin };
const checkInMutate = vi.fn(async () => checkInResult);
const checkOutMutate = vi.fn(async () => checkOutResult);
const noShowMutate = vi.fn(async () => makeVisit());

function setSession() {
  asMock(useSession).mockReturnValue({
    data: {
      user: { staffId: 'staff-1', role: 'staff' },
      accessToken: 'a',
      refreshToken: 'r',
    },
    status: 'authenticated',
  });
}

/** Install a geolocation mock that succeeds (default) or denies. */
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
  // clearAllMocks は implementation を消さないため、ディープリンク系テストで
  // 差し替えた searchParamsGet を毎回「?qr= 無し」へ戻す (漏れると後続の
  // スキャナ系テストが全部プレビュー直行になる)。
  searchParamsGet.mockImplementation(() => null);
  window.localStorage.clear();
  mockGeolocation('ok');

  setSession();
  checkInResult = {
    ...makeVisit('in_progress'),
    latest_checkin: makeCheckin('arrival', 'match', 5),
  };
  checkOutResult = {
    ...makeVisit('completed'),
    latest_checkin: makeCheckin('departure', 'match', 5),
  };

  asMock(useMyVisit).mockReturnValue({
    data: makeVisit('planned'),
    isLoading: false,
    isError: false,
    error: null,
  });
  asMock(useCheckIn).mockReturnValue({ mutateAsync: checkInMutate, isPending: false });
  asMock(useCheckOut).mockReturnValue({ mutateAsync: checkOutMutate, isPending: false });
  asMock(useNoShow).mockReturnValue({ mutateAsync: noShowMutate, isPending: false });
  asMock(useVisitPhotos).mockReturnValue({ data: [] });
  asMock(useUploadPhoto).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  asMock(useCheckinSettingsPublic).mockReturnValue({
    data: { match_m: 100, review_m: 300, accuracy_m: 50 },
  });
});

describe('QR チェックイン モバイル — 基本表示', () => {
  it('未訪問では「QRで到着を記録」と「訪問できなかった」を表示する', () => {
    render(<MobileVisitDetailPage />);
    expect(screen.getByText('QRで到着を記録')).toBeInTheDocument();
    expect(screen.getByText('訪問できなかった（理由を記録）')).toBeInTheDocument();
  });
});

describe('QR チェックイン モバイル — 到着→訪問中→退出フロー (確認→記録の単一POST)', () => {
  it('スキャン→プレビュー(一致)→記録→退出まで通す', async () => {
    render(<MobileVisitDetailPage />);

    // 到着スキャン開始 → スキャナ表示。
    fireEvent.click(screen.getByText('QRで到着を記録'));
    expect(screen.getByTestId('qr-scanner')).toBeInTheDocument();

    // QR 読取 → GPS取得 → プレビュー (まだ POST しない)。
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    expect(checkInMutate).not.toHaveBeenCalled();
    expect(screen.getByText('登録住所と一致')).toBeInTheDocument();

    // 「到着を記録する」で単一 POST。
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({ qr_token: 'TESTTOKEN', lat: 35.1 });
    expect(screen.getByText('QRで退出を記録')).toBeInTheDocument();

    // 退出スキャン → プレビュー → 記録。
    fireEvent.click(screen.getByText('QRで退出を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('退出の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('退出を記録する'));
    await waitFor(() => expect(checkOutMutate).toHaveBeenCalledTimes(1));
    // ④ 退出 POST の payload (qr_token / 座標) を assert。
    expect(checkOutMutate.mock.calls[0][0]).toMatchObject({
      qr_token: 'TESTTOKEN',
      lat: 35.1,
      lng: 140.1,
    });

    // 完了。
    expect(screen.getByText('訪問完了')).toBeInTheDocument();
  });
});

describe('QR チェックイン モバイル — ディープリンク (?qr=) のスキャン省略', () => {
  it('?qr= があれば到着はスキャナを出さずプレビューへ直行し、記録成功で消費する', async () => {
    searchParamsGet.mockImplementation((key: string) => (key === 'qr' ? 'DEEPTOKEN1' : null));
    render(<MobileVisitDetailPage />);

    // 到着: スキャナは出ず、GPS 取得 → プレビューへ直行 (自動送信はしない)。
    fireEvent.click(screen.getByText('QRで到着を記録'));
    expect(screen.queryByTestId('qr-scanner')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    expect(checkInMutate).not.toHaveBeenCalled();

    // 「到着を記録する」でディープリンクの token 付き単一 POST。
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({ qr_token: 'DEEPTOKEN1' });

    // token は 1 記録で消費される — 退出は通常どおりスキャナが出る。
    fireEvent.click(screen.getByText('QRで退出を記録'));
    expect(screen.getByTestId('qr-scanner')).toBeInTheDocument();
  });
});

describe('QR チェックイン モバイル — 代行 (担当外) モード', () => {
  /** 担当が別スタッフの visit (= 代行) を ?qr= 付きで開いた状態。 */
  function setSubstituteVisit(status = 'planned') {
    searchParamsGet.mockImplementation((key: string) => (key === 'qr' ? 'DEEPTOKEN1' : null));
    asMock(useMyVisit).mockReturnValue({
      data: { ...makeVisit(status), primary_staff_id: 'staff-9', staff_name: '田中 先輩' },
      isLoading: false,
      isError: false,
      error: null,
    });
  }

  it('詳細 GET には qr_token を渡し、代行バッジと予定担当を表示する', () => {
    setSubstituteVisit();
    render(<MobileVisitDetailPage />);
    // 担当外は通常 GET が 404 になるため、フックへトークンを渡してフォールバックさせる。
    expect(asMock(useMyVisit).mock.calls[0]).toEqual(['visit-1', 'DEEPTOKEN1']);
    expect(screen.getByTestId('mobile-detail-substitute')).toBeInTheDocument();
    expect(screen.getByText(/予定の担当: 田中 先輩/)).toBeInTheDocument();
  });

  it('未訪問 (no-show) は出さない (担当スタッフ専用)', () => {
    setSubstituteVisit();
    render(<MobileVisitDetailPage />);
    expect(screen.getByText('QRで到着を記録')).toBeInTheDocument();
    expect(screen.queryByText('訪問できなかった（理由を記録）')).not.toBeInTheDocument();
  });

  it('?qr= 無しなら担当欄に自分が居なくても代行モードにしない (assignments 担当の回帰防止)', () => {
    // searchParamsGet は beforeEach で「?qr= 無し」に戻る。visit_staff_assignments
    // だけで担当しているスタッフは VisitRead の担当欄に出ないため、QR 経由でない
    // 通常導線でも代行扱いすると no-show / 手動フォールバックが消える回帰になる。
    asMock(useMyVisit).mockReturnValue({
      data: { ...makeVisit(), primary_staff_id: 'staff-9', staff_name: '田中 先輩' },
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<MobileVisitDetailPage />);
    expect(screen.queryByTestId('mobile-detail-substitute')).not.toBeInTheDocument();
    expect(screen.getByText('訪問できなかった（理由を記録）')).toBeInTheDocument();
    // 手動フォールバックも従来どおり出る。
    fireEvent.click(screen.getByText('QRで到着を記録'));
    expect(screen.getByText('__manual__')).toBeInTheDocument();
  });

  it('assignments だけで担当していれば ?qr= 付きでも代行モードにしない (M-4)', () => {
    // primary/secondary/mentor/同行 には出ないが visit_staff_assignments で担当して
    // いるスタッフ。/q 経由で開いても本人なので no-show / 手動フォールバックは残す。
    searchParamsGet.mockImplementation((key: string) => (key === 'qr' ? 'DEEPTOKEN1' : null));
    asMock(useMyVisit).mockReturnValue({
      data: {
        ...makeVisit(),
        primary_staff_id: 'staff-9',
        staff_name: '田中 先輩',
        staff_assignments: [
          { visit_id: 'visit-1', staff_id: 'staff-1', assigned_at: '2026-06-30T00:00:00Z' },
        ],
      },
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<MobileVisitDetailPage />);
    expect(screen.queryByTestId('mobile-detail-substitute')).not.toBeInTheDocument();
    expect(screen.getByText('訪問できなかった（理由を記録）')).toBeInTheDocument();
  });

  it('真の担当外 (assignments 空配列) は ?qr= 付きで代行モードのまま (M-4)', () => {
    // 担当外の QR capability GET では BE が staff_assignments を空配列に落とす。
    searchParamsGet.mockImplementation((key: string) => (key === 'qr' ? 'DEEPTOKEN1' : null));
    asMock(useMyVisit).mockReturnValue({
      data: {
        ...makeVisit(),
        primary_staff_id: 'staff-9',
        staff_name: '田中 先輩',
        staff_assignments: [],
      },
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('mobile-detail-substitute')).toBeInTheDocument();
    expect(screen.queryByText('訪問できなかった（理由を記録）')).not.toBeInTheDocument();
  });

  it('打刻 POST に qr_token を必ず載せ、退出は手動フォールバック無しの再スキャン', async () => {
    setSubstituteVisit();
    render(<MobileVisitDetailPage />);

    // 到着: ディープリンクの token でプレビュー直行 → 記録 (token 同梱)。
    fireEvent.click(screen.getByText('QRで到着を記録'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({ qr_token: 'DEEPTOKEN1' });

    // 退出: token は消費済み → 再スキャン。手動フォールバックは出さない (決定#6)。
    fireEvent.click(screen.getByText('QRで退出を記録'));
    expect(screen.getByTestId('qr-scanner')).toBeInTheDocument();
    expect(screen.queryByText('__manual__')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('退出の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('退出を記録する'));
    await waitFor(() => expect(checkOutMutate).toHaveBeenCalledTimes(1));
    expect(checkOutMutate.mock.calls[0][0]).toMatchObject({ qr_token: 'TESTTOKEN' });
  });
});

describe('QR チェックイン モバイル — 手入力フォールバック', () => {
  it('「QRなしで記録」は qr_token 無しで checkin する', async () => {
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__manual__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    const payload = checkInMutate.mock.calls[0][0] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('qr_token');
    expect(payload).toMatchObject({ lat: 35.1, lng: 140.1 });
  });
});

describe('QR チェックイン モバイル — mismatch 理由必須 + 単一POST', () => {
  it('プレビュー不一致では理由未入力で記録できず、理由入力で is_override の単一 POST', async () => {
    // 患者座標を遠くに置き、クライアントプレビューで mismatch にする。
    asMock(useMyVisit).mockReturnValue({
      data: makeVisit('planned', { lat: 35.5, lng: 140.5 }),
      isLoading: false,
      isError: false,
      error: null,
    });
    checkInResult = {
      ...makeVisit('in_progress'),
      latest_checkin: makeCheckin('arrival', 'mismatch', 40000),
    };
    render(<MobileVisitDetailPage />);

    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    expect(screen.getByText('登録住所と不一致（300m超）')).toBeInTheDocument();
    expect(checkInMutate).not.toHaveBeenCalled();

    // 理由未入力で記録 → POST されずエラートースト。
    fireEvent.click(screen.getByText('理由を付けて到着を記録'));
    expect(checkInMutate).not.toHaveBeenCalled();
    expect(asMock(toast.error)).toHaveBeenCalled();

    // 理由入力 → is_override で単一 POST。
    fireEvent.change(screen.getByLabelText('理由（不一致のため必須・管理者に共有）'), {
      target: { value: '裏口で測位' },
    });
    fireEvent.click(screen.getByText('理由を付けて到着を記録'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({
      is_override: true,
      reason: '裏口で測位',
      qr_token: 'TESTTOKEN',
    });
  });
});

describe('QR チェックイン モバイル — プレビューしきい値の動的同期 (Phase 4)', () => {
  it('public しきい値を反映したラベルを表示する (取得値=150/500)', async () => {
    asMock(useCheckinSettingsPublic).mockReturnValue({
      data: { match_m: 150, review_m: 500, accuracy_m: 50 },
    });
    // 遠い患者座標 → クライアントプレビューで mismatch。ラベルは取得した reviewM(500) 由来。
    asMock(useMyVisit).mockReturnValue({
      data: makeVisit('planned', { lat: 35.5, lng: 140.5 }),
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    expect(screen.getByText('登録住所と不一致（500m超）')).toBeInTheDocument();
  });
});

describe('QR チェックイン モバイル — 未訪問 (no-show)', () => {
  it('理由を入力して未訪問記録を送る', async () => {
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('訪問できなかった（理由を記録）'));

    // 理由未入力では送らない。
    fireEvent.click(screen.getByText('未訪問として記録する'));
    expect(noShowMutate).not.toHaveBeenCalled();

    // チップで理由を入れて記録。
    fireEvent.click(screen.getByText('不在（応答なし）'));
    fireEvent.click(screen.getByText('未訪問として記録する'));
    await waitFor(() => expect(noShowMutate).toHaveBeenCalledTimes(1));
    expect(noShowMutate.mock.calls[0][0]).toMatchObject({ reason: '不在（応答なし）' });
  });
});

describe('QR チェックイン モバイル — GPS 拒否で座標なし POST', () => {
  it('位置情報を拒否してもプレビューを経て座標なしで記録できる', async () => {
    mockGeolocation('deny');
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('測位不良')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    const payload = checkInMutate.mock.calls[0][0] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('lat');
    expect(payload).not.toHaveProperty('lng');
    expect(payload).toMatchObject({ qr_token: 'TESTTOKEN' });
  });
});

describe('QR チェックイン モバイル — 404/409 はユーザー向けエラー (退避しない)', () => {
  it('404 は「このQRは無効です」を表示し退避しない', async () => {
    checkInMutate.mockRejectedValueOnce(new ApiError('not found', 404, { detail: 'invalid' }));
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() =>
      expect(asMock(toast.error)).toHaveBeenCalledWith('このQRは無効です', expect.anything()),
    );
    // 退避していない (pending キューが空)。
    expect(window.localStorage.getItem('checkin-pending:staff-1')).toBeNull();
  });

  it('409 は代行/予定外への導線を出し、/q/{token} へ渡す (設計 §5)', async () => {
    checkInMutate.mockRejectedValueOnce(new ApiError('conflict', 409, { detail: 'other' }));
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));

    // 行き止まりのトーストではなく、読んだ QR の患者を記録する導線を出す。
    await waitFor(() => expect(screen.getByTestId('wrong-patient-panel')).toBeInTheDocument());
    fireEvent.click(screen.getByText('代行／予定外として記録する'));
    expect(routerPush).toHaveBeenCalledWith('/q/TESTTOKEN');
    // 記録は退避しない (サーバの確定回答)。
    expect(window.localStorage.getItem('checkin-pending:staff-1')).toBeNull();
  });
});

describe('QR チェックイン モバイル — ネットワーク障害 / 5xx で退避', () => {
  it('ネットワーク障害で localStorage + pending キューへ退避する', async () => {
    checkInMutate.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));

    await waitFor(() => expect(asMock(toast.warning)).toHaveBeenCalled());
    // pending キューに 1 件積まれている。
    const raw = window.localStorage.getItem('checkin-pending:staff-1');
    expect(raw).toBeTruthy();
    const entries = JSON.parse(raw ?? '[]') as Array<{ kind: string }>;
    expect(entries).toHaveLength(1);
    expect(entries[0]?.kind).toBe('arrival');
    // 楽観的に checked_in 表示 (退出ボタンが出る)。
    expect(screen.getByText('QRで退出を記録')).toBeInTheDocument();
  });

  it('5xx でも退避する', async () => {
    checkInMutate.mockRejectedValueOnce(new ApiError('boom', 503, { detail: 'unavailable' }));
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(asMock(toast.warning)).toHaveBeenCalled());
    expect(window.localStorage.getItem('checkin-pending:staff-1')).toBeTruthy();
  });
});
