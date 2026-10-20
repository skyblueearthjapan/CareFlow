/**
 * /m/leave スマホの「休み」ページ (leave-and-kinmu-sheet-design §6・モック「スマホの申請画面」).
 *
 * 担保する約束:
 *   - 種類を選んでから日を押す → 「N 日を申請する」→ メモを付けて逐次 POST (override_type に種類)
 *   - 同じ種類でもう一度押すと外れる・勤務しない曜日は選べない
 *   - 一部が失敗したら失敗した日だけ選んだまま残る
 *   - 締め切り (前の月の 20 日 JST) 後・確定後は「変更をお願いする」(kind='change'・理由が必須・cancel も)
 *   - 申請中の日は取り下げ・承認済みの日は「管理者へ相談」
 *   - 休みの数: 型の休み + 公休 1 + 半休 0.5・有給は数えない / 半休は「午前休（承認済み）」
 *   - 申請と結果 (却下の理由)・staffId が無いアカウントは申請できない
 *
 * 「いま」は 2026-11-10 (JST) に固定 → 既定の月は 2026 年 12 月 (締め切り 11/20（金）)。
 * 12 月の土日は 8 日。職員の型は月〜金の勤務。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

let sessionStaffId: string | null = 'staff-1';
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: {
      user: { staffId: sessionStaffId, role: 'staff' },
      accessToken: 'a',
      refreshToken: 'r',
    },
    status: 'authenticated',
  }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const createMutateAsync = vi.fn();
const withdrawMutateAsync = vi.fn();
const refetchRequests = vi.fn();
let requestItems: unknown[] = [];
vi.mock('@/lib/queries/pending_requests', () => ({
  usePendingRequests: () => ({
    data: { items: requestItems, total: requestItems.length, limit: 100, offset: 0 },
    isLoading: false,
    refetch: refetchRequests,
  }),
  useCreatePendingRequest: () => ({ mutateAsync: createMutateAsync, isPending: false }),
  useWithdrawPendingRequest: () => ({ mutateAsync: withdrawMutateAsync, isPending: false }),
}));

let overrides: unknown[] = [];
vi.mock('@/lib/queries/staff-overrides', () => ({
  useStaffOverrides: () => ({ data: overrides, isLoading: false }),
}));

const SHIFTS = Array.from({ length: 7 }, (_, weekday) => ({
  weekday,
  is_on: weekday <= 4,
  start_time: weekday <= 4 ? '09:00' : null,
  end_time: weekday <= 4 ? '18:00' : null,
}));
vi.mock('@/lib/queries/staff-shifts', () => ({
  useStaffShifts: () => ({ data: { shifts: SHIFTS }, isLoading: false }),
}));

let confirmations: unknown[] = [];
vi.mock('@/lib/queries/staff-shift-confirmations', () => ({
  useShiftConfirmations: () => ({ data: confirmations, isLoading: false }),
}));

import { toast } from '@/components/ui/sonner';
import MobileLeavePage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function request(
  id: string,
  date: string,
  status: 'pending' | 'approved' | 'rejected',
  payload: Record<string, unknown> = {},
  rejection_reason: string | null = null,
) {
  return {
    id,
    request_type: 'staff_off',
    payload: { staff_id: 'staff-1', date, override_type: 'off', ...payload },
    target_staff_id: 'staff-1',
    target_date: date,
    status,
    rejection_reason,
    requester_user_id: 'user-1',
    created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-01T00:00:00Z',
  };
}

function override(id: string, date: string, type: string) {
  return { id: `00000000-0000-0000-0000-00000000000${id}`, date, type };
}

/** カレンダーの日のボタン (aria-label は '12/3（木）…')。 */
function day(label: string) {
  return screen.getByRole('button', { name: new RegExp(`^${label}`) });
}

function chip(label: string) {
  return screen.getByRole('button', { name: new RegExp(`^${label}`) });
}

type CreateArg = {
  request_type: string;
  target_staff_id: string;
  target_date: string;
  payload: Record<string, unknown>;
};
const createArgs = () => createMutateAsync.mock.calls.map((c) => c[0] as CreateArg);

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-10T10:00:00+09:00'));
  sessionStaffId = 'staff-1';
  requestItems = [];
  overrides = [];
  confirmations = [];
  createMutateAsync.mockResolvedValue({});
  withdrawMutateAsync.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('MobileLeavePage — 申請', () => {
  it('来月・締め切りの帯・休みの数を出し、未選択では申請ボタンが押せない', () => {
    render(<MobileLeavePage />);
    expect(screen.getByRole('heading', { name: '休み' })).toBeInTheDocument();
    expect(screen.getByText('2026年12月')).toBeInTheDocument();
    expect(screen.getByText(/12月の休みの希望は/)).toHaveTextContent('11/20（金）まで');
    expect(screen.getByTestId('leave-count')).toHaveTextContent('12月の休み 8 日');
    expect(screen.getByRole('button', { name: '日を選ぶと申請できます' })).toBeDisabled();
    // その日何人休むかは出さない
    expect(screen.queryByText(/休\d+人/)).not.toBeInTheDocument();
  });

  it('種類を選んで日を押し、メモを付けて送ると種類ごとの override_type で逐次 POST する', async () => {
    render(<MobileLeavePage />);
    fireEvent.click(chip('午前休'));
    fireEvent.click(day('12/3（木）'));
    fireEvent.click(chip('有給'));
    fireEvent.click(day('12/4（金）'));
    // 午前休 0.5・有給は数えない
    expect(screen.getByText(/選択中 0.5 日/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '2 日を申請する' }));
    fireEvent.change(await screen.findByLabelText(/メモ/), { target: { value: '通院のため' } });
    fireEvent.click(screen.getByRole('button', { name: '申請する' }));

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(2));
    const [a, b] = createArgs();
    expect(a).toMatchObject({
      request_type: 'staff_off',
      target_staff_id: 'staff-1',
      target_date: '2026-12-03',
      payload: {
        staff_id: 'staff-1',
        date: '2026-12-03',
        override_type: 'am_off',
        note: '通院のため',
      },
    });
    expect(a!.payload.kind).toBeUndefined();
    expect(b).toMatchObject({ target_date: '2026-12-04', payload: { override_type: 'paid_off' } });
    await waitFor(() =>
      expect(asMock(toast.success)).toHaveBeenCalledWith('2 日を申請しました', expect.anything()),
    );
    expect(screen.getByRole('button', { name: '日を選ぶと申請できます' })).toBeInTheDocument();
  });

  it('同じ種類でもう一度押すと外れ、別の種類で押すと種類が変わる', () => {
    render(<MobileLeavePage />);
    fireEvent.click(day('12/3（木）'));
    expect(day('12/3（木）')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(day('12/3（木）'));
    expect(day('12/3（木）')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(day('12/3（木）'));
    expect(day('12/3（木）')).toHaveTextContent('休');
    fireEvent.click(chip('午後休'));
    fireEvent.click(day('12/3（木）'));
    expect(day('12/3（木）')).toHaveTextContent('午後');
    expect(screen.getByRole('button', { name: '1 日を申請する' })).toBeEnabled();
  });

  it('勤務しない曜日は選べず、案内を出す (マスに「休」は出さず、読み上げのラベルは残す)', () => {
    render(<MobileLeavePage />);
    expect(day('12/5（土）')).toHaveAccessibleName('12/5（土） 勤務しない曜日');
    expect(day('12/5（土）')).toHaveTextContent(/^5$/);
    fireEvent.click(day('12/5（土）'));
    expect(asMock(toast.info)).toHaveBeenCalledWith('12/5（土） はもともと勤務しない曜日です');
    expect(screen.getByRole('button', { name: '日を選ぶと申請できます' })).toBeDisabled();
  });

  it('一部が失敗したら、失敗した日だけ選んだまま残し 422 の文言を見せる', async () => {
    createMutateAsync
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new Error('この日はすでに申請があります'));
    render(<MobileLeavePage />);
    fireEvent.click(day('12/3（木）'));
    fireEvent.click(day('12/4（金）'));
    fireEvent.click(screen.getByRole('button', { name: '2 日を申請する' }));
    fireEvent.click(await screen.findByRole('button', { name: '申請する' }));

    await waitFor(() =>
      expect(asMock(toast.error)).toHaveBeenCalledWith('1 日の申請ができませんでした', {
        description: '12/4（金）: この日はすでに申請があります',
      }),
    );
    expect(screen.getByRole('button', { name: '1 日を申請する' })).toBeInTheDocument();
    expect(day('12/4（金）')).toHaveAttribute('aria-pressed', 'true');
  });

  it('送信中は小窓を閉じられず、終わったら成功した日だけを選択から外す', async () => {
    let resolveFirst: (v: unknown) => void = () => {};
    createMutateAsync
      .mockImplementationOnce(() => new Promise((r) => (resolveFirst = r)))
      .mockRejectedValueOnce(new Error('この日はすでに申請があります'));
    render(<MobileLeavePage />);
    fireEvent.click(day('12/3（木）'));
    fireEvent.click(day('12/4（金）'));
    fireEvent.click(screen.getByRole('button', { name: '2 日を申請する' }));
    fireEvent.click(await screen.findByRole('button', { name: '申請する' }));

    // 送信中: 「戻る」は押せず、Esc でも閉じない
    expect(await screen.findByRole('button', { name: '申請中…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '戻る' })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    resolveFirst({});
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(day('12/3（木）')).toHaveAttribute('aria-pressed', 'false');
    expect(day('12/4（金）')).toHaveAttribute('aria-pressed', 'true');
  });

  it('staffId 未紐付けアカウントには Alert を出す', () => {
    sessionStaffId = null;
    render(<MobileLeavePage />);
    expect(screen.getByText('申請できません')).toBeInTheDocument();
  });
});

describe('MobileLeavePage — 申請中・承認済み・結果', () => {
  it('申請中の日を押すと取り下げられる', async () => {
    requestItems = [request('req-1', '2026-12-08', 'pending', { override_type: 'pm_off' })];
    render(<MobileLeavePage />);
    expect(day('12/8（火）')).toHaveAccessibleName('12/8（火） 午後休を申請中');
    // 申請中の休みは「（申請中 0.5 日）」
    expect(screen.getByText(/申請中 0.5 日/)).toBeInTheDocument();

    fireEvent.click(day('12/8（火）'));
    expect(
      await screen.findByText('管理者がまだ見ていません。取り下げると申請は消えます。'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取り下げる' }));
    await waitFor(() => expect(withdrawMutateAsync).toHaveBeenCalledWith('req-1'));
    await waitFor(() => expect(refetchRequests).toHaveBeenCalled());
  });

  it('取り下げに失敗しても小窓を閉じ、一覧を読み直す', async () => {
    requestItems = [request('req-1', '2026-12-08', 'pending')];
    withdrawMutateAsync.mockRejectedValueOnce(new Error('この申請はすでに承認されています'));
    render(<MobileLeavePage />);
    fireEvent.click(day('12/8（火）'));
    fireEvent.click(await screen.findByRole('button', { name: '取り下げる' }));
    await waitFor(() =>
      expect(asMock(toast.error)).toHaveBeenCalledWith('取り下げられませんでした', {
        description: 'この申請はすでに承認されています',
      }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(refetchRequests).toHaveBeenCalled();
  });

  it('申請中の増分は「その種類の数 − その日のいまの数」・勤務しない曜日は 0', () => {
    overrides = [override('1', '2026-12-09', '午前休')];
    requestItems = [
      request('req-1', '2026-12-09', 'pending', { override_type: 'off' }), // 1 − 0.5
      request('req-2', '2026-12-12', 'pending', { override_type: 'off' }), // 土曜 → 0
    ];
    render(<MobileLeavePage />);
    expect(screen.getByTestId('leave-count')).toHaveTextContent('（申請中 0.5 日）');
  });

  it('承認済みの日を押すと「管理者へ相談」の案内だけ出す', async () => {
    overrides = [override('1', '2026-12-09', '休み')];
    render(<MobileLeavePage />);
    fireEvent.click(day('12/9（水）'));
    expect(
      await screen.findByText('承認された休みです。変えたいときは管理者へ相談してください。'),
    ).toBeInTheDocument();
    expect(createMutateAsync).not.toHaveBeenCalled();
  });

  it('半休は「午前休（承認済み）」で半分を塗り、休みの数は 0.5・有給は数えない', () => {
    overrides = [
      override('1', '2026-12-09', '午前休'),
      override('2', '2026-12-10', '有給'),
      override('3', '2026-12-11', '休み'),
    ];
    render(<MobileLeavePage />);
    expect(day('12/9（水）')).toHaveAccessibleName('12/9（水） 午前休（承認済み）');
    expect(day('12/9（水）')).toHaveTextContent('午前');
    expect(day('12/10（木）')).toHaveAccessibleName('12/10（木） 有給（承認済み）');
    expect(screen.getByTestId('leave-count')).toHaveTextContent('12月の休み 9.5 日');
  });

  it('申請と結果に却下の理由・承認後に取り消された休みを出す', () => {
    requestItems = [
      request('req-1', '2026-12-14', 'rejected', {}, 'この日は研修のため'),
      request('req-2', '2026-12-15', 'approved', { override_type: 'am_off' }),
    ];
    render(<MobileLeavePage />);
    expect(screen.getByText('理由：この日は研修のため')).toBeInTheDocument();
    expect(day('12/14（月）')).toHaveAccessibleName('12/14（月） 却下');
    // 承認済みなのに休みの登録が無い → 承認後に取り消し
    expect(screen.getByText('承認後に取り消し')).toBeInTheDocument();
  });

  it('申請と違う種類で承認されたら、いまの種類を「午後休で承認」と出す', () => {
    overrides = [override('1', '2026-12-15', '午後休')];
    requestItems = [request('req-2', '2026-12-15', 'approved', { override_type: 'am_off' })];
    render(<MobileLeavePage />);
    expect(screen.getByText('午後休で承認')).toBeInTheDocument();
  });
});

describe('MobileLeavePage — 締め切り後・確定後の「変更をお願いする」', () => {
  it('締め切り (11/20 23:59 JST) までは 12 月を開き、普通の申請', () => {
    vi.setSystemTime(new Date('2026-11-20T23:59:00+09:00'));
    render(<MobileLeavePage />);
    expect(screen.getByText('2026年12月')).toBeInTheDocument();
    fireEvent.click(day('12/3（木）'));
    expect(day('12/3（木）')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText(/を過ぎました/)).not.toBeInTheDocument();
  });

  it('11/21 0:00 JST からは開いたときの月が再来月 (1 月) になる', () => {
    vi.setSystemTime(new Date('2026-11-21T00:00:00+09:00'));
    render(<MobileLeavePage />);
    expect(screen.getByText('2027年1月')).toBeInTheDocument();
    expect(screen.getByText(/1月の休みの希望は/)).toHaveTextContent('12/20（日）まで');
  });

  it('11/21 0:00 JST を過ぎた 12 月は、日を押すと理由が必須の変更のお願い (kind=change) になる', async () => {
    vi.setSystemTime(new Date('2026-11-21T00:00:00+09:00'));
    render(<MobileLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: '前の月' }));
    expect(screen.getByText(/を過ぎました/)).toHaveTextContent('締め切り（11/20（金））');
    // 種類のボタンは出さない (お願いの中で選ぶ)
    expect(screen.queryByRole('group', { name: '休みの種類' })).not.toBeInTheDocument();

    fireEvent.click(day('12/3（木）'));
    const submit = await screen.findByRole('button', { name: '変更をお願いする' });
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: 'この日を「午後休」にしてほしい' }));
    fireEvent.change(screen.getByLabelText(/理由/), { target: { value: '急な通院のため' } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1));
    expect(createArgs()[0]).toMatchObject({
      request_type: 'staff_off',
      target_date: '2026-12-03',
      payload: {
        staff_id: 'staff-1',
        date: '2026-12-03',
        override_type: 'pm_off',
        kind: 'change',
        note: '急な通院のため',
      },
    });
  });

  it('確定済みの月は、休みの日を押して「休みを取り消してほしい」(cancel) を送れる', async () => {
    confirmations = [
      {
        id: '00000000-0000-0000-0000-000000000001',
        staff_id: 'staff-1',
        month: '2026-12-01',
        confirmed_by: null,
        confirmed_at: '2026-11-08T03:00:00Z',
      },
    ];
    overrides = [override('1', '2026-12-09', '休み')];
    render(<MobileLeavePage />);
    expect(screen.getByText(/12月は確定しました/)).toBeInTheDocument();

    fireEvent.click(day('12/9（水）'));
    // いまの種類 (公休) はお願いの選択肢に出さない
    expect(
      await screen.findByRole('radio', { name: '「午前休」に変えてほしい' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: '「公休」に変えてほしい' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: '休みを取り消してほしい（出勤したい）' }));
    fireEvent.change(screen.getByLabelText(/理由/), {
      target: { value: '出勤できるようになったため' },
    });
    fireEvent.click(screen.getByRole('button', { name: '変更をお願いする' }));

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1));
    expect(createArgs()[0]!.payload).toMatchObject({
      override_type: 'cancel',
      kind: 'change',
      note: '出勤できるようになったため',
    });
  });

  it('変更のお願いが 422 で断られたら、その文言を見せる', async () => {
    vi.setSystemTime(new Date('2026-11-25T09:00:00+09:00'));
    createMutateAsync.mockRejectedValueOnce(new Error('理由を入れてください'));
    render(<MobileLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: '前の月' }));
    fireEvent.click(day('12/3（木）'));
    fireEvent.change(await screen.findByLabelText(/理由/), { target: { value: '用事' } });
    fireEvent.click(screen.getByRole('button', { name: '変更をお願いする' }));
    await waitFor(() =>
      expect(asMock(toast.error)).toHaveBeenCalledWith('お願いを送れませんでした', {
        description: '理由を入れてください',
      }),
    );
  });
});
