'use client';

/**
 * SyncStripRow — 同期ストリップの「カード行」(方向性A・docs/mockups/sync-strip-mock.html)。
 *
 * 1 行 = 差分 1 件。左に 日付/種別、中央に 種別タグ + 「誰が・何が・どう変わる」の
 * 1 文、右端に操作ボタン。行の本文を押すと選択され、直下に詳細 (children) が開く。
 *
 * select は使わない (モックの決定): 一覧のまま読めて、操作は行の右端で完結する。
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/** 種別タグの色 (DiffDetailCard と同じ配色: 新規=ブランド / 変更=注意 / 取消=情報)。 */
export type SyncRowTone = 'add' | 'update' | 'delete' | 'na';

const TONE_CLS: Record<SyncRowTone, string> = {
  add: 'bg-brand-primary-50 text-brand-primary-hover',
  update: 'bg-warning-bg text-warning-strong',
  delete: 'bg-info-bg text-info-strong',
  na: 'bg-bg-muted text-text-muted',
};

export interface SyncRowAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** 主操作 (取り込む / 送る) を塗りボタンにする。 */
  primary?: boolean;
  title?: string;
  testId?: string;
}

export interface SyncStripRowProps {
  /** 8/20(木) など。 */
  dateLabel: string;
  /** 訪問 / イベント / スタッフ など。 */
  kindLabel: string;
  tag: { label: string; tone: SyncRowTone };
  /** 「久須見 様 10:00」のような主語 + 時刻。 */
  headline: string;
  /** 「担当 熊澤 → 佐藤」のような変化点 (無ければ省く)。 */
  change?: string;
  /** 「カイポケ側で変わっている」などの補足。 */
  note?: string;
  /** 操作できない行 (自動送信不可) を薄くする。 */
  muted?: boolean;
  selected?: boolean;
  onSelect?: () => void;
  actions?: SyncRowAction[];
  testId?: string;
  /** 選択中に行の直下へ開く詳細 (「何から何へ」表)。 */
  children?: React.ReactNode;
}

export function SyncStripRow({
  dateLabel,
  kindLabel,
  tag,
  headline,
  change,
  note,
  muted = false,
  selected = false,
  onSelect,
  actions = [],
  testId,
  children,
}: SyncStripRowProps) {
  return (
    <li className="space-y-1">
      <div
        className={cn(
          'flex items-start gap-3 rounded-lg border bg-bg-base px-3 py-2',
          selected ? 'border-brand-primary ring-2 ring-brand-primary-50' : 'border-border-subtle',
          muted && 'opacity-60',
        )}
        data-testid={testId}
      >
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          disabled={onSelect == null}
          className="flex min-w-0 flex-1 items-start gap-3 text-left disabled:cursor-default"
        >
          <span className="w-[5.5rem] shrink-0 text-[13px] font-bold text-text-secondary">
            {dateLabel}
            <small className="block text-[11px] font-normal text-text-muted">{kindLabel}</small>
          </span>
          <span className="min-w-0 flex-1">
            <span
              className={cn(
                'mr-1.5 inline-block rounded-full px-1.5 py-px text-[11px] font-bold',
                TONE_CLS[tag.tone],
              )}
            >
              {tag.label}
            </span>
            <b className="text-[14px] text-text-primary">{headline}</b>
            {change ? (
              <span className="ml-1.5 text-[13px] font-bold text-warning-strong">{change}</span>
            ) : null}
            {note ? <span className="mt-0.5 block text-[12px] text-text-muted">{note}</span> : null}
          </span>
        </button>
        <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
          {actions.map((a) => (
            <Button
              key={a.label}
              type="button"
              size="sm"
              variant={a.primary ? 'default' : 'outline'}
              className="h-7 px-2.5 text-[13px]"
              disabled={a.disabled}
              title={a.title}
              data-testid={a.testId}
              onClick={a.onClick}
            >
              {a.label}
            </Button>
          ))}
        </div>
      </div>
      {children}
    </li>
  );
}
