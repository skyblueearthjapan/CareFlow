'use client';
/**
 * 「突合レポート」ボタン — らく助×カイポケの週突合を A4 の独立 HTML で新しいタブに開く
 * (read-only・PO 要望 2026-09-01「差分確認/突き合わせの際に HTML が出てくるとらく」)。
 *
 * - カイポケ側は保存済みの最新スナップショット (RPA は回さない・即応答)。
 *   鮮度はレポート冒頭に取得時刻として明示される。
 * - `window.open` は click ハンドラ内で同期的に呼ぶ (ポップアップブロック回避)。
 *   'noopener' を features に付けると window.open が null を返すため付けず、
 *   遷移後に win.opener = null で切り離す (FeasibilityCheckButton と同じ・レビュー NEW-1)。
 */
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { useStartDiffLocal } from '@/lib/queries/integrations';
import { useReconcileReport, type ReconcileReport } from '@/lib/queries/reconcileReport';

/**
 * 突合レポートの区分キー「非稼働患者」(BE `reconcile_report_html` と同じ文字列)。
 * らく助側が稼働中でない患者に対応するカイポケの行 = 削除候補 (§3-5)。
 */
const INACTIVE_PATIENT_KEY = '非稼働患者';

export interface ReconcileReportButtonProps {
  /** 週の月曜 (YYYY-MM-DD)。 */
  weekStart: string;
  /** 月曜から何日分 (既定 7)。 */
  days?: number;
  /** admin のみ表示 (RBAC)。 */
  canEdit: boolean;
  /** ボタンの見た目 (置き場所に合わせる)。 */
  size?: 'sm' | 'md';
}

export function ReconcileReportButton({
  weekStart,
  days,
  canEdit,
  size = 'sm',
}: ReconcileReportButtonProps) {
  const mut = useReconcileReport();
  const [last, setLast] = useState<ReconcileReport | null>(null);

  const run = useCallback(async () => {
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    try {
      const r = await mut.mutateAsync({ weekStart, days });
      setLast(r);
      if (win && r.html) {
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
      const ng =
        (r.counts['相違'] ?? 0) + (r.counts['らく助のみ'] ?? 0) + (r.counts['カイポケのみ'] ?? 0);
      // 非稼働患者の行 (患者ステータス連動・design 2026-09-09 §3-5)。
      // らく助が稼働中でない患者に対応するカイポケの行 = 削除候補。
      const inactive = r.counts[INACTIVE_PATIENT_KEY] ?? 0;
      toast[ng > 0 || inactive > 0 ? 'warning' : 'success'](
        `突合レポート: 一致 ${r.counts['一致'] ?? 0} / 相違系 ${ng} 件（全 ${r.total} 件）` +
          (inactive > 0 ? ` ／ ${INACTIVE_PATIENT_KEY}の行 ${inactive} 件（削除候補）` : ''),
      );
    } catch (e) {
      win?.close();
      toast.error(
        `突合レポートの生成に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`,
      );
    }
  }, [mut, weekStart, days]);

  if (!canEdit) return null;

  const ngCount = last
    ? (last.counts['相違'] ?? 0) +
      (last.counts['らく助のみ'] ?? 0) +
      (last.counts['カイポケのみ'] ?? 0)
    : null;
  const inactiveCount = last ? (last.counts[INACTIVE_PATIENT_KEY] ?? 0) : 0;

  return (
    <span className="inline-flex items-center gap-1.5">
      <Button
        type="button"
        variant="outline"
        size={size}
        onClick={run}
        disabled={mut.isPending}
        title="らく助×カイポケの週突合を印刷用 HTML で開く（カイポケ側は保存済みスナップショット・read-only）"
        data-testid="reconcile-report-button"
      >
        {mut.isPending ? '突合中…' : '🔍 突合レポート'}
      </Button>
      {ngCount != null ? (
        <span
          className={
            ngCount > 0
              ? 'rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-bold text-red-700'
              : 'rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700'
          }
          data-testid="reconcile-report-badge"
        >
          {ngCount > 0 ? `相違 ${ngCount}` : '全一致'}
        </span>
      ) : null}
      {inactiveCount > 0 ? (
        <span
          className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800"
          title="らく助では稼働中でない患者様の行です（カイポケ側の削除候補）"
          data-testid="reconcile-report-inactive-badge"
        >
          {INACTIVE_PATIENT_KEY} {inactiveCount}
        </span>
      ) : null}
    </span>
  );
}

/** 「差分最新化」— カイポケから最新 CSV を取得してスナップショットを更新する
 * (連携ページの「この週の差分を計算」と同一処理・約1分・RPA 使用)。
 * PO 要望 (2026-09-01): 最新化 → 突合レポート の流れにしたい。 */
export function ReconcileRefreshButton({
  weekStart,
  canEdit,
  size = 'sm',
}: {
  weekStart: string;
  canEdit: boolean;
  size?: 'sm' | 'md';
}) {
  const diffLocal = useStartDiffLocal();

  const run = useCallback(async () => {
    try {
      const start = new Date(`${weekStart}T00:00:00`);
      const end = new Date(start);
      end.setDate(end.getDate() + 6);
      const ymd = (d: Date) =>
        `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
          d.getDate(),
        ).padStart(2, '0')}`;
      const res = await diffLocal.mutateAsync({
        month: weekStart.slice(0, 7),
        weekStart,
        weekEnd: ymd(end),
      });
      const s = (res.summary ?? {}) as Record<string, number>;
      const total = s.total ?? 0;
      toast.success(
        `差分最新化が完了しました（差分 ${total} 件）。「🔍 突合レポート」で最新の突合を開けます。`,
      );
    } catch (e) {
      toast.error(`差分最新化に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`);
    }
  }, [diffLocal, weekStart]);

  if (!canEdit) return null;
  return (
    <Button
      type="button"
      variant="outline"
      size={size}
      onClick={run}
      disabled={diffLocal.isPending}
      title="カイポケから最新CSVを取得してスナップショットを更新します（約1分・連携ページの差分計算と同じ）"
      data-testid="reconcile-refresh-button"
    >
      {diffLocal.isPending ? 'カイポケ取得中…約1分' : '🔄 差分最新化'}
    </Button>
  );
}

/** 「差分最新化 → 突合レポート」の 2 ボタンを並べたツールバー (盤面用)。 */
export function ReconcileToolbar({
  weekStart,
  canEdit,
  size = 'sm',
}: {
  weekStart: string;
  canEdit: boolean;
  size?: 'sm' | 'md';
}) {
  if (!canEdit) return null;
  return (
    <span className="inline-flex items-center gap-1.5">
      <ReconcileRefreshButton weekStart={weekStart} canEdit={canEdit} size={size} />
      <ReconcileReportButton weekStart={weekStart} canEdit={canEdit} size={size} />
    </span>
  );
}
