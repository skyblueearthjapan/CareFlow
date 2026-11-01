/** 訪問モニターのカードのやることの印 (patient-tasks-design §3)。 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { TaskCountBadge } from '../TaskCountBadge';

describe('TaskCountBadge', () => {
  it('未完了は 📋N', () => {
    render(
      <TaskCountBadge
        testId="b"
        count={{ open_count: 2, total_count: 3, has_carried_over: false, all_done: false }}
      />,
    );
    const b = screen.getByTestId('b');
    expect(b.textContent).toBe('📋2');
    expect(b.dataset.tone).toBe('open');
  });

  it('期日を過ぎたもの (持ち越し) があれば赤', () => {
    render(
      <TaskCountBadge
        testId="b"
        count={{ open_count: 1, total_count: 1, has_carried_over: true, all_done: false }}
      />,
    );
    expect(screen.getByTestId('b').dataset.tone).toBe('over');
  });

  it('全部完了なら ✓', () => {
    render(
      <TaskCountBadge
        testId="b"
        count={{ open_count: 0, total_count: 2, has_carried_over: false, all_done: true }}
      />,
    );
    const b = screen.getByTestId('b');
    expect(b.textContent).toBe('✓');
    expect(b.dataset.tone).toBe('done');
  });

  it('やることが無ければ出さない', () => {
    const { container } = render(<TaskCountBadge count={undefined} />);
    expect(container.innerHTML).toBe('');
  });
});
