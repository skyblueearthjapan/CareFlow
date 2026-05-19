'use client';

/**
 * Staff master — edit form (W10-FE1 Phase 3: 全部載せ).
 *
 * 5 セクション構成:
 *   ① 基本情報  (PATCH /staff/{id})
 *   ② 週間シフト (PUT /staff/{id}/shifts — ShiftsEditDialog 流用)
 *   ③ オーバーライド (today+90 日)
 *   ④ イベント
 *   ⑤ 同行スタッフ割付 (is_trainee=true 時のみ — StaffCompanionPanel)
 *
 * 患者編集ページと同じ流派:
 *   上半分 = 1 form 一括 PATCH (基本情報)
 *   下半分 = 独立 PUT/POST/DELETE (シフト/オーバーライド/イベント/同行割付)
 *
 * is_trainee を OFF にした時:
 *   確認ダイアログ → 同行スタッフ割付を DELETE してから PATCH。
 *
 * メンター → 同行スタッフ ラベル変更 (W10-FE1 §4)。
 */
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Pencil, Plus, Trash2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { RecordNavigator, type NavigatorRecord } from '@/components/RecordNavigator';
import { useStaffEvents } from '@/lib/queries/staff-events';
import { useStaffOverrides, type OverrideRange } from '@/lib/queries/staff-overrides';
import { useStaffShifts } from '@/lib/queries/staff-shifts';
import { useDeleteCompanionAssignments } from '@/lib/queries/staff_companion';
import { useDeleteStaff, useStaff, useStaffList, useUpdateStaff } from '@/lib/queries/staff';
import type { OverrideRead } from '@/lib/schemas/staff-overrides';
import {
  STAFF_ROLE_VALUES,
  STAFF_SEX_VALUES,
  STAFF_STATUS_VALUES,
  WEEKDAY_LABELS,
  normalizeStaffSex,
  normalizeStaffStatus,
  roleLabel,
  sexLabel,
  staffUpdateSchema,
  statusLabel,
  type StaffRead,
  type StaffUpdate,
} from '@/lib/schemas/staff';
import type { StaffShiftItem } from '@/lib/schemas/staff-shifts';
import type { EventRead } from '@/lib/schemas/staff-events';

import { DeleteConfirmModal } from '../../_components/DeleteConfirmModal';
import { StaffCompanionPanel } from '../../_components/StaffCompanionPanel';
import { StaffFormFields, type StaffFormState } from '../../_components/StaffFormFields';
import { EventAddDialog } from '../_components/EventAddDialog';
import { EventEditDialog } from '../_components/EventEditDialog';
import { OverrideAddDialog } from '../_components/OverrideAddDialog';
import { OverrideEditDialog } from '../_components/OverrideEditDialog';
import { ShiftsEditDialog } from '../_components/ShiftsEditDialog';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const OVERRIDES_RANGE_DAYS_FORWARD = 90;
const EVENTS_RANGE_DAYS_BACK = 30;
const EVENTS_RANGE_DAYS_FORWARD = 180;

function isoDateOffset(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// ---------------------------------------------------------------------------
// Form helpers
// ---------------------------------------------------------------------------

function fromStaff(staff: StaffRead): StaffFormState {
  const sex = normalizeStaffSex(staff.sex as string | null | undefined);
  const status = normalizeStaffStatus(staff.status as string | null | undefined);
  return {
    code: staff.code ?? '',
    name: staff.name,
    kana: staff.kana ?? '',
    sex: sex ?? '',
    status,
    role: staff.role,
    primary_office_id: staff.primary_office_id ?? '',
    is_trainee: staff.is_trainee ?? false,
    note: staff.note ?? '',
  };
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
    is_trainee: form.is_trainee,
    note: form.note.trim() || null,
  };
}

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function StaffEditPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  const { data: session, status } = useSession();
  const sessionRole = session?.user?.role;
  const isPrivileged = sessionRole === 'admin' || sessionRole === 'manager';
  const canDelete = sessionRole === 'admin';
  const denied = status === 'authenticated' && !isPrivileged;

  useEffect(() => {
    if (denied && id) {
      router.replace(`/staff/${id}`);
    }
  }, [denied, id, router]);

  const { data, isLoading, isError, error } = useStaff(id);
  const { data: staffList } = useStaffList({ limit: 500 });
  const navRecords = useMemo<NavigatorRecord[]>(
    () =>
      (staffList ?? []).map((s) => ({
        id: s.id,
        code: s.code ?? null,
        name: s.name,
        kana: s.kana,
      })),
    [staffList],
  );

  const [form, setForm] = useState<StaffFormState | null>(null);
  const [isFormDirty, setIsFormDirty] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  // 削除確認ダイアログ
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const del = useDeleteStaff();

  const handleDelete = async () => {
    if (!data?.id) return;
    try {
      await del.mutateAsync(data.id);
      toast.success('スタッフを削除しました');
      setDeleteConfirmOpen(false);
      router.push('/staff');
    } catch (e) {
      toast.error(`削除に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`);
    }
  };

  // is_trainee OFF 確認ダイアログ
  const [traineeOffConfirmOpen, setTraineeOffConfirmOpen] = useState(false);
  const [pendingPayload, setPendingPayload] = useState<StaffUpdate | null>(null);

  const deleteCompanion = useDeleteCompanionAssignments(id ?? '__none__');

  useEffect(() => {
    if (data && form === null) {
      setForm(fromStaff(data));
      setIsFormDirty(false);
    }
  }, [data, form]);

  const update = useUpdateStaff({
    onSuccess: () => {
      toast.success('スタッフ情報を保存しました');
      setIsFormDirty(false);
    },
  });

  const handleBeforeNavigate = useCallback(() => {
    if (!isFormDirty) return true;
    return window.confirm(
      '未保存の変更があります。移動するとこれらの変更は失われます。続行しますか？',
    );
  }, [isFormDirty]);

  if (!id) return null;

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

  const prevIsTrainee = (data as StaffRead & { is_trainee?: boolean }).is_trainee ?? false;

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

    // is_trainee を OFF にしようとしている場合は確認ダイアログ
    if (prevIsTrainee && !form.is_trainee) {
      setPendingPayload(parsed.data);
      setTraineeOffConfirmOpen(true);
      return;
    }

    update.mutate({ id, payload: parsed.data });
  };

  const confirmTraineeOff = async () => {
    if (!pendingPayload) return;
    try {
      // 同行スタッフ割付を先に削除してから基本情報を PATCH
      await deleteCompanion.mutateAsync();
      update.mutate({ id, payload: pendingPayload });
    } catch {
      toast.error('同行スタッフ割付の削除に失敗しました');
    } finally {
      setTraineeOffConfirmOpen(false);
      setPendingPayload(null);
    }
  };

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link href={`/staff/${id}`}>
            <ArrowLeft className="h-4 w-4" />
            詳細へ
          </Link>
        </Button>
        <h1 className="font-serif text-2xl font-bold text-text-primary">
          スタッフ編集 — {data?.name}
        </h1>
        {navRecords.length > 0 && id && (
          <div className="flex flex-1 justify-center">
            <RecordNavigator
              currentId={id}
              records={navRecords}
              hrefTemplate="/staff/{id}/edit"
              entityLabel="スタッフ"
              onBeforeNavigate={handleBeforeNavigate}
            />
          </div>
        )}
        {canDelete && !data?.deleted_at ? (
          <Button
            variant="destructive"
            size="sm"
            className="ml-auto"
            onClick={() => setDeleteConfirmOpen(true)}
            disabled={del.isPending}
          >
            <Trash2 className="h-4 w-4" />
            削除
          </Button>
        ) : null}
      </header>

      {update.isError && (
        <Alert variant="destructive">
          <AlertTitle>更新に失敗しました</AlertTitle>
          <AlertDescription>
            {update.error instanceof Error ? update.error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      {/* ① 基本情報 */}
      <Card>
        <CardHeader>
          <CardTitle>基本情報</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <StaffFormFields
              form={form}
              errors={errors}
              onChange={(next) => {
                setForm(next);
                setIsFormDirty(true);
              }}
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

      {/* ② 週間シフト */}
      <ShiftsCardInline staffId={id} canEdit={isPrivileged} />

      {/* ③ 今後の休み・時間変更 */}
      <OverridesCardInline staffId={id} canEdit={isPrivileged} />

      {/* ④ 研修日 / イベント */}
      <EventsCardInline staffId={id} canEdit={isPrivileged} />

      {/* ⑤ 同行スタッフ割付 (is_trainee=true 時のみ) */}
      {form.is_trainee && <StaffCompanionPanel staffId={id} />}

      {/* 削除確認ダイアログ */}
      {data && (
        <DeleteConfirmModal
          open={deleteConfirmOpen}
          title="スタッフを削除しますか？"
          description={`${data.name} を削除すると一覧から外れます。論理削除のため後で復元可能です。`}
          confirming={del.isPending}
          onCancel={() => setDeleteConfirmOpen(false)}
          onConfirm={() => void handleDelete()}
        />
      )}

      {/* is_trainee OFF 確認ダイアログ */}
      <Dialog open={traineeOffConfirmOpen} onOpenChange={setTraineeOffConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新人フラグを解除しますか？</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-text-secondary">
            新人フラグを OFF にすると、登録済みの同行スタッフ割付データが全て削除されます。
            この操作は取り消せません。
          </p>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setTraineeOffConfirmOpen(false);
                setPendingPayload(null);
              }}
              disabled={deleteCompanion.isPending}
            >
              キャンセル
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={confirmTraineeOff}
              disabled={deleteCompanion.isPending}
            >
              {deleteCompanion.isPending ? '削除中…' : '解除して削除する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Inline section cards (mirrors detail page cards, with canEdit prop)
// ---------------------------------------------------------------------------

function ShiftsCardInline({ staffId, canEdit }: { staffId: string; canEdit: boolean }) {
  const { data, isLoading, isError, error } = useStaffShifts(staffId);
  const [open, setOpen] = useState(false);

  const rows: StaffShiftItem[] = data?.shifts ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>週間シフト</CardTitle>
        {canEdit && (
          <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
            <Pencil className="h-4 w-4" />
            編集
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-border-default text-left text-text-secondary">
              <tr>
                <th className="px-3 py-2 font-medium">曜日</th>
                <th className="px-3 py-2 font-medium">勤務</th>
                <th className="px-3 py-2 font-medium">開始</th>
                <th className="px-3 py-2 font-medium">終了</th>
              </tr>
            </thead>
            <tbody>
              {WEEKDAY_LABELS.map((label, weekday) => {
                const s = rows.find((r) => r.weekday === weekday);
                return (
                  <tr key={weekday} className="border-b border-border-default last:border-0">
                    <td className="px-3 py-2">{label}</td>
                    <td className="px-3 py-2">{s?.is_on ? '〇' : '休'}</td>
                    <td className="px-3 py-2 tnum text-text-secondary">{s?.start_time ?? '--'}</td>
                    <td className="px-3 py-2 tnum text-text-secondary">{s?.end_time ?? '--'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </CardContent>

      {canEdit && (
        <ShiftsEditDialog
          staffId={staffId}
          initialShifts={rows}
          open={open}
          onOpenChange={setOpen}
        />
      )}
    </Card>
  );
}

function OverridesCardInline({ staffId, canEdit }: { staffId: string; canEdit: boolean }) {
  const range = useMemo<OverrideRange>(
    () => ({
      from: isoDateOffset(0),
      to: isoDateOffset(OVERRIDES_RANGE_DAYS_FORWARD),
    }),
    [],
  );

  const { data, isLoading, isError, error } = useStaffOverrides(staffId, range);
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<OverrideRead | null>(null);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>今後の休み・時間変更</CardTitle>
        {canEdit && (
          <Button type="button" variant="outline" size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" />
            追加
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : !data || data.length === 0 ? (
          <p className="text-sm text-text-muted">登録された変更はありません</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {data.map((o) => (
              <li
                key={o.id}
                className="flex items-center justify-between gap-3 rounded border border-border-default p-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center rounded-full border border-border-default bg-bg-muted px-2 py-0.5 text-xs text-text-secondary">
                      {o.type}
                    </span>
                    <span className="tnum font-medium text-text-primary">{o.date}</span>
                  </div>
                  {(o.start_time || o.end_time) && (
                    <div className="tnum text-text-secondary">
                      {o.start_time ?? '--'} 〜 {o.end_time ?? '--'}
                    </div>
                  )}
                  {o.note && <div className="text-xs text-text-muted">{o.note}</div>}
                </div>
                {canEdit && (
                  <Button type="button" variant="outline" size="sm" onClick={() => setEditing(o)}>
                    <Pencil className="h-4 w-4" />
                    編集
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      {canEdit && (
        <>
          <OverrideAddDialog staffId={staffId} open={addOpen} onOpenChange={setAddOpen} />
          <OverrideEditDialog
            staffId={staffId}
            override={editing}
            open={!!editing}
            onOpenChange={(next: boolean) => {
              if (!next) setEditing(null);
            }}
          />
        </>
      )}
    </Card>
  );
}

function EventsCardInline({ staffId, canEdit }: { staffId: string; canEdit: boolean }) {
  const range = useMemo(
    () => ({
      from: isoDateOffset(-EVENTS_RANGE_DAYS_BACK),
      to: isoDateOffset(EVENTS_RANGE_DAYS_FORWARD),
    }),
    [],
  );

  const { data, isLoading, isError, error } = useStaffEvents(staffId, range);
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<EventRead | null>(null);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>研修日 / イベント</CardTitle>
        {canEdit && (
          <Button type="button" variant="outline" size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" />
            追加
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : !data || data.length === 0 ? (
          <p className="text-sm text-text-muted">登録された研修・イベントはありません</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {data.map((e) => (
              <li
                key={e.id}
                className="flex items-center justify-between gap-3 rounded border border-border-default p-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center rounded-full border border-border-default bg-bg-muted px-2 py-0.5 text-xs text-text-secondary">
                      {e.type}
                    </span>
                    <span className="font-medium text-text-primary">{e.title}</span>
                  </div>
                  <div className="tnum text-text-secondary">
                    {e.date}　{e.start_time} 〜 {e.end_time}
                  </div>
                  {e.note && <div className="text-xs text-text-muted">{e.note}</div>}
                </div>
                {canEdit && (
                  <Button type="button" variant="outline" size="sm" onClick={() => setEditing(e)}>
                    <Pencil className="h-4 w-4" />
                    編集
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      {canEdit && (
        <>
          <EventAddDialog staffId={staffId} open={addOpen} onOpenChange={setAddOpen} />
          <EventEditDialog
            staffId={staffId}
            event={editing}
            open={!!editing}
            onOpenChange={(next: boolean) => {
              if (!next) setEditing(null);
            }}
          />
        </>
      )}
    </Card>
  );
}
