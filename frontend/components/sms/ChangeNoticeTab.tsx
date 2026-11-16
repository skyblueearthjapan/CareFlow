'use client';

/**
 * 予定のお知らせ — 「変更のお知らせ」のタブ (設計 §5-2・決まり §2〜§4)。
 *
 * - お伝えした内容と今の予定が違う方 (お知らせ待ち) を並べる。人が確かめてから送る。
 * - 今日・明日の変更は「急ぎ」。お電話でもお伝えするよう番号を出す。
 * - 送らずに済ませる (お電話でお伝えした／お知らせしない) こともできる。
 * - 送るときに渡すのは、患者様と週と件数と見た内容の印 (token) だけ。本文はサーバーが作り直す。
 * - 確かめを開いた時点の中身を控え、送るのは控えだけ。確かめているあいだに一覧が新しくなったら閉じる。
 * - 「済みにする」は、フォームを開いた時点の変更の印 (change_token) を返す (決まり §12)。
 *   手段と理由は決まった組だけ (お電話 ↔ お電話でお伝えした／お知らせしない ↔ お申し出・その他・§13-6)。
 * - 窓口の設定が足りない (sendBlocked) ときは送れない。済みにすることはできる。
 */
import { useEffect, useRef, useState } from 'react';
import { Loader2, Phone, Send } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useResolveChange, useSendChangeNotices, useSmsPendingChanges } from '@/lib/queries/sms';
import type { ResolveChannel, ResolveReason, SmsPendingItem } from '@/lib/schemas/sms';

import { SendConfirmPanel } from './SendConfirmPanel';
import {
  SMS_LIST_REFRESHED_MESSAGE,
  SMS_SEND_UNKNOWN_MESSAGE,
  formatWaiting,
  formatYen,
  smsNotSendableLabel,
} from './smsLabels';

export interface ChangeNoticeTabProps {
  active: boolean;
  /** 窓口の設定が足りないなど、いまは送れないとき true。 */
  sendBlocked?: boolean;
}

const itemKey = (it: SmsPendingItem) => `${it.patient_id}:${it.week_start}`;

/** 1 名に送る件数 (送り先の数)。 */
const messageCount = (it: SmsPendingItem) => it.destinations.length;

const CHANNEL_OPTIONS: ReadonlyArray<{ value: ResolveChannel; label: string }> = [
  { value: 'phone', label: 'お電話でお伝えした' },
  { value: 'none', label: 'お知らせしない' },
];

const REASON_OPTIONS: ReadonlyArray<{ value: ResolveReason; label: string }> = [
  { value: 'phoned', label: 'お電話でお伝えした' },
  { value: 'patient_request', label: 'ご本人・ご家族からのお申し出' },
  { value: 'other', label: 'その他' },
];

/** 手段ごとに選べる理由 (決まり §13-6・合わない組はサーバーが 422)。先頭が既定。 */
const RESOLVE_REASONS_BY_CHANNEL: Record<ResolveChannel, readonly ResolveReason[]> = {
  phone: ['phoned'],
  none: ['patient_request', 'other'],
};

const NOTE_MAX = 200;

interface Totals {
  people: number;
  messages: number;
  segments: number;
  yen: number;
}

/** 確かめを開いた時点の控え。送るのはこの中身だけ。 */
interface SendSnapshot {
  items: Array<{ patient_id: string; week_start: string; token: string }>;
  totals: Totals;
  dataUpdatedAt: number;
}

export function ChangeNoticeTab({ active, sendBlocked = false }: ChangeNoticeTabProps) {
  const pending = useSmsPendingChanges(active);
  const send = useSendChangeNotices();
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [snapshot, setSnapshot] = useState<SendSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [resolvingKey, setResolvingKey] = useState<string | null>(null);
  const sendingRef = useRef(false);

  const data = pending.data;
  const items = data?.items ?? [];
  // 既定では印を付けない (変更は 1 名ずつ確かめてから)。新しく出てきた方にも付かない。
  const selected = items.filter((it) => it.sendable && checked[itemKey(it)]);
  const totals: Totals = {
    people: selected.length,
    messages: selected.reduce((s, it) => s + messageCount(it), 0),
    segments: selected.reduce((s, it) => s + (it.segments_est ?? 0) * messageCount(it), 0),
    yen: selected.reduce((s, it) => s + (it.est_yen ?? 0), 0),
  };
  const withinHours = data?.within_send_hours ?? false;
  const confirming = snapshot !== null;
  const canSend =
    !!data &&
    !sendBlocked &&
    withinHours &&
    totals.messages > 0 &&
    !send.isPending &&
    !pending.isFetching;

  // 確かめているあいだに一覧が新しくなったら、控えを捨てて確かめ直してもらう。
  useEffect(() => {
    if (!snapshot || sendingRef.current) return;
    if (pending.dataUpdatedAt !== snapshot.dataUpdatedAt) {
      setSnapshot(null);
      setError(SMS_LIST_REFRESHED_MESSAGE);
    }
  }, [pending.dataUpdatedAt, snapshot]);

  const openConfirm = () => {
    if (!canSend) return;
    setError(null);
    setDone(null);
    setSnapshot({
      items: selected.map((it) => ({
        patient_id: it.patient_id,
        week_start: it.week_start,
        token: it.token ?? '',
      })),
      totals,
      dataUpdatedAt: pending.dataUpdatedAt,
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
        items: snap.items,
        expected_count: snap.totals.messages,
      });
      const msg = `${res.patient_count} 名への変更のお知らせ（${res.message_count} 件）を受け付けました`;
      setDone(msg);
      toast.success(msg);
      setChecked({});
    } catch (e) {
      let msg: string;
      if (!(e instanceof ApiError) || e.status >= 500) {
        // サーバーは送ったかもしれない。印を外して一覧を読み込む。
        msg = SMS_SEND_UNKNOWN_MESSAGE;
        setChecked({});
      } else if (e.status === 409) {
        msg = `${apiErrorMessage(e)}（一覧を開き直しました。もう一度確かめてください）`;
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

  if (pending.isLoading) {
    return (
      <p className="py-8 text-center text-sm text-text-secondary">
        <Loader2 className="mr-1 inline h-4 w-4 animate-spin" aria-hidden />
        読み込んでいます…
      </p>
    );
  }
  if (pending.isError || !data) {
    return (
      <div className="space-y-2 py-6 text-center">
        <p className="text-sm text-red-700">
          一覧を読み込めませんでした: {apiErrorMessage(pending.error)}
        </p>
        <Button type="button" variant="outline" size="sm" onClick={() => void pending.refetch()}>
          もう一度読み込む
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="sms-change-tab">
      <p className="text-sm text-text-secondary">
        お知らせしたあとに予定が変わった方です。文面を確かめて、送る方に印を付けてください。
        お電話でお伝えしたときは［送らずに済みにする］を押してください。
      </p>

      {error ? (
        <p
          role="alert"
          className="rounded border border-red-200 bg-red-50 p-2 text-sm text-red-800"
          data-testid="sms-change-send-error"
        >
          {error}
        </p>
      ) : null}
      {done ? (
        <p
          role="status"
          className="rounded border border-emerald-200 bg-emerald-50 p-2 text-sm text-emerald-900"
          data-testid="sms-change-send-done"
        >
          {done}
        </p>
      ) : null}

      {items.length === 0 ? (
        <p className="rounded border border-border-default p-4 text-sm text-text-secondary">
          お知らせ待ちの方はいません。
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((it) => {
            const key = itemKey(it);
            const isChecked = it.sendable && !!checked[key];
            return (
              <li
                key={key}
                className={
                  'rounded-md border p-3 ' +
                  (it.urgent ? 'border-red-300 bg-red-50/40' : 'border-border-default')
                }
                data-testid={`sms-change-row-${it.patient_id}`}
              >
                <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
                  <label
                    className={
                      'flex min-w-[12rem] items-center gap-2 py-1.5 ' +
                      (it.sendable ? 'cursor-pointer' : 'cursor-not-allowed')
                    }
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-brand-primary"
                      checked={isChecked}
                      disabled={!it.sendable || send.isPending}
                      onChange={() => {
                        setChecked((prev) => ({ ...prev, [key]: !prev[key] }));
                        setSnapshot(null);
                      }}
                      aria-label={`${it.patient_name} 様に送る`}
                    />
                    <span className="text-sm font-bold text-text-primary">
                      {it.patient_name} 様
                    </span>
                  </label>
                  {it.urgent ? (
                    <span className="rounded-full border border-red-300 bg-red-100 px-2 py-0.5 text-xs font-bold text-red-800">
                      急ぎ（今日・明日）
                    </span>
                  ) : null}
                  <span className="text-sm text-text-secondary">
                    気づいてから {formatWaiting(it.waiting_minutes)}
                  </span>
                  {it.sendable ? (
                    <span className="ml-auto text-sm text-text-secondary tnum">
                      {it.char_count ?? 0} 文字・{it.segments_est ?? 0} 通分
                      {messageCount(it) > 1 ? ` × ${messageCount(it)} 件` : ''}・
                      {formatYen(it.est_yen ?? 0)}
                    </span>
                  ) : null}
                </div>

                <ul className="mt-1 flex flex-wrap gap-2 text-sm text-text-primary">
                  {it.changes.map((c, i) => (
                    <li key={i} className="rounded bg-bg-muted px-2 py-0.5">
                      {c.label}
                    </li>
                  ))}
                </ul>

                {it.sendable ? (
                  <>
                    <div className="mt-1 text-sm text-text-secondary">
                      送り先:{' '}
                      {it.destinations
                        .map((d) => `${d.relation_label}（末尾 ${d.phone_last4}）`)
                        .join('、')}
                    </div>
                    {it.body ? (
                      <pre className="mt-2 whitespace-pre-wrap break-words rounded border border-border-default bg-bg-muted/50 p-2 font-mono text-sm leading-relaxed text-text-primary">
                        {it.body}
                      </pre>
                    ) : null}
                  </>
                ) : (
                  <p className="mt-1 text-sm font-bold text-amber-900">
                    SMS でお知らせできません:{' '}
                    {smsNotSendableLabel(it.not_sendable_reason, it.not_sendable_label)}
                  </p>
                )}

                {it.urgent && it.call_numbers.length > 0 ? (
                  <div
                    className="mt-2 rounded border border-red-200 bg-bg-base p-2 text-sm"
                    data-testid={`sms-change-call-${it.patient_id}`}
                  >
                    <p className="font-bold text-red-800">
                      <Phone className="mr-1 inline h-4 w-4" aria-hidden />
                      お電話でもお伝えください
                    </p>
                    <ul className="mt-1 flex flex-wrap gap-x-4">
                      {it.call_numbers.map((n, i) => (
                        <li key={i} className="tnum">
                          {n.relation_label}: {n.phone_display}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <div className="mt-2 flex justify-end">
                  {resolvingKey === key ? null : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setResolvingKey(key)}
                      data-testid={`sms-change-resolve-open-${it.patient_id}`}
                    >
                      送らずに済みにする
                    </Button>
                  )}
                </div>
                {resolvingKey === key ? (
                  <ResolveForm item={it} onDone={() => setResolvingKey(null)} />
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      <div className="sticky bottom-0 space-y-2 border-t border-border-default bg-bg-base pt-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-text-primary tnum" data-testid="sms-change-totals">
            送る方 <b>{totals.people}</b> 名・<b>{totals.messages}</b> 件（{totals.segments}{' '}
            通分）・見込み <b>{formatYen(totals.yen)}</b>
          </p>
          <div className="ml-auto flex items-center gap-2">
            {!withinHours ? (
              <span className="text-sm text-amber-900" data-testid="sms-change-hours-reason">
                お知らせを送れるのは {data.send_hours}（日本時間）です
              </span>
            ) : null}
            <Button
              type="button"
              onClick={openConfirm}
              disabled={!canSend || confirming}
              data-testid="sms-change-send"
            >
              <Send className="mr-1 h-4 w-4" aria-hidden />
              選んだ方に送る
            </Button>
          </div>
        </div>
        {snapshot ? (
          <SendConfirmPanel
            people={snapshot.totals.people}
            messages={snapshot.totals.messages}
            segments={snapshot.totals.segments}
            yen={snapshot.totals.yen}
            pending={send.isPending}
            blocked={pending.isFetching}
            onConfirm={() => void handleSend()}
            onBack={() => setSnapshot(null)}
            testId="sms-change-confirm"
          />
        ) : null}
      </div>
    </div>
  );
}

function ResolveForm({ item, onDone }: { item: SmsPendingItem; onDone: () => void }) {
  const resolve = useResolveChange();
  // フォームを開いた時点の変更の印。あとで一覧が新しくなっても、見た変更だけを「済み」にする。
  const [changeToken] = useState(() => item.change_token ?? '');
  const [channel, setChannel] = useState<ResolveChannel>('phone');
  const [reason, setReason] = useState<ResolveReason>('phoned');
  const allowedReasons = RESOLVE_REASONS_BY_CHANNEL[channel];
  const chooseChannel = (next: ResolveChannel) => {
    setChannel(next);
    // 手段に合わない理由は、その手段の既定に替える。
    const allowed = RESOLVE_REASONS_BY_CHANNEL[next];
    setReason((r) => (allowed.includes(r) ? r : (allowed[0] ?? r)));
  };
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const busyRef = useRef(false);
  const noteRequired = reason === 'other';
  const pairOk = allowedReasons.includes(reason);
  const noteOk = !noteRequired || note.trim().length > 0;

  const handleSave = async () => {
    if (busyRef.current || !noteOk || !pairOk) return;
    busyRef.current = true;
    setError(null);
    try {
      await resolve.mutateAsync({
        patient_id: item.patient_id,
        week_start: item.week_start,
        channel,
        reason,
        note: note.trim(),
        change_token: changeToken,
      });
      toast.success(`${item.patient_name} 様を「お伝え済み」にしました`);
      onDone();
    } catch (e) {
      let msg: string;
      if (!(e instanceof ApiError) || e.status >= 500) {
        msg = '済みにできたかどうか分かりません。一覧が新しくなったら確かめてください';
      } else if (e.status === 409) {
        msg = `${apiErrorMessage(e)}（一覧を開き直しました。もう一度確かめてください）`;
      } else {
        msg = apiErrorMessage(e, '済みにできませんでした');
      }
      setError(msg);
      toast.error(msg);
      // 一覧は useResolveChange の onSettled で読み込まれる。
    } finally {
      busyRef.current = false;
    }
  };

  const id = `resolve-${item.patient_id}-${item.week_start}`;
  return (
    <div
      className="mt-2 space-y-3 rounded-md border border-border-default bg-bg-muted/40 p-3"
      data-testid={`sms-change-resolve-${item.patient_id}`}
    >
      <p className="text-sm font-bold text-text-primary">SMS を送らずに「お伝え済み」にします</p>
      <fieldset className="space-y-1">
        <legend className="text-sm text-text-secondary">手段</legend>
        <div className="flex flex-wrap gap-4">
          {CHANNEL_OPTIONS.map((o) => (
            <label key={o.value} className="flex cursor-pointer items-center gap-2 py-1.5 text-sm">
              <input
                type="radio"
                name={`${id}-channel`}
                className="h-4 w-4 accent-brand-primary"
                checked={channel === o.value}
                onChange={() => chooseChannel(o.value)}
              />
              {o.label}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-sm text-text-secondary">理由</legend>
        <div className="flex flex-wrap gap-4">
          {REASON_OPTIONS.filter((o) => allowedReasons.includes(o.value)).map((o) => (
            <label key={o.value} className="flex cursor-pointer items-center gap-2 py-1.5 text-sm">
              <input
                type="radio"
                name={`${id}-reason`}
                className="h-4 w-4 accent-brand-primary"
                checked={reason === o.value}
                onChange={() => setReason(o.value)}
              />
              {o.label}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="space-y-1">
        <label htmlFor={`${id}-note`} className="text-sm text-text-secondary">
          メモ{noteRequired ? '（その他のときは書いてください）' : '（なくてもかまいません）'}
        </label>
        <textarea
          id={`${id}-note`}
          className="w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary"
          rows={2}
          maxLength={NOTE_MAX}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          data-testid={`sms-change-resolve-note-${item.patient_id}`}
        />
      </div>
      {error ? (
        <p role="alert" className="text-sm text-red-800">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onDone}
          disabled={resolve.isPending}
        >
          やめる
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={() => void handleSave()}
          disabled={resolve.isPending || !noteOk || !pairOk}
          data-testid={`sms-change-resolve-save-${item.patient_id}`}
        >
          {resolve.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden /> : null}
          済みにする
        </Button>
      </div>
    </div>
  );
}
