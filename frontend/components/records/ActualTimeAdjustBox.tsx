'use client';

/**
 * 「実績の時刻を合わせる」枠 — 打刻履歴の詳細と訪問モニターの詳細パネルで共通の部品
 * （設計 `pc-actual-time-edit-design-2026-10-06.md` §3・モック A〜C）。
 *
 * * 到着の実績がある訪問: 到着・退出を 1 行ずつ、別々に保存する。調整したあとは
 *   「読取時刻に戻す」（手で入れた時刻は「手入力の時刻を消す」）。
 * * 退出の読み取りが無い訪問: 「退出を HH:MM で記録する」。ひと押しの候補は 2 つ
 *   （予定の終わり／到着＋予定の長さ = 最初に選ぶ・PO 決定 Q2）。
 * * 打刻なし（到着の実績が無い）の訪問: 管理者だけ「到着・退出を手で入れる」（D2）。
 *
 * どちらの入口で合わせても API（`PUT` / `DELETE /visits/{id}/actual-time`）・記録・印・
 * 監査ログは同じ。合わせられるか（`adjustAllowed` / `manualArrivalAllowed`）と時刻の範囲は
 * BE が決め、422 / 403 / 409 の `detail` はそのまま出す。保存後は打刻履歴・モニターなどの
 * クエリを取り直す（`useAdjustVisitActualTime` が無効化する）。
 *
 * 文言は「合わせる」「調整」「手入力」「記録する」（PO 決定）。理由は尋ねない。
 */

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { jstHm } from '@/lib/format/actualTime';
import { cn } from '@/lib/utils';
import {
  useAdjustVisitActualTime,
  useResetVisitActualTime,
  type ActualTimeKind,
} from '@/lib/queries/visit-history';

import {
  DEFAULT_DEPARTURE_CANDIDATE,
  defaultDepartureTime,
  departureCandidates,
  type ActualTimeTarget,
} from './actualTimeAdjust';

const KIND_LABEL: Record<ActualTimeKind, string> = { arrival: '到着', departure: '退出' };

/** 無効でも title（理由）が出るようにする。既定の Button は無効時にポインタを切る。 */
const disabledHintCls = 'disabled:pointer-events-auto disabled:cursor-not-allowed';

const HHMM = /^\d{2}:\d{2}$/;

interface ActualTimeAdjustBoxProps {
  target: ActualTimeTarget;
  /** 合わせられないときに添える理由。 */
  naReason: string;
  /** 合わせた / 戻した直後に呼ぶ。 */
  onAdjusted?: (visitId: string) => void;
  /** data-testid の接頭辞（打刻履歴 = `history-adjust`・モニター = `monitor-adjust`）。 */
  testIdPrefix?: string;
}

/**
 * 到着の実績があれば「実績の時刻を合わせる」枠、無ければ（管理者だけ）「到着・退出を
 * 手で入れる」枠。どちらも出せない訪問は何も出さない。
 */
export function ActualTimeAdjustBox(props: ActualTimeAdjustBoxProps) {
  const { target } = props;
  if (target.arrivalAt) return <AdjustBox {...props} />;
  if (target.manualArrivalAllowed) return <ManualEntryBox {...props} />;
  return null;
}

function sideOf(t: ActualTimeTarget, kind: ActualTimeKind) {
  const arrival = kind === 'arrival';
  const at = jstHm(arrival ? t.arrivalAt : t.departureAt);
  const adjusted = arrival ? t.arrivalAdjusted : t.departureAdjusted;
  const manual = arrival ? t.arrivalManual : t.departureManual;
  // 読取時刻の項目が無い応答（古い BE）は、調整が無ければ実績 = 読取。
  const readAt =
    jstHm(arrival ? t.arrivalReadAt : t.departureReadAt) ?? (adjusted || manual ? null : at);
  return { at, readAt, adjusted, manual };
}

/**
 * 「未訪問」の記録がある訪問の注意書き。到着が無いうちは「入れると訪問した扱い」、
 * 到着が入った後は履歴として出す (PO 決定 2026-10-07)。
 */
function NoShowNote({
  target,
  testId,
  arrived,
}: {
  target: ActualTimeTarget;
  testId: string;
  arrived: boolean;
}) {
  if (!target.hasNoShow) return null;
  const reason = target.noShowReason ? `（理由: ${target.noShowReason}）` : '';
  return (
    <p
      className="mb-2 rounded-md border border-border-warning bg-warning-bg px-3 py-2 text-xs text-warning-strong"
      data-testid={testId}
    >
      {arrived
        ? `この訪問には未訪問の記録があります${reason}。到着が入っているため、訪問した扱いです。`
        : `この訪問には未訪問の記録があります${reason}。到着を入れると訪問した扱いになります。`}
    </p>
  );
}

function ErrorNote({ error, testId }: { error: string | null; testId: string }) {
  if (!error) return null;
  return (
    <p
      role="alert"
      className="mt-2 whitespace-pre-wrap rounded-md border border-border-error bg-error-bg px-3 py-2 text-sm text-error"
      data-testid={testId}
    >
      {error}
    </p>
  );
}

function AdjustBox({
  target,
  naReason,
  onAdjusted,
  testIdPrefix = 'history-adjust',
}: ActualTimeAdjustBoxProps) {
  const adjust = useAdjustVisitActualTime();
  const reset = useResetVisitActualTime();
  const [error, setError] = useState<string | null>(null);
  const allowed = target.adjustAllowed === true;
  const pending = adjust.isPending || reset.isPending;

  const save = async (kind: ActualTimeKind, time: string, recordNew: boolean) => {
    setError(null);
    try {
      await adjust.mutateAsync({ visitId: target.visitId, kind, time });
      onAdjusted?.(target.visitId);
      toast.success(
        recordNew
          ? `退出を ${time} で記録しました`
          : `${KIND_LABEL[kind]}を ${time} に合わせました`,
      );
    } catch (e) {
      // 範囲外（422）・期間外（403）・記録なし（409）の理由は BE の文言をそのまま出す。
      setError(apiErrorMessage(e));
    }
  };

  const undo = async (kind: ActualTimeKind, manual: boolean) => {
    setError(null);
    try {
      await reset.mutateAsync({ visitId: target.visitId, kind });
      onAdjusted?.(target.visitId);
      toast.success(
        manual ? `手入力の${KIND_LABEL[kind]}時刻を消しました` : '読取時刻に戻しました',
      );
    } catch (e) {
      setError(apiErrorMessage(e));
    }
  };

  const departureMissing = !target.departureAt;

  return (
    <section
      className="rounded-xl border border-dashed border-brand-primary bg-brand-primary-50 px-4 py-3.5"
      title={allowed ? undefined : naReason}
      data-testid={`${testIdPrefix}-box`}
    >
      <h3 className="text-base font-bold text-text-primary">実績の時刻を合わせる</h3>
      <p className="mb-2 mt-0.5 text-xs text-text-secondary">
        お宅に着いてから QR
        を読み取るまでに時間があったときなどに、実績の時刻を実際に合わせます。読み取った時刻は消さずに残り、予定は変わりません。
      </p>
      <NoShowNote target={target} arrived testId={`${testIdPrefix}-no-show`} />
      {!allowed && (
        <p
          className="mb-2 text-xs font-medium text-text-secondary"
          data-testid={`${testIdPrefix}-na`}
        >
          {naReason}
        </p>
      )}

      <AdjustRow
        target={target}
        kind="arrival"
        allowed={allowed}
        naReason={naReason}
        pending={pending}
        testIdPrefix={testIdPrefix}
        onSave={(kind, time) => save(kind, time, false)}
        onReset={undo}
      />
      {departureMissing ? (
        <DepartureEntryRow
          target={target}
          allowed={allowed}
          naReason={naReason}
          pending={pending}
          testIdPrefix={testIdPrefix}
          onSave={(time) => save('departure', time, true)}
        />
      ) : (
        <AdjustRow
          target={target}
          kind="departure"
          allowed={allowed}
          naReason={naReason}
          pending={pending}
          testIdPrefix={testIdPrefix}
          onSave={(kind, time) => save(kind, time, false)}
          onReset={undo}
        />
      )}

      <ErrorNote error={error} testId={`${testIdPrefix}-error`} />
    </section>
  );
}

function AdjustRow({
  target,
  kind,
  allowed,
  naReason,
  pending,
  testIdPrefix,
  onSave,
  onReset,
}: {
  target: ActualTimeTarget;
  kind: ActualTimeKind;
  allowed: boolean;
  naReason: string;
  pending: boolean;
  testIdPrefix: string;
  onSave: (kind: ActualTimeKind, time: string) => void;
  onReset: (kind: ActualTimeKind, manual: boolean) => void;
}) {
  const label = KIND_LABEL[kind];
  const { at, readAt, adjusted, manual } = sideOf(target, kind);

  const [time, setTime] = useState(at ?? '');

  const hasAdjustment = adjusted || manual;
  const dirty = time !== (at ?? '');
  const valid = HHMM.test(time);
  const disabledTitle = allowed ? undefined : naReason;

  const submit = () => {
    // 読取時刻と同じ時刻にするのは「読取時刻に戻す」と同じ（調整を残さない）。
    if (adjusted && !manual && readAt && time === readAt) onReset(kind, false);
    else onSave(kind, time);
  };

  return (
    <div
      className="border-t border-brand-primary-light py-2.5"
      data-testid={`${testIdPrefix}-${kind}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-9 shrink-0 text-sm font-bold text-text-primary">{label}</span>
        <Input
          type="time"
          aria-label={`${label}の時刻`}
          className="tnum h-9 w-[124px] px-2"
          value={time}
          disabled={!allowed || pending}
          title={disabledTitle}
          onChange={(e) => setTime(e.target.value)}
        />
        <Button
          type="button"
          size="sm"
          className={`h-9 ${disabledHintCls}`}
          disabled={!allowed || pending || !valid || !dirty}
          title={disabledTitle}
          onClick={submit}
          data-testid={`${testIdPrefix}-save-${kind}`}
        >
          {label}を保存
        </Button>
        {hasAdjustment && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={`h-9 ${disabledHintCls}`}
            disabled={!allowed || pending}
            title={disabledTitle}
            onClick={() => onReset(kind, manual)}
            data-testid={`${testIdPrefix}-reset-${kind}`}
          >
            {manual ? '手入力の時刻を消す' : '読取時刻に戻す'}
          </Button>
        )}
      </div>
      <p
        className="tnum mt-1 pl-11 text-xs text-text-secondary"
        data-testid={`${testIdPrefix}-note-${kind}`}
      >
        {readAt
          ? `読取 ${readAt}${adjusted ? ' ・ 調整あり' : ''}`
          : manual
            ? '読み取りなし ・ 手入力の時刻'
            : '調整あり'}
      </p>
    </div>
  );
}

/** 退出の読み取りが無い訪問の退出行: ひと押しの候補（2 つ）と「退出を HH:MM で記録する」。 */
function DepartureEntryRow({
  target,
  allowed,
  naReason,
  pending,
  testIdPrefix,
  onSave,
}: {
  target: ActualTimeTarget;
  allowed: boolean;
  naReason: string;
  pending: boolean;
  testIdPrefix: string;
  onSave: (time: string) => void;
}) {
  const arrival = jstHm(target.arrivalAt);
  const cands = departureCandidates(arrival, target.plannedStart, target.plannedEnd);
  const [time, setTime] = useState(() =>
    defaultDepartureTime(arrival, target.plannedStart, target.plannedEnd),
  );
  const valid = HHMM.test(time);
  const disabledTitle = allowed ? undefined : naReason;

  return (
    <div
      className="border-t border-brand-primary-light py-2.5"
      data-testid={`${testIdPrefix}-departure`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-9 shrink-0 text-sm font-bold text-text-primary">退出</span>
        <Input
          type="time"
          aria-label="退出の時刻"
          className="tnum h-9 w-[124px] px-2"
          value={time}
          disabled={!allowed || pending}
          title={disabledTitle}
          onChange={(e) => setTime(e.target.value)}
        />
        <Button
          type="button"
          size="sm"
          className={`h-9 ${disabledHintCls}`}
          disabled={!allowed || pending || !valid}
          title={disabledTitle}
          onClick={() => onSave(time)}
          data-testid={`${testIdPrefix}-save-departure`}
        >
          {valid ? `退出を ${time} で記録する` : '退出を記録する'}
        </Button>
      </div>
      <p
        className="tnum mt-1 pl-11 text-xs text-text-secondary"
        data-testid={`${testIdPrefix}-note-departure`}
      >
        読み取りなし ・ 退出の時刻を記録できます（記録すると「完了」になります）
      </p>
      {cands.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-11">
          <span className="text-xs text-text-secondary">ひと押しで入れる</span>
          {cands.map((c) => {
            const on = c.time === time;
            return (
              <button
                key={c.key}
                type="button"
                aria-pressed={on}
                disabled={!allowed || pending}
                title={disabledTitle}
                onClick={() => setTime(c.time)}
                data-testid={`${testIdPrefix}-cand-${c.key}`}
                data-default={c.key === DEFAULT_DEPARTURE_CANDIDATE || undefined}
                className={cn(
                  'tnum inline-flex h-8 items-center gap-1 rounded-full border px-3 text-xs',
                  disabledHintCls,
                  'disabled:opacity-60',
                  on
                    ? 'border-brand-primary bg-brand-primary font-bold text-white'
                    : 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted',
                )}
              >
                {c.label} <b>{c.time}</b>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * 「到着・退出を手で入れる」（管理者だけ・D2）。到着の QR も読んでいない訪問に、紙や
 * 看護記録の時刻を入れる。到着だけ入れて保存すると「実施中」（過去の日なら「退出未記録」）。
 */
function ManualEntryBox({
  target,
  onAdjusted,
  testIdPrefix = 'history-adjust',
}: ActualTimeAdjustBoxProps) {
  const adjust = useAdjustVisitActualTime();
  // 退出だけ読み取ってある訪問は、到着だけを入れる (読んだ退出はそのまま)。
  const departureRead = jstHm(target.departureAt);
  // 入力は空から (PO 決定 2026-10-07)。予定の時刻は横に出し、「予定の時刻を入れる」で入れる。
  const [arrival, setArrival] = useState('');
  const [departure, setDeparture] = useState('');
  const canFillPlanned = !!target.plannedStart;
  const fillPlanned = () => {
    if (target.plannedStart) setArrival(target.plannedStart);
    if (!departureRead && target.plannedEnd) setDeparture(target.plannedEnd);
  };
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const pending = saving || adjust.isPending;
  const arrivalValid = HHMM.test(arrival);
  const departureValid = departure === '' || HHMM.test(departure);

  const submit = async () => {
    setError(null);
    const end = departure || departureRead;
    if (end && end <= arrival) {
      setError('退出は到着より後の時刻にしてください');
      return;
    }
    setSaving(true);
    try {
      await adjust.mutateAsync({ visitId: target.visitId, kind: 'arrival', time: arrival });
    } catch (e) {
      setError(apiErrorMessage(e));
      setSaving(false);
      return;
    }
    onAdjusted?.(target.visitId);
    if (departure) {
      try {
        await adjust.mutateAsync({ visitId: target.visitId, kind: 'departure', time: departure });
      } catch (e) {
        // 到着は入っている。退出だけ理由つきで残し、合わせる枠から入れ直してもらう。
        setError(
          `到着 ${arrival} は記録しました。退出は記録できませんでした: ${apiErrorMessage(e)}`,
        );
        setSaving(false);
        return;
      }
    }
    setSaving(false);
    toast.success(
      departure
        ? `到着 ${arrival}・退出 ${departure} を手入力で記録しました`
        : `到着 ${arrival} を手入力で記録しました`,
    );
  };

  return (
    <section
      className="rounded-xl border border-dashed border-brand-primary bg-brand-primary-50 px-4 py-3.5"
      data-testid={`${testIdPrefix}-manual-box`}
    >
      <h3 className="flex items-center gap-2 text-base font-bold text-text-primary">
        到着・退出を手で入れる
        <span className="rounded-full bg-bg-base px-2 py-0.5 text-xs font-medium text-text-secondary">
          管理者のみ
        </span>
      </h3>
      <p className="mb-2 mt-0.5 text-xs text-text-secondary">
        QR
        の読み取りが無い訪問です。紙や看護記録の時刻を入れてください。入れた時刻は「手入力」の印で
        QR の実績と分けて出ます。
      </p>
      <NoShowNote target={target} arrived={false} testId={`${testIdPrefix}-no-show`} />
      {canFillPlanned && (
        <button
          type="button"
          onClick={fillPlanned}
          disabled={pending}
          data-testid={`${testIdPrefix}-manual-fill-planned`}
          className="tnum mb-1 inline-flex h-8 items-center rounded-full border border-border-default bg-bg-base px-3 text-xs text-text-primary hover:bg-bg-muted disabled:opacity-60"
        >
          予定の時刻を入れる（{target.plannedStart}
          {target.plannedEnd && !departureRead ? `–${target.plannedEnd}` : ''}）
        </button>
      )}
      {(
        [
          ['arrival', arrival, setArrival, target.plannedStart],
          ['departure', departure, setDeparture, target.plannedEnd],
        ] as const
      ).map(([kind, value, set, planned]) => (
        <div
          key={kind}
          className="flex flex-wrap items-center gap-2 border-t border-brand-primary-light py-2.5"
        >
          <span className="w-9 shrink-0 text-sm font-bold text-text-primary">
            {KIND_LABEL[kind]}
          </span>
          {kind === 'departure' && departureRead ? (
            <span
              className="tnum text-sm text-text-primary"
              data-testid={`${testIdPrefix}-manual-dep-read`}
            >
              {departureRead}（読み取り済み）
            </span>
          ) : (
            <Input
              type="time"
              aria-label={`${KIND_LABEL[kind]}の時刻`}
              className="tnum h-9 w-[124px] px-2"
              value={value}
              disabled={pending}
              onChange={(e) => set(e.target.value)}
            />
          )}
          {planned && <span className="tnum text-xs text-text-secondary">予定 {planned}</span>}
        </div>
      ))}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-brand-primary-light pt-2.5">
        <p className="text-xs text-text-secondary">
          到着だけ入れて保存すると「実施中」（過去の日なら「退出未記録」）。
        </p>
        <Button
          type="button"
          size="sm"
          className="h-9"
          disabled={pending || !arrivalValid || !departureValid}
          onClick={() => void submit()}
          data-testid={`${testIdPrefix}-manual-save`}
        >
          保存
        </Button>
      </div>
      <ErrorNote error={error} testId={`${testIdPrefix}-error`} />
    </section>
  );
}
