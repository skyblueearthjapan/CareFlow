/**
 * Special-week detail (Wave 3-E).
 *
 * Sections: ヘッダ情報 + 訪問明細テーブル.
 * Edit/Delete buttons gated by role (admin/manager edit, admin delete).
 */
'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useState } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import {
  useDeleteSpecialWeek,
  useSpecialWeek,
} from '@/lib/queries/special-weeks';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

export default function SpecialWeekDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canEdit = role === 'admin' || role === 'manager';
  const canDelete = role === 'admin';

  const { data, isLoading, isError, error } = useSpecialWeek(id);
  const deleteMutation = useDeleteSpecialWeek();
  const [confirmOpen, setConfirmOpen] = useState(false);

  if (isLoading) {
    return (
      <section className="space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </section>
    );
  }

  if (isError || !data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>取得に失敗しました</AlertTitle>
        <AlertDescription>
          {error instanceof Error ? error.message : '特別訪問週間を読み込めませんでした'}
        </AlertDescription>
      </Alert>
    );
  }

  const handleDelete = async () => {
    try {
      await deleteMutation.mutateAsync(id);
      toast.success('特別訪問週間を削除しました');
      router.push('/special-weeks');
    } catch (e) {
      toast.error(
        `削除に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`,
      );
    }
  };

  const items = data.items ?? [];

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-text-muted">
            <Link href="/special-weeks" className="hover:underline">
              ← 特別訪問週間一覧へ
            </Link>
          </p>
          <h1 className="font-serif text-2xl font-bold text-text-primary">
            {data.week_start} ～ {data.week_end}
            <span className="ml-2 text-sm text-text-secondary">
              ({data.apply_mode} / {data.status})
            </span>
          </h1>
        </div>
        <div className="flex gap-2">
          {canEdit ? (
            <Button asChild variant="outline">
              <Link href={`/special-weeks/${id}/edit`}>編集</Link>
            </Button>
          ) : null}
          {canDelete ? (
            <Button
              variant="destructive"
              onClick={() => setConfirmOpen(true)}
              disabled={deleteMutation.isPending}
            >
              削除
            </Button>
          ) : null}
        </div>
      </header>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">
          ヘッダ情報
        </h2>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm md:grid-cols-2">
          <DetailRow label="患者 ID" value={data.patient_id} mono />
          <DetailRow label="週開始日" value={data.week_start} />
          <DetailRow label="週終了日" value={data.week_end} />
          <DetailRow label="適用モード" value={data.apply_mode} />
          <DetailRow label="ステータス" value={data.status} />
          <DetailRow label="登録日時" value={data.registered_at} />
          <DetailRow
            label="理由"
            value={data.reason ?? '--'}
            className="md:col-span-2"
          />
        </dl>
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">
          訪問明細 ({items.length} 件)
        </h2>
        {items.length === 0 ? (
          <p className="text-sm text-text-muted">
            この特別週には明細が登録されていません。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">訪問日</th>
                  <th className="px-3 py-2 font-medium">曜日</th>
                  <th className="px-3 py-2 font-medium">タイプ</th>
                  <th className="px-3 py-2 font-medium">開始</th>
                  <th className="px-3 py-2 font-medium">終了</th>
                  <th className="px-3 py-2 font-medium">分</th>
                  <th className="px-3 py-2 font-medium">人数</th>
                  <th className="px-3 py-2 font-medium">個別変更</th>
                  <th className="px-3 py-2 font-medium">メモ</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr
                    key={it.id}
                    className="border-b border-border-default last:border-0"
                  >
                    <td className="px-3 py-2 tnum">{it.visit_date}</td>
                    <td className="px-3 py-2 text-text-secondary">
                      {WEEKDAY_LABELS[it.weekday] ?? it.weekday}
                    </td>
                    <td className="px-3 py-2 text-text-secondary">
                      {it.time_type}
                    </td>
                    <td className="px-3 py-2 tnum">{it.start_time ?? '--'}</td>
                    <td className="px-3 py-2 tnum">{it.end_time ?? '--'}</td>
                    <td className="px-3 py-2 tnum text-text-secondary">
                      {it.service_minutes}
                    </td>
                    <td className="px-3 py-2 tnum text-text-secondary">
                      {it.required_staff_count}
                    </td>
                    <td className="px-3 py-2 text-text-secondary">
                      {it.individual_change_handling ?? '--'}
                    </td>
                    <td className="px-3 py-2 text-text-secondary">
                      {it.note ?? '--'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="p-5 space-y-1 text-xs text-text-muted">
        <div>作成: {data.created_at}</div>
        <div>更新: {data.updated_at}</div>
      </Card>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>特別訪問週間を削除しますか？</DialogTitle>
            <DialogDescription>
              この週の明細もすべて削除されます。この操作は取り消せません。
            </DialogDescription>
          </DialogHeader>
          <p className="rounded-md border border-border-default bg-bg-muted/50 px-3 py-2 text-sm">
            {data.week_start} ～ {data.week_end} ({data.apply_mode})
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={deleteMutation.isPending}
            >
              キャンセル
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmOpen(false);
                void handleDelete();
              }}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? '削除中…' : '削除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

interface DetailRowProps {
  label: string;
  value: string;
  mono?: boolean;
  className?: string;
}

function DetailRow({ label, value, mono, className }: DetailRowProps) {
  return (
    <div
      className={`flex gap-3 border-b border-border-default/50 pb-2 ${className ?? ''}`}
    >
      <dt className="w-32 shrink-0 font-medium text-text-secondary">{label}</dt>
      <dd className={`text-text-primary ${mono ? 'font-mono text-xs' : ''}`}>
        {value}
      </dd>
    </div>
  );
}
