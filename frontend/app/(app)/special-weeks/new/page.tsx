/**
 * Special-week create (Wave 3-E).
 *
 * Role gate: admin / manager. POST /api/v1/special-weeks → /special-weeks/{id}.
 */
'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { toast } from '@/components/ui/sonner';
import { useCreateSpecialWeek } from '@/lib/queries/special-weeks';
import type { SpecialWeekFormValues } from '@/lib/schemas/special-week';

import { SpecialWeekForm } from '../_components/SpecialWeekForm';

export default function NewSpecialWeekPage() {
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const canCreate = role === 'admin' || role === 'manager';

  const createMutation = useCreateSpecialWeek();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = useMemo(
    () => async (values: SpecialWeekFormValues) => {
      setErrorMessage(null);
      try {
        const created = await createMutation.mutateAsync(values);
        toast.success('特別訪問週間を登録しました');
        router.push(`/special-weeks/${created.id}`);
      } catch (e) {
        const msg = e instanceof Error ? e.message : '不明なエラー';
        setErrorMessage(msg);
        toast.error(`登録に失敗しました: ${msg}`);
      }
    },
    [createMutation, router],
  );

  if (status === 'loading') {
    return <p className="text-sm text-text-muted">読み込み中…</p>;
  }

  if (!canCreate) {
    return (
      <Alert variant="destructive">
        <AlertTitle>権限がありません</AlertTitle>
        <AlertDescription>
          特別訪問週間の登録は管理者またはマネージャーのみ実行できます。
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <section className="space-y-4">
      <header>
        <p className="text-sm text-text-muted">
          <Link href="/special-weeks" className="hover:underline">
            ← 特別訪問週間一覧へ
          </Link>
        </p>
        <h1 className="font-serif text-2xl font-bold text-text-primary">
          特別訪問週間 新規登録
        </h1>
      </header>

      <SpecialWeekForm
        onSubmit={handleSubmit}
        onCancel={() => router.push('/special-weeks')}
        submitting={createMutation.isPending}
        errorMessage={errorMessage}
        submitLabel="登録"
      />
    </section>
  );
}
