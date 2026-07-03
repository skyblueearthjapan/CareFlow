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
    applySwapMutate: vi.fn(),
    visitMoveWeekOnlyMutate: vi.fn(),
    existingFixedVisits: [] as unknown[],
    templatesQueries: [] as unknown[],
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
  useQueries: () => mocks.templatesQueries,
  useQueryClient: () => ({ invalidateQueries: mocks.invalidateQueries }),
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));
vi.mock('@/lib/queries/improvementSuggestions', () => ({
  useImprovementSuggestions: () => mocks.suggestionsResult,
  useDismissSuggestion: () => ({ mutate: mocks.dismissMutate, isPending: false }),
  useApplySwap: () => ({ mutate: mocks.applySwapMutate, isPending: false }),
  IMPROVEMENT_SUGGESTIONS_KEY: 'improvement-suggestions',
}));
vi.mock('@/lib/queries/propose_confirm', () => ({
  useConfirmFixedVisits: () => ({ mutate: mocks.confirmMutate, isPending: false }),
}));
vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutate: mocks.visitMoveWeekOnlyMutate, isPending: false }),
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
import {
  improvementSuggestionsResponseSchema,
  type ImprovementSuggestion,
  type ImprovementSuggestionsResponse,
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
    changes: {
      changes: ['開始時刻が 09:00 → 10:00 に変わります'],
      unchanged: ['曜日は月曜のまま'],
    },
    staff_warnings: [],
    feasibility_basis: 'pfv',
    requires_patient_confirmation: false,
    within_preference: false,
    swap_counterpart: null,
    ...over,
  };
}

function makeSwapSuggestion(over: Partial<ImprovementSuggestion> = {}): ImprovementSuggestion {
  return {
    ...makeSuggestion(),
    kind: 'swap',
    target_weekday: 0,
    // X: 月09:00 → 月14:00 (candidate = Y の旧枠).
    candidate: {
      office_id: '11111111-1111-4111-8111-111111111111',
      office_name: '稲毛',
      weekday: 0,
      weekday_code: 'Mon',
      start_time: '14:00',
      end_time: '14:30',
      course_code: 'C',
      course_label: '稲C',
      staff_name: null,
    },
    swap_counterpart: {
      patient_id: '33333333-3333-4333-8333-333333333333',
      patient_name: '佐藤 花子',
      current_weekday: 0,
      current_start_time: '14:00',
      new_weekday: 0,
      new_start_time: '09:00',
      requires_patient_confirmation: false,
      within_preference: false,
    },
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
    mocks.templatesQueries = [];
    mocks.confirmMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess?: (d: unknown) => void }) =>
        opts.onSuccess?.({ warnings: [] }),
    );
    mocks.applySwapMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess?: (d: unknown) => void }) =>
        opts.onSuccess?.({ applied: true, warnings: [] }),
    );
  });

  it('1. 効果 (−18分/週・−2.1km/週) を主役表示する', () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSuggestion()]),
      isLoading: false,
      isError: false,
    };
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

  it('2b. (#P4-B) within_preference=true で「ご希望の範囲内」バッジ表示', () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSuggestion({ within_preference: true })]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    expect(screen.getByTestId('improvement-within-preference')).toHaveTextContent('ご希望の範囲内');
  });

  it('2c. (#P4-B) within_preference=false のとき希望内バッジは出ない', () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSuggestion({ within_preference: false })]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    expect(screen.queryByTestId('improvement-within-preference')).not.toBeInTheDocument();
  });

  it('2d. (#P4-B) swap: cp.within_preference=true で「◯◯様もご希望の範囲内」バッジ表示', () => {
    const swap = makeSwapSuggestion();
    swap.within_preference = true;
    swap.swap_counterpart = { ...swap.swap_counterpart!, within_preference: true };
    mocks.suggestionsResult = {
      data: makeResponse([swap]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    expect(screen.getByTestId('improvement-within-preference')).toBeInTheDocument();
    expect(screen.getByTestId('improvement-swap-counterpart-within-preference')).toHaveTextContent(
      '佐藤 花子 様もご希望の範囲内',
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
      data: makeResponse([
        makeSuggestion({ candidate: { ...makeSuggestion().candidate, weekday: 0 } }),
      ]),
      isLoading: false,
      isError: false,
    };
    renderSection();

    // Wave U-2: 採用ボタン → 反映先の確認ダイアログ → (既定 A) 反映する.
    await userEvent.click(screen.getByTestId('improvement-adopt-button'));
    await waitFor(() => expect(screen.getByTestId('move-confirm-dialog')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('move-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const vars = mocks.confirmMutate.mock.calls[0][0] as {
      patientId: string;
      body: { mode: string; items: { weekday: number }[]; change_scope?: string };
    };
    expect(vars.patientId).toBe(PATIENT.id);
    expect(vars.body.mode).toBe('normal');
    expect(vars.body.items[0]?.weekday).toBe(0);
    // Wave U-2: 既定 A = 型 + 今週即反映.
    expect(vars.body.change_scope).toBe('pattern_and_week');

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
    await waitFor(() => expect(screen.getByTestId('move-confirm-dialog')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('move-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const body = (
      mocks.confirmMutate.mock.calls[0][0] as {
        body: { items: Array<{ weekday: number; is_pinned?: boolean; movability?: string }> };
      }
    ).body;
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

  it('5b. (#P4-C) locked 内訳は「可動域が完全固定N件」と表示 (ピン留めと混同防止)', () => {
    mocks.suggestionsResult = {
      data: makeResponse([], { locked: 3 }),
      isLoading: false,
      isError: false,
    };
    renderSection();
    const empty = screen.getByTestId('improvement-suggestions-empty');
    expect(empty).toHaveTextContent('可動域が完全固定3件');
  });

  it('6. fetch エラー時はエラーテキストのみ (セクションは生きる)', () => {
    mocks.suggestionsResult = { data: undefined, isLoading: false, isError: true };
    renderSection();
    expect(screen.getByTestId('improvement-suggestions-error')).toBeInTheDocument();
    // セクション見出しは残る (dialog 全体は死なない).
    expect(screen.getByTestId('improvement-suggestions-section')).toBeInTheDocument();
  });

  it('7. canEdit=false で採用/見送りボタンを出さない', () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSuggestion()]),
      isLoading: false,
      isError: false,
    };
    renderSection(false);
    expect(screen.queryByTestId('improvement-adopt-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('improvement-dismiss-button')).not.toBeInTheDocument();
  });

  it('8. swap カード: ◯◯様と入れ替えヘッダ + 双方向の移動表示', () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSwapSuggestion()]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    expect(screen.getByTestId('improvement-swap-header')).toHaveTextContent(
      '佐藤 花子 様と入れ替え',
    );
    // W3 UI改善: チップ型の双方向表示 (患者名 + from/to チップ)。
    // X (表示中患者) の移動: 月09:00 → 月14:00.
    const moveX = screen.getByTestId('improvement-swap-move-x');
    expect(moveX).toHaveTextContent('中尾 要太 様');
    expect(moveX).toHaveTextContent('月 09:00');
    expect(moveX).toHaveTextContent('月 14:00');
    // Y (counterpart) の移動: 月14:00 → 月09:00.
    const moveY = screen.getByTestId('improvement-swap-move-y');
    expect(moveY).toHaveTextContent('佐藤 花子 様');
    expect(moveY).toHaveTextContent('月 14:00');
    expect(moveY).toHaveTextContent('月 09:00');
    // 効果は move カードと同じ主役表示.
    expect(screen.getByTestId('improvement-effect')).toHaveTextContent('−18分/週（−2.1km/週）');
  });

  it('9. swap カード: Y 側 (counterpart) の要確認バッジを患者名入りで出す', () => {
    mocks.suggestionsResult = {
      data: makeResponse([
        makeSwapSuggestion({
          requires_patient_confirmation: false,
          swap_counterpart: {
            ...makeSwapSuggestion().swap_counterpart!,
            requires_patient_confirmation: true,
          },
        }),
      ]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    expect(screen.getByTestId('improvement-swap-counterpart-confirmation')).toHaveTextContent(
      '佐藤 花子 様の可動域未設定',
    );
    // X 側は未設定なので出ない.
    expect(screen.queryByTestId('improvement-requires-confirmation')).not.toBeInTheDocument();
  });

  it('10. swap 採用 → 確認ダイアログ → apply-swap payload (a/b 対応・course 解決)', async () => {
    // 候補拠点の course-templates を注入し、a_new.course_template_id を resolver で解決させる.
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
        dataUpdatedAt: 1,
        status: 'success',
        isLoading: false,
      },
    ];
    mocks.suggestionsResult = {
      data: makeResponse([makeSwapSuggestion()]),
      isLoading: false,
      isError: false,
    };
    renderSection();

    // 採用 → 確認ダイアログが開く (即 mutate しない).
    await userEvent.click(screen.getByTestId('improvement-adopt-button'));
    expect(mocks.applySwapMutate).not.toHaveBeenCalled();
    expect(screen.getByTestId('swap-confirm-dialog')).toBeInTheDocument();

    // 確認 → apply-swap 実行.
    await userEvent.click(screen.getByTestId('swap-confirm-apply'));
    await waitFor(() => expect(mocks.applySwapMutate).toHaveBeenCalledTimes(1));

    const req = mocks.applySwapMutate.mock.calls[0][0] as {
      patient_a_id: string;
      patient_b_id: string;
      a_new: { weekday: number; start_time: string; course_template_id?: string };
      b_new: { weekday: number; start_time: string; course_template_id?: string };
      iso_year: number;
      iso_week: number;
    };
    // a = 表示中患者 X, b = counterpart Y.
    expect(req.patient_a_id).toBe(PATIENT.id);
    expect(req.patient_b_id).toBe('33333333-3333-4333-8333-333333333333');
    // a_new = candidate 由来 (X → Y の旧枠) + course 解決.
    expect(req.a_new).toMatchObject({
      weekday: 0,
      start_time: '14:00',
      course_template_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    });
    // b_new = counterpart.new_* (Y → X の旧枠). course は解決不能なので省略.
    expect(req.b_new).toEqual({ weekday: 0, start_time: '09:00' });
    expect(req.iso_year).toBe(2026);
    expect(req.iso_week).toBe(27);

    // 成功でカードが消える.
    await waitFor(() =>
      expect(screen.queryByTestId('improvement-adopt-button')).not.toBeInTheDocument(),
    );
  });

  it('11. swap 見送り → DismissReasonDialog を kind=swap で開き、day_immovable でも昇格確認を出さず即 dismiss', async () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSwapSuggestion()]),
      isLoading: false,
      isError: false,
    };
    mocks.dismissMutate.mockImplementation((_vars: unknown, opts: { onSuccess?: () => void }) =>
      opts.onSuccess?.(),
    );
    renderSection();

    await userEvent.click(screen.getByTestId('improvement-dismiss-button'));
    expect(screen.getByTestId('dismiss-reason-dialog')).toBeInTheDocument();

    // day_immovable を選んで「見送る」→ swap では昇格ステップを挟まず即 POST.
    await userEvent.click(screen.getByLabelText('この曜日は動かせない'));
    await userEvent.click(screen.getByTestId('dismiss-reason-next'));

    // 昇格確認は出ない.
    expect(screen.queryByTestId('dismiss-promote-confirm')).not.toBeInTheDocument();
    await waitFor(() => expect(mocks.dismissMutate).toHaveBeenCalledTimes(1));
    const body = mocks.dismissMutate.mock.calls[0][0] as {
      kind: string;
      promote_movability: boolean;
    };
    expect(body.kind).toBe('swap');
    expect(body.promote_movability).toBe(false);
  });

  it('12. 未知 kind の要素は静かに除外され、既知カードは表示される (寛容化の施錠)', () => {
    const known = makeSuggestion();
    // BE が将来返しうる未知 kind の要素 (zod で除外される).
    const unknown = {
      ...makeSuggestion(),
      kind: 'future_kind',
    } as unknown as ImprovementSuggestion;
    const parsed = improvementSuggestionsResponseSchema.parse({
      patient_id: PATIENT.id,
      iso_year: 2026,
      iso_week: 27,
      suggestions: [known, unknown],
      filtered_summary: {
        pinned: 0,
        locked: 0,
        no_current_visit: 0,
        dismissed: 0,
        below_threshold: 0,
        day_restricted: 0,
      },
    });
    // 未知要素は落ち、既知 1 件のみ残る.
    expect(parsed.suggestions).toHaveLength(1);
    expect(parsed.suggestions[0]?.kind).toBe('time_change');

    mocks.suggestionsResult = { data: parsed, isLoading: false, isError: false };
    renderSection();
    // 既存カードは表示される (セクション全滅しない).
    expect(screen.getByTestId('improvement-effect')).toBeInTheDocument();
  });
});

describe('CourseMoveTimeline (UI 統一)', () => {
  it('source_course 付きの提案はカード下にコースタイムラインを描画する', () => {
    const withSnapshot = makeSuggestion({
      source_course: {
        office_id: '11111111-1111-4111-8111-111111111111',
        weekday: 0,
        course_code: 'A',
        course_label: '稲A',
        staff_name: '熊澤 妙子',
        visits: [
          {
            patient_id: PATIENT.id,
            patient_name: '中尾 要太',
            start_time: '09:00',
            end_time: '09:30',
          },
          {
            patient_id: '44444444-4444-4444-8444-444444444444',
            patient_name: '別の 患者',
            start_time: '11:00',
            end_time: '11:30',
          },
        ],
      },
      destination_course: null, // 同一コース内 → 1 枚のタイムライン
    });
    mocks.suggestionsResult = {
      data: makeResponse([withSnapshot]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    const timeline = screen.getByTestId('course-move-timeline-single');
    // 移動元 (打消し) 行と挿入 (← ここへ移動) 行が同居する.
    expect(timeline).toHaveTextContent('コース内の動き（稲A・熊澤 妙子）');
    expect(timeline).toHaveTextContent('別の 患者 様');
    expect(timeline).toHaveTextContent('← ここへ移動');
  });

  it('source_course が無い (旧 BE) 提案はタイムラインを出さない', () => {
    mocks.suggestionsResult = {
      data: makeResponse([makeSuggestion()]),
      isLoading: false,
      isError: false,
    };
    renderSection();
    expect(screen.queryByTestId('course-move-timeline-single')).not.toBeInTheDocument();
    expect(screen.queryByTestId('course-move-timeline-pair')).not.toBeInTheDocument();
  });
});
