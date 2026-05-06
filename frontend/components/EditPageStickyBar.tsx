/**
 * EditPageStickyBar — sticky 保存バー (W10-FE3).
 *
 * isDirty=true の間、画面下部に sticky で警告 + 保存ボタンを表示する。
 * AppShell の <main> は overflow-y-auto なので、<main> 内末尾に
 * sticky bottom-0 で配置するとスクロールに伴って末尾に張り付く。
 * fixed を使うと rounded-xl をはみ出すため使用しない。
 *
 * Usage:
 *   <EditPageStickyBar
 *     isDirty={formState.isDirty}
 *     isSaving={mutation.isPending}
 *     errorMessage={errorMessage ?? undefined}
 *     onDiscard={handleDiscard}
 *     onSave={handleSave}
 *   />
 */
'use client';

import * as React from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

export interface EditPageStickyBarProps {
  /** フォームに変更がある場合 true */
  isDirty: boolean;
  /** 保存処理中の場合 true — ボタンを disable + spinner 表示 */
  isSaving: boolean;
  /** API エラーメッセージ */
  errorMessage?: string;
  /** 「破棄」ボタン押下時のコールバック */
  onDiscard: () => void;
  /** 「更新」ボタン押下時のコールバック */
  onSave: () => void;
  /** 保存ボタンのラベル (デフォルト: '更新') */
  saveLabel?: string;
}

export function EditPageStickyBar({
  isDirty,
  isSaving,
  errorMessage,
  onDiscard,
  onSave,
  saveLabel = '更新',
}: EditPageStickyBarProps) {
  if (!isDirty) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="sticky-bar"
      className="sticky bottom-0 left-0 right-0 z-50 border-t border-warning/30 bg-warning/10 px-4 py-3"
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-sm text-text-secondary">
          <AlertTriangle className="h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
          <span>
            {errorMessage ? (
              <span className="text-error">{errorMessage}</span>
            ) : (
              '未保存の変更があります'
            )}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onDiscard} disabled={isSaving}>
            破棄
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={onSave}
            disabled={isSaving}
            className="bg-brand-primary text-white hover:bg-brand-primary/90"
          >
            {isSaving ? (
              <>
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" />
                保存中…
              </>
            ) : (
              saveLabel
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
