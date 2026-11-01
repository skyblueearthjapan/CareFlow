/** スマホの訪問詳細の「事務所からのお願い」「事務所からの伝言」(patient-tasks-design §3)。 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ApiError } from '@/lib/api-client';
import type { PatientTaskRead } from '@/lib/schemas/patient-task';

const completeMutate = vi.fn();
const reopenMutate = vi.fn();
let tasks: PatientTaskRead[] = [];

vi.mock('@/lib/queries/tasks', () => ({
  useVisitTasks: () => ({ data: tasks, isError: false }),
  useCompleteTask: () => ({ mutate: completeMutate, isPending: false }),
  useReopenTask: () => ({ mutate: reopenMutate, isPending: false }),
}));

const toastInfo = vi.fn();
const toastError = vi.fn();
vi.mock('@/components/ui/sonner', () => ({
  toast: {
    success: vi.fn(),
    error: (...a: unknown[]) => toastError(...a),
    info: (...a: unknown[]) => toastInfo(...a),
  },
}));

import { MobileVisitTasks } from '../MobileVisitTasks';

function makeTask(over: Partial<PatientTaskRead> = {}): PatientTaskRead {
  return {
    id: 't1',
    patient_id: '00000000-0000-0000-0000-000000000001',
    patient_name: '青木 さくら',
    start_date: '2026-11-02',
    item_type_id: '00000000-0000-0000-0000-0000000000a1',
    item_type_name: '計画書サイン',
    is_message: false,
    memo: null,
    created_by: null,
    created_by_name: '事務 太郎',
    created_at: '2026-11-01T00:00:00Z',
    status: 'open',
    overdue: false,
    carried_over: false,
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

describe('MobileVisitTasks', () => {
  beforeEach(() => {
    completeMutate.mockReset();
    reopenMutate.mockReset();
    toastInfo.mockReset();
    toastError.mockReset();
  });

  it('やることが無ければ何も出さない', () => {
    tasks = [];
    const { container } = render(<MobileVisitTasks visitId="v1" />);
    expect(container.innerHTML).toBe('');
  });

  it('四角を押すとその訪問で完了にする', () => {
    tasks = [makeTask({ carried_over: true, start_date: '2026-10-29' })];
    render(<MobileVisitTasks visitId="v1" />);
    expect(screen.getByTestId('mobile-visit-requests').textContent).toContain(
      '10/29 からの持ち越し',
    );
    const box = screen.getByTestId('mobile-task-t1');
    expect(box).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(box);
    expect(completeMutate).toHaveBeenCalledWith(
      { taskId: 't1', visitId: 'v1' },
      expect.any(Object),
    );
  });

  it('ほかの人が先に完了にしていた (409) ときは、その旨を出す', () => {
    tasks = [makeTask()];
    completeMutate.mockImplementation((_v, opts: { onError: (e: unknown) => void }) =>
      opts.onError(new ApiError('API 409', 409, { detail: 'すでに完了しています' })),
    );
    render(<MobileVisitTasks visitId="v1" />);
    fireEvent.click(screen.getByTestId('mobile-task-t1'));
    expect(toastError).toHaveBeenCalledWith('完了にできませんでした', {
      description: expect.stringContaining('すでに完了しています。ほかの人が先に操作した'),
    });
  });

  it('自分で完了にしたものは、もう一度押すと戻す', () => {
    tasks = [
      makeTask({
        status: 'done',
        done_by_name: '佐藤 花子',
        done_at: '2026-11-02T00:31:00Z',
        can_reopen: true,
      }),
    ];
    render(<MobileVisitTasks visitId="v1" />);
    const box = screen.getByTestId('mobile-task-t1');
    expect(box).toHaveAttribute('aria-checked', 'true');
    expect(box.textContent).toContain('完了 佐藤 花子 11/2 09:31');
    fireEvent.click(box);
    expect(reopenMutate).toHaveBeenCalledWith('t1', expect.any(Object));
  });

  it('ほかの人の完了は戻さず、知らせだけ出す', () => {
    tasks = [makeTask({ status: 'done', done_by_name: '鈴木 一郎', can_reopen: false })];
    render(<MobileVisitTasks visitId="v1" />);
    fireEvent.click(screen.getByTestId('mobile-task-t1'));
    expect(reopenMutate).not.toHaveBeenCalled();
    expect(toastInfo).toHaveBeenCalled();
  });

  it('伝言は「事務所からの伝言」に出し、「読みました」で完了にする', () => {
    tasks = [
      makeTask({
        id: 'm1',
        is_message: true,
        item_type_id: null,
        item_type_name: null,
        memo: '次回は裏口から入ってください。',
      }),
    ];
    render(<MobileVisitTasks visitId="v1" />);
    expect(screen.queryByTestId('mobile-visit-requests')).toBeNull();
    expect(screen.getByTestId('mobile-visit-messages').textContent).toContain(
      '次回は裏口から入ってください。',
    );
    fireEvent.click(screen.getByTestId('mobile-message-read-m1'));
    expect(completeMutate).toHaveBeenCalledWith(
      { taskId: 'm1', visitId: 'v1' },
      expect.any(Object),
    );
  });
});
