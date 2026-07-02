/**
 * ImprovementSuggestionsSection + ImprovementSuggestionCard 単体テスト (P2-C).
 *
 * 検証:
 *   1. 効果 (−N分/週・−Nkm/週) を主役に表示する.
 *   2. requires_patient_confirmation=true で要確認バッジを表示する.
 *   3. staff_warnings を proposeWarningLabel で表示する.
 *   4. 採用 → useConfirmFixedVisits が採用枠 (候補曜日) を含む body で呼ばれ、
 *      成功でカードが消え improvement-suggestions が invalidate される.
 *   5. 0 件時は filtered_summary から非表示内訳テキストを出す (0 は省略).
 *   6. fetch エラー時はセクション内にエラーテキストのみ (dialog は生きる).
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  mocks: {
    suggestionsResult: { data: undefined as unknown, isLoading: false, isError: false },
    confirmMutate: vi.fn(),
    dismissMutate: vi.fn(),
    existingFixedVisits: [] as unknown[],
    invalidateQueries: vi.fn(),
    toastFixedVisitWarnings: vi.fn(),
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 't', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));
vi.mock('@tanstack/react-query', () => ({
  useQueries: () => [],
  useQueryClient: () => ({ invalidateQueries: mocks.invalidateQueries }),
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));
vi.mock('@/lib/queries/improvementSuggestions', () => ({
  useImprovementSuggestions: () => mocks.suggestionsResult,
  useDismissSuggestion: () => ({ mutate: mocks.dismissMutate, isPending: false }),
  IMPROVEMENT_SUGGESTIONS_KEY: 'improvement-suggestions',
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
  toastFixedVisitWarnings: (w?: unknown) => mocks.toastFixedVisitWarnings(w),
}));
vi.mock('@/lib/queries/fieldBoard', () => ({ proposeWarningLabel: (c: string) => `LBL:${c}` }));
vi.mock('@/lib/schemas/patient', () => ({
  coerceWeeklyPattern: () => ({ service_minutes: 30 }),
}));

import { ImprovementSuggestionsSection } from '../ImprovementSuggestionsSection';
import type {
  ImprovementSuggestion,
  ImprovementSuggestionsResponse,
} from '@/lib/schemas/v2/improvementSuggestion';
import type { PatientRead } from '@/lib/schemas/patient';

const PATIENT = {
  id: '22222222-2222-4222-8222-222222222222',
  name: '中尾 要太',
  weekly_pattern: null,
} as unknown as PatientRead;

function makeSuggestion(over: Partial<ImprovementSuggestion> = {}): ImprovementSuggestion {
  return {
    kind: 'time_change',
    target_weekday: 0,
    current: {
      office_id: '11111111-1111-4111-8111-111111111111',
      weekday: 0,
      weekday_code: 'Mon',
      start_time: '09:00',
      end_time: '09:30',
      course_label: '稲A',
      staff_name: null,
    },
    candidate: {
      office_id: '11111111-1111-4111-8111-111111111111',
      office_name: '稲毛',
      weekday: 0,
      weekday_code: 'Mon',
      start_time: '10:00',
      end_time: '10:30',
      course_code: 'B',
      course_label: '稲B',
      staff_name: null,
    },
    delta: { travel_minutes_saved: 18, travel_km_saved: 2.1 },
    changes: { changes: ['開始時刻が 09:00 → 10:00 に変わります'], unchanged: ['曜日は月曜のまま'] },
    staff_warnings: [],
    feasibility_basis: 'pfv',
    requires_patient_confirmation: false,
    ...over,
  };
}

function makeResponse(
  suggestions: ImprovementSuggestion[],
  filtered?: Partial<ImprovementSuggestionsResponse['filtered_summary']>,
): ImprovementSuggestionsResponse {
  return {
    patient_id: PATIENT.id,
    iso_year: 2026,
    iso_week: 27,
    suggestions,
    filtered_summary: {
      pinned: 0,
      locked: 0,
      no_current_visit: 0,
      dismissed: 0,
      below_threshold: 0,
      day_restricted: 0,
      ...filtered,
    },
  };
}

function renderSection(canEdit = true) {
  render(
    <ImprovementSuggestionsSection
      patient={PATIENT}
      isoYear={2026}
      isoWeek={27}
      canEdit={canEdit}
    />,
  );
}

describe('ImprovementSuggestionsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.suggestionsResult = { data: undefined, isLoading: false, isError: false };
    mocks.existingFixedVisits = [];
    mocks.confirmMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess?: (d: unknown) => void }) =>
        opts.onSuccess?.({ warnings: [] }),
    );
  });

  it('1. 効果 (−18分/週・−2.1km/週) を主役表示する', () => {
    mocks.suggestionsResult = { data: makeResponse([makeSuggestion()]), isLoading: false, isError: false };
    renderSection();
    expect(screen.getByTestId('improvement-effect')).toHaveTextContent('−18分/週（−2.1km/週）');
  });

  it('2. requires_patient_confirmation=true で要確認バッジ表示', () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSuggestion({ requires_patient_confirmation: true })]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    expect(screen.getByTestId('improvement-requires-confirmation')).toHaveTextContent(
      '可動域未設定・患者様への確認推奨',
    );
  });

  it('3. staff_warnings を proposeWarningLabel で表示', () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSuggestion({ staff_warnings: ['staff_absent'] })]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    expect(screen.getByTestId('improvement-staff-warnings')).toHaveTextContent('LBL:staff_absent');
  });

  it('4. 採用 → confirm mutate が候補曜日の枠で呼ばれ、成功でカード消滅 + invalidate', async () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSuggestion({ candidate: { ...makeSuggestion().candidate, weekday: 0 } })]),
      isLoading: false,
      isError: false,
    };
    renderSection();

    await userEvent.click(screen.getByTestId('improvement-adopt-button'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const vars = mocks.confirmMutate.mock.calls[0][0] as {
      patientId: string;
      body: { mode: string; items: { weekday: number }[] };
    };
    expect(vars.patientId).toBe(PATIENT.id);
    expect(vars.body.mode).toBe('normal');
    expect(vars.body.items[0]?.weekday).toBe(0);

    // 成功パスで警告トーストヘルパ + improvement-suggestions invalidate.
    expect(mocks.toastFixedVisitWarnings).toHaveBeenCalledTimes(1);
    expect(mocks.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['improvement-suggestions'],
    });

    // カードが消える (採用済み指紋で hidden → 空表示).
    await waitFor(() =>
      expect(screen.queryByTestId('improvement-adopt-button')).not.toBeInTheDocument(),
    );
  });

  it('4b. day_change 採用 → 元曜日 (target) の slot0 を除去して候補曜日へ移動', async () => {
    mocks.existingFixedVisits = [
      // 元曜日 (月=0, slot0) — day_change で除去されるべき.
      {
        id: 'r0',
        patient_id: PATIENT.id,
        mode: 'normal',
        weekday: 0,
        slot_index: 0,
        start_time: '09:00',
        duration_min: 30,
        course_template_id: null,
        created_at: '2026-01-01T00:00:00',
        updated_at: '2026-01-01T00:00:00',
      },
      // 別曜日 (火=1) — 保持されるべき.
      {
        id: 'r1',
        patient_id: PATIENT.id,
        mode: 'normal',
        weekday: 1,
        slot_index: 0,
        start_time: '11:00',
        duration_min: 30,
        course_template_id: null,
        is_pinned: true,
        movability: 'locked',
        created_at: '2026-01-01T00:00:00',
        updated_at: '2026-01-01T00:00:00',
      },
    ];
    const dayChange = makeSuggestion({
      kind: 'day_change',
      target_weekday: 0,
      candidate: { ...makeSuggestion().candidate, weekday: 2, weekday_code: 'Wed' },
    });
    mocks.suggestionsResult = { data: makeResponse([dayChange]), isLoading: false, isError: false };
    renderSection();

    await userEvent.click(screen.getByTestId('improvement-adopt-button'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const body = (mocks.confirmMutate.mock.calls[0][0] as { body: { items: Array<{ weekday: number; is_pinned?: boolean; movability?: string }> } }).body;
    const weekdays = body.items.map((i) => i.weekday).sort();
    // 元曜日 0 は消え、火(1)保持 + 候補水(2)追加.
    expect(weekdays).toEqual([1, 2]);
    // 火(1)の movability/is_pinned は保持される.
    const preserved = body.items.find((i) => i.weekday === 1);
    expect(preserved?.is_pinned).toBe(true);
    expect(preserved?.movability).toBe('locked');
  });

  it('5. 0 件 + filtered_summary で非表示内訳テキスト (0 は省略)', () => {
    mocks.suggestionsResult = {
      data: makeResponse([], { pinned: 2, below_threshold: 1, dismissed: 0 }),
      isLoading: false,
      isError: false,
    };
    renderSection();
    const empty = screen.getByTestId('improvement-suggestions-empty');
    expect(empty).toHaveTextContent('ピン留め2件');
    expect(empty).toHaveTextContent('効果が閾値未満1件');
    // 0 のカテゴリ (却下済み) は出さない.
    expect(empty).not.toHaveTextContent('却下済み');
  });

  it('6. fetch エラー時はエラーテキストのみ (セクションは生きる)', () => {
    mocks.suggestionsResult = { data: undefined, isLoading: false, isError: true };
    renderSection();
    expect(screen.getByTestId('improvement-suggestions-error')).toBeInTheDocument();
    // セクション見出しは残る (dialog 全体は死なない).
    expect(screen.getByTestId('improvement-suggestions-section')).toBeInTheDocument();
  });

  it('7. canEdit=false で採用/見送りボタンを出さない', () => {
    mocks.suggestionsResult = { data: makeResponse([makeSuggestion()]), isLoading: false, isError: false };
    renderSection(false);
    expect(screen.queryByTestId('improvement-adopt-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('improvement-dismiss-button')).not.toBeInTheDocument();
  });
});
