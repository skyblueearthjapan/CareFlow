'use client';

/**
 * 同行モードの下部固定バー (§7.1 / general-accompaniment-design.md §4)。
 *
 * 同行スタッフのセレクタ (新人を先頭にグルーピング) / 対象の二択
 * (コース(曜日)単位・患者単位) / 「◯コース＋◯件選択中」/ 警告領域 /
 * 「☑ コース選択を毎週の既定にする」/［確定］［キャンセル］。
 * 重複が残る間は確定を無効化 (確定ブロック)。
 * ロジックは持たず、`useAccompanimentController` の bar props を描画するだけ。
 */
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import type { AccompanimentBarProps } from './useAccompanimentController';

export function AccompanimentBar({
  staffOptions,
  selectedStaffId,
  onSelectStaff,
  targetMode,
  onChangeTargetMode,
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
  // 新人は先頭グループへ (同行の主役は依然として新人だが、一般スタッフも選べる)。
  const trainees = staffOptions.filter((s) => s.is_trainee === true);
  const others = staffOptions.filter((s) => s.is_trainee !== true);

  return (
    <div
      className="sticky bottom-0 z-[20] mt-2 rounded-t-lg border border-border-default bg-bg-base p-3 shadow-[var(--shadow-md)]"
      data-testid="accompaniment-bar"
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] font-bold text-brand-primary">👥 同行</span>

        {/* 同行スタッフのセレクタ (新人を先頭グループに・新人はバッジ付き)。 */}
        <label className="flex items-center gap-1.5 text-[12px] text-text-primary">
          <span className="text-text-muted">同行者</span>
          <select
            value={selectedStaffId ?? ''}
            onChange={(e) => onSelectStaff(e.target.value)}
            data-testid="accompaniment-staff-select"
            className="rounded border border-border-default bg-bg-base px-1.5 py-0.5 text-[12px] font-bold text-text-primary"
          >
            <option value="" disabled>
              （同行するスタッフを選択）
            </option>
            {trainees.length > 0 && (
              <optgroup label="新人">
                {trainees.map((s) => (
                  <option key={s.id} value={s.id} data-trainee="true">
                    {s.name}（新人）
                  </option>
                ))}
              </optgroup>
            )}
            {others.length > 0 && (
              <optgroup label="スタッフ">
                {others.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>

        {/* 対象の二択 (確定#2: armed target の絞り込み。既存の混在は壊さない)。 */}
        <span
          className="flex items-center gap-1 text-[12px] text-text-primary"
          data-testid="accompaniment-target-mode"
        >
          <span className="text-text-muted">対象</span>
          <span className="inline-flex overflow-hidden rounded border border-border-default">
            <button
              type="button"
              onClick={() => onChangeTargetMode('course')}
              aria-pressed={targetMode === 'course'}
              data-testid="accompaniment-target-course"
              className={cn(
                'px-2 py-0.5 text-[12px] font-bold',
                targetMode === 'course'
                  ? 'bg-brand-primary text-white'
                  : 'bg-bg-base text-text-secondary',
              )}
            >
              コース(曜日)単位
            </button>
            <button
              type="button"
              onClick={() => onChangeTargetMode('visit')}
              aria-pressed={targetMode === 'visit'}
              data-testid="accompaniment-target-visit"
              className={cn(
                'border-l border-border-default px-2 py-0.5 text-[12px] font-bold',
                targetMode === 'visit'
                  ? 'bg-brand-primary text-white'
                  : 'bg-bg-base text-text-secondary',
              )}
            >
              患者単位
            </button>
          </span>
        </span>

        <span
          className="tnum text-[12px] font-semibold text-text-primary"
          data-testid="accompaniment-count"
        >
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
                  : '同行するスタッフを選択してください'
            }
          >
            {isSaving ? '保存中…' : '確定'}
          </Button>
        </div>
      </div>

      {/* 使い方ガイド (PO報告 2026-07-12: 何をクリックすればよいか分からない)。
          未選択のうちは info トーンで大きめに、選択が始まったら控えめに残す。
          対象の二択に応じて「今クリックできる場所」だけを案内する。 */}
      <p
        className={cn(
          'mt-2 rounded-md px-2.5 py-1.5 text-[11.5px] leading-relaxed',
          courseCount + visitCount === 0
            ? 'border border-info/40 bg-info-bg font-medium text-info'
            : 'text-text-muted',
        )}
        data-testid="accompaniment-guide"
      >
        {targetMode === 'course' ? (
          <>
            使い方: 上のタイムラインで
            <span className="font-bold">コースの曜日ヘッダー</span>
            （例: 稲毛A の「月」）をクリックすると
            <span className="font-bold">その日のコースまるごと</span>
            に同行が付きます。患者様ごとに付けたいときは「患者単位」に切り替えてください。
          </>
        ) : (
          <>
            使い方: 上のタイムラインで
            <span className="font-bold">患者カード</span>をクリックすると
            <span className="font-bold">その患者だけ</span>
            に同行が付きます。コースまるごとに付けたいときは「コース(曜日)単位」に切り替えてください。
          </>
        )}
        もう一度クリックで解除 → 最後に［確定］を押してください。
      </p>

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
