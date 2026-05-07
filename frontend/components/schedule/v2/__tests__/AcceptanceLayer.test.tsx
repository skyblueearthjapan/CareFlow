/**
 * AcceptanceLayer テスト (W15-FE).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/components/ui/card', () => ({
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import { AcceptanceBadge, AcceptanceLegend, acceptanceCellClass } from '../AcceptanceLayer';

describe('acceptanceCellClass', () => {
  it('available は class なし', () => {
    expect(acceptanceCellClass('available')).toBe('');
  });
  it('consult は warning 系背景', () => {
    expect(acceptanceCellClass('consult')).toMatch(/warning/);
  });
  it('unavailable は error 系背景', () => {
    expect(acceptanceCellClass('unavailable')).toMatch(/error/);
  });
  it('null/undefined は class なし', () => {
    expect(acceptanceCellClass(null)).toBe('');
    expect(acceptanceCellClass(undefined)).toBe('');
  });
});

describe('AcceptanceBadge', () => {
  it('status 未指定では何も描画しない', () => {
    const { container } = render(<AcceptanceBadge status={null} />);
    expect(container.firstChild).toBeNull();
  });
  it('available のとき ○ を描画する', () => {
    render(<AcceptanceBadge status="available" />);
    expect(screen.getByText('○')).toBeInTheDocument();
  });
  it('unavailable のとき × を描画する', () => {
    render(<AcceptanceBadge status="unavailable" />);
    expect(screen.getByText('×')).toBeInTheDocument();
  });
});

describe('AcceptanceLegend', () => {
  it('凡例文言を出す (○ / △ / ×)', () => {
    render(<AcceptanceLegend />);
    expect(screen.getByText(/受入目安:/)).toBeInTheDocument();
    expect(screen.getByText(/受入可/)).toBeInTheDocument();
    expect(screen.getByText(/要相談/)).toBeInTheDocument();
    expect(screen.getByText(/受入不可/)).toBeInTheDocument();
  });
});
