/** 訪問モニターのカードのやることの印 (patient-tasks-design §3)。 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { TaskCountBadge } from '../TaskCountBadge';

describe('TaskCountBadge', () => {
  it('未完了は 📋N', () => {
    render(
      <TaskCountBadge
        testId="b"
        count={{
          open_count: 2,
          total_count: 3,
          has_carried_over: false,
          has_overdue: false,
          all_done: false,
        }}
      />,
    );
    const b = screen.getByTestId('b');
    expect(b.textContent).toBe('📋2');
    expect(b.dataset.tone).toBe('open');
    // 絵文字だけの印なので、読み上げは aria-label で伝える。
    expect(b).toHaveAttribute('role', 'img');
  });

  it('期日を過ぎたものがあれば赤 (2 回目以降があっても赤を先に)', () => {
    render(
      <TaskCountBadge
        testId="b"
        count={{
          open_count: 1,
          total_count: 1,
          has_carried_over: true,
          has_overdue: true,
          all_done: false,
        }}
      />,
    );
    const b = screen.getByTestId('b');
    expect(b.dataset.tone).toBe('over');
    expect(b.getAttribute('title')).toBe(
      '事務所からのやること：未完了 1 件（期日を過ぎたものあり・2 回目以降の訪問待ちあり）',
    );
  });

  it('2 回目以降の訪問待ちだけなら橙', () => {
    render(
      <TaskCountBadge
        testId="b"
        count={{
          open_count: 2,
          total_count: 2,
          has_carried_over: true,
          has_overdue: false,
          all_done: false,
        }}
      />,
    );
    const b = screen.getByTestId('b');
    expect(b.dataset.tone).toBe('carry');
    expect(b.getAttribute('title')).toBe(
      '事務所からのやること：未完了 2 件（2 回目以降の訪問待ちあり）',
    );
  });

  it('全部完了なら ✓', () => {
    render(
      <TaskCountBadge
        testId="b"
        count={{
          open_count: 0,
          total_count: 2,
          has_carried_over: false,
          has_overdue: false,
          all_done: true,
        }}
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
