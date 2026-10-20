'use client';

/**
 * 「休み・勤務表」の右の一覧「カイポケとの差分」(設計 §5・§7-4-3)。
 *
 *   - 「カイポケと比べる」: 取り直しのジョブを始め、数秒おきに進み具合を読む
 *     (取り直し中 N/M 週 → 比べている → 比べ終わった)。17〜18 時台は注意を出す。
 *   - 差分の一覧: action ごとのボタン (らく助に入れる／カイポケの種類に合わせる／らく助の休みを
 *     取り消す／種類を選ぶ／カイポケと比べ直す)。action が無い差分は案内 (detail.guide) だけ。
 *     合わせた差分は「合わせ済み」。同じ職員の「入れる」「取り消す」はまとめて押せる。
 *   - 「カイポケへ送る」: 送る前の一覧 (LeaveOutboundDialog)。
 *   - 「実績を記録する」: 未確認の日・差分・送る予定・照合前・照合の後の変更があれば押せない
 *     (理由を出す)。記録の API はまだ無いので、いまは理由を出すところまで。
 * 画面の言葉は「合わせる」(「直す」は使わない)。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { LeaveOutboundDialog } from '@/components/leave/LeaveOutboundDialog';
import { cn } from '@/lib/utils';
import { fmtDateTimeShort, fmtDayJp, LEAVE_LABEL } from '@/lib/leave-sheet';
import { invalidateAfterLeave, leaveErrorMessage } from '@/lib/queries/leave';
import {
  useApplyCompare,
  useCompareJob,
  useCompareLatest,
  useStartCompare,
} from '@/lib/queries/leaveKaipoke';
import {
  isCompareRunning,
  isEveningJst,
  isJobActive,
  type ChosenKind,
  type CompareApplyItem,
  type CompareJobStatus,
  type LeaveCompareRunView,
  type LeaveDiff,
} from '@/lib/schemas/leaveKaipoke';

const ACTION_LABEL: Record<'add_to_rakusuke' | 'match_kaipoke_kind' | 'cancel_rakusuke', string> = {
  add_to_rakusuke: 'らく助に入れる',
  match_kaipoke_kind: 'カイポケの種類に合わせる',
  cancel_rakusuke: 'らく助の休みを取り消す',
};

/** 種類がわからない予定に選ぶもの (設計 §7-4-3 の順)。 */
const CHOOSE_KINDS: { value: ChosenKind; label: string }[] = [
  { value: 'not_leave', label: '休みではない' },
  { value: 'off', label: '公休' },
  { value: 'am_off', label: '午前休' },
  { value: 'pm_off', label: '午後休' },
  { value: 'paid_off', label: '有給' },
];

/** 同じ職員でまとめて押せる合わせ方 (最初の照合でたくさん出るもの・設計 §7-4-3)。 */
const BULK_ACTIONS = ['add_to_rakusuke', 'cancel_rakusuke'] as const;

const TERMINAL = new Set(['done', 'compare_failed', 'failed', 'cancelled']);

/** 押せる差分 (まだ合わせていない・ボタンのある合わせ方)。 */
function applicable(d: LeaveDiff): boolean {
  return !d.resolved && !!d.staff_id && !!d.date && !!d.action && d.action !== 'refetch_week';
}

/**
 * 「実績を記録する」を押せない理由 (設計 §7-4-2)。空なら条件はそろっている。
 * 未確認の日が 1 日でもあれば押せない。
 */
export function recordActualBlockers(args: {
  run: LeaveCompareRunView | null | undefined;
  changedSinceCompare: boolean;
  baselinePendingCount: number;
}): string[] {
  const { run } = args;
  if (!run) return ['まだカイポケと比べていません'];
  const out: string[] = [];
  if (run.unconfirmed_days > 0) {
    out.push(`未確認の日が ${run.unconfirmed_days} 日あります（その週を取り直してください）`);
  }
  const open = run.diffs.filter((d) => !d.resolved && d.kind !== 'unconfirmed').length;
  if (open > 0) out.push(`カイポケとの差分が ${open} 件あります`);
  if (run.planned_send_count > 0) {
    out.push(`カイポケへ送る予定の休みが ${run.planned_send_count} 件あります`);
  }
  if (args.baselinePendingCount > 0) {
    out.push(`照合前の休みが ${args.baselinePendingCount} 件あります`);
  }
  if (args.changedSinceCompare) {
    out.push('比べた後に休みを変えました（もう一度比べてください）');
  } else if (run.diffs.some((d) => d.resolved)) {
    out.push('合わせた差分があります（もう一度比べて確かめてください）');
  }
  return out;
}

/** ジョブの進み具合の文。 */
function progressText(status: CompareJobStatus | undefined, total: number | null): string | null {
  if (!status) return null;
  const job = status.job;
  switch (status.state) {
    case 'fetching':
      return job.stale
        ? 'カイポケの取り直しが止まっているようです（しばらくしてから、もう一度比べてください）'
        : `カイポケから取り直し中（${job.done}/${job.total ?? total ?? '?'} 週）`;
    case 'comparing':
      return 'らく助の休みと比べています';
    case 'done':
      return '比べ終わりました';
    case 'compare_failed':
      return `比べられませんでした${status.compare_error ? `：${status.compare_error}` : ''}`;
    case 'failed':
      return `カイポケから取り直せませんでした${job.error ? `：${job.error}` : ''}`;
    case 'cancelled':
      return 'カイポケの取り直しは取り消されました';
  }
}

export interface LeaveComparePanelProps {
  month: string;
  /** 照合前の休みの件数 (月の表の compare.baseline_pending_count)。 */
  baselinePendingCount: number;
}

export function LeaveComparePanel({ month, baselinePendingCount }: LeaveComparePanelProps) {
  const qc = useQueryClient();
  const latest = useCompareLatest(month);
  const start = useStartCompare();
  const apply = useApplyCompare();
  const [jobId, setJobId] = useState<string | null>(null);
  const [startTotal, setStartTotal] = useState<number | null>(null);
  const [startWarning, setStartWarning] = useState<string | null>(null);
  const [recompare, setRecompare] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);

  // 開いたときに動いているジョブがあれば、その進み具合を読む
  const latestJob = latest.data?.job;
  const activeJobId = jobId ?? (latestJob && isJobActive(latestJob) ? latestJob.id : null);
  const jobQuery = useCompareJob(activeJobId);
  const state = jobQuery.data?.state;
  // 止まった (stale) ジョブ・読めなかったジョブは終わったものとして扱う
  const finished =
    !!jobQuery.data && (TERMINAL.has(jobQuery.data.state) || !!jobQuery.data.job.stale);
  const running =
    start.isPending ||
    (!!activeJobId && !jobQuery.isError && (!jobQuery.data || isCompareRunning(jobQuery.data)));

  // 夕方の注意は時間が過ぎたら消す (1 分ごとに確かめる)
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);

  // ジョブが終わったら、最新の照合と月の表を 1 回だけ読み直す
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (!activeJobId || !finished) return;
    if (handled.current === activeJobId) return;
    handled.current = activeJobId;
    if (state === 'done') setRecompare(false);
    void invalidateAfterLeave(qc);
  }, [activeJobId, finished, state, qc]);

  const run = latest.data?.run ?? jobQuery.data?.run ?? null;
  const diffs = useMemo(() => run?.diffs ?? [], [run]);
  const evening = isEveningJst(now) ? (startWarning ?? latest.data?.evening_warning ?? null) : null;
  const changedSince = latest.data?.changed_since_compare ?? false;
  const blockers = recordActualBlockers({
    run,
    changedSinceCompare: changedSince || recompare,
    baselinePendingCount,
  });

  const groups = useMemo(() => {
    const out: { key: string; staffId: string | null; name: string; items: LeaveDiff[] }[] = [];
    const byKey = new Map<string, (typeof out)[number]>();
    diffs.forEach((d, i) => {
      const key = d.staff_id ?? `__${i}`;
      let g = byKey.get(key);
      if (!g) {
        g = { key, staffId: d.staff_id ?? null, name: d.staff_name, items: [] };
        byKey.set(key, g);
        out.push(g);
      }
      g.items.push(d);
    });
    return out;
  }, [diffs]);

  const doStart = () => {
    start
      .mutateAsync(month)
      .then((r) => {
        handled.current = null;
        setJobId(r.job_id);
        setStartTotal(r.total);
        setStartWarning(r.evening_warning ?? null);
      })
      .catch((e: unknown) => toast.error(leaveErrorMessage(e, 'カイポケと比べられませんでした')));
  };

  const doApply = (items: CompareApplyItem[]) => {
    if (items.length === 0) return;
    apply
      .mutateAsync(items)
      .then((r) => {
        if (r.recompare_needed) setRecompare(true);
        toast.success(`${r.applied.length} 件を合わせました`, {
          description:
            [
              r.moved_visit_count > 0
                ? `訪問 ${r.moved_visit_count} 件を「担当なし」へ移しました`
                : '',
              r.recompare_needed ? 'もう一度「カイポケと比べる」を押して確かめてください' : '',
            ]
              .filter(Boolean)
              .join('。') || undefined,
        });
      })
      .catch((e: unknown) => toast.error(leaveErrorMessage(e, '合わせられませんでした')));
  };

  const itemOf = (d: LeaveDiff, chosen?: ChosenKind): CompareApplyItem | null => {
    if (!d.staff_id || !d.date || !d.action || d.action === 'refetch_week') return null;
    const hash = typeof d.detail.content_hash === 'string' ? d.detail.content_hash : null;
    return {
      staff_id: d.staff_id,
      date: d.date,
      action: d.action,
      ...(hash ? { content_hash: hash } : {}),
      ...(chosen ? { chosen_kind: chosen } : {}),
    };
  };

  const busy = running || apply.isPending;
  const progress = start.isPending
    ? 'カイポケと比べ始めています'
    : jobQuery.isError
      ? `進み具合を読めませんでした：${leaveErrorMessage(jobQuery.error, '通信に失敗しました')}（しばらくしてから、もう一度比べてください）`
      : progressText(jobQuery.data, startTotal);
  const openCount = diffs.filter((d) => !d.resolved).length;

  return (
    <aside
      aria-label="カイポケとの差分"
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-border-default bg-bg-base p-3 lg:max-h-[640px] lg:overflow-y-auto"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" disabled={busy} onClick={doStart}>
          カイポケと比べる
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => setSendOpen(true)}
        >
          カイポケへ送る…
        </Button>
      </div>

      {evening && (
        <p role="note" className="rounded-md bg-warning-bg px-3 py-2 text-sm text-warning-strong">
          {evening}
        </p>
      )}
      {progress && (
        <p
          role="status"
          data-testid="compare-progress"
          className={cn(
            'rounded-md px-3 py-2 text-sm',
            state === 'failed' || state === 'compare_failed' || jobQuery.isError
              ? 'bg-error-bg text-error'
              : 'bg-bg-muted text-text-secondary',
          )}
        >
          {progress}
        </p>
      )}

      <div className="text-sm text-text-secondary" data-testid="compare-summary">
        {run ? (
          <>
            <p>比べた時刻：{fmtDateTimeShort(latest.data?.last_compared_at ?? run.created_at)}</p>
            <p>
              差分 {run.diff_count} 件（まだ合わせていない {openCount} 件）・送る予定{' '}
              {run.planned_send_count} 件・未確認の日 {run.unconfirmed_days} 日
            </p>
          </>
        ) : (
          <p>
            {latest.isError
              ? `照合の結果を読めませんでした：${leaveErrorMessage(latest.error, '通信に失敗しました')}`
              : latest.isLoading
                ? '照合の結果を読み込んでいます'
                : 'この月はまだカイポケと比べていません'}
          </p>
        )}
        {(changedSince || recompare) && (
          <p className="font-bold text-warning-strong">
            {recompare
              ? '合わせた結果を確かめるため、もう一度「カイポケと比べる」を押してください'
              : '比べた後に休みを変えました。もう一度比べてください'}
          </p>
        )}
      </div>

      <div className="space-y-1 rounded-md border border-border-default p-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          // 記録の API (#13) ができるまでは押せない。理由は下に出す
          disabled
          aria-describedby="record-actual-reasons"
        >
          実績を記録する
        </Button>
        <ul id="record-actual-reasons" className="list-disc pl-5 text-xs text-text-secondary">
          {blockers.length > 0 ? (
            blockers.map((b) => <li key={b}>{b}</li>)
          ) : (
            <li>条件はそろっています（実績の記録はこのあと使えるようになります）</li>
          )}
        </ul>
      </div>

      {run && diffs.length === 0 && (
        <p className="text-sm text-text-secondary">カイポケとの差分はありません。</p>
      )}
      {groups.map((g) => (
        <section
          key={g.key}
          aria-label={g.staffId ? g.name : (g.items[0]?.message ?? '差分')}
          className="flex flex-col gap-2 rounded-lg border border-border-default p-2"
        >
          {g.staffId && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <b className="text-sm text-text-primary">{g.name}</b>
              {BULK_ACTIONS.map((a) => {
                const items = g.items.filter((d) => applicable(d) && d.action === a);
                if (items.length < 2) return null;
                return (
                  <Button
                    key={a}
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      doApply(items.map((d) => itemOf(d)).filter((x): x is CompareApplyItem => !!x))
                    }
                  >
                    まとめて「{ACTION_LABEL[a]}」（{items.length} 件）
                  </Button>
                );
              })}
            </div>
          )}
          <ul className="flex flex-col gap-2">
            {g.items.map((d, i) => (
              <DiffRow
                key={`${d.kind}-${d.date ?? ''}-${i}`}
                diff={d}
                busy={busy}
                onApply={(chosen) => {
                  const item = itemOf(d, chosen);
                  if (item) doApply([item]);
                }}
                onRefetch={doStart}
              />
            ))}
          </ul>
        </section>
      ))}

      <LeaveOutboundDialog open={sendOpen} onOpenChange={setSendOpen} month={month} />
    </aside>
  );
}

function DiffRow({
  diff,
  busy,
  onApply,
  onRefetch,
}: {
  diff: LeaveDiff;
  busy: boolean;
  onApply: (chosen?: ChosenKind) => void;
  onRefetch: () => void;
}) {
  const d = diff.detail;
  const guide = typeof d.guide === 'string' ? d.guide : null;
  const visits = Array.isArray(d.visits) ? (d.visits as Record<string, unknown>[]) : [];
  const dupItems = Array.isArray(d.items) ? (d.items as Record<string, unknown>[]) : [];
  const dates = Array.isArray(d.dates) ? (d.dates as string[]) : [];
  const time = (s: unknown, e: unknown) => (s || e ? `${String(s ?? '')}〜${String(e ?? '')}` : '');

  return (
    <li
      data-testid="leave-diff"
      data-kind={diff.kind}
      className="space-y-1 border-t border-dashed border-border-default pt-2 text-sm first:border-t-0 first:pt-0"
    >
      <p className="text-text-primary">
        {diff.date && <b className="mr-1 tnum">{fmtDayJp(diff.date)}</b>}
        {diff.message}
      </p>
      {diff.kind === 'unknown_kind' && (
        <p className="text-xs text-text-muted">
          カイポケの予定：「{String(d.subject ?? '')}」{time(d.start, d.end)}
        </p>
      )}
      {visits.length > 0 && (
        <p className="text-xs text-text-muted">
          実績の訪問：
          {visits.map((v) => `${String(v.patient ?? '')} ${time(v.start, v.end)}`).join('・')}
        </p>
      )}
      {dupItems.length > 0 && (
        <p className="text-xs text-text-muted">
          カイポケの予定：
          {dupItems.map((v) => `「${String(v.subject ?? '')}」${time(v.start, v.end)}`).join('・')}
        </p>
      )}
      {dates.length > 0 && (
        <p className="text-xs text-text-muted">日付：{dates.map(fmtDayJp).join('・')}</p>
      )}

      {diff.resolved ? (
        <p className="inline-block rounded bg-success-bg px-2 text-xs font-bold text-success">
          合わせ済み
          {diff.resolved.chosen_kind
            ? `（${CHOOSE_KINDS.find((k) => k.value === diff.resolved?.chosen_kind)?.label ?? ''}）`
            : ''}
        </p>
      ) : diff.action === 'choose_kind' ? (
        <div className="flex flex-wrap gap-1" role="group" aria-label="種類を選ぶ">
          {CHOOSE_KINDS.map((k) => (
            <Button
              key={k.value}
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => onApply(k.value)}
            >
              {k.label}
            </Button>
          ))}
        </div>
      ) : diff.action === 'refetch_week' ? (
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onRefetch}>
          カイポケと比べ直す（月をまとめて取り直します）
        </Button>
      ) : diff.action ? (
        <Button type="button" size="sm" disabled={busy} onClick={() => onApply()}>
          {ACTION_LABEL[diff.action]}
          {diff.action !== 'cancel_rakusuke' && diff.kaipoke
            ? `（${LEAVE_LABEL[diff.kaipoke]}）`
            : ''}
        </Button>
      ) : (
        <p className="text-xs text-text-secondary" data-testid="diff-guide">
          {guide ?? 'カイポケで合わせてから、もう一度比べてください'}
        </p>
      )}
    </li>
  );
}
