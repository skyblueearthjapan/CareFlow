import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  TimelineMoveDialog,
  type TimelineMoveContext,
} from '@/components/schedule/timeline/TimelineMoveDialog';

const ctx = (over: Partial<TimelineMoveContext> = {}): TimelineMoveContext => ({
  patientName: '青柳 あい',
  fromLabel: '稲毛A',
  toLabel: '稲毛B',
  weekdayLabel: '月',
  oldTimeHM: '09:30',
  newTimeHM: '13:15',
  durationMin: 45,
  courseChanged: true,
  ...over,
});

function renderDialog(over: Partial<Parameters<typeof TimelineMoveDialog>[0]> = {}) {
  const onConfirm = vi.fn();
  const onClose = vi.fn();
  render(
    <TimelineMoveDialog open context={ctx()} onConfirm={onConfirm} onClose={onClose} {...over} />,
  );
  return { onConfirm, onClose };
}

describe('TimelineMoveDialog', () => {
  it('文脈 (患者名・時刻・コース変更) を表示する', () => {
    renderDialog();
    expect(screen.getByText('青柳 あい')).toBeInTheDocument();
    expect(screen.getByText('09:30')).toBeInTheDocument();
    expect(screen.getByText('13:15')).toBeInTheDocument();
    expect(screen.getByText('稲毛A')).toBeInTheDocument();
    expect(screen.getByText('稲毛B')).toBeInTheDocument();
  });

  it('コース据置 (courseChanged=false) のときコース変更の文言を出さない', () => {
    renderDialog({ context: ctx({ courseChanged: false }) });
    expect(screen.queryByText('稲毛B')).toBeNull();
  });

  it('既定は「この週だけ」で、確定で onConfirm("week") を呼ぶ', () => {
    const { onConfirm } = renderDialog();
    expect(screen.getByTestId('change-scope-week').getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByTestId('timeline-move-confirm'));
    expect(onConfirm).toHaveBeenCalledWith('week');
  });

  it('固定パターンへ切り替えて確定で onConfirm("pattern") を呼ぶ', () => {
    const { onConfirm } = renderDialog();
    fireEvent.click(screen.getByTestId('change-scope-pattern'));
    fireEvent.click(screen.getByTestId('timeline-move-confirm'));
    expect(onConfirm).toHaveBeenCalledWith('pattern');
  });

  it('キャンセルで onClose を呼ぶ', () => {
    const { onClose } = renderDialog();
    fireEvent.click(screen.getByTestId('timeline-move-cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('再オープンで選択が既定 (この週だけ) に戻る', () => {
    const props = { context: ctx(), onConfirm: vi.fn(), onClose: vi.fn() };
    const { rerender } = render(<TimelineMoveDialog open {...props} />);
    fireEvent.click(screen.getByTestId('change-scope-pattern'));
    expect(screen.getByTestId('change-scope-pattern').getAttribute('aria-checked')).toBe('true');
    rerender(<TimelineMoveDialog open={false} {...props} />);
    rerender(<TimelineMoveDialog open {...props} />);
    expect(screen.getByTestId('change-scope-week').getAttribute('aria-checked')).toBe('true');
  });

  it('busy 中は確定/キャンセルとも無効', () => {
    renderDialog({ busy: true });
    expect(screen.getByTestId('timeline-move-confirm')).toBeDisabled();
    expect(screen.getByTestId('timeline-move-cancel')).toBeDisabled();
  });
});
