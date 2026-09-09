/**
 * 入口ガードの「実配線」テスト (Phase 2 / 設計 §3-3)。
 *
 * ゲート本体の単体テストは `PatientNotActiveGate.test.tsx` にある。こちらは
 * **呼び出し側が `useGuardedMutation` で包むのをやめたら落ちる**ことを目的にした
 * 実コンポーネントの結線テスト。`FixedTimeEditModal` は依存が
 * `useUpdateFixedTimeMasterMutation` 1 本だけなので、盤面まるごとを立ち上げずに
 * 「422 patient_not_active → 案内ダイアログ」を通しで確かめられる。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

// Phase 1 のステータス変更ダイアログは react-query / next-auth に依存するので stub。
vi.mock('@/components/patients/PatientStatusChangeDialog', () => ({
  PatientStatusChangeDialog: (props: { open: boolean }) =>
    props.open ? <div data-testid="stub-status-dialog" /> : null,
}));

// 氏名解決 (usePatient) もネットワークに出さない。
vi.mock('@/lib/queries/patients', () => ({
  usePatient: () => ({ data: { id: 'p1', name: '小湊 花子' } }),
}));

const masterMutateAsync = vi.fn();
vi.mock('@/lib/queries/autoScheduleV2', () => ({
  useUpdateFixedTimeMasterMutation: () => ({
    mutate: vi.fn(),
    mutateAsync: masterMutateAsync,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

import { ApiError } from '@/lib/api-client';
import { FixedTimeEditModal } from '../FixedTimeEditModal';
import { PatientNotActiveGateProvider } from '../PatientNotActiveGate';
import type { V2Warning } from '@/lib/schemas/v2/autoScheduleV2';

const PATIENT_ID = '11111111-2222-3333-4444-555555555555';

const WARNING = {
  code: 'same_address_cluster',
  message: '同住所の訪問が離れています',
  patient_id: PATIENT_ID,
  patient_name: '小湊 花子',
  weekday: 0,
  current_time: '10:00',
  suggested_time: '09:30',
  time_type: '固定',
} as unknown as V2Warning;

function notActive() {
  return new ApiError('API 422 (/x)', 422, {
    detail: {
      code: 'patient_not_active',
      patient_id: PATIENT_ID,
      status: 'admitted',
      status_label: '入院中',
      can_override: true,
      message: '小湊 花子様は入院中のため予定に入れられません',
    },
  });
}

function renderModal() {
  return render(
    <PatientNotActiveGateProvider>
      <FixedTimeEditModal open onClose={vi.fn()} onSuccess={vi.fn()} warning={WARNING} />
    </PatientNotActiveGateProvider>,
  );
}

describe('FixedTimeEditModal × PatientNotActiveGate (実配線)', () => {
  beforeEach(() => {
    masterMutateAsync.mockReset();
  });

  it('update-fixed-time-master が 422 patient_not_active → 案内ダイアログが出る', async () => {
    const user = userEvent.setup();
    masterMutateAsync.mockRejectedValue(notActive());
    renderModal();

    await user.click(screen.getByTestId('fixed-time-edit-confirm'));

    expect(await screen.findByTestId('patient-not-active-gate')).toBeInTheDocument();
    expect(screen.getByTestId('patient-not-active-message')).toHaveTextContent(
      '小湊 花子様は入院中のため予定に入れられません',
    );

    // 「稼働中にして続ける」で Phase 1 の復帰ダイアログへ渡る。
    await user.click(screen.getByTestId('patient-not-active-confirm'));
    expect(await screen.findByTestId('stub-status-dialog')).toBeInTheDocument();
  });

  it('別の 422 (NG スタッフ等) は素通しでゲートを開かない', async () => {
    const user = userEvent.setup();
    masterMutateAsync.mockRejectedValue(
      new ApiError('API 422 (/x)', 422, { detail: { code: 'ng_staff', message: 'NG' } }),
    );
    renderModal();

    await user.click(screen.getByTestId('fixed-time-edit-confirm'));

    await vi.waitFor(() => expect(masterMutateAsync).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('patient-not-active-gate')).not.toBeInTheDocument();
  });
});
