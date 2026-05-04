'use client';

import { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';

export function AiFab() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <button
      type="button"
      onClick={() => setOpen((v) => !v)}
      aria-label="AI入力 (Cmd/Ctrl + K)"
      className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full text-white transition-transform hover:scale-105 active:scale-95"
      style={{
        background: 'linear-gradient(135deg, #0D9488, #14B8A6)',
        boxShadow: 'var(--shadow-fab)',
      }}
    >
      <Sparkles className="h-6 w-6" strokeWidth={1.75} />
      <span className="sr-only">{open ? 'AI 入力を閉じる' : 'AI 入力を開く'}</span>
      {/* TODO: D4 で Gemini 入力モーダルを実装 */}
    </button>
  );
}
