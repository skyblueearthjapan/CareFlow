'use client';

/**
 * PoolPanel — 保留プール (W3-FE5).
 *
 * 設計 v0.9 §3.6.2 (Layer 1) の「未配置プール」。新規患者や時刻スロットから
 * 解除された患者をここに溜め、ドラッグでセルに配置する。配置済みカードを
 * 本パネルにドロップすると配置解除 (= weekly_pattern entry を削除予定として
 * 親 state に戻す) になる。
 *
 * 1 つの大きな drop target として `useDroppable` を取り、子に PatientCard を
 * 並べる。空のときも drop 可能であることを示すプレースホルダを描画する。
 *
 * Wave 18 Phase B-3 (希望曜日別グループ化):
 *   - `PoolGroupedByWeekday` を併記。プール内 patient を
 *     `weekly_pattern.preferred_weekdays` でセクション分けし、各セクション
 *     は折りたたみ可能 (`<details>` ベース)。
 */
import * as React from 'react';
import { useDroppable } from '@dnd-kit/core';
import { Inbox } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import {
  WEEKDAY_KEYS,
  WEEKDAY_LABELS_JA,
  coerceWeeklyPattern,
  type WeekdayKey,
  type PatientRead,
} from '@/lib/schemas/patient';

export const POOL_DROPPABLE_ID = 'pool';

// ─────────────────────────────────────────────────────────────────────────
// Wave 18 Phase B-3: PoolGroupedByWeekday
// (旧 PoolPanel / PoolPanelProps は W17 時代のシンプル版。Wave 18 B-3 で
//  PoolGroupedByWeekday に置き換え済みのため削除。現在の利用箇所はなし。)
// ─────────────────────────────────────────────────────────────────────────

/** プール患者のグループ化キー: WeekdayKey の配列を文字列化 (canonical 順)。 */
export function poolGroupKey(preferredWeekdays: WeekdayKey[] | null | undefined): string {
  const arr = Array.isArray(preferredWeekdays)
    ? [...preferredWeekdays]
        .filter((d): d is WeekdayKey => (WEEKDAY_KEYS as readonly string[]).includes(d as string))
        .sort((a, b) => WEEKDAY_KEYS.indexOf(a) - WEEKDAY_KEYS.indexOf(b))
    : [];
  if (arr.length === 0) return '__none__';
  return arr.join(',');
}

/** グループキーを表示用ラベルに変換: 'Mon,Wed,Fri' → '月・水・金 希望' / '__none__' → '希望なし' */
export function poolGroupLabel(key: string): string {
  if (key === '__none__') return '希望なし';
  const parts = key.split(',') as WeekdayKey[];
  if (parts.length === 1) return `${WEEKDAY_LABELS_JA[parts[0]!]}希望`;
  return `${parts.map((p) => WEEKDAY_LABELS_JA[p]).join('・')} 希望`;
}

export interface PoolGroupedByWeekdayProps {
  /** 表示対象 patient (parent でフィルタ済み)。 */
  patients: PatientRead[];
  /**
   * patient → カード React 要素のレンダラ。親が `PatientCard` (draggableId 付き)
   * を組み立てる。グループごとにマウントされるため key は patient.id を推奨。
   */
  renderCard: (patient: PatientRead) => React.ReactNode;
  /** プール全体の disabled (RBAC)。drop target も無効化。 */
  disabled?: boolean;
}

/**
 * 患者を `weekly_pattern.preferred_weekdays` でセクション分けして表示する。
 * 1 セクション = 1 グループ (例: 「月希望」「月・水・金 希望」「希望なし」)。
 * 各セクションは `<details open>` で折りたたみ可。
 *
 * drop target は `PoolPanel` と同じ `POOL_DROPPABLE_ID` を 1 つだけ取る
 * (親の onDragEnd でセル → プール戻しを判定)。
 */
export function PoolGroupedByWeekday({
  patients,
  renderCard,
  disabled = false,
}: PoolGroupedByWeekdayProps) {
  const { isOver, setNodeRef } = useDroppable({
    id: POOL_DROPPABLE_ID,
    disabled,
    data: { kind: 'pool' },
  });

  const grouped = React.useMemo(() => {
    const m = new Map<string, PatientRead[]>();
    for (const p of patients) {
      const wp = coerceWeeklyPattern(p.weekly_pattern);
      const key = poolGroupKey(wp.preferred_weekdays);
      const arr = m.get(key) ?? [];
      arr.push(p);
      m.set(key, arr);
    }
    // 出力順: 単曜日 (Mon..Sun) → 複数曜日 (alphabetical) → 希望なし
    const keys = [...m.keys()].sort((a, b) => {
      if (a === '__none__') return 1;
      if (b === '__none__') return -1;
      const aLen = a.split(',').length;
      const bLen = b.split(',').length;
      if (aLen !== bLen) return aLen - bLen;
      return a.localeCompare(b);
    });
    return keys.map((key) => ({ key, items: m.get(key) ?? [] }));
  }, [patients]);

  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="flex items-center gap-1 text-sm font-semibold text-text-primary">
          <Inbox className="h-4 w-4" aria-hidden />
          保留プール (希望曜日別)
        </h2>
        <span className="tnum text-xs text-text-muted">{patients.length} 名</span>
      </div>
      <div
        ref={setNodeRef}
        data-pool="true"
        data-testid="pool-grouped-by-weekday"
        className={cn(
          'min-h-[64px] rounded border border-dashed p-2 transition-colors',
          isOver && !disabled
            ? 'border-brand-primary/60 bg-brand-primary/10'
            : 'border-border-default bg-bg-muted/40',
        )}
      >
        {patients.length === 0 ? (
          <p className="py-3 text-center text-xs text-text-muted">
            プールは空です。配置を解除した患者がここに戻ります。
          </p>
        ) : (
          <div className="space-y-2">
            {grouped.map(({ key, items }) => (
              <details
                key={key}
                open
                className="rounded border border-border-default/60 bg-bg-base"
                data-testid={`pool-group-${key}`}
              >
                <summary className="cursor-pointer select-none rounded px-2 py-1 text-xs font-semibold text-text-secondary hover:bg-bg-muted">
                  {poolGroupLabel(key)}
                  <span className="tnum ml-2 text-[10px] font-normal text-text-muted">
                    ({items.length})
                  </span>
                </summary>
                <div className="space-y-1 p-2">
                  {items.map((p) => (
                    <React.Fragment key={p.id}>{renderCard(p)}</React.Fragment>
                  ))}
                </div>
              </details>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
