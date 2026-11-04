/** やることの「項目の設定」の「預かる」切り替え (patient-tasks-hold-first-visitor-design §4)。 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const updateMutate = vi.fn();

vi.mock('@/lib/queries/tasks', () => ({
  useTaskItemTypes: () => ({
    data: [
      {
        id: 'i1',
        name: '計画書サイン',
        sort_order: 1,
        is_active: true,
        hold_by_first_visitor: false,
      },
      { id: 'i2', name: 'GAF 入力', sort_order: 2, is_active: true, hold_by_first_visitor: true },
    ],
    isLoading: false,
    refetch: vi.fn(),
  }),
  useCreateTaskItemType: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateTaskItemType: () => ({ mutate: updateMutate, isPending: false }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { TaskItemTypesSettings } from '../TaskItemTypesSettings';

describe('TaskItemTypesSettings の「預かる」', () => {
  beforeEach(() => updateMutate.mockReset());

  it('各項目に「預かる」の切り替えがあり、今の値を出す', () => {
    render(<TaskItemTypesSettings />);
    expect(screen.getByTestId('task-item-type-hold-i1')).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByTestId('task-item-type-hold-i2')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('task-item-types-hold-help').textContent).toContain(
      '「預かる」をオンにした項目は、最初の訪問で済まなかったとき、その訪問の主担当が預かります。',
    );
  });

  it('押すと hold_by_first_visitor を送る', () => {
    render(<TaskItemTypesSettings />);
    fireEvent.click(screen.getByTestId('task-item-type-hold-i1'));
    expect(updateMutate).toHaveBeenCalledWith(
      { id: 'i1', payload: { hold_by_first_visitor: true } },
      expect.any(Object),
    );
    fireEvent.click(screen.getByTestId('task-item-type-hold-i2'));
    expect(updateMutate).toHaveBeenLastCalledWith(
      { id: 'i2', payload: { hold_by_first_visitor: false } },
      expect.any(Object),
    );
  });
});
