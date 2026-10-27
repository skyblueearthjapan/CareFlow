'use client';

/**
 * 音声記録 v2 の本文（Markdown）を安全に出す部品（設計 v2 §3 P4）。
 *
 * 読み取りは `voiceMarkdownParser.ts`。ここでは React の通常の表示だけで組み立てるので、
 * AI の文に HTML のタグやリンクが混ざっても**文字のまま**出る
 * （`dangerouslySetInnerHTML` は使わない・`<a>` も作らない）。
 *
 * 時刻 `[m:ss]` は「12:05 ごろ」と出す（時刻は約 20〜60 秒ずれる・§8）。
 * `onSeek` を渡すと押せる文字になり、押すと時刻の 15 秒前から再生する。
 * 音声が消えている記録では `onSeek` を渡さない（押せない文字になる）。
 */

import type { ReactNode } from 'react';

import {
  aroundLabel,
  parseVoiceMarkdown,
  seekStartSec,
  type MdBlock,
  type MdInline,
} from '@/components/records/voiceMarkdownParser';

const NEEDS_CHECK = '【要確認】';

/** 経過秒を受け取り、その 15 秒前から再生する。 */
export type SeekHandler = (startSec: number) => void;

/** 時刻の文字（押せる / 押せない）。要確認の一覧でも使う。 */
export function TimeMark({ sec, onSeek }: { sec: number; onSeek?: SeekHandler | null }) {
  const label = aroundLabel(sec);
  if (!onSeek) {
    return (
      <span className="tnum whitespace-nowrap text-xs text-text-muted" data-testid="voice-time">
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      className="tnum mx-0.5 inline-flex items-center whitespace-nowrap rounded-full border border-border-default bg-bg-muted px-1.5 text-xs text-brand-primary hover:bg-brand-primary-light"
      aria-label={`${label}から聞く`}
      title="少し前から再生します"
      data-testid="voice-time"
      onClick={() => onSeek(seekStartSec(sec))}
    >
      {label}
    </button>
  );
}

/** 文字の中の【要確認】だけ目立たせる。 */
function textWithMarks(text: string, key: string): ReactNode[] {
  return text.split(NEEDS_CHECK).flatMap((piece, k) => {
    const out: ReactNode[] = [];
    if (k > 0) {
      out.push(
        <span key={`${key}-m${k}`} className="font-semibold text-warning-strong">
          {NEEDS_CHECK}
        </span>,
      );
    }
    if (piece) out.push(piece);
    return out;
  });
}

function Inlines({ inlines, onSeek }: { inlines: MdInline[]; onSeek?: SeekHandler | null }) {
  return (
    <>
      {inlines.map((x, i) => {
        const node =
          x.kind === 'time' ? (
            <TimeMark key={i} sec={x.sec} onSeek={onSeek} />
          ) : (
            textWithMarks(x.text, String(i))
          );
        return x.strong ? <strong key={i}>{node}</strong> : <span key={i}>{node}</span>;
      })}
    </>
  );
}

function Block({ block, onSeek }: { block: MdBlock; onSeek?: SeekHandler | null }) {
  switch (block.kind) {
    case 'heading':
      return block.level <= 2 ? (
        <h4 className="pt-1 text-sm font-bold text-text-primary">
          <Inlines inlines={block.inlines} onSeek={onSeek} />
        </h4>
      ) : (
        <h5 className="text-sm font-semibold text-text-secondary">
          <Inlines inlines={block.inlines} onSeek={onSeek} />
        </h5>
      );
    case 'para':
      return (
        <p className="whitespace-pre-wrap">
          <Inlines inlines={block.inlines} onSeek={onSeek} />
        </p>
      );
    case 'bullets':
      return (
        <ul className="list-disc space-y-0.5 pl-5">
          {block.items.map((item, i) => (
            <li key={i}>
              <Inlines inlines={item.inlines} onSeek={onSeek} />
              {item.children.length > 0 && (
                <ul className="list-[circle] space-y-0.5 pl-5">
                  {item.children.map((child, k) => (
                    <li key={k}>
                      <Inlines inlines={child} onSeek={onSeek} />
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      );
    case 'numbers':
      return (
        <ol className="list-decimal space-y-0.5 pl-5">
          {block.items.map((item, i) => (
            <li key={i} value={item.num}>
              <Inlines inlines={item.inlines} onSeek={onSeek} />
            </li>
          ))}
        </ol>
      );
    case 'table':
      return (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {block.head.map((cell, k) => (
                  <th
                    key={k}
                    className="border border-border-default bg-bg-muted px-2 py-1 text-left font-semibold"
                  >
                    <Inlines inlines={cell} onSeek={onSeek} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, k) => (
                    <td key={k} className="border border-border-default px-2 py-1 align-top">
                      <Inlines inlines={cell} onSeek={onSeek} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

export function VoiceMarkdown({
  markdown,
  onSeek,
  className,
  testId = 'voice-markdown',
}: {
  markdown: string | null | undefined;
  onSeek?: SeekHandler | null;
  className?: string;
  testId?: string;
}) {
  const blocks = parseVoiceMarkdown(markdown);
  return (
    <div className={`space-y-2 text-sm text-text-primary ${className ?? ''}`} data-testid={testId}>
      {blocks.map((b, i) => (
        <Block key={i} block={b} onSeek={onSeek} />
      ))}
    </div>
  );
}
