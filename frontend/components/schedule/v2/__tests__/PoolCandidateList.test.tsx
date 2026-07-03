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
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('lucide-react', () => ({
  AlertTriangle: () => <span />,
  CheckCircle2: () => <span />,
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
