import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

import { ApprovalCard, PlanFrame, QuestionCard, RunCard } from '../parts';
import type { TimelineItem } from '../types';

const approval: Extract<TimelineItem, { kind: 'approval' }> = {
  kind: 'approval',
  id: 'a1',
  approvalId: 'v1',
  planId: 'p1',
  version: 1,
};

describe('ApprovalCard の押し間違い防止', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('出てから 0.4 秒は押せない', () => {
    const onDecide = vi.fn();
    render(<ApprovalCard item={approval} onDecide={onDecide} />);
    const btn = screen.getByRole('button', { name: 'この案で反映する' });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onDecide).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(401);
    });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onDecide).toHaveBeenCalledWith('v1', 'approve');
  });

  it('Enter キーでは反映されない', () => {
    const onDecide = vi.fn();
    render(<ApprovalCard item={approval} onDecide={onDecide} />);
    act(() => {
      vi.advanceTimersByTime(500);
    });
    const btn = screen.getByRole('button', { name: 'この案で反映する' });
    const notPrevented = fireEvent.keyDown(btn, { key: 'Enter' });
    expect(notPrevented).toBe(false); // preventDefault された
    expect(onDecide).not.toHaveBeenCalled();
  });

  it('画面全体の Enter でも反映されない', () => {
    const onDecide = vi.fn();
    render(<ApprovalCard item={approval} onDecide={onDecide} guardMs={0} />);
    fireEvent.keyDown(document.body, { key: 'Enter' });
    expect(onDecide).not.toHaveBeenCalled();
  });

  it('決まったあとは結果の行になる', () => {
    render(
      <ApprovalCard item={{ ...approval, decision: 'approved' }} onDecide={vi.fn()} guardMs={0} />,
    );
    expect(screen.queryByRole('button', { name: 'この案で反映する' })).toBeNull();
    expect(screen.getByText(/反映をお願いされました/)).toBeInTheDocument();
  });
});

describe('QuestionCard', () => {
  it('選ぶと答えが渡る(0.4 秒後から)', () => {
    vi.useFakeTimers();
    const onAnswer = vi.fn();
    render(
      <QuestionCard
        item={{
          kind: 'question',
          id: 'q1',
          questionId: 'q1',
          questions: [
            { question: '一日中でしょうか？', options: [{ label: '一日中' }, { label: '午前だけ' }] },
          ],
        }}
        onAnswer={onAnswer}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(401);
    });
    fireEvent.click(screen.getByRole('button', { name: '午前だけ' }));
    expect(onAnswer).toHaveBeenCalledWith('q1', ['午前だけ']);
    vi.useRealTimers();
  });
});

describe('PlanFrame', () => {
  it('iframe は sandbox 空 と srcdoc を持つ(スクリプト不可・不透明オリジン)', () => {
    render(<PlanFrame html="<p>案</p>" size="a4" title="案 1" />);
    const f = screen.getByTitle('案 1');
    expect(f.tagName).toBe('IFRAME');
    expect(f.getAttribute('sandbox')).toBe('');
    expect(f.getAttribute('srcdoc')).toBe('<p>案</p>');
    expect(f.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(f.getAttribute('src')).toBeNull();
  });

  it('A3 は 1587px 幅、A4 は 794px 幅で描く', () => {
    const { rerender } = render(<PlanFrame html="x" size="a4" fit={false} title="t" />);
    expect(screen.getByTitle('t').getAttribute('width')).toBe('794');
    rerender(<PlanFrame html="x" size="a3" fit={false} title="t" />);
    expect(screen.getByTitle('t').getAttribute('width')).toBe('1587');
  });
});

describe('RunCard の［元に戻す］', () => {
  const run: Extract<TimelineItem, { kind: 'run' }> = {
    kind: 'run',
    id: 'r1',
    runKind: 'apply',
    runId: 'run-1',
    ok: true,
    done: 2,
    total: 2,
    line: '2 件を反映しました',
    undoable: true,
    finished: true,
  };

  it('0.4 秒後から押せて run_id が渡る・Enter では押さない', () => {
    vi.useFakeTimers();
    const onUndo = vi.fn();
    render(<RunCard item={run} onUndo={onUndo} />);
    const btn = screen.getByRole('button', { name: '元に戻す' });
    expect(btn).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(401);
    });
    expect(fireEvent.keyDown(btn, { key: 'Enter' })).toBe(false);
    expect(onUndo).not.toHaveBeenCalled();
    fireEvent.click(btn);
    expect(onUndo).toHaveBeenCalledWith('run-1');
    vi.useRealTimers();
  });

  it('元に戻す結果・依頼済み・戻せないものにはボタンを出さない', () => {
    const { rerender } = render(<RunCard item={{ ...run, runKind: 'undo' }} onUndo={vi.fn()} guardMs={0} />);
    expect(screen.queryByRole('button', { name: '元に戻す' })).toBeNull();
    rerender(<RunCard item={{ ...run, undoRequested: true }} onUndo={vi.fn()} guardMs={0} />);
    expect(screen.queryByRole('button', { name: '元に戻す' })).toBeNull();
    rerender(<RunCard item={{ ...run, undoable: false }} onUndo={vi.fn()} guardMs={0} />);
    expect(screen.queryByRole('button', { name: '元に戻す' })).toBeNull();
  });
});
