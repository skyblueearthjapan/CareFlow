/**
 * 【規約】盤面のピン留めは型と一致する訪問にのみ可能 (PO 決定 2026-08-08)。
 * 仕様: docs/plans/pin-and-movability-spec.md §1.3
 *
 * この規約は現状「突き合わせキーの副作用」で成立しており、どこにも明示されて
 * いなかった。突き合わせ方 (患者 × 曜日 × 開始時刻 × スロット の完全一致) を
 * 将来変えたときに黙って壊れないよう、ここで固定する。
 *
 * バックエンドには同じ制約を置かない (患者マスタからの型へのピン留めは、今週の
 * 配置と無関係に行える正当な操作のため)。よって FE テストが唯一の歯止めになる。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PinToggleButton, type VisitListItem } from '../WeekdayScheduleCard';

function makeVisit(overrides: Partial<VisitListItem> = {}): VisitListItem {
  return {
    key: 'v1',
    patient_id: 'p1',
    start_time: '10:25',
    patient_name: '渡邉 愛',
    ...overrides,
  } as VisitListItem;
}

describe('盤面のピン留め: 型と一致する訪問にのみ可能', () => {
  it('型と一致している訪問はピン留めできる', () => {
    render(
      <PinToggleButton
        visit={makeVisit({ fixed_visit_id: 'pfv-1', is_pinned: false })}
        onTogglePin={vi.fn()}
      />,
    );
    expect(screen.getByTestId('weekday-pin-btn-v1')).not.toBeDisabled();
  });

  it('型とズレている訪問はピン留めできず、型の時刻を理由として示す', () => {
    // 型は 13:00、今週の実配置は 10:25 → 突き合わせ失敗で fixed_visit_id は null。
    render(
      <PinToggleButton
        visit={makeVisit({ fixed_visit_id: null, master_start_time: '13:00' })}
        onTogglePin={vi.fn()}
      />,
    );
    const btn = screen.getByTestId('weekday-pin-btn-v1');
    expect(btn).toBeDisabled();
    // 「先に固定枠登録が必要」は嘘になる (固定枠は存在する)。
    expect(btn).toHaveAttribute(
      'title',
      '固定訪問スケジュールは 13:00 です。この時間帯では完全固定にできません',
    );
  });

  it('そもそも固定枠が無い訪問は従来どおりの案内にする', () => {
    render(
      <PinToggleButton
        visit={makeVisit({ fixed_visit_id: null, master_start_time: null })}
        onTogglePin={vi.fn()}
      />,
    );
    const btn = screen.getByTestId('weekday-pin-btn-v1');
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', '先に固定枠登録が必要');
  });

  it('ピン留め済みの訪問は解除操作ができる (無効化しない)', () => {
    render(
      <PinToggleButton
        visit={makeVisit({ fixed_visit_id: 'pfv-1', is_pinned: true })}
        onTogglePin={vi.fn()}
      />,
    );
    expect(screen.getByTestId('weekday-pin-btn-v1')).not.toBeDisabled();
  });
});
