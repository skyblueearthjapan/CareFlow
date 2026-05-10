'use client';

import { useCallback, useEffect } from 'react';
import { Sparkles } from 'lucide-react';

import { AiInputModal } from '@/components/AiInputModal';
import { useUIStore } from '@/lib/stores/ui';
import { useAiSubmissionHandler } from '@/lib/ai/useAiSubmissionHandler';
import { useDraggableFab } from '@/lib/hooks/useDraggableFab';

/**
 * Floating Action Button for the global AI input modal.
 *
 * Cmd/Ctrl + K toggles the modal — see `<AiInputModal />` for the actual
 * dialog, voice-input wiring, and Gemini round-trip.
 *
 * Drag behavior (AssistiveTouch-like):
 * - Press & drag to move the button anywhere within the viewport.
 * - Click without dragging opens the modal.
 * - Position persists across sessions in localStorage.
 *
 * Drag state machine is shared with the mobile FAB (`MobileAiFab`) via
 * `useDraggableFab` — change behavior there once and both UIs follow.
 */

const FAB_SIZE = 56; // h-14 w-14
const DEFAULT_MARGIN = 24; // initial bottom-right offset
const STORAGE_KEY = 'careflow:aifab-position';

export function AiFab() {
  const aiInputOpen = useUIStore((s) => s.aiInputOpen);
  const setAiInputOpen = useUIStore((s) => s.setAiInputOpen);
  const toggleAiInput = useUIStore((s) => s.toggleAiInput);

  // W7-FE1: pending_requests 統合 (Must-fix #6)
  const { onSubmitInterceptor, missingInfoSlot, submissionMode } = useAiSubmissionHandler({
    isMobile: false,
  });

  const { position, dragging, wasDragRef, handlers } = useDraggableFab({
    fabSize: FAB_SIZE,
    storageKey: STORAGE_KEY,
    initialPosition: () => ({
      x: window.innerWidth - FAB_SIZE - DEFAULT_MARGIN,
      y: window.innerHeight - FAB_SIZE - DEFAULT_MARGIN,
    }),
  });

  // Cmd/Ctrl + K shortcut.
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

  const onClick = useCallback(() => {
    if (wasDragRef.current) return; // suppress click that follows a drag
    setAiInputOpen(!aiInputOpen);
  }, [aiInputOpen, setAiInputOpen, wasDragRef]);

  // Avoid SSR/initial-render flicker by not rendering until we know where to place the button.
  if (!position) {
    return (
      <AiInputModal
        submissionMode={submissionMode}
        onSubmitInterceptor={onSubmitInterceptor}
        missingInfoSlot={missingInfoSlot}
      />
    );
  }

  return (
    <>
      <button
        type="button"
        {...handlers}
        onClick={onClick}
        aria-label="AI入力 (Cmd/Ctrl + K) — ドラッグで移動可"
        className="fixed z-50 flex h-14 w-14 items-center justify-center rounded-full text-white transition-shadow hover:shadow-xl"
        style={{
          left: position.x,
          top: position.y,
          background: 'linear-gradient(135deg, #0D9488, #14B8A6)',
          boxShadow: 'var(--shadow-fab)',
          touchAction: 'none', // prevent page scroll while dragging on touch devices
          cursor: dragging ? 'grabbing' : 'grab',
          userSelect: 'none',
          opacity: dragging ? 0.85 : 1,
        }}
      >
        <Sparkles className="h-6 w-6" strokeWidth={1.75} />
        <span className="sr-only">{aiInputOpen ? 'AI 入力を閉じる' : 'AI 入力を開く'}</span>
      </button>
      <AiInputModal
        submissionMode={submissionMode}
        onSubmitInterceptor={onSubmitInterceptor}
        missingInfoSlot={missingInfoSlot}
      />
    </>
  );
}
