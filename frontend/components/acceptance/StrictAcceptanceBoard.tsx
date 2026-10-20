'use client';

/**
 * 受け入れ枠 (数週続けて空いているか) — PC の表・条件・内訳。
 *
 * 正典: docs/plans/acceptance-strict-design-2026-10-20.md §5-1。
 * サーバーの「職員×日×時台の入れる」を、担い手・見る週・休みの扱いでここで集計する
 * (切り替えのたびに問い合わせ直さない)。毎週の上書きは自動より優先し、角に「手」の印。
 */
import { useMemo, useState, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  DOW,
  KIND_LABELS,
  REFRESH_WAIT_NOTE,
  WEEK_CHOICES,
  cellDetail,
  cellKey,
  dayLabel,
  effectiveMark,
  failedMessages,
  isJobActive,
  jstLabel,
  overrideMap,
  overrideMark,
  periodLabel,
  presetSelection,
  progressLabel,
  rulesText,
  staleSources,
  summarize,
  unmatchedCount,
  type LeaveMode,
  type Mark,
  type Preset,
  type RefreshJob,
  type StrictOverride,
  type StrictPayload,
  type WeekChoice,
} from '@/lib/acceptanceStrict';
import type { AcceptanceStatus } from '@/lib/schemas/v2/acceptance';
import { cn } from '@/lib/utils';

export interface OverrideEditApi {
  busy: boolean;
  set: (v: {
    weekday: number;
    hour: number;
    status: AcceptanceStatus;
    note: string | null;
  }) => void;
  clear: (v: { weekday: number; hour: number }) => void;
}

export interface RefreshApi {
  job: RefreshJob | null | undefined;
  starting: boolean;
  start: (scope: 'recent' | 'all') => void;
}

export interface StrictAcceptanceBoardProps {
  data: StrictPayload;
  /** 管理者だけ: 毎週の上書き。 */
  edit?: OverrideEditApi;
  /** 管理者だけ: 取り直し。 */
  refresh?: RefreshApi;
  /** 開発用の切り替え (本番では渡さない)。 */
  devToggle?: ReactNode;
}

const MARK_STYLE: Record<Mark, string> = {
  '○': 'bg-emerald-50 text-emerald-700 border-emerald-200',
  '△': 'bg-amber-50 text-amber-700 border-amber-200',
  '×': 'bg-bg-base text-text-muted border-border-default',
  '－': 'bg-bg-muted text-text-muted border-border-default',
};

const PRESETS: { key: Preset; label: string }[] = [
  { key: 'staff', label: '職員だけ' },
  { key: 'managers', label: '＋マネージャー' },
  { key: 'trainees', label: '＋新人' },
  { key: 'all', label: '全員' },
];

const STATUS_CHOICES: { status: AcceptanceStatus; label: string }[] = [
  { status: 'available', label: '手で ○ にする' },
  { status: 'consult', label: '手で △ にする' },
  { status: 'unavailable', label: '手で × にする' },
];

function ToggleButton({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1 text-sm',
        pressed
          ? 'border-teal-600 bg-teal-600 text-white'
          : 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted',
      )}
    >
      {children}
    </button>
  );
}

export function StrictAcceptanceBoard({
  data,
  edit,
  refresh,
  devToggle,
}: StrictAcceptanceBoardProps) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(data.staff.filter((s) => s.default_selected).map((s) => s.id)),
  );
  const [mode, setMode] = useState<LeaveMode>('real');
  const [nWeeks, setNWeeks] = useState<WeekChoice>(4);
  const [sel, setSel] = useState<string | null>(null);

  const summary = useMemo(
    () => summarize(data, selected, nWeeks, mode),
    [data, selected, nWeeks, mode],
  );
  const overrides = useMemo(() => overrideMap(data.overrides), [data.overrides]);
  const text = rulesText(data.rules, nWeeks);
  const job = refresh?.job ?? data.sources.last_job;
  const running = isJobActive(job);
  const failed = failedMessages(job, data.sources.snapshots);
  const stale = staleSources(data);
  const unmatched = unmatchedCount(data.sources);
  const unmatchedNames = Object.keys(data.sources.unmatched_staff_names);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const selectedCell = sel ? summary.cells.get(sel) : undefined;

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)_320px]">
      {/* 左: 条件 */}
      <Card className="space-y-4 p-4 text-sm no-print">
        <section className="space-y-2">
          <h3 className="font-bold text-text-primary">担い手</h3>
          <div className="flex flex-wrap gap-1.5">
            {data.staff.map((s) => (
              <ToggleButton key={s.id} pressed={selected.has(s.id)} onClick={() => toggle(s.id)}>
                {s.name}
                {s.kind !== 'staff' && (
                  <small className="ml-1 text-xs">{KIND_LABELS[s.kind]}</small>
                )}
              </ToggleButton>
            ))}
            {data.staff.length === 0 && (
              <p className="text-xs text-text-secondary">この拠点に職員がいません。</p>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <Button
                key={p.key}
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setSelected(presetSelection(data.staff, p.key))}
              >
                {p.label}
              </Button>
            ))}
          </div>
        </section>

        <section className="space-y-2">
          <h3 className="font-bold text-text-primary">休み</h3>
          <div className="flex gap-1.5">
            <ToggleButton pressed={mode === 'real'} onClick={() => setMode('real')}>
              実際のとおり
            </ToggleButton>
            <ToggleButton pressed={mode === 'ignore'} onClick={() => setMode('ignore')}>
              無いとする
            </ToggleButton>
          </div>
        </section>

        <section className="space-y-2">
          <h3 className="font-bold text-text-primary">見る週</h3>
          <div className="flex gap-1.5">
            {WEEK_CHOICES.map((w) => (
              <ToggleButton key={w} pressed={nWeeks === w} onClick={() => setNWeeks(w)}>
                {w === 4 ? '直近 4 週' : `${w} 週`}
              </ToggleButton>
            ))}
          </div>
          <p className="text-xs text-text-secondary">{periodLabel(data, nWeeks)}</p>
        </section>

        <section className="space-y-2">
          <h3 className="font-bold text-text-primary">カイポケの材料</h3>
          <p>
            カイポケから取った日時:{' '}
            {jstLabel(data.sources.oldest_fetched_at) || 'まだ取っていません'}
            {data.sources.oldest_fetched_at && (
              <span className="text-xs text-text-secondary">（いちばん古いもの）</span>
            )}
          </p>
          {refresh && (
            <div className="space-y-1.5">
              <Button
                type="button"
                size="sm"
                disabled={running || refresh.starting}
                onClick={() => refresh.start('recent')}
              >
                カイポケから取り直す
              </Button>
              {running && job && (
                <div role="status" className="space-y-1">
                  <div className="h-2 overflow-hidden rounded bg-bg-muted">
                    <div
                      className="h-full bg-teal-600"
                      style={{
                        width: `${job.total ? Math.round((job.done / job.total) * 100) : 0}%`,
                      }}
                    />
                  </div>
                  <p className="text-xs">{progressLabel(job)}</p>
                </div>
              )}
              <button
                type="button"
                className="block text-xs text-teal-700 underline disabled:opacity-50"
                disabled={running || refresh.starting}
                onClick={() => refresh.start('all')}
              >
                8 週ぶんをすべて取り直す（15 分ほどかかります）
              </button>
            </div>
          )}
          <p className="text-xs text-text-secondary">{REFRESH_WAIT_NOTE}</p>
          {failed.map((m) => (
            <p key={m} className="text-xs text-rose-700">
              {m}
            </p>
          ))}
          {stale.length > 0 && (
            <p className="text-xs text-text-secondary">
              古い材料を使っています: {stale.join('、')}
            </p>
          )}
          {unmatched > 0 && (
            <p className="text-xs text-text-secondary">
              らく助に無い職員名 {unmatched} 件
              {unmatchedNames.length > 0 && `（${unmatchedNames.join('・')}）`}は数えていません
            </p>
          )}
          {devToggle}
        </section>
      </Card>

      {/* 真ん中: マス */}
      <div id="acceptance-print" className="min-w-0 space-y-3">
        <div className="flex flex-wrap gap-2 text-sm" aria-label="記号の数">
          <span className="rounded border border-emerald-200 bg-emerald-50 px-3 py-1 text-emerald-700">
            ○ 受け入れられる {summary.counts.ok}
          </span>
          <span className="rounded border border-amber-200 bg-amber-50 px-3 py-1 text-amber-700">
            △ 相談 {summary.counts.mid}
          </span>
          <span className="rounded border border-border-default px-3 py-1">
            × 難しい {summary.counts.ng}
          </span>
          {summary.counts.none > 0 && (
            <span className="rounded border border-border-default px-3 py-1">
              － 判定できない {summary.counts.none}
            </span>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                <th className="w-20" />
                {summary.hours.map((h) => (
                  <th key={h} className="px-1 py-1 text-center font-medium">
                    {h}時台
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {summary.weekdays.map((wd) => {
                const counted = summary.countedByWeekday.get(wd) ?? 0;
                return (
                  <tr key={wd}>
                    <th scope="row" className="px-1 py-1 text-left font-medium">
                      {DOW[wd]}
                      {counted !== nWeeks && (
                        <span className="block text-xs text-text-secondary">
                          （{counted} 週で判定）
                        </span>
                      )}
                    </th>
                    {summary.hours.map((h) => {
                      const key = cellKey(wd, h);
                      const auto = summary.cells.get(key);
                      if (!auto) return <td key={h} />;
                      const eff = effectiveMark(auto, overrides.get(key));
                      return (
                        <td key={h} className="p-0.5">
                          <button
                            type="button"
                            aria-pressed={sel === key}
                            aria-label={`${DOW[wd]}曜 ${h}時台 ${eff.mark} ${auto.counted} 週のうち ${auto.ok} 週空き${eff.manual ? '（手で上書き）' : ''}`}
                            onClick={() => setSel(key)}
                            className={cn(
                              'relative w-full rounded border px-1 py-1.5 text-center',
                              MARK_STYLE[eff.mark],
                              sel === key && 'ring-2 ring-teal-600',
                            )}
                          >
                            <span className="block text-base font-bold leading-none">
                              {eff.mark}
                            </span>
                            <span className="block text-xs">
                              {auto.mark === '－' ? '－' : `${auto.ok}/${auto.counted}週`}
                            </span>
                            {eff.manual && (
                              <span
                                data-testid={eff.conflict ? 'manual-conflict' : 'manual'}
                                className={cn(
                                  'absolute right-0.5 top-0.5 rounded px-0.5 text-xs leading-none',
                                  eff.conflict
                                    ? 'bg-rose-600 text-white'
                                    : 'bg-slate-500 text-white',
                                )}
                              >
                                手
                              </span>
                            )}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <Card className="space-y-2 p-4 text-sm">
          <h3 className="font-bold">この表の見方</h3>
          <p>{text.intro}</p>
          <ul className="space-y-1">
            <li>
              <b>○ 受け入れられる</b> … {text.ok}
            </li>
            <li>
              <b>△ 相談</b> … {text.mid}
            </li>
            <li>
              <b>× 難しい</b> … {text.ng}
            </li>
            <li>
              <b>－</b> … {text.none}
            </li>
          </ul>
          <p>
            <b>入れられるかの決まり</b>: {text.how}
          </p>
          <p className="text-xs text-text-secondary">{text.travel}</p>
          <p className="text-xs text-text-secondary">
            「手」の印 =
            毎週の手での上書き（自動の判定より優先）。赤い印は自動の判定と食い違うマスです。
          </p>
        </Card>
      </div>

      {/* 右: 内訳 */}
      <Card className="space-y-3 p-4 text-sm no-print" aria-label="マスの内訳">
        {!selectedCell && (
          <p className="text-text-secondary">マスを押すと、週ごとの内訳が出ます。</p>
        )}
        {selectedCell && (
          <CellDetailPanel
            key={sel}
            data={data}
            selected={selected}
            nWeeks={nWeeks}
            mode={mode}
            weekday={selectedCell.weekday}
            hour={selectedCell.hour}
            autoMark={selectedCell.mark}
            ok={selectedCell.ok}
            counted={selectedCell.counted}
            override={overrides.get(cellKey(selectedCell.weekday, selectedCell.hour))}
            edit={edit}
          />
        )}
        {data.sources.dropped_events > 0 && (
          <p className="text-xs text-text-secondary">
            時間の無い予定 {data.sources.dropped_events} 件（メモなど）は見ていません
          </p>
        )}
      </Card>
    </div>
  );
}

function CellDetailPanel({
  data,
  selected,
  nWeeks,
  mode,
  weekday,
  hour,
  autoMark,
  ok,
  counted,
  override,
  edit,
}: {
  data: StrictPayload;
  selected: ReadonlySet<string>;
  nWeeks: number;
  mode: LeaveMode;
  weekday: number;
  hour: number;
  autoMark: Mark;
  ok: number;
  counted: number;
  override: StrictOverride | undefined;
  edit?: OverrideEditApi;
}) {
  const [note, setNote] = useState(override?.note ?? '');
  const weeks = cellDetail(data, selected, nWeeks, mode, weekday, hour);
  return (
    <div className="space-y-3">
      <h3 className="text-base font-bold">
        {DOW[weekday]}曜 {hour}時台
      </h3>
      {autoMark === '－' ? (
        <p>見られた週が {counted} 週だけ（祝日・データなし）のため、判定していません。</p>
      ) : (
        <p>
          {counted} 週のうち {ok} 週で入れる人がいました（{autoMark}）。
        </p>
      )}
      {override && (
        <p className="rounded bg-bg-muted px-2 py-1">
          自動の判定は {autoMark} {ok}/{counted} 週。手で {overrideMark(override.status)}{' '}
          にしています
          {override.note ? `（理由: ${override.note}）` : ''}
        </p>
      )}
      {edit && (
        <div className="space-y-1.5 rounded border border-border-default p-2">
          <p className="text-xs text-text-secondary">毎週の手での上書き（自動の判定より優先）</p>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="理由（任意）"
            aria-label="上書きの理由"
            className="w-full rounded border border-border-default bg-bg-base px-2 py-1 text-sm"
          />
          <div className="flex flex-wrap gap-1">
            {STATUS_CHOICES.map((c) => (
              <Button
                key={c.status}
                type="button"
                size="sm"
                variant="outline"
                disabled={edit.busy}
                onClick={() => edit.set({ weekday, hour, status: c.status, note: note || null })}
              >
                {c.label}
              </Button>
            ))}
            {override && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={edit.busy}
                onClick={() => edit.clear({ weekday, hour })}
              >
                手の上書きを外す
              </Button>
            )}
          </div>
        </div>
      )}
      <ul className="space-y-2">
        {weeks.map((w) => (
          <li key={w.date} className="border-t border-border-default pt-2">
            <p className="font-medium">{dayLabel(w.date)}</p>
            {w.skipReason ? (
              <p className="text-xs text-text-secondary">{w.skipReason}</p>
            ) : (
              <div className="flex flex-wrap gap-1">
                {w.free.length === 0 && (
                  <span className="text-xs text-text-secondary">この時間に入れる人なし</span>
                )}
                {w.free.map((f) => (
                  <span key={f.name} className="rounded bg-emerald-100 px-1.5 text-emerald-800">
                    {f.name}
                    {f.note && <small className="ml-1 text-xs">{f.note}</small>}
                  </span>
                ))}
                {w.off.map((f) => (
                  <span key={`off-${f.name}`} className="rounded bg-pink-100 px-1.5 text-pink-800">
                    {f.name}：{f.text}
                  </span>
                ))}
                {w.busy.map((f) => (
                  <span
                    key={`busy-${f.name}`}
                    className="rounded bg-slate-100 px-1.5 text-slate-700"
                  >
                    {f.name}：{f.text}
                  </span>
                ))}
              </div>
            )}
            {w.notes.map((n) => (
              <p key={n} className="text-xs text-text-secondary">
                {n}
              </p>
            ))}
          </li>
        ))}
      </ul>
      <p className="text-xs text-text-secondary">
        緑 = その時間台に新しい訪問が入る人。桃 = 休み・半休、灰 =
        件数で満杯か、移動を入れた隙間が無い人。
      </p>
    </div>
  );
}
