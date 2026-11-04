/**
 * /tasks — やることの一覧の期日と「何回目の訪問待ち」(patient-tasks-due-design-2026-11-04 §3・§6)。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import type { PatientTaskRead, TaskListStatus } from '@/lib/schemas/patient-task';

const setDueMutate = vi.fn();
const useTasksMock = vi.fn();

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { role: 'admin' } }, status: 'authenticated' }),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock('@/lib/queries/staff', () => ({ useStaffList: () => ({ data: [] }) }));
vi.mock('@/lib/queries/tasks', () => ({
  useTasks: (p: { status?: TaskListStatus }) => useTasksMock(p),
  useCompleteTask: () => ({ mutate: vi.fn(), isPending: false }),
  useReopenTask: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelTask: () => ({ mutate: vi.fn(), isPending: false }),
  useSetTaskDue: () => ({ mutate: setDueMutate, isPending: false }),
}));
vi.mock('@/components/tasks/AddTaskDialog', () => ({ AddTaskDialog: () => null }));
vi.mock('@/components/tasks/RecurringTasksPanel', () => ({ RecurringTasksPanel: () => null }));
vi.mock('@/components/tasks/TaskItemTypesSettings', () => ({ TaskItemTypesSettings: () => null }));
vi.mock('@/components/brand/Rakusuke', () => ({
  RakusukeTitle: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import TasksPage from '../page';

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
    created_by_name: null,
    created_at: '2026-11-01T00:00:00Z',
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
    next_visit: null,
    done_visit: null,
    can_reopen: false,
    ...over,
  };
}

const OPEN = [
  makeTask({ id: 'a', due_date: null, visit_round: 1 }),
  makeTask({ id: 'b', due_date: '2026-11-03', overdue: true, visit_round: 2 }),
  makeTask({ id: 'c', due_date: '2026-11-04', visit_round: 1 }),
  makeTask({ id: 'd', due_date: '2026-11-10', visit_round: 3 }),
];
const DONE = [
  makeTask({
    id: 'z',
    status: 'done',
    due_date: '2026-11-05',
    done_by_name: '佐藤 花子',
    done_at: '2026-11-03T05:00:00Z',
  }),
];

describe('TasksPage の期日と何回目の訪問待ち', () => {
  beforeEach(() => {
    // 今日 = 2026-11-04 (JST)。タイマーは本物のまま (Date だけ止める)。
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-04T00:00:00Z'));
    setDueMutate.mockReset();
    useTasksMock.mockImplementation((p: { status?: TaskListStatus }) => ({
      data: p.status === 'done' ? DONE : p.status === 'all' ? [...OPEN, ...DONE] : OPEN,
      isLoading: false,
      isError: false,
    }));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('上の件数: 未完了・期日を過ぎた・2 回目以降の訪問待ち・完了', () => {
    render(<TasksPage />);
    expect(screen.getByTestId('tasks-stat-open').textContent).toContain('4');
    expect(screen.getByTestId('tasks-stat-overdue').textContent).toContain('1');
    expect(screen.getByTestId('tasks-stat-waiting2plus').textContent).toContain('2');
    expect(screen.getByTestId('tasks-stat-done').textContent).toContain('1');
  });

  it('絞り込みに「2 回目以降」があり、status=waiting2plus で読む', () => {
    render(<TasksPage />);
    fireEvent.click(screen.getByRole('button', { name: '2 回目以降' }));
    expect(useTasksMock).toHaveBeenCalledWith(expect.objectContaining({ status: 'waiting2plus' }));
  });

  it('期日の欄: なし・今日まで・あと N 日・過ぎた日数', () => {
    render(<TasksPage />);
    expect(screen.getByTestId('tasks-due-a').textContent).toBe('なし');
    expect(screen.getByTestId('tasks-due-b').textContent).toContain('11/3（火） まで');
    expect(screen.getByTestId('tasks-due-b').textContent).toContain('1 日過ぎた');
    expect(screen.getByTestId('tasks-due-c').textContent).toContain('今日まで');
    expect(screen.getByTestId('tasks-due-d').textContent).toContain('11/10（火） まで');
    expect(screen.getByTestId('tasks-due-d').textContent).toContain('あと 6 日');
  });

  it('状態の札: N 回目の訪問待ち (1 回目は灰・2 回目以降は橙)・期日を過ぎたは先頭に赤', () => {
    render(<TasksPage />);
    const rowB = screen.getByTestId('tasks-row-b');
    const text = rowB.textContent ?? '';
    expect(text).toContain('期日を過ぎた');
    expect(text.indexOf('期日を過ぎた')).toBeLessThan(text.indexOf('2 回目の訪問待ち'));
    expect(within(rowB).getByTestId('task-round').dataset.round).toBe('2');
    expect(screen.getByTestId('tasks-row-a').textContent).toContain('1 回目の訪問待ち');
    expect(screen.getByTestId('tasks-row-a').textContent).not.toContain('期日を過ぎた');
    expect(screen.getByTestId('tasks-row-d').textContent).toContain('3 回目の訪問待ち');
  });

  it('急ぐ順に並べる: 期日を過ぎた → 今日まで → 回数の多い順', () => {
    render(<TasksPage />);
    const ids = screen
      .getAllByTestId(/^tasks-row-/)
      .map((el) => el.getAttribute('data-testid')?.replace('tasks-row-', ''));
    expect(ids).toEqual(['b', 'c', 'd', 'a']);
  });

  it('期日の欄を押すと日付を選べ、PATCH で期日を変える', () => {
    render(<TasksPage />);
    fireEvent.click(screen.getByTestId('tasks-due-a'));
    const input = screen.getByTestId('tasks-due-input-a');
    // 日付 (11/2) より前は決められない
    fireEvent.change(input, { target: { value: '2026-11-01' } });
    expect(screen.getByTestId('tasks-due-save-a')).toBeDisabled();
    expect(screen.getByRole('alert').textContent).toContain('より前です');
    fireEvent.change(input, { target: { value: '2026-11-12' } });
    fireEvent.click(screen.getByTestId('tasks-due-save-a'));
    expect(setDueMutate).toHaveBeenCalledWith(
      { taskId: 'a', dueDate: '2026-11-12' },
      expect.any(Object),
    );
  });

  it('期日のあるものは「なし」に戻せる', () => {
    render(<TasksPage />);
    fireEvent.click(screen.getByTestId('tasks-due-d'));
    fireEvent.click(screen.getByTestId('tasks-due-clear-d'));
    expect(setDueMutate).toHaveBeenCalledWith({ taskId: 'd', dueDate: null }, expect.any(Object));
  });

  it('完了の行の期日は押せない', () => {
    render(<TasksPage />);
    // 絞り込みの「完了」(行の「完了」ボタンと分けるため aria-pressed のあるもの)。
    const chip = screen
      .getAllByRole('button', { name: '完了' })
      .find((b) => b.hasAttribute('aria-pressed'));
    fireEvent.click(chip!);
    const cell = screen.getByTestId('tasks-due-z');
    expect(cell.tagName).not.toBe('BUTTON');
    expect(cell.textContent).toContain('11/5（木） まで');
    expect(cell.textContent).not.toContain('あと');
  });
});
