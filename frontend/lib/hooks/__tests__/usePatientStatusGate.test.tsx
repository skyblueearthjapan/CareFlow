/**
 * usePatientStatusGate のユニットテスト
 * (`docs/plans/patient-status-schedule-design-2026-09-09.md` §7-6 FE-2)。
 *
 * 固定したいこと:
 *   1. ステータスが変わらなければ **素通り**（従来どおり PATCH 1 本）。
 *   2. 非稼働どうしの移動 (入院中 → 一時休止) も素通り (direction=none)。
 *   3. 稼働中 ⇄ 非稼働 はダイアログを開いて保存を保留し、確定後に
 *      `omitStatus: true` で本来の保存を実行する（PATCH が status を再送しない）。
 *   4. キャンセルは静かに終了（保存は走らず、例外も投げない）。
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

import { toast } from '@/components/ui/sonner';
import { emptyPatientFormValues, type PatientFormValues } from '@/lib/schemas/patient';
import { statusChangeResultSchema } from '@/lib/schemas/patientStatus';

import { formatStatusChangeMessage, usePatientStatusGate } from '../usePatientStatusGate';

const PID = '00000000-0000-0000-0000-000000000001';

function values(status: PatientFormValues['status']): PatientFormValues {
  return { ...emptyPatientFormValues, name: '小湊', code: 'P001', status };
}

function result(over: Record<string, unknown> = {}) {
  return statusChangeResultSchema.parse({
    patient: {
      id: PID,
      code: 'P001',
      name: '小湊',
      status: 'admitted',
      created_at: '2026-09-01T00:00:00',
      updated_at: '2026-09-09T00:00:00',
    },
    direction: 'deactivate',
    cancelled_count: 9,
    ...over,
  });
}

function setup(initialStatus: string) {
  return renderHook(() =>
    usePatientStatusGate({ patientId: PID, patientName: '小湊', initialStatus }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('usePatientStatusGate — 素通り', () => {
  it('ステータスが変わらなければ確認せずそのまま保存する', async () => {
    const { result: hook } = setup('active');
    const submit = vi.fn().mockResolvedValue(undefined);

    await act(async () => {
      await hook.current.wrapSubmit(submit)(values('active'));
    });

    expect(submit).toHaveBeenCalledTimes(1);
    expect(submit.mock.calls[0][1]).toEqual({ omitStatus: false });
    expect(hook.current.dialogProps.open).toBe(false);
  });

  it('非稼働どうしの移動 (入院中 → 一時休止) も素通りする', async () => {
    const { result: hook } = setup('admitted');
    const submit = vi.fn().mockResolvedValue(undefined);

    await act(async () => {
      await hook.current.wrapSubmit(submit)(values('suspended'));
    });

    expect(submit).toHaveBeenCalledTimes(1);
    expect(submit.mock.calls[0][1]).toEqual({ omitStatus: false });
    expect(hook.current.dialogProps.open).toBe(false);
  });
});

describe('usePatientStatusGate — 確認ダイアログ', () => {
  it('稼働中 → 入院中 はダイアログを開き、確定後に omitStatus:true で保存する', async () => {
    const { result: hook } = setup('active');
    const submit = vi.fn().mockResolvedValue(undefined);
    const next = values('admitted');

    let settled = false;
    let pending: Promise<void>;
    act(() => {
      pending = hook.current.wrapSubmit(submit)(next);
      void pending.then(() => {
        settled = true;
      });
    });

    // ダイアログが開くだけ。保存はまだ走らない。
    await waitFor(() => expect(hook.current.dialogProps.open).toBe(true));
    expect(hook.current.dialogProps.fromStatus).toBe('active');
    expect(hook.current.dialogProps.toStatus).toBe('admitted');
    expect(submit).not.toHaveBeenCalled();
    expect(settled).toBe(false);

    await act(async () => {
      hook.current.dialogProps.onDone(result());
    });

    expect(submit).toHaveBeenCalledTimes(1);
    expect(submit.mock.calls[0][0]).toBe(next);
    expect(submit.mock.calls[0][1]).toEqual({ omitStatus: true });
    await waitFor(() => expect(settled).toBe(true));
    expect(hook.current.dialogProps.open).toBe(false);
    expect((toast.success as unknown as Mock).mock.calls[0][0]).toBe(
      '入院中にしました（予定 9 件を取消）',
    );
  });

  it('確認中に保存をもう一度押しても 2 本目は走らない (再入ガード)', async () => {
    const { result: hook } = setup('active');
    const submit = vi.fn().mockResolvedValue(undefined);

    act(() => {
      void hook.current.wrapSubmit(submit)(values('admitted'));
    });
    await waitFor(() => expect(hook.current.dialogProps.open).toBe(true));

    // 2 度押し (別の保存を仕掛けようとする) は黙って捨てる。
    await act(async () => {
      await hook.current.wrapSubmit(submit)(values('cancelled'));
    });
    expect(submit).not.toHaveBeenCalled();
    expect(hook.current.dialogProps.toStatus).toBe('admitted');

    await act(async () => {
      hook.current.dialogProps.onDone(result());
    });
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it('キャンセルすると保存は走らず、静かに終了する', async () => {
    const { result: hook } = setup('active');
    const submit = vi.fn().mockResolvedValue(undefined);

    let settled = false;
    act(() => {
      void hook.current
        .wrapSubmit(submit)(values('cancelled'))
        .then(() => {
          settled = true;
        });
    });
    await waitFor(() => expect(hook.current.dialogProps.open).toBe(true));

    await act(async () => {
      hook.current.dialogProps.onCancel();
    });

    expect(submit).not.toHaveBeenCalled();
    await waitFor(() => expect(settled).toBe(true));
    expect(hook.current.dialogProps.open).toBe(false);
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('onStatusChanged を渡すと sonner ではなくそちらへ通知する', async () => {
    const onStatusChanged = vi.fn();
    const { result: hook } = renderHook(() =>
      usePatientStatusGate({
        patientId: PID,
        patientName: '小湊',
        initialStatus: 'active',
        onStatusChanged,
      }),
    );
    const submit = vi.fn().mockResolvedValue(undefined);

    act(() => {
      void hook.current.wrapSubmit(submit)(values('admitted'));
    });
    await waitFor(() => expect(hook.current.dialogProps.open).toBe(true));
    await act(async () => {
      hook.current.dialogProps.onDone(result());
    });

    expect(onStatusChanged).toHaveBeenCalledTimes(1);
    expect(onStatusChanged.mock.calls[0][0]).toBe('入院中にしました（予定 9 件を取消）');
    expect(toast.success).not.toHaveBeenCalled();
  });
});

describe('formatStatusChangeMessage', () => {
  it('非稼働化: 取消件数つき / 特別訪問週間を終了したら添える', () => {
    expect(formatStatusChangeMessage(result())).toBe('入院中にしました（予定 9 件を取消）');
    expect(formatStatusChangeMessage(result({ cancelled_count: 0 }))).toBe('入院中にしました');
    expect(
      formatStatusChangeMessage(
        result({
          special_period: {
            id: '00000000-0000-0000-0000-0000000000a1',
            action: 'end',
            cancelled_pool_marks: 25,
          },
        }),
      ),
    ).toBe('入院中にしました（予定 9 件を取消）・特別訪問週間を終了');
  });

  it('復帰: 作成件数つき', () => {
    const r = result({
      direction: 'reactivate',
      cancelled_count: 0,
      patient: {
        id: PID,
        code: 'P001',
        name: '小湊',
        status: 'active',
        created_at: '2026-09-01T00:00:00',
        updated_at: '2026-09-09T00:00:00',
      },
      regenerated: { created: 8, weeks: [] },
    });
    expect(formatStatusChangeMessage(r)).toBe('稼働中に戻しました（8 件を作成）');
  });
});
