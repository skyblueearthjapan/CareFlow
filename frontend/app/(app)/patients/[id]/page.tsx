/**
 * Patient detail (Phase 3-2 + 3-7 delete confirm).
 *
 * Sections: 基本情報 / 連絡先 / 保険 / 訪問条件 / 備考.
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
import { Skeleton } from '@/components/ui/skeleton';
import { useDeletePatient, usePatient } from '@/lib/queries/patients';
import type { PatientRead } from '@/lib/schemas/patient';

export default function PatientDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canEdit = role === 'admin' || role === 'manager';
  const canDelete = role === 'admin';

  const { data, isLoading, isError, error } = usePatient(id);
  const deleteMutation = useDeletePatient();
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
          {error instanceof Error ? error.message : '患者情報を読み込めませんでした'}
        </AlertDescription>
      </Alert>
    );
  }

  const handleDelete = async () => {
    try {
      await deleteMutation.mutateAsync(id);
      router.push('/patients');
    } catch (e) {
      // eslint-disable-next-line no-alert -- Phase 4 で正規 Toast 統合予定
      alert(`削除に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`);
    }
  };

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-text-muted">
            <Link href="/patients" className="hover:underline">
              ← 患者一覧へ
            </Link>
          </p>
          <h1 className="font-serif text-2xl font-bold text-text-primary">
            {data.name}
            <span className="ml-2 text-sm text-text-secondary tnum">({data.code})</span>
          </h1>
          {data.deleted_at ? (
            <span className="inline-block rounded bg-error/10 px-2 py-0.5 text-xs text-error">
              削除済 ({data.deleted_at})
            </span>
          ) : null}
        </div>
        <div className="flex gap-2">
          {canEdit && !data.deleted_at ? (
            <Button asChild variant="outline">
              <Link href={`/patients/${id}/edit`}>編集</Link>
            </Button>
          ) : null}
          {canDelete && !data.deleted_at ? (
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
        <h2 className="font-serif text-lg font-bold text-text-primary">基本情報</h2>
        <DetailGrid
          rows={[
            ['コード', data.code],
            ['氏名', data.name],
            ['カナ', data.kana ?? '--'],
            ['性別', data.sex ?? '--'],
            ['年齢', data.age != null ? String(data.age) : '--'],
            ['状態', data.status === 'active' ? '有効' : '無効'],
          ]}
        />
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">連絡先</h2>
        <DetailGrid
          rows={[
            ['住所', data.address ?? '--'],
            ['緯度', data.lat != null ? String(data.lat) : '--'],
            ['経度', data.lng != null ? String(data.lng) : '--'],
          ]}
        />
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">保険・拠点</h2>
        <DetailGrid
          rows={[
            ['保険区分', data.insurance ?? '--'],
            ['主担当拠点 ID', data.primary_office_id ?? '--'],
          ]}
        />
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">訪問条件</h2>
        <DetailGrid
          rows={[
            ['必要スタッフ数', String(data.required_staff_count ?? 1)],
            ['性別制限', data.sex_restriction ?? '--'],
            ['NG時間 (開始)', data.ng_time_start ?? '--'],
            ['NG時間 (終了)', data.ng_time_end ?? '--'],
            ['週間訪問パターン', data.weekly_pattern ?? '--'],
            ['特別週', data.special_week ? '有効' : '--'],
          ]}
        />
        {/* TODO(Wave 2): NG スタッフ / 同行希望スタッフ 表示 */}
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">備考</h2>
        <p className="whitespace-pre-wrap text-sm text-text-primary">
          {data.note ?? '--'}
        </p>
      </Card>

      <Card className="p-5 space-y-1 text-xs text-text-muted">
        <div>作成: {data.created_at}</div>
        <div>更新: {data.updated_at}</div>
      </Card>

      {confirmOpen ? (
        <DeleteConfirmDialog
          patient={data}
          submitting={deleteMutation.isPending}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => {
            setConfirmOpen(false);
            void handleDelete();
          }}
        />
      ) : null}
    </section>
  );
}

interface DetailGridProps {
  rows: ReadonlyArray<readonly [string, string]>;
}

function DetailGrid({ rows }: DetailGridProps) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm md:grid-cols-2">
      {rows.map(([k, v]) => (
        <div key={k} className="flex gap-3 border-b border-border-default/50 pb-2">
          <dt className="w-32 shrink-0 font-medium text-text-secondary">{k}</dt>
          <dd className="text-text-primary">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

interface DeleteConfirmDialogProps {
  patient: PatientRead;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

function DeleteConfirmDialog({
  patient,
  submitting,
  onCancel,
  onConfirm,
}: DeleteConfirmDialogProps) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-confirm-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onCancel}
    >
      <Card
        className="w-full max-w-md p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="delete-confirm-title" className="font-serif text-lg font-bold text-text-primary">
          患者を削除しますか？
        </h3>
        <p className="text-sm text-text-secondary">
          以下の患者を削除します（ソフト削除）。この操作は取り消せます（管理者復旧）。
        </p>
        <p className="rounded-md border border-border-default bg-bg-muted/50 px-3 py-2 text-sm">
          <span className="font-medium">{patient.name}</span>
          <span className="ml-2 text-text-muted tnum">({patient.code})</span>
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel} disabled={submitting}>
            キャンセル
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={submitting}
          >
            {submitting ? '削除中…' : '削除'}
          </Button>
        </div>
      </Card>
    </div>
  );
}
