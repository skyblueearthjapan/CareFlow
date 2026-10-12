/**
 * DismissReasonDialog — 見送り理由 + 可動域昇格分岐の単体テスト (P2-C).
 *
 * 検証:
 *   1. time_immovable だけ「見送る」後に昇格確認ステップを挟む (day_immovable は記録のみ).
 *   2. 昇格「はい」→ promote_movability=true で dismiss POST.
 *   3. 昇格「いいえ」→ promote_movability=false で dismiss POST.
 *   4. staff_relation は昇格ステップを挟まず即 POST (promote=false).
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  mocks: { dismissMutate: vi.fn() },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('@/lib/queries/improvementSuggestions', () => ({
  useDismissSuggestion: () => ({ mutate: mocks.dismissMutate, isPending: false }),
}));

import { DismissReasonDialog } from '../DismissReasonDialog';
import type { ImprovementSuggestion } from '@/lib/schemas/v2/improvementSuggestion';

const PATIENT_ID = '22222222-2222-4222-8222-222222222222';

const SUGGESTION: ImprovementSuggestion = {
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
  changes: { changes: [], unchanged: [] },
  staff_warnings: [],
  feasibility_basis: 'pfv',
  requires_patient_confirmation: false,
};

function renderDialog() {
  const onClose = vi.fn();
  const onDismissed = vi.fn();
  render(
    <DismissReasonDialog
      open
      suggestion={SUGGESTION}
      patientId={PATIENT_ID}
      onClose={onClose}
      onDismissed={onDismissed}
    />,
  );
  return { onClose, onDismissed };
}

describe('DismissReasonDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 既定は onSuccess を即発火する mutate 実装.
    mocks.dismissMutate.mockImplementation((_body: unknown, opts: { onSuccess?: () => void }) =>
      opts.onSuccess?.(),
    );
  });

  it('0. day_immovable は昇格確認を挟まず promote=false で即 POST', async () => {
    renderDialog();
    await userEvent.click(screen.getByTestId('dismiss-reason-next'));
    expect(screen.queryByTestId('dismiss-promote-confirm')).not.toBeInTheDocument();
    await waitFor(() => expect(mocks.dismissMutate).toHaveBeenCalledTimes(1));
    expect(mocks.dismissMutate.mock.calls[0][0]).toMatchObject({
      reason: 'day_immovable',
      promote_movability: false,
    });
  });

  it('1+2. time_immovable → 昇格確認 → はい で promote_movability=true POST', async () => {
    const { onClose, onDismissed } = renderDialog();

    await userEvent.click(screen.getByLabelText('この時刻は動かせない'));
    await userEvent.click(screen.getByTestId('dismiss-reason-next'));

    // 昇格確認ステップが出る.
    expect(screen.getByTestId('dismiss-promote-confirm')).toBeInTheDocument();
    // どちらの理由も「完全固定 (曜日・時刻を固定)」として記録する旧 2 段階の言葉は出さない。
    expect(screen.getByTestId('dismiss-promote-confirm')).toHaveTextContent('完全固定');
    expect(screen.getByTestId('dismiss-promote-confirm')).toHaveTextContent('曜日・時刻を固定');
    expect(screen.getByTestId('dismiss-promote-confirm')).not.toHaveTextContent('曜日固定」');

    // 「はい」→ promote true.
    await userEvent.click(screen.getByTestId('dismiss-promote-yes'));

    await waitFor(() => expect(mocks.dismissMutate).toHaveBeenCalledTimes(1));
    const body = mocks.dismissMutate.mock.calls[0][0];
    expect(body).toMatchObject({
      patient_id: PATIENT_ID,
      kind: 'time_change',
      target_weekday: 0,
      reason: 'time_immovable',
      promote_movability: true,
    });
    expect(onDismissed).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('3. time_immovable → 昇格確認 → いいえ で promote_movability=false POST', async () => {
    renderDialog();
    await userEvent.click(screen.getByLabelText('この時刻は動かせない'));
    await userEvent.click(screen.getByTestId('dismiss-reason-next'));
    await userEvent.click(screen.getByTestId('dismiss-promote-no'));

    await waitFor(() => expect(mocks.dismissMutate).toHaveBeenCalledTimes(1));
    expect(mocks.dismissMutate.mock.calls[0][0]).toMatchObject({
      reason: 'time_immovable',
      promote_movability: false,
    });
  });

  it('4. staff_relation は昇格ステップを挟まず即 POST (promote=false)', async () => {
    renderDialog();

    // staff_relation を選択.
    await userEvent.click(screen.getByLabelText('スタッフとの関係'));
    await userEvent.click(screen.getByTestId('dismiss-reason-next'));

    // 昇格確認は出ない.
    expect(screen.queryByTestId('dismiss-promote-confirm')).not.toBeInTheDocument();

    await waitFor(() => expect(mocks.dismissMutate).toHaveBeenCalledTimes(1));
    expect(mocks.dismissMutate.mock.calls[0][0]).toMatchObject({
      reason: 'staff_relation',
      promote_movability: false,
    });
  });
});
