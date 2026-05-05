'use client';

/**
 * Staff master — edit form (Phase 3-7).
 *
 * Mirrors `../../new/page.tsx` but seeds the form from useStaff() and calls
 * useUpdateStaff with PATCH semantics.
 */
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useStaff, useUpdateStaff } from '@/lib/queries/staff';
import {
  STAFF_ROLE_VALUES,
  STAFF_SEX_VALUES,
  STAFF_STATUS_VALUES,
  roleLabel,
  sexLabel,
  staffUpdateSchema,
  statusLabel,
  type StaffRead,
  type StaffUpdate,
} from '@/lib/schemas/staff';

import { StaffFormFields, type StaffFormState } from '../../_components/StaffFormFields';

function fromStaff(staff: StaffRead): StaffFormState {
  return {
    code: staff.code ?? '',
    name: staff.name,
    kana: staff.kana ?? '',
    sex: staff.sex ?? '',
    status: staff.status,
    role: staff.role,
    primary_office_id: staff.primary_office_id ?? '',
    can_double_team: staff.can_double_team,
    mentor_id: staff.mentor_id ?? '',
    home_address: staff.home_address ?? '',
    home_lat:
      staff.home_lat !== undefined && staff.home_lat !== null
        ? String(staff.home_lat)
        : '',
    home_lng:
      staff.home_lng !== undefined && staff.home_lng !== null
        ? String(staff.home_lng)
        : '',
    areas: (staff.areas ?? []).join(', '),
    max_per_day: String(staff.max_per_day ?? 6),
    skill_level: staff.skill_level ?? '',
    assignment_volume: staff.assignment_volume ?? '',
    note: staff.note ?? '',
  };
}

/** Split a comma-separated string into trimmed, non-empty area codes. */
function parseAreas(value: string): string[] {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function toPayload(form: StaffFormState): StaffUpdate {
  return {
    code: form.code.trim() || null,
    name: form.name.trim(),
    kana: form.kana.trim() || null,
    sex: form.sex === '' ? null : form.sex,
    status: form.status,
    role: form.role,
    primary_office_id: form.primary_office_id.trim() || null,
    can_double_team: form.can_double_team,
    mentor_id: form.mentor_id.trim() || null,
    home_address: form.home_address.trim() || null,
    home_lat: form.home_lat.trim() === '' ? undefined : Number(form.home_lat),
    home_lng: form.home_lng.trim() === '' ? undefined : Number(form.home_lng),
    areas: parseAreas(form.areas),
    max_per_day: form.max_per_day.trim() === '' ? undefined : Number(form.max_per_day),
    skill_level: form.skill_level === '' ? null : form.skill_level,
    assignment_volume: form.assignment_volume === '' ? null : form.assignment_volume,
    note: form.note.trim() || null,
  };
}

export default function StaffEditPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  // Route guard: backend PATCH /staff/{id} is admin/manager only, so staff
  // users must be redirected back to the detail view rather than rendering a
  // form they cannot submit.
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const isPrivileged = role === 'admin' || role === 'manager';
  const denied = status === 'authenticated' && !isPrivileged;

  useEffect(() => {
    if (denied && id) {
      router.replace(`/staff/${id}`);
    }
  }, [denied, id, router]);

  const { data, isLoading, isError, error } = useStaff(id);

  const [form, setForm] = useState<StaffFormState | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (data && form === null) {
      setForm(fromStaff(data));
    }
  }, [data, form]);

  const update = useUpdateStaff({
    onSuccess: (saved) => {
      router.push(`/staff/${saved.id}`);
    },
  });

  if (!id) return null;

  // Show a skeleton while session is resolving or while we are redirecting an
  // unauthorized staff user away from this route.
  if (status === 'loading' || denied) {
    return (
      <section className="space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </section>
    );
  }

  if (isLoading || form === null) {
    return (
      <section className="space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </section>
    );
  }

  if (isError) {
    return (
      <section className="space-y-4">
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      </section>
    );
  }

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErrors({});
    const payload = toPayload(form);
    const parsed = staffUpdateSchema.safeParse(payload);
    if (!parsed.success) {
      const fieldErrors: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0]?.toString() ?? '_';
        if (!fieldErrors[key]) fieldErrors[key] = issue.message;
      }
      setErrors(fieldErrors);
      return;
    }
    update.mutate({ id, payload: parsed.data });
  };

  return (
    <section className="space-y-4">
      <header className="flex items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link href={`/staff/${id}`}>
            <ArrowLeft className="h-4 w-4" />
            詳細へ
          </Link>
        </Button>
        <h1 className="font-serif text-2xl font-bold text-text-primary">
          スタッフ編集 — {data?.name}
        </h1>
      </header>

      {update.isError && (
        <Alert variant="destructive">
          <AlertTitle>更新に失敗しました</AlertTitle>
          <AlertDescription>
            {update.error instanceof Error ? update.error.message : '不明なエラー'}
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
              roleOptions={STAFF_ROLE_VALUES.map((v) => ({ value: v, label: roleLabel(v) }))}
              statusOptions={STAFF_STATUS_VALUES.map((v) => ({
                value: v,
                label: statusLabel(v),
              }))}
            />

            <div className="flex justify-end gap-3 border-t border-border-default pt-4">
              <Button asChild variant="outline">
                <Link href={`/staff/${id}`}>キャンセル</Link>
              </Button>
              <Button type="submit" disabled={update.isPending}>
                {update.isPending ? '保存中…' : '保存する'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </section>
  );
}
