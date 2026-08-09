'use client';

/**
 * Staff master — create form (Phase 3-7 / W1-BE2 v2).
 *
 * react-hook-form is not yet a project dependency, so this form is hand-rolled
 * with React state and zod validation. Keep it close to the patterns we'd use
 * once react-hook-form lands so the future swap is mechanical.
 *
 * v2 (W1-BE2): backend は §4.2 の 9 項目のみ受け付ける (``extra="forbid"``)。
 */
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useCreateStaff } from '@/lib/queries/staff';
import {
  STAFF_ROLE_OPTIONS,
  STAFF_ROLE_VALUES,
  STAFF_SEX_VALUES,
  STAFF_STATUS_VALUES,
  roleLabel,
  sexLabel,
  staffCreateSchema,
  statusLabel,
  type StaffCreate,
} from '@/lib/schemas/staff';

import { StaffFormFields, type StaffFormState } from '../_components/StaffFormFields';

const EMPTY_FORM: StaffFormState = {
  code: '',
  name: '',
  kana: '',
  sex: '',
  status: 'active',
  role: 'staff',
  primary_office_id: '',
  is_trainee: false,
  note: '',
};

function toPayload(form: StaffFormState): StaffCreate {
  return {
    code: form.code.trim() || null,
    name: form.name.trim(),
    kana: form.kana.trim() || null,
    sex: form.sex === '' ? null : form.sex,
    status: form.status,
    role: form.role,
    primary_office_id: form.primary_office_id.trim() || null,
    is_trainee: form.is_trainee,
    note: form.note.trim() || null,
  };
}

export default function StaffNewPage() {
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;

  // Backend rejects POST /staff for the staff role with 403; redirect early
  // so the form never renders for an unauthorised user.
  useEffect(() => {
    if (status === 'authenticated' && role === 'staff') {
      router.replace('/dashboard');
    }
  }, [status, role, router]);

  const [form, setForm] = useState<StaffFormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const create = useCreateStaff({
    onSuccess: (data) => {
      router.push(`/staff/${data.id}`);
    },
  });

  if (status === 'authenticated' && role === 'staff') {
    return null;
  }

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErrors({});
    const payload = toPayload(form);
    const parsed = staffCreateSchema.safeParse(payload);
    if (!parsed.success) {
      const fieldErrors: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0]?.toString() ?? '_';
        if (!fieldErrors[key]) fieldErrors[key] = issue.message;
      }
      setErrors(fieldErrors);
      return;
    }
    create.mutate(parsed.data);
  };

  return (
    <section className="space-y-4">
      <header className="flex items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link href="/staff">
            <ArrowLeft className="h-4 w-4" />
            一覧へ
          </Link>
        </Button>
        <RakusukeTitle pose="clap" title="新規スタッフ" />
      </header>

      {create.isError && (
        <Alert variant="destructive">
          <AlertTitle>登録に失敗しました</AlertTitle>
          <AlertDescription>
            {create.error instanceof Error ? create.error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>基本情報</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <StaffFormFields
              form={form}
              errors={errors}
              onChange={setForm}
              sexOptions={STAFF_SEX_VALUES.map((v) => ({ value: v, label: sexLabel(v) }))}
              roleOptions={STAFF_ROLE_OPTIONS.map((v) => ({ value: v, label: roleLabel(v) }))}
              statusOptions={STAFF_STATUS_VALUES.map((v) => ({
                value: v,
                label: statusLabel(v),
              }))}
            />

            <div className="flex justify-end gap-3 border-t border-border-default pt-4">
              <Button asChild variant="outline">
                <Link href="/staff">キャンセル</Link>
              </Button>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? '登録中…' : '登録する'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </section>
  );
}
