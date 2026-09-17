/**
 * `/records` 訪問記録一覧の vitest（設計 §11-2 / モック ⑤）。
 *
 * 縛る挙動:
 *   1. 絞り込みは **BE パラメータ**で行う（期間タブ・状態・確認済みがクエリに乗る）
 *   2. 行クリックで詳細ダイアログが開く
 *   3. 0 件は らく助 (think) の空状態
 *   4. ページング（50 件・total）で offset が動く
 *   5. `?patient=` で初期フィルタが決まる（期間で切らない）
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

const { mockUseVisitRecordings, mockSearchParams, mockRole, mockSessionStatus } = vi.hoisted(
  () => ({
    mockUseVisitRecordings: vi.fn(),
    mockSearchParams: { value: new Map<string, string>() },
    mockRole: { value: 'admin' as string },
    mockSessionStatus: { value: 'authenticated' as string },
  }),
);

vi.mock('next/navigation', () => ({
  useSearchParams: () => ({ get: (k: string) => mockSearchParams.value.get(k) ?? null }),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data:
      mockSessionStatus.value === 'loading'
        ? null
        : { user: { role: mockRole.value, staffId: 'st-1' }, accessToken: 'tok' },
    status: mockSessionStatus.value,
  }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

// 実モジュールを土台に、フックだけ差し替える（共有ファクトリ・レビュー H-1）。
// 一覧は内側で RecordDetailDialog を開くので、ダイアログが使うフックも要る。
vi.mock('@/lib/queries/visit-recordings', async (importOriginal) => {
  const { visitRecordingsMock } = await import(
    '@/components/records/__tests__/visitRecordingsMock'
  );
  return visitRecordingsMock(importOriginal, {
    useVisitRecordings: (...a: unknown[]) => mockUseVisitRecordings(...a),
  });
});

vi.mock('@/lib/queries/offices', () => ({
  useOffices: () => ({ offices: [{ id: 'of-1', name: '都賀' }], allOffices: [] }),
}));
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({ data: [{ id: 'st-1', name: '川名 幸子' }] }),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: [] }, isLoading: false }),
}));

import RecordsPage from '../page';

/** 2026-09-17 (木)。今週 = 9/14(月)〜9/20(日)。 */
const NOW = new Date(2026, 8, 17, 10, 0, 0);

// URL クエリは UUID しか受理しない (L-3)。
const PATIENT_UUID = '11111111-1111-1111-1111-111111111111';
const STAFF_UUID = '22222222-2222-2222-2222-222222222222';
const VISIT_UUID = '33333333-3333-3333-3333-333333333333';

function makeRecording(over: Record<string, unknown> = {}) {
  return {
    id: 'rec-1',
    visit_id: 'v-1',
    patient_id: 'p-1',
    patient_name: '山田 花子',
    staff_id: 'st-1',
    staff_name: '川名 幸子',
    office_id: 'of-1',
    office_name: '都賀',
    recorded_at: '2026-09-17T05:08:00Z',
    duration_sec: 1694,
    status: 'summarized',
    has_audio: true,
    summary_text: '「足が重い」とのご本人の訴え。\n顔色良好。',
    reviewed_at: null,
    ...over,
  };
}

/** 直近の `useVisitRecordings` 呼び出し引数。 */
function lastParams(): Record<string, unknown> {
  const calls = mockUseVisitRecordings.mock.calls;
  return (calls[calls.length - 1]?.[0] ?? {}) as Record<string, unknown>;
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  mockSearchParams.value = new Map();
  mockRole.value = 'admin';
  mockSessionStatus.value = 'authenticated';
  mockUseVisitRecordings.mockReset();
  mockUseVisitRecordings.mockReturnValue({
    data: { items: [makeRecording()], total: 1 },
    isLoading: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('RecordsPage', () => {
  it('開いた瞬間は「今週」— BE へ今週の from/to と新しい順で問い合わせる', () => {
    render(<RecordsPage />);
    const p = lastParams();
    expect(p.from).toBe('2026-09-14');
    expect(p.to).toBe('2026-09-20');
    expect(p.order).toBe('recorded_at_desc');
    expect(p.limit).toBe(50);
    expect(p.offset).toBe(0);
    expect(p.unscoped).toBe(true);
  });

  it('期間タブ「今月」で from/to が月初〜月末になる', () => {
    render(<RecordsPage />);
    fireEvent.click(screen.getByRole('tab', { name: '今月' }));
    const p = lastParams();
    expect(p.from).toBe('2026-09-01');
    expect(p.to).toBe('2026-09-30');
  });

  it('期間タブ「すべて」は期間を付けない', () => {
    render(<RecordsPage />);
    fireEvent.click(screen.getByRole('tab', { name: 'すべて' }));
    const p = lastParams();
    expect(p.from).toBeNull();
    expect(p.to).toBeNull();
  });

  it('状態・確認済み・スタッフ・拠点は BE パラメータに乗る', () => {
    render(<RecordsPage />);
    fireEvent.change(screen.getByLabelText('状態'), { target: { value: 'failed' } });
    expect(lastParams().status).toBe('failed');

    fireEvent.change(screen.getByLabelText('確認済み'), { target: { value: 'no' } });
    expect(lastParams().reviewed).toBe(false);

    fireEvent.change(screen.getByLabelText('スタッフ'), { target: { value: 'st-1' } });
    expect(lastParams().staffId).toBe('st-1');

    fireEvent.change(screen.getByLabelText('拠点'), { target: { value: 'of-1' } });
    expect(lastParams().officeId).toBe('of-1');
  });

  it('検索は 300ms デバウンスして q に乗る', () => {
    render(<RecordsPage />);
    fireEvent.change(screen.getByLabelText('訪問記録を検索'), { target: { value: '浮腫' } });
    expect(lastParams().q).toBeNull();
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(lastParams().q).toBe('浮腫');
  });

  it('1 文字の間は q を送らず「2 文字以上」を案内する (M-1)', () => {
    render(<RecordsPage />);
    fireEvent.change(screen.getByLabelText('訪問記録を検索'), { target: { value: '足' } });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(lastParams().q).toBeNull();
    expect(screen.getByTestId('records-search-hint')).toHaveTextContent('2 文字以上');

    // 2 文字目で送られ、案内は消える。
    fireEvent.change(screen.getByLabelText('訪問記録を検索'), { target: { value: '足が' } });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(lastParams().q).toBe('足が');
    expect(screen.queryByTestId('records-search-hint')).not.toBeInTheDocument();
  });

  it('検索欄は 100 文字で切る (M-1)', () => {
    render(<RecordsPage />);
    const box = screen.getByLabelText('訪問記録を検索') as HTMLInputElement;
    fireEvent.change(box, { target: { value: 'あ'.repeat(150) } });
    expect(box.value).toHaveLength(100);
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(String(lastParams().q)).toHaveLength(100);
  });

  it('読み込み失敗は detail 付きで出し、表は描かない (M-2)', () => {
    mockUseVisitRecordings.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('BE が落ちています'),
    });
    render(<RecordsPage />);
    expect(screen.getByTestId('records-error')).toHaveTextContent('BE が落ちています');
    expect(screen.queryByTestId('records-table')).not.toBeInTheDocument();
    expect(screen.queryByTestId('records-empty')).not.toBeInTheDocument();
  });

  it('staff ロールはスタッフ / 拠点セレクトが disabled (L-2)', () => {
    mockRole.value = 'staff';
    render(<RecordsPage />);
    expect(screen.getByLabelText('スタッフ')).toBeDisabled();
    expect(screen.getByLabelText('拠点')).toBeDisabled();
    expect(screen.getByLabelText('スタッフ')).toHaveAttribute(
      'title',
      '自分の記録のみ表示されます',
    );
    // 患者・状態・検索は staff でも使える。
    expect(screen.getByLabelText('状態')).not.toBeDisabled();
  });

  it('セッション取得中は staffScoped 判定を保留する (N-3)', () => {
    // role が未定の一瞬で管理者にセレクトを無効化して見せない。
    mockSessionStatus.value = 'loading';
    render(<RecordsPage />);
    expect(screen.getByLabelText('スタッフ')).not.toBeDisabled();
    expect(screen.getByLabelText('拠点')).not.toBeDisabled();
  });

  it('UUID でない ?patient / ?staff / ?visit は無視する (L-3)', () => {
    mockSearchParams.value = new Map([
      ['patient', "'; drop"],
      ['staff', '123'],
      ['visit', 'not-a-uuid'],
    ]);
    render(<RecordsPage />);
    const p = lastParams();
    expect(p.patientId).toBeNull();
    expect(p.staffId).toBeNull();
    expect(p.visitId).toBeNull();
    // 名指しが 1 つも無い = 既定の「今週」に戻る。
    expect(p.from).toBe('2026-09-14');
  });

  it('行クリックで詳細ダイアログが開く', () => {
    render(<RecordsPage />);
    expect(screen.queryByTestId('record-detail-dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('records-row-rec-1'));
    expect(screen.getByTestId('record-detail-dialog')).toBeInTheDocument();
  });

  it('要約の 1 行目だけを一覧に出す', () => {
    render(<RecordsPage />);
    expect(screen.getByText('「足が重い」とのご本人の訴え。')).toBeInTheDocument();
    expect(screen.queryByText('顔色良好。')).not.toBeInTheDocument();
  });

  it('0 件は空状態（らく助）を出し、表は描かない', () => {
    mockUseVisitRecordings.mockReturnValue({ data: { items: [], total: 0 }, isLoading: false });
    render(<RecordsPage />);
    expect(screen.getByTestId('records-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('records-table')).not.toBeInTheDocument();
  });

  it('50 件を超えるときだけページャを出し、「次へ」で offset が動く', () => {
    mockUseVisitRecordings.mockReturnValue({
      data: { items: [makeRecording()], total: 120 },
      isLoading: false,
    });
    render(<RecordsPage />);
    expect(screen.getByTestId('records-pager')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '次へ' }));
    expect(lastParams().offset).toBe(50);
  });

  it('?patient= で初期フィルタが決まり、期間では切らない', () => {
    mockSearchParams.value = new Map([['patient', PATIENT_UUID]]);
    render(<RecordsPage />);
    const p = lastParams();
    expect(p.patientId).toBe(PATIENT_UUID);
    expect(p.from).toBeNull();
    expect(p.to).toBeNull();
  });

  it('?staff= で初期フィルタが決まる', () => {
    mockSearchParams.value = new Map([['staff', STAFF_UUID]]);
    render(<RecordsPage />);
    expect(lastParams().staffId).toBe(STAFF_UUID);
  });

  it('?visit= は訪問で絞り、1 件目の詳細を自動で開く', () => {
    mockSearchParams.value = new Map([['visit', VISIT_UUID]]);
    render(<RecordsPage />);
    expect(lastParams().visitId).toBe(VISIT_UUID);
    expect(screen.getByTestId('record-detail-dialog')).toBeInTheDocument();
  });
});
