'use client';

/**
 * 新人同行モードの下部固定バー (§7.1)。
 *
 * 「◯コース＋◯件選択中」/ 警告領域 / 「☑ コース選択を毎週の既定にする」/
 * ［確定］［キャンセル］。重複が残る間は確定を無効化 (確定ブロック)。
 * ロジックは持たず、`useAccompanimentController` の bar props を描画するだけ。
 */
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import type { AccompanimentBarProps } from './useAccompanimentController';

export function AccompanimentBar({
  trainees,
  selectedTraineeId,
  onSelectTrainee,
  courseCount,
  visitCount,
  overlapMessages,
  serverOverlapMessages,
  canConfirm,
  isSaving,
  setDefaultChecked,
  onToggleSetDefault,
  onConfirm,
  onCancel,
  isLoadingLinks,
}: AccompanimentBarProps) {
  // クライアント判定 + サーバ 422 を統合表示 (重複排除)。
  const warnings = Array.from(new Set([...overlapMessages, ...serverOverlapMessages]));

  return (
    <div
      className="sticky bottom-0 z-[20] mt-2 rounded-t-lg border border-border-default bg-bg-base p-3 shadow-[var(--shadow-md)]"
      data-testid="accompaniment-bar"
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] font-bold text-brand-primary">👥 新人同行</span>

        {/* 新人セレクタ (1人なら自動選択済みだが切替可)。 */}
        <label className="flex items-center gap-1.5 text-[12px] text-text-primary">
          <span className="text-text-muted">新人</span>
          <select
            value={selectedTraineeId ?? ''}
            onChange={(e) => onSelectTrainee(e.target.value)}
            data-testid="accompaniment-trainee-select"
            className="rounded border border-border-default bg-bg-base px-1.5 py-0.5 text-[12px] font-bold text-text-primary"
          >
            <option value="" disabled>
              （新人を選択）
            </option>
            {trainees.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>

        <span className="tnum text-[12px] font-semibold text-text-primary" data-testid="accompaniment-count">
          {courseCount}コース＋{visitCount}件選択中
        </span>

        {isLoadingLinks && <span className="text-[11px] text-text-muted">読み込み中…</span>}

        <label className="ml-auto flex items-center gap-1.5 text-[12px] text-text-primary">
          <input
            type="checkbox"
            checked={setDefaultChecked}
            onChange={(e) => onToggleSetDefault(e.target.checked)}
            data-testid="accompaniment-set-default"
            className="h-4 w-4 accent-[var(--brand-primary)]"
          />
          コース選択を毎週の既定にする
        </label>

        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onCancel}
            data-testid="accompaniment-cancel"
          >
            キャンセル
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={onConfirm}
            disabled={!canConfirm}
            data-testid="accompaniment-confirm"
            title={
              canConfirm
                ? undefined
                : warnings.length > 0
                  ? '時間の重複を解消してください'
                  : '新人を選択してください'
            }
          >
            {isSaving ? '保存中…' : '確定'}
          </Button>
        </div>
      </div>

      {/* 警告領域: 時間重複 (確定ブロック中)。 */}
      {warnings.length > 0 && (
        <ul
          className={cn(
            'mt-2 space-y-1 rounded-md border border-error/40 bg-error-bg px-2.5 py-1.5 text-[11.5px] font-medium text-error',
          )}
          data-testid="accompaniment-warnings"
        >
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
