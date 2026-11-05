/**
 * Patient create (Phase 3-3).
 *
 * Role gate: admin/manager only. POST /api/v1/patients → /patients/{id}.
 * 連絡先 (管理者だけ) は作成の後に PUT /patients/{id}/contacts。連絡先だけ失敗したら
 * 画面に残して「連絡先をもう一度保存する」で送り直す。もう一度「登録」を押したときは、
 * 患者の欄の変更を PATCH (編集画面と同じ useUpdatePatient) してから連絡先を送る。
 */
'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { toast } from '@/components/ui/sonner';
import { useCreatePatient, useUpdatePatient } from '@/lib/queries/patients';
import type { PatientFormValues } from '@/lib/schemas/patient';

import { PatientForm } from '../_components/PatientForm';
import { usePatientContactsEditor } from '@/components/patients/usePatientContactsEditor';
import { isAdminRole } from '@/lib/rbac';

export default function NewPatientPage() {
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const canCreate = isAdminRole(role);

  const createMutation = useCreatePatient();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const contacts = usePatientContactsEditor({ enabled: canCreate });
  // 患者は作れたが連絡先だけ失敗したとき: もう一度作らず、患者の欄を PATCH してから連絡先を送る。
  // values は最後に保存できた患者の欄 (PATCH の差分の元)。
  const [created, setCreated] = useState<{ id: string; values: PatientFormValues } | null>(null);
  const createdId = created?.id ?? null;
  const updateMutation = useUpdatePatient(createdId ?? '', created?.values);

  const handleSubmit = useMemo(
    () => async (values: PatientFormValues) => {
      setErrorMessage(null);
      if (!contacts.validate()) {
        toast.error('連絡先の電話番号・メールアドレスを確かめてください');
        return;
      }
      if (createdId) {
        try {
          await updateMutation.mutateAsync(values);
          setCreated({ id: createdId, values });
        } catch (e) {
          const msg = e instanceof Error ? e.message : '不明なエラー';
          setErrorMessage(msg);
          toast.error(`保存に失敗しました: ${msg}`);
          return;
        }
        if (await contacts.saveTo(createdId)) router.push(`/patients/${createdId}`);
        return;
      }
      try {
        const res = await createMutation.mutateAsync(values);
        toast.success('患者を登録しました');
        setCreated({ id: res.id, values });
        if (!(await contacts.saveTo(res.id))) return;
        router.push(`/patients/${res.id}`);
      } catch (e) {
        const msg = e instanceof Error ? e.message : '不明なエラー';
        setErrorMessage(msg);
        toast.error(`登録に失敗しました: ${msg}`);
      }
    },
    [createMutation, updateMutation, router, contacts, createdId],
  );

  const retryContacts = async () => {
    if (createdId && contacts.validate() && (await contacts.saveTo(createdId)))
      router.push(`/patients/${createdId}`);
  };

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
        <RakusukeTitle pose="heart" title="患者 新規登録" />
      </header>

      <PatientForm
        onSubmit={handleSubmit}
        onCancel={() => router.push('/patients')}
        submitting={createMutation.isPending || updateMutation.isPending}
        errorMessage={errorMessage}
        submitLabel="登録"
        contacts={
          contacts.enabled
            ? {
                value: contacts.drafts,
                onChange: contacts.setDrafts,
                errorMessage: contacts.error,
                onRetry: createdId && contacts.failed ? () => void retryContacts() : undefined,
                retrying: contacts.saving,
              }
            : undefined
        }
      />
    </section>
  );
}
