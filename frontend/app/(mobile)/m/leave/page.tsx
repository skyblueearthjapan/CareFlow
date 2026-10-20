'use client';

/**
 * /m/leave — スマホの「休み」ページ (leave-and-kinmu-sheet-design §6・モック「スマホの申請画面」).
 *
 * 旧「休み申請」と旧「出勤カレンダー」(/m/shifts → ここへ転送) を 1 つにまとめた。
 *   - 上: 月の切り替え・締め切りの帯 (前の月の 20 日 JST まで) / 確定済みの帯
 *   - 「M月の休み N 日」(数だけ。必要休日数・あと何日・その日何人休むかは出さない)
 *   - 種類 (公休・午前休・午後休・有給) を選んでから日を押す (何日でも) → 「N 日を申請する」
 *   - 申請中の日は取り下げ・承認済みの日は「管理者へ相談」
 *   - 締め切り後・確定後は日を押すと「変更をお願いする」(種類を変えて / 休みを取り消して・理由が必須)
 *   - 下に「申請と結果」
 *
 * 送信は 1 日 = 1 件の pending_request (request_type='staff_off') を逐次 POST し、
 * 失敗した日は選んだまま残す。payload.override_type は DB の値 (off/am_off/pm_off/paid_off、
 * 変更のお願いだけ cancel も) を送る。締め切り・確定の検証はサーバが行い、422 の文言をそのまま見せる。
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';
import { ChevronLeft, ChevronRight, Trash2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/sonner';
import { MobileSection } from '@/components/mobile/MobileSection';
import { cn } from '@/lib/utils';
import {
  AM_OFF_UNTIL,
  firstOpenLeaveMonth,
  isPastLeaveDeadline,
  jstToday,
  LEAVE_REQUEST_DEADLINE_DAY,
  LEAVE_TYPES,
  PM_OFF_FROM,
  type LeaveType,
} from '@/lib/leave';
import {
  useCreatePendingRequest,
  usePendingRequests,
  useWithdrawPendingRequest,
} from '@/lib/queries/pending_requests';
import { useLeaveMonthSummary } from '@/lib/queries/leave';
import { useShiftConfirmations } from '@/lib/queries/staff-shift-confirmations';
import { useStaffOverrides } from '@/lib/queries/staff-overrides';
import { useStaffShifts } from '@/lib/queries/staff-shifts';
import type { PendingRequestV2Read } from '@/lib/schemas/pending_request';
import {
  buildShiftMonth,
  endOfMonth,
  fmtIsoLocal,
  leaveDayWeight,
  startOfMonth,
  summarizeShiftMonth,
  type ShiftDay,
} from '@/lib/shift-calendar';

const WEEKDAYS_JP = ['日', '月', '火', '水', '木', '金', '土'] as const;

/** 種類のボタンの並び (モックの順)。 */
const LEAVE_CHOICES: readonly LeaveType[] = ['off', 'am_off', 'pm_off', 'paid_off'];

/** 変更のお願いで送れる種類 (休みの種類＋休みの取り消し)。 */
type RequestOverrideType = LeaveType | 'cancel';

const TYPE_LABEL: Record<RequestOverrideType, string> = {
  off: '公休',
  am_off: '午前休',
  pm_off: '午後休',
  paid_off: '有給',
  cancel: '休みの取り消し',
};

/** カレンダーのマスに出す短い名前。 */
const TYPE_SHORT: Record<RequestOverrideType, string> = {
  off: '休',
  am_off: '午前',
  pm_off: '午後',
  paid_off: '有給',
  cancel: '取消',
};

/** '13:00' → '13時' / '12:30' → '12時半'。 */
function jpClock(hhmm: string): string {
  const [h = '0', m = '00'] = hhmm.split(':');
  return `${Number(h)}時${m === '30' ? '半' : m === '00' ? '' : `${Number(m)}分`}`;
}

const TYPE_SUB: Record<LeaveType, string> = {
  off: '終日',
  am_off: `${jpClock(AM_OFF_UNTIL)}から勤務`,
  pm_off: `${jpClock(PM_OFF_FROM)}から休み`,
  paid_off: '有給休暇',
};

/** 'YYYY-MM-DD' → '12/3（木）'。 */
function fmtJp(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}（${WEEKDAYS_JP[d.getDay()]}）`;
}

/** 'YYYY-MM-DD' (confirmed_at など ISO 日時も可) → 'M/D'。 */
function fmtMd(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 申請の payload (編集して承認されたなら edited_payload)。 */
function payloadOf(r: PendingRequestV2Read): Record<string, unknown> {
  return (r.edited_payload ?? r.payload ?? {}) as Record<string, unknown>;
}

/** 申請の種類。知らない値・未指定は公休 (旧い申請は 'off' 固定だった)。 */
function requestTypeOf(r: PendingRequestV2Read): RequestOverrideType {
  const v = payloadOf(r).override_type;
  if (v === 'cancel') return 'cancel';
  return typeof v === 'string' && LEAVE_TYPES.has(v) ? (v as LeaveType) : 'off';
}

function isChangeRequest(r: PendingRequestV2Read): boolean {
  return payloadOf(r).kind === 'change';
}

function noteOf(r: PendingRequestV2Read): string {
  const v = payloadOf(r).note;
  return typeof v === 'string' ? v : '';
}

/** その日の休みの登録の種類 (時間変更・出勤なら null)。 */
function leaveOf(day: ShiftDay | undefined): LeaveType | null {
  if (!day) return null;
  return LEAVE_TYPES.has(day.kind) ? (day.kind as LeaveType) : null;
}

type Sheet =
  | { kind: 'send' }
  | { kind: 'withdraw'; request: PendingRequestV2Read }
  | { kind: 'approved'; date: string; type: LeaveType }
  | { kind: 'change'; date: string };

export default function MobileLeavePage() {
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? null;

  const todayIso = jstToday();
  // 開いたときは締め切りがまだ来ていない最初の月 (JST の 20 日までなら来月、21 日からは再来月)。
  const [month, setMonth] = useState<Date>(() => {
    const [y = 0, m = 1] = firstOpenLeaveMonth().split('-').map(Number);
    return new Date(y, m - 1, 1);
  });
  const monthStart = fmtIsoLocal(startOfMonth(month));
  const monthEnd = fmtIsoLocal(endOfMonth(month));
  const monthNum = month.getMonth() + 1;

  const [chip, setChip] = useState<LeaveType>('off');
  const [selected, setSelected] = useState<Map<string, LeaveType>>(() => new Map());
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [memo, setMemo] = useState('');
  const [changeType, setChangeType] = useState<RequestOverrideType>('off');
  const [changeReason, setChangeReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const shiftsQuery = useStaffShifts(staffId);
  const overridesQuery = useStaffOverrides(staffId, { from: monthStart, to: monthEnd });
  const confirmationsQuery = useShiftConfirmations(staffId, { from: monthStart, to: monthStart });
  // 祝日 (振替休日・国民の休日を含む) は月の軽い情報から。祝日も出勤の日のまま (日付を赤くするだけ)
  const monthInfoQuery = useLeaveMonthSummary(monthStart.slice(0, 7));
  const holidayByDate = useMemo(
    () => new Map((monthInfoQuery.data?.holidays ?? []).map((h) => [h.date, h.name])),
    [monthInfoQuery.data],
  );
  // 本人のその月の staff_off (staff の GET はサーバ側で自分軸に絞り込み済み)
  const requestsQuery = usePendingRequests({
    request_type: 'staff_off',
    target_staff_id: staffId ?? undefined,
    target_date_from: monthStart,
    target_date_to: monthEnd,
    limit: 100,
  });

  const createMut = useCreatePendingRequest();
  const withdrawMut = useWithdrawPendingRequest();

  const days = useMemo(
    () =>
      buildShiftMonth({
        month,
        shifts: shiftsQuery.data?.shifts ?? [],
        overrides: overridesQuery.data ?? [],
      }),
    [month, shiftsQuery.data, overridesQuery.data],
  );
  const dayByDate = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);

  const requests = useMemo(
    () => (requestsQuery.data?.items ?? []).filter((r) => r.target_date),
    [requestsQuery.data],
  );
  /** 日ごとの代表の申請: 申請中を優先し、なければいちばん新しいもの。 */
  const requestByDate = useMemo(() => {
    const ordered = [...requests].sort((a, b) => {
      const pa = a.status === 'pending' ? 0 : 1;
      const pb = b.status === 'pending' ? 0 : 1;
      if (pa !== pb) return pa - pb;
      return a.updated_at > b.updated_at ? -1 : 1;
    });
    const m = new Map<string, PendingRequestV2Read>();
    for (const r of ordered) if (!m.has(r.target_date!)) m.set(r.target_date!, r);
    return m;
  }, [requests]);

  const confirmation = confirmationsQuery.data?.[0] ?? null;
  const deadlinePassed = isPastLeaveDeadline(monthStart);
  const changeMode = confirmation !== null || deadlinePassed;
  const deadlineDate = new Date(
    month.getFullYear(),
    month.getMonth() - 1,
    LEAVE_REQUEST_DEADLINE_DAY,
  );
  const deadlineLabel = `${deadlineDate.getMonth() + 1}/${deadlineDate.getDate()}（${
    WEEKDAYS_JP[deadlineDate.getDay()]
  }）`;

  const summary = useMemo(() => summarizeShiftMonth(days), [days]);
  // 申請中の増分 = その種類の数 − その日のいまの数 (勤務しない曜日はもともと休みなので 0)。
  const pendingGain = useMemo(() => {
    let n = 0;
    for (const r of requests) {
      if (r.status !== 'pending' || isChangeRequest(r)) continue;
      const t = requestTypeOf(r);
      const day = dayByDate.get(r.target_date!);
      if (t === 'cancel' || !day || !day.patternWorking) continue;
      const leave = leaveOf(day);
      const now = leave ? leaveDayWeight(leave, true) : 0;
      n += leaveDayWeight(t, true) - now;
    }
    return n;
  }, [requests, dayByDate]);
  const selectedInMonth = [...selected].filter(([iso]) => iso.startsWith(monthStart.slice(0, 7)));
  const selectedGain = selectedInMonth.reduce((n, [iso, t]) => {
    const day = dayByDate.get(iso);
    return n + (day ? leaveDayWeight(t, day.patternWorking) : 0);
  }, 0);

  const sortedSelected = [...selected].sort(([a], [b]) => (a < b ? -1 : 1));

  const changeMonth = (delta: number) => {
    setMonth((m) => new Date(m.getFullYear(), m.getMonth() + delta, 1));
  };

  function handleDayTap(day: ShiftDay) {
    const req = requestByDate.get(day.date);
    const leave = leaveOf(day);
    if (req && req.status === 'pending') {
      setSheet({ kind: 'withdraw', request: req });
      return;
    }
    if (!leave && day.kind === 'nonworking') {
      toast.info(`${fmtJp(day.date)} はもともと勤務しない曜日です`);
      return;
    }
    if (changeMode) {
      // 既定はお願いの選択肢の先頭 (いまの種類を除いた最初の種類)。
      setChangeType(LEAVE_CHOICES.find((t) => t !== leave) ?? 'off');
      setChangeReason('');
      setSheet({ kind: 'change', date: day.date });
      return;
    }
    if (leave) {
      setSheet({ kind: 'approved', date: day.date, type: leave });
      return;
    }
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.get(day.date) === chip) next.delete(day.date);
      else next.set(day.date, chip);
      return next;
    });
  }

  async function handleSubmit() {
    if (!staffId || sortedSelected.length === 0 || submitting) return;
    setSubmitting(true);
    const trimmedNote = memo.trim();
    // 部分失敗時は成功分だけ選択から外し、失敗日は理由つきで選択に残す
    // (D-1 一括登録と同じ再送安全パターン)。逐次実行 = 作成時の重複の止めが
    // 同じまとまりの中の重複も正しく検知できる。
    const failed: { date: string; type: LeaveType; message: string }[] = [];
    for (const [iso, type] of sortedSelected) {
      try {
        await createMut.mutateAsync({
          request_type: 'staff_off',
          target_staff_id: staffId,
          target_date: iso,
          payload: {
            staff_id: staffId,
            date: iso,
            override_type: type,
            ...(trimmedNote ? { note: trimmedNote } : {}),
          },
        });
      } catch (err) {
        failed.push({
          date: iso,
          type,
          message: err instanceof Error ? err.message : String(err),
        });
      }
    }
    const okCount = sortedSelected.length - failed.length;
    const failedDates = new Set(failed.map((f) => f.date));
    // 成功した日だけをいまの選択から外す (選択の全体は上書きしない)
    setSelected((prev) => {
      const next = new Map(prev);
      for (const [iso] of sortedSelected) if (!failedDates.has(iso)) next.delete(iso);
      return next;
    });
    setSheet(null);
    if (failed.length === 0) {
      setMemo('');
      toast.success(`${okCount} 日を申請しました`, {
        description: '管理者が承認すると通知が届きます',
      });
    } else {
      if (okCount > 0) toast.success(`${okCount} 日を申請しました`);
      toast.error(`${failed.length} 日の申請ができませんでした`, {
        description: failed.map((f) => `${fmtJp(f.date)}: ${f.message}`).join(' / '),
      });
    }
    setSubmitting(false);
  }

  async function handleChange(date: string) {
    const reason = changeReason.trim();
    if (!staffId || !reason || submitting) return;
    setSubmitting(true);
    try {
      await createMut.mutateAsync({
        request_type: 'staff_off',
        target_staff_id: staffId,
        target_date: date,
        payload: {
          staff_id: staffId,
          date,
          override_type: changeType,
          kind: 'change',
          note: reason,
        },
      });
      setSheet(null);
      toast.success('管理者へ変更をお願いしました', {
        description: '管理者が確認すると通知が届きます',
      });
    } catch (err) {
      toast.error('お願いを送れませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
    setSubmitting(false);
  }

  async function handleWithdraw(item: PendingRequestV2Read) {
    try {
      await withdrawMut.mutateAsync(item.id);
      toast.success('取り下げました');
    } catch (err) {
      toast.error('取り下げられませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      // 成功でも失敗 (すでに承認された など) でも小窓を閉じ、一覧を読み直す
      setSheet(null);
      void requestsQuery.refetch();
    }
  }

  // カレンダーの先頭の空き (日曜はじまり)。
  const leadingBlanks = startOfMonth(month).getDay();

  // 申請と結果: その月の申請を日付順に。承認済みなのに休みが無い日は「承認後に取り消し」。
  const resultItems = useMemo(
    () =>
      [...requests].sort((a, b) =>
        a.target_date === b.target_date
          ? a.created_at < b.created_at
            ? -1
            : 1
          : a.target_date! < b.target_date!
            ? -1
            : 1,
      ),
    [requests],
  );

  function resultBadge(r: PendingRequestV2Read) {
    if (r.status === 'pending') return <Badge variant="warning">申請中</Badge>;
    if (r.status === 'rejected') return <Badge variant="destructive">却下</Badge>;
    const t = requestTypeOf(r);
    const now = leaveOf(dayByDate.get(r.target_date!));
    if (overridesQuery.data !== undefined && t !== 'cancel') {
      if (!now) return <Badge variant="secondary">承認後に取り消し</Badge>;
      // 管理者が種類を変えて承認した (または後で変えた) ときは、いまの種類を出す
      if (now !== t) return <Badge variant="success">{TYPE_LABEL[now]}で承認</Badge>;
    }
    return <Badge variant="success">承認</Badge>;
  }

  return (
    <MobileSection pose="calendar" title="休み" subtitle="月のカレンダーを見て、休みを申請できます">
      {!staffId && (
        <Alert variant="destructive">
          <AlertTitle>申請できません</AlertTitle>
          <AlertDescription>
            スタッフIDが紐付いていないアカウントです。管理者にご連絡ください。
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-center justify-between">
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="前の月"
          onClick={() => changeMonth(-1)}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <p className="font-serif text-lg font-bold text-text-primary tnum">
          {month.getFullYear()}年{monthNum}月
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
      </div>

      {confirmation ? (
        <div role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          ✓ {monthNum}月は確定しました（{fmtMd(confirmation.confirmed_at)}
          ）。変えたいときは日を押して「変更をお願いする」から。
        </div>
      ) : deadlinePassed ? (
        <div
          role="status"
          className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          {monthNum}月の休みの希望の締め切り（{deadlineLabel}
          ）を過ぎました。日を押すと「変更をお願いする」になります。
        </div>
      ) : (
        <div
          role="status"
          className="rounded-lg border border-brand-primary-light bg-brand-primary-50 px-3 py-2 text-sm text-brand-primary"
        >
          {monthNum}月の休みの希望は <b>{deadlineLabel}まで</b>。そのあと管理者が組んで確定します。
        </div>
      )}

      <Card data-testid="leave-count" className="px-3 py-2 text-sm text-text-primary">
        {monthNum}月の休み <b className="tnum text-base">{summary.holidays}</b> 日
        {pendingGain > 0 && (
          <span className="text-text-secondary">（申請中 {pendingGain} 日）</span>
        )}
        {selectedGain > 0 && (
          <span className="text-text-secondary">・選択中 {selectedGain} 日</span>
        )}
      </Card>

      {changeMode ? (
        <p className="text-xs text-text-muted">日を押すと「変更をお願いする」になります</p>
      ) : (
        <>
          <p className="text-xs text-text-muted">① 種類を選ぶ　② 日を押す（何日でも）</p>
          <div className="grid grid-cols-4 gap-1.5" role="group" aria-label="休みの種類">
            {LEAVE_CHOICES.map((t) => (
              <button
                key={t}
                type="button"
                aria-pressed={chip === t}
                onClick={() => setChip(t)}
                className={cn(
                  'flex min-h-[52px] flex-col items-center justify-center gap-0.5 rounded-lg border px-1 text-sm font-bold',
                  chip === t
                    ? t === 'paid_off'
                      ? 'border-emerald-600 bg-emerald-600 text-white'
                      : 'border-brand-primary bg-brand-primary text-white'
                    : 'border-border-default bg-bg-base text-text-primary',
                )}
              >
                {TYPE_LABEL[t]}
                <span
                  className={cn(
                    'text-xs font-normal',
                    chip === t ? 'text-white' : 'text-text-muted',
                  )}
                >
                  {TYPE_SUB[t]}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      <Card className="p-2">
        <div className="grid grid-cols-7 gap-1">
          {WEEKDAYS_JP.map((w) => (
            <div key={w} className="text-center text-xs text-text-muted">
              {w}
            </div>
          ))}
          {Array.from({ length: leadingBlanks }, (_, i) => (
            <div key={`blank-${i}`} />
          ))}
          {days.map((day) => (
            <DayCell
              key={day.date}
              day={day}
              selectedType={selected.get(day.date) ?? null}
              request={requestByDate.get(day.date) ?? null}
              holiday={holidayByDate.get(day.date) ?? null}
              past={day.date < todayIso}
              onTap={() => handleDayTap(day)}
            />
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border-default px-1 pt-2 text-xs text-text-secondary">
          <Legend className="bg-brand-primary-light" label="休み" />
          <Legend className="bg-bg-base" half="top" label="午前休" />
          <Legend className="bg-bg-base" half="bottom" label="午後休" />
          <Legend className="bg-emerald-100" label="有給" />
          <Legend className="ring-2 ring-inset ring-brand-primary" label="申請中" />
          <Legend className="bg-bg-muted" label="勤務しない曜日" />
        </div>
      </Card>

      {!changeMode && (
        <Button
          type="button"
          size="lg"
          className="w-full"
          disabled={!staffId || sortedSelected.length === 0 || submitting}
          onClick={() => setSheet({ kind: 'send' })}
        >
          {sortedSelected.length > 0
            ? `${sortedSelected.length} 日を申請する`
            : '日を選ぶと申請できます'}
        </Button>
      )}

      <Card className="p-4">
        <h2 className="font-serif text-base font-bold text-text-primary">申請と結果</h2>
        {resultItems.length === 0 ? (
          <p className="mt-2 text-sm text-text-muted">{monthNum}月の申請はまだありません</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {resultItems.map((r) => {
              const t = requestTypeOf(r);
              const change = isChangeRequest(r);
              const meta =
                r.status === 'rejected' && r.rejection_reason
                  ? `理由：${r.rejection_reason}`
                  : noteOf(r);
              return (
                <li
                  key={r.id}
                  className="flex items-center justify-between gap-2 rounded-md border border-border-default bg-bg-base p-2"
                >
                  <div className="min-w-0">
                    <p className="text-sm text-text-primary">
                      <b>{fmtJp(r.target_date!)}</b>{' '}
                      {change
                        ? t === 'cancel'
                          ? '休みの取り消しのお願い'
                          : `${TYPE_LABEL[t]}への変更のお願い`
                        : TYPE_LABEL[t]}
                    </p>
                    {meta && <p className="truncate text-xs text-text-muted">{meta}</p>}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {resultBadge(r)}
                    {r.status === 'pending' && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={`取り下げる（${fmtJp(r.target_date!)}）`}
                        onClick={() => setSheet({ kind: 'withdraw', request: r })}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Dialog
        open={sheet !== null}
        onOpenChange={(open) => {
          // 送信中は閉じさせない (結果が出てから閉じる)
          if (!open && !submitting) setSheet(null);
        }}
      >
        <DialogContent>
          {sheet?.kind === 'send' && (
            <>
              <DialogHeader>
                <DialogTitle>{sortedSelected.length} 日を申請します</DialogTitle>
                <DialogDescription>管理者が承認すると通知が届きます。</DialogDescription>
              </DialogHeader>
              <ul className="space-y-1">
                {sortedSelected.map(([iso, t]) => (
                  <li key={iso} className="flex justify-between text-sm">
                    <b>{fmtJp(iso)}</b>
                    <span>{TYPE_LABEL[t]}</span>
                  </li>
                ))}
              </ul>
              <div className="space-y-1">
                <Label htmlFor="leave-memo" className="text-sm">
                  メモ（任意・管理者に届きます）
                </Label>
                <Textarea
                  id="leave-memo"
                  value={memo}
                  onChange={(e) => setMemo(e.target.value)}
                  maxLength={200}
                  rows={2}
                  placeholder="例：通院のため"
                />
              </div>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setSheet(null)}
                  disabled={submitting}
                >
                  戻る
                </Button>
                <Button type="button" onClick={() => void handleSubmit()} disabled={submitting}>
                  {submitting ? '申請中…' : '申請する'}
                </Button>
              </DialogFooter>
            </>
          )}

          {sheet?.kind === 'withdraw' && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {fmtJp(sheet.request.target_date!)}{' '}
                  {isChangeRequest(sheet.request)
                    ? '変更のお願い'
                    : TYPE_LABEL[requestTypeOf(sheet.request)]}
                  （申請中）
                </DialogTitle>
                <DialogDescription>
                  管理者がまだ見ていません。取り下げると申請は消えます。
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setSheet(null)}>
                  閉じる
                </Button>
                <Button
                  type="button"
                  onClick={() => void handleWithdraw(sheet.request)}
                  disabled={withdrawMut.isPending}
                >
                  取り下げる
                </Button>
              </DialogFooter>
            </>
          )}

          {sheet?.kind === 'approved' && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {fmtJp(sheet.date)} {TYPE_LABEL[sheet.type]}（承認済み）
                </DialogTitle>
                <DialogDescription>
                  承認された休みです。変えたいときは管理者へ相談してください。
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setSheet(null)}>
                  閉じる
                </Button>
              </DialogFooter>
            </>
          )}

          {sheet?.kind === 'change' && (
            <ChangeSheet
              date={sheet.date}
              monthNum={monthNum}
              confirmed={confirmation !== null}
              current={leaveOf(dayByDate.get(sheet.date))}
              value={changeType}
              onValueChange={setChangeType}
              reason={changeReason}
              onReasonChange={setChangeReason}
              submitting={submitting}
              onCancel={() => setSheet(null)}
              onSubmit={() => void handleChange(sheet.date)}
            />
          )}
        </DialogContent>
      </Dialog>
    </MobileSection>
  );
}

function Legend({
  className,
  label,
  half,
}: {
  className: string;
  label: string;
  /** 半休の見本 (上半分 / 下半分を塗る)。 */
  half?: 'top' | 'bottom';
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <span
        className={cn(
          'relative inline-block h-3 w-3 overflow-hidden rounded-sm border border-border-default',
          className,
        )}
      >
        {half && (
          <span
            className={cn(
              'absolute inset-x-0 h-1/2 bg-brand-primary-light',
              half === 'top' ? 'top-0' : 'bottom-0',
            )}
          />
        )}
      </span>
      {label}
    </span>
  );
}

interface DayCellProps {
  day: ShiftDay;
  selectedType: LeaveType | null;
  request: PendingRequestV2Read | null;
  /** 祝日の名前 (祝日でなければ null)。日付を赤くするだけで、出勤の日のまま。 */
  holiday: string | null;
  past: boolean;
  onTap: () => void;
}

/** カレンダーの 1 日。選択中 > 休みの登録 > 勤務しない曜日 の順に塗り、申請中は枠、却下は ✕。 */
function DayCell({ day, selectedType, request, holiday, past, onTap }: DayCellProps) {
  const leave = leaveOf(day);
  const d = new Date(day.date + 'T00:00:00');
  const dow = d.getDay();
  const pending = request?.status === 'pending' ? request : null;
  const rejected = !leave && request?.status === 'rejected' ? request : null;

  let label = '';
  let status = '';
  if (selectedType) {
    label = TYPE_SHORT[selectedType];
    status = '選択中';
  } else if (leave) {
    label = TYPE_SHORT[leave];
    status = `${TYPE_LABEL[leave]}（承認済み）`;
  } else if (day.kind === 'custom') {
    label = '時間';
    status = '時間変更';
  } else if (day.kind === 'nonworking') {
    // マスは灰色だけ (「休」は出さない)。読み上げのラベルは残す。
    status = '勤務しない曜日';
  }
  let sub = '';
  if (pending) {
    const t = requestTypeOf(pending);
    if (!selectedType && !leave) label = TYPE_SHORT[t];
    sub = '申請中';
    status = `${status ? `${status}・` : ''}${TYPE_LABEL[t]}を申請中`;
  } else if (rejected && !selectedType) {
    label = `✕${TYPE_SHORT[requestTypeOf(rejected)]}`;
    sub = '却下';
    status = '却下';
  }

  return (
    <button
      type="button"
      disabled={past}
      onClick={onTap}
      aria-label={`${fmtJp(day.date)}${holiday ? ` ${holiday}` : ''}${status ? ` ${status}` : ''}`}
      data-holiday={holiday ?? undefined}
      aria-pressed={selectedType !== null}
      className={cn(
        'relative h-16 overflow-hidden rounded-md border border-border-default bg-bg-base text-center',
        !selectedType && leave === 'off' && 'border-transparent bg-brand-primary-light',
        !selectedType && leave === 'paid_off' && 'border-transparent bg-emerald-100',
        !selectedType && !leave && day.kind === 'nonworking' && 'bg-bg-muted text-text-muted',
        selectedType &&
          (selectedType === 'paid_off'
            ? 'border-emerald-600 bg-emerald-600 text-white'
            : 'border-brand-primary bg-brand-primary text-white'),
        pending && 'ring-2 ring-inset ring-brand-primary',
        past && 'opacity-40',
      )}
    >
      {!selectedType && (leave === 'am_off' || leave === 'pm_off') && (
        <span
          aria-hidden
          className={cn(
            'absolute inset-x-0 h-1/2 bg-brand-primary-light',
            leave === 'am_off' ? 'top-0' : 'bottom-0',
          )}
        />
      )}
      <span
        className={cn(
          'absolute left-1 top-0.5 text-sm font-medium',
          !selectedType && (dow === 0 || holiday) && 'text-red-600',
          !selectedType && dow === 6 && !holiday && 'text-blue-600',
        )}
      >
        {d.getDate()}
      </span>
      <span className="absolute inset-x-0 bottom-0.5 flex flex-col text-xs font-bold leading-tight">
        <span className={cn(!selectedType && rejected && 'text-red-600')}>{label}</span>
        {sub && <span className="font-normal">{sub}</span>}
      </span>
    </button>
  );
}

interface ChangeSheetProps {
  date: string;
  monthNum: number;
  confirmed: boolean;
  /** その日のいまの休みの種類 (無ければ null)。 */
  current: LeaveType | null;
  value: RequestOverrideType;
  onValueChange: (v: RequestOverrideType) => void;
  reason: string;
  onReasonChange: (v: string) => void;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: () => void;
}

/** 締め切り後・確定後の「変更をお願いする」(種類を変えて / 休みを取り消して・理由が必須)。 */
function ChangeSheet({
  date,
  monthNum,
  confirmed,
  current,
  value,
  onValueChange,
  reason,
  onReasonChange,
  submitting,
  onCancel,
  onSubmit,
}: ChangeSheetProps) {
  const options: RequestOverrideType[] = current
    ? [...LEAVE_CHOICES.filter((t) => t !== current), 'cancel']
    : [...LEAVE_CHOICES];
  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {fmtJp(date)}
          {current ? ` ${TYPE_LABEL[current]}` : ''}
        </DialogTitle>
        <DialogDescription>
          {confirmed
            ? `${monthNum}月は確定済みです。`
            : `${monthNum}月の休みの希望の締め切りを過ぎています。`}
          管理者へ変更をお願いします。
        </DialogDescription>
      </DialogHeader>
      <RadioGroup
        aria-label="お願いの中身"
        value={value}
        onValueChange={(v) => onValueChange(v as RequestOverrideType)}
        className="gap-1.5"
      >
        {options.map((t) => (
          <Label
            key={t}
            htmlFor={`change-${t}`}
            className={cn(
              'flex w-full cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm font-normal',
              value === t
                ? 'border-brand-primary bg-brand-primary-50 font-bold text-brand-primary'
                : 'border-border-default bg-bg-base text-text-primary',
            )}
          >
            <RadioGroupItem id={`change-${t}`} value={t} />
            {t === 'cancel'
              ? '休みを取り消してほしい（出勤したい）'
              : current
                ? `「${TYPE_LABEL[t]}」に変えてほしい`
                : `この日を「${TYPE_LABEL[t]}」にしてほしい`}
          </Label>
        ))}
      </RadioGroup>
      <div className="space-y-1">
        <Label htmlFor="change-reason" className="text-sm">
          理由（必須・管理者に届きます）
        </Label>
        <Textarea
          id="change-reason"
          value={reason}
          onChange={(e) => onReasonChange(e.target.value)}
          maxLength={200}
          rows={2}
          placeholder="例：急な通院のため"
        />
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
          やめる
        </Button>
        <Button type="button" onClick={onSubmit} disabled={submitting || reason.trim() === ''}>
          変更をお願いする
        </Button>
      </DialogFooter>
    </>
  );
}
