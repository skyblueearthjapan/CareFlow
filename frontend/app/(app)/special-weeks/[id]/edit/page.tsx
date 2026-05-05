/**
 * Special-week edit (Wave 3-E).
 *
 * Loads via useSpecialWeek(id), submits via useUpdateSpecialWeek(id) -> PATCH.
 * Role gate: admin / manager.
 */
'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import {
  useSpecialWeek,
  useUpdateSpecialWeek,
} from '@/lib/queries/special-weeks';
import {
  specialWeekReadToFormValues,
  type SpecialWeekFormValues,
} from '@/lib/schemas/special-week';

import { SpecialWeekForm } from '../../_components/SpecialWeekForm';

export default function EditSpecialWeekPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const canEdit = role === 'admin' || role === 'manager';

  const { data: sw, isLoading, isError, error } = useSpecialWeek(id);
  const updateMutation = useUpdateSpecialWeek(id);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const initialFormValues = useMemo(
    () => (sw ? specialWeekReadToFormValues(sw) : undefined),
    [sw],
  );

  const handleSubmit = useMemo(
    () => async (values: SpecialWeekFormValues) => {
      setErrorMessage(null);
      try {
        await updateMutation.mutateAsync(values);
        toast.success('特別訪問週間を更新しました');
        router.push(`/special-weeks/${id}`);
      } catch (e) {
        const msg = e instanceof Error ? e.message : '不明なエラー';
        setErrorMessage(msg);
        toast.error(`更新に失敗しました: ${msg}`);
      }
    },
    [updateMutation, router, id],
  );

  if (status === 'loading' || isLoading) {
    return (
      <section className="space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </section>
    );
  }

  if (!canEdit) {
    return (
      <Alert variant="destructive">
        <AlertTitle>権限がありません</AlertTitle>
        <AlertDescription>
          特別訪問週間の編集は管理者またはマネージャーのみ実行できます。
        </AlertDescription>
      </Alert>
    );
  }

  if (isError || !sw || !initialFormValues) {
    return (
      <Alert variant="destructive">
        <AlertTitle>取得に失敗しました</AlertTitle>
        <AlertDescription>
          {error instanceof Error ? error.message : '特別訪問週間を読み込めませんでした'}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <section className="space-y-4">
      <header>
        <p className="text-sm text-text-muted">
          <Link href={`/special-weeks/${id}`} className="hover:underline">
            ← 詳細へ
          </Link>
        </p>
        <h1 className="font-serif text-2xl font-bold text-text-primary">
          特別訪問週間 編集
          <span className="ml-2 text-base font-normal text-text-secondary">
            {sw.week_start} ～ {sw.week_end}
          </span>
        </h1>
      </header>

      <SpecialWeekForm
        defaultValues={initialFormValues}
        onSubmit={handleSubmit}
        onCancel={() => router.push(`/special-weeks/${id}`)}
        submitting={updateMutation.isPending}
        errorMessage={errorMessage}
        submitLabel="更新"
        lockPatient
      />
    </section>
  );
}
