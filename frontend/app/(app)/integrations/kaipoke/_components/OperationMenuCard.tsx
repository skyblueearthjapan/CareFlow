'use client';

/**
 * OperationMenuCard — カイポケ操作メニュー（展開 / エクスポート / 差分計算）。
 *
 * 対象月を選び、読み取り系・生成系の操作を起動する。実行中は全ボタンを
 * ロックし多重起動を防ぐ（kaipoke は単一スロット）。差分適用は下段の
 * 差分シート（CorrectionSheetView）側で明示確認を挟むため、ここには置かない。
 */
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

interface Props {
  month: string;
  onMonthChange: (v: string) => void;
  busy: boolean;
  onExpand: () => void;
  onExport: () => void;
  onDiff: () => void;
}

export function OperationMenuCard({
  month,
  onMonthChange,
  busy,
  onExpand,
  onExport,
  onDiff,
}: Props) {
  return (
    <Card className="p-5">
      <h2 className="mb-4 font-serif text-lg font-bold text-text-primary">操作メニュー</h2>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,220px)_1fr] lg:items-end">
        <label className="text-sm">
          <span className="mb-1.5 block text-xs font-medium text-text-secondary">
            対象月 (YYYY-MM)
          </span>
          <Input type="month" value={month} onChange={(e) => onMonthChange(e.target.value)} />
        </label>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <OperationButton
            title="スケジュール展開"
            desc="週間パターンを月間へ展開"
            onClick={onExpand}
            disabled={busy || !month}
          />
          <OperationButton
            title="CSV エクスポート"
            desc="カイポケ現況を取得（読み取り）"
            onClick={onExport}
            disabled={busy || !month}
          />
          <OperationButton
            title="差分計算"
            desc="現況と最適案の差分を作成"
            onClick={onDiff}
            disabled={busy || !month}
          />
        </div>
      </div>

      {busy && (
        <p className="mt-4 text-xs text-text-muted">
          実行中は新しい操作を開始できません。完了までお待ちください。
        </p>
      )}
    </Card>
  );
}

function OperationButton({
  title,
  desc,
  onClick,
  disabled,
}: {
  title: string;
  desc: string;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group flex flex-col items-start gap-1 rounded-lg border border-border-default bg-bg-base px-4 py-3 text-left transition-colors hover:border-brand-primary hover:bg-brand-primary-50 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-border-default disabled:hover:bg-bg-base"
    >
      <span className="text-sm font-semibold text-text-primary group-hover:text-brand-primary-hover">
        {title}
      </span>
      <span className="text-xs leading-relaxed text-text-muted">{desc}</span>
    </button>
  );
}
