'use client';

/**
 * 「A4 で印刷」の出力オプション（設計 §4: 並び・改ページ・打刻なしを含めるかを選んでから開く）。
 *
 * A4 は BE が返す HTML を新しいタブで開く（用紙の組み立ては BE の CSS 印刷フロー）。
 * 開き方は `components/records/RecordReportButton.tsx` と同じ:
 *   - `window.open` は click ハンドラ内で**同期的に**呼ぶ（ポップアップブロック回避）。
 *   - 'noopener' を付けると `window.open` が null を返すので付けず、遷移後に切り離す。
 *   - 開く先が無いなら BE にレポートを作らせない。
 *
 * 親は開くたびにマウントし直す（`{open && <…/>}`）。選択の初期値を毎回いまの並びに
 * 合わせるため。文字・余白は `add-visit-anywhere-design.md` §3-5。
 */

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import {
  useVisitHistoryReport,
  type VisitHistoryFilters,
  type VisitHistorySort,
} from '@/lib/queries/visit-history';

import { formatRangeLabel } from './visitHistoryFormat';

const GROUP_OPTIONS: ReadonlyArray<{ value: VisitHistorySort; label: string }> = [
  { value: 'staff', label: '看護師別' },
  { value: 'date', label: '日付順' },
  { value: 'patient', label: '患者別' },
];

interface VisitHistoryPrintDialogProps {
  /** いまの絞り込み（一覧と同じものをそのまま BE へ渡す）。 */
  filters: VisitHistoryFilters;
  onClose: () => void;
}

export function VisitHistoryPrintDialog({ filters, onClose }: VisitHistoryPrintDialogProps) {
  // 一覧が日付順のときは、照合しやすい看護師別を初期値にする（モックと同じ）。
  const [group, setGroup] = useState<VisitHistorySort>(
    filters.sort && filters.sort !== 'date' ? filters.sort : 'staff',
  );
  // 既定は BE の既定に合わせる（設計 §3-3: 改ページなし・到着のある訪問だけ）。
  const [pageBreak, setPageBreak] = useState(false);
  const [includeNone, setIncludeNone] = useState(false);
  const { mutateAsync, isPending } = useVisitHistoryReport();

  const canBreak = group !== 'date';

  const run = async () => {
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    if (!win) {
      toast.warning('ポップアップがブロックされました。ブロックを解除してもう一度お試しください。');
      return;
    }
    try {
      const html = await mutateAsync({
        filters,
        options: { group, includeNone, pageBreak: canBreak && pageBreak },
      });
      const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
      win.location.href = url;
      try {
        win.opener = null;
      } catch {
        /* 一部ブラウザで読み取り専用 */
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      onClose();
    } catch (e) {
      win.close();
      toast.error(`A4 の出力に失敗しました: ${apiErrorMessage(e)}`);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-lg text-sm" data-testid="history-print-dialog">
        <DialogHeader>
          <DialogTitle>A4 で印刷</DialogTitle>
          <DialogDescription className="text-text-secondary">
            {formatRangeLabel(filters.from, filters.to)}
            の打刻履歴を、いまの絞り込みのまま A4 縦で開きます。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <label className="flex items-center gap-3 py-1.5">
            <span className="w-12 shrink-0 text-text-secondary">並び</span>
            <select
              aria-label="印刷の並び"
              className="h-9 rounded-md border border-border-default bg-bg-base px-2 text-sm text-text-primary"
              value={group}
              onChange={(e) => setGroup(e.target.value as VisitHistorySort)}
            >
              {GROUP_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>

          <label
            className={[
              'flex items-center gap-2 py-1.5',
              canBreak ? 'cursor-pointer' : 'cursor-not-allowed text-text-muted',
            ].join(' ')}
            title={canBreak ? undefined : '日付順では改ページしません'}
          >
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={canBreak && pageBreak}
              disabled={!canBreak}
              onChange={(e) => setPageBreak(e.target.checked)}
            />
            看護師・患者ごとに改ページ
          </label>

          <label className="flex cursor-pointer items-center gap-2 py-1.5">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={includeNone}
              onChange={(e) => setIncludeNone(e.target.checked)}
            />
            打刻のない予定も載せる
          </label>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={isPending}>
            キャンセル
          </Button>
          <Button
            type="button"
            onClick={() => void run()}
            disabled={isPending}
            data-testid="history-print-open"
          >
            {isPending ? '作成中…' : 'A4 を開く'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
