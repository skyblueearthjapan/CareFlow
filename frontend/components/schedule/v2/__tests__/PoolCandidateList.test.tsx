/**
 * PoolCandidateList — ③ 単体MVP テスト.
 *
 * 検証:
 *   1. 初期は「他の空き枠も見る」ボタンのみ. 押すと propose-slots を当該患者で呼ぶ
 *      (existing_patient_id / office_ids / 希望条件が乗る).
 *   2. 候補スロットがランキング表示される.
 *   3. 候補の「この枠で採用」→ 確認 → fixed-visits マージ確定 (PUT) が、 採用枠を含む
 *      body で呼ばれる (他曜日保持の確定経路).
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mocks: {
    proposeMutate: vi.fn(),
    proposeData: undefined as unknown,
    confirmMutate: vi.fn(),
    placeAndFixMutate: vi.fn(),
    existingFixedVisits: [] as unknown[],
    // course-templates 並列取得。空 = resolver が null を返す (A 経路では許容)。
    // B 経路テストでは有効なテンプレートを設定してから呼ぶこと。
    templatesQueries: [] as unknown[],
    // W-12d: 詰まり解消相談 (propose-unblock) の探索/適用 mutation。
    unblockMutate: vi.fn(),
    unblockData: undefined as unknown,
    unblockPending: false,
    unblockApplyMutate: vi.fn(),
    unblockApplyPending: false,
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('lucide-react', () => ({
  AlertTriangle: () => <span />,
  CheckCircle2: () => <span />,
  Lightbulb: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  Plus: () => <span />,
  Sparkles: () => <span />,
  X: () => <span />,
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));

vi.mock('@tanstack/react-query', () => ({
  // course-templates 並列取得は動的 mock (B 経路テストでのみ有効データを設定).
  useQueries: () => mocks.templatesQueries,
}));

vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));

// W-12d: ApiError を実クラスで提供 (409 判定 `err instanceof ApiError` を成立させる)。
vi.mock('@/lib/api-client', () => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(message: string, status: number, body: unknown) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.body = body;
    }
  }
  return { ApiError };
});

vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({
    checked,
    onCheckedChange,
    ...rest
  }: {
    checked?: boolean;
    onCheckedChange?: (v: boolean) => void;
    [k: string]: unknown;
  }) => (
    <input
      type="checkbox"
      checked={checked}
      onChange={(e) => onCheckedChange?.(e.target.checked)}
      {...rest}
    />
  ),
}));

vi.mock('@/lib/queries/unblock', () => ({
  useProposeUnblockMutation: () => ({
    mutate: mocks.unblockMutate,
    reset: vi.fn(),
    isPending: mocks.unblockPending,
    data: mocks.unblockData,
  }),
  useUnblockApplyMutation: () => ({
    mutate: mocks.unblockApplyMutate,
    isPending: mocks.unblockApplyPending,
  }),
}));

vi.mock('@/lib/queries/place_and_fix', () => ({
  usePlaceAndFix: () => ({ mutate: mocks.placeAndFixMutate, isPending: false }),
}));

vi.mock('@/components/ui/alert', () => ({
  Alert: ({ children, ...rest }: React.HTMLAttributes<HTMLDivElement>) => (
    <div {...rest}>{children}</div>
  ),
  AlertTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children, ...rest }: React.HTMLAttributes<HTMLSpanElement>) => (
    <span {...rest}>{children}</span>
  ),
}));
vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    ...rest
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} disabled={disabled} {...rest}>
      {children}
    </button>
  ),
}));

vi.mock('@/lib/queries/fieldBoard', () => ({
  useProposeSlots: () => ({
    mutate: mocks.proposeMutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    data: mocks.proposeData,
  }),
  proposeWarningLabel: (code: string) => code,
}));

vi.mock('@/lib/queries/propose_confirm', () => ({
  useConfirmFixedVisits: () => ({ mutate: mocks.confirmMutate, isPending: false }),
}));

vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useFixedVisits: () => ({
    data: mocks.existingFixedVisits,
    isLoading: false,
    isFetching: false,
    isError: false,
  }),
  toastFixedVisitWarnings: (warnings?: Array<{ message: string }>) => {
    // 実挙動 (最初の3件 + 他N件) を模して sonner.toast.warning を呼ぶ.
    if (!warnings || warnings.length === 0) return;
    const shown = warnings.slice(0, 3);
    for (const w of shown) mockToast.warning(w.message);
    if (warnings.length > shown.length)
      mockToast.warning(`他 ${warnings.length - shown.length} 件の警告があります`);
  },
}));

vi.mock('@/lib/schemas/patient', () => ({
  coerceWeeklyPattern: () => ({
    frequency_per_week: 1,
    visit_frequency: null,
    preferred_weekdays: ['Tue'],
    service_minutes: 35,
    time_type: '固定',
    preferred_start: '16:00',
    preferred_end: null,
  }),
}));

import { ApiError } from '@/lib/api-client';

import { PoolCandidateList } from '../PoolCandidateList';

const PATIENT = {
  id: '22222222-2222-4222-8222-222222222222',
  name: '中尾 要太',
  address: '千葉県千葉市花見川区検見川町1-52',
  lat: 35.66,
  lng: 140.12,
  sex_restriction: null,
  weekly_pattern: {},
} as unknown as Parameters<typeof PoolCandidateList>[0]['patient'];

function makeSlot(over: Record<string, unknown> = {}) {
  return {
    office_id: '11111111-1111-4111-8111-111111111111',
    office_name: '稲毛',
    weekday: 1, // 火
    weekday_code: 'Tue',
    course_code: 'C',
    course_label: '稲C',
    staff_name: '山田',
    start_time: '14:00:00',
    end_time: '14:35:00',
    score: 80,
    reasons: ['近接'],
    warnings: [],
    is_pair: false,
    pair_partner: null,
    mini_schedule: [],
    ...over,
  };
}

const COMMON = {
  patient: PATIENT,
  isoYear: 2026,
  isoWeek: 24,
  officeId: '11111111-1111-4111-8111-111111111111',
  canEdit: true,
};

describe('PoolCandidateList (③ 単体MVP)', () => {
  beforeEach(() => {
    mocks.proposeMutate.mockReset();
    mocks.confirmMutate.mockReset();
    mocks.placeAndFixMutate.mockReset();
    mocks.proposeData = undefined;
    mocks.existingFixedVisits = [];
    mocks.templatesQueries = [];
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
  });

  it('初期はボタンのみ. 押すと当該患者で propose-slots を呼ぶ', () => {
    render(<PoolCandidateList {...COMMON} />);
    const btn = screen.getByTestId('pool-candidate-run-button');
    fireEvent.click(btn);
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
    const req = mocks.proposeMutate.mock.calls[0][0];
    expect(req.existing_patient_id).toBe(PATIENT.id);
    expect(req.office_ids).toEqual([COMMON.officeId]);
    expect(req.iso_year).toBe(2026);
    expect(req.iso_week).toBe(24);
    // 固定希望なので preferred_start が乗る.
    expect(req.preferred_start).toBe('16:00');
  });

  it('primary (主提案) は開いた時点で自動的に propose-slots を呼ぶ (ボタン不要)', () => {
    render(<PoolCandidateList {...COMMON} primary />);
    // on-demand ボタンは出ない (自動実行).
    expect(screen.queryByTestId('pool-candidate-run-button')).not.toBeInTheDocument();
    // マウント時に当該患者で propose-slots を呼ぶ.
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
    expect(mocks.proposeMutate.mock.calls[0][0].existing_patient_id).toBe(PATIENT.id);
    // 主提案モードの見出し.
    expect(screen.getByText('空き枠の候補')).toBeInTheDocument();
  });

  it('候補スロットがランキング表示される (コース当日スケジュール + 挿入位置を含む)', () => {
    mocks.proposeData = {
      slots: [
        makeSlot({
          mini_schedule: [
            {
              time: '13:00',
              name: '既存A',
              ins: null,
              is_here: false,
              is_pair: false,
              sex_restriction: 'female_only',
              is_multi_staff: false,
            },
            {
              time: '14:00',
              name: '(提案)',
              ins: null,
              is_here: true,
              is_pair: false,
              sex_restriction: null,
              is_multi_staff: true,
            },
          ],
        }),
      ],
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.getByTestId('pool-candidate-slots')).toBeInTheDocument();
    // 稲C は候補ヘッダ + ミニスケジュール見出しの 2 箇所に出る.
    expect(screen.getAllByText(/稲C/).length).toBeGreaterThan(0);
    expect(screen.getByText(/担当: 山田/)).toBeInTheDocument();
    // コース当日の既存訪問 + 「ここに入れますか」挿入位置が見える.
    expect(screen.getByText('既存A')).toBeInTheDocument();
    expect(screen.getByText('ここに入れますか')).toBeInTheDocument();
    // 通常リストと同じ色分け: 性別制限・2名体制マーカーが出る.
    expect(screen.getByText('👩女性のみ')).toBeInTheDocument();
    expect(screen.getByText('複数')).toBeInTheDocument();
  });

  it('採用 → 確認 → fixed-visits マージ確定が採用枠を含む body で呼ばれる', async () => {
    const slot = makeSlot();
    mocks.proposeData = { slots: [slot], message: null };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));

    const adoptBtn = screen.getByTestId(
      `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
    );
    fireEvent.click(adoptBtn);
    // 確認バーが出る.
    expect(screen.getByTestId('pool-candidate-confirm')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const arg = mocks.confirmMutate.mock.calls[0][0];
    expect(arg.patientId).toBe(PATIENT.id);
    expect(arg.body.mode).toBe('normal');
    // 既存枠なし → 採用枠 1 件のみ.
    expect(arg.body.items).toHaveLength(1);
    expect(arg.body.items[0].weekday).toBe(1);
    expect(arg.body.items[0].start_time).toBe('14:00:00');
    expect(arg.body.items[0].duration_min).toBe(35); // 14:00-14:35.
  });

  it('採用時、他曜日/他slotの既存固定枠は保持され、採用曜日のslot0のみ置換される', async () => {
    const slot = makeSlot(); // 火(weekday=1) slot0 を採用.
    mocks.proposeData = { slots: [slot], message: null };
    // 既存: 月(0,slot0) / 木(3,slot0) / 火(1,slot1=2名体制相方) / 火(1,slot0=置換対象).
    mocks.existingFixedVisits = [
      { weekday: 0, start_time: '09:00:00', duration_min: 30, slot_index: 0 },
      { weekday: 3, start_time: '10:00:00', duration_min: 60, slot_index: 0 },
      { weekday: 1, start_time: '08:00:00', duration_min: 30, slot_index: 1 },
      { weekday: 1, start_time: '08:00:00', duration_min: 30, slot_index: 0 },
    ];
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(
      screen.getByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    );
    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const items = mocks.confirmMutate.mock.calls[0][0].body.items as Array<{
      weekday: number;
      slot_index: number;
      start_time: string;
    }>;
    // 月(0)・木(3) は保持.
    expect(items.find((i) => i.weekday === 0 && i.slot_index === 0)).toBeTruthy();
    expect(items.find((i) => i.weekday === 3 && i.slot_index === 0)).toBeTruthy();
    // 火 slot1 (相方) は保持.
    expect(items.find((i) => i.weekday === 1 && i.slot_index === 1)).toBeTruthy();
    // 火 slot0 は採用枠で置換 (14:00, 既存 08:00 ではない).
    const tueSlot0 = items.filter((i) => i.weekday === 1 && i.slot_index === 0);
    expect(tueSlot0).toHaveLength(1);
    expect(tueSlot0[0]!.start_time).toBe('14:00:00');
    // 合計 4 件 (月/木/火slot1 保持 + 火slot0 置換).
    expect(items).toHaveLength(4);
  });

  it('PUT 成功時、レスポンス warnings が非空なら toast.warning を出す (P0-2 Commit 3)', async () => {
    const slot = makeSlot();
    mocks.proposeData = { slots: [slot], message: null };
    // confirmMutate を「成功して onSuccess にエンベロープを渡す」実装にする.
    mocks.confirmMutate.mockImplementation(
      (_vars: unknown, opts?: { onSuccess?: (data: unknown) => void }) => {
        opts?.onSuccess?.({
          items: [],
          warnings: [
            {
              code: 'time_conflict',
              message: '他患者と時間が重複しています',
              weekday: 1,
              severity: 'warning',
            },
            {
              code: 'lunch_break',
              message: '昼休みと重複しています',
              weekday: 1,
              severity: 'warning',
            },
          ],
          // U-1: week_sync が null だと「今週未反映」warning トーストに切り替わるため、
          // 本テスト (success 経路) では今週反映済みのレスポンスを模す。
          week_sync: { visits_regenerated: 1, visits_soft_deleted: 0 },
        });
      },
    );
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(
      screen.getByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    );
    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    // success トーストは従来どおり 1 回.
    expect(mockToast.success).toHaveBeenCalledTimes(1);
    // warnings 2 件が個別に toast.warning される.
    expect(mockToast.warning).toHaveBeenCalledWith('他患者と時間が重複しています');
    expect(mockToast.warning).toHaveBeenCalledWith('昼休みと重複しています');
  });

  it('canEdit=false では採用ボタンを出さない', () => {
    const slot = makeSlot();
    mocks.proposeData = { slots: [slot], message: null };
    render(<PoolCandidateList {...COMMON} canEdit={false} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.getByTestId('pool-candidate-slots')).toBeInTheDocument();
    expect(
      screen.queryByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    ).not.toBeInTheDocument();
  });

  // ── P-1a: delta バッジ ────────────────────────────────────────────────────

  it('marginal_cost_minutes が正値のとき delta バッジ "+N分" が表示される', () => {
    mocks.proposeData = {
      slots: [makeSlot({ marginal_cost_minutes: 15.4 })],
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    const badge = screen.getByTestId('pool-candidate-delta-badge');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent('コースの移動 +15分');
    expect(badge).toHaveAttribute('title');
  });

  it('marginal_cost_minutes が 0 以下のとき delta バッジ "±0分" が表示される', () => {
    mocks.proposeData = {
      slots: [makeSlot({ marginal_cost_minutes: 0 })],
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    const badge = screen.getByTestId('pool-candidate-delta-badge');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent('コースの移動 ±0分');
  });

  it('marginal_cost_minutes が null のとき delta バッジを表示しない', () => {
    mocks.proposeData = {
      slots: [makeSlot({ marginal_cost_minutes: null })],
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.queryByTestId('pool-candidate-delta-badge')).not.toBeInTheDocument();
  });

  it('marginal_cost_minutes が undefined (旧BE) のとき delta バッジを表示しない', () => {
    mocks.proposeData = {
      slots: [makeSlot()], // marginal_cost_minutes を含まないデフォルト
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.queryByTestId('pool-candidate-delta-badge')).not.toBeInTheDocument();
  });

  // ── P-1b: excluded_summary ───────────────────────────────────────────────

  it('候補 0 件 + excluded_summary あり → 理由別に表示する', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      excluded_summary: [
        { reason: 'capacity_full', count: 3, weekday: 1, sample_course_code: 'A' },
        { reason: 'travel_shortage', count: 2, weekday: 3, sample_course_code: null },
      ],
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    const summary = screen.getByTestId('pool-candidate-excluded-summary');
    expect(summary).toBeInTheDocument();
    expect(summary).toHaveTextContent('火曜');
    expect(summary).toHaveTextContent('コース容量が上限');
    expect(summary).toHaveTextContent('3件');
    expect(summary).toHaveTextContent('木曜');
    expect(summary).toHaveTextContent('移動時間が確保できず');
    expect(summary).toHaveTextContent('2件');
  });

  it('未知 reason は「その他の理由」で件数表示する (寛容パース)', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      excluded_summary: [
        { reason: 'unknown_future_reason', count: 1, weekday: 4, sample_course_code: null },
      ],
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    const summary = screen.getByTestId('pool-candidate-excluded-summary');
    expect(summary).toHaveTextContent('その他の理由');
    expect(summary).toHaveTextContent('1件');
  });

  it('候補 0 件 + excluded_summary なし (旧BE) → 従来の「見つかりませんでした」フォールバック', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      // excluded_summary フィールド自体を含まない (旧BE)
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.queryByTestId('pool-candidate-excluded-summary')).not.toBeInTheDocument();
    expect(screen.getByText(/実現可能な空き枠が見つかりませんでした/)).toBeInTheDocument();
  });

  // ── U-1: A/B 反映先選択 ───────────────────────────────────────────────────

  it('U-1: 採用確認パネルに ChangeScopeChoice が表示される（既定 A 選択）', () => {
    const slot = makeSlot();
    mocks.proposeData = { slots: [slot], message: null };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(
      screen.getByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    );
    // 確認パネルが出る
    expect(screen.getByTestId('pool-candidate-confirm')).toBeInTheDocument();
    // ChangeScopeChoice が表示される
    expect(screen.getByTestId('change-scope-choice')).toBeInTheDocument();
    // 既定は A (pattern) が選択済み
    expect(screen.getByTestId('change-scope-pattern')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('change-scope-week')).toHaveAttribute('aria-checked', 'false');
  });

  it('U-1 A 経路 (既定): confirmMutate に change_scope=pattern_and_week + iso_year/iso_week が付く', async () => {
    const slot = makeSlot();
    mocks.proposeData = { slots: [slot], message: null };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(
      screen.getByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    );
    // A が既定なのでそのまま確定
    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const arg = mocks.confirmMutate.mock.calls[0][0];
    expect(arg.patientId).toBe(PATIENT.id);
    expect(arg.body.mode).toBe('normal');
    // U-1: change_scope が付いている
    expect(arg.body.change_scope).toBe('pattern_and_week');
    expect(arg.body.iso_year).toBe(2026);
    expect(arg.body.iso_week).toBe(24);
    // place-and-fix は呼ばれない
    expect(mocks.placeAndFixMutate).not.toHaveBeenCalled();
  });

  it('U-1 B 経路: B 選択後に確定すると place-and-fix(fix_pattern=false) が呼ばれ PUT は呼ばれない', async () => {
    const slot = makeSlot();
    mocks.proposeData = { slots: [slot], message: null };
    // B 経路では course_template_id を解決するためテンプレートが必要
    mocks.templatesQueries = [
      {
        data: [
          {
            id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            office_id: '11111111-1111-4111-8111-111111111111',
            label: 'C',
            deleted_at: null,
          },
        ],
        status: 'success',
        isLoading: false,
        dataUpdatedAt: Date.now(),
      },
    ];
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(
      screen.getByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    );
    // B を選択
    fireEvent.click(screen.getByTestId('change-scope-week'));
    expect(screen.getByTestId('change-scope-week')).toHaveAttribute('aria-checked', 'true');

    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.placeAndFixMutate).toHaveBeenCalledTimes(1));
    const req = mocks.placeAndFixMutate.mock.calls[0][0];
    // fix_pattern=false が付いている
    expect(req.fix_pattern).toBe(false);
    // patient_id が付いている
    expect(req.patient_id).toBe(PATIENT.id);
    // iso_year/iso_week が付いている
    expect(req.iso_year).toBe(2026);
    expect(req.iso_week).toBe(24);
    // weekday が付いている
    expect(req.weekday).toBe(slot.weekday);
    // PUT (confirmMutate) は呼ばれない
    expect(mocks.confirmMutate).not.toHaveBeenCalled();
  });
});

// ── 方式b: 定員超過候補 ──────────────────────────────────────────────────────

describe('方式b: 定員超過候補 (overcapacity)', () => {
  it('overcapacity_available_count > 0 のとき呼びかけバナーが出る', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      overcapacity_available_count: 3,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.getByTestId('pool-overcapacity-callout')).toBeInTheDocument();
    expect(screen.getByTestId('pool-overcapacity-show-button')).toBeInTheDocument();
    expect(screen.getByText(/定員を \+1 名許容すれば入る候補が 3 件あります/)).toBeInTheDocument();
  });

  it('overcapacity_available_count が null・0・欠落のとき呼びかけを出さない', () => {
    for (const count of [null, 0, undefined] as const) {
      mocks.proposeData = { slots: [], message: null, overcapacity_available_count: count };
      const { unmount } = render(<PoolCandidateList {...COMMON} />);
      fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
      expect(screen.queryByTestId('pool-overcapacity-callout')).not.toBeInTheDocument();
      unmount();
      mocks.proposeMutate.mockReset();
    }
  });

  it('表示ボタン → include_overcapacity=true で再実行、超過セクションとバッジが出る', () => {
    const overcapSlot = makeSlot({ overcapacity: true });
    mocks.proposeData = {
      slots: [],
      message: null,
      overcapacity_available_count: 2,
      overcapacity_slots: [overcapSlot],
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));

    // 呼びかけバナーが出る
    expect(screen.getByTestId('pool-overcapacity-callout')).toBeInTheDocument();

    // 表示ボタンをクリック
    fireEvent.click(screen.getByTestId('pool-overcapacity-show-button'));

    // 2 回目の呼び出しが include_overcapacity=true で行われた
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(2);
    const lastCall = mocks.proposeMutate.mock.calls[1][0];
    expect(lastCall.include_overcapacity).toBe(true);

    // 超過セクションとバッジが出る
    expect(screen.getByTestId('pool-overcapacity-section')).toBeInTheDocument();
    expect(screen.getByTestId('pool-overcapacity-badge')).toBeInTheDocument();
  });

  it('超過候補の採用: 理由未入力なら確定 disabled / 入力後 PUT body に capacity_override_reason が入る', async () => {
    const overcapSlot = makeSlot({ overcapacity: true });
    mocks.proposeData = {
      slots: [],
      message: null,
      overcapacity_available_count: 1,
      overcapacity_slots: [overcapSlot],
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));

    // 超過候補を表示
    fireEvent.click(screen.getByTestId('pool-overcapacity-show-button'));

    // 「この枠で採用」をクリック
    const adoptBtn = screen.getByTestId(
      `pool-overcapacity-adopt-${overcapSlot.office_id}-${overcapSlot.weekday}-${overcapSlot.course_code}-${overcapSlot.start_time}`,
    );
    fireEvent.click(adoptBtn);

    // 確認パネルと理由入力欄が出る
    expect(screen.getByTestId('pool-candidate-confirm')).toBeInTheDocument();
    expect(screen.getByTestId('pool-overcapacity-reason-input')).toBeInTheDocument();

    // 理由未入力 → 確定ボタン disabled
    const confirmBtn = screen.getByTestId('pool-candidate-confirm-apply');
    expect(confirmBtn).toBeDisabled();

    // 理由を入力 → 確定ボタンが有効になる
    const reasonInput = screen.getByTestId('pool-overcapacity-reason-input');
    fireEvent.change(reasonInput, { target: { value: 'テスト理由' } });
    expect(confirmBtn).not.toBeDisabled();

    // 確定 → PUT body に capacity_override_reason が入る
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const arg = mocks.confirmMutate.mock.calls[0][0];
    expect(arg.body.capacity_override_reason).toBe('テスト理由');
  });
});

// ── スキーマ: 旧 BE レスポンス (両フィールド欠落) でパースが通る ─────────────

describe('proposeSlotsResponseSchema 寛容パース (P-1 後方互換)', () => {
  // モック依存のない純粋なスキーマテストなので vi.mock は不要.
  // vitest は同ファイル内で複数 describe を持てる.
  it('旧 BE レスポンス (marginal_cost_minutes / excluded_summary 欠落) がパースに通る', async () => {
    const { proposeSlotsResponseSchema } = await import('@/lib/schemas/v2/propose_slots');
    const oldBePayload = {
      iso_year: 2026,
      iso_week: 24,
      candidate_lat: null,
      candidate_lng: null,
      resolved_office_id: null,
      slots: [
        {
          office_id: '11111111-1111-4111-8111-111111111111',
          weekday: 1,
          weekday_code: 'Tue',
          course_code: 'A',
          course_label: 'ALabel',
          start_time: '09:00:00',
          end_time: '09:30:00',
          score: 90,
          // marginal_cost_minutes フィールドなし (旧BE)
        },
      ],
      // excluded_summary フィールドなし (旧BE)
      message: null,
    };
    const result = proposeSlotsResponseSchema.safeParse(oldBePayload);
    expect(result.success).toBe(true);
    if (result.success) {
      // excluded_summary は [] にフォールバック
      expect(result.data.excluded_summary).toEqual([]);
      // marginal_cost_minutes は undefined (nullish)
      expect(result.data.slots[0]!.marginal_cost_minutes).toBeUndefined();
    }
  });

  it('旧 BE レスポンス (方式b 新フィールド欠落) がパースに通る', async () => {
    const { proposeSlotsResponseSchema } = await import('@/lib/schemas/v2/propose_slots');
    const oldBePayload = {
      iso_year: 2026,
      iso_week: 24,
      candidate_lat: null,
      candidate_lng: null,
      resolved_office_id: null,
      slots: [
        {
          office_id: '11111111-1111-4111-8111-111111111111',
          weekday: 1,
          weekday_code: 'Tue',
          course_code: 'A',
          course_label: 'ALabel',
          start_time: '09:00:00',
          end_time: '09:30:00',
          score: 90,
          // overcapacity フィールドなし (旧BE)
        },
      ],
      message: null,
      // overcapacity_available_count / overcapacity_slots フィールドなし (旧BE)
    };
    const result = proposeSlotsResponseSchema.safeParse(oldBePayload);
    expect(result.success).toBe(true);
    if (result.success) {
      // 旧BE: overcapacity_available_count は undefined (nullish: 欠落 → undefined)
      expect(result.data.overcapacity_available_count == null).toBe(true);
      // 旧BE: overcapacity_slots は [] (default)
      expect(result.data.overcapacity_slots).toEqual([]);
      // 旧BE: proposeSlotItem.overcapacity は false (default)
      expect(result.data.slots[0]!.overcapacity).toBe(false);
    }
  });
});

// ── W-3: 効率優先の代替枠 ────────────────────────────────────────────────────

describe('W-3: 効率優先の代替枠 (efficiency alternatives)', () => {
  beforeEach(() => {
    mocks.proposeMutate.mockReset();
    mocks.confirmMutate.mockReset();
    mocks.placeAndFixMutate.mockReset();
    mocks.proposeData = undefined;
    mocks.existingFixedVisits = [];
    mocks.templatesQueries = [];
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
  });

  it('propose-slots リクエストに include_efficiency_alternatives: true が付く', () => {
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
    const req = mocks.proposeMutate.mock.calls[0][0];
    expect(req.include_efficiency_alternatives).toBe(true);
  });

  it('is_efficiency_alternative=true のスロットが折りたたみセクションに表示される', () => {
    const normalSlot = makeSlot({ is_efficiency_alternative: false });
    const effSlot = makeSlot({
      weekday: 3, // 木
      weekday_code: 'Thu',
      course_code: 'D',
      start_time: '09:00:00',
      end_time: '09:35:00',
      is_efficiency_alternative: true,
      reasons: ['近接高効率'],
    });
    mocks.proposeData = {
      slots: [normalSlot, effSlot],
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));

    // 通常候補リストが出る
    expect(screen.getByTestId('pool-candidate-slots')).toBeInTheDocument();

    // 効率代替セクションが出る (折りたたみ = details 要素)
    const effSection = screen.getByTestId('pool-efficiency-section');
    expect(effSection).toBeInTheDocument();

    // 件数バッジが 1件
    expect(screen.getByTestId('pool-efficiency-count')).toHaveTextContent('1件');

    // セクション内に効率スロットが含まれる
    expect(screen.getByTestId('pool-efficiency-slot-list')).toBeInTheDocument();

    // 効率スロットのラベルが存在する (details が open でないと hidden になるが DOM には存在する)
    expect(screen.getByTestId(`pool-efficiency-${effSlot.office_id}-${effSlot.weekday}-${effSlot.course_code}-${effSlot.start_time}`)).toBeInTheDocument();
  });
});

// ── W-5b: autoRequestOvercapacity ───────────────────────────────────────────

describe('W-5b: autoRequestOvercapacity (超過候補の自動展開)', () => {
  beforeEach(() => {
    mocks.proposeMutate.mockReset();
    mocks.confirmMutate.mockReset();
    mocks.placeAndFixMutate.mockReset();
    mocks.proposeData = undefined;
    mocks.existingFixedVisits = [];
    mocks.templatesQueries = [];
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
  });

  it('primary + autoRequestOvercapacity: 通常候補0件 + 超過候補ありで include_overcapacity=true が自動発火する', () => {
    // propose-slots がマウント時に 1 回呼ばれた後、結果 (data) に overcapacity_available_count>=1 がある。
    mocks.proposeData = {
      slots: [],
      message: null,
      overcapacity_available_count: 2,
      overcapacity_slots: [makeSlot({ overcapacity: true })],
    };
    render(<PoolCandidateList {...COMMON} primary autoRequestOvercapacity />);

    // 通常 (1 回目) + 自動 overcapacity (2 回目) の 2 回呼ばれるはず。
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(2);
    // 1 回目: 通常リクエスト (include_overcapacity=false)。
    expect(mocks.proposeMutate.mock.calls[0][0].include_overcapacity).toBe(false);
    // 2 回目: 自動 overcapacity リクエスト (include_overcapacity=true)。
    expect(mocks.proposeMutate.mock.calls[1][0].include_overcapacity).toBe(true);
  });

  it('primary + autoRequestOvercapacity: 通常候補がある場合は自動発火しない', () => {
    mocks.proposeData = {
      slots: [makeSlot()], // 通常候補が 1 件ある
      message: null,
      overcapacity_available_count: 2,
    };
    render(<PoolCandidateList {...COMMON} primary autoRequestOvercapacity />);

    // 通常 1 回のみ (overcapacity は自動発火しない)。
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
    expect(mocks.proposeMutate.mock.calls[0][0].include_overcapacity).toBe(false);
  });

  it('primary + autoRequestOvercapacity: 超過候補がない (count=0) 場合は自動発火しない', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      overcapacity_available_count: 0,
    };
    render(<PoolCandidateList {...COMMON} primary autoRequestOvercapacity />);

    // 通常 1 回のみ。
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
  });

  it('primary + autoRequestOvercapacity なし: 自動発火しない (従来挙動維持)', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      overcapacity_available_count: 3,
    };
    render(<PoolCandidateList {...COMMON} primary />);

    // autoRequestOvercapacity=false (デフォルト) では自動発火しない。
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
    expect(mocks.proposeMutate.mock.calls[0][0].include_overcapacity).toBe(false);
  });
});

// ── W-12a: 2名体制ペア候補 ───────────────────────────────────────────────────

const PARTNER_TPL_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const PRIMARY_TPL_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

/** 2名体制ペア候補 (partner_* を持つ). 既定は主コース C + 相方コース D の同時刻ペア. */
function makePairSlot(over: Record<string, unknown> = {}) {
  return makeSlot({
    partner_course_code: 'D',
    partner_course_label: '稲D',
    partner_course_template_id: PARTNER_TPL_ID,
    partner_staff_name: '佐藤',
    ...over,
  });
}

describe('W-12a: 2名体制ペア候補 (PoolCandidateList)', () => {
  beforeEach(() => {
    mocks.proposeMutate.mockReset();
    mocks.confirmMutate.mockReset();
    mocks.placeAndFixMutate.mockReset();
    mocks.proposeData = undefined;
    mocks.existingFixedVisits = [];
    mocks.templatesQueries = [];
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
  });

  it('ペア候補は 2 コースチップ・相方担当・同時配置説明を表示する', () => {
    mocks.proposeData = { slots: [makePairSlot()], message: null };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));

    // 2名体制チップ + 2 コース表記.
    const badge = screen.getByTestId('pool-candidate-pair-badge');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent('2名体制');
    expect(badge).toHaveTextContent('稲C と 稲D');
    // 主担当 + 相方担当の 2 名.
    expect(screen.getByText(/担当: 山田/)).toBeInTheDocument();
    expect(screen.getByText(/相方: 佐藤/)).toBeInTheDocument();
    // 同時配置説明.
    expect(screen.getByTestId('pool-candidate-pair-desc')).toHaveTextContent(
      /稲C と 稲D の 14:00 に同時配置/,
    );
  });

  it('採用 (A経路): PUT body に slot0+slot1 の2行 (同時刻・別 course_template_id) が入る', async () => {
    const slot = makePairSlot();
    mocks.proposeData = { slots: [slot], message: null };
    // 主コース C の course_template_id を解決させる (相方は partner_course_template_id を直接使う).
    mocks.templatesQueries = [
      {
        data: [
          {
            id: PRIMARY_TPL_ID,
            office_id: '11111111-1111-4111-8111-111111111111',
            label: 'C',
            deleted_at: null,
          },
        ],
        status: 'success',
        isLoading: false,
        dataUpdatedAt: Date.now(),
      },
    ];
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(
      screen.getByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    );
    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const items = mocks.confirmMutate.mock.calls[0][0].body.items as Array<{
      weekday: number;
      slot_index: number;
      start_time: string;
      course_template_id?: string;
    }>;
    // slot0 (主) + slot1 (相方) の 2 行.
    expect(items).toHaveLength(2);
    const slot0 = items.find((i) => i.slot_index === 0)!;
    const slot1 = items.find((i) => i.slot_index === 1)!;
    expect(slot0).toBeTruthy();
    expect(slot1).toBeTruthy();
    // 同時刻・同曜日.
    expect(slot0.weekday).toBe(1);
    expect(slot1.weekday).toBe(1);
    expect(slot0.start_time).toBe('14:00:00');
    expect(slot1.start_time).toBe('14:00:00');
    // 別 course_template_id (主 = 解決値 / 相方 = partner_course_template_id).
    expect(slot0.course_template_id).toBe(PRIMARY_TPL_ID);
    expect(slot1.course_template_id).toBe(PARTNER_TPL_ID);
    expect(slot0.course_template_id).not.toBe(slot1.course_template_id);
    // place-and-fix は呼ばれない (A経路).
    expect(mocks.placeAndFixMutate).not.toHaveBeenCalled();
  });

  it('採用 (B経路): place-and-fix が staff_count=2 + course_template_ids[主,相方] で呼ばれる', async () => {
    const slot = makePairSlot();
    mocks.proposeData = { slots: [slot], message: null };
    mocks.templatesQueries = [
      {
        data: [
          {
            id: PRIMARY_TPL_ID,
            office_id: '11111111-1111-4111-8111-111111111111',
            label: 'C',
            deleted_at: null,
          },
        ],
        status: 'success',
        isLoading: false,
        dataUpdatedAt: Date.now(),
      },
    ];
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(
      screen.getByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    );
    // B (今週だけ) を選択.
    fireEvent.click(screen.getByTestId('change-scope-week'));
    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.placeAndFixMutate).toHaveBeenCalledTimes(1));
    const req = mocks.placeAndFixMutate.mock.calls[0][0];
    expect(req.staff_count).toBe(2);
    expect(req.fix_pattern).toBe(false);
    expect(req.course_template_ids).toEqual([PRIMARY_TPL_ID, PARTNER_TPL_ID]);
    // 旧形式の単一 course_template_id は送らない.
    expect(req.course_template_id).toBeUndefined();
    // PUT (confirmMutate) は呼ばれない.
    expect(mocks.confirmMutate).not.toHaveBeenCalled();
  });

  it('two_staff_not_guaranteed 警告はペア候補では表示しない', () => {
    mocks.proposeData = {
      slots: [makePairSlot({ warnings: ['two_staff_not_guaranteed'] })],
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    // proposeWarningLabel は mock で code をそのまま返すため、非表示なら該当テキストが無い.
    expect(screen.queryByText('two_staff_not_guaranteed')).not.toBeInTheDocument();
  });

  it('two_staff_not_guaranteed 警告は非ペア候補では従来どおり表示する', () => {
    mocks.proposeData = {
      slots: [makeSlot({ warnings: ['two_staff_not_guaranteed'] })],
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.getByText('two_staff_not_guaranteed')).toBeInTheDocument();
  });

  it('no_pair_slot 除外理由が訳語表示される', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      excluded_summary: [
        { reason: 'no_pair_slot', count: 2, weekday: 1, sample_course_code: 'C' },
      ],
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    const summary = screen.getByTestId('pool-candidate-excluded-summary');
    expect(summary).toHaveTextContent('火曜');
    expect(summary).toHaveTextContent('同時刻に入れる2コースの組が見つかりません');
    expect(summary).toHaveTextContent('2件');
  });
});

// ── W-12d: 詰まり解消相談 (propose-unblock) ───────────────────────────────────

/** 時間起因の候補0 (詰まり解消相談の発動条件を満たす propose-slots レスポンス)。 */
const TIME_BLOCKER_PROPOSE = {
  slots: [],
  message: null,
  excluded_summary: [{ reason: 'no_gap', count: 2, weekday: 1, sample_course_code: 'B' }],
};

function makeUnblockPlan(over: Record<string, unknown> = {}) {
  return {
    plan_id: 'plan-1',
    moves: [
      {
        patient_id: 'p-move-1',
        patient_name: '田中',
        from: { weekday: 1, course_code: 'B', course_label: '稲B', start_time: '16:00:00' },
        to: { weekday: 1, course_code: 'B', course_label: '稲B', start_time: '15:30:00' },
        delta_minutes: 3,
        within_preference: true,
      },
    ],
    insert: {
      weekday: 1,
      course_code: 'B',
      course_label: '稲B',
      start_time: '16:00:00',
      end_time: '16:35:00',
      partner_course_code: null,
      partner_course_label: null,
    },
    total_delta_minutes: 5,
    moved_count: 1,
    ...over,
  };
}

function makeUnblockResult(over: Record<string, unknown> = {}) {
  return {
    plans: [makeUnblockPlan()],
    unmovable_summary: {
      pinned: 0,
      locked: 0,
      two_staff: 0,
      pair: 0,
      dismissed: 0,
      confirmation_required: 0,
    },
    state_token: 'tok-abc',
    ...over,
  };
}

describe('W-12d: 詰まり解消相談 (unblock)', () => {
  beforeEach(() => {
    mocks.proposeMutate.mockReset();
    mocks.confirmMutate.mockReset();
    mocks.placeAndFixMutate.mockReset();
    mocks.unblockMutate.mockReset();
    mocks.unblockApplyMutate.mockReset();
    mocks.proposeData = undefined;
    mocks.unblockData = undefined;
    mocks.unblockPending = false;
    mocks.unblockApplyPending = false;
    mocks.existingFixedVisits = [];
    mocks.templatesQueries = [];
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
  });

  it('発動条件: 候補0 + 時間起因 (no_gap) のとき呼びかけと探索ボタンが出る', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.getByTestId('unblock-callout')).toBeInTheDocument();
    expect(screen.getByTestId('unblock-search-button')).toBeInTheDocument();
    expect(screen.getByText(/既存の訪問を少しずらせば入る手/)).toBeInTheDocument();
  });

  it('W-15: capacity_full のみでも「ずらせば入る手」呼びかけと探索ボタンが出る', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      excluded_summary: [{ reason: 'capacity_full', count: 3, weekday: 1, sample_course_code: 'A' }],
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    // W-15: 定員起因でもブロッカーを他コースへ退避させる手を探せる。
    expect(screen.getByTestId('unblock-callout')).toBeInTheDocument();
    expect(screen.getByTestId('unblock-search-button')).toBeInTheDocument();
    // 定員超過候補 (方式b) は無いので方式b callout は出ない（区切りも出ない）。
    expect(screen.queryByTestId('pool-overcapacity-callout')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pool-consult-divider')).not.toBeInTheDocument();
  });

  it('W-15: 定員起因で定員超過候補もあるとき 方式b と unblock の両呼びかけが並列表示される', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      overcapacity_available_count: 2,
      excluded_summary: [{ reason: 'capacity_full', count: 3, weekday: 1, sample_course_code: 'A' }],
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    // 方式b（+1名相談）の呼びかけ。
    expect(screen.getByTestId('pool-overcapacity-callout')).toBeInTheDocument();
    // unblock（ずらす）の呼びかけ。
    expect(screen.getByTestId('unblock-callout')).toBeInTheDocument();
    // 両方出るとき軽い区切りが方式b → unblock の間に挟まる。
    expect(screen.getByTestId('pool-consult-divider')).toBeInTheDocument();
  });

  it('探索ボタン: propose-unblock を当該患者・拠点・limit=5 で呼ぶ', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(screen.getByTestId('unblock-search-button'));
    expect(mocks.unblockMutate).toHaveBeenCalledTimes(1);
    const req = mocks.unblockMutate.mock.calls[0][0];
    expect(req.existing_patient_id).toBe(PATIENT.id);
    expect(req.office_id).toBe(COMMON.officeId);
    expect(req.iso_year).toBe(2026);
    expect(req.iso_week).toBe(24);
    expect(req.limit).toBe(5);
    // 固定希望なので preferred_start が乗る。
    expect(req.preferred_start).toBe('16:00');
  });

  it('プランカード: 手順・希望範囲内バッジ・フッター・配置行を表示する', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    mocks.unblockData = makeUnblockResult();
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));

    expect(screen.getByTestId('unblock-plans')).toBeInTheDocument();
    expect(screen.getByTestId('unblock-plan-card')).toBeInTheDocument();
    // 手順ステップ: 移動 1 件 + 配置 1 件 = 2 ステップ。
    expect(screen.getAllByTestId('unblock-plan-step')).toHaveLength(2);
    // 移動患者名 + 希望範囲内バッジ。
    expect(screen.getByText('田中')).toBeInTheDocument();
    expect(screen.getByText('希望範囲内')).toBeInTheDocument();
    // 配置行 + フッター。
    expect(screen.getByText('この枠に配置:')).toBeInTheDocument();
    expect(screen.getByText(/合計 \+5分\/週・動くのは 1名/)).toBeInTheDocument();
    // 発動ボタン。
    expect(screen.getByTestId('unblock-plan-apply')).toBeInTheDocument();
  });

  it('W-15: frees_capacity=true のプランに「定員内に収まります」バッジが出る', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    mocks.unblockData = makeUnblockResult({
      plans: [makeUnblockPlan({ frees_capacity: true })],
    });
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.getByTestId('unblock-plan-frees-capacity')).toBeInTheDocument();
    expect(screen.getByText('定員内に収まります')).toBeInTheDocument();
  });

  it('W-15: frees_capacity=false のプランにはバッジを出さない', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    mocks.unblockData = makeUnblockResult({
      plans: [makeUnblockPlan({ frees_capacity: false })],
    });
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.queryByTestId('unblock-plan-frees-capacity')).not.toBeInTheDocument();
  });

  it('確認ダイアログ: 未チェックでは確定不可・チェックで有効化 (gating)', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    mocks.unblockData = makeUnblockResult();
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(screen.getByTestId('unblock-plan-apply'));

    // 確認ダイアログ + Ctrl+Z 対象外バナー。
    expect(screen.getByTestId('unblock-confirm')).toBeInTheDocument();
    const banner = screen.getByTestId('unblock-confirm-banner');
    expect(banner).toHaveTextContent(/毎週の型（固定訪問週間）が変わり/);
    expect(banner).toHaveTextContent(/Ctrl\+Z の対象外/);
    // 動く患者の明示。
    expect(screen.getByTestId('unblock-confirm')).toHaveTextContent('田中');

    // 未チェック → 確定 disabled。
    const applyBtn = screen.getByTestId('unblock-confirm-apply');
    expect(applyBtn).toBeDisabled();

    // チェック → 有効化。
    fireEvent.click(screen.getByTestId('unblock-confirm-checkbox'));
    expect(applyBtn).not.toBeDisabled();
  });

  it('適用: plan と state_token をそのまま送る', async () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    mocks.unblockData = makeUnblockResult();
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(screen.getByTestId('unblock-plan-apply'));
    fireEvent.click(screen.getByTestId('unblock-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('unblock-confirm-apply'));

    await waitFor(() => expect(mocks.unblockApplyMutate).toHaveBeenCalledTimes(1));
    const arg = mocks.unblockApplyMutate.mock.calls[0][0];
    expect(arg.office_id).toBe(COMMON.officeId);
    expect(arg.iso_year).toBe(2026);
    expect(arg.iso_week).toBe(24);
    // target_patient_id: BE の指紋照合に使う患者 UUID (設計書 §2.2)。
    expect(arg.target_patient_id).toBe(PATIENT.id);
    expect(arg.state_token).toBe('tok-abc');
    // plan はそのまま (探索結果の plan を無改変で送る)。
    expect(arg.plan).toEqual(makeUnblockPlan());
  });

  it('409: 再探索メッセージと再探索ボタンを出す', async () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    mocks.unblockData = makeUnblockResult();
    mocks.unblockApplyMutate.mockImplementation(
      (_vars: unknown, opts?: { onError?: (err: unknown) => void }) => {
        opts?.onError?.(new ApiError('conflict', 409, null));
      },
    );
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(screen.getByTestId('unblock-plan-apply'));
    fireEvent.click(screen.getByTestId('unblock-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('unblock-confirm-apply'));

    await waitFor(() => expect(screen.getByTestId('unblock-research')).toBeInTheDocument());
    expect(screen.getByTestId('unblock-research-button')).toBeInTheDocument();
    expect(mockToast.error).toHaveBeenCalledWith('スケジュールが変わりました。再探索してください');
  });

  it('plans 0 件: unmovable_summary を「動かせない事情」として表示する', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    mocks.unblockData = makeUnblockResult({
      plans: [],
      unmovable_summary: {
        pinned: 2,
        locked: 1,
        two_staff: 0,
        pair: 1,
        dismissed: 0,
        confirmation_required: 3,
      },
    });
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));

    const summary = screen.getByTestId('unblock-unmovable-summary');
    expect(summary).toBeInTheDocument();
    expect(summary).toHaveTextContent('ピン留め 2件');
    expect(summary).toHaveTextContent('固定（可動域） 1件');
    expect(summary).toHaveTextContent('同住所ペア 1件');
    expect(summary).toHaveTextContent('患者確認が必要 3件');
    // 0 件のキー (2名体制・見送り済み) は出さない。
    expect(summary).not.toHaveTextContent('2名体制');
  });
});

// ── W-14: autoRequestUnblock (詰まり解消探索の自動発火) ───────────────────────

describe('W-14: autoRequestUnblock (詰まり解消探索の自動発火)', () => {
  beforeEach(() => {
    mocks.proposeMutate.mockReset();
    mocks.confirmMutate.mockReset();
    mocks.placeAndFixMutate.mockReset();
    mocks.unblockMutate.mockReset();
    mocks.unblockApplyMutate.mockReset();
    mocks.proposeData = undefined;
    mocks.unblockData = undefined;
    mocks.unblockPending = false;
    mocks.unblockApplyPending = false;
    mocks.existingFixedVisits = [];
    mocks.templatesQueries = [];
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
  });

  it('primary + autoRequestUnblock: 候補0 + 時間起因で探索が自動発火する (1 回だけ)', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    const { rerender } = render(
      <PoolCandidateList {...COMMON} primary autoRequestUnblock />,
    );
    // mount 直後に propose-unblock (runSearch) が 1 回だけ自動発火する。
    expect(mocks.unblockMutate).toHaveBeenCalledTimes(1);
    const req = mocks.unblockMutate.mock.calls[0][0];
    expect(req.existing_patient_id).toBe(PATIENT.id);
    expect(req.limit).toBe(5);
    // 再レンダーしても ref ガードで再発火しない。
    rerender(<PoolCandidateList {...COMMON} primary autoRequestUnblock />);
    expect(mocks.unblockMutate).toHaveBeenCalledTimes(1);
  });

  it('primary + autoRequestUnblock: 通常候補があるときは自動発火しない', () => {
    // 通常候補が 1 件あると UnblockConsult 自体が mount されない。
    mocks.proposeData = {
      slots: [makeSlot()],
      message: null,
      excluded_summary: [{ reason: 'no_gap', count: 1, weekday: 1, sample_course_code: 'B' }],
    };
    render(<PoolCandidateList {...COMMON} primary autoRequestUnblock />);
    expect(mocks.unblockMutate).not.toHaveBeenCalled();
  });

  it('primary + autoRequestUnblock なし: 自動発火しない (従来どおり呼びかけのみ)', () => {
    mocks.proposeData = TIME_BLOCKER_PROPOSE;
    render(<PoolCandidateList {...COMMON} primary />);
    // 自動発火せず、静かな呼びかけボタンのみ出る。
    expect(mocks.unblockMutate).not.toHaveBeenCalled();
    expect(screen.getByTestId('unblock-callout')).toBeInTheDocument();
  });

  it('W-15: primary + autoRequestUnblock: 定員起因 (capacity_full) でも探索が自動発火する', () => {
    mocks.proposeData = {
      slots: [],
      message: null,
      excluded_summary: [
        { reason: 'capacity_full', count: 3, weekday: 1, sample_course_code: 'A' },
      ],
    };
    render(<PoolCandidateList {...COMMON} primary autoRequestUnblock />);
    // W-15: capacity_full も発動理由。UnblockConsult が mount され autoFire で 1 回発火する。
    expect(mocks.unblockMutate).toHaveBeenCalledTimes(1);
  });
});
