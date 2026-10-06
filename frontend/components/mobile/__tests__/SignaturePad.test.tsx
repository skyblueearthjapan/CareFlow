/**
 * サインの画面 (signature-checkin-design-2026-10-06 §5-1・モック場面 2)。
 *
 * - 何か書くまで「サインして退出を記録」は押せない。
 * - 「消してもう一度」で消すと、また押せなくなる。
 * - 押すと PNG を 1 枚渡す。
 * - スマホ縦の案内「横向きにすると広く書けます」がある (表示は CSS の portrait:max-sm)。
 *
 * jsdom はキャンバスに描けないので、getContext / toBlob を差し替える。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { SignaturePad, exportSignaturePng } from '../SignaturePad';

function fakeContext(): CanvasRenderingContext2D {
  const noop = () => undefined;
  return new Proxy(
    {},
    {
      get: (_t, key) => (key === 'canvas' ? null : noop),
      set: () => true,
    },
  ) as unknown as CanvasRenderingContext2D;
}

// jsdom には PointerEvent が無い (fireEvent が clientX / pointerId を載せられない)。
if (typeof window.PointerEvent === 'undefined') {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  (window as unknown as { PointerEvent: unknown }).PointerEvent = PointerEventPolyfill;
}

const originalGetContext = HTMLCanvasElement.prototype.getContext;
const originalToBlob = HTMLCanvasElement.prototype.toBlob;

beforeEach(() => {
  HTMLCanvasElement.prototype.getContext = vi.fn(() =>
    fakeContext(),
  ) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.toBlob = function toBlob(cb: BlobCallback, type?: string) {
    cb(new Blob(['png'], { type: type ?? 'image/png' }));
  };
});

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = originalGetContext;
  HTMLCanvasElement.prototype.toBlob = originalToBlob;
});

function renderPad(onSave = vi.fn(), onCancel = vi.fn()) {
  render(
    <SignaturePad
      patientName="山田 花子 様"
      subtitle="2026 年 10 月 6 日（火） ・ 退出"
      statusChip={<span>位置: 一致 38m</span>}
      onSave={onSave}
      onCancel={onCancel}
    />,
  );
  return { onSave, onCancel };
}

function draw() {
  const canvas = screen.getByTestId('signature-canvas');
  fireEvent.pointerDown(canvas, { clientX: 10, clientY: 10, pointerId: 1 });
  fireEvent.pointerMove(canvas, { clientX: 40, clientY: 30, pointerId: 1 });
  fireEvent.pointerUp(canvas, { pointerId: 1 });
}

describe('SignaturePad', () => {
  it('利用者名・日付・位置の札と、押した時刻で記録する旨を出す', () => {
    renderPad();
    expect(screen.getByRole('dialog', { name: 'サインの画面' })).toBeInTheDocument();
    expect(screen.getByText('山田 花子 様')).toBeInTheDocument();
    expect(screen.getByText('2026 年 10 月 6 日（火） ・ 退出')).toBeInTheDocument();
    expect(screen.getByText('位置: 一致 38m')).toBeInTheDocument();
    expect(screen.getByText('押した時刻で記録')).toBeInTheDocument();
    expect(screen.getByText('この枠の中にサインをお願いします')).toBeInTheDocument();
    expect(screen.getByTestId('signature-rotate-hint')).toHaveTextContent(
      '横向きにすると広く書けます',
    );
    expect(screen.getByTestId('signature-rotate-hint').className).toContain('portrait:max-sm:flex');
  });

  it('書くまで「サインして退出を記録」は押せない・「消してもう一度」で戻る', () => {
    renderPad();
    const save = screen.getByRole('button', { name: /サインして退出を記録/ });
    const clear = screen.getByRole('button', { name: '消してもう一度' });
    expect(save).toBeDisabled();
    expect(clear).toBeDisabled();

    draw();
    expect(save).toBeEnabled();
    expect(clear).toBeEnabled();
    expect(screen.queryByText('この枠の中にサインをお願いします')).not.toBeInTheDocument();

    fireEvent.click(clear);
    expect(save).toBeDisabled();
    expect(screen.getByText('この枠の中にサインをお願いします')).toBeInTheDocument();
    // 「書き直す」は使わない (PO 決定)。
    expect(screen.queryByText('書き直す')).not.toBeInTheDocument();
  });

  it('触れただけ (点 1 つ・短い線) では押せない', () => {
    renderPad();
    const canvas = screen.getByTestId('signature-canvas');
    const save = screen.getByRole('button', { name: /サインして退出を記録/ });
    fireEvent.pointerDown(canvas, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    expect(save).toBeDisabled();
    // 書いたものは残る (消してもう一度は押せる)。
    expect(screen.getByRole('button', { name: '消してもう一度' })).toBeEnabled();
    fireEvent.pointerDown(canvas, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 15, clientY: 12, pointerId: 1 });
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    expect(save).toBeDisabled();
  });

  it('書いている間のほかの指 (手のひら) は無視する', () => {
    renderPad();
    const canvas = screen.getByTestId('signature-canvas');
    const save = screen.getByRole('button', { name: /サインして退出を記録/ });
    fireEvent.pointerDown(canvas, { clientX: 10, clientY: 10, pointerId: 1 });
    // 手のひら (pointerId 2) が大きく動いても、線の長さには入らない。
    fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, pointerId: 2 });
    fireEvent.pointerMove(canvas, { clientX: 300, clientY: 300, pointerId: 2 });
    expect(save).toBeDisabled();
    fireEvent.pointerMove(canvas, { clientX: 40, clientY: 30, pointerId: 1 });
    expect(save).toBeEnabled();
  });

  it('保存できなかったときの案内を出す', () => {
    render(
      <SignaturePad
        patientName="山田 花子 様"
        subtitle="退出"
        notice="この端末に保存できませんでした。電波が戻ってから送ります。"
        onSave={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByTestId('signature-notice')).toHaveTextContent('電波が戻ってから送ります');
  });

  it('押すと PNG を 1 枚渡す', async () => {
    const { onSave } = renderPad();
    draw();
    fireEvent.click(screen.getByRole('button', { name: /サインして退出を記録/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const blob = onSave.mock.calls[0][0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('image/png');
  });

  it('「やめる」で閉じる', () => {
    const { onCancel } = renderPad();
    fireEvent.click(screen.getByRole('button', { name: 'やめる' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('送信中は押せない', () => {
    render(
      <SignaturePad
        patientName="山田 花子 様"
        subtitle="退出"
        saving
        onSave={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    draw();
    expect(screen.getByRole('button', { name: /記録しています/ })).toBeDisabled();
  });

  it('exportSignaturePng は線が無くても白地の PNG を作る', async () => {
    const blob = await exportSignaturePng([], 2);
    expect(blob.type).toBe('image/png');
    const inked = await exportSignaturePng(
      [
        [
          [0.1, 0.5],
          [0.9, 0.6],
        ],
      ],
      2,
    );
    expect(inked).toBeInstanceOf(Blob);
  });
});
