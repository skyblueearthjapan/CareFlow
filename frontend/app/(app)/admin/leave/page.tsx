'use client';

/**
 * /admin/leave — 「休み・勤務表」(PC・管理者だけ)。
 *
 * 正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §5・
 * モック docs/mockups/leave-admin-mock-2026-10-20.html「休み・勤務表（案A）」。
 *
 *   - 上: 月の切り替え・確定の状態・照合の状態・「休みの時間帯に担当が付いている訪問 N 件」・
 *         押すと入るもの (選んで操作／公休／午前休／午後休／有給／取り消す)・事業所の休み・
 *         申請をまとめて承認・この月を確定して通知
 *   - 中央: 職員×日付の表 (名前の列を固定・表の中だけ横にスクロール)
 *   - 右: 申請中 (日付順／職員順・日ごと／人ごとにまとめて承認)
 *   - 下: 勤務表 (Google スプレッドシート) の接続の状態
 * 取り消しはマスの小窓の「取り消す」だけ (「元に戻す」は出さない・§5-3)。
 * ?staff=&date= で開くとそのマスの小窓を開く (申請履歴の「表で開く」)。?staff= だけならその人の行を光らせる。
 */
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, ExternalLink } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { BulkApproveDialog } from '@/components/leave/BulkApproveDialog';
import { ClosedDaysDialog } from '@/components/leave/ClosedDaysDialog';
import { LeaveCellPopover } from '@/components/leave/LeaveCellPopover';
import { LeaveConfirmDialog } from '@/components/leave/LeaveConfirmDialog';
import { LeavePendingPanel } from '@/components/leave/LeavePendingPanel';
import { LeaveSheetGrid } from '@/components/leave/LeaveSheetGrid';
import { cn } from '@/lib/utils';
import { isAdminRole } from '@/lib/rbac';
import {
  addMonths,
  cellKey,
  fmtDateTimeShort,
  fmtDayJp,
  fmtMonthJp,
  fmtNum,
  LEAVE_LABEL,
  LEAVE_STAMP_TYPES,
  parseMonthParam,
  upcomingLeaveMonth,
  WEEKDAY_FROM_MON,
} from '@/lib/leave-sheet';
import { useGoogleDriveStatus } from '@/lib/queries/googleDrive';
import {
  invalidateAfterLeave,
  leaveErrorMessage,
  leaveErrorRequestId,
  useApproveLeaveBatch,
  useApproveLeaveRequest,
  useCancelLeaveDay,
  useConfirmLeaveMonth,
  useDeleteClosedDay,
  useLeaveMonth,
  usePutClosedDay,
  usePutLeaveMonth,
  useRejectLeaveRequest,
  useKinmuSheets,
  useSetLeaveDay,
  useSetLeaveDayBulk,
} from '@/lib/queries/leave';
import type {
  LeaveDayResult,
  LeaveMonthView,
  LeavePendingItem,
  LeaveTypeCode,
} from '@/lib/schemas/leave';

type Stamp = 'pointer' | LeaveTypeCode | 'clear';

const STAMP_SWATCH: Record<Exclude<Stamp, 'pointer'>, string> = {
  off: 'bg-brand-primary-light',
  am_off: 'bg-[linear-gradient(to_bottom,var(--brand-primary-light)_50%,var(--bg-base)_50%)]',
  pm_off: 'bg-[linear-gradient(to_bottom,var(--bg-base)_50%,var(--brand-primary-light)_50%)]',
  paid_off: 'bg-emerald-100',
  clear: 'bg-bg-base',
};

interface PopTarget {
  staffId: string;
  date: string;
  anchor: HTMLElement;
}

export default function AdminLeavePage() {
  // useSearchParams は Suspense 境界が必須 (Next 15 の CSR bailout 対策)。
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
      <LeaveSheetPage />
    </Suspense>
  );
}

/** 休みを入れた結果 → トーストの説明。 */
function describeResult(r: LeaveDayResult): string {
  const parts: string[] = [];
  if (r.past_day) parts.push('今日より前の日なので、休みの記録だけです');
  if (r.moved_visit_count > 0) {
    parts.push(
      `訪問 ${r.moved_visit_count} 件（${r.moved_patient_count} 名）を「担当なし」へ移しました`,
    );
  }
  if (r.skipped_visit_count > 0)
    parts.push(`打刻済みなどの訪問 ${r.skipped_visit_count} 件はそのままです`);
  if (r.cancelled_event_count > 0)
    parts.push(`固定の予定 ${r.cancelled_event_count} 件を取り消しました`);
  if (r.accompaniment_count > 0) {
    parts.push(`同行 ${r.accompaniment_count} 件はそのままです。職員スケジュールで外してください`);
  }
  return parts.join('。');
}

function LeaveSheetPage() {
  const { data: session, status: sessionStatus } = useSession();
  const isAdmin = isAdminRole(session?.user?.role);
  const params = useSearchParams();
  const linkStaff = params.get('staff');
  const linkDate = params.get('date');
  const linkMonth = params.get('month');

  const [month, setMonth] = useState<string>(
    () => parseMonthParam(linkMonth) ?? parseMonthParam(linkDate) ?? upcomingLeaveMonth(),
  );
  const monthQuery = useLeaveMonth(month, isAdmin);
  const view = monthQuery.data;

  const [stamp, setStamp] = useState<Stamp>('pointer');
  const [pop, setPop] = useState<PopTarget | null>(null);
  const [groupMode, setGroupMode] = useState<'date' | 'staff'>('date');
  const [focusPending, setFocusPending] = useState(false);
  const [highlight, setHighlight] = useState<{ staffId: string; date: string } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [closedOpen, setClosedOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);

  const qc = useQueryClient();
  // 書き込みは 1 件ずつ順に (並行の POST を出さない)。読み直しは落ち着いてから 1 回だけ。
  const setDay = useSetLeaveDay({ invalidate: false });
  const setDayBulk = useSetLeaveDayBulk({ invalidate: false });
  const cancelDay = useCancelLeaveDay({ invalidate: false });
  const approveOne = useApproveLeaveRequest({ invalidate: false });
  const approveBatch = useApproveLeaveBatch({ invalidate: false });
  const reject = useRejectLeaveRequest({ invalidate: false });
  const putClosed = usePutClosedDay();
  const deleteClosed = useDeleteClosedDay();
  const putMonth = usePutLeaveMonth();
  const confirmMonth = useConfirmLeaveMonth();
  const [confirming, setConfirming] = useState(false);

  const staffById = useMemo(() => new Map((view?.staff ?? []).map((s) => [s.id, s])), [view]);
  const dayByDate = useMemo(() => new Map((view?.days ?? []).map((d) => [d.date, d])), [view]);
  const cellByKey = useMemo(
    () => new Map((view?.cells ?? []).map((c) => [cellKey(c.staff_id, c.date), c])),
    [view],
  );
  const pendingById = useMemo(() => new Map((view?.pending ?? []).map((p) => [p.id, p])), [view]);

  const nameOf = useCallback((staffId: string) => staffById.get(staffId)?.name ?? '', [staffById]);

  // ── 書き込みの順番待ち ───────────────────────────────────────────────
  /** 書き込みが進行中・順番待ちのマス (押せなくして薄く出す)。 */
  const [busyCells, setBusyCells] = useState<ReadonlySet<string>>(() => new Set());
  const busyRef = useRef(new Set<string>());
  const queueRef = useRef<{ keys: string[]; run: () => Promise<void> }[]>([]);
  const drainingRef = useRef(false);

  const drain = async (): Promise<void> => {
    if (drainingRef.current) return;
    drainingRef.current = true;
    const done: string[] = [];
    try {
      for (let job = queueRef.current.shift(); job; job = queueRef.current.shift()) {
        // 各仕事は自分で成功・失敗のトーストを出す
        await job.run().catch(() => undefined);
        done.push(...job.keys);
      }
      await invalidateAfterLeave(qc).catch(() => undefined);
    } finally {
      for (const k of done) busyRef.current.delete(k);
      setBusyCells(new Set(busyRef.current));
      drainingRef.current = false;
      // 読み直しのあいだに押されたものがあれば続ける
      if (queueRef.current.length > 0) void drain();
    }
  };

  /** 書き込みを順番待ちに入れる。そのマスが進行中なら何もしない (連打)。 */
  const enqueue = (keys: string[], run: () => Promise<void>): boolean => {
    if (keys.some((k) => busyRef.current.has(k))) return false;
    for (const k of keys) busyRef.current.add(k);
    setBusyCells(new Set(busyRef.current));
    queueRef.current.push({ keys, run });
    void drain();
    return true;
  };

  // 月を変えたら小窓・光りを閉じる
  const changeMonth = (delta: number) => {
    setPop(null);
    setHighlight(null);
    setMonth((m) => addMonths(m, delta));
  };

  // ── 操作 ──────────────────────────────────────────────────────────────
  const doSetLeave = (staffId: string, date: string, type: LeaveTypeCode) =>
    enqueue([cellKey(staffId, date)], async () => {
      try {
        const r = await setDay.mutateAsync({ staff_id: staffId, date, leave_type: type });
        const desc = describeResult(r);
        toast.success(`${nameOf(staffId)}さん ${fmtDayJp(date)} を${LEAVE_LABEL[type]}にしました`, {
          description: desc || undefined,
        });
      } catch (e) {
        toast.error(leaveErrorMessage(e, '休みを入れられませんでした'));
      }
    });

  const doCancelLeave = (staffId: string, date: string) =>
    enqueue([cellKey(staffId, date)], async () => {
      try {
        const r = await cancelDay.mutateAsync({ staff_id: staffId, date });
        toast.success(`取り消しました（${nameOf(staffId)}さん ${fmtDayJp(date)}）`, {
          description:
            r.moved_visit_count > 0
              ? `担当なしへ移した訪問 ${r.moved_visit_count} 件は自動では戻りません`
              : undefined,
        });
      } catch (e) {
        toast.error(leaveErrorMessage(e, '取り消せませんでした'));
      }
    });

  const doApprove = (item: LeavePendingItem, overrideType: LeaveTypeCode | null = null) =>
    enqueue([cellKey(item.staff_id, item.date)], async () => {
      try {
        const r = await approveOne.mutateAsync({ id: item.id, override_type: overrideType });
        setHighlight(null);
        const label = LEAVE_LABEL[r.override_type];
        toast.success(`承認しました（${item.staff_name}さん ${fmtDayJp(item.date)} ${label}）`, {
          description: [r.result ? describeResult(r.result) : '', '本人へ通知しました']
            .filter(Boolean)
            .join('。'),
        });
      } catch (e) {
        toast.error(leaveErrorMessage(e, '承認できませんでした'));
      }
    });

  const doApproveMany = (ids: string[]) => {
    const items = ids
      .map((id) => pendingById.get(id))
      .filter((x): x is LeavePendingItem => x !== undefined);
    if (items.length === 0) return;
    const byId = new Map(items.map((i) => [i.id, i]));
    enqueue(
      items.map((i) => cellKey(i.staff_id, i.date)),
      async () => {
        try {
          const r = await approveBatch.mutateAsync(items.map((i) => i.id));
          setBulkOpen(false);
          setHighlight(null);
          toast.success(`${r.approved.length} 件を承認し、本人へ通知しました`, {
            description:
              r.moved_visit_count > 0
                ? `訪問 ${r.moved_visit_count} 件を「担当なし」へ移しました（通知を見てください）`
                : undefined,
          });
        } catch (e) {
          const msg = leaveErrorMessage(e, 'まとめて承認できませんでした');
          const rid = leaveErrorRequestId(e);
          const it = rid ? byId.get(rid) : undefined;
          toast.error(it ? `${it.staff_name}さん ${fmtDayJp(it.date)}：${msg}` : msg, {
            description: '1 件でも承認できないと、まとめて承認は全部やめます',
          });
        }
      },
    );
  };

  const doReject = (item: LeavePendingItem, reason: string) =>
    enqueue([cellKey(item.staff_id, item.date)], async () => {
      try {
        await reject.mutateAsync({ id: item.id, reason });
        setPop(null);
        toast.success(
          `却下して本人へ通知しました（${item.staff_name}さん ${fmtDayJp(item.date)}）`,
        );
      } catch (e) {
        toast.error(leaveErrorMessage(e, '却下できませんでした'));
      }
    });

  /**
   * 「全員に公休を入れる」の相手 (件数の表示用・実際の相手はサーバーが同じ決まりで決める):
   * その日に勤務する在籍の常勤 (新人は含む)。型で勤務しない曜日の人・非常勤・休職中・
   * 申請中・休みが入っている人は除く。
   */
  const fillTargets = useCallback(
    (date: string, v: LeaveMonthView | undefined) => {
      const day = dayByDate.get(date);
      if (!v || !day) return [];
      return v.staff.filter((s) => {
        if (!s.is_full_time || s.status !== 'active') return false;
        if (s.off_weekdays.includes(day.weekday)) return false;
        const c = cellByKey.get(cellKey(s.id, date));
        return !c?.leave_type && !c?.pending;
      });
    },
    [dayByDate, cellByKey],
  );

  const doFillAll = (date: string) => {
    const targets = fillTargets(date, view);
    if (targets.length === 0) return;
    enqueue(
      targets.map((s) => cellKey(s.id, date)),
      async () => {
        // 1 トランザクション・管理者への通知は 1 通 (相手はサーバーが決める)
        try {
          const r = await setDayBulk.mutateAsync({ date, override_type: 'off' });
          toast.success(`${fmtDayJp(date)} に ${r.applied.length} 人の公休を入れました`, {
            description:
              r.moved_visit_count > 0
                ? `訪問 ${r.moved_visit_count} 件を「担当なし」へ移しました`
                : undefined,
          });
        } catch (e) {
          toast.error(leaveErrorMessage(e, '公休を入れられませんでした'), {
            description: '1 人でも入れられないと、全部やめます',
          });
        }
      },
    );
  };

  const doConfirm = async (requiredHours: number | null | undefined) => {
    setConfirming(true);
    try {
      if (requiredHours !== undefined) {
        await putMonth.mutateAsync({ month, required_hours: requiredHours });
      }
      const r = await confirmMonth.mutateAsync(month);
      setConfirmOpen(false);
      toast.success(`${fmtMonthJp(month)}を確定し、${r.notified_count} 人へ通知しました`, {
        description:
          [
            r.pending_remaining > 0 ? `申請中 ${r.pending_remaining} 件は申請中のままです` : '',
            r.short_staff.length > 0 ? `休日が足りない人 ${r.short_staff.length} 人` : '',
          ]
            .filter(Boolean)
            .join('・') || undefined,
      });
    } catch (e) {
      toast.error(leaveErrorMessage(e, '確定できませんでした'));
    } finally {
      setConfirming(false);
    }
  };

  // ── マスを押す ────────────────────────────────────────────────────────
  // 表のマスは memo なので、渡す関数は変えない (中身は ref で最新にする)
  const cellClickRef = useRef<(staffId: string, date: string, el: HTMLElement) => void>(
    () => undefined,
  );
  cellClickRef.current = (staffId, date, el) => {
    if (busyRef.current.has(cellKey(staffId, date))) return;
    const cell = cellByKey.get(cellKey(staffId, date));
    // 押すと入るもの: 申請中のマスは上書きせず小窓を出す
    if (stamp !== 'pointer' && !cell?.pending) {
      const leave = cell?.leave_type ?? null;
      if (stamp === 'clear') {
        if (leave) doCancelLeave(staffId, date);
        return;
      }
      const s = staffById.get(staffId);
      const d = dayByDate.get(date);
      if (s && d && !leave && s.off_weekdays.includes(d.weekday)) {
        toast.info(
          `${s.name}さんは${WEEKDAY_FROM_MON[d.weekday]}曜日がもともと勤務外です（休日に数えています）。休みを入れるときは「選んで操作」でマスの小窓から入れてください`,
        );
        return;
      }
      if (leave === stamp) return;
      doSetLeave(staffId, date, stamp);
      return;
    }
    setPop({ staffId, date, anchor: el });
  };
  const onCellClick = useCallback(
    (staffId: string, date: string, el: HTMLElement) => cellClickRef.current(staffId, date, el),
    [],
  );

  // 一覧に指を置いたら、表のマスと列を光らせ、見えない所ならマスまで寄せる
  const onHighlight = useCallback((target: { staffId: string; date: string } | null) => {
    setHighlight(target);
    if (!target) return;
    document
      .querySelector<HTMLElement>(`[data-cell="${cellKey(target.staffId, target.date)}"]`)
      ?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, []);

  const openCellFor = (item: LeavePendingItem) => {
    const el = document.querySelector<HTMLElement>(
      `[data-cell="${cellKey(item.staff_id, item.date)}"]`,
    );
    if (!el) return;
    el.scrollIntoView?.({ block: 'nearest', inline: 'center' });
    setPop({ staffId: item.staff_id, date: item.date, anchor: el });
  };

  // ?staff=&date= (または ?month=) が変わったら、月と小窓もそれに合わせる
  const linkKey = `${linkStaff ?? ''}|${linkDate ?? ''}|${linkMonth ?? ''}`;
  const prevLinkKey = useRef(linkKey);
  const linkHandled = useRef(false);
  useEffect(() => {
    if (prevLinkKey.current === linkKey) return;
    prevLinkKey.current = linkKey;
    const m = parseMonthParam(linkMonth) ?? parseMonthParam(linkDate);
    if (m) setMonth(m);
    setPop(null);
    linkHandled.current = false;
  }, [linkKey, linkMonth, linkDate]);

  // ?staff=&date= で開いたとき: そのマスの小窓を開く (リンクごとに 1 回)
  useEffect(() => {
    if (linkHandled.current || !view || !linkStaff) return;
    if (view.month.slice(0, 7) !== month) return;
    linkHandled.current = true;
    if (linkDate && linkDate.slice(0, 7) === month) {
      const el = document.querySelector<HTMLElement>(
        `[data-cell="${cellKey(linkStaff, linkDate)}"]`,
      );
      if (el) {
        el.scrollIntoView?.({ block: 'nearest', inline: 'center' });
        setPop({ staffId: linkStaff, date: linkDate, anchor: el });
      }
      return;
    }
    document
      .querySelector<HTMLElement>(`[data-staff-row="${linkStaff}"]`)
      ?.scrollIntoView?.({ block: 'nearest' });
  }, [view, linkStaff, linkDate, month, linkKey]);

  // ── 描画 ──────────────────────────────────────────────────────────────
  if (sessionStatus === 'loading') return null;

  const header = (
    <header>
      <RakusukeTitle
        pose="calendar"
        title="休み・勤務表"
        subtitle="全員の月の休みを見て、申請を承認し、足りない人に休みを足して、月を確定します。"
      />
    </header>
  );

  if (!isAdmin) {
    return (
      <section className="space-y-4">
        {header}
        <Alert>
          <AlertDescription>このページは管理者だけが使えます。</AlertDescription>
        </Alert>
      </section>
    );
  }

  const popStaff = pop ? staffById.get(pop.staffId) : undefined;
  const popDay = pop ? dayByDate.get(pop.date) : undefined;
  const popCell = pop ? cellByKey.get(cellKey(pop.staffId, pop.date)) : undefined;
  const popPending = popCell?.pending
    ? (pendingById.get(popCell.pending.request_id) ?? null)
    : null;

  return (
    <section className="min-w-0 max-w-full space-y-4" data-testid="leave-page">
      {header}

      <Card className="space-y-3 p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label="前の月"
              onClick={() => changeMonth(-1)}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <p className="font-serif text-lg font-bold text-text-primary tnum" aria-live="polite">
              {fmtMonthJp(month)}
            </p>
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label="次の月"
              onClick={() => changeMonth(1)}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
            {view && <ConfirmBadge view={view} />}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={!view}
              onClick={() => setClosedOpen(true)}
            >
              事業所の休み
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!view || view.pending.length === 0 || busyCells.size > 0}
              onClick={() => setBulkOpen(true)}
            >
              申請をまとめて承認
              <span className="rounded-full bg-brand-primary px-2 text-xs font-bold text-white tnum">
                {view?.pending.length ?? 0}
              </span>
            </Button>
            <Button type="button" disabled={!view} onClick={() => setConfirmOpen(true)}>
              この月を確定して通知
            </Button>
          </div>
        </div>

        {view && <StatusLine view={view} />}

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-text-muted">押すと入るもの</span>
          <div
            role="group"
            aria-label="押すと入るもの"
            className="flex flex-wrap gap-1 rounded-lg bg-bg-muted p-1"
          >
            {(['pointer', ...LEAVE_STAMP_TYPES, 'clear'] as Stamp[]).map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={stamp === s}
                onClick={() => {
                  setPop(null);
                  setStamp(s);
                }}
                className={cn(
                  'inline-flex min-h-[34px] items-center gap-1.5 rounded-md border px-2.5 text-sm',
                  stamp === s
                    ? 'border-border-strong bg-bg-base font-bold text-text-primary shadow-sm'
                    : 'border-transparent text-text-secondary',
                )}
              >
                {s !== 'pointer' && (
                  <i
                    aria-hidden
                    className={cn(
                      'h-3.5 w-4 rounded-sm border border-border-strong',
                      STAMP_SWATCH[s],
                    )}
                  />
                )}
                {s === 'pointer' ? '選んで操作' : s === 'clear' ? '取り消す' : LEAVE_LABEL[s]}
              </button>
            ))}
          </div>
        </div>
        <p className="text-sm text-text-secondary" data-testid="stamp-hint">
          {stamp === 'pointer'
            ? '申請は右の「申請中」に並び、表の日付の上に「申N」と件数が出ます。マスを押すとその日の内容と操作が出ます。上の「押すと入るもの」を選ぶと、押したマスにそのまま入ります（続けて何マスでも）。'
            : `「${stamp === 'clear' ? '取り消す' : LEAVE_LABEL[stamp]}」を選んでいます。押したマスにそのまま入ります。申請中のマスは上書きせず、承認・却下の小窓を出します。終わったら「選んで操作」に戻してください。`}
        </p>
      </Card>

      {monthQuery.isError ? (
        <Alert variant="destructive">
          <AlertTitle>読み込めませんでした</AlertTitle>
          <AlertDescription>
            {leaveErrorMessage(monthQuery.error, '月の休みを読み込めませんでした')}
          </AlertDescription>
        </Alert>
      ) : !view ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <>
          <DayNotes view={view} onOpenClosedDays={() => setClosedOpen(true)} />
          <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <Card className="min-w-0 overflow-hidden p-0">
              <LeaveSheetGrid
                view={view}
                focusPending={focusPending}
                highlight={highlight}
                highlightStaffId={linkStaff}
                busyCells={busyCells}
                expandedCell={pop ? cellKey(pop.staffId, pop.date) : null}
                onCellClick={onCellClick}
              />
              <Legend />
            </Card>
            <LeavePendingPanel
              view={view}
              mode={groupMode}
              onModeChange={setGroupMode}
              focusPending={focusPending}
              onFocusPendingChange={setFocusPending}
              busyCells={busyCells}
              onHighlight={onHighlight}
              onApprove={(it) => doApprove(it)}
              onApproveMany={(items) => doApproveMany(items.map((i) => i.id))}
              onOpenCell={openCellFor}
            />
          </div>
          <DriveCard month={month} />
        </>
      )}

      {view && pop && popStaff && popDay && (
        <LeaveCellPopover
          key={`${pop.staffId}|${pop.date}`}
          anchor={pop.anchor}
          staff={popStaff}
          day={popDay}
          cell={popCell}
          pending={popPending}
          otherPendingSameDay={view.pending.filter(
            (p) => p.date === pop.date && p.id !== popPending?.id,
          )}
          offWarnThreshold={view.off_warn_threshold}
          today={view.today}
          busy={busyCells.has(cellKey(pop.staffId, pop.date))}
          onClose={() => setPop(null)}
          onSetLeave={(t) => {
            setPop(null);
            doSetLeave(pop.staffId, pop.date, t);
          }}
          onCancelLeave={() => {
            setPop(null);
            doCancelLeave(pop.staffId, pop.date);
          }}
          onApprove={(t) => {
            if (!popPending) return;
            setPop(null);
            doApprove(popPending, t);
          }}
          onReject={(reason) => popPending && doReject(popPending, reason)}
        />
      )}

      {view && (
        <>
          <LeaveConfirmDialog
            open={confirmOpen}
            onOpenChange={setConfirmOpen}
            month={month}
            view={view}
            busy={confirming}
            onConfirm={(h) => void doConfirm(h)}
          />
          <ClosedDaysDialog
            open={closedOpen}
            onOpenChange={setClosedOpen}
            month={month}
            firstDay={view.days[0]?.date ?? `${month}-01`}
            lastDay={view.days[view.days.length - 1]?.date ?? `${month}-01`}
            closedDays={view.closed_days}
            busy={putClosed.isPending || deleteClosed.isPending || busyCells.size > 0}
            fillTargetCount={(d) => fillTargets(d, view).length}
            onAdd={(date, label) =>
              putClosed
                .mutateAsync({ date, label })
                .then(() => toast.success(`${fmtDayJp(date)} を事業所の休み（${label}）にしました`))
                .catch((e: unknown) => toast.error(leaveErrorMessage(e, '入れられませんでした')))
            }
            onDelete={(date) =>
              deleteClosed
                .mutateAsync(date)
                .then(() => toast.success(`${fmtDayJp(date)} の事業所の休みを消しました`))
                .catch((e: unknown) => toast.error(leaveErrorMessage(e, '消せませんでした')))
            }
            onFillAll={doFillAll}
          />
          <BulkApproveDialog
            open={bulkOpen}
            onOpenChange={setBulkOpen}
            items={view.pending}
            busy={busyCells.size > 0}
            onApprove={doApproveMany}
          />
        </>
      )}
    </section>
  );
}

function ConfirmBadge({ view }: { view: LeaveMonthView }) {
  const c = view.confirmation;
  if (c.confirmed) {
    return (
      <span
        className="rounded-full bg-success-bg px-2.5 py-0.5 text-sm text-success"
        data-testid="confirm-badge"
      >
        ✓ 確定済み{c.last_confirmed_at ? `（${fmtDateTimeShort(c.last_confirmed_at)}）` : ''}
        {c.changed_since_confirm &&
          ' ・ 確定の後に休みを変えました（もう一度確定して通知してください）'}
      </span>
    );
  }
  return (
    <span
      className="rounded-full bg-bg-muted px-2.5 py-0.5 text-sm text-text-secondary"
      data-testid="confirm-badge"
    >
      未確定
      {c.confirmed_staff_count > 0 && `（${c.confirmed_staff_count}/${c.staff_count} 人確定）`}
    </span>
  );
}

/** 必要休日数・勤務すべき時間数・休みの時間帯の訪問・カイポケとの照合の状態。 */
function StatusLine({ view }: { view: LeaveMonthView }) {
  const cmp = view.compare;
  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-text-secondary"
      data-testid="status-line"
    >
      <span>
        必要休日数 <b className="text-text-primary tnum">{view.required_holidays}</b> 日
      </span>
      <span>
        勤務すべき時間数{' '}
        {view.required_hours != null ? (
          <b className="text-text-primary tnum">{fmtNum(view.required_hours)} 時間</b>
        ) : (
          <span className="text-text-muted">未入力（確定のときに入れます）</span>
        )}
      </span>
      {view.visits_on_leave_count > 0 && (
        <span
          className="rounded bg-warning-bg px-2 py-0.5 font-bold text-warning-strong"
          data-testid="visits-on-leave"
        >
          休みの時間帯に担当が付いている訪問 {view.visits_on_leave_count} 件
        </span>
      )}
      <span data-testid="compare-status">
        {cmp.last_compared_at
          ? `カイポケと比べた：${fmtDateTimeShort(cmp.last_compared_at)}`
          : 'まだカイポケと比べていません'}
        {cmp.last_compared_at && cmp.diff_count != null && ` ・ 差分 ${cmp.diff_count} 件`}
        {cmp.changed_since_compare && ' ・ その後に休みを変えました（もう一度比べてください）'}
        {cmp.baseline_pending_count > 0 && ` ・ 照合前の休み ${cmp.baseline_pending_count} 件`}
      </span>
    </div>
  );
}

/** 祝日 (名前) と事業所の休み (名前・全員に公休を入れる)。 */
function DayNotes({
  view,
  onOpenClosedDays,
}: {
  view: LeaveMonthView;
  onOpenClosedDays: () => void;
}) {
  if (view.holidays.length === 0 && view.closed_days.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" data-testid="day-notes">
      {view.holidays.length > 0 && (
        <span className="text-red-600">
          祝日：{view.holidays.map((h) => `${fmtDayJp(h.date)} ${h.name}`).join('・')}
          <span className="ml-1 text-xs text-text-muted">（勤務日のまま）</span>
        </span>
      )}
      {view.closed_days.length > 0 && (
        <span className="text-text-secondary">
          事業所の休み：{view.closed_days.map((c) => `${fmtDayJp(c.date)} ${c.label}`).join('・')}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ml-1"
            onClick={onOpenClosedDays}
          >
            全員に公休を入れる…
          </Button>
        </span>
      )}
    </div>
  );
}

function Legend() {
  const items: { cls: string; label: string }[] = [
    { cls: STAMP_SWATCH.off, label: '公休' },
    { cls: STAMP_SWATCH.am_off, label: '午前休（13:00 から勤務）' },
    { cls: STAMP_SWATCH.pm_off, label: '午後休（12:30 から休み）' },
    { cls: STAMP_SWATCH.paid_off, label: '有給' },
    {
      cls: 'bg-brand-primary-50 ring-2 ring-inset ring-brand-primary',
      label: '申請中（本人から）',
    },
    { cls: 'bg-bg-muted', label: '勤務しない曜日（週の勤務の型）' },
  ];
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border-default px-3 py-2 text-xs text-text-secondary">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <i
            aria-hidden
            className={cn('h-3.5 w-5 rounded-sm border border-border-strong', it.cls)}
          />
          {it.label}
        </span>
      ))}
      <span>右上の「足」= 管理者が入れた休み・「カ」= カイポケから取り込んだ休み</span>
      <span>左上の印: 灰の丸 = 照合前・白丸 = 未送信・赤の「!」= カイポケで要対応</span>
      <span>斜線 = 承認後に取り消し／下の数字 = その日の訪問の件数</span>
    </div>
  );
}

/** 勤務表 (Google スプレッドシート) の一覧と置き場の接続の状態 (§7-2・§7-3)。 */
function DriveCard({ month }: { month: string }) {
  const drive = useGoogleDriveStatus();
  const sheets = useKinmuSheets(month);
  const st = drive.data;
  const text = !st
    ? drive.isError
      ? '接続の状態を読み込めませんでした'
      : '接続の状態を確かめています'
    : st.status === 'connected'
      ? `接続済み${st.folder_name ? `（保存先：${st.folder_name}）` : '（保存先のフォルダが未設定）'}`
      : st.status === 'needs_reconnect'
        ? 'もう一度接続してください'
        : 'まだ接続していません';
  return (
    <Card className="space-y-2 p-3 text-sm" data-testid="drive-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-text-secondary">
          勤務表（Google スプレッドシート）の置き場：<b className="text-text-primary">{text}</b>
        </span>
        <Button asChild variant="outline" size="sm">
          <Link href="/settings/google-drive">
            Google ドライブの設定
            <ExternalLink className="h-4 w-4" />
          </Link>
        </Button>
      </div>
      <div data-testid="kinmu-sheets">
        {sheets.isError ? (
          <p className="text-text-muted">勤務表の一覧を読み込めませんでした</p>
        ) : !sheets.data ? (
          <p className="text-text-muted">勤務表の一覧を読み込んでいます</p>
        ) : sheets.data.length === 0 ? (
          <p className="text-text-muted">
            {fmtMonthJp(month)}の勤務表はまだありません（確定すると予定版を作ります）
          </p>
        ) : (
          <ul className="space-y-1">
            {sheets.data.map((k) => (
              <li key={k.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-text-primary">
                  {k.kind === 'plan' ? '予定版' : '確定版'} 第{k.version}版
                </span>
                <span className="text-xs text-text-muted">
                  {fmtDateTimeShort(k.created_at)}
                  {k.reason ? ` ・ ${k.reason}` : ''}
                </span>
                {k.url && (
                  <a
                    href={k.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-brand-primary underline-offset-2 hover:underline"
                  >
                    開く
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
