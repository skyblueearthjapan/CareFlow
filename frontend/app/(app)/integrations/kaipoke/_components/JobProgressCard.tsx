'use client';

/**
 * JobProgressCard — 実行中ジョブのライブ進捗（処理件数・現在対象・成否内訳）。
 *
 * kaipoke の単一スロット worker を映す。/integrations/live のポーリング値を
 * そのまま可視化する（processed/total と current_name、成功/失敗/スキップ）。
 */
import { Card } from '@/components/ui/card';
import { RakusukeWorking } from '@/components/brand/Rakusuke';

import type { LiveSnapshot } from '@/lib/schemas/integration';

const COMMAND_LABELS: Record<string, string> = {
  expand: 'スケジュール展開',
  export: 'CSV エクスポート',
  apply: '差分適用',
  diff: '差分計算',
};

export function commandLabel(command: string | null | undefined): string | null {
  if (!command) return null;
  return COMMAND_LABELS[command] ?? command;
}

interface Props {
  live: LiveSnapshot;
}

export function JobProgressCard({ live }: Props) {
  const { processed, total, currentName, success, failed, skipped } = live;
  const hasCount = typeof processed === 'number' && typeof total === 'number' && total > 0;
  const pct = hasCount ? Math.min(100, Math.round((processed! / total!) * 100)) : null;
  const label = commandLabel(live.command) ?? '処理';

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="font-serif text-lg font-bold text-text-primary">実行中 — {label}</h2>
        {hasCount && (
          <span className="font-mono text-sm tabular-nums text-text-secondary">
            {processed} <span className="text-text-muted">/</span> {total}
            {pct !== null && <span className="ml-2 text-brand-primary">{pct}%</span>}
          </span>
        )}
      </div>

      {/* R-11: らく助が代わりに入力してくれている演出 (アニメーション付き) */}
      <RakusukeWorking
        className="mb-3"
        message={`${label}を、らく助が代わりに進めています`}
        sub="そのまま見守っていて大丈夫です。終わったら結果をお知らせします"
      />

      {/* プログレスバー */}
      <div className="h-2 w-full overflow-hidden rounded-full bg-bg-muted">
        <div
          className={`h-full rounded-full bg-brand-primary transition-all duration-500 ease-out ${
            pct === null ? 'animate-pulse' : ''
          }`}
          style={{ width: pct !== null ? `${pct}%` : '40%' }}
        />
      </div>

      {currentName && (
        <p className="mt-3 text-sm text-text-secondary">
          現在の対象: <span className="font-medium text-text-primary">{currentName}</span> 様
        </p>
      )}

      {(typeof success === 'number' ||
        typeof failed === 'number' ||
        typeof skipped === 'number') && (
        <div className="mt-4 grid grid-cols-3 gap-2">
          <MiniStat label="成功" value={success} tone="success" />
          <MiniStat label="失敗" value={failed} tone="error" />
          <MiniStat label="スキップ" value={skipped} tone="warning" />
        </div>
      )}
    </Card>
  );
}

function MiniStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null | undefined;
  tone: 'success' | 'error' | 'warning';
}) {
  const toneClass =
    tone === 'success'
      ? 'bg-success-bg text-success'
      : tone === 'error'
        ? 'bg-error-bg text-error'
        : 'bg-warning-bg text-warning-strong';
  return (
    <div className={`rounded-md px-3 py-2 text-center ${toneClass}`}>
      <div className="font-mono text-lg font-bold tabular-nums leading-none">{value ?? 0}</div>
      <div className="mt-1 text-[11px] font-medium opacity-80">{label}</div>
    </div>
  );
}
