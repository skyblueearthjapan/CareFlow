'use client';

import { useCallback } from 'react';
import { Sparkles } from 'lucide-react';

import { AiInputModal } from '@/components/AiInputModal';
import { useUIStore } from '@/lib/stores/ui';
import { useAiSubmissionHandler } from '@/lib/ai/useAiSubmissionHandler';
import { useDraggableFab } from '@/lib/hooks/useDraggableFab';

/**
 * モバイル用 AI 入力 FAB.
 *
 * デスクトップ版 `AiFab` と異なる点:
 * - ボタンサイズ 64px (h-16 w-16) — タップターゲット拡大
 * - 初期位置はボトムナビ (h-16 = 64px) 上 + 余白 24px、右端 16px
 * - 音声入力主体 (`voiceFirst`)
 *
 * ドラッグ仕様 (PC 版と同じ AssistiveTouch 風):
 * - 押下→ドラッグ→離す で位置移動。`localStorage` に永続化。
 * - タップ(動かさず離す)で AiInputModal を開く。
 * - 端末横向き / リサイズ時にも画面外へ追い出されないよう自動 clamp。
 * - `useDraggableFab` を PC 版と共有。挙動改善は両方に反映される。
 *
 * 詳細仕様: `docs/design/10-mobile.md` §10-8 / `docs/plans/v2-allocation-redesign.md` §3.5
 *
 * W7-FE1: `useAiSubmissionHandler` を使って `pending_requests` 統合を追加。
 * - `isMobile: true` → admin/manager でも即時反映せず `pending` として申請
 * - RBAC マッピングにより staff の権限外操作は out_of_scope としてガード
 * - missing_fields がある場合は `MissingInfoModal` で補完を促す
 */

const FAB_SIZE = 64; // h-16 w-16
const RIGHT_MARGIN = 16; // 既存の right-4 と一致
const BOTTOM_OFFSET = 88; // ボトムナビ 64px + 余白 24px (safe-area は inset 取れない場合 0 として扱う)
const STORAGE_KEY = 'careflow:mobile-aifab-position';
// タップしながら少し指がブレるのは普通なので、PC より少し大きめに。
const MOBILE_DRAG_THRESHOLD = 8;

export function MobileAiFab() {
  const aiInputOpen = useUIStore((s) => s.aiInputOpen);
  const setAiInputOpen = useUIStore((s) => s.setAiInputOpen);

  // W7-FE1: pending_requests 統合 (Must-fix #6)
  const { onSubmitInterceptor, missingInfoSlot, submissionMode } = useAiSubmissionHandler({
    isMobile: true,
  });

  const { position, dragging, wasDragRef, handlers } = useDraggableFab({
    fabSize: FAB_SIZE,
    storageKey: STORAGE_KEY,
    dragThreshold: MOBILE_DRAG_THRESHOLD,
    initialPosition: () => ({
      x: window.innerWidth - FAB_SIZE - RIGHT_MARGIN,
      y: window.innerHeight - FAB_SIZE - BOTTOM_OFFSET,
    }),
  });

  const onClick = useCallback(() => {
    if (wasDragRef.current) return; // suppress click that follows a drag
    setAiInputOpen(!aiInputOpen);
  }, [aiInputOpen, setAiInputOpen, wasDragRef]);

  // 初期位置が決まるまで(post-mount まで)ボタンは描画せず、モーダルだけ生やしておく
  // ことで、別 UI からの open リクエストを取り逃がさない。
  if (!position) {
    return (
      <AiInputModal
        submissionMode={submissionMode}
        onSubmitInterceptor={onSubmitInterceptor}
        missingInfoSlot={missingInfoSlot}
        voiceFirst
      />
    );
  }

  return (
    <>
      <button
        type="button"
        {...handlers}
        onClick={onClick}
        aria-label="AI入力 — ドラッグで移動可"
        className="fixed z-40 flex h-16 w-16 items-center justify-center rounded-full text-white shadow-lg active:scale-95"
        style={{
          left: position.x,
          top: position.y,
          background: 'linear-gradient(135deg, #0D9488, #14B8A6)',
          touchAction: 'none', // ドラッグ中のページスクロール抑制
          cursor: dragging ? 'grabbing' : 'grab',
          userSelect: 'none',
          opacity: dragging ? 0.85 : 1,
        }}
      >
        <Sparkles className="h-7 w-7" strokeWidth={1.75} />
      </button>
      <AiInputModal
        submissionMode={submissionMode}
        onSubmitInterceptor={onSubmitInterceptor}
        missingInfoSlot={missingInfoSlot}
        voiceFirst
      />
    </>
  );
}
