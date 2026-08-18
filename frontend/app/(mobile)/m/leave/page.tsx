'use client';

/**
 * /m/leave — モバイル休み申請 (mobile-leave-request-design.md).
 *
 * カレンダーでお休みしたい日をポチポチ選択 → 「この内容で申請する」で
 * 1日 = 1件の pending_request (staff_off) を作成する。承認は PC の
 * 「モバイル申請履歴」(/admin/pending-requests) — 承認されると
 * staff_weekly_overrides (スタッフマスターの休み) へ自動反映される。
 *
 * カレンダーの3状態:
 *   - 選択中   = ピンク塗り (これから申請する日)
 *   - 申請中   = ピンク輪郭・タップ不可 (pending の staff_off)
 *   - 登録済み = グレー打消し・タップ不可 (staff_weekly_overrides 既存行)
 *
 * payload.override_type は DB 正典の 'off' を送る (applier は raw 文字列を
 * そのまま挿入するため、日本語ラベルを送ってはならない — 設計書 §1-b)。
 */
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { CalendarDays, ChevronRight, Send, Trash2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { MobileSection } from '@/components/mobile/MobileSection';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { cn } from '@/lib/utils';
import {
  useCreatePendingRequest,
  usePendingRequests,
  useWithdrawPendingRequest,
} from '@/lib/queries/pending_requests';
import { useStaffOverrides } from '@/lib/queries/staff-overrides';
import type { PendingRequestV2Read } from '@/lib/schemas/pending_request';

/** 申請できる先の上限 (PC スタッフマスターの表示ホライズンと一致)。 */
const HORIZON_DAYS = 90;

const WEEKDAYS_JP = ['日', '月', '火', '水', '木', '金', '土'] as const;

/** ローカル時刻基準の YYYY-MM-DD (toISOString は UTC ズレするため使わない)。 */
function fmtIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

/** 'YYYY-MM-DD' → '8/24（月）' 表示。 */
function fmtJp(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}（${WEEKDAYS_JP[d.getDay()]}）`;
}

function startOfToday(): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

/** 凡例チップ。 */
function LegendChip({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-text-muted">
      <span className={cn('inline-block h-3 w-3 rounded-full', className)} />
      {label}
    </span>
  );
}

export default function MobileLeavePage() {
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? null;

  const today = useMemo(startOfToday, []);
  const horizon = useMemo(() => {
    const d = new Date(today);
    d.setDate(d.getDate() + HORIZON_DAYS);
    return d;
  }, [today]);

  const [selected, setSelected] = useState<Date[]>([]);
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // 自分の staff_off 申請 (staff の GET はサーバ側で自分軸に絞り込み済み)
  const requestsQuery = usePendingRequests({
    request_type: 'staff_off',
    target_staff_id: staffId ?? undefined,
    limit: 100,
  });
  // 登録済みの休み・時間変更 (承認済み分を含む) — GET は本人可
  const overridesQuery = useStaffOverrides(staffId, {
    from: fmtIso(today),
    to: fmtIso(horizon),
  });

  const items = useMemo(() => requestsQuery.data?.items ?? [], [requestsQuery.data]);
  const pendingItems = useMemo(
    () =>
      items
        .filter((i) => i.status === 'pending' && i.target_date)
        .sort((a, b) => (a.target_date! < b.target_date! ? -1 : 1)),
    [items],
  );
  const recentResults = useMemo(
    () =>
      items
        .filter((i) => i.status === 'approved' || i.status === 'rejected')
        .sort((a, b) => (a.updated_at > b.updated_at ? -1 : 1))
        .slice(0, 5),
    [items],
  );

  const pendingDates = useMemo(
    () => new Set(pendingItems.map((i) => i.target_date!)),
    [pendingItems],
  );
  const registeredDates = useMemo(
    () => new Set((overridesQuery.data ?? []).map((o) => o.date)),
    [overridesQuery.data],
  );

  const createMut = useCreatePendingRequest();
  const withdrawMut = useWithdrawPendingRequest();

  const sortedSelected = useMemo(
    () => [...selected].sort((a, b) => a.getTime() - b.getTime()),
    [selected],
  );

  async function handleSubmit() {
    if (!staffId || sortedSelected.length === 0 || submitting) return;
    setSubmitting(true);
    const trimmedNote = note.trim();
    // 部分失敗時は成功分だけ選択から外し、失敗日は理由つきで選択に残す
    // (D-1 一括登録と同じ再送安全パターン)。逐次実行 = 作成時重複ガードが
    // 同一バッチ内の重複も正しく検知できる。
    const failed: { date: Date; message: string }[] = [];
    for (const day of sortedSelected) {
      const iso = fmtIso(day);
      try {
        await createMut.mutateAsync({
          request_type: 'staff_off',
          target_staff_id: staffId,
          target_date: iso,
          payload: {
            staff_id: staffId,
            date: iso,
            override_type: 'off',
            ...(trimmedNote ? { note: trimmedNote } : {}),
          },
        });
      } catch (err) {
        failed.push({
          date: day,
          message: err instanceof Error ? err.message : String(err),
        });
      }
    }
    const okCount = sortedSelected.length - failed.length;
    setSelected(failed.map((f) => f.date));
    if (failed.length === 0) {
      setNote('');
      toast.success(`${okCount}日分の休みを申請しました`, {
        description: '管理者が確認するとスケジュールに反映されます',
      });
    } else {
      if (okCount > 0) {
        toast.success(`${okCount}日分の休みを申請しました`);
      }
      toast.error(`${failed.length}日分の申請に失敗しました`, {
        description: failed.map((f) => `${fmtJp(fmtIso(f.date))}: ${f.message}`).join(' / '),
      });
    }
    setSubmitting(false);
  }

  async function handleWithdraw(item: PendingRequestV2Read) {
    const label = item.target_date ? fmtJp(item.target_date) : 'この';
    if (!window.confirm(`${label} の休み申請を取り下げますか？`)) return;
    try {
      await withdrawMut.mutateAsync(item.id);
      toast.success('申請を取り下げました');
    } catch (err) {
      toast.error('取り下げに失敗しました', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return (
    <MobileSection
      pose="calendar"
      title="休み申請"
      subtitle="お休みしたい日をタップして選んでください"
    >
      {!staffId && (
        <Alert variant="destructive">
          <AlertTitle>申請できません</AlertTitle>
          <AlertDescription>
            スタッフIDが紐付いていないアカウントです。管理者にご連絡ください。
          </AlertDescription>
        </Alert>
      )}

      <Card className="p-2">
        <Calendar
          mode="multiple"
          selected={selected}
          onSelect={(days) => setSelected(days ?? [])}
          disabled={[
            { before: today },
            { after: horizon },
            (d: Date) => pendingDates.has(fmtIso(d)) || registeredDates.has(fmtIso(d)),
          ]}
          fromMonth={today}
          toDate={horizon}
          modifiers={{
            requested: (d: Date) => pendingDates.has(fmtIso(d)),
            registered: (d: Date) => registeredDates.has(fmtIso(d)),
          }}
          modifiersClassNames={{
            requested: 'ring-2 ring-inset ring-brand-primary text-brand-primary !opacity-100',
            registered: 'bg-bg-muted text-text-muted line-through',
          }}
          classNames={{
            // タップターゲットを 44px に拡大 (既定は h-9 w-9 = 36px)
            day: cn(
              buttonVariants({ variant: 'ghost' }),
              'h-11 w-11 p-0 font-normal aria-selected:opacity-100',
            ),
            head_cell: 'text-text-muted rounded-md w-11 font-normal text-[0.8rem]',
          }}
          className="mx-auto w-fit"
        />
        <div className="flex flex-wrap justify-center gap-3 border-t border-border-default px-2 py-2">
          <LegendChip className="bg-brand-primary" label="選択中" />
          <LegendChip className="ring-2 ring-inset ring-brand-primary" label="申請中" />
          <LegendChip className="bg-bg-muted line-through" label="登録済み" />
        </div>
      </Card>

      <Card className="p-4">
        <div className="space-y-2">
          <Label htmlFor="leave-note" className="text-xs">
            備考（任意・選んだ日すべてに付きます）
          </Label>
          <input
            id="leave-note"
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={200}
            placeholder="例: 通院のため"
            className="w-full rounded-md border border-border-default bg-bg-base p-2 text-sm text-text-primary placeholder:text-text-muted"
          />
        </div>
        {sortedSelected.length > 0 && (
          <p className="mt-3 text-sm text-text-secondary">
            選んだ日:{' '}
            <span className="font-medium text-text-primary">
              {sortedSelected.map((d) => fmtJp(fmtIso(d))).join('・')}
            </span>
          </p>
        )}
        <Button
          type="button"
          size="lg"
          onClick={handleSubmit}
          disabled={!staffId || sortedSelected.length === 0 || submitting}
          className="mt-3 w-full"
        >
          <Send className="mr-1 h-4 w-4" />
          {submitting
            ? '申請中…'
            : sortedSelected.length > 0
              ? `この内容で申請する（${sortedSelected.length}日）`
              : '日付を選んでください'}
        </Button>
      </Card>

      <Card className="p-4">
        <h2 className="font-serif text-base font-bold text-text-primary">申請中の休み</h2>
        <p className="mt-1 text-xs text-text-muted">
          管理者の確認待ちです。承認されるとスケジュールに反映されます。
        </p>
        {requestsQuery.isLoading && (
          <div className="mt-2 space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        )}
        {!requestsQuery.isLoading && pendingItems.length === 0 && (
          <RakusukeNote
            pose="joy"
            size="sm"
            className="mt-2"
            title="申請中の休みはありません"
            comment="カレンダーから申請すると、ここに並びます"
          />
        )}
        {pendingItems.length > 0 && (
          <ul className="mt-2 space-y-2">
            {pendingItems.map((item) => (
              <li
                key={item.id}
                className="flex items-center justify-between gap-2 rounded-md border border-border-default bg-bg-base p-2"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-text-primary">
                    {item.target_date ? fmtJp(item.target_date) : '--'}
                  </p>
                  {typeof item.payload?.note === 'string' && item.payload.note && (
                    <p className="truncate text-xs text-text-muted">{item.payload.note}</p>
                  )}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void handleWithdraw(item)}
                  disabled={withdrawMut.isPending}
                >
                  <Trash2 className="mr-1 h-3.5 w-3.5" />
                  取り下げ
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Link
        href="/m/shifts"
        className="flex items-center justify-between rounded-lg border border-border-default bg-bg-base p-4 transition-colors hover:bg-bg-muted"
      >
        <div className="flex items-center gap-3">
          <CalendarDays className="h-5 w-5 text-brand-primary" />
          <div>
            <p className="font-medium text-text-primary">出勤カレンダーを見る</p>
            <p className="text-xs text-text-muted">1か月の出勤日とお休み・確定状況</p>
          </div>
        </div>
        <ChevronRight className="h-4 w-4 text-text-muted" />
      </Link>

      {recentResults.length > 0 && (
        <Card className="p-4">
          <h2 className="font-serif text-base font-bold text-text-primary">最近の結果</h2>
          <ul className="mt-2 space-y-2">
            {recentResults.map((item) => (
              <li
                key={item.id}
                className="rounded-md border border-border-default bg-bg-base p-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-text-primary">
                    {item.target_date ? fmtJp(item.target_date) : '--'}
                  </span>
                  <Badge
                    variant={item.status === 'approved' ? 'success' : 'destructive'}
                    className="text-[10px]"
                  >
                    {item.status === 'approved' ? '承認されました' : '却下'}
                  </Badge>
                </div>
                {item.status === 'rejected' && item.rejection_reason && (
                  <p className="mt-1 text-xs text-text-muted">理由: {item.rejection_reason}</p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </MobileSection>
  );
}
