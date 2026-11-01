'use client';

/**
 * 実現性の印 (赤「！」・黄「△」) — 押すと理由が出る。スケジュールと訪問モニターで共通。
 *
 * 設計 = docs/plans/schedule-feasibility-alerts-design-2026-11-01.md §3-5 / §3-6。
 * - 印のデータは `FeasibilityMarksProvider` が期間 (1 週 / 1 日) をまとめて 1 回で取り、
 *   各カードは `useFeasibilityMarkFor(visitId)` で読むだけにする (大きな盤面に props を
 *   通さずに済む)。Provider の外では常に空 = 何も出ない (他の呼び出し元は今のまま)。
 * - 印は管理者だけに出す (PO 決定 2026-11-01)。Provider の `enabled` で切る。
 * - 印からは予定を変えない。合わせる操作は、押した先の既存の操作に任せる。
 */

import { createContext, useContext, type ReactNode } from 'react';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  useFeasibilityMarks,
  type FeasibilityMarkEntry,
  type FeasibilityMarksResponse,
  type FeasibilityTier,
} from '@/lib/queries/feasibility';
import { cn } from '@/lib/utils';

const FeasibilityMarksContext = createContext<FeasibilityMarksResponse | null>(null);

const EMPTY: FeasibilityMarkEntry[] = [];

export interface FeasibilityMarksProviderProps {
  dateFrom: string; // YYYY-MM-DD
  dateTo: string; // YYYY-MM-DD
  officeId?: string | null;
  /** 管理者のときだけ true にする。false なら取得もせず、印も帯も出ない。 */
  enabled: boolean;
  children: ReactNode;
}

export function FeasibilityMarksProvider({
  dateFrom,
  dateTo,
  officeId = null,
  enabled,
  children,
}: FeasibilityMarksProviderProps) {
  const q = useFeasibilityMarks({ dateFrom, dateTo, officeId, enabled });
  const value = enabled ? (q.data ?? null) : null;
  return (
    <FeasibilityMarksContext.Provider value={value}>{children}</FeasibilityMarksContext.Provider>
  );
}

/** Provider が取った期間の印・一覧・件数 (Provider の外や管理者以外は null)。 */
export function useFeasibilityMarksData(): FeasibilityMarksResponse | null {
  return useContext(FeasibilityMarksContext);
}

/** 訪問 1 件の印 (無ければ空の配列)。 */
export function useFeasibilityMarkFor(visitId: string): FeasibilityMarkEntry[] {
  const data = useContext(FeasibilityMarksContext);
  return data?.marks[visitId] ?? EMPTY;
}

export const TIER_LABEL: Record<FeasibilityTier, string> = { hard: '無理', soft: '注意' };

/** 赤「！」/ 黄「△」の小さな丸 (一覧や帯でも使う)。 */
export function FeasibilityTierIcon({
  tier,
  className,
}: {
  tier: FeasibilityTier;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-grid h-4 min-w-4 shrink-0 place-items-center rounded-full px-0.5 text-[10px] font-bold leading-none',
        tier === 'hard' ? 'bg-error text-white' : 'bg-warning text-white',
        className,
      )}
    >
      {tier === 'hard' ? '！' : '△'}
    </span>
  );
}

export interface FeasibilityMarkProps {
  marks: FeasibilityMarkEntry[];
  testId?: string;
  className?: string;
  /** 終わった訪問 (退出済み) は薄く出す (訪問モニター)。 */
  dimmed?: boolean;
}

/**
 * カードに重ねる印。いちばん重い区分の色を出し、2 件以上なら数を添える。
 * ドラッグできるカードの上でも押せるよう、pointerdown とクリックをカードへ伝えない。
 */
export function FeasibilityMark({
  marks,
  testId,
  className,
  dimmed = false,
}: FeasibilityMarkProps) {
  if (marks.length === 0) return null;
  const tier: FeasibilityTier = marks.some((m) => m.tier === 'hard') ? 'hard' : 'soft';
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid={testId}
          data-tier={tier}
          aria-label={`${TIER_LABEL[tier]}の印（${marks.length} 件）。押すと理由が出ます`}
          title={`${TIER_LABEL[tier]}：押すと理由が出ます`}
          onPointerDown={(e) => e.stopPropagation()}
          onPointerDownCapture={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          className={cn(
            'pointer-events-auto inline-flex items-center gap-px rounded-full shadow-[0_0_0_2px_#fff] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary',
            dimmed && 'opacity-50',
            className,
          )}
        >
          <FeasibilityTierIcon tier={tier} />
          {marks.length > 1 ? (
            <span
              className={cn(
                'rounded-full px-1 text-[9px] font-bold leading-4 text-white',
                tier === 'hard' ? 'bg-error' : 'bg-warning',
              )}
            >
              {marks.length}
            </span>
          ) : null}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-80 p-3 text-xs"
        data-testid={testId ? `${testId}-reasons` : undefined}
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <ul className="space-y-2">
          {marks.map((m, i) => (
            <li key={`${m.kind}-${i}`} className="flex items-start gap-1.5">
              <FeasibilityTierIcon tier={m.tier} className="mt-px" />
              <span className="min-w-0">
                <span
                  className={cn(
                    'mr-1 font-bold',
                    m.tier === 'hard' ? 'text-error' : 'text-warning-strong',
                  )}
                >
                  {TIER_LABEL[m.tier]}（{m.kind}）
                </span>
                <span className="text-text-primary">{m.reason}</span>
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[11px] text-text-muted">
          時刻や担当は、カードの操作で合わせてください。
        </p>
      </PopoverContent>
    </Popover>
  );
}
