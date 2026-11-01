/** やることのページの「＋ やることを付ける」の入力 (patient-tasks-design §2-6)。 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const createMutate = vi.fn();

vi.mock('@/lib/queries/tasks', () => ({
  useTaskItemTypes: () => ({
    data: [
      { id: 'i1', name: '項目あ', sort_order: 1, is_active: true },
      { id: 'i2', name: '項目い', sort_order: 2, is_active: true },
      { id: 'i3', name: '使わない項目', sort_order: 3, is_active: false },
    ],
  }),
  useCreateTasks: () => ({ mutate: createMutate, isPending: false }),
}));

vi.mock('@/components/master/PatientCombobox', () => ({
  PatientCombobox: ({ onChange }: { onChange: (id: string) => void }) => (
    <button type="button" data-testid="pick-patient" onClick={() => onChange('p-1')}>
      患者様を選ぶ
    </button>
  ),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { AddTaskDialog } from '../AddTaskDialog';

function AddTaskForm(props: { defaultDate: string; onClose: () => void }) {
  return <AddTaskDialog open onOpenChange={props.onClose} defaultDate={props.defaultDate} />;
}

const NEXT = {
  visit_id: 'v9',
  visit_date: '2026-11-05',
  start_time: '14:00',
  end_time: '14:35',
  staff: [{ staff_id: 's2', name: '鈴木 一郎', role: 'primary' as const }],
};

type Opts = { onSuccess: (r: unknown) => void };

describe('AddTaskForm', () => {
  // 返り値を返さない (vitest は beforeEach の返り値を後片付けとして呼ぶ)。
  beforeEach(() => {
    createMutate.mockReset();
  });

  it('患者様と、項目か伝言が入るまで「付ける」を押せない', () => {
    render(<AddTaskForm defaultDate="2026-11-02" onClose={vi.fn()} />);
    const submit = screen.getByTestId('add-task-submit');
    expect(submit).toBeDisabled();
    expect(screen.getByTestId('add-task-missing').textContent).toContain('患者様・項目か伝言');

    fireEvent.click(screen.getByTestId('pick-patient'));
    expect(submit).toBeDisabled();

    // 空白だけの伝言は数えない・伝言だけなら付けられる
    fireEvent.change(screen.getByTestId('add-task-memo'), { target: { value: '  ' } });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId('add-task-memo'), { target: { value: '封筒を渡す' } });
    expect(submit).toBeEnabled();
  });

  it('使わない項目はチップに出さない・選んだ項目と日付で付ける', () => {
    render(<AddTaskForm defaultDate="2026-11-02" onClose={vi.fn()} />);
    expect(screen.queryByText('使わない項目')).toBeNull();
    fireEvent.click(screen.getByTestId('pick-patient'));
    fireEvent.click(screen.getByRole('button', { name: '項目あ' }));
    fireEvent.click(screen.getByRole('button', { name: '項目い' }));
    fireEvent.change(screen.getByTestId('add-task-date'), { target: { value: '2026-11-04' } });
    fireEvent.click(screen.getByTestId('add-task-submit'));
    expect(createMutate).toHaveBeenCalledWith(
      { patient_id: 'p-1', start_date: '2026-11-04', item_type_ids: ['i1', 'i2'], memo: null },
      expect.any(Object),
    );
  });

  it('付けたら結果の next_visit で「出す訪問」を出す', () => {
    createMutate.mockImplementation((_p, opts: Opts) =>
      opts.onSuccess({
        tasks: [{ patient_name: '岡本 翔' }],
        next_visit: NEXT,
        notified_user_count: 1,
      }),
    );
    render(<AddTaskForm defaultDate="2026-11-02" onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId('pick-patient'));
    fireEvent.click(screen.getByRole('button', { name: '項目あ' }));
    fireEvent.click(screen.getByTestId('add-task-submit'));
    expect(screen.getByTestId('add-task-next-visit').textContent).toBe(
      '11/5（木） 14:00・鈴木 一郎',
    );
  });

  it('出す訪問が無ければ「訪問が入ったら出ます」', () => {
    createMutate.mockImplementation((_p, opts: Opts) =>
      opts.onSuccess({
        tasks: [{ patient_name: '岡本 翔' }],
        next_visit: null,
        notified_user_count: 0,
      }),
    );
    render(<AddTaskForm defaultDate="2026-11-02" onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId('pick-patient'));
    fireEvent.change(screen.getByTestId('add-task-memo'), { target: { value: '伝言' } });
    fireEvent.click(screen.getByTestId('add-task-submit'));
    expect(screen.getByTestId('add-task-no-visit')).toBeInTheDocument();
  });

  it('二度押ししても付けるのは 1 回だけ・失敗して終わったら次を送れる', () => {
    let settle: (() => void) | undefined;
    createMutate.mockImplementation((_p, opts: { onSettled: () => void }) => {
      settle = opts.onSettled;
    });
    render(<AddTaskForm defaultDate="2026-11-02" onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId('pick-patient'));
    fireEvent.click(screen.getByRole('button', { name: '項目あ' }));
    const submit = screen.getByTestId('add-task-submit');
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(createMutate).toHaveBeenCalledTimes(1);
    settle?.();
    fireEvent.click(submit);
    expect(createMutate).toHaveBeenCalledTimes(2);
  });
});
