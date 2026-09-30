'use client';

/**
 * 「実績の時刻を合わせる」シート (設計 2026-09-30 §7-4・見た目と文言は
 * `docs/mockups/mobile-actual-time-fix-mock.html` が正)。
 *
 * 下から出るシート (`NotificationListSheet` と同じく Dialog を下寄せ)。
 * 到着 / 退出を切り替え、ひと押しチップ・ホイール・「1 分 まえ / あと」で時刻を
 * 選び、「HH:MM に合わせる」で保存する。予定は動かさない — 合わせるのは実績だけ。
 *
 * 退出がまだ記録されていない訪問で退出側を保存するのは、時刻を合わせるのではなく
 * **退出を新しく記録する**操作 (訪問が完了になる)。ボタンの文言を「退出を HH:MM で
 * 記録する」に変えて取り違えを防ぎ、入れた退出は同じ場所から取り消せるようにする。
 *
 * 文言の決まり (PO 決定 #7): 「合わせる」「調整」を使う。遅れて記録されるのは
 * 看護師の誤りではないので、誤りを正す響きの言葉は使わない。
 *
 * 時刻はすべて「その日の 0 時からの分」で受け取り、保存は JST の "HH:MM" で返す。
 * 保存の行き先 (調整 API / 圏外で退避した打刻の控え) は呼び出し元が決める。
 */
import { useState } from 'react';
import { Loader2, X } from 'lucide-react';

import { TimeWheel } from '@/components/mobile/TimeWheel';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import { jstMinutes, minutesToHm } from '@/lib/format/actualTime';
import type { ActualTimeKind, AdjustReasonCode } from '@/lib/queries/me';
import { cn } from '@/lib/utils';

/**
 * 到着をさかのぼれる上限 (分)。サーバの検証 (設計 §6-1) と同じ値。
 * サーバは管理者にこの下限を課さないが、スマホは管理者でも 90 分まで (仕様)。
 * それより前に合わせるのは PC の打刻履歴から。
 */
const ARRIVAL_BACK_LIMIT_MIN = 90;
/** 退出を読取時刻より後にできる上限 (分)。サーバの検証と同じ値。 */
const DEPARTURE_AFTER_READ_LIMIT_MIN = 30;
const LAST_MINUTE_OF_DAY = 23 * 60 + 59;

/** 理由の表示名 (設計 §4)。 */
const REASON_LABEL: Record<AdjustReasonCode, string> = {
  intercom_wait: 'インターホン待ち',
  read_later: '読み取りが後になった',
  no_read: '読み取りなし',
  other: 'その他',
};

/**
 * 理由の選択肢。**先頭が初期値**。PC の打刻履歴 (`VisitHistoryDetailDialog`) と同じ規則:
 *   到着                 … インターホン待ち
 *   退出 (読み取りあり)  … 読み取りが後になった
 *   退出 (読み取りなし)  … 読み取りなし
 */
function reasonCodes(kind: ActualTimeKind, departureHasRead: boolean): AdjustReasonCode[] {
  if (kind === 'arrival') return ['intercom_wait', 'read_later', 'other'];
  return departureHasRead ? ['read_later', 'no_read', 'other'] : ['no_read', 'other'];
}

/** 到着のひと押し (読取時刻からの差・分)。 */
const ARRIVAL_QUICK: { delta: number; label: string }[] = [
  { delta: 0, label: '読取どおり' },
  { delta: -5, label: '5分前' },
  { delta: -10, label: '10分前' },
  { delta: -15, label: '15分前' },
  { delta: -20, label: '20分前' },
  { delta: -30, label: '30分前' },
];

/** 実績 1 つ分 (0 時からの分)。 */
export interface ActualTimeSide {
  /** 実績時刻 (調整後。無ければ読取時刻)。まだ無ければ null。 */
  at: number | null;
  /** QR を読み取った時刻。読み取りが無ければ null。 */
  readAt: number | null;
}

export interface ActualTimeTimes {
  /** 到着。読み取りが必ずある (無い訪問ではシートを開かない)。 */
  arrival: { at: number; readAt: number };
  departure: ActualTimeSide;
  /** 今日の訪問か (退出は現在時刻まで・過去の訪問は 23:59 まで)。 */
  isToday: boolean;
  /** 現在時刻 (0 時からの分・JST)。 */
  nowMin: number;
}

/**
 * 合わせられる時刻の範囲 `[下端, 上端]` (どちらも含む)。上端 < 下端 なら
 * 合わせられる時刻が無い。サーバの検証 (設計 §6-1) と同じ規則。
 *
 *   到着: 読取時刻の 90 分前 〜 読取時刻。退出の実績があればその 1 分前まで。
 *   退出: 到着の実績の 1 分後 〜。読み取りがあれば読取時刻の 30 分後まで。
 *         今日の訪問は現在時刻まで、過去の訪問は 23:59 まで。
 */
export function actualTimeRange(kind: ActualTimeKind, t: ActualTimeTimes): [number, number] {
  if (kind === 'arrival') {
    const lo = Math.max(0, t.arrival.readAt - ARRIVAL_BACK_LIMIT_MIN);
    const hi =
      t.departure.at != null ? Math.min(t.arrival.readAt, t.departure.at - 1) : t.arrival.readAt;
    return [lo, hi];
  }
  const cap = t.isToday ? t.nowMin : LAST_MINUTE_OF_DAY;
  const lo = t.arrival.at + 1;
  const hi =
    t.departure.readAt != null
      ? Math.min(cap, t.departure.readAt + DEPARTURE_AFTER_READ_LIMIT_MIN)
      : cap;
  return [lo, hi];
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

export interface ActualTimeSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 開いたときに選ばれている側。 */
  initialKind: ActualTimeKind;
  patientName: string;
  /** 予定の開始 / 終了 (0 時からの分)。表示と「滞在 予定どおり」に使う。 */
  planStart: number;
  planEnd: number;
  arrival: { at: number; readAt: number };
  departure: ActualTimeSide;
  isToday: boolean;
  /** 退出側を選べなくする (圏外で退避した到着を合わせている間)。 */
  departureDisabled?: boolean;
  /** いまの退出は、読み取りが無く手で入れた時刻か。 */
  departureManual?: boolean;
  /**
   * 「入れた退出時刻を取り消す」。渡されていて `departureManual` のときだけ、退出側に
   * ボタンを出す。成功したら true を返す (シートを閉じる)。
   */
  onCancelManualDeparture?: () => Promise<boolean>;
  saving?: boolean;
  /**
   * 保存。`time` は JST の "HH:MM"。**null は「読取時刻に戻す」**(読取時刻と同じ
   * 時刻を選んで保存したとき)。成功したら true を返す (シートを閉じる)。
   */
  onSave: (
    kind: ActualTimeKind,
    time: string | null,
    reasonCode: AdjustReasonCode,
  ) => Promise<boolean>;
}

export function ActualTimeSheet({ open, onOpenChange, ...body }: ActualTimeSheetProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        // 既定の閉じるボタン (16px) は押しにくいので隠し、44px のものを自前で置く。
        className="bottom-0 left-0 right-0 top-auto max-h-[92dvh] max-w-full translate-x-0 translate-y-0 gap-0 overflow-y-auto rounded-b-none rounded-t-[22px] border-x-0 border-b-0 px-4 pb-5 pt-2.5 [&>button:last-child]:hidden"
        data-testid="actual-time-sheet"
      >
        {/* 開くたびに作り直す (前回の選びかけを持ち越さない)。 */}
        <SheetBody {...body} onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

type SheetBodyProps = Omit<ActualTimeSheetProps, 'open' | 'onOpenChange'> & {
  onClose: () => void;
};

function SheetBody({
  initialKind,
  patientName,
  planStart,
  planEnd,
  arrival,
  departure,
  isToday,
  departureDisabled = false,
  departureManual = false,
  onCancelManualDeparture,
  saving = false,
  onSave,
  onClose,
}: SheetBodyProps) {
  // 開いた時点の現在時刻で範囲を決める (開いている間に列が伸びて位置がずれない)。
  const [nowMin] = useState(() => jstMinutes(new Date().toISOString()) ?? LAST_MINUTE_OF_DAY);
  const times: ActualTimeTimes = { arrival, departure, isToday, nowMin };
  const plan = planEnd - planStart;

  /** その側を開いたときの最初の時刻。未記録の退出は「到着 + 予定の長さ」から。 */
  function initialDraft(k: ActualTimeKind): number {
    const [lo, hi] = actualTimeRange(k, times);
    const base = k === 'arrival' ? arrival.at : (departure.at ?? arrival.at + plan);
    return clamp(base, lo, Math.max(lo, hi));
  }

  const departureHasRead = departure.readAt != null;
  const startKind: ActualTimeKind = departureDisabled ? 'arrival' : initialKind;
  const [kind, setKind] = useState<ActualTimeKind>(startKind);
  const [draft, setDraft] = useState(() => initialDraft(startKind));
  const [reason, setReason] = useState<AdjustReasonCode>(
    () => reasonCodes(startKind, departureHasRead)[0]!,
  );

  function switchKind(next: ActualTimeKind) {
    setKind(next);
    setDraft(initialDraft(next));
    setReason(reasonCodes(next, departureHasRead)[0]!);
  }

  const [lo, hi] = actualTimeRange(kind, times);
  const hasRange = hi >= lo;
  const current = kind === 'arrival' ? arrival.at : departure.at;
  const readAt = kind === 'arrival' ? arrival.readAt : departure.readAt;
  /** 読み取りの無い退出を、これから入れる。 */
  const manualEntry = kind === 'departure' && departure.at == null;
  const changed = manualEntry || draft !== current;
  const canSave = hasRange && changed && draft >= lo && draft <= hi;

  const jump = (target: number) => setDraft(clamp(target, lo, hi));

  // 滞在 = 退出 − 到着。いま選んでいる側は選びかけの時刻で計算する。
  const stayArrival = kind === 'arrival' ? draft : arrival.at;
  const stayDeparture = kind === 'departure' ? draft : departure.at;
  const stay = stayDeparture != null ? stayDeparture - stayArrival : null;

  const diff = readAt != null ? draft - readAt : null;

  const quick =
    kind === 'arrival'
      ? ARRIVAL_QUICK.map((q) => ({ target: arrival.readAt + q.delta, label: q.label }))
      : [
          { stay: plan, label: `滞在 予定どおり ${plan}分` },
          { stay: plan - 5, label: `滞在 ${plan - 5}分` },
          { stay: plan + 5, label: `滞在 ${plan + 5}分` },
          { stay: plan + 10, label: `滞在 ${plan + 10}分` },
        ]
          .filter((q) => q.stay > 0)
          .map((q) => ({ target: arrival.at + q.stay, label: q.label }));

  async function handleSave() {
    if (!canSave || saving) return;
    // 読取時刻と同じ時刻 = 読取時刻に戻す (調整を残さない)。
    const time = readAt != null && draft === readAt ? null : minutesToHm(draft);
    const ok = await onSave(kind, time, reason);
    if (ok) onClose();
  }

  async function handleCancelManualDeparture() {
    if (!onCancelManualDeparture || saving) return;
    const ok = await onCancelManualDeparture();
    if (ok) onClose();
  }

  return (
    <div>
      <div className="mx-auto mb-2.5 h-1 w-10 rounded-full bg-border-strong" aria-hidden="true" />

      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <DialogTitle className="text-base leading-snug">実績の時刻を合わせる</DialogTitle>
          <DialogDescription className="tnum truncate text-xs text-text-secondary">
            {patientName}　予定 {minutesToHm(planStart)}–{minutesToHm(planEnd)}
          </DialogDescription>
        </div>
        <DialogClose
          aria-label="閉じる"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-border-default bg-bg-base text-text-secondary"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </DialogClose>
      </div>

      {/* 到着 / 退出の切り替え。 */}
      <div className="my-2.5 grid grid-cols-2 overflow-hidden rounded-[10px] border border-border-strong">
        {(['arrival', 'departure'] as const).map((k) => {
          const on = kind === k;
          const at = k === 'arrival' ? arrival.at : departure.at;
          return (
            <button
              key={k}
              type="button"
              aria-pressed={on}
              disabled={k === 'departure' && departureDisabled}
              onClick={() => {
                if (!on) switchKind(k);
              }}
              className={cn(
                'tnum h-11 text-sm disabled:cursor-not-allowed disabled:text-text-muted',
                k === 'departure' && 'border-l border-border-strong',
                on ? 'bg-brand-primary font-bold text-white' : 'bg-bg-base text-text-secondary',
              )}
            >
              {k === 'arrival' ? '到着' : '退出'} {at != null ? minutesToHm(at) : '（未記録）'}
            </button>
          );
        })}
      </div>

      {/* いま選んでいる時刻。 */}
      <div className="mb-1 mt-1.5 text-center">
        <p
          className="tnum text-[52px] font-bold leading-none text-text-primary"
          data-testid="actual-time-draft"
        >
          {minutesToHm(draft)}
        </p>
        <p className="tnum mt-1.5 text-[13px] text-text-secondary" data-testid="actual-time-sub">
          {readAt == null || diff == null ? (
            '退出の読み取りがありません（手入力）'
          ) : diff === 0 ? (
            `読取 ${minutesToHm(readAt)} のまま`
          ) : (
            <>
              読取 {minutesToHm(readAt)} ・{' '}
              <em className="font-bold not-italic text-brand-primary-hover">
                {Math.abs(diff)} 分{diff < 0 ? '前' : '後'}
              </em>
            </>
          )}
        </p>
      </div>

      {hasRange ? (
        <>
          {/* ひと押しで飛ぶ。範囲の外は端に寄せる。 */}
          <div className="flex gap-1.5 overflow-x-auto pb-0.5 pt-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {quick.map((q) => {
              const on = draft === q.target;
              return (
                <button
                  key={q.label}
                  type="button"
                  aria-pressed={on}
                  onClick={() => jump(q.target)}
                  className={cn(
                    'h-11 shrink-0 rounded-full border px-3.5 text-sm',
                    on
                      ? 'border-brand-primary bg-brand-primary font-bold text-white'
                      : 'border-border-strong bg-bg-base text-text-primary',
                  )}
                >
                  {q.label}
                </button>
              );
            })}
          </div>

          <TimeWheel
            // 到着 ⇄ 退出で作り直す。前の側で回していた勢い (慣性スクロール) を、
            // 切り替えた先の時刻の列へ持ち込まない。
            key={kind}
            className="mb-1.5 mt-2"
            min={lo}
            max={hi}
            value={draft}
            onChange={setDraft}
            ariaLabel={kind === 'arrival' ? '到着の時刻' : '退出の時刻'}
          />

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={draft - 1 < lo}
              onClick={() => jump(draft - 1)}
              className="h-12 rounded-xl border border-border-strong bg-bg-base text-[15px] font-bold text-text-primary disabled:opacity-40"
            >
              1 分 まえ
            </button>
            <button
              type="button"
              disabled={draft + 1 > hi}
              onClick={() => jump(draft + 1)}
              className="h-12 rounded-xl border border-border-strong bg-bg-base text-[15px] font-bold text-text-primary disabled:opacity-40"
            >
              1 分 あと
            </button>
          </div>
        </>
      ) : (
        <p
          className="my-3 rounded-lg bg-bg-muted px-3 py-3 text-center text-[13px] text-text-secondary"
          data-testid="actual-time-no-range"
        >
          いまは合わせられる時刻がありません。少し時間をおいてから開いてください。
        </p>
      )}

      <p
        className="tnum mb-0.5 mt-1.5 text-center text-[13px] text-text-secondary"
        data-testid="actual-time-summary"
      >
        {stay != null ? (
          <>
            滞在 <b className="text-text-primary">{stay} 分</b>（予定 {plan} 分）
            {stay === plan && <span className="font-bold text-success"> 予定どおり</span>}
          </>
        ) : (
          `予定 ${minutesToHm(planStart)}–${minutesToHm(planEnd)}（${plan} 分）`
        )}
      </p>

      {/* 理由 (任意・初期値つき)。 */}
      <div className="mb-3 mt-2 flex flex-wrap justify-center gap-1.5">
        {reasonCodes(kind, departureHasRead).map((code) => {
          const on = reason === code;
          return (
            <button
              key={code}
              type="button"
              aria-pressed={on}
              onClick={() => setReason(code)}
              className={cn(
                'h-11 rounded-full border px-3.5 text-[13px]',
                on
                  ? 'border-text-primary bg-text-primary text-white'
                  : 'border-border-strong bg-bg-base text-text-primary',
              )}
            >
              {REASON_LABEL[code]}
            </button>
          );
        })}
      </div>

      <Button
        type="button"
        size="lg"
        className="tnum w-full text-base"
        disabled={!canSave || saving}
        onClick={() => void handleSave()}
      >
        {saving && <Loader2 className="h-4 w-4 animate-spin" />}
        {/* 未記録の退出は「合わせる」ではなく、退出を新しく記録する操作。 */}
        {manualEntry
          ? `退出を ${minutesToHm(draft)} で記録する`
          : `${minutesToHm(draft)} に合わせる`}
      </Button>
      {manualEntry && (
        <p
          className="mt-1.5 text-center text-xs text-text-secondary"
          data-testid="actual-time-manual-note"
        >
          退出を記録すると、この訪問は完了になります。
        </p>
      )}

      {/* 手で入れた退出は、ここから取り消せる (訪問中に戻る)。 */}
      {kind === 'departure' && departureManual && onCancelManualDeparture && (
        <Button
          type="button"
          variant="outline"
          className="mt-2 h-11 w-full text-sm"
          disabled={saving}
          onClick={() => void handleCancelManualDeparture()}
          data-testid="actual-time-cancel-manual"
        >
          入れた退出時刻を取り消す
        </Button>
      )}
    </div>
  );
}
