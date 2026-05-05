'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';

import { AiInputModal } from '@/components/AiInputModal';
import { useUIStore } from '@/lib/stores/ui';
import { useAiSubmissionHandler } from '@/lib/ai/useAiSubmissionHandler';

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
 */

const FAB_SIZE = 56; // h-14 w-14
const SCREEN_MARGIN = 8; // keep button inside the viewport with a small gutter
const DEFAULT_MARGIN = 24; // initial bottom-right offset
const DRAG_THRESHOLD = 5; // px movement before a press is treated as a drag
const STORAGE_KEY = 'careflow:aifab-position';

interface Position {
  x: number;
  y: number;
}

function clampToViewport(p: Position): Position {
  const maxX = Math.max(SCREEN_MARGIN, window.innerWidth - FAB_SIZE - SCREEN_MARGIN);
  const maxY = Math.max(SCREEN_MARGIN, window.innerHeight - FAB_SIZE - SCREEN_MARGIN);
  return {
    x: Math.min(Math.max(SCREEN_MARGIN, p.x), maxX),
    y: Math.min(Math.max(SCREEN_MARGIN, p.y), maxY),
  };
}

function loadSavedPosition(): Position | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (
      parsed &&
      typeof parsed === 'object' &&
      typeof (parsed as Position).x === 'number' &&
      typeof (parsed as Position).y === 'number'
    ) {
      return parsed as Position;
    }
    return null;
  } catch {
    return null;
  }
}

function defaultPosition(): Position {
  return {
    x: window.innerWidth - FAB_SIZE - DEFAULT_MARGIN,
    y: window.innerHeight - FAB_SIZE - DEFAULT_MARGIN,
  };
}

export function AiFab() {
  const aiInputOpen = useUIStore((s) => s.aiInputOpen);
  const setAiInputOpen = useUIStore((s) => s.setAiInputOpen);
  const toggleAiInput = useUIStore((s) => s.toggleAiInput);

  // W7-FE1: pending_requests 統合 (Must-fix #6)
  const { onSubmitInterceptor, missingInfoSlot, submissionMode } = useAiSubmissionHandler({
    isMobile: false,
  });

  const [position, setPosition] = useState<Position | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragStartRef = useRef<{
    pointerId: number;
    pointerX: number;
    pointerY: number;
    startX: number;
    startY: number;
  } | null>(null);
  // True between drag end and the (possibly suppressed) follow-up click.
  // Used to distinguish a real tap from the click event that browsers may
  // synthesize after a small drag.
  const wasDragRef = useRef(false);

  // Initial position (post-mount; needs `window`).
  useEffect(() => {
    setPosition(clampToViewport(loadSavedPosition() ?? defaultPosition()));
  }, []);

  // Re-clamp when the viewport resizes so the button never lands off-screen.
  useEffect(() => {
    const onResize = () => {
      setPosition((prev) => (prev ? clampToViewport(prev) : prev));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

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

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      if (!position) return;
      // Capture so we keep getting move/up events even when the cursor leaves the button.
      e.currentTarget.setPointerCapture(e.pointerId);
      dragStartRef.current = {
        pointerId: e.pointerId,
        pointerX: e.clientX,
        pointerY: e.clientY,
        startX: position.x,
        startY: position.y,
      };
      wasDragRef.current = false;
    },
    [position],
  );

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    const start = dragStartRef.current;
    if (!start || start.pointerId !== e.pointerId) return;
    const dx = e.clientX - start.pointerX;
    const dy = e.clientY - start.pointerY;
    if (!wasDragRef.current && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    if (!wasDragRef.current) {
      wasDragRef.current = true;
      setDragging(true);
    }
    setPosition(clampToViewport({ x: start.startX + dx, y: start.startY + dy }));
  }, []);

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    const start = dragStartRef.current;
    if (!start || start.pointerId !== e.pointerId) return;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // already released
    }
    const wasDrag = wasDragRef.current;
    dragStartRef.current = null;
    setDragging(false);

    if (wasDrag) {
      const finalPos = clampToViewport({
        x: start.startX + (e.clientX - start.pointerX),
        y: start.startY + (e.clientY - start.pointerY),
      });
      setPosition(finalPos);
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(finalPos));
      } catch {
        // quota / disabled — non-fatal
      }
      // Keep wasDragRef true so the follow-up click (if any) is suppressed,
      // then clear on next tick so a fresh tap works again.
      window.setTimeout(() => {
        wasDragRef.current = false;
      }, 0);
    }
    // Tap-without-drag: native click event will fire and trigger onClick below.
  }, []);

  const onClick = useCallback(() => {
    if (wasDragRef.current) return; // suppress click that follows a drag
    setAiInputOpen(!aiInputOpen);
  }, [aiInputOpen, setAiInputOpen]);

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
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
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
