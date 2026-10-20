/**
 * AcceptanceFieldView — スマホの受け入れ枠 (見るだけ・設計書 §5-2)。
 *
 * 既定の条件 (職員だけ・直近 4 週・休みは実際のとおり) の結果を出す。
 * 週の送り・チップ・取り直しは無い。マスを押すと内訳を下に開く。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { makePayload } from '@/lib/__tests__/fixtures/acceptanceStrictPayload';

vi.mock('@/lib/queries/acceptance_strict', () => ({
  useStrictAcceptance: () => ({ data: makePayload(), isLoading: false, isError: false }),
}));
vi.mock('@/lib/queries/offices', () => ({
  useOffices: () => ({ allOffices: [{ id: 'office-1', name: '稲毛拠点' }] }),
}));
vi.mock('@/components/mobile/MobileSurfaceSwitcher', () => ({
  MobileSurfaceSwitcher: () => null,
}));

import { AcceptanceFieldView } from '../AcceptanceFieldView';

describe('AcceptanceFieldView', () => {
  it('既定の条件の ○ × と、短い決まりの文章・取った日時を出す', () => {
    render(<AcceptanceFieldView />);
    expect(
      screen.getByText(
        /直近 4 週のカイポケの記録で、4 週すべてに空きのある人がいた時間 = ○、3 週 = △/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/カイポケから取った日時: 10\/20 9:12/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^火曜 9時台 ○/ })).toBeInTheDocument();
    // 職員だけ (マネージャーは入れない) → 火曜 15 時台は ×。
    expect(screen.getByRole('button', { name: /^火曜 15時台 ×/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '前の週' })).not.toBeInTheDocument();
    expect(screen.queryByText('カイポケから取り直す')).not.toBeInTheDocument();
  });

  it('マスを押すと内訳を下に開く', () => {
    render(<AcceptanceFieldView />);
    fireEvent.click(screen.getByRole('button', { name: /^月曜 9時台/ }));
    const panel = screen.getByLabelText('マスの内訳');
    expect(panel).toHaveTextContent('入れる人: 職員A');
    expect(panel).toHaveTextContent('祝日（スポーツの日）のため数えていません');
  });
});
