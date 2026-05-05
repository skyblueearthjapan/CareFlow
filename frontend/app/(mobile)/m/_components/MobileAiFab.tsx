'use client';

import { Sparkles } from 'lucide-react';

import { AiInputModal } from '@/components/AiInputModal';
import { useUIStore } from '@/lib/stores/ui';
import { useAiSubmissionHandler } from '@/lib/ai/useAiSubmissionHandler';

/**
 * モバイル用 AI 入力 FAB.
 *
 * デスクトップ版 `AiFab` と異なり：
 * - 位置固定（右 16, 下 88：ボトムナビの上）
 * - ドラッグ移動なし（モバイルは画面が小さい）
 * - 音声入力主体（タップで `AiInputModal` を開く。音声モード優先化は Wave 5 で対応）
 *
 * 詳細仕様: `docs/design/10-mobile.md` §10-8 / `docs/plans/v2-allocation-redesign.md` §3.5
 *
 * W7-FE1: `useAiSubmissionHandler` を使って `pending_requests` 統合を追加。
 * - `isMobile: true` → admin/manager でも即時反映せず `pending` として申請
 * - RBAC マッピングにより staff の権限外操作は out_of_scope としてガード
 * - missing_fields がある場合は `MissingInfoModal` で補完を促す
 */
export function MobileAiFab() {
  const setAiInputOpen = useUIStore((s) => s.setAiInputOpen);

  // W7-FE1: pending_requests 統合 (Must-fix #6)
  const { onSubmitInterceptor, missingInfoSlot, submissionMode } = useAiSubmissionHandler({
    isMobile: true,
  });

  return (
    <>
      <button
        type="button"
        onClick={() => setAiInputOpen(true)}
        aria-label="AI入力"
        className="fixed right-4 z-40 flex h-16 w-16 items-center justify-center rounded-full text-white shadow-lg active:scale-95"
        style={{
          // ボトムナビ (h-16 = 64px) + 余白 24px、safe-area 対応
          bottom: 'calc(88px + env(safe-area-inset-bottom))',
          background: 'linear-gradient(135deg, #0D9488, #14B8A6)',
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
