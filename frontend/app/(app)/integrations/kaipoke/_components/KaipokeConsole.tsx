'use client';

/**
 * KaipokeConsole — カイポケ連携の操作コンソール (PO確定レイアウト 2026-07-09)。
 *
 * 上段: 左=ライブモニター+稼働状況(モニター下の空きに非圧縮)、右=週次反映/取り込みの操作。
 * 下段: 全幅の大きなカレンダー枠。タブで「送る（この週の予定）」「取り込みプレビュー」を切替。
 *
 * 送る側/取り込み側の状態はそれぞれ useWeeklyApply / useInbound フックが 1 回だけ持ち、
 * 操作部 (上段 Controls) とカレンダー部 (下段) の 2 箇所へ描き分ける。ロジックは移動のみ。
 */
import { useEffect, useRef, useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type { KaipokeJob, LiveSnapshot } from '@/lib/schemas/integration';
import { useKaipokeJobs } from '@/lib/queries/integrations';
import type { useStopJob } from '@/lib/queries/integrations';

import { EmergencyStopButton } from './EmergencyStopButton';
import { InboundCalendar } from './InboundCalendar';
import { InboundControls } from './InboundControls';
import { JobProgressCard, commandLabel } from './JobProgressCard';
import { LiveMonitorCard } from './LiveMonitorCard';
import { LiveStatusDot } from './LiveStatusDot';
import { WeeklyApplyCalendar } from './WeeklyApplyCalendar';
import { WeeklyApplyControls } from './WeeklyApplyControls';
import { useInbound } from './useInbound';
import { useWeeklyApply } from './useWeeklyApply';

/** 直近ジョブ履歴の 1 行ラベル: params.op → 現場の言葉。未知の op は job_type で表示。 */
const JOB_OP_LABELS: Record<string, string> = {
  expand: '①スケジュール展開',
  diff: '②差分を計算',
  apply: '④カイポケへ反映',
  'diff-inbound': '取り込み差分の計算',
  'apply-inbound': '取り込み適用（差分）',
  'replace-inbound': '置換取り込み',
  'smart-apply': 'カイポケから取り込み',
  'events-inbound': 'イベント取り込み',
};

function jobLabel(job: KaipokeJob): string {
  const op = typeof job.params?.op === 'string' ? (job.params.op as string) : null;
  return (op && JOB_OP_LABELS[op]) || (op ?? (job.job_type === 'push' ? '送信' : '取得'));
}

function jobTimeLabel(job: KaipokeJob): string {
  const d = new Date(job.completed_at ?? job.created_at);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

const JOB_STATUS_META: Record<string, { label: string; cls: string }> = {
  completed: { label: '成功', cls: 'text-success' },
  failed: { label: '失敗', cls: 'text-error font-bold' },
  running: { label: '実行中', cls: 'text-info' },
  pending: { label: '待機', cls: 'text-text-muted' },
  cancelled: { label: '中止', cls: 'text-text-muted' },
};

interface Props {
  live: LiveSnapshot | undefined;
  liveLoading?: boolean;
  running: boolean;
  reachable: boolean;
  latestJob: NonNullable<LiveSnapshot['latestJob']> | null;
  credentialsConfigured: boolean;
  stop: ReturnType<typeof useStopJob>;
}

export function KaipokeConsole({
  live,
  liveLoading = false,
  running,
  reachable,
  latestJob,
  credentialsConfigured,
  stop,
}: Props) {
  const weekly = useWeeklyApply({ busy: running, credentialsConfigured });
  const inbound = useInbound({ busy: running, credentialsConfigured });
  // 稼働状況カードの「直近のジョブ履歴」(案2 / PO 決定 2026-08-09)。
  const recentJobsQuery = useKaipokeJobs({ limit: 5 });
  const recentJobs = recentJobsQuery.data?.items ?? [];

  const [tab, setTab] = useState<'send' | 'inbound'>('send');

  // 取り込みの❶差分取得が成功して sheetId が入ったら「取り込みプレビュー」タブへ自動切替。
  // 新しい sheetId への遷移時だけ切り替え、ユーザーが手動で戻したタブは尊重する。
  const prevInboundSheet = useRef<string | null>(null);
  useEffect(() => {
    if (inbound.sheetId && prevInboundSheet.current !== inbound.sheetId) {
      setTab('inbound');
    }
    prevInboundSheet.current = inbound.sheetId;
  }, [inbound.sheetId]);

  const statusTone = !reachable ? 'error' : running ? 'running' : 'idle';
  const statusLabel = !reachable ? '到達不可' : running ? '実行中' : '待機中';

  return (
    <div className="space-y-6">
      {/* ── 上段: 左=モニター / 右=稼働状況 + 操作パネル ── */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.05fr_1fr]">
        {/* 左カラム: ライブモニター + 稼働状況(非圧縮・モニター下の空きスペースに) +
            実行中のみ進捗ゲージ / 非常停止。
            flex-col + 稼働状況 flex-1 で右カラムがどれだけ伸びても下辺が揃う
            (案2 / PO 決定 2026-08-09: 中途半端な余白はカード内へ・履歴で情報に変える)。 */}
        <div className="flex min-w-0 flex-col gap-4">
          <LiveMonitorCard
            monitorUrl={live?.monitorUrl}
            running={running}
            reachable={reachable}
            commandLabel={commandLabel(live?.command)}
          />

          {/* 稼働状況（モニター直下・非圧縮・flex-1 で下辺を右カラムに揃える）。 */}
          <Card className="flex-1 p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-serif text-lg font-bold text-text-primary">稼働状況</h2>
              <LiveStatusDot tone={statusTone} label={statusLabel} />
            </div>
            {liveLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : !reachable ? (
              <Alert variant="destructive">
                <AlertTitle>kaipoke-api に到達できません</AlertTitle>
                <AlertDescription className="break-all">
                  {live?.error ?? '接続を確認してください'}
                </AlertDescription>
              </Alert>
            ) : (
              <>
                <dl className="grid grid-cols-2 gap-y-2 text-sm">
                  <dt className="text-text-secondary">現在の状態</dt>
                  <dd className="text-text-primary">
                    {running ? (commandLabel(live?.command) ?? '実行中') : '待機中'}
                  </dd>
                  <dt className="text-text-secondary">直近ジョブ</dt>
                  <dd className="text-text-primary">
                    {latestJob ? `${latestJob.job_type} / ${latestJob.status}` : 'なし'}
                  </dd>
                </dl>
                {/* 直近のジョブ履歴 (最新5件)。伸びた余白を運用情報に変える (案2)。 */}
                {recentJobs.length > 0 && (
                  <div
                    className="mt-4 border-t border-border-default pt-3"
                    data-testid="recent-jobs-list"
                  >
                    <p className="mb-2 text-xs font-medium text-text-secondary">直近のジョブ履歴</p>
                    <ul className="space-y-1.5">
                      {recentJobs.map((job) => {
                        const meta = JOB_STATUS_META[job.status] ?? {
                          label: job.status,
                          cls: 'text-text-muted',
                        };
                        return (
                          <li
                            key={job.id}
                            className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs"
                          >
                            <span className="tnum shrink-0 text-text-muted">
                              {jobTimeLabel(job)}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-text-primary">
                              {jobLabel(job)}
                            </span>
                            <span className={meta.cls}>{meta.label}</span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
              </>
            )}
          </Card>

          {/* ライブ進捗ゲージ（実行中のみ）— モニターの直下 */}
          {running && live && <JobProgressCard live={live} />}

          {/* 実行中ジョブの非常停止 */}
          {running && latestJob && (
            <div className="flex items-center justify-between rounded-lg border border-border-warning bg-warning-bg px-4 py-3">
              <p className="text-sm text-warning-strong">
                ジョブが実行中です。必要な場合は安全に停止できます。
              </p>
              <EmergencyStopButton
                pending={stop.isPending}
                onConfirm={() => stop.mutate(latestJob.id)}
              />
            </div>
          )}
        </div>

        {/* 右カラム: 週次反映の操作 + 取り込みの操作。
            flex-col にして最後の「取り込む」カードを flex-1 で下まで伸ばし、左カラム
            (モニター+稼働状況) の下辺と揃える (PO要望 2026-07-09: 下辺のズレを解消)。 */}
        <div className="flex min-w-0 flex-col gap-4">
          {/* 週次反映ワークフロー — 操作 */}
          <Card className="p-5">
            <WeeklyApplyControls vm={weekly} />
          </Card>

          {/* カイポケから取り込む — 操作 (余白を許容して下辺を稼働状況カードに揃える) */}
          <Card className="flex-1 p-5">
            <InboundControls vm={inbound} />
          </Card>
        </div>
      </div>

      {/* ── 下段: 全幅の大きなカレンダー枠（タブ切替） ──
          min-h で常に大きな面積を確保し、1週間の予定が縦に潰れず全て見えるようにする
          (PO要望 2026-07-09: 週の予定が縦に映りきらない → 縦幅を伸ばす)。 */}
      <Card className="p-5">
        <Tabs value={tab} onValueChange={(v) => setTab(v as 'send' | 'inbound')}>
          <TabsList>
            <TabsTrigger value="send" data-testid="kaipoke-tab-send">
              送る（この週の予定）
            </TabsTrigger>
            <TabsTrigger value="inbound" data-testid="kaipoke-tab-inbound">
              取り込みプレビュー
            </TabsTrigger>
          </TabsList>
          <TabsContent value="send" className="mt-4 min-h-[460px]">
            <WeeklyApplyCalendar vm={weekly} />
          </TabsContent>
          <TabsContent value="inbound" className="mt-4 min-h-[460px]">
            <InboundCalendar vm={inbound} />
          </TabsContent>
        </Tabs>
      </Card>
    </div>
  );
}
