/**
 * VoiceMarkdown（v2 の本文を安全に出す部品）の vitest。
 *
 * 縛る挙動:
 *   1. AI の文に混ざったタグ・`javascript:` のリンクは文字のまま（要素にならない）
 *   2. 見出し・箇条書き・番号・表・太字を React の要素で出す
 *   3. `[m:ss]` は「m:ss ごろ」。押すと 15 秒前（0 未満は 0）を渡す
 *   4. `onSeek` が無い（音声が消えた）ときは押せない文字
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { VoiceMarkdown } from '../VoiceMarkdown';

describe('VoiceMarkdown', () => {
  it('タグやリンクは文字のまま出る', () => {
    const { container } = render(
      <VoiceMarkdown
        markdown={
          '<script>alert(1)</script>\n<b>太く</b> <img src=x onerror=alert(1)>\n[押して](javascript:alert(1))'
        }
      />,
    );
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('a')).toBeNull();
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
    expect(screen.getByText('<b>太く</b> <img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(screen.getByText('[押して](javascript:alert(1))')).toBeInTheDocument();
  });

  it('見出し・箇条書き（入れ子）・番号・表・太字', () => {
    const { container } = render(
      <VoiceMarkdown
        markdown={[
          '## 様子',
          '- **むくみ** あり',
          '  - 右足',
          '1. 足浴',
          '| 項目 | 値 |',
          '|---|---|',
          '| 体温 | 36.5 |',
        ].join('\n')}
      />,
    );
    expect(screen.getByRole('heading', { name: '様子' })).toBeInTheDocument();
    expect(container.querySelector('strong')).toHaveTextContent('むくみ');
    expect(container.querySelectorAll('ul ul li')).toHaveLength(1);
    expect(container.querySelector('ol li')).toHaveTextContent('足浴');
    expect(screen.getByRole('columnheader', { name: '項目' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '36.5' })).toBeInTheDocument();
  });

  it('時刻を押すと 15 秒前から（0 未満は 0）', () => {
    const onSeek = vi.fn();
    render(<VoiceMarkdown markdown={'血圧 132/78 [12:05]\n挨拶 [0:10]'} onSeek={onSeek} />);
    fireEvent.click(screen.getByRole('button', { name: '12:05 ごろから聞く' }));
    expect(onSeek).toHaveBeenLastCalledWith(710);
    fireEvent.click(screen.getByRole('button', { name: '0:10 ごろから聞く' }));
    expect(onSeek).toHaveBeenLastCalledWith(0);
  });

  it('onSeek が無ければ押せない文字', () => {
    render(<VoiceMarkdown markdown="血圧 132/78 [12:05]" />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('12:05 ごろ')).toBeInTheDocument();
  });
});
