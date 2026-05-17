/**
 * Patient edit (Phase 3-4).
 *
 * Loads via usePatient(id), submits via useUpdatePatient(id) -> PATCH.
 * Role gate: admin/manager only.
 *
 * W10-FE3: EditPageStickyBar を追加し、未保存変更を画面下部で通知する。
 */
'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMemo, useRef, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
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
import { EditPageStickyBar } from '@/components/EditPageStickyBar';
import { useDeletePatient, usePatient, useUpdatePatient } from '@/lib/queries/patients';
import {
  patientReadToFormValues,
  type PatientFormValues,
  type WeeklyPattern,
} from '@/lib/schemas/patient';

import { PatientForm, type PatientFormHandle } from '../../_components/PatientForm';
import { PatientFixedVisitsPanel } from '../../_components/PatientFixedVisitsPanel';

export default function EditPatientPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const canEdit = role === 'admin' || role === 'manager';
  const canDelete = role === 'admin';

  const { data: patient, isLoading, isError, error } = usePatient(id);
  const initialFormValues = useMemo(
    () => (patient ? patientReadToFormValues(patient) : undefined),
    [patient],
  );
  const updateMutation = useUpdatePatient(id, initialFormValues);
  const deleteMutation = useDeletePatient();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  // sticky bar 用: フォームの isDirty 状態と外部操作ハンドル
  const [isFormDirty, setIsFormDirty] = useState(false);
  const formHandleRef = useRef<PatientFormHandle>(null);

  const handleDelete = async () => {
    try {
      await deleteMutation.mutateAsync(id);
      toast.success('患者を削除しました');
      router.push('/patients');
    } catch (e) {
      toast.error(`削除に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`);
    }
  };

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
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
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
        </div>
        {canDelete && !patient.deleted_at ? (
          <Button
            variant="destructive"
            onClick={() => setDeleteConfirmOpen(true)}
            disabled={deleteMutation.isPending}
          >
            削除
          </Button>
        ) : null}
      </header>

      <PatientForm
        defaultValues={defaults}
        onSubmit={handleSubmit}
        onCancel={() => router.push(`/patients/${id}`)}
        submitting={updateMutation.isPending}
        errorMessage={errorMessage}
        submitLabel="更新"
        onDirtyChange={setIsFormDirty}
        formRef={formHandleRef}
      />

      <PatientFixedVisitsPanel
        patientId={id}
        weeklyPattern={patient.weekly_pattern as WeeklyPattern | null | undefined}
        primaryOfficeId={patient.primary_office_id}
        requiresMultipleStaff={patient.requires_multiple_staff === true}
      />

      <EditPageStickyBar
        isDirty={isFormDirty}
        isSaving={updateMutation.isPending}
        errorMessage={errorMessage ?? undefined}
        onDiscard={() => formHandleRef.current?.resetForm()}
        onSave={() => formHandleRef.current?.submitForm()}
      />

      <Dialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>患者を削除しますか？</DialogTitle>
            <DialogDescription>
              以下の患者を削除します（ソフト削除）。この操作は取り消せます（管理者復旧）。
            </DialogDescription>
          </DialogHeader>
          <p className="rounded-md border border-border-default bg-bg-muted/50 px-3 py-2 text-sm">
            <span className="font-medium">{patient.name}</span>
            <span className="ml-2 text-text-muted tnum">({patient.code})</span>
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteConfirmOpen(false)}
              disabled={deleteMutation.isPending}
            >
              キャンセル
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setDeleteConfirmOpen(false);
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
