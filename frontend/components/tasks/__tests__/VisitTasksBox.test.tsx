/** 訪問モニターの詳細の「事務所からのやること」(patient-tasks-design §3)。 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import type { PatientTaskRead } from '@/lib/schemas/patient-task';

const completeMutate = vi.fn();
const reopenMutate = vi.fn();
const cancelMutate = vi.fn();
const createMutate = vi.fn();
let tasks: PatientTaskRead[] = [];

vi.mock('@/lib/queries/tasks', () => ({
  useVisitTasks: () => ({ data: tasks, isLoading: false, isError: false }),
  useCompleteTask: () => ({ mutate: completeMutate, isPending: false }),
  useReopenTask: () => ({ mutate: reopenMutate, isPending: false }),
  useCancelTask: () => ({ mutate: cancelMutate, isPending: false }),
  useCreateTasks: () => ({ mutate: createMutate, isPending: false }),
  useTaskItemTypes: () => ({
    data: [{ id: 'i1', name: '項目あ', sort_order: 1, is_active: true }],
  }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { VisitTasksBox } from '../VisitTasksBox';

function makeTask(over: Partial<PatientTaskRead> = {}): PatientTaskRead {
  return {
    id: 't1',
    patient_id: '00000000-0000-0000-0000-000000000001',
    patient_name: '岡本 翔',
    start_date: '2026-10-29',
    item_type_id: '00000000-0000-0000-0000-0000000000a1',
    item_type_name: '項目あ',
    is_message: false,
    memo: '封筒を持っていく',
    created_by: null,
    created_by_name: '事務 太郎',
    created_at: '2026-11-01T00:00:00Z',
    status: 'open',
    overdue: true,
    carried_over: true,
    done_at: null,
    done_by_user_id: null,
    done_by_staff_id: null,
    done_by_name: null,
    done_visit_id: null,
    cancelled_at: null,
    next_visit: null,
    done_visit: null,
    can_reopen: false,
    ...over,
  };
}

const PATIENT = '00000000-0000-0000-0000-000000000001';

describe('VisitTasksBox', () => {
  beforeEach(() => {
    completeMutate.mockReset();
    reopenMutate.mockReset();
    cancelMutate.mockReset();
    createMutate.mockReset();
  });

  it('持ち越し・期日を過ぎた印と、付けた人を出し、その訪問で完了にする', () => {
    tasks = [makeTask()];
    render(
      <VisitTasksBox
        visitId="v1"
        visitDate="2026-11-02"
        patientId={PATIENT}
        patientName="岡本 翔"
      />,
    );
    const row = screen.getByTestId('visit-task-t1');
    expect(row.textContent).toContain('持ち越し');
    expect(row.textContent).toContain('期日 10/29 を過ぎています');
    expect(row.textContent).toContain('付けた人 事務 太郎');
    fireEvent.click(screen.getByRole('button', { name: '完了にする' }));
    expect(completeMutate).toHaveBeenCalledWith(
      { taskId: 't1', visitId: 'v1' },
      expect.any(Object),
    );
  });

  it('取り消すは確かめてから', () => {
    tasks = [makeTask()];
    const confirm = vi
      .spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    render(
      <VisitTasksBox
        visitId="v1"
        visitDate="2026-11-02"
        patientId={PATIENT}
        patientName="岡本 翔"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '取り消す' }));
    expect(cancelMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '取り消す' }));
    expect(cancelMutate).toHaveBeenCalledWith('t1', expect.any(Object));
    confirm.mockRestore();
  });

  it('完了したものは完了した人と「完了を戻す」', () => {
    tasks = [
      makeTask({
        status: 'done',
        overdue: false,
        done_by_name: '佐藤 花子',
        done_at: '2026-11-02T00:31:00Z',
        can_reopen: true,
      }),
    ];
    render(
      <VisitTasksBox
        visitId="v1"
        visitDate="2026-11-02"
        patientId={PATIENT}
        patientName="岡本 翔"
      />,
    );
    expect(screen.getByTestId('visit-task-t1').textContent).toContain('完了 佐藤 花子 11/2 09:31');
    fireEvent.click(screen.getByRole('button', { name: '完了を戻す' }));
    expect(reopenMutate).toHaveBeenCalledWith('t1', expect.any(Object));
  });

  it('付けるときの日付はその訪問の日が既定・項目か伝言が要る', () => {
    tasks = [];
    render(
      <VisitTasksBox
        visitId="v1"
        visitDate="2026-11-02"
        patientId={PATIENT}
        patientName="岡本 翔"
      />,
    );
    expect(screen.getByTestId('visit-tasks-empty')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('visit-tasks-add-open'));
    expect(screen.getByTestId('visit-tasks-date')).toHaveValue('2026-11-02');
    const submit = screen.getByTestId('visit-tasks-add-submit');
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '項目あ' }));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    expect(createMutate).toHaveBeenCalledWith(
      { patient_id: PATIENT, start_date: '2026-11-02', item_type_ids: ['i1'], memo: null },
      expect.any(Object),
    );
  });

  it('二度押ししても付けるのは 1 回だけ・終わったら次を送れる', () => {
    tasks = [];
    let settle: (() => void) | undefined;
    createMutate.mockImplementation((_p, opts: { onSettled: () => void }) => {
      settle = opts.onSettled;
    });
    render(
      <VisitTasksBox
        visitId="v1"
        visitDate="2026-11-02"
        patientId={PATIENT}
        patientName="岡本 翔"
      />,
    );
    fireEvent.click(screen.getByTestId('visit-tasks-add-open'));
    fireEvent.click(screen.getByRole('button', { name: '項目あ' }));
    const submit = screen.getByTestId('visit-tasks-add-submit');
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(createMutate).toHaveBeenCalledTimes(1);
    settle?.();
    fireEvent.click(submit);
    expect(createMutate).toHaveBeenCalledTimes(2);
  });
});
