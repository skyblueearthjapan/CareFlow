'use client';

/**
 * 予定のお知らせ — 「週のお知らせ」のタブ (設計 §5-1・決まり §12)。
 *
 * - 一覧はサーバーが作る (preview)。1 行 = 患者様 1 名。既定で全員に印、「送信済み」の方だけ外す。
 * - 「送っている途中」の方は選べず、送る中身にも入れない。「前回は届いていません」の方は既定で印を付け、
 *   お電話でお伝えする方の印刷にも載せる (決まり §13-1)。
 * - 確かめ (SendConfirmPanel) を開いた時点の「だれに・何件・見た内容の印」を控え、送るのは控えだけ。
 *   確かめているあいだに一覧が新しくなったら、確かめを閉じて、もう一度確かめてもらう。
 * - 送るときに渡すのは、ID と件数 (expected_count) と見た内容の印 (tokens) だけ。本文はサーバーが作り直す。
 * - 送れる時間の外では押せない。409 はサーバーの言葉を出して一覧を読み込む。
 * - 通信が切れた・5xx は「送れたかどうか分かりません」。印の選び方を既定に戻して一覧を読み込む。
 * - 窓口の設定が足りない (sendBlocked) ときは送れない (決まり §13-6)。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, Printer, Send } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useSendWeekNotices, useWeekNoticePreview } from '@/lib/queries/sms';
import type { SmsStatus, WeekNoticeRow } from '@/lib/schemas/sms';

import { SendConfirmPanel } from './SendConfirmPanel';
import { openCallListPrint } from './printCallList';
import {
  SMS_LIST_REFRESHED_MESSAGE,
  SMS_ROW_STATE_LABEL,
  SMS_SEND_UNKNOWN_MESSAGE,
  formatHm,
  formatJstDateTime,
  formatYen,
  formatYmdShort,
  smsNotSendableLabel,
} from './smsLabels';

export interface WeekNoticeTabProps {
  weekStart: string;
  active: boolean;
  /** 窓口の設定が足りないなど、いまは送れないとき true。 */
  sendBlocked?: boolean;
  /** 試験の状態・模擬の窓口を確かめの欄に出すため。 */
  status?: SmsStatus;
}

/** 印を付けられる行か。送っている途中の方は選べない (知らない状態もここに入る)。 */
function selectable(row: WeekNoticeRow): boolean {
  return row.state !== 'sending';
}

function defaultChecked(row: WeekNoticeRow): boolean {
  return selectable(row) && row.state !== 'already_sent_same';
}

const visitsText = (visits: ReadonlyArray<{ date: string; start: string }>) =>
  visits.map((v) => `${formatYmdShort(v.date)}${formatHm(v.start)}`).join('、');

function stateChipClass(state: WeekNoticeRow['state']): string {
  switch (state) {
    case 'already_sent_same':
      return 'border-border-default bg-bg-muted text-text-secondary';
    case 'sending':
      return 'border-sky-300 bg-sky-50 text-sky-900';
    case 'not_delivered':
      return 'border-red-300 bg-red-50 font-bold text-red-800';
    default:
      return 'border-amber-300 bg-amber-50 text-amber-900';
  }
}

interface Totals {
  people: number;
  messages: number;
  segments: number;
  yen: number;
}

/** 確かめを開いた時点の控え。送るのはこの中身だけ。 */
interface SendSnapshot {
  weekStart: string;
  patientIds: string[];
  tokens: Record<string, string>;
  totals: Totals;
  resendCount: number;
  notDeliveredCount: number;
  dataUpdatedAt: number;
}

export function WeekNoticeTab({
  weekStart,
  active,
  sendBlocked = false,
  status,
}: WeekNoticeTabProps) {
  const preview = useWeekNoticePreview(weekStart, active);
  const send = useSendWeekNotices();
  // 人が触った印だけを持つ (触っていない行は既定のまま)。
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  // 人が初めて印を触ったときに見えていた方。これより後に出てきた方には印を付けない。
  const [knownIds, setKnownIds] = useState<ReadonlySet<string> | null>(null);
  const [snapshot, setSnapshot] = useState<SendSnapshot | null>(null);
  const [notSendableOpen, setNotSendableOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const sendingRef = useRef(false);

  const data = preview.data;
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const isChecked = (r: WeekNoticeRow) => {
    if (!selectable(r)) return false;
    const o = overrides[r.patient_id];
    if (o !== undefined) return o;
    if (knownIds && !knownIds.has(r.patient_id)) return false;
    return defaultChecked(r);
  };
  const selected = rows.filter(isChecked);
  const totals: Totals = {
    people: selected.length,
    messages: selected.reduce((s, r) => s + r.message_count, 0),
    segments: selected.reduce((s, r) => s + r.segments_est * r.message_count, 0),
    yen: selected.reduce((s, r) => s + r.est_yen, 0),
  };
  const withinHours = data?.within_send_hours ?? false;
  const confirming = snapshot !== null;
  const canSend =
    !!data &&
    !sendBlocked &&
    withinHours &&
    totals.messages > 0 &&
    !send.isPending &&
    !preview.isFetching;

  // 確かめているあいだに一覧が新しくなったら、控えを捨てて確かめ直してもらう。
  useEffect(() => {
    if (!snapshot || sendingRef.current) return;
    if (preview.dataUpdatedAt !== snapshot.dataUpdatedAt) {
      setSnapshot(null);
      setError(SMS_LIST_REFRESHED_MESSAGE);
    }
  }, [preview.dataUpdatedAt, snapshot]);

  const freezeKnown = () => {
    setKnownIds((prev) => prev ?? new Set(rows.map((r) => r.patient_id)));
  };

  const toggle = (r: WeekNoticeRow) => {
    if (!selectable(r)) return;
    freezeKnown();
    setOverrides((prev) => ({ ...prev, [r.patient_id]: !isChecked(r) }));
    setSnapshot(null);
  };

  const openConfirm = () => {
    if (!data || !canSend) return;
    setError(null);
    setDone(null);
    const tokens: Record<string, string> = {};
    for (const r of selected) if (r.token) tokens[r.patient_id] = r.token;
    setSnapshot({
      weekStart: data.week_start,
      patientIds: selected.map((r) => r.patient_id),
      tokens,
      totals,
      resendCount: selected.filter((r) => r.state === 'already_sent_same').length,
      notDeliveredCount: selected.filter((r) => r.state === 'not_delivered').length,
      dataUpdatedAt: preview.dataUpdatedAt,
    });
  };

  const handleSend = async () => {
    const snap = snapshot;
    if (!snap || sendingRef.current) return;
    sendingRef.current = true;
    setError(null);
    setDone(null);
    try {
      const res = await send.mutateAsync({
        week_start: snap.weekStart,
        patient_ids: snap.patientIds,
        expected_count: snap.totals.messages,
        resend: snap.resendCount > 0,
        tokens: snap.tokens,
      });
      const msg = `${res.patient_count} 名へのお知らせ（${res.message_count} 件）を受け付けました`;
      setDone(msg);
      toast.success(msg);
      setOverrides({});
      setKnownIds(null);
    } catch (e) {
      let msg: string;
      if (!(e instanceof ApiError) || e.status >= 500) {
        // サーバーは送ったかもしれない。既定の印 (送信済みは外す) に戻して一覧を読み込む。
        msg = SMS_SEND_UNKNOWN_MESSAGE;
        setOverrides({});
        setKnownIds(null);
      } else if (e.status === 409) {
        msg = `${apiErrorMessage(e)}（一覧を開き直しました。もう一度確かめてください）`;
        freezeKnown();
      } else {
        msg = apiErrorMessage(e, '送れませんでした');
      }
      setError(msg);
      toast.error(msg);
      // 一覧は送った後に読み込まれる (useSend* の onSettled で ['sms'] を無効にする)。
    } finally {
      setSnapshot(null);
      sendingRef.current = false;
    }
  };

  const notDeliveredRows = rows.filter((r) => r.state === 'not_delivered');
  const callRowCount = (data?.not_sendable.length ?? 0) + notDeliveredRows.length;
  // week_label は文面用の「来週の」「12/1からの週の」なので、画面では末尾の「の」を外す。
  const weekName = (data?.week_label ?? '').replace(/の$/, '');

  const handlePrint = () => {
    if (!data) return;
    const ok = openCallListPrint(`お電話でお伝えする方（${weekName}）`, [
      ...data.not_sendable.map((r) => ({
        name: r.patient_name,
        reason: smsNotSendableLabel(r.reason, r.reason_label),
        visits: visitsText(r.visits),
      })),
      ...notDeliveredRows.map((r) => ({
        name: r.patient_name,
        reason: '前回のお知らせが届いていません',
        visits: visitsText(r.visits),
      })),
    ]);
    if (!ok) toast.warning('印刷の窓を開けませんでした。ポップアップのブロックを解除してください');
  };

  if (preview.isLoading) {
    return (
      <p className="py-8 text-center text-sm text-text-secondary">
        <Loader2 className="mr-1 inline h-4 w-4 animate-spin" aria-hidden />
        一覧を作っています…
      </p>
    );
  }
  if (preview.isError || !data) {
    return (
      <div className="space-y-2 py-6 text-center">
        <p className="text-sm text-red-700" data-testid="sms-week-error">
          一覧を読み込めませんでした: {apiErrorMessage(preview.error)}
        </p>
        <Button type="button" variant="outline" size="sm" onClick={() => void preview.refetch()}>
          もう一度読み込む
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3" data-testid="sms-week-tab">
      {error ? (
        <p
          role="alert"
          className="rounded border border-red-200 bg-red-50 p-2 text-sm text-red-800"
          data-testid="sms-week-send-error"
        >
          {error}
        </p>
      ) : null}
      {done ? (
        <p
          role="status"
          className="rounded border border-emerald-200 bg-emerald-50 p-2 text-sm text-emerald-900"
          data-testid="sms-week-send-done"
        >
          {done}
        </p>
      ) : null}

      <div className="min-h-[8rem] flex-1 space-y-3 overflow-y-auto" data-testid="sms-week-body">
        <p className="text-sm text-text-secondary">
          {weekName}の訪問予定を、患者様（またはご家族）へ SMS でお知らせします。
          送る方に印を付けて、文面を確かめてください。
        </p>

        {rows.length === 0 ? (
          <p className="rounded border border-border-default p-4 text-sm text-text-secondary">
            SMS でお知らせできる方はいません。
          </p>
        ) : (
          <ul className="space-y-2" data-testid="sms-week-rows">
            {rows.map((r) => {
              const checked = isChecked(r);
              const canPick = selectable(r);
              const stateLabel = SMS_ROW_STATE_LABEL[r.state];
              return (
                <li
                  key={r.patient_id}
                  className={
                    'rounded-md border p-3 ' +
                    (checked
                      ? 'border-brand-primary/60 bg-bg-base'
                      : 'border-border-default bg-bg-muted/40')
                  }
                  data-testid={`sms-week-row-${r.patient_id}`}
                >
                  <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
                    <label
                      className={
                        'flex min-w-[12rem] items-center gap-2 py-1.5 ' +
                        (canPick ? 'cursor-pointer' : 'cursor-not-allowed')
                      }
                    >
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-brand-primary"
                        checked={checked}
                        onChange={() => toggle(r)}
                        disabled={!canPick || send.isPending}
                        aria-label={`${r.patient_name} 様に送る`}
                      />
                      <span className="text-sm font-bold text-text-primary">
                        {r.patient_name} 様
                      </span>
                    </label>
                    {stateLabel ? (
                      <span
                        className={
                          'rounded-full border px-2 py-0.5 text-xs ' + stateChipClass(r.state)
                        }
                        data-testid={`sms-week-state-${r.patient_id}`}
                      >
                        {stateLabel}
                      </span>
                    ) : null}
                    {r.state === 'not_delivered' && r.last_attempt ? (
                      <span
                        className="text-sm text-red-800"
                        data-testid={`sms-week-last-attempt-${r.patient_id}`}
                      >
                        {r.last_attempt.label || '届いていません'}（
                        {formatJstDateTime(r.last_attempt.at)}）
                      </span>
                    ) : null}
                    <div className="text-sm text-text-secondary">
                      送り先:{' '}
                      {r.destinations
                        .map((d) => `${d.relation_label}（末尾 ${d.phone_last4}）`)
                        .join('、')}
                    </div>
                    <div className="ml-auto text-sm text-text-secondary tnum">
                      {r.char_count} 文字・{r.segments_est} 通分
                      {r.message_count > 1 ? ` × ${r.message_count} 件` : ''}・
                      {formatYen(r.est_yen)}
                    </div>
                  </div>
                  {r.state === 'sending' ? (
                    <p
                      className="mt-1 text-xs text-sky-900"
                      data-testid={`sms-week-sending-note-${r.patient_id}`}
                    >
                      同じ番号へ続けて送るときは、10 分ほどあけて送ります。しばらくお待ちください
                    </p>
                  ) : null}
                  {r.surname_uncertain ? (
                    <p
                      className="mt-1 text-xs text-text-secondary"
                      data-testid={`sms-week-surname-note-${r.patient_id}`}
                    >
                      お名前に空白が無いため、姓の代わりにお名前全体を入れています
                    </p>
                  ) : null}
                  <pre
                    className="mt-2 whitespace-pre-wrap break-words rounded border border-border-default bg-bg-muted/50 p-2 font-mono text-sm leading-relaxed text-text-primary"
                    data-testid={`sms-week-body-${r.patient_id}`}
                  >
                    {r.body}
                  </pre>
                </li>
              );
            })}
          </ul>
        )}

        {data.not_sendable.length > 0 ? (
          <section
            className="rounded-md border border-border-default"
            data-testid="sms-week-not-sendable"
          >
            <div className="flex items-center justify-between gap-2 p-2">
              <button
                type="button"
                className="flex items-center gap-1 text-sm font-bold text-text-primary"
                onClick={() => setNotSendableOpen((o) => !o)}
                aria-expanded={notSendableOpen}
              >
                {notSendableOpen ? (
                  <ChevronDown className="h-4 w-4" aria-hidden />
                ) : (
                  <ChevronRight className="h-4 w-4" aria-hidden />
                )}
                SMS でお知らせできない方（{data.not_sendable.length} 名）
              </button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handlePrint}
                data-testid="sms-week-print"
              >
                <Printer className="mr-1 h-4 w-4" aria-hidden />
                お電話でお伝えする方を印刷
              </Button>
            </div>
            {notSendableOpen ? (
              <ul className="divide-y divide-border-default border-t border-border-default">
                {data.not_sendable.map((r) => (
                  <li key={r.patient_id} className="flex flex-wrap gap-x-4 px-3 py-2 text-sm">
                    <span className="font-bold text-text-primary">{r.patient_name} 様</span>
                    <span className="text-amber-900">
                      {smsNotSendableLabel(r.reason, r.reason_label)}
                    </span>
                    <span className="text-text-secondary">{visitsText(r.visits)}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        ) : callRowCount > 0 ? (
          <div className="flex justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handlePrint}
              data-testid="sms-week-print"
            >
              <Printer className="mr-1 h-4 w-4" aria-hidden />
              お電話でお伝えする方を印刷
            </Button>
          </div>
        ) : null}
      </div>

      <div
        className="shrink-0 space-y-2 border-t border-border-default bg-bg-base pt-3"
        data-testid="sms-week-footer"
      >
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-text-primary tnum" data-testid="sms-week-totals">
            送る方 <b>{totals.people}</b> 名・<b>{totals.messages}</b> 件（{totals.segments}{' '}
            通分）・見込み <b>{formatYen(totals.yen)}</b>
          </p>
          <div className="ml-auto flex items-center gap-2">
            {!withinHours ? (
              <span className="text-sm text-amber-900" data-testid="sms-week-hours-reason">
                お知らせを送れるのは {data.send_hours}（日本時間）です
              </span>
            ) : null}
            <Button
              type="button"
              onClick={openConfirm}
              disabled={!canSend || confirming}
              data-testid="sms-week-send"
            >
              <Send className="mr-1 h-4 w-4" aria-hidden />
              {totals.people} 名に送る
            </Button>
          </div>
        </div>
        {snapshot ? (
          <SendConfirmPanel
            people={snapshot.totals.people}
            messages={snapshot.totals.messages}
            segments={snapshot.totals.segments}
            yen={snapshot.totals.yen}
            resendCount={snapshot.resendCount}
            notDeliveredCount={snapshot.notDeliveredCount}
            pending={send.isPending}
            blocked={preview.isFetching}
            status={status}
            onConfirm={() => void handleSend()}
            onBack={() => setSnapshot(null)}
            testId="sms-week-confirm"
          />
        ) : null}
      </div>
    </div>
  );
}
