'use client';

/**
 * InboundCalendar — カイポケ → CareFlow 取り込みの差分プレビュー (月〜土6列グリッド)。
 *
 * 旧 InboundPanel の InboundDiffView を、下段の大きなカレンダー枠に描くため分離した
 * (中身は不変)。差分未取得 (sheetId 無し) のときは空状態を出す。
 */
import { Skeleton } from '@/components/ui/skeleton';
import type { CorrectionItem } from '@/lib/schemas/integration';

import { type InboundVm, WEEKDAYS, field } from './useInbound';

const ACTION_META: Record<string, { label: string; cls: string }> = {
  add: { label: 'カイポケ追加', cls: 'bg-success-bg text-success' },
  delete: { label: 'キャンセル候補', cls: 'bg-error-bg text-error' },
  update: { label: '時間変更', cls: 'bg-warning-bg text-warning-strong' },
  edit: { label: '時間変更', cls: 'bg-warning-bg text-warning-strong' },
  date_change: { label: '日付変更', cls: 'bg-info-bg text-info' },
  companion_change: { label: '同行変更', cls: 'bg-bg-muted text-text-secondary' },
};

export function InboundCalendar({ vm }: { vm: InboundVm }) {
  const { sheetId, weekStart, items, itemsQuery } = vm;

  if (!sheetId) {
    return (
      <p className="py-8 text-center text-sm text-text-muted">
        ❶ でカイポケの現況を取得すると、ここに取り込みプレビューが出ます。
      </p>
    );
  }

  if (itemsQuery.isLoading) {
    return <Skeleton className="h-32 w-full" />;
  }

  return <InboundDiffView weekStart={weekStart} items={items} />;
}

/** 月〜土の6列グリッドに差分アイテムを日付で振り分けて表示。 */
function InboundDiffView({ weekStart, items }: { weekStart: Date; items: CorrectionItem[] }) {
  const days = Array.from({ length: 6 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    return d;
  });

  const byDay = new Map<number, CorrectionItem[]>();
  for (const it of items) {
    const dayStr = field(it.after, 'date') || field(it.before, 'date');
    const n = Number.parseInt(dayStr, 10);
    if (!Number.isFinite(n)) continue;
    const arr = byDay.get(n);
    if (arr) arr.push(it);
    else byDay.set(n, [it]);
  }

  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-[720px] grid-cols-6 gap-2">
        {days.map((d, i) => {
          const list = byDay.get(d.getDate()) ?? [];
          return (
            <div key={i} className="rounded-lg border border-border-default bg-bg-muted">
              <div className="border-b border-border-subtle px-2 py-1.5 text-center text-xs font-medium text-text-secondary">
                {d.getMonth() + 1}/{d.getDate()} （{WEEKDAYS[i]}）
                {list.length > 0 && (
                  <span className="ml-1 text-[10px] text-text-muted">{list.length}件</span>
                )}
              </div>
              <div className="min-h-[80px] space-y-1.5 p-1.5">
                {list.length === 0 ? (
                  <p className="pt-4 text-center text-[11px] text-text-muted">—</p>
                ) : (
                  list.map((it) => <InboundCard key={it.id} item={it} />)
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function InboundCard({ item }: { item: CorrectionItem }) {
  const meta = ACTION_META[item.action] ?? {
    label: item.action,
    cls: 'bg-bg-muted text-text-secondary',
  };
  const name = field(item.after, 'user_name') || field(item.before, 'user_name') || '—';
  const timeAfter = field(item.after, 'start_time');
  const timeBefore = field(item.before, 'start_time');
  const isEdit = item.action === 'edit' || item.action === 'update';
  const isExcluded = !item.include;

  return (
    <div
      className={[
        'rounded-md border px-2 py-1.5 text-[11px] shadow-xs',
        isExcluded ? 'border-border-subtle bg-bg-muted' : 'border-border-subtle bg-bg-base',
      ].join(' ')}
    >
      <div className="mb-1 flex items-center justify-between gap-1">
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 font-medium ${
            isExcluded ? 'bg-bg-muted text-text-muted' : meta.cls
          }`}
        >
          {meta.label}
        </span>
        {isExcluded && (
          <span className="text-[10px] text-text-muted" title="名寄せ未解決などで自動的に除外">
            取り込み対象外
          </span>
        )}
      </div>
      <div
        className={[
          'truncate font-medium',
          isExcluded ? 'text-text-muted' : 'text-text-primary',
        ].join(' ')}
        title={name}
      >
        {name}
      </div>
      <div className={['mt-0.5', isExcluded ? 'text-text-muted' : 'text-text-secondary'].join(' ')}>
        {isEdit && timeBefore && timeBefore !== timeAfter ? (
          <span>
            <span className="opacity-60 line-through">{timeBefore}</span> → {timeAfter}
          </span>
        ) : (
          <span>{timeAfter || timeBefore || '--:--'}</span>
        )}
      </div>
    </div>
  );
}
