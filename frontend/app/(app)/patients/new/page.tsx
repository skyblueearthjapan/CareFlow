/**
 * Patient create (Phase 3-3).
 *
 * Role gate: admin/manager only. POST /api/v1/patients → /patients/{id}.
 */
'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useCreatePatient } from '@/lib/queries/patients';
import type { PatientFormValues } from '@/lib/schemas/patient';

import { PatientForm } from '../_components/PatientForm';

export default function NewPatientPage() {
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const canCreate = role === 'admin' || role === 'manager';

  const createMutation = useCreatePatient();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = useMemo(
    () => async (values: PatientFormValues) => {
      setErrorMessage(null);
      try {
        const created = await createMutation.mutateAsync(values);
        router.push(`/patients/${created.id}`);
      } catch (e) {
        const msg = e instanceof Error ? e.message : '不明なエラー';
        setErrorMessage(msg);
        // eslint-disable-next-line no-alert -- Phase 4 で正規 Toast 統合予定
        alert(`登録に失敗しました: ${msg}`);
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
          患者の新規登録は管理者またはマネージャーのみ実行できます。
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <section className="space-y-4">
      <header>
        <p className="text-sm text-text-muted">
          <Link href="/patients" className="hover:underline">
            ← 患者一覧へ
          </Link>
        </p>
        <h1 className="font-serif text-2xl font-bold text-text-primary">患者 新規登録</h1>
      </header>

      <PatientForm
        onSubmit={handleSubmit}
        onCancel={() => router.push('/patients')}
        submitting={createMutation.isPending}
        errorMessage={errorMessage}
        submitLabel="登録"
      />
    </section>
  );
}
