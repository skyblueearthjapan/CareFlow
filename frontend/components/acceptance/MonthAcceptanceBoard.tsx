'use client';

/**
 * 受け入れ枠「月で見る」— 谷 (空き ○△×) と山 (稼働率の色) を 1 枚で。
 *
 * マスは曜日 (月〜土) × 30 分 (9:30〜18:00)。○△× は今の受け入れ枠と同じ決まりを
 * 「その月の週」に当てたもの。背景の色は稼働率 (訪問していた時間 ÷ 勤務できた時間) で、
 * 混みの基準以上のマスには斜線 (新しい訪問は入れない方がよい)。前の月より稼働率が
 * 大きく上がった・下がったマスに ▲▼。担い手・休み・昼休みを変えたらサーバーに問い合わせ直す。
 * 試算: docs/reports/agent/2026-11-受け入れ枠の月の埋まり.html。
 */
import {
  Fragment,
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { ChevronLeft, ChevronRight, Printer } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  MARK_TEXT,
  SOURCE_TEXT,
  currentMonthJst,
  monthCellKey,
  monthLabel,
  monthRulesText,
  num,
  pct,
  shiftMonth,
  utilBand,
  type MonthCell,
  type MonthPayload,
  type UtilBand,
} from '@/lib/acceptanceMonth';
import {
  DOW,
  KIND_LABELS,
  presetSelection,
  type LeaveMode,
  type Preset,
} from '@/lib/acceptanceStrict';
import { useMonthAcceptance, useMonthPrint } from '@/lib/queries/acceptance_month';
import { cn } from '@/lib/utils';

const PRESETS: { key: Preset; label: string }[] = [
  { key: 'staff', label: '職員だけ' },
  { key: 'managers', label: '＋マネージャー' },
  { key: 'trainees', label: '＋新人' },
  { key: 'all', label: '全員' },
];

const BAND_STYLE: Record<UtilBand, string> = {
  none: 'bg-bg-muted text-text-muted border-border-default',
  u0: 'bg-bg-base border-border-default',
  u1: 'bg-amber-50 border-amber-100',
  u2: 'bg-orange-100 border-orange-200',
  u3: 'bg-orange-200 border-orange-300',
  u4: 'bg-orange-300 border-orange-400',
};

const MARK_COLOR: Record<string, string> = {
  '○': 'text-emerald-700',
  '△': 'text-amber-700',
  '×': 'text-text-muted',
  '－': 'text-text-muted',
};

const CROWD_STYLE: CSSProperties = {
  backgroundImage:
    'repeating-linear-gradient(135deg, rgba(168,57,47,0.28) 0 1.5px, transparent 1.5px 6px)',
};

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

function slotEnd(slot: string, minutes: number): string {
  const [h = 0, m = 0] = slot.split(':').map(Number);
  const t = h * 60 + m + minutes;
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
}

export interface MonthAcceptanceBoardProps {
  /** null = 全て (全拠点)。拠点を変えたら呼び出し側で作り直す (key)。 */
  officeId: string | null;
  /** 試験で月を決める。 */
  initialMonth?: string;
}

export function MonthAcceptanceBoard({ officeId, initialMonth }: MonthAcceptanceBoardProps) {
  const [month, setMonth] = useState(() => initialMonth ?? currentMonthJst());
  /** null = 既定の担い手 (最初の応答で決まる)。 */
  const [staffIds, setStaffIds] = useState<string[] | null>(null);
  const [mode, setMode] = useState<LeaveMode>('real');
  const [lunch, setLunch] = useState(true);
  const [sel, setSel] = useState<string | null>(null);

  const params = useMemo(
    () => ({ month, officeId, staffIds, mode, lunch }),
    [month, officeId, staffIds, mode, lunch],
  );
  const query = useMonthAcceptance(params);
  const print = useMonthPrint();
  const data = query.data;
  const selected = useMemo(
    () => new Set(staffIds ?? data?.selected_staff_ids ?? []),
    [staffIds, data?.selected_staff_ids],
  );

  const choose = (next: Set<string>) => setStaffIds(Array.from(next).sort());
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    choose(next);
  };

  const openPrint = useCallback(async () => {
    // 押した直後に空のタブを開く (読み込みの後に開くとブロックされる)。
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    try {
      const html = await print.mutateAsync(params);
      if (!win) {
        toast.warning(
          '印刷用のタブを開けませんでした。ブラウザでこのサイトのポップアップを許可して、もう一度押してください。',
        );
        return;
      }
      const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
      win.location.href = url;
      try {
        win.opener = null;
      } catch {
        /* 一部ブラウザで読み取り専用 */
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      win?.close();
      toast.error(
        `印刷用のページを開けませんでした: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }, [print, params]);

  const selectedCell = sel && data ? data.cells[sel] : undefined;
  const crowdedAt = data?.rules.crowded_utilization ?? 0.7;

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)_300px]">
      {/* 左: 条件 */}
      <Card className="space-y-4 p-4 text-sm">
        <section className="space-y-2">
          <h3 className="font-bold text-text-primary">月</h3>
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label="前の月"
              onClick={() => setMonth((m) => shiftMonth(m, -1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="min-w-[7rem] text-center font-medium" data-testid="month-label">
              {monthLabel(month)}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label="次の月"
              onClick={() => setMonth((m) => shiftMonth(m, 1))}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
          {data && <p className="text-xs text-text-secondary">{SOURCE_TEXT[data.source]}</p>}
        </section>

        <section className="space-y-2">
          <h3 className="font-bold text-text-primary">担い手</h3>
          <div className="flex flex-wrap gap-1.5">
            {data?.staff.map((s) => (
              <ToggleButton key={s.id} pressed={selected.has(s.id)} onClick={() => toggle(s.id)}>
                {s.name}
                {s.kind !== 'staff' && (
                  <small className="ml-1 text-xs">{KIND_LABELS[s.kind]}</small>
                )}
              </ToggleButton>
            ))}
          </div>
          {data && data.staff.length === 0 && (
            <p className="text-xs text-text-secondary">職員がいません。</p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <Button
                key={p.key}
                type="button"
                variant="outline"
                size="sm"
                disabled={!data}
                onClick={() => data && choose(presetSelection(data.staff, p.key))}
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
          <h3 className="font-bold text-text-primary">昼休み</h3>
          <div className="flex gap-1.5">
            <ToggleButton pressed={lunch} onClick={() => setLunch(true)}>
              考える
            </ToggleButton>
            <ToggleButton pressed={!lunch} onClick={() => setLunch(false)}>
              考えない
            </ToggleButton>
          </div>
          {data && (
            <p className="text-xs text-text-secondary">
              考える = {data.rules.lunch_from}〜{data.rules.lunch_to} の間に {data.rules.lunch_min}{' '}
              分の昼休みが取れること
            </p>
          )}
        </section>

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="gap-1.5"
          disabled={!data || print.isPending}
          onClick={() => void openPrint()}
        >
          <Printer className="h-4 w-4" />
          {print.isPending ? '作っています…' : 'A4 横で開く（印刷）'}
        </Button>
      </Card>

      {/* 真ん中: 要約とマス */}
      <div className="min-w-0 space-y-3">
        {query.isLoading && <p className="text-sm text-text-secondary">読み込み中…</p>}
        {query.isError && (
          <p className="text-sm text-rose-600">読み込みに失敗しました: {query.error.message}</p>
        )}
        {data && (
          <>
            <MonthSummary data={data} fetching={query.isFetching} />
            <div className="overflow-x-auto" data-testid="month-grid">
              <table className="w-full min-w-[26rem] table-fixed border-collapse text-sm">
                <thead>
                  <tr>
                    <th className="w-14" />
                    {data.weekdays.map((wd) => (
                      <th key={wd} scope="col" className="px-0.5 py-1 text-center font-medium">
                        {DOW[wd]}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.slots.map((slot) => (
                    <tr key={slot}>
                      <th
                        scope="row"
                        className="px-0.5 py-0.5 text-right text-xs font-medium text-text-secondary"
                      >
                        {slot}
                      </th>
                      {data.weekdays.map((wd) => {
                        const key = monthCellKey(wd, slot);
                        const c = data.cells[key];
                        if (!c) return <td key={key} />;
                        return (
                          <td key={key} className="p-0.5">
                            <HeatCell
                              cell={c}
                              band={utilBand(c.counted ? c.utilization : null, crowdedAt)}
                              pressed={sel === key}
                              onClick={() => setSel(key)}
                            />
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Legend data={data} />
          </>
        )}
      </div>

      {/* 右: 内訳 */}
      <Card className="space-y-3 p-4 text-sm" aria-label="マスの内訳">
        {!selectedCell && (
          <p className="text-text-secondary">マスを押すと、件数や稼働率の内訳が出ます。</p>
        )}
        {selectedCell && data && <CellDetail cell={selectedCell} data={data} />}
      </Card>
    </div>
  );
}

function HeatCell({
  cell,
  band,
  pressed,
  onClick,
}: {
  cell: MonthCell;
  band: UtilBand;
  pressed: boolean;
  onClick: () => void;
}) {
  const label =
    `${DOW[cell.weekday]}曜 ${cell.slot} ${cell.mark} ${cell.counted} 週のうち ${cell.ok} 週空き` +
    ` 稼働率 ${pct(cell.utilization)}` +
    (cell.trend ? ` 前の月より${cell.trend === '▲' ? '上がった' : '下がった'}` : '') +
    (cell.crowded ? ' 混み（新しい訪問は入れない方がよい）' : '');
  return (
    <button
      type="button"
      aria-pressed={pressed}
      aria-label={label}
      title={cell.crowded ? '新しい訪問は入れない方がよい' : undefined}
      onClick={onClick}
      data-crowded={cell.crowded ? 'true' : undefined}
      style={cell.crowded ? CROWD_STYLE : undefined}
      className={cn(
        'relative flex w-full items-baseline justify-center gap-1 rounded border px-1 py-1',
        BAND_STYLE[band],
        pressed && 'ring-2 ring-teal-600',
      )}
    >
      <span className={cn('text-base font-bold leading-none', MARK_COLOR[cell.mark])}>
        {cell.mark}
      </span>
      <span className="text-xs tabular-nums">{pct(cell.utilization)}</span>
      {cell.trend && (
        <span
          className={cn(
            'absolute right-0.5 top-0 text-[10px] leading-none',
            cell.trend === '▲' ? 'text-rose-700' : 'text-sky-800',
          )}
        >
          {cell.trend}
        </span>
      )}
    </button>
  );
}

function MonthSummary({ data, fetching }: { data: MonthPayload; fetching: boolean }) {
  const pd = (items: MonthPayload['peaks']['utilization'], fmt: (v: number) => string) =>
    items.map((p) => `${DOW[p.weekday]} ${p.slot}（${fmt(p.value)}）`).join('、') || '－';
  const everyone = data.weekday_summary_all;
  return (
    <Card className="space-y-2 p-3 text-sm" aria-label="月の要約">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span>
          稼働率（月）<b className="ml-1 text-lg">{pct(data.utilization)}</b>
          <span className="ml-1 text-xs text-text-secondary">
            前の月 {pct(data.prev_utilization)}
          </span>
        </span>
        <span className="text-emerald-700">○ {data.counts.ok}</span>
        <span className="text-amber-700">△ {data.counts.mid}</span>
        <span>× {data.counts.ng}</span>
        {data.counts.none > 0 && <span>－ {data.counts.none}</span>}
        <span className="text-rose-700">混み {data.counts.crowded} マス</span>
        {fetching && <span className="text-xs text-text-secondary">読み直しています…</span>}
      </div>
      <table className="w-full table-fixed border-collapse text-xs" aria-label="曜日ごとの合計">
        <thead>
          <tr className="text-text-secondary">
            <th className="w-36 text-left font-medium" />
            {data.weekday_summary.map((r) => (
              <th key={r.weekday} className="text-center font-medium">
                {DOW[r.weekday]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(
            [
              ['事業所全体', everyone],
              ['担い手', data.weekday_summary],
            ] as const
          ).map(([label, rows]) => (
            <Fragment key={label}>
              <tr>
                <th className="text-left font-medium">{label} 件数 / 1 日</th>
                {data.weekday_summary.map((r) => (
                  <td key={r.weekday} className="text-center tabular-nums">
                    {num(rows.find((x) => x.weekday === r.weekday)?.visits_per_day)}
                  </td>
                ))}
              </tr>
              <tr>
                <th className="text-left font-medium">{label} 稼働率</th>
                {data.weekday_summary.map((r) => (
                  <td key={r.weekday} className="text-center tabular-nums">
                    {pct(rows.find((x) => x.weekday === r.weekday)?.utilization)}
                  </td>
                ))}
              </tr>
            </Fragment>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-text-secondary" data-testid="month-summary-note">
        件数は主担当の件数です（事業所全体＝マネージャー・新人を含む全員の主担当の件数、担い手＝担い手の主担当の件数）。空き（○△×）は担い手だけで数えています。勤務の型は今のものを使っているため、過去の月は空きが多めに出ることがあります。入職より前の日は数えていません。
      </p>
      <p className="text-xs">
        <b>ピーク</b> 稼働率：{pd(data.peaks.utilization, (v) => pct(v))}／始まる訪問（1
        日あたり）：{pd(data.peaks.starts, (v) => num(v))}
      </p>
    </Card>
  );
}

function Legend({ data }: { data: MonthPayload }) {
  const sw = (band: UtilBand) => (
    <span className={cn('inline-block h-3 w-4 rounded-sm border align-[-2px]', BAND_STYLE[band])} />
  );
  return (
    <Card className="space-y-2 p-4 text-sm">
      <h3 className="font-bold">この表の見方</h3>
      <ul className="space-y-1">
        {(['○', '△', '×', '－'] as const).map((m) => (
          <li key={m}>
            <b className={MARK_COLOR[m]}>{m}</b> … {MARK_TEXT[m]}
          </li>
        ))}
      </ul>
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span>背景の色＝稼働率（訪問していた時間 ÷ 勤務できた時間）</span>
        <span>{sw('u1')} 低い</span>
        <span>{sw('u2')} 中</span>
        <span>{sw('u3')} 高い</span>
        <span>
          {sw('u4')} {pct(data.rules.crowded_utilization)} 以上（混み）
        </span>
        <span>
          <span
            className="inline-block h-3 w-4 rounded-sm border align-[-2px]"
            style={CROWD_STYLE}
          />{' '}
          斜線＝新しい訪問は入れない方がよい
        </span>
        <span>▲▼＝前の月より稼働率が {data.rules.trend_points} ポイント以上上がった・下がった</span>
      </p>
      <p>
        <b>入れられるかの決まり</b>: {monthRulesText(data.rules)}
      </p>
    </Card>
  );
}

function CellDetail({ cell, data }: { cell: MonthCell; data: MonthPayload }) {
  const rows: [string, string][] = [
    ['始まる訪問', `1 日 ${num(cell.starts_avg)} 件（月 ${cell.starts} 件）`],
    ['稼働率', pct(cell.utilization)],
    [
      '前の月の稼働率',
      cell.prev_utilization === null || cell.prev_utilization === undefined
        ? '－'
        : `${pct(cell.prev_utilization)}（${(cell.trend_points ?? 0) > 0 ? '+' : ''}${cell.trend_points ?? 0} ポイント）`,
    ],
    [`${data.rules.busy_day_visits} 件以上の職員`, `1 日 ${num(cell.busy_staff_avg)} 人`],
    [
      `間の余裕 ${data.rules.buffer_min + data.rules.travel_guess_min} 分未満`,
      cell.pairs ? `${pct(cell.tight_ratio)}（続く訪問 ${cell.pairs} 組）` : '－',
    ],
    ['時間固定の利用者', cell.starts ? pct(cell.fixed_ratio) : '－'],
    ['入れる職員', `週平均 ${num(cell.free_avg)} 人`],
  ];
  return (
    <div className="space-y-3">
      <h3 className="text-base font-bold">
        {DOW[cell.weekday]}曜 {cell.slot}〜{slotEnd(cell.slot, data.rules.slot_min)}
      </h3>
      {cell.mark === '－' ? (
        <p>数えられた日が {cell.counted} 日だけのため、判定していません。</p>
      ) : (
        <p>
          {cell.counted} 週のうち {cell.ok} 週で入れる人がいました（{cell.mark}）。
        </p>
      )}
      {cell.crowded && (
        <p className="rounded bg-rose-50 px-2 py-1 text-rose-800">
          混んでいます。新しい訪問は入れない方がよいマスです。
        </p>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-text-secondary">{k}</dt>
            <dd className="tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {cell.per_week.length > 0 && (
        <p className="text-xs text-text-secondary">
          週ごとの入れる人数：{cell.per_week.map((n) => `${n} 人`).join('・')}
        </p>
      )}
    </div>
  );
}
