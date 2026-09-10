'use client';
/**
 * 予実比較レポート (月) のカード — 連携（カイポケ）コンソールの操作パネルに置く。
 *
 * ①月を選ぶ → ②「実績を取得して比較」(RPA・約2分) → ③「最新のレポートを開く」。
 * ②の POST は 202 で即返り、ジョブは BE 側で走り続ける。そのため画面は
 *   - 対象月のジョブが pending/running の間だけジョブ一覧を 5 秒間隔でポーリング
 *   - ライブの running が true→false に落ちた瞬間にジョブ一覧を invalidate
 * の 2 段構えで完了を拾う (リロード不要)。
 *
 * 直近の結果 (件数) はジョブ履歴の `result_summary` から読む — レポートを開かなくても
 * 「一致がいくつ・相違がいくつ」だけは画面で分かるようにする (PO 要望)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api-client';
import { jstDateString } from '@/lib/format/patientStatus';
import { isPlanActualJob, jobMonth } from '@/lib/kaipokeOps';
import {
  useKaipokeJobs,
  useKaipokeLive,
  useStartPlanActualCompare,
} from '@/lib/queries/integrations';
import { planActualErrorDetail } from '@/lib/queries/planActualReport';
import {
  PLAN_ACTUAL_COUNT_KEYS,
  PlanActualSummarySchema,
  type KaipokeJob,
} from '@/lib/schemas/integration';

import { PlanActualReportButton } from './PlanActualReportButton';

/** 未確定実績の扱い (カイポケの CSV 仕様) — PO へ毎回口頭で説明していた注意書き。 */
const UNCONFIRMED_NOTE =
  '職種が未設定（画面で「未」）の実績行は「職種未設定」として一覧に出ます。同じ担当の同日複数行とあわせて実績側を確認してください';

/** 応答が返らなかったとき (5xx / 524 / ネットワーク断) の案内。ジョブは走っている可能性が高い。 */
const AMBIGUOUS_START_NOTE =
  '応答が返りませんでしたが、取得は続いている可能性があります。しばらく待って「最新のレポートを開く」でご確認ください。';

/** 二度押し防止の保険 (ジョブ一覧に pending が現れるまでの空白を埋める)。 */
const START_LOCK_MS = 15_000;

/** 実行中ジョブを追いかける間隔。 */
const JOB_POLL_MS = 5_000;

/** ジョブ一覧のキー (useRelayMutation の invalidate と同じ)。 */
const JOBS_QUERY_KEY = ['integrations', 'kaipoke', 'jobs'] as const;

/** `YYYY-MM` → 「2026年8月」。 */
export function formatMonthLabel(month: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  return m ? `${Number(m[1])}年${Number(m[2])}月` : month;
}

/** `YYYY-MM` に月を足す。 */
function shiftMonth(month: string, delta: number): string {
  const [y = 0, m = 1] = month.split('-').map(Number);
  const idx = y * 12 + (m - 1) + delta;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, '0')}`;
}

/** 選べる月 = 前3か月・当月・翌月 (古い順)。基準は JST の今日。 */
export function planActualMonthOptions(current = jstDateString().slice(0, 7)): string[] {
  return [-3, -2, -1, 0, 1].map((d) => shiftMonth(current, d));
}

/** 既定の月 = 前月 (予実は月が締まってから前月を見るのが通常運用)。 */
export function planActualDefaultMonth(current = jstDateString().slice(0, 7)): string {
  return shiftMonth(current, -1);
}

/**
 * 区分ではなく「内数」のタグ (BE の counts に同じキーで入る)。0 件なら出さない。
 * 区分キー (PLAN_ACTUAL_COUNT_KEYS) に混ぜると「合計が合わない」表示になるため分ける。
 */
const PLAN_ACTUAL_TAG_KEYS = ['職種未設定', '同日複数'] as const;

/**
 * 件数の 1 行表示。0 件の区分は畳んで「一致 480・相違 3」のように短く出す。
 * 内数のタグ (職種未設定・同日複数) は 0 件なら出さず、区分の後ろに足す。
 * 訪問ではないイベント行 (events_skipped) は区分に混ぜず、末尾に添える。
 */
export function formatCountsLine(counts: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const key of PLAN_ACTUAL_COUNT_KEYS) {
    const v = counts[key];
    if (typeof v !== 'number') continue;
    // 一致は 0 でも出す (「全部ズレている」ことが分かるように)。
    if (v === 0 && key !== '一致') continue;
    parts.push(`${key} ${v}`);
  }
  for (const key of PLAN_ACTUAL_TAG_KEYS) {
    const v = counts[key];
    if (typeof v === 'number' && v > 0) parts.push(`${key} ${v}`);
  }
  const skipped = counts['events_skipped'];
  const suffix = typeof skipped === 'number' && skipped > 0 ? `（イベント除外 ${skipped}）` : '';
  return `${parts.join('・')}${suffix}`;
}

/** 実行中 = まだ結果が出ていないジョブ。 */
function isActiveJob(job: KaipokeJob | null): boolean {
  return job?.status === 'pending' || job?.status === 'running';
}

export interface PlanActualReportCardProps {
  /** カイポケ接続設定が済んでいるか (未設定なら開始できない・他カードと同じ扱い)。 */
  credentialsConfigured?: boolean;
}

export function PlanActualReportCard({ credentialsConfigured = true }: PlanActualReportCardProps) {
  // 長時間開きっぱなしのタブでも日付が進めば選択肢が入れ替わるよう、毎レンダーで
  // JST の当月を取り直し、その文字列をキーにして memo する。
  const todayMonth = jstDateString().slice(0, 7);
  const options = useMemo(() => planActualMonthOptions(todayMonth), [todayMonth]);
  const [month, setMonth] = useState(() => planActualDefaultMonth());

  const qc = useQueryClient();
  const live = useKaipokeLive();
  const start = useStartPlanActualCompare();

  const liveRunning = Boolean(live.data?.running);

  // ライブが running → idle に落ちた瞬間 = 何かが終わった瞬間。ジョブ一覧を取り直す。
  const prevLiveRunning = useRef(false);
  useEffect(() => {
    if (prevLiveRunning.current && !liveRunning) {
      void qc.invalidateQueries({ queryKey: [...JOBS_QUERY_KEY] });
    }
    prevLiveRunning.current = liveRunning;
  }, [liveRunning, qc]);

  // 対象月のジョブが動いている間だけポーリング (それ以外は従来どおり手動更新)。
  const [polling, setPolling] = useState(false);
  const jobsQuery = useKaipokeJobs({ limit: 20, refetchInterval: polling ? JOB_POLL_MS : false });
  const jobs = useMemo(() => jobsQuery.data?.items ?? [], [jobsQuery.data]);

  /** 選択中の月の最新の予実比較ジョブ (履歴は新しい順)。 */
  const latestJob: KaipokeJob | null = useMemo(
    () => jobs.find((j) => isPlanActualJob(j) && jobMonth(j) === month) ?? null,
    [jobs, month],
  );

  /** 月を問わず動いている予実比較ジョブ (二重起動の防止に使う)。 */
  const activeJob: KaipokeJob | null = useMemo(
    () => jobs.find((j) => isPlanActualJob(j) && isActiveJob(j)) ?? null,
    [jobs],
  );

  // ライブ側にしか出ていない実行中ジョブも拾う (ジョブ一覧が追いつく前の数秒)。
  const liveJob = liveRunning ? (live.data?.latestJob ?? null) : null;
  const liveJobIsPlanActual = isPlanActualJob(liveJob);
  const monthRunning =
    isActiveJob(latestJob) || (liveJobIsPlanActual && jobMonth(liveJob) === month);
  const otherMonth = activeJob
    ? jobMonth(activeJob)
    : liveJobIsPlanActual
      ? jobMonth(liveJob)
      : null;
  const otherPlanActualRunning = !monthRunning && (activeJob != null || liveJobIsPlanActual);

  useEffect(() => {
    setPolling(monthRunning);
  }, [monthRunning]);

  /** 開始直後の空白を埋める短いロック (POST は 202 即返しなので一覧に出るまで数秒ある)。 */
  const [locked, setLocked] = useState(false);
  const lockTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (lockTimer.current != null) window.clearTimeout(lockTimer.current);
    },
    [],
  );
  const lock = useCallback(() => {
    setLocked(true);
    if (lockTimer.current != null) window.clearTimeout(lockTimer.current);
    lockTimer.current = window.setTimeout(() => setLocked(false), START_LOCK_MS);
  }, []);

  /** 完了ジョブの件数 (result_summary)。BE が形を変えても落ちないよう寛容に読む。 */
  const summaryLine = useMemo(() => {
    if (latestJob?.status !== 'completed') return null;
    const parsed = PlanActualSummarySchema.safeParse(latestJob.result_summary ?? {});
    if (!parsed.success) return null;
    return formatCountsLine(parsed.data.counts) || null;
  }, [latestJob]);

  /** 失敗ジョブの理由 (BE は result_summary.error に日本語で入れる)。 */
  const errorLine = useMemo(() => {
    if (latestJob?.status !== 'failed') return null;
    const parsed = PlanActualSummarySchema.safeParse(latestJob.result_summary ?? {});
    const detail = parsed.success ? parsed.data.error : null;
    return detail ?? '取得に失敗しました。もう一度お試しください。';
  }, [latestJob]);

  const busy = liveRunning || activeJob != null || locked || !credentialsConfigured;

  const onStart = useCallback(async () => {
    try {
      await start.mutateAsync({ month });
      lock();
      toast.success(
        `${formatMonthLabel(month)}の実績取得を開始しました（約2分）。終わると「直近の結果」に件数が出ます。`,
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        toast.warning('別の処理が実行中です');
        return;
      }
      if (e instanceof ApiError && e.status < 500) {
        // 4xx は BE の日本語 detail をそのまま見せる (無ければ定型文)。
        toast.error(
          planActualErrorDetail(e) ??
            '実績の取得を開始できませんでした。時間をおいてもう一度お試しください。',
        );
        return;
      }
      // 5xx / 524 / ネットワーク断: ジョブ自体は走っている可能性が高いので、
      // 生の例外メッセージは出さず「待って確認」に誘導する (二度押しもロックで防ぐ)。
      lock();
      toast.warning(AMBIGUOUS_START_NOTE);
    }
  }, [start, month, lock]);

  return (
    <div data-testid="plan-actual-card">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">予実比較（月）</h2>
        <span className="text-sm text-text-secondary">カイポケの予定 × 実績</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="plan-actual-month" className="text-sm text-text-secondary">
          対象月
        </label>
        <select
          id="plan-actual-month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="h-9 rounded-md border border-border-default bg-bg-base px-3 text-sm tabular-nums text-text-primary"
          data-testid="plan-actual-month-select"
        >
          {options.map((m) => (
            <option key={m} value={m}>
              {formatMonthLabel(m)}
            </option>
          ))}
        </select>

        <Button
          type="button"
          size="md"
          className="h-9"
          onClick={onStart}
          disabled={busy || start.isPending}
          title={
            credentialsConfigured
              ? 'カイポケから対象月の実績を取得し、らく助の予定と突き合わせます（約2分・RPA を使用）'
              : '先に接続設定を完了してください'
          }
          data-testid="plan-actual-start-button"
        >
          {start.isPending ? '開始中…' : '実績を取得して比較'}
        </Button>

        <PlanActualReportButton
          month={month}
          size="md"
          label="最新のレポートを開く"
          className="h-9"
        />
      </div>

      {/* 実行中の案内 (単一スロットなので他オペで塞がっていることもある) */}
      {(monthRunning || otherPlanActualRunning || liveRunning) && (
        <p className="mt-2 text-sm text-info" data-testid="plan-actual-running">
          {monthRunning
            ? `${formatMonthLabel(month)}の実績を取得中…（約2分）`
            : otherPlanActualRunning
              ? `${formatMonthLabel(otherMonth ?? '')}の実績を取得中…（約2分）`
              : '他の処理が実行中です。終わってから実行してください。'}
        </p>
      )}

      {/* 直近の結果 (完了ジョブの件数) */}
      {summaryLine && (
        <p className="mt-2 text-sm text-text-primary" data-testid="plan-actual-summary">
          <span className="text-text-secondary">直近の結果：</span>
          {summaryLine}
        </p>
      )}

      {/* 直近が失敗していたら理由をそのまま出す (握りつぶさない) */}
      {errorLine && (
        <p className="mt-2 text-sm font-medium text-error" data-testid="plan-actual-error">
          {errorLine}
        </p>
      )}

      <p className="mt-2 text-sm text-text-secondary" data-testid="plan-actual-note">
        {UNCONFIRMED_NOTE}
      </p>
    </div>
  );
}
