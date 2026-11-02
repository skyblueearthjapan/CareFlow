'use client';

/**
 * 「やること」ページの「毎月のやること」タブ (管理者だけ)。
 *
 * 正典 = docs/plans/patient-tasks-recurring-design-2026-11-02.md §3-1・
 * モック docs/mockups/patient-tasks-recurring-mock.html。
 * 設定の一覧・作る／変える欄 (項目・対象の区分・個別に外す方・毎月何日から・最初に作る月・
 * 伝言・有効／停止)・止めた後の「未完了をまとめて取り消す」・来月の試し表示。
 * 作るのは BE の毎日の見回り (保存したときも、今月の設定日を過ぎていれば今月の分を作る)。
 */
import { useMemo, useState } from 'react';
import { Plus } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { cn } from '@/lib/utils';
import { useTaskItemTypes } from '@/lib/queries/tasks';
import {
  useCancelRecurringOpen,
  useRecurringCandidates,
  useRecurringPreview,
  useRecurringRules,
  useSaveRecurringRule,
} from '@/lib/queries/recurringTasks';
import {
  PATIENT_STATUS_LABELS,
  VISIT_CATEGORY_LABELS,
  type RecurringPreview,
  type RecurringTaskRuleInput,
  type RecurringTaskRuleRead,
  type VisitCategory,
} from '@/lib/schemas/recurring-task';

import { Pill } from './TaskPill';
import { taskMdWd, visitBriefLabel } from './taskFormat';

const CATEGORIES: VisitCategory[] = ['psychiatric', 'general'];
const DAYS = Array.from({ length: 28 }, (_, i) => i + 1);

function todayJst(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

/** YYYY-MM-DD → その月の 1 日 (offset か月後)。 */
function monthStart(ymd: string, offset = 0): string {
  const [y, m] = ymd.split('-').map(Number);
  const dt = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1 + offset, 1));
  return dt.toISOString().slice(0, 10);
}

function monthLabel(ymd: string): string {
  const [y, m] = ymd.split('-').map(Number);
  return `${y} 年 ${m} 月`;
}

function targetLabel(
  rule: Pick<RecurringTaskRuleRead, 'target_categories' | 'excluded_patients'>,
): string {
  const cats = rule.target_categories
    .map((c) => VISIT_CATEGORY_LABELS[c as VisitCategory] ?? c)
    .join('・');
  const ex = rule.excluded_patients.length;
  return ex > 0 ? `${cats}の方・${ex} 名を外す` : `${cats}の方`;
}

function ruleTitle(rule: RecurringTaskRuleRead): string {
  return rule.item_type_name ?? '伝言';
}

type Draft = RecurringTaskRuleInput & { id: string | null };

function draftFrom(rule: RecurringTaskRuleRead | null, today: string): Draft {
  if (!rule) {
    return {
      id: null,
      item_type_id: null,
      memo: '',
      day_of_month: 1,
      target_categories: ['psychiatric'],
      // 初めは来月から (今月は手で付けた分を使う・設計 §6 (f))。
      start_month: monthStart(today, 1),
      is_active: true,
      excluded_patient_ids: [],
    };
  }
  return {
    id: rule.id,
    item_type_id: rule.item_type_id,
    memo: rule.memo ?? '',
    day_of_month: rule.day_of_month,
    target_categories: rule.target_categories.filter((c): c is VisitCategory =>
      (CATEGORIES as string[]).includes(c),
    ),
    start_month: rule.start_month,
    is_active: rule.is_active,
    excluded_patient_ids: rule.excluded_patients.map((p) => p.patient_id),
  };
}

export function RecurringTasksPanel() {
  const today = todayJst();
  const nextMonth = monthStart(today, 1);
  const rulesQuery = useRecurringRules();
  const rules = rulesQuery.data ?? [];
  const [draft, setDraft] = useState<Draft | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const cancelOpen = useCancelRecurringOpen();

  const shownPreviewId = previewId ?? rules[0]?.id ?? null;
  const previewRule = rules.find((r) => r.id === shownPreviewId) ?? null;

  const onCancelOpen = (rule: RecurringTaskRuleRead) => {
    if (
      !window.confirm(
        `「${ruleTitle(rule)}」から作った未完了の ${rule.open_count} 件を取り消しますか？完了した分は残ります。`,
      )
    )
      return;
    cancelOpen.mutate(rule.id, {
      onSuccess: (res) => toast.success(`${res.cancelled_count} 件を取り消しました`),
      onError: (err) => toast.error('取り消せませんでした', { description: apiErrorMessage(err) }),
    });
  };

  return (
    <div className="space-y-4" data-testid="recurring-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 text-xs text-text-muted">
          毎月その日に、対象の方へやることを 1
          件ずつ作ります。出方・持ち越し・完了は、今のやることと同じです。作ったときのベルは出しません。
        </p>
        <Button
          type="button"
          onClick={() => setDraft(draftFrom(null, today))}
          data-testid="recurring-add-open"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          毎月のやることを作る
        </Button>
      </div>

      {draft && (
        <RuleForm
          key={draft.id ?? 'new'}
          initial={draft}
          excludedRefs={rules.find((r) => r.id === draft.id)?.excluded_patients ?? []}
          today={today}
          onClose={() => setDraft(null)}
          onSaved={(id) => {
            setDraft(null);
            setPreviewId(id);
          }}
        />
      )}

      <div className="overflow-x-auto rounded-lg border border-border-default bg-bg-base">
        <table className="w-full min-w-[720px] text-sm" data-testid="recurring-table">
          <thead className="border-b border-border-default bg-bg-muted text-left text-xs text-text-secondary">
            <tr>
              <th className="px-3 py-2 font-medium">項目</th>
              <th className="px-3 py-2 font-medium">対象</th>
              <th className="px-3 py-2 font-medium">毎月何日から</th>
              <th className="px-3 py-2 font-medium">対象の人数</th>
              <th className="px-3 py-2 font-medium">状態</th>
              <th className="px-3 py-2 font-medium" />
            </tr>
          </thead>
          <tbody>
            {rulesQuery.isLoading ? (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-text-muted">
                  読み込み中…
                </td>
              </tr>
            ) : rulesQuery.isError ? (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-error">
                  毎月のやることを読み込めませんでした：{apiErrorMessage(rulesQuery.error)}
                </td>
              </tr>
            ) : rules.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-text-muted">
                  毎月のやることはまだありません。
                </td>
              </tr>
            ) : (
              rules.map((r) => (
                <tr
                  key={r.id}
                  className={cn(
                    'border-b border-border-default align-top last:border-0',
                    !r.is_active && 'opacity-70',
                  )}
                  data-testid={`recurring-row-${r.id}`}
                >
                  <td className="px-3 py-2">
                    <b>{ruleTitle(r)}</b>
                    {r.item_type_name && r.memo && (
                      <div className="mt-0.5 whitespace-pre-wrap text-xs text-text-secondary">
                        伝言：{r.memo}
                      </div>
                    )}
                    {!r.item_type_name && r.memo && (
                      <div className="mt-0.5 whitespace-pre-wrap text-xs text-text-secondary">
                        {r.memo}
                      </div>
                    )}
                    {!r.item_type_active && (
                      <div className="mt-0.5 text-xs font-bold text-error">
                        項目を「使わない」にしたため、作りません
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">{targetLabel(r)}</td>
                  <td className="whitespace-nowrap px-3 py-2">
                    毎月 {r.day_of_month} 日から
                    <div className="text-xs text-text-muted">{monthLabel(r.start_month)} から</div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums">{r.target_count} 名</td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {r.is_active ? <Pill tone="ok">有効</Pill> : <Pill tone="over">停止</Pill>}
                    {r.open_count > 0 && (
                      <div className="mt-0.5 text-xs text-text-muted">未完了 {r.open_count} 件</div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1.5">
                      <Button
                        type="button"
                        size="sm"
                        onClick={() => setDraft(draftFrom(r, today))}
                        data-testid={`recurring-edit-${r.id}`}
                      >
                        変える
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setPreviewId(r.id)}
                      >
                        来月の出方
                      </Button>
                      {!r.is_active && r.open_count > 0 && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={cancelOpen.isPending}
                          onClick={() => onCancelOpen(r)}
                          className="text-text-secondary"
                          data-testid={`recurring-cancel-open-${r.id}`}
                        >
                          未完了をまとめて取り消す
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className="m-0 text-xs text-text-muted">
        対象の人数は、いま稼働中の方で数えています（入院中・一時休止・開始前の方は入りません。稼働に戻ったら、その月の分を作ります）。止めても、作り済みの分はそのまま残ります。
      </p>

      {previewRule && <PreviewSection rule={previewRule} month={nextMonth} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 作る・変える欄
// ---------------------------------------------------------------------------

function RuleForm({
  initial,
  excludedRefs,
  today,
  onClose,
  onSaved,
}: {
  initial: Draft;
  /** 保存済みの外す方 (名前つき)。候補に出ない方 (終了・区分の外) もここから並べる。 */
  excludedRefs: RecurringTaskRuleRead['excluded_patients'];
  today: string;
  onClose: () => void;
  onSaved: (id: string) => void;
}) {
  const [d, setD] = useState<Draft>(initial);
  const items = useTaskItemTypes({ includeInactive: true });
  const candidates = useRecurringCandidates(d.target_categories);
  const save = useSaveRecurringRule();
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));

  const itemOptions = (items.data ?? []).filter(
    (it) => it.is_active || it.id === initial.item_type_id,
  );
  const monthOptions = useMemo(() => {
    const list = [monthStart(today, 0), monthStart(today, 1), monthStart(today, 2)];
    return list.includes(initial.start_month) ? list : [initial.start_month, ...list];
  }, [today, initial.start_month]);

  const excluded = new Set(d.excluded_patient_ids);
  const candidateIds = new Set((candidates.data ?? []).map((p) => p.patient_id));
  // 外している方のうち候補に出ない方 (終了になった・区分を外した)。外す指定を解けるように並べる。
  const offList = candidates.isLoading
    ? []
    : excludedRefs.filter((p) => !candidateIds.has(p.patient_id));
  const activeCount = (candidates.data ?? []).filter(
    (p) => p.status === 'active' && !excluded.has(p.patient_id),
  ).length;
  const canSave =
    d.target_categories.length > 0 && (d.item_type_id !== null || (d.memo ?? '').trim().length > 0);

  const toggleCategory = (c: VisitCategory) =>
    set({
      target_categories: d.target_categories.includes(c)
        ? d.target_categories.filter((x) => x !== c)
        : [...d.target_categories, c],
    });
  const toggleExcluded = (id: string) =>
    set({
      excluded_patient_ids: excluded.has(id)
        ? d.excluded_patient_ids.filter((x) => x !== id)
        : [...d.excluded_patient_ids, id],
    });

  const submit = () => {
    if (!canSave || save.isPending) return;
    const { id, ...rest } = d;
    save.mutate(
      { id, payload: { ...rest, memo: (rest.memo ?? '').trim() || null } },
      {
        onSuccess: (res) => {
          toast.success(
            res.created_now
              ? `保存しました。今月の分を ${res.created_now} 件作りました`
              : '保存しました（作り済みの分は変わりません）',
          );
          onSaved(res.id);
        },
        onError: (err) =>
          toast.error('保存できませんでした', { description: apiErrorMessage(err) }),
      },
    );
  };

  const field = 'mt-1 h-10 w-full rounded-md border border-border-default bg-bg-base px-2 text-sm';
  const label = 'mt-3 block text-xs font-bold text-text-secondary';

  return (
    <div
      className="rounded-lg border-2 border-brand-primary-50 bg-bg-base p-4"
      data-testid="recurring-form"
    >
      <h3 className="m-0 text-base font-bold text-text-primary">
        {d.id ? '毎月のやることを変える' : '毎月のやることを作る'}
      </h3>
      <div className="grid gap-x-6 md:grid-cols-2">
        <div>
          <label className={label}>
            項目
            <select
              className={field}
              value={d.item_type_id ?? ''}
              onChange={(e) => set({ item_type_id: e.target.value || null })}
              data-testid="recurring-item"
            >
              <option value="">（項目なし・伝言だけ）</option>
              {itemOptions.map((it) => (
                <option key={it.id} value={it.id}>
                  {it.name}
                  {it.is_active ? '' : '（使わない）'}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            毎月何日から
            <select
              className={field}
              value={d.day_of_month}
              onChange={(e) => set({ day_of_month: Number(e.target.value) })}
              data-testid="recurring-day"
            >
              {DAYS.map((n) => (
                <option key={n} value={n}>
                  毎月 {n} 日から
                </option>
              ))}
            </select>
          </label>
          <p className="m-0 mt-1 text-xs text-text-muted">
            その日以降の、その方の最初の訪問に出ます。29〜31 日は月によって無いため選べません。
          </p>
          <label className={label}>
            最初に作る月
            <select
              className={field}
              value={d.start_month}
              onChange={(e) => set({ start_month: e.target.value })}
              data-testid="recurring-start-month"
            >
              {monthOptions.map((m) => (
                <option key={m} value={m}>
                  {monthLabel(m)}
                  {m === monthStart(today, 0)
                    ? '（今月・設定日を過ぎていれば保存したときに作ります）'
                    : ''}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            伝言（任意）
            <textarea
              className="mt-1 min-h-[72px] w-full rounded-md border border-border-default bg-bg-base px-2 py-1.5 text-sm"
              value={d.memo ?? ''}
              maxLength={2000}
              onChange={(e) => set({ memo: e.target.value })}
              placeholder="作るやることに同じ文が入ります"
              data-testid="recurring-memo"
            />
          </label>
          <div className={label}>状態</div>
          <div className="mt-1 flex gap-1.5">
            <Button
              type="button"
              size="sm"
              variant={d.is_active ? 'default' : 'outline'}
              onClick={() => set({ is_active: true })}
            >
              有効
            </Button>
            <Button
              type="button"
              size="sm"
              variant={d.is_active ? 'outline' : 'default'}
              onClick={() => set({ is_active: false })}
              data-testid="recurring-stop"
            >
              停止
            </Button>
          </div>
          <p className="m-0 mt-1 text-xs text-text-muted">
            変えた中身は次に作る分から使います。停止しても、作り済みの分はそのまま残ります。
          </p>
        </div>

        <div>
          <div className={label}>訪問の区分</div>
          <div className="mt-1 flex flex-wrap gap-3">
            {CATEGORIES.map((c) => (
              <label key={c} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={d.target_categories.includes(c)}
                  onChange={() => toggleCategory(c)}
                  data-testid={`recurring-category-${c}`}
                />
                {VISIT_CATEGORY_LABELS[c]}
              </label>
            ))}
          </div>
          <div className={label}>この中から個別に外す方（チェックを外すと外れます）</div>
          <div className="mt-1 max-h-64 overflow-y-auto rounded-md border border-border-default">
            {d.target_categories.length === 0 ? (
              <p className="m-0 p-3 text-xs text-text-muted">区分を選ぶと、ここに方が並びます。</p>
            ) : candidates.isLoading ? (
              <p className="m-0 p-3 text-xs text-text-muted">読み込み中…</p>
            ) : (
              (candidates.data ?? []).map((p) => {
                const out = excluded.has(p.patient_id);
                return (
                  <label
                    key={p.patient_id}
                    className={cn(
                      'flex items-center gap-2 border-b border-border-default px-3 py-1.5 text-sm last:border-0',
                      out && 'bg-bg-muted text-text-muted line-through',
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={!out}
                      onChange={() => toggleExcluded(p.patient_id)}
                      data-testid={`recurring-patient-${p.patient_id}`}
                    />
                    <span className="flex-1">{p.name} 様</span>
                    <small className="text-xs text-text-muted no-underline">
                      {VISIT_CATEGORY_LABELS[p.visit_category as VisitCategory] ?? p.visit_category}
                      ・{PATIENT_STATUS_LABELS[p.status] ?? p.status}
                    </small>
                  </label>
                );
              })
            )}
          </div>
          {offList.length > 0 && (
            <div className="mt-2" data-testid="recurring-off-list">
              <div className="text-xs text-text-muted">
                外している方のうち、いまは候補に出ない方（終了・対象の区分の外）。チェックを付けると外す指定を解きます。
              </div>
              <div className="mt-1 rounded-md border border-border-default">
                {offList.map((p) => {
                  const out = excluded.has(p.patient_id);
                  return (
                    <label
                      key={p.patient_id}
                      className={cn(
                        'flex items-center gap-2 border-b border-border-default px-3 py-1.5 text-sm last:border-0',
                        out && 'bg-bg-muted text-text-muted line-through',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={!out}
                        onChange={() => toggleExcluded(p.patient_id)}
                        data-testid={`recurring-off-patient-${p.patient_id}`}
                      />
                      <span className="flex-1">{p.name ?? '—'} 様</span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}
          <p className="m-0 mt-2 text-sm" data-testid="recurring-active-count">
            いま稼働中で対象の方：<b className="tabular-nums">{activeCount}</b> 名
          </p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          type="button"
          disabled={!canSave || save.isPending}
          onClick={submit}
          data-testid="recurring-save"
        >
          保存する
        </Button>
        <Button type="button" variant="outline" onClick={onClose}>
          閉じる
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 来月の試し表示
// ---------------------------------------------------------------------------

const RESULT_LABELS: Record<RecurringPreview['rows'][number]['result'], string> = {
  create: '作ります',
  exists: '作り済み',
  manual: '手で付けた分があるため作りません',
  excluded: '個別に外した方',
  not_active: '稼働中でないため作りません（戻ったら作ります）',
};

function PreviewSection({ rule, month }: { rule: RecurringTaskRuleRead; month: string }) {
  const query = useRecurringPreview(rule.id, month.slice(0, 7));
  const m = Number(month.split('-')[1]);
  const p = query.data;
  return (
    <div className="space-y-2" data-testid="recurring-preview">
      <h3 className="m-0 text-base font-bold text-text-primary">
        {m} 月はこう出ます（試し表示・まだ作りません）：{ruleTitle(rule)}
      </h3>
      {!rule.is_active && (
        <p className="m-0 text-xs font-bold text-warning-strong">
          この設定は停止中です。有効にすると、このように作ります。
        </p>
      )}
      {query.isLoading ? (
        <p className="text-sm text-text-muted">読み込み中…</p>
      ) : query.isError || !p ? (
        <p className="text-sm text-error">試し表示を読み込めませんでした</p>
      ) : (
        <>
          <p className="m-0 text-sm text-text-secondary">
            {m} 月分を作る {p.counts.create ?? 0} 名・作り済み {p.counts.exists ?? 0} 名・作らない{' '}
            {(p.counts.manual ?? 0) + (p.counts.excluded ?? 0) + (p.counts.not_active ?? 0)} 名
            {p.prev_open_count > 0 && `・先月までの残り ${p.prev_open_count} 名`}
            （日付 {taskMdWd(p.start_date)} から）
          </p>
          <div className="overflow-x-auto rounded-lg border border-border-default bg-bg-base">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="border-b border-border-default bg-bg-muted text-left text-xs text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">患者様</th>
                  <th className="px-3 py-2 font-medium">出方</th>
                  <th className="px-3 py-2 font-medium">出る訪問</th>
                </tr>
              </thead>
              <tbody>
                {p.rows.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="px-3 py-6 text-center text-text-muted">
                      対象の方がいません。
                    </td>
                  </tr>
                ) : (
                  p.rows.map((r) => {
                    const shows = r.result === 'create' || r.result === 'exists';
                    return (
                      <tr
                        key={r.patient_id}
                        className={cn(
                          'border-b border-border-default align-top last:border-0',
                          !shows && 'text-text-muted',
                        )}
                        data-testid={`recurring-preview-row-${r.patient_id}`}
                      >
                        <td className="whitespace-nowrap px-3 py-2">
                          <b>{r.patient_name ?? '—'}</b> 様
                          <div className="text-xs text-text-muted">
                            {VISIT_CATEGORY_LABELS[r.visit_category as VisitCategory] ??
                              r.visit_category}
                            ・{PATIENT_STATUS_LABELS[r.status] ?? r.status}
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          {RESULT_LABELS[r.result]}
                          {r.prev_open && (
                            <div className="mt-0.5">
                              <Pill tone="carry">先月までの分が残っています（並べて出ます）</Pill>
                            </div>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          {!shows ? (
                            '—'
                          ) : r.visit ? (
                            visitBriefLabel(r.visit)
                          ) : (
                            <Pill tone="over">訪問が入ったら出ます</Pill>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
