'use client';

/**
 * ExecutionLogViewer — kaipoke worker のリングバッファログの末尾表示。
 *
 * /integrations/live の logs[] をそのまま等幅で流す。新しい行が下に積まれるため
 * 末尾へ自動スクロールする。落ち着いた濃色面 + クリーム文字で「端末」的に見せる。
 */
import { useEffect, useRef } from 'react';

import { Card } from '@/components/ui/card';

export function ExecutionLogViewer({ lines }: { lines: string[] }) {
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // scrollIntoView はページ全体のスクロールも巻き込み、連携ページを開いた瞬間に
    // ログ位置まで下がってしまう (PO報告 2026-07-10)。ログ枠内だけを末尾へ送る。
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-border-subtle px-5 py-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">実行ログ</h2>
        <span className="text-xs text-text-muted">末尾 {lines.length} 行</span>
      </div>
      <div ref={boxRef} className="max-h-56 overflow-y-auto bg-stone-950 px-5 py-3">
        {lines.length === 0 ? (
          <p className="py-6 text-center text-xs text-white/40">ログはまだありません</p>
        ) : (
          <div className="space-y-0.5 font-mono text-xs leading-relaxed text-white/80">
            {lines.map((line, i) => (
              <div key={`${i}-${line.slice(0, 12)}`} className="whitespace-pre-wrap break-all">
                {line}
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
