'use client';

/**
 * KaipokeConsole — カイポケ連携の操作コンソール (PO確定レイアウト 2026-07-09)。
 *
 * 上段: 左=ライブモニター、右=縦積みで「稼働状況(1行圧縮)」「週次反映の操作」「取り込みの操作」。
 * 下段: 全幅の大きなカレンダー枠。タブで「送る（この週の予定）」「取り込みプレビュー」を切替。
 *
 * 送る側/取り込み側の状態はそれぞれ useWeeklyApply / useInbound フックが 1 回だけ持ち、
 * 操作部 (上段 Controls) とカレンダー部 (下段) の 2 箇所へ描き分ける。ロジックは移動のみ。
 */
import { useEffect, useRef, useState } from 'react';

import { Card } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type { LiveSnapshot } from '@/lib/schemas/integration';
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
        {/* 左カラム: ライブモニター + 実行中のみ進捗ゲージ / 非常停止 */}
        <div className="min-w-0 space-y-4">
          <LiveMonitorCard
            monitorUrl={live?.monitorUrl}
            running={running}
            reachable={reachable}
            commandLabel={commandLabel(live?.command)}
          />

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

        {/* 右カラム: 稼働状況(1行圧縮) + 週次反映の操作 + 取り込みの操作 */}
        <div className="min-w-0 space-y-4">
          {/* 稼働状況（1行に圧縮） */}
          <Card className="p-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <LiveStatusDot tone={statusTone} label={statusLabel} />
              {liveLoading ? (
                <span className="text-text-muted">稼働状況を確認中…</span>
              ) : !reachable ? (
                <span className="break-all text-error">
                  {live?.error ?? '接続を確認してください'}
                </span>
              ) : (
                <span className="text-text-secondary">
                  現在:{' '}
                  <span className="font-medium text-text-primary">
                    {running ? (commandLabel(live?.command) ?? '実行中') : '待機中'}
                  </span>
                  <span className="mx-1.5 text-text-muted">・</span>
                  直近:{' '}
                  <span className="font-medium text-text-primary">
                    {latestJob ? `${latestJob.job_type} / ${latestJob.status}` : 'なし'}
                  </span>
                </span>
              )}
            </div>
          </Card>

          {/* 週次反映ワークフロー — 操作 */}
          <Card className="p-5">
            <WeeklyApplyControls vm={weekly} />
          </Card>

          {/* カイポケから取り込む — 操作 */}
          <Card className="p-5">
            <InboundControls vm={inbound} />
          </Card>
        </div>
      </div>

      {/* ── 下段: 全幅の大きなカレンダー枠（タブ切替） ── */}
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
          <TabsContent value="send" className="mt-4">
            <WeeklyApplyCalendar vm={weekly} />
          </TabsContent>
          <TabsContent value="inbound" className="mt-4">
            <InboundCalendar vm={inbound} />
          </TabsContent>
        </Tabs>
      </Card>
    </div>
  );
}
