/**
 * スタッフ別の実績 (管理者だけ)。期間・拠点の切り替え → チーム全体の数字 →
 * B (人ごとのカード) / C (1 人を深く見る) → 評価に使う際の注意。
 *
 * 設計: docs/plans/dashboard-staff-performance-design-2026-09-30.md §4〜§6。
 */
'use client';

import * as React from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/dashboard/EmptyState';
import { KpiCard } from '@/components/dashboard/KpiCard';
import {
  MAX_PERIOD_DAYS,
  PERIOD_PRESETS,
  monthDay,
  periodDays,
  presetRange,
  r0,
  r1,
  todayJst,
  type PeriodPreset,
} from '@/lib/dashboard-period';
import { useStaffPerformance } from '@/lib/queries/dashboard';
import { cn } from '@/lib/utils';

import { StaffDeepView } from './StaffDeepView';
import { StaffPerformanceCard } from './StaffPerformanceCard';

/** QR で時間が取れた割合がこれ未満の間は注意書きを出す。 */
const QR_NOTICE_BELOW = 0.5;

function Seg({
  items,
  value,
  onChange,
  label,
}: {
  items: { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
  label: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex overflow-hidden rounded-md border border-border-default bg-bg-base"
    >
      {items.map((it, i) => (
        <button
          key={it.key}
          type="button"
          aria-pressed={it.key === value}
          onClick={() => onChange(it.key)}
          className={cn(
            'px-3 py-1 text-[13px]',
            i > 0 && 'border-l border-border-default',
            it.key === value
              ? 'bg-brand-primary font-bold text-bg-base'
              : 'text-text-secondary hover:bg-bg-muted',
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

export interface StaffPerformanceSectionProps {
  /** 上段 (チーム全体の数字) の下に小さく残す、今日の運用の数字 (設計 §5-6)。 */
  opsSummary?: React.ReactNode;
}

export function StaffPerformanceSection({ opsSummary }: StaffPerformanceSectionProps) {
  // 毎回計算する (日付をまたいで開いたままでも「今週」「今月」が今日に追いつく)。
  const today = todayJst();
  const [preset, setPreset] = React.useState<PeriodPreset>('this_month');
  const [custom, setCustom] = React.useState(() => presetRange('this_month', today));
  const [officeId, setOfficeId] = React.useState('');
  const [selected, setSelected] = React.useState<string | null>(null);

  const range = preset === 'custom' ? custom : presetRange(preset, today);
  const customMessage =
    preset !== 'custom'
      ? null
      : !custom.from || !custom.to || custom.from > custom.to
        ? '期間の始めと終わりを選んでください。'
        : periodDays(custom.from, custom.to) > MAX_PERIOD_DAYS
          ? `期間は ${MAX_PERIOD_DAYS} 日以内で選んでください。`
          : null;
  const customInvalid = customMessage != null;
  const query = useStaffPerformance({
    from: range.from,
    to: range.to,
    officeId: officeId || null,
    enabled: !customInvalid,
  });
  const data = query.data;

  // 拠点の選択肢は、いちど取れた一覧を持ち続ける (読み込み中に消さない)。
  const [offices, setOffices] = React.useState<{ key: string; label: string }[]>([]);
  React.useEffect(() => {
    if (data) setOffices(data.offices.map((o) => ({ key: o.id, label: o.name })));
  }, [data]);

  // 絞り込みで選んでいた人がいなくなったら B に戻る。
  React.useEffect(() => {
    if (selected && data && !data.staff.some((s) => s.staff_id === selected)) setSelected(null);
  }, [data, selected]);

  const team = data?.team.period;
  const showNotice = team != null && team.visits > 0 && (team.qr_ratio ?? 0) < QR_NOTICE_BELOW;

  return (
    <section className="space-y-4" aria-label="スタッフ別の実績">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-text-secondary">期間</span>
        <Seg
          label="期間"
          items={PERIOD_PRESETS}
          value={preset}
          onChange={(k) => setPreset(k as PeriodPreset)}
        />
        {preset === 'custom' ? (
          <span className="flex items-center gap-1 text-sm">
            <input
              type="date"
              aria-label="期間の始め"
              max={today}
              value={custom.from}
              onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))}
              className="rounded-md border border-border-default bg-bg-base px-2 py-0.5"
            />
            〜
            <input
              type="date"
              aria-label="期間の終わり"
              max={today}
              value={custom.to}
              onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))}
              className="rounded-md border border-border-default bg-bg-base px-2 py-0.5"
            />
          </span>
        ) : (
          <span className="text-xs tnum text-text-secondary">
            {monthDay(range.from)}〜{monthDay(range.to)}
          </span>
        )}
        <span className="ml-2 text-xs text-text-secondary">拠点</span>
        <Seg
          label="拠点"
          items={[{ key: '', label: '全拠点' }, ...offices]}
          value={officeId}
          onChange={setOfficeId}
        />
      </div>

      {customMessage ? <p className="text-sm text-warning-strong">{customMessage}</p> : null}

      {query.isError ? (
        <Alert variant="destructive">
          <AlertTitle>スタッフ別の実績を読み込めませんでした</AlertTitle>
          <AlertDescription>
            {query.error instanceof Error ? query.error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <KpiCard
          label="訪問"
          value={team?.visits ?? 0}
          unit="件"
          isLoading={query.isLoading}
          caption={
            team
              ? `${team.staff_count ?? 0} 人・のべ ${team.days} 日（2 名訪問はそれぞれに数えます）`
              : undefined
          }
        />
        <KpiCard
          label="1 人 1 日あたり"
          value={r1(team?.per_day)}
          unit="件"
          isLoading={query.isLoading}
          caption="訪問のあった日の平均"
        />
        <KpiCard
          label="1 回あたり（予定）"
          value={r0(team?.plan_min)}
          unit="分"
          isLoading={query.isLoading}
          caption={
            team
              ? team.actual_min != null
                ? `実績 ${r0(team.actual_min)} 分（全員の QR 記録の平均・${team.actual_samples} 件）`
                : '実績は QR の記録が足りません'
              : undefined
          }
        />
        <KpiCard
          label="移動（直線）"
          value={r1(team?.km_per_day)}
          unit="km/日"
          isLoading={query.isLoading}
          caption={team ? `1 件あたり ${r1(team.km_per_visit)} km` : undefined}
        />
        <KpiCard
          label="QR で時間が取れた訪問"
          value={team?.qr_ratio != null ? Math.round(team.qr_ratio * 100) : '—'}
          unit={team?.qr_ratio != null ? '%' : undefined}
          isLoading={query.isLoading}
          caption={
            team ? `${team.actual_samples} / ${team.visits} 件（到着と退出の両方）` : undefined
          }
        />
      </div>

      {showNotice && team ? (
        <div
          role="note"
          className="rounded-md border border-border-warning bg-warning-bg px-3 py-2 text-[13px] text-warning-strong"
        >
          実績の時間は、QR の到着と退出が揃った訪問（
          {Math.round((team.qr_ratio ?? 0) * 100)}%）からだけ出しています。記録が{' '}
          {data?.min_actual_samples ?? 5}{' '}
          件に満たない人は「—」です。件数・予定の時間・移動距離は全件から出しています。
        </div>
      ) : null}

      {opsSummary}

      {query.isLoading ? (
        <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(330px,1fr))]">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-72 w-full" />
          ))}
        </div>
      ) : data && data.staff.length === 0 ? (
        <EmptyState
          pose="calendar"
          title="この期間の訪問はありません"
          description="期間や拠点を変えてみてください。"
        />
      ) : data && selected ? (
        <StaffDeepView
          data={data}
          staffId={selected}
          onSelect={setSelected}
          onBack={() => setSelected(null)}
        />
      ) : data ? (
        <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(330px,1fr))]">
          {data.staff.map((row) => (
            <StaffPerformanceCard
              key={row.staff_id}
              row={row}
              team={data.team.period}
              weekStarts={data.weeks.map((w) => w.start)}
              minActualSamples={data.min_actual_samples}
              onOpen={setSelected}
            />
          ))}
        </div>
      ) : null}

      <Card className="px-4 py-3 text-[13.5px]" data-testid="perf-caveats">
        <h2 className="mb-1.5 text-[13px] font-bold text-brand-primary-hover">
          評価に使う前に知っておくこと
        </h2>
        <ul className="list-disc space-y-1 pl-5 text-text-primary">
          <li>
            <b>実績の時間は参考値です。</b>QR
            の到着と退出が揃った訪問だけから出すため、読み取りが定着するまでは参考にとどめてください。読み取りの無い人は「—」です。
          </li>
          <li>
            <b>件数だけでは比べられません。</b>
            管理業務のある人・新人・勤務日数の違いで件数は変わります。役割の札と「1
            日あたり」を並べて見てください。
          </li>
          <li>
            <b>距離は直線です。</b>
            人どうしの比較には使えますが、実際の走行距離や燃料費の計算には使えません。
          </li>
        </ul>
      </Card>
    </section>
  );
}
