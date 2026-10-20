/**
 * ChangeScopeChoice 単体テスト (U-1 共通部品).
 *
 * 検証:
 *   1. 初期は 'pattern' (A) が選択済みで表示される.
 *   2. B のボタンをクリックすると onChange('week') が呼ばれる.
 *   3. value='week' のとき B 側が aria-checked=true になる.
 *   4. disabled のとき両ボタンが disabled になる.
 *   5. defaultNote が表示される.
 *   6. data-testid が規約どおり付いている.
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ChangeScopeChoice } from '../ChangeScopeChoice';

describe('ChangeScopeChoice', () => {
  it('data-testid が規約どおり付いている', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="pattern" onChange={onChange} />);
    expect(screen.getByTestId('change-scope-choice')).toBeInTheDocument();
    expect(screen.getByTestId('change-scope-pattern')).toBeInTheDocument();
    expect(screen.getByTestId('change-scope-week')).toBeInTheDocument();
  });

  it('value="pattern" のとき A が aria-checked=true、B が aria-checked=false', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="pattern" onChange={onChange} />);
    expect(screen.getByTestId('change-scope-pattern')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('change-scope-week')).toHaveAttribute('aria-checked', 'false');
  });

  it('value="week" のとき B が aria-checked=true、A が aria-checked=false', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="week" onChange={onChange} />);
    expect(screen.getByTestId('change-scope-week')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('change-scope-pattern')).toHaveAttribute('aria-checked', 'false');
  });

  it('A ラベルと説明文が表示される', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="pattern" onChange={onChange} />);
    expect(screen.getByText('固定訪問週間に登録')).toBeInTheDocument();
    expect(screen.getByText(/毎週の型が変わります/)).toBeInTheDocument();
  });

  it('B ラベルと説明文が表示される', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="pattern" onChange={onChange} />);
    expect(screen.getByText('この週だけ反映')).toBeInTheDocument();
    expect(screen.getByText(/今週のみ変更します/)).toBeInTheDocument();
  });

  it('B ボタンクリックで onChange("week") が呼ばれる', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="pattern" onChange={onChange} />);
    fireEvent.click(screen.getByTestId('change-scope-week'));
    expect(onChange).toHaveBeenCalledWith('week');
  });

  it('A ボタンクリックで onChange("pattern") が呼ばれる', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="week" onChange={onChange} />);
    fireEvent.click(screen.getByTestId('change-scope-pattern'));
    expect(onChange).toHaveBeenCalledWith('pattern');
  });

  it('disabled=true のとき両ボタンが disabled', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="pattern" onChange={onChange} disabled />);
    expect(screen.getByTestId('change-scope-pattern')).toBeDisabled();
    expect(screen.getByTestId('change-scope-week')).toBeDisabled();
  });

  it('disabled=true のとき onChange は呼ばれない', () => {
    const onChange = vi.fn();
    render(<ChangeScopeChoice value="pattern" onChange={onChange} disabled />);
    fireEvent.click(screen.getByTestId('change-scope-week'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('defaultNote が表示される', () => {
    const onChange = vi.fn();
    render(
      <ChangeScopeChoice value="pattern" onChange={onChange} defaultNote="（既定: A が推奨）" />,
    );
    expect(screen.getByText('（既定: A が推奨）')).toBeInTheDocument();
  });

  it('defaultNote が省略されたとき余分な要素は出ない', () => {
    const onChange = vi.fn();
    const { container } = render(<ChangeScopeChoice value="pattern" onChange={onChange} />);
    // 3 要素 (change-scope-choice div, pattern button, week button) のみ
    expect(
      container.querySelector('[data-testid="change-scope-choice"]')?.children,
    ).toHaveLength(2);
  });
});
