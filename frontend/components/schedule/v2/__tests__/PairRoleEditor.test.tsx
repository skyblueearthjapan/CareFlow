/**
 * PairRoleEditor テスト (W15-FE D-4).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';

vi.mock('lucide-react', () => ({
  Star: ({ className }: { className?: string }) => (
    <span data-testid="star" data-classname={className} />
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import { PairRoleEditor } from '../PairRoleEditor';

describe('PairRoleEditor', () => {
  it('value=null は primary 扱い (★塗りつぶし)', () => {
    const onChange = vi.fn();
    render(<PairRoleEditor value={null} onChange={onChange} canEdit={true} />);
    const btn = screen.getByRole('button');
    expect(btn).toHaveAttribute('data-pair-role', 'primary');
  });

  it('value=primary でクリックすると onChange("support") が呼ばれる', () => {
    const onChange = vi.fn();
    render(<PairRoleEditor value="primary" onChange={onChange} canEdit={true} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onChange).toHaveBeenCalledWith('support');
  });

  it('value=support でクリックすると onChange("primary") が呼ばれる', () => {
    const onChange = vi.fn();
    render(<PairRoleEditor value="support" onChange={onChange} canEdit={true} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onChange).toHaveBeenCalledWith('primary');
  });

  it('canEdit=false ではクリックしても onChange が呼ばれない', () => {
    const onChange = vi.fn();
    render(<PairRoleEditor value="primary" onChange={onChange} canEdit={false} />);
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onChange).not.toHaveBeenCalled();
  });
});
