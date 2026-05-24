/**
 * OperatingWeekdaysField vitest unit tests — Phase G-45.
 *
 * テストケース:
 * 1. 7 個の toggle (月-日) が描画される.
 * 2. toggle クリックで onChange が呼ばれ、 0..6 昇順で正規化された配列を返す.
 * 3. 「全選択」ボタンで全曜日 (0..6) を渡す.
 * 4. 「全解除」ボタンで [] を渡す.
 * 5. value = [] のとき error メッセージが表示される (= 0 個禁止).
 * 6. disabled prop で toggle / ボタンが無効化される.
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { OperatingWeekdaysField } from '../OperatingWeekdaysField';

describe('OperatingWeekdaysField', () => {
  it('renders 7 weekday toggle buttons (月-日)', () => {
    const onChange = vi.fn();
    render(<OperatingWeekdaysField value={[0, 1, 2, 3, 4, 5]} onChange={onChange} />);
    const labels = ['月', '火', '水', '木', '金', '土', '日'];
    for (const label of labels) {
      expect(screen.getByLabelText(`${label}曜`)).toBeInTheDocument();
    }
  });

  it('renders selected state on toggles based on value', () => {
    const onChange = vi.fn();
    render(<OperatingWeekdaysField value={[0, 2, 4]} onChange={onChange} />);
    expect(screen.getByTestId('operating-weekdays-toggle-0').getAttribute('aria-checked')).toBe(
      'true',
    );
    expect(screen.getByTestId('operating-weekdays-toggle-1').getAttribute('aria-checked')).toBe(
      'false',
    );
    expect(screen.getByTestId('operating-weekdays-toggle-2').getAttribute('aria-checked')).toBe(
      'true',
    );
  });

  it('toggle add: clicking unchecked weekday adds it and sorts 0..6', () => {
    const onChange = vi.fn();
    render(<OperatingWeekdaysField value={[1, 3]} onChange={onChange} />);
    fireEvent.click(screen.getByTestId('operating-weekdays-toggle-0'));
    expect(onChange).toHaveBeenLastCalledWith([0, 1, 3]);
  });

  it('toggle remove: clicking checked weekday removes it', () => {
    const onChange = vi.fn();
    render(<OperatingWeekdaysField value={[0, 1, 2]} onChange={onChange} />);
    fireEvent.click(screen.getByTestId('operating-weekdays-toggle-1'));
    expect(onChange).toHaveBeenLastCalledWith([0, 2]);
  });

  it('select-all button passes [0,1,2,3,4,5,6]', () => {
    const onChange = vi.fn();
    render(<OperatingWeekdaysField value={[0, 1]} onChange={onChange} />);
    fireEvent.click(screen.getByTestId('operating-weekdays-select-all'));
    expect(onChange).toHaveBeenLastCalledWith([0, 1, 2, 3, 4, 5, 6]);
  });

  it('select-all is disabled when already all selected', () => {
    const onChange = vi.fn();
    render(<OperatingWeekdaysField value={[0, 1, 2, 3, 4, 5, 6]} onChange={onChange} />);
    expect(screen.getByTestId('operating-weekdays-select-all')).toBeDisabled();
  });

  it('clear-all button passes []', () => {
    const onChange = vi.fn();
    render(<OperatingWeekdaysField value={[0, 1]} onChange={onChange} />);
    fireEvent.click(screen.getByTestId('operating-weekdays-clear-all'));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it('shows error message when value is empty (= 0 days selected)', () => {
    render(<OperatingWeekdaysField value={[]} onChange={vi.fn()} />);
    expect(screen.getByTestId('operating-weekdays-error')).toBeInTheDocument();
    expect(screen.getByTestId('operating-weekdays-error').textContent).toMatch(/最低 1 日選択/);
  });

  it('does not show error when value has at least 1 weekday', () => {
    render(<OperatingWeekdaysField value={[0]} onChange={vi.fn()} />);
    expect(screen.queryByTestId('operating-weekdays-error')).not.toBeInTheDocument();
  });

  it('shows external error prop over default empty warning', () => {
    render(<OperatingWeekdaysField value={[0]} onChange={vi.fn()} error="サーバーエラー" />);
    expect(screen.getByTestId('operating-weekdays-error').textContent).toContain('サーバーエラー');
  });

  it('disabled prop disables all toggles and buttons', () => {
    const onChange = vi.fn();
    render(<OperatingWeekdaysField value={[0, 1, 2]} onChange={onChange} disabled />);
    expect(screen.getByTestId('operating-weekdays-toggle-0')).toBeDisabled();
    expect(screen.getByTestId('operating-weekdays-select-all')).toBeDisabled();
    expect(screen.getByTestId('operating-weekdays-clear-all')).toBeDisabled();
    fireEvent.click(screen.getByTestId('operating-weekdays-toggle-3'));
    expect(onChange).not.toHaveBeenCalled();
  });
});
