'use client';

/**
 * WeekDiffView — 対象週の反映内容を「週ビュー」で表示 (K-2 UI)。
 *
 * diff-local が作った CorrectionSheet の各修正を、月〜日の7列グリッドに日付で
 * 振り分けて表示する。アクション別に色分けし、編集は before→after を見せる。
 * 「この週で何が変わるか」を一目で確認してから apply するための画面。
 */
import type { CorrectionItem } from '@/lib/schemas/integration';

const WEEKDAY = ['月', '火', '水', '木', '金', '土', '日'];

const ACTION_META: Record<string, { label: string; cls: string; dot: string }> = {
  add: { label: '追加', cls: 'bg-success-bg text-success', dot: 'bg-success' },
  delete: { label: '削除', cls: 'bg-error-bg text-error', dot: 'bg-error' },
  update: { label: '編集', cls: 'bg-warning-bg text-warning-strong', dot: 'bg-warning' },
  edit: { label: '編集', cls: 'bg-warning-bg text-warning-strong', dot: 'bg-warning' },
  date_change: { label: '日付変更', cls: 'bg-info-bg text-info', dot: 'bg-info' },
  companion_change: {
    label: '同行変更',
    cls: 'bg-c-coupled-bg text-c-coupled',
    dot: 'bg-c-coupled',
  },
};

function field(obj: unknown, key: string): string {
  if (obj && typeof obj === 'object' && key in obj) {
    const v = (obj as Record<string, unknown>)[key];
    return v == null ? '' : String(v);
  }
  return '';
}

function dayOf(item: CorrectionItem): number | null {
  const d = field(item.after, 'date') || field(item.before, 'date');
  const n = Number.parseInt(d, 10);
  return Number.isFinite(n) ? n : null;
}

export function WeekDiffView({ weekStart, items }: { weekStart: Date; items: CorrectionItem[] }) {
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    return d;
  });

  const byDay = new Map<number, CorrectionItem[]>();
  for (const it of items) {
    const n = dayOf(it);
    if (n == null) continue;
    const arr = byDay.get(n);
    if (arr) arr.push(it);
    else byDay.set(n, [it]);
  }

  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-[900px] grid-cols-7 gap-2">
        {days.map((d, i) => {
          const list = byDay.get(d.getDate()) ?? [];
          return (
            <div key={i} className="rounded-lg border border-border-default bg-bg-muted/40">
              <div className="border-b border-border-subtle px-2 py-1.5 text-center text-xs font-medium text-text-secondary">
                {d.getMonth() + 1}/{d.getDate()}{' '}
                <span className={i >= 5 ? 'text-error' : ''}>（{WEEKDAY[i]}）</span>
                {list.length > 0 && (
                  <span className="ml-1 text-[10px] text-text-muted">{list.length}件</span>
                )}
              </div>
              <div className="min-h-[96px] space-y-1.5 p-1.5">
                {list.length === 0 ? (
                  <p className="pt-6 text-center text-[11px] text-text-muted">—</p>
                ) : (
                  list.map((it) => <CorrectionCard key={it.id} item={it} />)
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CorrectionCard({ item }: { item: CorrectionItem }) {
  const meta = ACTION_META[item.action] ?? {
    label: item.action,
    cls: 'bg-bg-muted text-text-secondary',
    dot: 'bg-border-strong',
  };
  const name = field(item.after, 'user_name') || field(item.before, 'user_name') || '—';
  const timeAfter = field(item.after, 'start_time');
  const timeBefore = field(item.before, 'start_time');
  const staffAfter = field(item.after, 'staff1');
  const staffBefore = field(item.before, 'staff1');
  const isEdit = item.action === 'edit' || item.action === 'update';
  const unresolved = !item.patient_id;

  return (
    <div className="rounded-md border border-border-subtle bg-bg-base px-2 py-1.5 text-[11px] shadow-xs">
      <div className="mb-1 flex items-center justify-between gap-1">
        <span className={`inline-flex items-center rounded px-1.5 py-0.5 font-medium ${meta.cls}`}>
          {meta.label}
        </span>
        {unresolved && (
          <span
            className="text-warning-strong"
            title="CareFlow の患者マスタに未登録（apply時にスキップされます）"
          >
            要確認
          </span>
        )}
      </div>
      <div className="truncate font-medium text-text-primary" title={name}>
        {name}
      </div>
      <div className="mt-0.5 text-text-secondary">
        {isEdit && timeBefore && timeBefore !== timeAfter ? (
          <span>
            <span className="text-text-muted line-through">{timeBefore}</span> → {timeAfter}
          </span>
        ) : (
          <span>{timeAfter || timeBefore || '--:--'}</span>
        )}
      </div>
      {(staffAfter || staffBefore) && (
        <div className="mt-0.5 truncate text-text-muted">
          {isEdit && staffBefore && staffBefore !== staffAfter ? (
            <span>
              <span className="line-through">{staffBefore}</span> → {staffAfter}
            </span>
          ) : (
            staffAfter || staffBefore
          )}
        </div>
      )}
    </div>
  );
}
