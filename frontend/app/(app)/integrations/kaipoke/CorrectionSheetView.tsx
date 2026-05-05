'use client';

/**
 * CorrectionSheetView — 連携センター差分プレビュー UI (Wave 4-A Phase C).
 *
 * Lists correction items with per-row include checkboxes, a comment
 * editor, and bulk select-all/deselect-all/include-only actions.
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import {
  useBulkUpdateItems,
  useCorrectionItems,
  useCorrectionSheet,
  useUpdateCorrectionItem,
  useStartApply,
} from '@/lib/queries/integrations';
import type { CorrectionItem } from '@/lib/schemas/integration';

interface Props {
  month?: string;
}

export function CorrectionSheetView({ month }: Props) {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

  const sheetQuery = useCorrectionSheet(month);
  const sheet = sheetQuery.data;
  const itemsQuery = useCorrectionItems(sheet?.id, { limit: 500 });
  const items = itemsQuery.data?.items ?? [];

  const updateOne = useUpdateCorrectionItem();
  const bulk = useBulkUpdateItems();
  const apply = useStartApply();

  const [filterAction, setFilterAction] = useState<string>('all');
  const visible = useMemo(
    () => (filterAction === 'all' ? items : items.filter((i) => i.action === filterAction)),
    [items, filterAction],
  );
  const includedCount = items.filter((i) => i.include).length;

  const allIds = visible.map((i) => i.id);
  const handleBulk = async (include: boolean) => {
    if (!sheet?.id || allIds.length === 0) return;
    await bulk.mutateAsync({ sheetId: sheet.id, ids: allIds, patch: { include } });
  };

  const handleApply = async (dryRun: boolean) => {
    if (!sheet?.id) return;
    await apply.mutateAsync({ sheetId: sheet.id, dryRun });
  };

  if (sheetQuery.isLoading) {
    return (
      <Card className="p-4">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="mt-3 h-32 w-full" />
      </Card>
    );
  }
  if (sheetQuery.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>差分シートを取得できませんでした</AlertTitle>
        <AlertDescription>
          {sheetQuery.error instanceof Error
            ? sheetQuery.error.message
            : 'まだ差分が生成されていない可能性があります'}
        </AlertDescription>
      </Alert>
    );
  }
  if (!sheet) {
    return <p className="text-sm text-text-muted">差分シートはありません</p>;
  }

  return (
    <Card className="p-4">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-serif text-lg font-bold text-text-primary">
            差分シート — {sheet.target_month}
          </h3>
          <p className="text-xs text-text-secondary">
            状態: {sheet.status} / 全 {items.length} 件 / 取込対象 {includedCount} 件
          </p>
        </div>
        {isAdmin && (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleBulk(true)}
              disabled={bulk.isPending}
            >
              全選択
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleBulk(false)}
              disabled={bulk.isPending}
            >
              全解除
            </Button>
            <Button
              size="sm"
              onClick={() => handleApply(true)}
              disabled={apply.isPending || includedCount === 0}
            >
              適用 (dry-run)
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => handleApply(false)}
              disabled={apply.isPending || includedCount === 0}
            >
              適用 (本番)
            </Button>
          </div>
        )}
      </header>

      <div className="mb-3 flex flex-wrap gap-2 text-sm">
        {['all', 'add', 'delete', 'update', 'companion_change'].map((a) => (
          <button
            key={a}
            onClick={() => setFilterAction(a)}
            className={`rounded border px-2 py-1 ${
              filterAction === a
                ? 'border-brand-primary text-brand-primary'
                : 'border-border-default text-text-secondary'
            }`}
          >
            {a}
          </button>
        ))}
      </div>

      {itemsQuery.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : visible.length === 0 ? (
        <p className="text-sm text-text-muted">該当する差分はありません</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border-default text-left text-text-secondary">
              <tr>
                <th className="px-2 py-2 font-medium">取込</th>
                <th className="px-2 py-2 font-medium">action</th>
                <th className="px-2 py-2 font-medium">before / after</th>
                <th className="px-2 py-2 font-medium">コメント</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => (
                <CorrectionRow
                  key={item.id}
                  item={item}
                  isAdmin={Boolean(isAdmin)}
                  onUpdate={(patch) => updateOne.mutateAsync({ id: item.id, patch })}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(bulk.isError || apply.isError || updateOne.isError) && (
        <Alert variant="destructive" className="mt-3">
          <AlertTitle>更新に失敗しました</AlertTitle>
          <AlertDescription>
            {(bulk.error || apply.error || updateOne.error)?.message ?? 'エラー'}
          </AlertDescription>
        </Alert>
      )}
    </Card>
  );
}

interface RowProps {
  item: CorrectionItem;
  isAdmin: boolean;
  onUpdate: (patch: { include?: boolean; comment?: string | null }) => Promise<unknown>;
}

function CorrectionRow({ item, isAdmin, onUpdate }: RowProps) {
  const [comment, setComment] = useState(item.comment ?? '');

  return (
    <tr className="border-b border-border-default last:border-0">
      <td className="px-2 py-2 align-top">
        <input
          type="checkbox"
          checked={item.include}
          disabled={!isAdmin}
          onChange={(e) => onUpdate({ include: e.target.checked })}
        />
      </td>
      <td className="px-2 py-2 align-top text-xs uppercase tracking-wide text-text-secondary">
        {item.action}
      </td>
      <td className="px-2 py-2 align-top">
        <pre className="max-w-md overflow-x-auto whitespace-pre-wrap text-xs text-text-secondary">
          {JSON.stringify({ before: item.before ?? null, after: item.after ?? null }, null, 2)}
        </pre>
      </td>
      <td className="px-2 py-2 align-top">
        <Input
          value={comment}
          disabled={!isAdmin}
          onChange={(e) => setComment(e.target.value)}
          onBlur={() => {
            if ((item.comment ?? '') !== comment) {
              void onUpdate({ comment });
            }
          }}
          placeholder="メモ"
        />
      </td>
    </tr>
  );
}
