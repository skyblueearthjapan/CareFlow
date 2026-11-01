'use client';

/**
 * 取り込み後の確認 — カイポケ取り込みで新しく出た無理 (赤)・注意 (黄) の帯と一覧。
 *
 * 設計 = docs/plans/schedule-feasibility-alerts-design-2026-11-01.md §3-4。
 * トーストは消えるので、こちらは次の読み込み・週の切り替えまで画面に残す。
 * 参考 (要注意(実走行) など) は BE が返さない (PO 決定 2026-11-01 夕)。
 */

import { FeasibilityAlertList } from '@/components/schedule/feasibility/FeasibilityBand';
import { FeasibilityTierIcon } from '@/components/schedule/feasibility/FeasibilityMark';
import type { ImportFeasibility } from '@/lib/queries/feasibility';

export function ImportFeasibilityPanel({ feasibility }: { feasibility: ImportFeasibility | null }) {
  if (!feasibility) return null;
  const unassignedNote =
    feasibility.unassignedCount > 0 ? (
      <p className="mt-1 text-xs text-text-secondary" data-testid="import-feasibility-unassigned">
        担当が決まっていない訪問が {feasibility.unassignedCount}{' '}
        件あります（重なり・移動の確認はしていません）。
      </p>
    ) : null;
  // 訪問・イベントの片方だけ確認できなかったとき (ok のまま message が付く)。
  const partialNote =
    feasibility.ok && feasibility.message ? (
      <p className="mt-1 text-xs text-warning-strong" data-testid="import-feasibility-partial">
        {feasibility.message}
      </p>
    ) : null;
  if (!feasibility.ok) {
    return (
      <div
        className="mb-4 rounded-md border border-warning/40 bg-warning-bg px-3 py-2 text-sm text-warning-strong"
        data-testid="import-feasibility"
        data-state="failed"
      >
        {feasibility.message ?? '取り込み後の確認ができませんでした（取り込みは終わっています）'}
      </div>
    );
  }
  if (feasibility.items.length === 0) {
    return (
      <div
        className="mb-4 rounded-md border border-success/40 bg-success-bg px-3 py-2 text-sm text-success"
        data-testid="import-feasibility"
        data-state="none"
      >
        取り込みで新しく出た無理・注意はありません。
        {partialNote}
        {unassignedNote}
      </div>
    );
  }
  return (
    <div
      className="mb-4 rounded-md border border-error/40 bg-error-bg text-sm"
      data-testid="import-feasibility"
      data-state="found"
      role="region"
      aria-label="取り込み後の確認"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        <span className="font-bold text-text-primary">取り込み後の確認</span>
        {feasibility.hardCount > 0 ? (
          <span className="inline-flex items-center gap-1 font-bold text-error">
            <FeasibilityTierIcon tier="hard" />
            無理 <span data-testid="import-feasibility-hard">{feasibility.hardCount}</span>
          </span>
        ) : null}
        {feasibility.softCount > 0 ? (
          <span className="inline-flex items-center gap-1 font-bold text-warning-strong">
            <FeasibilityTierIcon tier="soft" />
            注意 <span data-testid="import-feasibility-soft">{feasibility.softCount}</span>
          </span>
        ) : null}
        <span className="text-xs text-text-secondary">
          この取り込みで新しく出た予定です。スケジュール画面の印で確かめて、時刻や担当を合わせてください。
        </span>
      </div>
      <div className="max-h-64 overflow-auto border-t border-border-subtle bg-bg-base">
        <FeasibilityAlertList items={feasibility.items} testIdPrefix="import-feasibility-item" />
      </div>
      {partialNote || unassignedNote ? (
        <div className="px-3 pb-2">
          {partialNote}
          {unassignedNote}
        </div>
      ) : null}
    </div>
  );
}
