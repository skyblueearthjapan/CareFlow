'use client';
/**
 * 「実現性チェック」ボタン — 週の予定を移動・重なり・バッファ・同住所ルールで機械判定し、
 * A4 縦の印刷用レポートを新しいタブに開く (read-only・盤面は変更しない)。
 *
 * - 押すたびに backend で計算し直す (直前の DnD 編集を反映)。
 * - 結果の件数 (❗成立しない / △余裕なし) をボタン脇のバッジに出し、トーストでも要約する。
 * - レポート HTML は backend が自己完結で生成 (フォント等の外部読み込みなし)。
 *   `window.open` は click ハンドラ内で同期的に呼ぶ (ポップアップブロック回避)。
 */
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { useFeasibilityReport, type FeasibilityReport } from '@/lib/queries/feasibility';

export interface FeasibilityCheckButtonProps {
  isoYear: number;
  isoWeek: number;
  officeId?: string | null;
  /** admin のみ表示 (RBAC)。 */
  canEdit: boolean;
}

function summarize(r: FeasibilityReport): string {
  const parts = Object.entries(r.summary).map(([k, v]) => `${k} ${v}`);
  return parts.length ? parts.join(' / ') : '指摘なし';
}

export function FeasibilityCheckButton({ isoYear, isoWeek, officeId, canEdit }: FeasibilityCheckButtonProps) {
  const mut = useFeasibilityReport();
  const [last, setLast] = useState<FeasibilityReport | null>(null);

  const run = useCallback(async () => {
    // click 直下で先に空タブを確保 (非同期 fetch 後の open はブロックされる)。
    // 注意: features に 'noopener' を付けると仕様上 window.open が null を返すため付けない
    // (レビュー NEW-1)。opener の切り離しは遷移後に win.opener = null で行う。
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    try {
      const r = await mut.mutateAsync({ isoYear, isoWeek, officeId: officeId ?? null });
      setLast(r);
      if (win && r.html) {
        // document.write ではなく Blob URL へ静的に遷移する (backend 生成 HTML をそのまま表示、
        // アプリ側の DOM へのハンドルを渡さない)。blob: はアプリと同一オリジンなので、
        // HTML 側のエスケープ (backend html.escape) が安全性の要であることに変わりはない。
        const url = URL.createObjectURL(new Blob([r.html], { type: 'text/html;charset=utf-8' }));
        win.location.href = url;
        try {
          win.opener = null;
        } catch {
          /* 一部ブラウザで読み取り専用 */
        }
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } else if (win) {
        win.close();
      }
      const head = r.hard_count > 0 ? `❗ 成立しない ${r.hard_count} 件` : '❗ なし';
      toast[r.hard_count > 0 ? 'warning' : 'success'](
        `実現性チェック: ${head} ／ △ ${r.soft_count} 件（${summarize(r)}）`,
      );
      if (!win) {
        toast.warning(
          'レポートのタブを開けませんでした（ポップアップがブロックされています）。ブラウザでこのサイトのポップアップを許可して、もう一度押してください。',
        );
      }
    } catch (err) {
      win?.close();
      toast.error(`実現性チェックに失敗しました: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [mut, isoYear, isoWeek, officeId]);

  if (!canEdit) return null;

  return (
    <div className="flex items-center gap-1.5" data-testid="feasibility-check">
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => void run()}
        disabled={mut.isPending}
        aria-label="実現性チェック (移動・重なり・バッファを判定して印刷用レポートを開く)"
        title="移動時間・重なり・バッファ・同住所ルールを判定し、A4 のレポートを新しいタブに開きます（予定は変更しません）"
      >
        {mut.isPending ? '判定中…' : '実現性チェック'}
      </Button>
      {last ? (
        <span
          className="tnum rounded border border-border-default px-1.5 py-0.5 text-[11px] text-text-secondary"
          data-testid="feasibility-check-badge"
          title={summarize(last)}
        >
          <span className={last.hard_count > 0 ? 'font-semibold text-red-600' : ''}>❗{last.hard_count}</span>
          <span className="mx-1 text-text-muted">/</span>
          <span className={last.soft_count > 0 ? 'font-semibold text-amber-700' : ''}>△{last.soft_count}</span>
        </span>
      ) : null}
    </div>
  );
}
