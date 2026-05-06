/**
 * EditPageStickyBar vitest unit tests (W10-FE3).
 *
 * テストケース:
 * 1. isDirty=false → コンポーネントが非表示 (null)
 * 2. isDirty=true → バー + 破棄・更新ボタンが表示
 * 3. isSaving=true → ボタン disabled + spinner 表示
 * 4. onSave / onDiscard コールバックが発火する
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { EditPageStickyBar } from '../EditPageStickyBar';

const defaultProps = {
  isDirty: true,
  isSaving: false,
  onDiscard: vi.fn(),
  onSave: vi.fn(),
};

describe('EditPageStickyBar', () => {
  it('1. isDirty=false のとき何も表示しない', () => {
    const { container } = render(<EditPageStickyBar {...defaultProps} isDirty={false} />);
    expect(container.firstChild).toBeNull();
  });

  it('2. isDirty=true のとき「破棄」「更新」ボタンが表示される', () => {
    render(<EditPageStickyBar {...defaultProps} isDirty={true} />);

    expect(screen.getByTestId('sticky-bar')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '破棄' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '更新' })).toBeInTheDocument();
    expect(screen.getByText('未保存の変更があります')).toBeInTheDocument();
  });

  it('3. isSaving=true のときボタンが disabled になり spinner が表示される', () => {
    render(<EditPageStickyBar {...defaultProps} isDirty={true} isSaving={true} />);

    const discardBtn = screen.getByRole('button', { name: '破棄' });
    const saveBtn = screen.getByRole('button', { name: '保存中…' });

    expect(discardBtn).toBeDisabled();
    expect(saveBtn).toBeDisabled();
  });

  it('4. onSave / onDiscard コールバックが発火する', async () => {
    const onSave = vi.fn();
    const onDiscard = vi.fn();

    render(
      <EditPageStickyBar {...defaultProps} isDirty={true} onSave={onSave} onDiscard={onDiscard} />,
    );

    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    expect(onSave).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole('button', { name: '破棄' }));
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });
});
