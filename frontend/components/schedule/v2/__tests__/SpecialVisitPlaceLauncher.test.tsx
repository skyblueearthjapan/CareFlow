/**
 * SpecialVisitPlaceLauncher — 特別訪問週間の「配置先を決める…」ラッパ。
 *
 * 検証 (`special-visit-week-ux-investigation-2026-09-07.md` §3-2 2):
 *   ① ＋訪問モーダルを **患者・日付・反映先「新しく 1 件追加」固定** で開く
 *   ② 登録できたら、その訪問 id で place (`visit_id` モード) を呼び ● にする
 *   ③ 登録に失敗したら place は呼ばず、モーダルは開いたまま (reject)
 *   ④ 2 名体制 (2 件) でも紐付けは先頭 1 件だけ
 *   ⑤ 紐付けだけ失敗したら再試行させない (二重登録の防止)
 *   ⑥ ● の入れ替えは **新しい訪問ができてから** 取消 → 作り直し → 紐付け
 *   ⑦ NG スタッフ / 性別制限の 422 は確認 → acknowledge 再送 (盤面と同じ)
 *
 * 通信はすべてモック (BE は叩かない)。＋訪問モーダル本体の挙動は
 * `cockpit/__tests__/AddVisitAnywhereDialog.test.tsx` が担保する。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { ApiError } from '@/lib/api-client';
import type { AddVisitAnywhereDialogProps } from '../cockpit/AddVisitAnywhereDialog';
import type { AddVisitPlan } from '@/lib/scheduling/addVisitPlan';
import type { AddVisitExecResult } from '@/lib/scheduling/addVisitExecutor';

const PATIENT_ID = '11111111-1111-4111-8111-111111111111';
const PERIOD_ID = 'period-1';
const MARK_ID = 'mark-pool';
const DATE = '2026-09-15';

const { holder, mockToast } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  holder: {
    props: null as AddVisitAnywhereDialogProps | null,
    execute: vi.fn(),
    place: vi.fn(),
    deleteMark: vi.fn(),
    createMark: vi.fn(),
    invalidate: vi.fn(),
    /** 入れ替えの順序を見るための呼び出しログ。 */
    order: [] as string[],
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('../cockpit/AddVisitAnywhereDialog', () => ({
  AddVisitAnywhereDialog: (props: AddVisitAnywhereDialogProps) => {
    holder.props = props;
    return (
      <div data-testid="ava-mock">
        <button
          type="button"
          data-testid="ava-mock-execute"
          onClick={() => {
            void props.onExecute(PLAN).catch(() => undefined);
          }}
        >
          登録
        </button>
      </div>
    );
  },
}));

vi.mock('@/lib/scheduling/addVisitExecutor', () => ({
  executeAddVisitPlan: (...args: unknown[]) => holder.execute(...args),
}));

vi.mock('@/lib/queries/specialVisitWeek', () => ({
  usePlaceSpecialMark: () => ({ mutateAsync: holder.place }),
  useDeleteSpecialVisitMark: () => ({ mutateAsync: holder.deleteMark }),
  useCreateSpecialVisitMark: () => ({ mutateAsync: holder.createMark }),
}));

vi.mock('@/lib/queries/patients', () => ({
  usePatient: () => ({
    data: {
      id: PATIENT_ID,
      name: '山田 太郎',
      status: 'active',
      primary_office_id: 'office-1',
      lat: 35.6,
      lng: 140.1,
      weekly_pattern: { service_minutes: 45 },
      sex_restriction: null,
      requires_multiple_staff: false,
    },
  }),
}));

vi.mock('@/lib/queries/offices', () => ({
  useOffices: () => ({ offices: [{ id: 'office-1', name: '稲毛' }] }),
}));

vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({ data: [{ id: 'staff-1', name: '熊澤' }] }),
}));

vi.mock('@/lib/queries/fieldBoard', () => ({
  useProposeSlots: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('@/lib/queries/place_and_fix', () => ({
  usePlaceAndFix: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('@/lib/queries/visits', () => ({
  useCreateVisit: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

vi.mock('@tanstack/react-query', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useQueries: () => [],
  useQueryClient: () => ({ invalidateQueries: holder.invalidate }),
}));

import { SpecialVisitPlaceLauncher } from '../SpecialVisitPlaceLauncher';

const PLAN: AddVisitPlan = {
  patientId: PATIENT_ID,
  items: [
    {
      date: DATE,
      isoYear: 2026,
      isoWeek: 38,
      weekday: 1,
      startHM: '12:00',
      minutes: 45,
      officeId: 'office-1',
      courseTemplateId: 'tpl-a',
      courseLabel: '稲毛A',
      isM: false,
      isOtherOffice: false,
      staffCount: 1,
      partnerCourseTemplateId: null,
      reason: null,
      scope: 'new',
      sourceVisit: null,
      noCandidateReason: null,
    },
  ],
};

function execResult(over: Partial<AddVisitExecResult> = {}): AddVisitExecResult {
  return {
    done: [{ item: PLAN.items[0]!, kind: 'new', visitIds: ['visit-new'] }],
    skipped: [],
    ordered: PLAN.items,
    ...over,
  };
}

type LauncherOverrides = Partial<React.ComponentProps<typeof SpecialVisitPlaceLauncher>>;

function renderLauncher(overrides: LauncherOverrides = {}) {
  const onPlaced = overrides.onPlaced ?? vi.fn();
  const onOpenChange = overrides.onOpenChange ?? vi.fn();
  render(
    <SpecialVisitPlaceLauncher
      open
      onOpenChange={onOpenChange}
      patientId={PATIENT_ID}
      periodId={PERIOD_ID}
      markId={MARK_ID}
      date={DATE}
      isoYear={2026}
      isoWeek={38}
      weekday={1}
      {...overrides}
      onPlaced={onPlaced}
    />,
  );
  return { onPlaced, onOpenChange };
}

beforeEach(() => {
  vi.clearAllMocks();
  holder.props = null;
  holder.order = [];
  holder.execute.mockImplementation(async () => {
    holder.order.push('execute');
    return execResult();
  });
  holder.place.mockImplementation(async () => {
    holder.order.push('place');
    return { mark: null, visit_id: 'visit-new' };
  });
  holder.deleteMark.mockImplementation(async () => {
    holder.order.push('delete');
  });
  holder.createMark.mockImplementation(async () => {
    holder.order.push('create');
    return { id: 'mark-fresh' };
  });
});

describe('SpecialVisitPlaceLauncher', () => {
  it('① 患者・日付・反映先を固定して ＋訪問モーダルを開く', () => {
    renderLauncher();

    expect(screen.getByTestId('ava-mock')).toBeInTheDocument();
    const props = holder.props!;
    expect(props.initial).toEqual({
      patientId: PATIENT_ID,
      dates: [DATE],
      lockedScope: 'new',
      lockedDates: true,
    });
    expect(props.isoYear).toBe(2026);
    expect(props.isoWeek).toBe(38);
    // 患者は 1 人だけ (プールの印は使わない)。
    expect(props.patients.map((p) => p.id)).toEqual([PATIENT_ID]);
    expect(props.patients[0]!.service_minutes).toBe(45);
    expect(props.poolPatientIds.size).toBe(0);
  });

  it('② 登録できたら visit_id でマークへ紐付ける', async () => {
    const { onPlaced } = renderLauncher();

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    await waitFor(() =>
      expect(holder.place).toHaveBeenCalledWith({
        markId: MARK_ID,
        payload: { visit_id: 'visit-new' },
      }),
    );
    expect(mockToast.success).toHaveBeenCalledWith('9/15 に配置しました（この週のみ）');
    expect(onPlaced).toHaveBeenCalled();
  });

  it('③ 登録に失敗したら place は呼ばない', async () => {
    holder.execute.mockResolvedValue(
      execResult({
        done: [],
        failed: {
          item: PLAN.items[0]!,
          kind: 'error',
          index: 0,
          error: new Error('boom'),
          message: '入れられませんでした',
          detail: null,
        },
      }),
    );
    renderLauncher();

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(holder.place).not.toHaveBeenCalled();
  });

  it('④ 2 名体制で 2 件できても紐付けは先頭 1 件だけ', async () => {
    holder.execute.mockResolvedValue(
      execResult({
        done: [{ item: PLAN.items[0]!, kind: 'new', visitIds: ['visit-1', 'visit-2'] }],
      }),
    );
    renderLauncher();

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    await waitFor(() => expect(holder.place).toHaveBeenCalledTimes(1));
    expect(holder.place).toHaveBeenCalledWith({
      markId: MARK_ID,
      payload: { visit_id: 'visit-1' },
    });
  });

  it('⑤ 紐付けだけ失敗したら再試行させず、重複注意を出して閉じる', async () => {
    holder.place.mockRejectedValue(new Error('link failed'));
    const { onOpenChange } = renderLauncher();

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(
        '訪問は登録できましたが追加枠への紐付けに失敗しました。盤面に予定は残っています（重複登録に注意）',
      ),
    );
    // 盤面・カレンダーは失効させる (place の onSuccess が走らないため)。
    expect(holder.invalidate).toHaveBeenCalledWith({ queryKey: ['special-visit'] });
    expect(holder.invalidate).toHaveBeenCalledWith({ queryKey: ['visits'] });
    // 二重登録を招く再試行はさせない = モーダルを閉じる。
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(mockToast.success).not.toHaveBeenCalled();
  });

  it('⑥ 入れ替えは 訪問の登録 → 取消 → 作り直し → 紐付け の順で行う', async () => {
    const { onPlaced } = renderLauncher({
      markId: null,
      replacingMarkId: 'mark-placed',
      replacingVisitId: 'visit-9',
    });

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    await waitFor(() => expect(holder.place).toHaveBeenCalled());
    // 先に壊さない: 新しい訪問ができてから取り消す。
    expect(holder.order).toEqual(['execute', 'delete', 'create', 'place']);
    expect(holder.deleteMark).toHaveBeenCalledWith({ markId: 'mark-placed', force: true });
    expect(holder.createMark).toHaveBeenCalledWith({
      periodId: PERIOD_ID,
      payload: { iso_year: 2026, iso_week: 38, weekday: 1 },
    });
    // 紐付け先は作り直した追加枠。
    expect(holder.place).toHaveBeenCalledWith({
      markId: 'mark-fresh',
      payload: { visit_id: 'visit-new' },
    });
    expect(onPlaced).toHaveBeenCalled();
  });

  it('⑥-b 入れ替えの登録に失敗したら、いまの配置は消さない', async () => {
    holder.execute.mockResolvedValue(
      execResult({
        done: [],
        failed: {
          item: PLAN.items[0]!,
          kind: 'error',
          index: 0,
          error: new Error('boom'),
          message: '入れられませんでした',
          detail: null,
        },
      }),
    );
    renderLauncher({ markId: null, replacingMarkId: 'mark-placed', replacingVisitId: 'visit-9' });

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(holder.deleteMark).not.toHaveBeenCalled();
    expect(holder.createMark).not.toHaveBeenCalled();
    expect(holder.place).not.toHaveBeenCalled();
  });

  it('⑥-c 入れ替えで同じ時刻のままなら、API を呼ばずに止める', async () => {
    renderLauncher({
      markId: null,
      replacingMarkId: 'mark-placed',
      replacingVisitId: 'visit-9',
      // PLAN の開始時刻と同じ = (患者・日付・開始時刻) が重複する。
      replacingStartHM: '12:00',
    });

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(
        '同じ時刻のままでは入れ替えられません。時刻を変えるか、いったん「配置を取り消す」で消してから付け直してください',
      ),
    );
    // 1 本も API を叩かない (モーダルは開いたまま = 時刻を変えてやり直せる)。
    expect(holder.order).toEqual([]);
    expect(holder.execute).not.toHaveBeenCalled();
    expect(holder.deleteMark).not.toHaveBeenCalled();
    expect(holder.place).not.toHaveBeenCalled();
  });

  it('⑥-d 入れ替え中の 409 も同じ案内に寄せる', async () => {
    holder.execute.mockImplementation(async () => {
      holder.order.push('execute');
      return execResult({
        done: [],
        failed: {
          item: PLAN.items[0]!,
          kind: 'error',
          index: 0,
          error: new ApiError('conflict', 409, {}),
          message: 'すでに予定があります',
          detail: null,
        },
      });
    });
    renderLauncher({
      markId: null,
      replacingMarkId: 'mark-placed',
      replacingVisitId: 'visit-9',
      replacingStartHM: '11:00',
    });

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(
        '同じ時刻のままでは入れ替えられません。時刻を変えるか、いったん「配置を取り消す」で消してから付け直してください',
      ),
    );
    expect(holder.deleteMark).not.toHaveBeenCalled();
  });

  it('⑦ NG/性別の 422 は確認ダイアログ → acknowledge 付きで再送', async () => {
    const constraintError = new ApiError('constraint', 422, {
      detail: {
        code: 'constraint_confirmation_required',
        warnings: [
          {
            kind: 'ng_staff',
            patient_id: PATIENT_ID,
            patient_name: '山田 太郎',
            staff_id: 'staff-1',
            staff_name: '熊澤',
            note: null,
          },
        ],
      },
    });
    holder.execute
      .mockImplementationOnce(async () => {
        holder.order.push('execute');
        return execResult({
          done: [],
          failed: {
            item: PLAN.items[0]!,
            kind: 'constraint',
            index: 0,
            error: constraintError,
            message: '確認が必要です',
            detail: null,
          },
        });
      })
      .mockImplementationOnce(async () => {
        holder.order.push('execute-ack');
        return execResult();
      });
    renderLauncher();

    fireEvent.click(screen.getByTestId('ava-mock-execute'));

    // 1 回目は止まり、確認ダイアログが出る (place はまだ)。
    await screen.findByTestId('constraint-override-confirm');
    expect(holder.place).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('constraint-override-ok'));

    await waitFor(() => expect(holder.place).toHaveBeenCalledTimes(1));
    // 2 回目は止まった 1 件だけを acknowledge して再開する。
    expect(holder.execute).toHaveBeenCalledTimes(2);
    const secondOpts = holder.execute.mock.calls[1]![2] as {
      startIndex?: number;
      acknowledgeIndex?: number;
    };
    expect(secondOpts.acknowledgeIndex).toBe(0);
    expect(secondOpts.startIndex).toBe(0);
    expect(holder.order).toEqual(['execute', 'execute-ack', 'place']);
  });
});
