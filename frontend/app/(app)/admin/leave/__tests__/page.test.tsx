/**
 * /admin/leave 「休み・勤務表」(leave-and-kinmu-sheet-design §5・モック「休み・勤務表（案A）」).
 *
 * 担保する約束:
 *   - 表: 種類 (公休・午前休・午後休・有給)・申請中 (?)・型で勤務しない曜日・祝日・事業所の休み・申N・
 *     足/カ・訪問の件数・承認後に取り消し・送信の状態
 *   - その日休む人の数と黄色・右端の休日数 (あと N 日・通れば・対象外)
 *   - 押すと入るもの: 続けて何マスでも・申請中のマスは上書きせず小窓・勤務しない曜日は入れない・取り消す
 *   - 小窓: 種類を変える・取り消す (「元に戻す」は出さない)・時間変更は消えます・申請の承認/種類を変えて承認/却下 (理由必須)
 *   - 申請中の一覧: 日ごと/人ごとにまとめて承認・1 件ずつ承認・行に指を置くとマスが光る
 *   - 確定のダイアログ: 申請中の残り・足りない人・勤務すべき時間数 (前の月の値)
 *   - 事業所の休み: 入れる・消す・全員に公休を入れる
 *   - ?staff=&date= でそのマスの小窓を開く・?staff= だけならその人の行を光らせる
 *   - 管理者だけ・ページは横に広がらない (表の中だけスクロール)
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ApiError } from '@/lib/api-client';
import type * as ReactQueryModule from '@tanstack/react-query';
import type * as LeaveQueries from '@/lib/queries/leave';
import { leaveMonthViewSchema, type LeaveMonthView } from '@/lib/schemas/leave';

import { makeView, P1, P2, P3, P4, S1, S2, S3, S4 } from './fixtures';

let role = 'admin';
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { role }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

let search = new URLSearchParams();
vi.mock('next/navigation', () => ({
  useSearchParams: () => search,
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// 読み直しは書き込みが落ち着いてから 1 回 (qcStub で数える)
const qcStub = { invalidateQueries: vi.fn(() => Promise.resolve()) };
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof ReactQueryModule>();
  return { ...actual, useQueryClient: () => qcStub };
});

let kinmuSheets: unknown[] = [];

vi.mock('@/lib/queries/googleDrive', () => ({
  useGoogleDriveStatus: () => ({
    data: { status: 'connected', folder_name: '勤務表' },
    isError: false,
  }),
}));

let view: LeaveMonthView = makeView();
const monthCalls: unknown[][] = [];
function mutation() {
  return { mutateAsync: vi.fn(), isPending: false };
}
const m = {
  setDay: mutation(),
  setDayBulk: mutation(),
  cancelDay: mutation(),
  approveOne: mutation(),
  approveBatch: mutation(),
  reject: mutation(),
  putClosed: mutation(),
  deleteClosed: mutation(),
  putMonth: mutation(),
  confirm: mutation(),
};
vi.mock('@/lib/queries/leave', async (importOriginal) => {
  const actual = await importOriginal<typeof LeaveQueries>();
  return {
    leaveErrorMessage: actual.leaveErrorMessage,
    leaveErrorRequestId: actual.leaveErrorRequestId,
    invalidateAfterLeave: actual.invalidateAfterLeave,
    useKinmuSheets: () => ({ data: kinmuSheets, isError: false }),
    useSetLeaveDayBulk: () => m.setDayBulk,
    useLeaveMonth: (...args: unknown[]) => {
      monthCalls.push(args);
      return { data: args[1] === false ? undefined : view, isError: false, error: null };
    },
    useSetLeaveDay: () => m.setDay,
    useCancelLeaveDay: () => m.cancelDay,
    useApproveLeaveRequest: () => m.approveOne,
    useApproveLeaveBatch: () => m.approveBatch,
    useRejectLeaveRequest: () => m.reject,
    usePutClosedDay: () => m.putClosed,
    useDeleteClosedDay: () => m.deleteClosed,
    usePutLeaveMonth: () => m.putMonth,
    useConfirmLeaveMonth: () => m.confirm,
  };
});

import { toast } from '@/components/ui/sonner';
import AdminLeavePage from '../page';

const DAY_RESULT = {
  staff_id: S1,
  date: '2026-11-16',
  leave_type: 'off',
  past_day: false,
  moved_visit_count: 2,
  moved_patient_count: 2,
  skipped_visit_count: 0,
  cancelled_event_count: 0,
  accompaniment_count: 0,
};

function cellEl(staffId: string, date: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`[data-cell="${staffId}|${date}"]`);
  if (!el) throw new Error(`cell not found: ${staffId} ${date}`);
  return el;
}

function stamp(name: string) {
  fireEvent.click(
    within(screen.getByRole('group', { name: '押すと入るもの' })).getByRole('button', { name }),
  );
}

async function popover() {
  return screen.findByRole('dialog');
}

beforeEach(() => {
  role = 'admin';
  search = new URLSearchParams();
  view = makeView();
  monthCalls.length = 0;
  for (const k of Object.keys(m) as (keyof typeof m)[]) m[k] = mutation();
  qcStub.invalidateQueries.mockClear();
  kinmuSheets = [];
  m.setDayBulk.mutateAsync.mockResolvedValue({
    date: '2026-11-30',
    override_type: 'off',
    applied: [{}, {}, {}],
    moved_visit_count: 5,
  });
  m.setDay.mutateAsync.mockResolvedValue(DAY_RESULT);
  m.cancelDay.mutateAsync.mockResolvedValue({
    staff_id: S1,
    date: '2026-11-10',
    cancelled_type: 'off',
    moved_visit_count: 2,
  });
  m.approveOne.mutateAsync.mockResolvedValue({
    request_id: P1,
    staff_id: S1,
    date: '2026-11-12',
    override_type: 'off',
    result: { ...DAY_RESULT, date: '2026-11-12', moved_visit_count: 3, moved_patient_count: 3 },
  });
  m.approveBatch.mutateAsync.mockResolvedValue({ approved: [{}, {}], moved_visit_count: 4 });
  m.reject.mutateAsync.mockResolvedValue({ request_id: P1, reason: 'x' });
  m.putClosed.mutateAsync.mockResolvedValue({});
  m.deleteClosed.mutateAsync.mockResolvedValue(undefined);
  m.putMonth.mutateAsync.mockResolvedValue({});
  m.confirm.mutateAsync.mockResolvedValue({
    month: '2026-11-01',
    confirmed_at: '2026-10-21T01:00:00Z',
    confirmed_staff_count: 4,
    notified_count: 3,
    pending_remaining: 4,
    short_staff: [],
    required_hours: 160,
  });
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  vi.mocked(toast.info).mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('材料', () => {
  it('試験の月は API のスキーマに合う', () => {
    expect(() => leaveMonthViewSchema.parse(makeView())).not.toThrow();
  });
});

describe('権限', () => {
  it('管理者でなければ表を出さず、月も読まない', () => {
    role = 'staff';
    render(<AdminLeavePage />);
    expect(screen.getByText('このページは管理者だけが使えます。')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(monthCalls.every((c) => c[1] === false)).toBe(true);
  });
});

describe('表の描画', () => {
  it('種類・申請中・型の休み・祝日・申N・足/カ・訪問の件数・取り消し・送信の状態', () => {
    render(<AdminLeavePage />);
    expect(monthCalls[0]).toEqual(['2026-11', true]);
    expect(cellEl(S1, '2026-11-10')).toHaveAttribute('data-leave', 'off');
    expect(cellEl(S1, '2026-11-10')).toHaveTextContent('休');
    expect(cellEl(S1, '2026-11-11')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('午前休・管理者が入れた'),
    );
    expect(cellEl(S1, '2026-11-11')).toHaveTextContent('足');
    expect(cellEl(S1, '2026-11-11')).toHaveTextContent('2件');
    expect(cellEl(S2, '2026-11-11')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('午後休・カイポケから取り込んだ'),
    );
    expect(cellEl(S2, '2026-11-11')).toHaveTextContent('カ');
    expect(cellEl(S2, '2026-11-13')).toHaveAttribute('data-leave', 'paid_off');
    expect(cellEl(S2, '2026-11-13')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('カイポケで要対応'),
    );
    expect(cellEl(S2, '2026-11-13')).toHaveTextContent('!');
    expect(cellEl(S1, '2026-11-10')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('カイポケへ未送信'),
    );
    expect(cellEl(S2, '2026-11-10')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('カイポケと照合前'),
    );
    // 申請中は「?」
    expect(cellEl(S1, '2026-11-12')).toHaveAttribute('data-pending', 'off');
    expect(cellEl(S1, '2026-11-12')).toHaveTextContent('休?');
    expect(cellEl(S2, '2026-11-12')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('午後休を申請中（変更のお願い）'),
    );
    // 型で勤務しない曜日 (土日) は灰色
    expect(cellEl(S1, '2026-11-14')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('勤務しない曜日'),
    );
    expect(cellEl(S1, '2026-11-14').className).toContain('bg-bg-muted');
    // 承認後に取り消し (斜線)
    expect(cellEl(S1, '2026-11-05')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('承認後に取り消し'),
    );
    expect(cellEl(S1, '2026-11-05').style.backgroundImage).toContain('repeating-linear-gradient');
    // 時間変更
    expect(cellEl(S1, '2026-11-06')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('時間変更 9:00–15:00'),
    );
    // 週が未作成の日は訪問の件数を出さない
    expect(cellEl(S3, '2026-11-16')).not.toHaveTextContent('件');
  });

  it('見出し: 祝日は赤と名前・申N・祝日と事業所の休みの名前の帯', () => {
    view = makeView({
      days: makeView().days.map((d) =>
        d.date === '2026-11-30' ? { ...d, closed_label: '試験の休業' } : d,
      ),
      closed_days: [
        {
          date: '2026-11-30',
          label: '試験の休業',
          created_by: null,
          created_at: '2026-10-01T00:00:00Z',
        },
      ],
    });
    render(<AdminLeavePage />);
    const head3 = document.querySelector('th[data-col="2026-11-03"]')!;
    expect(head3).toHaveAttribute('title', expect.stringContaining('文化の日'));
    // 「祝」は見た目だけ・読み上げは名前 (sr-only)
    expect(within(head3 as HTMLElement).getByText('文化の日')).toHaveClass('sr-only');
    expect(within(head3 as HTMLElement).getByText('祝')).toHaveAttribute('aria-hidden');
    expect(head3.querySelector('.text-red-600')).toHaveTextContent('3');
    const head12 = document.querySelector('th[data-col="2026-11-12"]')!;
    expect(head12).toHaveTextContent('申2');
    const head30 = document.querySelector('th[data-col="2026-11-30"]')!;
    expect(within(head30 as HTMLElement).getByText('事業所の休み 試験の休業')).toHaveClass(
      'sr-only',
    );
    const notes = screen.getByTestId('day-notes');
    expect(notes).toHaveTextContent('11/3（火） 文化の日');
    expect(notes).toHaveTextContent('11/23（月） 勤労感謝の日');
    expect(notes).toHaveTextContent('事業所の休み：11/30（月） 試験の休業');
  });

  it('その日休む人: 数と、2 人以上で黄色', () => {
    render(<AdminLeavePage />);
    const d10 = screen.getByTestId('off-count-2026-11-10');
    expect(d10).toHaveTextContent('2');
    expect(d10).toHaveAttribute('data-warn', 'true');
    expect(d10.className).toContain('bg-warning-bg');
    expect(within(d10).getByText('休み 2 人（重なっています）')).toHaveClass('sr-only');
    expect(screen.getByTestId('off-count-2026-11-11')).toHaveAttribute('data-warn', 'false');
    expect(screen.getByTestId('off-count-2026-11-11')).toHaveTextContent('');
  });

  it('右端: 休日数 / 必要休日数・あと N 日・通れば・有給・非常勤は対象外', () => {
    render(<AdminLeavePage />);
    const a = screen.getByTestId(`staff-summary-${S1}`);
    expect(a).toHaveTextContent('8 / 11');
    expect(a).toHaveTextContent('あと 3 日');
    expect(a).toHaveTextContent('申請中 1 件（通れば あと 2）');
    const b = screen.getByTestId(`staff-summary-${S2}`);
    expect(b).toHaveTextContent('✓ 足りています');
    expect(b).toHaveTextContent('有給 1 日');
    expect(screen.getByTestId(`staff-summary-${S3}`)).toHaveTextContent('対象外（非常勤）');
    expect(screen.getByTestId(`staff-summary-${S4}`)).toBeInTheDocument();
  });

  it('上: 必要休日数・勤務すべき時間数・休みの時間帯の訪問・照合の状態・Google ドライブ', () => {
    render(<AdminLeavePage />);
    const line = screen.getByTestId('status-line');
    expect(line).toHaveTextContent('必要休日数 11 日');
    expect(line).toHaveTextContent('勤務すべき時間数 未入力');
    expect(screen.getByTestId('visits-on-leave')).toHaveTextContent(
      '休みの時間帯に担当が付いている訪問 3 件',
    );
    expect(screen.getByTestId('compare-status')).toHaveTextContent(
      'まだカイポケと比べていません ・ 照合前の休み 5 件',
    );
    expect(screen.getByTestId('confirm-badge')).toHaveTextContent('未確定');
    expect(screen.getByTestId('drive-card')).toHaveTextContent('接続済み（保存先：勤務表）');
    expect(within(screen.getByTestId('drive-card')).getByRole('link')).toHaveAttribute(
      'href',
      '/settings/google-drive',
    );
  });

  it('確定の後に休みを変えたら知らせる・比べた後に変えたら比べ直し', () => {
    view = makeView({
      required_hours: 168,
      confirmation: {
        confirmed: true,
        confirmed_staff_count: 4,
        staff_count: 4,
        last_confirmed_at: '2026-10-20T01:00:00Z',
        changed_since_confirm: true,
      },
      compare: {
        ...makeView().compare,
        last_compared_at: '2026-10-19T03:00:00Z',
        diff_count: 2,
        changed_since_compare: true,
        baseline_pending_count: 0,
      },
    });
    render(<AdminLeavePage />);
    expect(screen.getByTestId('confirm-badge')).toHaveTextContent('✓ 確定済み');
    expect(screen.getByTestId('confirm-badge')).toHaveTextContent('確定の後に休みを変えました');
    expect(screen.getByTestId('compare-status')).toHaveTextContent('差分 2 件');
    expect(screen.getByTestId('compare-status')).toHaveTextContent('もう一度比べてください');
    expect(screen.getByTestId('status-line')).toHaveTextContent('勤務すべき時間数 168 時間');
  });

  it('月を切り替えると次の月を読む', () => {
    render(<AdminLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: '次の月' }));
    expect(monthCalls[monthCalls.length - 1]).toEqual(['2026-12', true]);
    expect(screen.getByText('2026年12月')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '前の月' }));
    fireEvent.click(screen.getByRole('button', { name: '前の月' }));
    expect(monthCalls[monthCalls.length - 1]).toEqual(['2026-10', true]);
  });
});

describe('押すと入るもの', () => {
  it('公休を選ぶと、押したマスにそのまま入る (続けて何マスでも)', async () => {
    render(<AdminLeavePage />);
    stamp('公休');
    expect(screen.getByTestId('stamp-hint')).toHaveTextContent('「公休」を選んでいます');
    fireEvent.click(cellEl(S1, '2026-11-16'));
    fireEvent.click(cellEl(S1, '2026-11-17'));
    fireEvent.click(cellEl(S2, '2026-11-18'));
    // 1 件ずつ順に送る (並行の POST を出さない)
    await waitFor(() => expect(m.setDay.mutateAsync).toHaveBeenCalledTimes(3));
    expect(m.setDay.mutateAsync).toHaveBeenNthCalledWith(1, {
      staff_id: S1,
      date: '2026-11-16',
      leave_type: 'off',
    });
    expect(m.setDay.mutateAsync).toHaveBeenNthCalledWith(3, {
      staff_id: S2,
      date: '2026-11-18',
      leave_type: 'off',
    });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('職員Aさん 11/16（月） を公休にしました', {
        description: '訪問 2 件（2 名）を「担当なし」へ移しました',
      }),
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('同じ種類のマスは何もしない・別の種類は種類を変える', () => {
    render(<AdminLeavePage />);
    stamp('公休');
    fireEvent.click(cellEl(S1, '2026-11-10'));
    expect(m.setDay.mutateAsync).not.toHaveBeenCalled();
    stamp('有給');
    fireEvent.click(cellEl(S1, '2026-11-10'));
    expect(m.setDay.mutateAsync).toHaveBeenCalledWith({
      staff_id: S1,
      date: '2026-11-10',
      leave_type: 'paid_off',
    });
  });

  it('申請中のマスは上書きせず、承認・却下の小窓を出す', async () => {
    render(<AdminLeavePage />);
    stamp('午前休');
    fireEvent.click(cellEl(S1, '2026-11-12'));
    expect(m.setDay.mutateAsync).not.toHaveBeenCalled();
    const pop = await popover();
    expect(pop).toHaveTextContent('本人からの申請');
  });

  it('型で勤務しない曜日には入れず、知らせる', () => {
    render(<AdminLeavePage />);
    stamp('公休');
    fireEvent.click(cellEl(S1, '2026-11-14'));
    expect(m.setDay.mutateAsync).not.toHaveBeenCalled();
    expect(toast.info).toHaveBeenCalledWith(
      '職員Aさんは土曜日がもともと勤務外です（休日に数えています）。休みを入れるときは「選んで操作」でマスの小窓から入れてください',
    );
  });

  it('取り消すを選ぶと休みのマスだけ取り消す', async () => {
    render(<AdminLeavePage />);
    stamp('取り消す');
    fireEvent.click(cellEl(S1, '2026-11-16'));
    expect(m.cancelDay.mutateAsync).not.toHaveBeenCalled();
    fireEvent.click(cellEl(S1, '2026-11-10'));
    expect(m.cancelDay.mutateAsync).toHaveBeenCalledWith({ staff_id: S1, date: '2026-11-10' });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('取り消しました（職員Aさん 11/10（火））', {
        description: '担当なしへ移した訪問 2 件は自動では戻りません',
      }),
    );
  });

  it('入れられなかったらバックエンドの detail をそのまま出す', async () => {
    m.setDay.mutateAsync.mockRejectedValue(
      new ApiError('API 409', 409, {
        detail: 'この日は申請中です。申請を承認か却下で決めてください',
      }),
    );
    render(<AdminLeavePage />);
    stamp('公休');
    fireEvent.click(cellEl(S1, '2026-11-16'));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'この日は申請中です。申請を承認か却下で決めてください',
      ),
    );
  });
});

describe('マスの小窓', () => {
  it('登録済み: 種類を変える・取り消す (「元に戻す」は出さない)', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S1, '2026-11-11'));
    let pop = await popover();
    expect(pop).toHaveTextContent('職員Aさん ・ 11/11（水）');
    expect(pop).toHaveTextContent('管理者が入れた休み');
    expect(pop).toHaveTextContent('午前休（13:00 から勤務）');
    expect(within(pop).getByRole('button', { name: /^午前休/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(pop).queryByText(/元に戻す/)).not.toBeInTheDocument();
    fireEvent.click(within(pop).getByRole('button', { name: /^有給/ }));
    expect(m.setDay.mutateAsync).toHaveBeenCalledWith({
      staff_id: S1,
      date: '2026-11-11',
      leave_type: 'paid_off',
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(cellEl(S1, '2026-11-11'));
    pop = await popover();
    fireEvent.click(within(pop).getByRole('button', { name: '休みを取り消す' }));
    expect(m.cancelDay.mutateAsync).toHaveBeenCalledWith({ staff_id: S1, date: '2026-11-11' });
    expect(screen.queryByText(/元に戻す/)).not.toBeInTheDocument();
  });

  it('出勤の日: 時間変更は消えますと出し、休みを入れる', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S1, '2026-11-06'));
    const pop = await popover();
    expect(pop).toHaveTextContent('出勤の予定 ・ この日の訪問 4 件');
    expect(pop).toHaveTextContent('時間変更 9:00–15:00は、休みを入れると消えます');
    fireEvent.click(within(pop).getByRole('button', { name: /^午後休/ }));
    expect(m.setDay.mutateAsync).toHaveBeenCalledWith({
      staff_id: S1,
      date: '2026-11-06',
      leave_type: 'pm_off',
    });
  });

  it('勤務しない曜日にも小窓から休みを入れられる・週が未作成の日はそう出す', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S1, '2026-11-14'));
    let pop = await popover();
    expect(pop).toHaveTextContent('もともと勤務しない曜日です');
    expect(pop).toHaveTextContent('カイポケへは営業時間の休みとして送ります');
    fireEvent.click(within(pop).getByRole('button', { name: /^有給/ }));
    expect(m.setDay.mutateAsync).toHaveBeenCalledWith({
      staff_id: S1,
      date: '2026-11-14',
      leave_type: 'paid_off',
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(cellEl(S1, '2026-11-14')).not.toBeDisabled());
    fireEvent.click(cellEl(S1, '2026-11-15'));
    pop = await popover();
    fireEvent.click(within(pop).getByRole('button', { name: '閉じる' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(cellEl(S1, '2026-11-18'));
    pop = await popover();
    expect(pop).toHaveTextContent('この週はまだ作っていません');
  });

  it('今日より前の日は「訪問は動かしません（休みの記録だけ）」', async () => {
    view = makeView({ today: '2026-11-20' });
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S1, '2026-11-10'));
    const pop = await popover();
    expect(pop).toHaveTextContent('訪問は動かしません（休みの記録だけ）');
  });

  it('申請: 移す訪問・その日休む人・ほかの申請も通すと・承認', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S1, '2026-11-12'));
    const pop = await popover();
    expect(pop).toHaveTextContent('本人からの申請');
    expect(pop).toHaveTextContent('メモ：用事');
    expect(pop).toHaveTextContent('3 件を「担当なし」へ移します');
    expect(within(pop).getByTestId('pending-off-count')).toHaveTextContent(
      'この日休む人：今 0 人 → この申請を承認すると 1 人（この日のほかの申請 1 件も通すと 2 人）',
    );
    fireEvent.click(within(pop).getByRole('button', { name: '承認する' }));
    expect(m.approveOne.mutateAsync).toHaveBeenCalledWith({ id: P1, override_type: null });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('承認しました（職員Aさん 11/12（木） 公休）', {
        description: '訪問 3 件（3 名）を「担当なし」へ移しました。本人へ通知しました',
      }),
    );
  });

  it('申請: 種類を変えて承認する', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S1, '2026-11-12'));
    const pop = await popover();
    fireEvent.click(within(pop).getByRole('button', { name: /^有給/ }));
    fireEvent.click(within(pop).getByRole('button', { name: '有給で承認する' }));
    expect(m.approveOne.mutateAsync).toHaveBeenCalledWith({ id: P1, override_type: 'paid_off' });
  });

  it('申請: 承認でその日の休みが 2 人以上なら黄色', async () => {
    view = makeView({
      pending: makeView().pending.map((p) =>
        p.id === P1 ? { ...p, off_count_now: 1, off_count_if_approved: 2 } : p,
      ),
    });
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S1, '2026-11-12'));
    const pop = await popover();
    expect(within(pop).getByTestId('pending-off-count').className).toContain('bg-warning-bg');
  });

  it('申請: 却下は理由が要る', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S2, '2026-11-12'));
    const pop = await popover();
    expect(pop).toHaveTextContent('変更のお願い');
    fireEvent.click(within(pop).getByRole('button', { name: '却下…' }));
    const go = within(pop).getByRole('button', { name: '却下して通知' });
    expect(go).toBeDisabled();
    fireEvent.change(within(pop).getByLabelText(/却下の理由/), { target: { value: '   ' } });
    expect(go).toBeDisabled();
    fireEvent.change(within(pop).getByLabelText(/却下の理由/), {
      target: { value: 'その日は人手が足りないため' },
    });
    fireEvent.click(go);
    expect(m.reject.mutateAsync).toHaveBeenCalledWith({
      id: P2,
      reason: 'その日は人手が足りないため',
    });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        '却下して本人へ通知しました（職員Bさん 11/12（木））',
      ),
    );
  });

  it('休みの取り消しのお願いは種類を選ばない', async () => {
    view = makeView({
      pending: makeView().pending.map((p) =>
        p.id === P2 ? { ...p, override_type: 'cancel', current_leave_type: 'off' } : p,
      ),
    });
    render(<AdminLeavePage />);
    fireEvent.click(cellEl(S2, '2026-11-12'));
    const pop = await popover();
    expect(pop).toHaveTextContent('休みの取り消し');
    expect(pop).toHaveTextContent('いまの登録：公休');
    expect(within(pop).queryByRole('group', { name: '休みの種類' })).not.toBeInTheDocument();
  });
});

describe('申請中の一覧', () => {
  it('日付順: 日ごとに「休む人 今 N → 全部承認すると N」とまとめて承認', async () => {
    render(<AdminLeavePage />);
    const panel = screen.getByRole('complementary', { name: '申請中の休み' });
    const day12 = within(panel).getByRole('region', { name: '11/12（木）' });
    expect(day12).toHaveTextContent(
      '申請 2 件 ・ 休む人 0 人 → 全部承認すると 2 人（休みが重なります）',
    );
    expect(day12).toHaveTextContent('訪問 3 件を担当なしへ');
    expect(day12).toHaveTextContent('変更のお願い');
    fireEvent.click(within(day12).getByRole('button', { name: 'この日をまとめて承認' }));
    expect(m.approveBatch.mutateAsync).toHaveBeenCalledWith([P1, P2]);
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    // 1 件だけの日はまとめて承認を出さない
    const day16 = within(panel).getByRole('region', { name: '11/16（月）' });
    expect(within(day16).queryByRole('button', { name: /まとめて承認/ })).not.toBeInTheDocument();
    expect(day16).toHaveTextContent('週は未作成');
  });

  it('職員順: 人ごとに休日数とまとめて承認', async () => {
    render(<AdminLeavePage />);
    const panel = screen.getByRole('complementary', { name: '申請中の休み' });
    fireEvent.click(within(panel).getByRole('button', { name: '職員順' }));
    const a = within(panel).getByRole('region', { name: '職員A' });
    expect(a).toHaveTextContent('休日 8 / 11 ・ あと 3 日');
    const c = within(panel).getByRole('region', { name: '職員C' });
    expect(c).toHaveTextContent('非常勤（対象外）');
    fireEvent.click(within(c).getByRole('button', { name: 'この人をまとめて承認' }));
    expect(m.approveBatch.mutateAsync).toHaveBeenCalledWith([P3, P4]);
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it('1 件ずつ承認・「…」で表のマスの小窓を開く', async () => {
    render(<AdminLeavePage />);
    const panel = screen.getByRole('complementary', { name: '申請中の休み' });
    fireEvent.click(within(panel).getByRole('button', { name: '職員C 有給 を承認' }));
    expect(m.approveOne.mutateAsync).toHaveBeenCalledWith({ id: P3, override_type: null });
    fireEvent.click(
      within(panel).getByRole('button', {
        name: '職員A 公休 を表で開く（種類を変えて承認・却下）',
      }),
    );
    const pop = await popover();
    expect(pop).toHaveTextContent('職員Aさん ・ 11/12（木）');
  });

  it('行に指を置くと表のマスと列が光る・目立たせるとほかが薄くなる', () => {
    render(<AdminLeavePage />);
    const row = document.querySelector<HTMLElement>(`[data-request="${P1}"]`)!;
    fireEvent.mouseEnter(row);
    expect(cellEl(S1, '2026-11-12').className).toContain('ring-brand-primary-hover');
    expect(document.querySelector('th[data-col="2026-11-12"]')!.className).toContain(
      'bg-brand-primary-50',
    );
    fireEvent.mouseLeave(row);
    expect(cellEl(S1, '2026-11-12').className).not.toContain('ring-brand-primary-hover');

    fireEvent.click(screen.getByLabelText('表で申請中を目立たせる（ほかを薄く）'));
    expect(cellEl(S1, '2026-11-10').className).toContain('opacity-30');
    expect(cellEl(S1, '2026-11-12').className).not.toContain('opacity-30');
  });

  it('まとめて承認のダイアログ: 外したものは残す', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: /申請をまとめて承認/ }));
    const dlg = await screen.findByRole('dialog', { name: '申請をまとめて承認' });
    fireEvent.click(within(dlg).getByLabelText(/職員B/));
    fireEvent.click(within(dlg).getByRole('button', { name: 'チェックした申請を承認（3 件）' }));
    expect(m.approveBatch.mutateAsync).toHaveBeenCalledWith([P1, P3, P4]);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('2 件を承認し、本人へ通知しました', {
        description: '訪問 4 件を「担当なし」へ移しました（通知を見てください）',
      }),
    );
  });

  it('まとめて承認が失敗したら detail の message を出す', async () => {
    m.approveBatch.mutateAsync.mockRejectedValue(
      new ApiError('API 409', 409, {
        detail: { request_id: P1, message: 'この申請は承認済みです' },
      }),
    );
    render(<AdminLeavePage />);
    const panel = screen.getByRole('complementary', { name: '申請中の休み' });
    fireEvent.click(within(panel).getByRole('button', { name: 'この日をまとめて承認' }));
    // どの申請で止まったかを職員名と日付で出す
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        '職員Aさん 11/12（木）：この申請は承認済みです',
        expect.anything(),
      ),
    );
  });

  it('申請中が無ければ確定へ進めると出す', () => {
    view = makeView({ pending: [], cells: makeView().cells.filter((c) => !c.pending) });
    render(<AdminLeavePage />);
    expect(
      screen.getByText('申請中の休みはありません。「この月を確定して通知」へ進めます。'),
    ).toBeInTheDocument();
  });
});

describe('確定のダイアログ', () => {
  it('申請中の残り・足りない人・勤務すべき時間数 (前の月の値) を出して確定する', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: 'この月を確定して通知' }));
    const dlg = await screen.findByRole('dialog', { name: '2026年11月を確定して通知' });
    expect(dlg).toHaveTextContent('申請中が 4 件残っています');
    expect(dlg).toHaveTextContent('休日が足りない人：職員A（あと 3 日）');
    expect(dlg).toHaveTextContent('まだカイポケと比べていないので、休みはカイポケへ送りません');
    const hours = within(dlg).getByLabelText('常勤の従業者が勤務すべき時間数（時間）');
    expect(hours).toHaveValue(160);
    expect(dlg).toHaveTextContent('前の月の値（160 時間）');
    fireEvent.click(within(dlg).getByRole('button', { name: 'このまま確定する' }));
    await waitFor(() => expect(m.confirm.mutateAsync).toHaveBeenCalledWith('2026-11'));
    expect(m.putMonth.mutateAsync).toHaveBeenCalledWith({ month: '2026-11', required_hours: 160 });
    expect(m.putMonth.mutateAsync.mock.invocationCallOrder[0]).toBeLessThan(
      m.confirm.mutateAsync.mock.invocationCallOrder[0]!,
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('2026年11月を確定し、3 人へ通知しました', {
        description: '申請中 4 件は申請中のままです',
      }),
    );
  });

  it('何も残っていなければ「確定して通知」・時間数が同じなら保存しない・範囲外は押せない', async () => {
    view = makeView({
      pending: [],
      cells: [],
      required_hours: 168,
      staff: makeView().staff.map((s) => ({ ...s, shortfall: s.is_full_time ? 0 : null })),
      compare: { ...makeView().compare, last_compared_at: '2026-10-19T03:00:00Z' },
    });
    render(<AdminLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: 'この月を確定して通知' }));
    const dlg = await screen.findByRole('dialog', { name: '2026年11月を確定して通知' });
    expect(dlg).toHaveTextContent('申請はすべて処理済みで、全員の休日数が足りています。');
    expect(dlg).not.toHaveTextContent('まだカイポケと比べていない');
    const hours = within(dlg).getByLabelText('常勤の従業者が勤務すべき時間数（時間）');
    fireEvent.change(hours, { target: { value: '999' } });
    expect(within(dlg).getByRole('button', { name: '確定して通知' })).toBeDisabled();
    fireEvent.change(hours, { target: { value: '168' } });
    fireEvent.click(within(dlg).getByRole('button', { name: '確定して通知' }));
    await waitFor(() => expect(m.confirm.mutateAsync).toHaveBeenCalledWith('2026-11'));
    expect(m.putMonth.mutateAsync).not.toHaveBeenCalled();
  });
});

describe('事業所の休み', () => {
  const closed = {
    days: makeView().days.map((d) =>
      d.date === '2026-11-30' ? { ...d, closed_label: '試験の休業' } : d,
    ),
    closed_days: [
      {
        date: '2026-11-30',
        label: '試験の休業',
        created_by: null,
        created_at: '2026-10-01T00:00:00Z',
      },
    ],
  };

  it('入れる・消す', async () => {
    view = makeView(closed);
    render(<AdminLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: '事業所の休み' }));
    const dlg = await screen.findByRole('dialog', { name: '2026年11月の事業所の休み' });
    fireEvent.change(within(dlg).getByLabelText('事業所の休みの日付'), {
      target: { value: '2026-11-27' },
    });
    fireEvent.change(within(dlg).getByLabelText('事業所の休みの名前'), {
      target: { value: '試験の休み' },
    });
    fireEvent.click(within(dlg).getByRole('button', { name: '入れる' }));
    expect(m.putClosed.mutateAsync).toHaveBeenCalledWith({
      date: '2026-11-27',
      label: '試験の休み',
    });
    fireEvent.click(within(dlg).getByRole('button', { name: '11/30（月） の事業所の休みを消す' }));
    expect(m.deleteClosed.mutateAsync).toHaveBeenCalledWith('2026-11-30');
  });

  it('月の外の日付は入れられない', async () => {
    render(<AdminLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: '事業所の休み' }));
    const dlg = await screen.findByRole('dialog', { name: '2026年11月の事業所の休み' });
    fireEvent.change(within(dlg).getByLabelText('事業所の休みの日付'), {
      target: { value: '2026-12-01' },
    });
    fireEvent.change(within(dlg).getByLabelText('事業所の休みの名前'), { target: { value: 'x' } });
    expect(within(dlg).getByRole('button', { name: '入れる' })).toBeDisabled();
  });

  it('全員に公休を入れる: 常勤・在籍・勤務する曜日の人だけ (新人は含む)・1 回の bulk で', async () => {
    view = makeView(closed);
    render(<AdminLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: '事業所の休み' }));
    const dlg = await screen.findByRole('dialog', { name: '2026年11月の事業所の休み' });
    fireEvent.click(within(dlg).getByRole('button', { name: '全員に公休を入れる' }));
    // 職員C (非常勤) は除く・職員D (新人) は含む
    expect(dlg).toHaveTextContent(
      '3 人に公休を入れます（勤務しない曜日の人・非常勤の人・休職中の人・申請中の人・休みが入っている人は除きます）',
    );
    await act(async () => {
      fireEvent.click(within(dlg).getAllByRole('button', { name: '入れる' })[0]!);
    });
    await waitFor(() =>
      expect(m.setDayBulk.mutateAsync).toHaveBeenCalledWith({
        date: '2026-11-30',
        override_type: 'off',
      }),
    );
    expect(m.setDayBulk.mutateAsync).toHaveBeenCalledTimes(1);
    expect(m.setDay.mutateAsync).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('11/30（月） に 3 人の公休を入れました', {
        description: '訪問 5 件を「担当なし」へ移しました',
      }),
    );
  });

  it('全員に公休を入れる: 休職中の人は数えない・失敗したら全部やめると出す', async () => {
    view = makeView({
      ...closed,
      staff: makeView().staff.map((s) => (s.id === S2 ? { ...s, status: 'on_leave' } : s)),
    });
    m.setDayBulk.mutateAsync.mockRejectedValue(
      new ApiError('API 409', 409, { detail: 'この日は申請中の人がいます' }),
    );
    render(<AdminLeavePage />);
    fireEvent.click(screen.getByRole('button', { name: '事業所の休み' }));
    const dlg = await screen.findByRole('dialog', { name: '2026年11月の事業所の休み' });
    fireEvent.click(within(dlg).getByRole('button', { name: '全員に公休を入れる' }));
    expect(dlg).toHaveTextContent('2 人に公休を入れます');
    await act(async () => {
      fireEvent.click(within(dlg).getAllByRole('button', { name: '入れる' })[0]!);
    });
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('この日は申請中の人がいます', {
        description: '1 人でも入れられないと、全部やめます',
      }),
    );
  });
});

describe('続けて押す (連打)', () => {
  it('書き込みは 1 件ずつ順に・進行中のマスは押せず薄い・読み直しは落ち着いてから 1 回', async () => {
    let release: (() => void) | null = null;
    m.setDay.mutateAsync.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve(DAY_RESULT);
        }),
    );
    render(<AdminLeavePage />);
    stamp('公休');
    fireEvent.click(cellEl(S1, '2026-11-16'));
    fireEvent.click(cellEl(S1, '2026-11-16'));
    fireEvent.click(cellEl(S1, '2026-11-17'));
    // 並行の POST を出さない: 1 件目が終わるまで 2 件目は出ない
    expect(m.setDay.mutateAsync).toHaveBeenCalledTimes(1);
    expect(cellEl(S1, '2026-11-16')).toBeDisabled();
    expect(cellEl(S1, '2026-11-16')).toHaveAttribute('aria-busy', 'true');
    expect(cellEl(S1, '2026-11-16').className).toContain('opacity-40');
    expect(cellEl(S1, '2026-11-17')).toBeDisabled();
    await act(async () => release?.());
    await waitFor(() => expect(m.setDay.mutateAsync).toHaveBeenCalledTimes(2));
    expect(m.setDay.mutateAsync).toHaveBeenNthCalledWith(2, {
      staff_id: S1,
      date: '2026-11-17',
      leave_type: 'off',
    });
    const leaveReloads = () =>
      qcStub.invalidateQueries.mock.calls.filter(
        (c: unknown[]) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey) === '["leave"]',
      ).length;
    expect(leaveReloads()).toBe(0);
    await act(async () => release?.());
    await waitFor(() => expect(cellEl(S1, '2026-11-17')).not.toBeDisabled());
    expect(leaveReloads()).toBe(1);
    expect(cellEl(S1, '2026-11-16')).not.toBeDisabled();
  });
});

describe('キーボードと光り', () => {
  it('小窓を開くと最初のボタンへ焦点・閉じるとマスへ戻る・マスに aria-expanded', async () => {
    render(<AdminLeavePage />);
    const c = cellEl(S1, '2026-11-11');
    expect(c).toHaveAttribute('aria-expanded', 'false');
    expect(c).toHaveAttribute('aria-haspopup', 'dialog');
    fireEvent.click(c);
    const pop = await popover();
    expect(c).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(pop.contains(document.activeElement)).toBe(true));
    expect(document.activeElement?.tagName).toBe('BUTTON');
    fireEvent.click(within(pop).getByRole('button', { name: '閉じる' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(document.activeElement).toBe(cellEl(S1, '2026-11-11'));
  });

  it('一覧に指を置くと同じ列のマスも枠で光り、見えない所ならマスへ寄せる', () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(<AdminLeavePage />);
    fireEvent.mouseEnter(document.querySelector<HTMLElement>(`[data-request="${P1}"]`)!);
    expect(cellEl(S3, '2026-11-12').className).toContain('ring-brand-primary');
    expect(cellEl(S3, '2026-11-11').className).not.toContain('ring-1');
    expect(scroll).toHaveBeenCalledWith({ block: 'nearest', inline: 'nearest' });
  });

  it('承認したら光りを消す', async () => {
    render(<AdminLeavePage />);
    const row = document.querySelector<HTMLElement>(`[data-request="${P1}"]`)!;
    fireEvent.mouseEnter(row);
    fireEvent.click(within(row).getByRole('button', { name: /を承認$/ }));
    await waitFor(() =>
      expect(cellEl(S1, '2026-11-12').className).not.toContain('ring-brand-primary-hover'),
    );
  });
});

describe('勤務表の一覧', () => {
  it('その月の勤務表を予定版・確定版と版で出し、開ける', () => {
    kinmuSheets = [
      {
        id: 'bbbbbbb1-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        month: '2026-11-01',
        kind: 'plan',
        version: 2,
        drive_file_id: 'f',
        url: 'https://docs.google.com/spreadsheets/d/x',
        reason: null,
        created_by: null,
        created_at: '2026-10-20T01:00:00Z',
      },
    ];
    render(<AdminLeavePage />);
    const list = screen.getByTestId('kinmu-sheets');
    expect(list).toHaveTextContent('予定版 第2版');
    // 日時は JST (10/20 10:00)
    expect(list).toHaveTextContent('10/20 10:00');
    expect(within(list).getByRole('link', { name: /開く/ })).toHaveAttribute(
      'href',
      'https://docs.google.com/spreadsheets/d/x',
    );
  });

  it('無ければそう出す', () => {
    render(<AdminLeavePage />);
    expect(screen.getByTestId('kinmu-sheets')).toHaveTextContent(
      '2026年11月の勤務表はまだありません',
    );
  });
});

describe('ほかの入口から開く', () => {
  it('?staff=&date= でそのマスの小窓を開き、その月を読む', async () => {
    search = new URLSearchParams({ staff: S1, date: '2026-11-12' });
    render(<AdminLeavePage />);
    expect(monthCalls[0]).toEqual(['2026-11', true]);
    const pop = await popover();
    expect(pop).toHaveTextContent('職員Aさん ・ 11/12（木）');
    expect(pop).toHaveTextContent('本人からの申請');
  });

  it('?staff=&date= が変わったら、月と小窓もそれに合わせる', async () => {
    search = new URLSearchParams({ staff: S1, date: '2026-11-12' });
    const { rerender } = render(<AdminLeavePage />);
    expect(await popover()).toHaveTextContent('職員Aさん ・ 11/12（木）');
    search = new URLSearchParams({ staff: S2, date: '2026-11-11' });
    rerender(<AdminLeavePage />);
    await waitFor(() =>
      expect(screen.getByRole('dialog')).toHaveTextContent('職員Bさん ・ 11/11（水）'),
    );
    search = new URLSearchParams({ staff: S2, date: '2026-12-02' });
    rerender(<AdminLeavePage />);
    await waitFor(() => expect(monthCalls[monthCalls.length - 1]).toEqual(['2026-12', true]));
  });

  it('?date= の月で開く', () => {
    search = new URLSearchParams({ staff: S1, date: '2026-12-02' });
    render(<AdminLeavePage />);
    expect(monthCalls[0]).toEqual(['2026-12', true]);
  });

  it('?staff= だけならその人の行を光らせる', () => {
    search = new URLSearchParams({ staff: S2 });
    render(<AdminLeavePage />);
    const row = document.querySelector(`[data-staff-row="${S2}"] th`)!;
    expect(row.className).toContain('bg-brand-primary-50');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('?month= の月で開く', () => {
    search = new URLSearchParams({ month: '2027-01' });
    render(<AdminLeavePage />);
    expect(monthCalls[0]).toEqual(['2027-01', true]);
  });
});

describe('幅 (360px)', () => {
  it('ページは横に広がらず、表の中だけ横にスクロールする・名前の列は固定', () => {
    window.innerWidth = 360;
    window.dispatchEvent(new Event('resize'));
    render(<AdminLeavePage />);
    const page = screen.getByTestId('leave-page');
    expect(page.className).toContain('min-w-0');
    expect(page.className).toContain('max-w-full');
    const scroller = screen.getByTestId('leave-grid-scroll');
    expect(scroller.className).toContain('overflow-x-auto');
    expect(scroller.className).toContain('max-w-full');
    expect(scroller.querySelector('table')).not.toBeNull();
    // 表を包むカードは min-w-0 (グリッドの子が中身の幅に伸びない)
    expect(scroller.parentElement!.className).toContain('min-w-0');
    expect(scroller.parentElement!.parentElement!.className).toContain('min-w-0');
    // 名前の列は左に固定
    const nameCell = document.querySelector(`[data-staff-row="${S1}"] th`)!;
    expect(nameCell.className).toContain('sticky');
    expect(nameCell.className).toContain('left-0');
    window.innerWidth = 1024;
  });
});
