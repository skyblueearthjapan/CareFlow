/**
 * push-pin.tsx render smoke テスト (CareFlow #P4-B).
 *
 * - PushPin (ピン留め中) は data-icon="push-pin" の svg を描画し、
 *   赤い丸頭が fill-error クラスで表現される.
 * - PushPinOff (未ピン) は data-icon="push-pin-off" の svg を描画し、丸頭は塗りなし.
 * - className はそのまま svg に透過する (サイズ指定を呼び出し側が渡せる).
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';

import { PushPin, PushPinOff } from '../push-pin';

describe('push-pin icons', () => {
  it('PushPin は赤い丸頭 (fill-error) を持つ svg を描画する', () => {
    const { container } = render(<PushPin className="h-4 w-4" />);
    const svg = container.querySelector('svg[data-icon="push-pin"]');
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute('class')).toContain('h-4 w-4');
    // 赤い丸頭 = fill-error の circle が存在する.
    const head = container.querySelector('circle.fill-error');
    expect(head).not.toBeNull();
  });

  it('PushPinOff は塗りなしの丸頭を持つ svg を描画する', () => {
    const { container } = render(<PushPinOff />);
    const svg = container.querySelector('svg[data-icon="push-pin-off"]');
    expect(svg).not.toBeNull();
    // fill-error の circle は存在しない (未ピンは灰色アウトライン).
    expect(container.querySelector('circle.fill-error')).toBeNull();
    expect(container.querySelector('circle[fill="none"]')).not.toBeNull();
  });
});
