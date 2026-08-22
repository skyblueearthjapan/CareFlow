/**
 * ShiftsEditDialog — 出勤ON時の既定時間 (09:00-18:00) と一括プリセット。
 * PO 要望 2026-08-22: 新規スタッフの週間シフト登録の手間削減。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DEFAULT_SHIFT_END, DEFAULT_SHIFT_START, ShiftsEditDialog } from '../ShiftsEditDialog';

// Radix Dialog が ResizeObserver を要求する (jsdom 未実装) → 最小ポリフィル。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

vi.mock('@/lib/queries/staff-shifts', () => ({
  useUpdateStaffShifts: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

function timeInputs(): HTMLInputElement[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>('input[type="time"]'));
}

describe('ShiftsEditDialog', () => {
  it('既定の勤務時間は 09:00〜18:00', () => {
    expect(DEFAULT_SHIFT_START).toBe('09:00');
    expect(DEFAULT_SHIFT_END).toBe('18:00');
  });

  it('勤務スイッチをONにすると 09:00〜18:00 が入る', () => {
    render(
      <ShiftsEditDialog open staffId="s1" initialShifts={[]} onOpenChange={() => undefined} />,
    );
    const switches = screen.getAllByRole('switch');
    fireEvent.click(switches[0]!); // 月
    const inputs = timeInputs();
    expect(inputs[0]!.value).toBe('09:00');
    expect(inputs[1]!.value).toBe('18:00');
  });

  it('「月〜金」プリセットで月〜金だけ 09:00〜18:00 でONになり、土日はOFFのまま', () => {
    render(
      <ShiftsEditDialog open staffId="s1" initialShifts={[]} onOpenChange={() => undefined} />,
    );
    fireEvent.click(screen.getByTestId('shifts-preset-weekdays'));
    const switches = screen.getAllByRole('switch');
    expect(switches.slice(0, 5).every((s) => s.getAttribute('aria-checked') === 'true')).toBe(true);
    expect(switches[5]!.getAttribute('aria-checked')).toBe('false');
    expect(switches[6]!.getAttribute('aria-checked')).toBe('false');
    const inputs = timeInputs();
    expect(inputs[0]!.value).toBe('09:00');
    expect(inputs[9]!.value).toBe('18:00'); // 金の終了
  });

  it('「月〜土」プリセットは既に入っている時刻を上書きしない', () => {
    render(
      <ShiftsEditDialog
        open
        staffId="s1"
        initialShifts={[{ weekday: 0, is_on: true, start_time: '10:00', end_time: '16:00' }]}
        onOpenChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId('shifts-preset-mon-sat'));
    const inputs = timeInputs();
    expect(inputs[0]!.value).toBe('10:00');
    expect(inputs[1]!.value).toBe('16:00');
    expect(inputs[10]!.value).toBe('09:00'); // 土の開始
    expect(inputs[11]!.value).toBe('18:00');
    expect(screen.getAllByRole('switch')[6]!.getAttribute('aria-checked')).toBe('false');
  });
});
