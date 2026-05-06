/**
 * Patient edit (Phase 3-4).
 *
 * Loads via usePatient(id), submits via useUpdatePatient(id) -> PATCH.
 * Role gate: admin/manager only.
 */
'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { usePatient, useUpdatePatient } from '@/lib/queries/patients';
import {
  patientReadToFormValues,
  type PatientFormValues,
  type WeeklyPattern,
} from '@/lib/schemas/patient';

import { PatientForm } from '../../_components/PatientForm';
import { PatientFixedVisitsPanel } from '../../_components/PatientFixedVisitsPanel';

export default function EditPatientPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const canEdit = role === 'admin' || role === 'manager';

  const { data: patient, isLoading, isError, error } = usePatient(id);
  const initialFormValues = useMemo(
    () => (patient ? patientReadToFormValues(patient) : undefined),
    [patient],
  );
  const updateMutation = useUpdatePatient(id, initialFormValues);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = useMemo(
    () => async (values: PatientFormValues) => {
      setErrorMessage(null);
      try {
        await updateMutation.mutateAsync(values);
        toast.success('患者情報を更新しました');
        router.push(`/patients/${id}`);
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
        <AlertDescription>患者の編集は管理者またはマネージャーのみ実行できます。</AlertDescription>
      </Alert>
    );
  }

  if (isError || !patient) {
    return (
      <Alert variant="destructive">
        <AlertTitle>取得に失敗しました</AlertTitle>
        <AlertDescription>
          {error instanceof Error ? error.message : '患者情報を読み込めませんでした'}
        </AlertDescription>
      </Alert>
    );
  }

  const defaults = initialFormValues ?? patientReadToFormValues(patient);

  return (
    <section className="space-y-4">
      <header>
        <p className="text-sm text-text-muted">
          <Link href={`/patients/${id}`} className="hover:underline">
            ← 患者詳細へ
          </Link>
        </p>
        <h1 className="font-serif text-2xl font-bold text-text-primary">
          患者編集
          <span className="ml-2 text-base font-normal text-text-secondary">
            {patient.name} ({patient.code})
          </span>
        </h1>
      </header>

      <PatientForm
        defaultValues={defaults}
        onSubmit={handleSubmit}
        onCancel={() => router.push(`/patients/${id}`)}
        submitting={updateMutation.isPending}
        errorMessage={errorMessage}
        submitLabel="更新"
      />

      <PatientFixedVisitsPanel
        patientId={id}
        weeklyPattern={patient.weekly_pattern as WeeklyPattern | null | undefined}
      />
    </section>
  );
}
