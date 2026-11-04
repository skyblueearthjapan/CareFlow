/** スマホの「預かっているやること N 件」の帯 (patient-tasks-hold-first-visitor-design §3)。 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import type { PatientTaskRead } from '@/lib/schemas/patient-task';

const completeMutate = vi.fn();
let held: PatientTaskRead[] | undefined = [];

vi.mock('@/lib/queries/tasks', () => ({
  useHeldTasks: () => ({ data: held }),
  useCompleteTask: () => ({ mutate: completeMutate, isPending: false }),
}));

const toastSuccess = vi.fn();
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: (...a: unknown[]) => toastSuccess(...a), error: vi.fn() },
}));

import { HeldTasksBand } from '../HeldTasksBand';

function makeTask(over: Partial<PatientTaskRead> = {}): PatientTaskRead {
  return {
    id: 't1',
    patient_id: '00000000-0000-0000-0000-000000000001',
    patient_name: '見本 花子',
    start_date: '2026-12-01',
    item_type_id: '00000000-0000-0000-0000-0000000000a1',
    item_type_name: 'GAF 入力',
    is_message: false,
    memo: null,
    created_by: null,
    created_by_name: null,
    created_at: '2026-12-01T00:00:00Z',
    status: 'open',
    due_date: null,
    overdue: false,
    visit_round: 1,
    carried_over: false,
    done_at: null,
    done_by_user_id: null,
    done_by_staff_id: null,
    done_by_name: null,
    done_visit_id: null,
    cancelled_at: null,
    held_by: {
      staff_id: '00000000-0000-0000-0000-0000000000b1',
      staff_name: '佐藤',
      staff_active: true,
      visit_id: '00000000-0000-0000-0000-0000000000c1',
      visit_date: '2026-12-02',
      start_time: '10:00',
    },
    next_visit: null,
    done_visit: null,
    can_reopen: false,
    ...over,
  };
}

describe('HeldTasksBand', () => {
  beforeEach(() => {
    completeMutate.mockReset();
    toastSuccess.mockReset();
  });

  it('0 件なら出さない (読めないときも)', () => {
    held = [];
    const { container, rerender } = render(<HeldTasksBand />);
    expect(container.innerHTML).toBe('');
    held = undefined;
    rerender(<HeldTasksBand />);
    expect(container.innerHTML).toBe('');
  });

  it('件数・説明・項目名・患者様名・何日の訪問からを出す', () => {
    held = [makeTask(), makeTask({ id: 't2', item_type_name: '計画書サイン' })];
    render(<HeldTasksBand />);
    const band = screen.getByTestId('held-tasks-band');
    expect(band.textContent).toContain('預かっているやること 2 件');
    expect(band.textContent).toContain(
      '前の訪問で済まなかった分です。訪問がない日でも完了にできます。',
    );
    const row = screen.getByTestId('held-task-t1');
    expect(row.textContent).toContain('GAF 入力');
    expect(row.textContent).toContain('見本 花子 様・12/2 の訪問から');
  });

  it('伝言は伝言の文を出す', () => {
    held = [
      makeTask({ is_message: true, item_type_id: null, item_type_name: null, memo: '封筒を渡す' }),
    ];
    render(<HeldTasksBand />);
    expect(screen.getByTestId('held-task-t1').textContent).toContain('封筒を渡す');
  });

  it('「完了」は訪問を指さずに完了にする', () => {
    held = [makeTask()];
    completeMutate.mockImplementation((_v, opts: { onSuccess: () => void }) => opts.onSuccess());
    render(<HeldTasksBand />);
    fireEvent.click(screen.getByTestId('held-task-complete-t1'));
    expect(completeMutate).toHaveBeenCalledWith(
      { taskId: 't1', visitId: null },
      expect.any(Object),
    );
    expect(toastSuccess).toHaveBeenCalledWith('完了にしました');
  });
});
