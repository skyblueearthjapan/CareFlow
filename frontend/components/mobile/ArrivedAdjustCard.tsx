'use client';

/**
 * 到着した直後のカード — その場で 1 回押すだけで到着の時刻を合わせる
 * (設計 2026-09-30 §7-2・見た目は `docs/mockups/mobile-actual-time-fix-mock.html`)。
 *
 * お宅に着いてから QR を読み取るまでに待ち時間 (インターホン待ちなど) があると、
 * 読取時刻は実際の到着より遅くなる。何もしなければ読取時刻のまま。
 *
 * このカードは表示だけを受け持つ。保存の行き先 (調整 API か、圏外で退避した
 * 打刻の控えか) は呼び出し元が決める。
 */
import { CheckCircle2 } from 'lucide-react';

import { minutesToHm } from '@/lib/format/actualTime';
import { cn } from '@/lib/utils';

/** ひと押しでさかのぼる分数。 */
const QUICK_MINUTES = [5, 10, 15] as const;

const SUB_BUTTON =
  'h-11 rounded-[10px] border border-border-strong bg-bg-base text-sm text-text-primary disabled:opacity-50';
const SUB_BUTTON_PRIMARY = 'border-brand-primary bg-brand-primary font-bold text-white';

export interface ArrivedAdjustCardProps {
  /** QR を読み取った時刻 (0 時からの分)。 */
  readMin: number;
  /** 合わせた後の到着時刻 (0 時からの分)。読取時刻のままなら null。 */
  adjustedMin: number | null;
  /** 保存中 (二度押し防止)。 */
  busy?: boolean;
  /** 「N分前」を押した。 */
  onQuick: (minutesBack: number) => void;
  /** 「細かく合わせる」— 時刻を合わせるシートを開く。 */
  onFine: () => void;
  /** 「元に戻す」— 読取時刻に戻す。 */
  onUndo: () => void;
  /** 「このままでOK」/「OK」— カードを閉じる。 */
  onDismiss: () => void;
}

export function ArrivedAdjustCard({
  readMin,
  adjustedMin,
  busy = false,
  onQuick,
  onFine,
  onUndo,
  onDismiss,
}: ArrivedAdjustCardProps) {
  /** 合わせてある時刻。読取時刻のままなら null。 */
  const adjustedTo = adjustedMin != null && adjustedMin !== readMin ? adjustedMin : null;

  return (
    <div
      className="rounded-xl border-2 border-brand-primary bg-brand-primary-50 p-3.5"
      data-testid="arrived-adjust-card"
    >
      <p className="tnum flex items-center gap-2 text-base font-bold text-brand-primary-hover">
        <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
        {adjustedTo != null
          ? `到着を ${minutesToHm(adjustedTo)} に合わせました`
          : `到着 ${minutesToHm(readMin)} を記録しました`}
      </p>

      {adjustedTo != null ? (
        <>
          <p className="tnum mb-2.5 mt-1 text-[13px] text-text-secondary">
            読み取った時刻は {minutesToHm(readMin)}（{readMin - adjustedTo} 分前に到着）。
            読取時刻も記録に残ります。
          </p>
          <div className="grid grid-cols-2 gap-2">
            <button type="button" className={SUB_BUTTON} disabled={busy} onClick={onUndo}>
              元に戻す
            </button>
            <button
              type="button"
              className={cn(SUB_BUTTON, SUB_BUTTON_PRIMARY)}
              onClick={onDismiss}
            >
              OK
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="mb-2.5 mt-1 text-[13px] text-text-secondary">
            お宅に着いてから読み取るまでに時間があったら、着いた時刻に合わせられます。
          </p>
          <div className="grid grid-cols-3 gap-2">
            {QUICK_MINUTES.map((n) => (
              <button
                key={n}
                type="button"
                // 0 時をまたぐ時刻には合わせられない (同じ日の中だけ)。
                disabled={busy || readMin - n < 0}
                onClick={() => onQuick(n)}
                className="h-[60px] rounded-[10px] border border-brand-primary bg-bg-base text-[15px] font-bold leading-tight text-brand-primary-hover disabled:opacity-50"
              >
                {n}分前
                <small className="tnum block text-xs font-medium text-text-secondary">
                  {readMin - n >= 0 ? minutesToHm(readMin - n) : '—'}
                </small>
              </button>
            ))}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <button type="button" className={SUB_BUTTON} disabled={busy} onClick={onFine}>
              細かく合わせる
            </button>
            <button
              type="button"
              className={cn(SUB_BUTTON, SUB_BUTTON_PRIMARY)}
              onClick={onDismiss}
            >
              このままでOK
            </button>
          </div>
        </>
      )}
    </div>
  );
}
