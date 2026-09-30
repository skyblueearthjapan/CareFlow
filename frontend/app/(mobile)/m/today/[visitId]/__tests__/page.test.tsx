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
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
  // 録音セッションの救出はパス変更でも走る (レビュー H-C)。
  usePathname: () => '/m/today/visit-1',
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
  // 実績の時刻を合わせる (設計 2026-09-30)。
  useAdjustActualTime: vi.fn(),
  useResetActualTime: vi.fn(),
  // 打刻ボタンは当日の訪問にだけ出す。fixture の visit_date と同じ日を「今日」にする。
  todayIso: () => '2026-06-30',
}));

vi.mock('@/lib/queries/visit-photos', () => ({
  useVisitPhotos: vi.fn(),
  useUploadPhoto: vi.fn(),
}));

// 音声記録 (設計 2026-09-17)。ここでは打刻フローの回帰だけを見るので、
// 一覧は 0 件・録音パネル / 記録カードは差し替える (MediaRecorder は jsdom に無い)。
vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: vi.fn(() => ({ data: { items: [], total: 0 } })),
}));
vi.mock('@/lib/voice/queue', () => ({
  useVoiceFlush: () => ({ pendingCount: 0, flushNow: vi.fn(), refreshPending: vi.fn() }),
}));
// 録音はセッションが持つ (レビュー H-C)。ここでは「ページ離脱で救出が走るか」だけ見る。
const rescueVoiceSessions = vi.hoisted(() => vi.fn(async () => 0));
vi.mock('@/lib/voice/session', () => ({ rescueVoiceSessions }));
// 録音パネルは到着打刻を跨いで**同じインスタンスのまま**でなければならない
// (レビュー H-C)。mount / unmount を数えて、打刻で作り直されないことを縛る。
const voicePanel = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }));
vi.mock('@/components/mobile/VoiceRecorderPanel', () => ({
  VoiceRecorderPanel: ({ heading }: { heading?: string }) => {
    React.useEffect(() => {
      voicePanel.mounts += 1;
      return () => {
        voicePanel.unmounts += 1;
      };
    }, []);
    return <div data-testid="voice-recorder-panel">{heading}</div>;
  },
}));
vi.mock('@/components/mobile/VisitRecordCard', () => ({
  VisitRecordCard: () => <div data-testid="visit-record-card" />,
}));

// 距離プレビューの public しきい値取得 (Phase 4)。既定では 100/300/50 を返す。
vi.mock('@/lib/queries/checkinSettings', () => ({
  useCheckinSettingsPublic: vi.fn(),
}));

import { useSession } from 'next-auth/react';
import {
  useMyVisit,
  useCheckIn,
  useCheckOut,
  useNoShow,
  useAdjustActualTime,
  useResetActualTime,
} from '@/lib/queries/me';
import { listPending } from '@/lib/checkin-queue';
import { fetcher } from '@/lib/api/fetcher';
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
const adjustMutate = vi.fn(async (_payload: unknown): Promise<MyVisit> => makeVisit());
const resetMutate = vi.fn(async (_kind: unknown): Promise<MyVisit> => makeVisit());

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
  voicePanel.mounts = 0;
  voicePanel.unmounts = 0;
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
  asMock(useAdjustActualTime).mockReturnValue({ mutateAsync: adjustMutate, isPending: false });
  asMock(useResetActualTime).mockReturnValue({ mutateAsync: resetMutate, isPending: false });
  asMock(useVisitPhotos).mockReturnValue({ data: [] });
  asMock(useUploadPhoto).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  asMock(useCheckinSettingsPublic).mockReturnValue({
    data: { match_m: 100, review_m: 300, accuracy_m: 50 },
  });
});

afterEach(() => {
  // 読取の瞬間を固定するテストが Date を差し替える。次のテストへ漏らさない。
  vi.useRealTimers();
  // URL のクエリを置くテストがある (1 回きりのクエリを外す挙動の確認)。
  window.history.replaceState({}, '', '/');
});

describe('QR チェックイン モバイル — 基本表示', () => {
  it('未訪問では「QRで到着を記録」と「訪問できなかった」を表示する', () => {
    render(<MobileVisitDetailPage />);
    expect(screen.getByText('QRで到着を記録')).toBeInTheDocument();
    expect(screen.getByText('訪問できなかった（理由を記録）')).toBeInTheDocument();
  });
});

/**
 * 到着前と訪問中で録音パネルを別々に置くと、到着打刻の瞬間に片方が unmount され、
 * 「到着前に録音 → 停止 → review（保存前）」の音声が巻き添えで消える。1 箇所に
 * 置いて見出しだけ変える（レビュー H-C）。
 */
describe('音声記録パネルは到着打刻を跨いで生き残る (H-C)', () => {
  it('到着前 / 訪問中で 1 つだけ・打刻で作り直さない', () => {
    const { rerender } = render(<MobileVisitDetailPage />);

    // 到着前 — 1 つだけ出る（見出しで「到着前でも録音できる」と伝える）。
    expect(screen.getAllByTestId('voice-recorder-panel')).toHaveLength(1);
    expect(screen.getByTestId('voice-recorder-panel')).toHaveTextContent(
      '音声記録（到着前でも録音できます）',
    );
    expect(voicePanel.mounts).toBe(1);

    // 到着を記録 → 訪問中へ（同じ位置・同じインスタンスのまま）。
    asMock(useMyVisit).mockReturnValue({
      data: makeVisit('in_progress'),
      isLoading: false,
      isError: false,
      error: null,
    });
    rerender(<MobileVisitDetailPage />);

    expect(screen.getAllByTestId('voice-recorder-panel')).toHaveLength(1);
    expect(screen.getByTestId('voice-recorder-panel')).toHaveTextContent('音声記録');
    expect(screen.getByText('QRで退出を記録')).toBeInTheDocument();
    // ここが要点 — unmount されていない = 保存前の録音が消えない。
    expect(voicePanel.unmounts).toBe(0);
    expect(voicePanel.mounts).toBe(1);
  });

  it('ページを離れるとき（unmount）に録音セッションを救出する', () => {
    const { unmount } = render(<MobileVisitDetailPage />);
    // 画面にいる間は止めない（スキャナ往復で録音が切れないのはこのため）。
    expect(rescueVoiceSessions).not.toHaveBeenCalled();

    unmount();

    expect(rescueVoiceSessions).toHaveBeenCalledWith('staff-1');
  });

  it('タブを閉じる / 背面に回る (pagehide) でも救出する', () => {
    render(<MobileVisitDetailPage />);

    window.dispatchEvent(new Event('pagehide'));

    expect(rescueVoiceSessions).toHaveBeenCalledWith('staff-1');
  });

  it('画面が隠れた (visibilitychange → hidden) ときも救出する (N-1)', () => {
    render(<MobileVisitDetailPage />);

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
    // まだ見えている = 救出しない (タブを切り替えて戻っただけで畳まない)。
    expect(rescueVoiceSessions).not.toHaveBeenCalled();

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));

    expect(rescueVoiceSessions).toHaveBeenCalledWith('staff-1');
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  });

  /**
   * `pagehide` と `beforeunload` は実機でほぼ同時に飛ぶ。`beforeunload` で保存すると
   * 同じ録音に対して停止と投入が 2 本走るので、**保存は `pagehide` 側だけ**にする
   * (`beforeunload` は録音中の離脱確認プロンプト専用・レビュー N-1)。
   */
  it('pagehide と beforeunload が同時に飛んでも救出は 1 回 (N-1)', () => {
    render(<MobileVisitDetailPage />);

    window.dispatchEvent(new Event('pagehide'));
    window.dispatchEvent(new Event('beforeunload'));

    expect(rescueVoiceSessions).toHaveBeenCalledTimes(1);
    expect(rescueVoiceSessions).toHaveBeenCalledWith('staff-1');
  });

  it('訪問完了では出さない', () => {
    asMock(useMyVisit).mockReturnValue({
      data: makeVisit('completed'),
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<MobileVisitDetailPage />);

    expect(screen.queryByTestId('voice-recorder-panel')).toBeNull();
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

/**
 * 打刻の実時刻 (お客様要望 2026-09-18)。予定はそのまま残し、打刻があれば
 * 直下に実績を並べる。完了後 (checked_out) でも実時間が読めることが要点。
 */
describe('訪問詳細 — 予定 + 実績の 2 行', () => {
  it('打刻なし: 「予定 09:30 - 10:30」だけで実績行は出さない', () => {
    render(<MobileVisitDetailPage />);
    expect(screen.getByText('予定 09:30 - 10:30')).toBeInTheDocument();
    expect(screen.queryByTestId('mobile-detail-actual')).toBeNull();
  });

  it('完了後: 予定の直下に「実績 12:56 – 13:40」', () => {
    asMock(useMyVisit).mockReturnValue({
      data: {
        ...makeVisit('completed'),
        // JST 12:56 到着 / 13:40 退出。
        actual_arrival_at: '2026-06-30T03:56:00Z',
        actual_departure_at: '2026-06-30T04:40:00Z',
      },
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<MobileVisitDetailPage />);
    expect(screen.getByText('予定 09:30 - 10:30')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-detail-actual').textContent).toContain('実績 12:56 – 13:40');
  });

  it('訪問中 (到着のみ): 「到着 12:56 〜」', () => {
    asMock(useMyVisit).mockReturnValue({
      data: {
        ...makeVisit('in_progress'),
        actual_arrival_at: '2026-06-30T03:56:00Z',
        actual_departure_at: null,
      },
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('mobile-detail-actual').textContent).toContain('到着 12:56 〜');
  });

  it('未訪問 (no_show): 到着打刻があっても実績行は出さない', () => {
    asMock(useMyVisit).mockReturnValue({
      data: {
        ...makeVisit('no_show'),
        actual_arrival_at: '2026-06-30T03:56:00Z',
        actual_departure_at: null,
      },
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<MobileVisitDetailPage />);
    expect(screen.getByText('予定 09:30 - 10:30')).toBeInTheDocument();
    expect(screen.queryByTestId('mobile-detail-actual')).toBeNull();
  });
});

// ===========================================================================
// 実績の時刻を合わせる (設計 2026-09-30 §7)
// ===========================================================================

/** サーバが「合わせられる」と返した訪問 (JST 13:06 に読み取り)。 */
function adjustableVisit(over: Partial<MyVisit> = {}): MyVisit {
  return {
    ...makeVisit('in_progress'),
    start_time: '13:00:00',
    end_time: '13:35:00',
    actual_arrival_at: '2026-06-30T04:06:20Z',
    actual_arrival_read_at: '2026-06-30T04:06:20Z',
    actual_arrival_adjusted: false,
    actual_departure_at: null,
    actual_departure_read_at: null,
    actual_departure_adjusted: false,
    actual_departure_manual: false,
    actual_adjust_allowed: true,
    ...over,
  };
}

function setVisit(visit: MyVisit) {
  asMock(useMyVisit).mockReturnValue({
    data: visit,
    isLoading: false,
    isError: false,
    error: null,
  });
}

/** 到着を 12:56 に合わせた後の応答。 */
const ADJUSTED = adjustableVisit({
  actual_arrival_at: '2026-06-30T03:56:00Z',
  actual_arrival_adjusted: true,
});

describe('読取の瞬間を at に載せる (設計 §3)', () => {
  it('カメラが読んだ時点の時刻を送り、確認画面にその時刻を出す (記録を押した時刻ではない)', async () => {
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z')); // JST 13:06:20 に読み取り
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    expect(screen.getByTestId('preview-read-time')).toHaveTextContent(
      '読み取った時刻 13:06 で記録します',
    );

    // 位置の取得と確認で 2 分かかった想定。
    vi.setSystemTime(new Date('2026-06-30T04:08:40Z'));
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({ at: '2026-06-30T04:06:20.000Z' });
  });

  it('「位置を再取得」しても読み取った瞬間は変えない', async () => {
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());

    vi.setSystemTime(new Date('2026-06-30T04:09:00Z'));
    fireEvent.click(screen.getByText('位置を再取得'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    expect(screen.getByTestId('preview-read-time')).toHaveTextContent('13:06');
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({ at: '2026-06-30T04:06:20.000Z' });
  });

  it('「QRなしで記録」は押した時点の時刻', async () => {
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    fireEvent.click(screen.getByText('__manual__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    vi.setSystemTime(new Date('2026-06-30T04:08:00Z'));
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({ at: '2026-06-30T04:06:20.000Z' });
  });

  it('ディープリンクは、ページを開いた時点の時刻', async () => {
    searchParamsGet.mockImplementation((key: string) => (key === 'qr' ? 'DEEPTOKEN1' : null));
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    render(<MobileVisitDetailPage />);
    vi.setSystemTime(new Date('2026-06-30T04:07:30Z'));
    fireEvent.click(screen.getByText('QRで到着を記録'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({ at: '2026-06-30T04:06:20.000Z' });
  });

  it('圏外で退避した打刻も、読み取った瞬間の at のまま控える', async () => {
    checkInMutate.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    vi.setSystemTime(new Date('2026-06-30T04:08:40Z'));
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(asMock(toast.warning)).toHaveBeenCalled());
    expect(listPending('staff-1')[0]?.payload.at).toBe('2026-06-30T04:06:20.000Z');
  });
});

describe('到着した直後のカード (設計 §7-2)', () => {
  /** 到着を記録して、カードが出た状態にする。 */
  async function arrive() {
    checkInResult = { ...adjustableVisit(), latest_checkin: makeCheckin('arrival', 'match', 5) };
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(screen.getByTestId('arrived-adjust-card')).toBeInTheDocument());
  }

  it('到着を記録するとカードが出る (5・10・15 分前に、合わせた後の時刻を併記)', async () => {
    await arrive();
    const card = screen.getByTestId('arrived-adjust-card');
    expect(card).toHaveTextContent('到着 13:06 を記録しました');
    expect(card).toHaveTextContent(
      'お宅に着いてから読み取るまでに時間があったら、着いた時刻に合わせられます。',
    );
    expect(screen.getByRole('button', { name: /^5分前/ })).toHaveTextContent('13:01');
    expect(screen.getByRole('button', { name: /^10分前/ })).toHaveTextContent('12:56');
    expect(screen.getByRole('button', { name: /^15分前/ })).toHaveTextContent('12:51');
    expect(screen.getByText('細かく合わせる')).toBeInTheDocument();
    expect(screen.getByText('このままでOK')).toBeInTheDocument();
  });

  it('「10分前」は 1 回押すだけで保存し、「元に戻す」で読取時刻に戻す', async () => {
    adjustMutate.mockResolvedValueOnce(ADJUSTED);
    resetMutate.mockResolvedValueOnce(adjustableVisit());
    await arrive();

    fireEvent.click(screen.getByRole('button', { name: /^10分前/ }));
    await waitFor(() => expect(adjustMutate).toHaveBeenCalledTimes(1));
    // 理由は付けない (PO 決定 2026-10-01)。
    expect(adjustMutate).toHaveBeenCalledWith({ kind: 'arrival', time: '12:56' });
    const card = await screen.findByText('到着を 12:56 に合わせました');
    expect(card).toBeInTheDocument();
    expect(screen.getByTestId('arrived-adjust-card')).toHaveTextContent(
      '読み取った時刻は 13:06（10 分前に到着）',
    );
    // 実績の行と経過時間の起点も、合わせた後の時刻になる。
    expect(screen.getByTestId('mobile-detail-actual')).toHaveTextContent('到着 12:56 〜');
    expect(screen.getByTestId('mobile-detail-elapsed')).toHaveTextContent('到着 12:56〜');
    expect(screen.getByTestId('mobile-detail-actual-note')).toHaveTextContent(
      '到着を 10 分 調整（読取 13:06）',
    );

    fireEvent.click(screen.getByText('元に戻す'));
    await waitFor(() => expect(resetMutate).toHaveBeenCalledWith('arrival'));
    expect(await screen.findByText('到着 13:06 を記録しました')).toBeInTheDocument();
  });

  it('「このままでOK」で閉じる (何も保存しない)', async () => {
    await arrive();
    fireEvent.click(screen.getByText('このままでOK'));
    expect(screen.queryByTestId('arrived-adjust-card')).toBeNull();
    expect(adjustMutate).not.toHaveBeenCalled();
    expect(resetMutate).not.toHaveBeenCalled();
  });

  it('保存できなかったら、サーバの detail をそのまま出す', async () => {
    adjustMutate.mockRejectedValueOnce(
      new ApiError('unprocessable', 422, { detail: '到着は読み取りの 90 分前までです' }),
    );
    await arrive();
    fireEvent.click(screen.getByRole('button', { name: /^5分前/ }));
    await waitFor(() =>
      expect(asMock(toast.error)).toHaveBeenCalledWith('時刻を合わせられませんでした', {
        description: '到着は読み取りの 90 分前までです',
      }),
    );
    expect(screen.getByTestId('arrived-adjust-card')).toHaveTextContent(
      '到着 13:06 を記録しました',
    );
  });

  it('圏外で退避した到着は API を呼ばず、退避キューの payload に書き込む', async () => {
    checkInMutate.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(screen.getByTestId('arrived-adjust-card')).toBeInTheDocument());
    expect(screen.getByTestId('arrived-adjust-card')).toHaveTextContent(
      '到着 13:06 を記録しました',
    );

    fireEvent.click(screen.getByRole('button', { name: /^10分前/ }));
    expect(await screen.findByText('到着を 12:56 に合わせました')).toBeInTheDocument();
    expect(adjustMutate).not.toHaveBeenCalled();
    expect(listPending('staff-1')[0]?.payload).toMatchObject({
      at: '2026-06-30T04:06:20.000Z',
      adjusted_time: '12:56',
    });
    expect(listPending('staff-1')[0]?.payload).not.toHaveProperty('adjust_reason_code');
    // 経過時間の起点も合わせた時刻。
    expect(screen.getByTestId('mobile-detail-elapsed')).toHaveTextContent('到着 12:56〜');

    // 元に戻す → 同梱をやめる (打刻の控えそのものは残る)。
    fireEvent.click(screen.getByText('元に戻す'));
    expect(await screen.findByText('到着 13:06 を記録しました')).toBeInTheDocument();
    const payload = listPending('staff-1')[0]?.payload;
    expect(payload).not.toHaveProperty('adjusted_time');
    expect(payload).not.toHaveProperty('adjust_reason_code');
    expect(payload?.at).toBe('2026-06-30T04:06:20.000Z');
    expect(resetMutate).not.toHaveBeenCalled();
  });

  it('合わせられない訪問 (actual_adjust_allowed なし) ではカードを出さない', async () => {
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(screen.getByText('QRで退出を記録')).toBeInTheDocument());
    expect(screen.queryByTestId('arrived-adjust-card')).toBeNull();
  });

  it('予定外訪問 (/q から ?arrived=1 で来る) でもカードを出す', () => {
    searchParamsGet.mockImplementation((key: string) => (key === 'arrived' ? '1' : null));
    setVisit(adjustableVisit());
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('arrived-adjust-card')).toHaveTextContent(
      '到着 13:06 を記録しました',
    );
  });
});

describe('実績の行の「時刻を合わせる」(設計 §7-3)', () => {
  it('actual_adjust_allowed=true のときだけボタンを出す', () => {
    setVisit(adjustableVisit());
    const { unmount } = render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('mobile-detail-adjust')).toHaveTextContent('時刻を合わせる');
    unmount();

    setVisit(adjustableVisit({ actual_adjust_allowed: false }));
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('mobile-detail-actual')).toBeInTheDocument();
    expect(screen.queryByTestId('mobile-detail-adjust')).toBeNull();
    expect(screen.queryByTestId('mobile-detail-manual-departure')).toBeNull();
  });

  it('完了後: 「滞在 35 分 ・ 到着を 10 分 調整（読取 13:06）」', () => {
    setVisit({
      ...ADJUSTED,
      status: 'completed',
      actual_departure_at: '2026-06-30T04:31:00Z',
      actual_departure_read_at: '2026-06-30T04:31:00Z',
    });
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('mobile-detail-actual')).toHaveTextContent('実績 12:56 – 13:31');
    expect(screen.getByTestId('mobile-detail-actual-note')).toHaveTextContent(
      '滞在 35 分 ・ 到着を 10 分 調整（読取 13:06）',
    );
    expect(screen.getByTestId('mobile-detail-adjust')).toBeInTheDocument();
    expect(
      screen.getByText('おつかれさまでした！時刻は上の「時刻を合わせる」から調整できます。'),
    ).toBeInTheDocument();
  });

  it('読み取りの無い退出は「退出は手入力」', () => {
    setVisit(
      adjustableVisit({
        status: 'completed',
        actual_departure_at: '2026-06-30T04:41:00Z',
        actual_departure_read_at: null,
        actual_departure_manual: true,
      }),
    );
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('mobile-detail-actual-note')).toHaveTextContent(
      '滞在 35 分 ・ 退出は手入力',
    );
  });

  it('ボタンからシートを開き、「10分前」→「12:56 に合わせる」で保存する', async () => {
    adjustMutate.mockResolvedValueOnce(ADJUSTED);
    setVisit(adjustableVisit());
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByTestId('mobile-detail-adjust'));
    expect(await screen.findByText('実績の時刻を合わせる')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '10分前' }));
    // 理由の選択は無い (PO 決定 2026-10-01)。
    expect(screen.queryByRole('button', { name: '読み取りが後になった' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '12:56 に合わせる' }));
    await waitFor(() =>
      expect(adjustMutate).toHaveBeenCalledWith({ kind: 'arrival', time: '12:56' }),
    );
    await waitFor(() =>
      expect(asMock(toast.success)).toHaveBeenCalledWith('到着を 12:56 に合わせました'),
    );
    // 保存できたらシートを閉じる。
    await waitFor(() => expect(screen.queryByTestId('actual-time-sheet')).toBeNull());
  });
});

describe('退出の読み取りが無いとき (設計 §7-5)', () => {
  it('訪問中の表示から退出時刻を入れる (時刻だけで記録する)', async () => {
    // いま JST 14:00。到着 13:06・予定 35 分 → 最初の候補は 13:41。
    vi.setSystemTime(new Date('2026-06-30T05:00:00Z'));
    adjustMutate.mockResolvedValueOnce(
      adjustableVisit({
        status: 'completed',
        actual_departure_at: '2026-06-30T04:41:00Z',
        actual_departure_manual: true,
      }),
    );
    setVisit(adjustableVisit());
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('退出の QR を読んでいないときは、退出時刻を入れる'));

    expect(await screen.findByTestId('actual-time-draft')).toHaveTextContent('13:41');
    expect(screen.getByTestId('actual-time-sub')).toHaveTextContent(
      '退出の読み取りがありません（手入力）',
    );
    expect(screen.queryByRole('button', { name: '読み取りなし' })).toBeNull();
    // 退出を新しく記録する操作なので、「合わせる」とは書かない (レビュー M-2)。
    expect(screen.queryByRole('button', { name: '13:41 に合わせる' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '退出を 13:41 で記録する' }));
    await waitFor(() =>
      expect(adjustMutate).toHaveBeenCalledWith({ kind: 'departure', time: '13:41' }),
    );
    // 応答が completed なら、その場で「訪問完了」に変わる。
    expect(await screen.findByText('訪問完了')).toBeInTheDocument();
  });
});

describe('過去の日の訪問詳細 (設計 §7-8)', () => {
  it('未訪問のまま過ぎた日: QR の打刻ボタンも「訪問できなかった」も出さない', () => {
    setVisit({ ...makeVisit('planned'), visit_date: '2026-06-29' });
    render(<MobileVisitDetailPage />);
    expect(screen.queryByText('QRで到着を記録')).toBeNull();
    expect(screen.queryByText('訪問できなかった（理由を記録）')).toBeNull();
  });

  it('退出の読み取りが無いまま過ぎた日: QR の退出ボタンは出さず、退出時刻は入れられる', () => {
    setVisit(
      adjustableVisit({
        visit_date: '2026-06-29',
        actual_arrival_at: '2026-06-29T04:06:20Z',
        actual_arrival_read_at: '2026-06-29T04:06:20Z',
      }),
    );
    render(<MobileVisitDetailPage />);
    expect(screen.queryByText('QRで退出を記録')).toBeNull();
    expect(screen.getByTestId('mobile-detail-manual-departure')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-detail-adjust')).toBeInTheDocument();
  });

  it('先の日の訪問: 「当日になると到着を記録できます。」', () => {
    setVisit({ ...makeVisit('planned'), visit_date: '2026-07-01' });
    render(<MobileVisitDetailPage />);
    expect(screen.queryByText('QRで到着を記録')).toBeNull();
    expect(screen.getByText('当日になると到着を記録できます。')).toBeInTheDocument();
  });

  it('今週の予定から開いたら、戻るリンクは「今週の予定に戻る」', () => {
    searchParamsGet.mockImplementation((key: string) => (key === 'from' ? 'week' : null));
    render(<MobileVisitDetailPage />);
    const back = screen.getByRole('link', { name: '今週の予定に戻る' });
    expect(back).toHaveAttribute('href', '/m/this-week');
  });

  it('通常は「今日の訪問に戻る」', () => {
    render(<MobileVisitDetailPage />);
    expect(screen.getByRole('link', { name: '今日の訪問に戻る' })).toHaveAttribute(
      'href',
      '/m/today',
    );
  });
});

describe('経過時間の起点 (設計 §7-6)', () => {
  it('実績の到着 (合わせた後の時刻) から数える', () => {
    vi.setSystemTime(new Date('2026-06-30T04:16:00Z')); // JST 13:16
    setVisit({ ...ADJUSTED, latest_checkin: makeCheckin('arrival', 'match', 5) });
    render(<MobileVisitDetailPage />);
    // 12:56 から 20 分 (読取 13:06 からなら 9 分 40 秒)。
    expect(screen.getByText('20:00')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-detail-elapsed')).toHaveTextContent('到着 12:56〜');
  });
});

// ===========================================================================
// コードレビューの指摘 (2026-09-30)
// ===========================================================================

/** 圏外で到着を退避させ、到着した直後のカードが出た状態にする (JST 13:06:20 に読み取り)。 */
async function arriveOffline() {
  checkInMutate.mockRejectedValueOnce(new TypeError('Failed to fetch'));
  vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
  render(<MobileVisitDetailPage />);
  fireEvent.click(screen.getByText('QRで到着を記録'));
  fireEvent.click(screen.getByText('__scan__'));
  await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
  fireEvent.click(screen.getByText('到着を記録する'));
  await waitFor(() => expect(screen.getByTestId('arrived-adjust-card')).toBeInTheDocument());
}

/** 再送の POST を途中で止める。`release` で成功、`fail` で通信失敗にする。 */
function holdResend() {
  let release!: () => void;
  let fail!: () => void;
  asMock(fetcher).mockImplementationOnce(
    () =>
      new Promise((resolve, reject) => {
        release = () => resolve({});
        fail = () => reject(new TypeError('Failed to fetch'));
      }),
  );
  return { release: () => release(), fail: () => fail() };
}

/** 再送で送った body。 */
function resentBody(call = 0): Record<string, unknown> {
  const init = asMock(fetcher).mock.calls[call]?.[1] as { body: string };
  return JSON.parse(init.body) as Record<string, unknown>;
}

describe('ディープリンクの到着が圏外で退避されたあとの退出 (H-1)', () => {
  it('退避でもトークンを消費し、退出は現地で読み直す — 退出の at は到着の時刻にならない', async () => {
    searchParamsGet.mockImplementation((key: string) => (key === 'qr' ? 'DEEPTOKEN1' : null));
    window.history.replaceState({}, '', '/m/today/visit-1?qr=DEEPTOKEN1&from=week');
    checkInMutate.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z')); // JST 13:06:20 にディープリンクで開いた
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(asMock(toast.warning)).toHaveBeenCalled());
    expect(listPending('staff-1')[0]?.payload).toMatchObject({
      at: '2026-06-30T04:06:20.000Z',
      qr_token: 'DEEPTOKEN1',
    });
    // URL からも `qr` を外す (他のクエリは残す)。
    expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-1?from=week', { scroll: false });

    // 35 分後に退出。トークンは到着で使ったので、スキャナが出る (現地で読み直す)。
    vi.setSystemTime(new Date('2026-06-30T04:41:00Z'));
    fireEvent.click(screen.getByText('QRで退出を記録'));
    expect(screen.getByTestId('qr-scanner')).toBeInTheDocument();
    fireEvent.click(screen.getByText('__scan__'));
    await waitFor(() => expect(screen.getByText('退出の確認')).toBeInTheDocument());
    expect(screen.getByTestId('preview-read-time')).toHaveTextContent('13:41');
    fireEvent.click(screen.getByText('退出を記録する'));
    await waitFor(() => expect(checkOutMutate).toHaveBeenCalledTimes(1));
    const sent = checkOutMutate.mock.calls[0][0] as { at: string };
    expect(sent).toMatchObject({ at: '2026-06-30T04:41:00.000Z', qr_token: 'TESTTOKEN' });
    // ここが要点 — 到着の読取時刻 (滞在 0 分) になっていない。
    expect(sent.at).not.toBe('2026-06-30T04:06:20.000Z');
  });

  it('退出にはディープリンクの読取時刻を使わない (退出を押した時点にする)', async () => {
    // 訪問中の visit を ?qr= で開いた (`/q` の「退出の記録へ」)。トークンは残っている。
    searchParamsGet.mockImplementation((key: string) => (key === 'qr' ? 'DEEPTOKEN1' : null));
    setVisit(makeVisit('in_progress'));
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    render(<MobileVisitDetailPage />);

    vi.setSystemTime(new Date('2026-06-30T04:41:00Z'));
    fireEvent.click(screen.getByText('QRで退出を記録'));
    // スキャンは省略する (トークンは URL から取得済み)。
    expect(screen.queryByTestId('qr-scanner')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('退出の確認')).toBeInTheDocument());
    expect(screen.getByTestId('preview-read-time')).toHaveTextContent('13:41');
    fireEvent.click(screen.getByText('退出を記録する'));
    await waitFor(() => expect(checkOutMutate).toHaveBeenCalledTimes(1));
    expect(checkOutMutate.mock.calls[0][0]).toMatchObject({
      at: '2026-06-30T04:41:00.000Z',
      qr_token: 'DEEPTOKEN1',
    });
  });
});

describe('/q から読取時刻を引き継ぐ (L-7)', () => {
  /** `?qr=DEEPTOKEN1&read_at=...` で開く。 */
  function openWithReadAt(readAt: string | null) {
    searchParamsGet.mockImplementation((key: string) =>
      key === 'qr' ? 'DEEPTOKEN1' : key === 'read_at' ? readAt : null,
    );
  }

  /** 到着を記録して、送った `at` を返す。 */
  async function recordArrival(): Promise<string> {
    fireEvent.click(screen.getByText('QRで到着を記録'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    return (checkInMutate.mock.calls[0][0] as { at: string }).at;
  }

  it('/q を開いた時刻を到着の at にする (詳細を開いた時刻ではない)', async () => {
    // 13:05:50 に QR を読み、選択画面を経て 13:06:20 に詳細が開いた。
    openWithReadAt('2026-06-30T04:05:50.000Z');
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('QRで到着を記録'));
    await waitFor(() => expect(screen.getByText('到着の確認')).toBeInTheDocument());
    expect(screen.getByTestId('preview-read-time')).toHaveTextContent('13:05');
    fireEvent.click(screen.getByText('到着を記録する'));
    await waitFor(() => expect(checkInMutate).toHaveBeenCalledTimes(1));
    expect(checkInMutate.mock.calls[0][0]).toMatchObject({ at: '2026-06-30T04:05:50.000Z' });
  });

  it.each([
    ['未来の時刻', '2026-06-30T04:30:00.000Z'],
    ['古すぎる時刻 (開きっぱなしの URL)', '2026-06-30T03:00:00.000Z'],
    ['別の日', '2026-06-29T04:05:50.000Z'],
    ['形の違う値', '13:05'],
    ['日付として読めない値', '2026-99-99T99:99:99.000Z'],
    ['タイムゾーンの書き方が違う値', '2026-06-30T13:05:50+09:00'],
  ])('%s は無視して、詳細を開いた時点の時刻にする', async (_label, readAt) => {
    openWithReadAt(readAt);
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    render(<MobileVisitDetailPage />);
    expect(await recordArrival()).toBe('2026-06-30T04:06:20.000Z');
  });

  it('消費したら URL から qr と read_at を外す (再読み込みで古い時刻を使わない)', async () => {
    openWithReadAt('2026-06-30T04:05:50.000Z');
    window.history.replaceState(
      {},
      '',
      '/m/today/visit-1?qr=DEEPTOKEN1&read_at=2026-06-30T04%3A05%3A50.000Z',
    );
    vi.setSystemTime(new Date('2026-06-30T04:06:20Z'));
    render(<MobileVisitDetailPage />);
    await recordArrival();
    expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-1', { scroll: false });
  });
});

describe('再送の POST 中に到着の時刻を合わせる (M-1)', () => {
  it('送信中は控えに書けたことにせず、送信が終わってから調整 API で届ける', async () => {
    await arriveOffline();
    const resend = holdResend();
    window.dispatchEvent(new Event('online'));
    await waitFor(() => expect(asMock(fetcher)).toHaveBeenCalledTimes(1));

    // POST が飛んでいる最中に「10分前」を押した。
    adjustMutate.mockResolvedValueOnce(ADJUSTED);
    fireEvent.click(screen.getByRole('button', { name: /^10分前/ }));
    // まだどこにも保存できていないので、「合わせました」とは出さない。
    expect(screen.queryByText('到着を 12:56 に合わせました')).toBeNull();
    expect(adjustMutate).not.toHaveBeenCalled();
    expect(resentBody()).not.toHaveProperty('adjusted_time');

    resend.release();
    await waitFor(() =>
      expect(adjustMutate).toHaveBeenCalledWith({ kind: 'arrival', time: '12:56' }),
    );
    expect(listPending('staff-1')).toHaveLength(0);
    expect(await screen.findByText('到着を 12:56 に合わせました')).toBeInTheDocument();
  });

  it('「元に戻す」も同じ — 送信が終わってから読取時刻に戻す', async () => {
    await arriveOffline();
    fireEvent.click(screen.getByRole('button', { name: /^10分前/ }));
    expect(await screen.findByText('到着を 12:56 に合わせました')).toBeInTheDocument();

    const resend = holdResend();
    window.dispatchEvent(new Event('online'));
    await waitFor(() => expect(asMock(fetcher)).toHaveBeenCalledTimes(1));
    // 送った body には、合わせた時刻が載っている。
    expect(resentBody()).toMatchObject({ adjusted_time: '12:56' });

    fireEvent.click(screen.getByText('元に戻す'));
    expect(resetMutate).not.toHaveBeenCalled();

    resend.release();
    await waitFor(() => expect(resetMutate).toHaveBeenCalledWith('arrival'));
    expect(adjustMutate).not.toHaveBeenCalled();
  });

  it('送信に失敗して控えが残ったら、控えに書き込む (API は呼ばない)', async () => {
    await arriveOffline();
    const resend = holdResend();
    // 待ち合わせの再送 (2 回目) も、まだ電波が無くて届かない。
    asMock(fetcher).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    window.dispatchEvent(new Event('online'));
    await waitFor(() => expect(asMock(fetcher)).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /^10分前/ }));
    resend.fail();

    expect(await screen.findByText('到着を 12:56 に合わせました')).toBeInTheDocument();
    expect(adjustMutate).not.toHaveBeenCalled();
    expect(listPending('staff-1')[0]?.payload).toMatchObject({
      at: '2026-06-30T04:06:20.000Z',
      adjusted_time: '12:56',
    });
  });

  it('飛んでいた POST は失敗したが、待ち合わせの再送で届いた → 調整 API で届ける', async () => {
    await arriveOffline();
    const resend = holdResend();
    window.dispatchEvent(new Event('online'));
    await waitFor(() => expect(asMock(fetcher)).toHaveBeenCalledTimes(1));

    adjustMutate.mockResolvedValueOnce(ADJUSTED);
    fireEvent.click(screen.getByRole('button', { name: /^10分前/ }));
    resend.fail();

    await waitFor(() => expect(adjustMutate).toHaveBeenCalledTimes(1));
    // 2 回目の POST で届いた。その body に合わせた時刻は無いので、API が届ける。
    expect(asMock(fetcher)).toHaveBeenCalledTimes(2);
    expect(resentBody(1)).not.toHaveProperty('adjusted_time');
    expect(listPending('staff-1')).toHaveLength(0);
  });

  it('待っている間は二度押しできない', async () => {
    await arriveOffline();
    const resend = holdResend();
    window.dispatchEvent(new Event('online'));
    await waitFor(() => expect(asMock(fetcher)).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /^10分前/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /^5分前/ })).toBeDisabled());

    adjustMutate.mockResolvedValueOnce(ADJUSTED);
    resend.release();
    await waitFor(() => expect(adjustMutate).toHaveBeenCalledTimes(1));
  });
});

describe('圏外で退避中の到着 — カードを閉じた後の入口とシート (L-1 / L-8)', () => {
  it('カードを閉じても、経過時間の下から到着の時刻を合わせられる (控えに書く)', async () => {
    await arriveOffline();
    // カードが出ている間は、同じ入口を重ねて出さない。
    expect(screen.queryByTestId('mobile-detail-adjust-queued')).toBeNull();

    fireEvent.click(screen.getByText('このままでOK'));
    expect(screen.queryByTestId('arrived-adjust-card')).toBeNull();
    const entry = screen.getByTestId('mobile-detail-adjust-queued');
    expect(entry).toHaveTextContent('到着の時刻を合わせる');
    expect(entry.className).toContain('h-11');

    fireEvent.click(entry);
    expect(await screen.findByText('実績の時刻を合わせる')).toBeInTheDocument();
    // 退避中は退出を合わせられない。
    expect(screen.getByRole('button', { name: '退出 （未記録）' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '10分前' }));
    fireEvent.click(screen.getByRole('button', { name: '12:56 に合わせる' }));

    await waitFor(() =>
      expect(listPending('staff-1')[0]?.payload).toMatchObject({
        at: '2026-06-30T04:06:20.000Z',
        adjusted_time: '12:56',
      }),
    );
    expect(adjustMutate).not.toHaveBeenCalled();
    expect(screen.getByTestId('mobile-detail-elapsed')).toHaveTextContent('到着 12:56〜');
  });

  it('サーバに届いた到着には出さない (実績の行の「時刻を合わせる」がある)', () => {
    setVisit(adjustableVisit());
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('mobile-detail-adjust')).toBeInTheDocument();
    expect(screen.queryByTestId('mobile-detail-adjust-queued')).toBeNull();
  });

  it('シートを開いている間に再送が成功しても、選びかけの時刻を保つ (L-8)', async () => {
    await arriveOffline();
    fireEvent.click(screen.getByText('細かく合わせる'));
    expect(await screen.findByText('実績の時刻を合わせる')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '10分前' }));
    expect(screen.getByTestId('actual-time-draft')).toHaveTextContent('12:56');

    // 電波が戻り、退避した到着が送信された (詳細の再取得はまだ届いていない)。
    window.dispatchEvent(new Event('online'));
    await waitFor(() => expect(listPending('staff-1')).toHaveLength(0));
    await waitFor(() => expect(qcStub.invalidateQueries).toHaveBeenCalled());

    // シートは開いたまま、選びかけの時刻も残っている。
    expect(screen.getByTestId('actual-time-sheet')).toBeInTheDocument();
    expect(screen.getByTestId('actual-time-draft')).toHaveTextContent('12:56');

    // 保存は、もう届いているので調整 API へ。
    adjustMutate.mockResolvedValueOnce(ADJUSTED);
    fireEvent.click(screen.getByRole('button', { name: '12:56 に合わせる' }));
    await waitFor(() =>
      expect(adjustMutate).toHaveBeenCalledWith({ kind: 'arrival', time: '12:56' }),
    );
  });
});

describe('?arrived=1 は 1 回きり (L-6)', () => {
  it('カードを出したら URL から外す (他のクエリは残す)', () => {
    searchParamsGet.mockImplementation((key: string) =>
      key === 'arrived' ? '1' : key === 'from' ? 'week' : null,
    );
    window.history.replaceState({}, '', '/m/today/visit-1?arrived=1&from=week');
    setVisit(adjustableVisit());
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('arrived-adjust-card')).toBeInTheDocument();
    expect(routerReplace).toHaveBeenCalledWith('/m/today/visit-1?from=week', { scroll: false });
  });

  it('arrived が無ければ URL に触らない', () => {
    window.history.replaceState({}, '', '/m/today/visit-1?from=week');
    render(<MobileVisitDetailPage />);
    expect(routerReplace).not.toHaveBeenCalled();
  });
});

describe('過去の日の未打刻の訪問 (L-9)', () => {
  const NOTE = 'この日の記録はありません。必要な場合は管理者にお伝えください。';

  it('ボタンが出ない理由を一言出す', () => {
    setVisit({ ...makeVisit('planned'), visit_date: '2026-06-29' });
    render(<MobileVisitDetailPage />);
    expect(screen.getByTestId('mobile-detail-past-no-record')).toHaveTextContent(NOTE);
  });

  it('今日・先の日・打刻のある過去の日・未訪問として記録済みの日には出さない', () => {
    for (const visit of [
      makeVisit('planned'),
      { ...makeVisit('planned'), visit_date: '2026-07-01' },
      adjustableVisit({
        visit_date: '2026-06-29',
        actual_arrival_at: '2026-06-29T04:06:20Z',
        actual_arrival_read_at: '2026-06-29T04:06:20Z',
      }),
      { ...makeVisit('no_show'), visit_date: '2026-06-29' },
      { ...makeVisit('cancelled'), visit_date: '2026-06-29' },
    ]) {
      setVisit(visit);
      const { unmount } = render(<MobileVisitDetailPage />);
      expect(screen.queryByTestId('mobile-detail-past-no-record')).toBeNull();
      unmount();
    }
  });
});

describe('手で入れた退出を取り消せる (M-2)', () => {
  /** 13:41 に手で入れた退出 (読み取りなし) で完了した訪問。 */
  const MANUAL_DONE = adjustableVisit({
    status: 'completed',
    actual_departure_at: '2026-06-30T04:41:00Z',
    actual_departure_read_at: null,
    actual_departure_manual: true,
  });

  it('記録した直後のトーストに「元に戻す」が付き、押すと取り消す', async () => {
    vi.setSystemTime(new Date('2026-06-30T05:00:00Z'));
    adjustMutate.mockResolvedValueOnce(MANUAL_DONE);
    resetMutate.mockResolvedValueOnce(adjustableVisit());
    setVisit(adjustableVisit());
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByText('退出の QR を読んでいないときは、退出時刻を入れる'));
    fireEvent.click(await screen.findByRole('button', { name: '退出を 13:41 で記録する' }));

    await waitFor(() => expect(asMock(toast.success)).toHaveBeenCalled());
    const [message, options] = asMock(toast.success).mock.calls[0] as [
      string,
      { action: { label: string; onClick: () => void }; classNames: { actionButton: string } },
    ];
    expect(message).toBe('退出を 13:41 で記録しました');
    expect(options.action.label).toBe('元に戻す');
    // 押す場所は 44px。
    expect(options.classNames.actionButton).toContain('h-11');
    expect(await screen.findByText('訪問完了')).toBeInTheDocument();

    options.action.onClick();
    await waitFor(() => expect(resetMutate).toHaveBeenCalledWith('departure'));
    await waitFor(() =>
      expect(asMock(toast.success)).toHaveBeenCalledWith('入れた退出時刻を取り消しました'),
    );
    // 訪問中に戻り、QR の退出ボタンが戻ってくる。
    expect(await screen.findByText('QRで退出を記録')).toBeInTheDocument();
  });

  it('完了後も、シートの退出側から取り消せる', async () => {
    vi.setSystemTime(new Date('2026-06-30T05:00:00Z'));
    resetMutate.mockResolvedValueOnce(adjustableVisit());
    setVisit(MANUAL_DONE);
    render(<MobileVisitDetailPage />);
    expect(screen.queryByText('QRで退出を記録')).toBeNull();

    fireEvent.click(screen.getByTestId('mobile-detail-adjust'));
    fireEvent.click(await screen.findByRole('button', { name: '退出 13:41' }));
    fireEvent.click(screen.getByRole('button', { name: '入れた退出時刻を取り消す' }));

    await waitFor(() => expect(resetMutate).toHaveBeenCalledWith('departure'));
    await waitFor(() =>
      expect(asMock(toast.success)).toHaveBeenCalledWith('入れた退出時刻を取り消しました'),
    );
    await waitFor(() => expect(screen.queryByTestId('actual-time-sheet')).toBeNull());
    expect(await screen.findByText('QRで退出を記録')).toBeInTheDocument();
  });

  it('読み取りのある退出には、取り消すボタンを出さない', async () => {
    vi.setSystemTime(new Date('2026-06-30T05:00:00Z'));
    setVisit(
      adjustableVisit({
        status: 'completed',
        actual_departure_at: '2026-06-30T04:41:00Z',
        actual_departure_read_at: '2026-06-30T04:41:00Z',
      }),
    );
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByTestId('mobile-detail-adjust'));
    fireEvent.click(await screen.findByRole('button', { name: '退出 13:41' }));
    expect(screen.queryByRole('button', { name: '入れた退出時刻を取り消す' })).toBeNull();
    // 読み取りのある退出は「合わせる」だけ (理由の選択は無い)。
    expect(screen.getByRole('button', { name: '13:41 に合わせる' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '読み取りが後になった' })).toBeNull();
  });

  it('取り消せなかったら、サーバの detail を出してシートは開いたまま', async () => {
    vi.setSystemTime(new Date('2026-06-30T05:00:00Z'));
    resetMutate.mockRejectedValueOnce(
      new ApiError('conflict', 409, { detail: '削除された訪問の時刻は合わせられません' }),
    );
    setVisit(MANUAL_DONE);
    render(<MobileVisitDetailPage />);
    fireEvent.click(screen.getByTestId('mobile-detail-adjust'));
    fireEvent.click(await screen.findByRole('button', { name: '退出 13:41' }));
    fireEvent.click(screen.getByRole('button', { name: '入れた退出時刻を取り消す' }));
    await waitFor(() =>
      expect(asMock(toast.error)).toHaveBeenCalledWith('時刻を合わせられませんでした', {
        description: '削除された訪問の時刻は合わせられません',
      }),
    );
    expect(screen.getByTestId('actual-time-sheet')).toBeInTheDocument();
  });
});
