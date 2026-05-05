'use client';

import { useEffect } from 'react';
import { Sparkles } from 'lucide-react';

import { AiInputModal } from '@/components/AiInputModal';
import { useUIStore } from '@/lib/stores/ui';

/**
 * Floating Action Button for the global AI input modal.
 *
 * Cmd/Ctrl + K toggles the modal — see `<AiInputModal />` for the actual
 * dialog, voice-input wiring, and Gemini round-trip.
 */
export function AiFab() {
  const aiInputOpen = useUIStore((s) => s.aiInputOpen);
  const setAiInputOpen = useUIStore((s) => s.setAiInputOpen);
  const toggleAiInput = useUIStore((s) => s.toggleAiInput);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        toggleAiInput();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleAiInput]);

  return (
    <>
      <button
        type="button"
        onClick={() => setAiInputOpen(!aiInputOpen)}
        aria-label="AI入力 (Cmd/Ctrl + K)"
        className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full text-white transition-transform hover:scale-105 active:scale-95"
        style={{
          background: 'linear-gradient(135deg, #0D9488, #14B8A6)',
          boxShadow: 'var(--shadow-fab)',
        }}
      >
        <Sparkles className="h-6 w-6" strokeWidth={1.75} />
        <span className="sr-only">{aiInputOpen ? 'AI 入力を閉じる' : 'AI 入力を開く'}</span>
      </button>
      <AiInputModal />
    </>
  );
}
