'use client';

/**
 * Staff master — detail view (Phase 3-7..3-11).
 *
 * All four sections are now backed by live endpoints:
 *   - Weekly shifts        (3-8)  GET/PUT  /api/v1/staff/:id/shifts
 *   - Weekly overrides     (3-9)  GET/POST/PATCH/DELETE /api/v1/staff/:id/overrides
 *   - Events / 研修日       (3-10) /api/v1/staff/:id/events            (F5-Frontend-B)
 *   - Mentor assignments   (3-11) /api/v1/staff/:id/mentor             (F5-Frontend-B)
 */
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useMemo, useState } from 'react';
import { ArrowLeft, Pencil, Plus, Trash2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useMentor } from '@/lib/queries/mentor';
import { useStaffEvents } from '@/lib/queries/staff-events';
import { useStaffOverrides, type OverrideRange } from '@/lib/queries/staff-overrides';
import { useStaffShifts } from '@/lib/queries/staff-shifts';
import { useDeleteStaff, useStaff } from '@/lib/queries/staff';
import type { OverrideRead } from '@/lib/schemas/staff-overrides';
import {
  WEEKDAY_LABELS,
  roleLabel,
  sexLabel,
  statusLabel,
  type StaffRead,
} from '@/lib/schemas/staff';
import type { StaffShiftItem } from '@/lib/schemas/staff-shifts';
import type { EventRead } from '@/lib/schemas/staff-events';

import { DeleteConfirmModal } from '../_components/DeleteConfirmModal';
import { EventAddDialog } from './_components/EventAddDialog';
import { EventEditDialog } from './_components/EventEditDialog';
import { MentorAssignDialog } from './_components/MentorAssignDialog';
import { OverrideAddDialog } from './_components/OverrideAddDialog';
import { OverrideEditDialog } from './_components/OverrideEditDialog';
import { ShiftsEditDialog } from './_components/ShiftsEditDialog';

/** Overrides default window: today through +90 days. */
const OVERRIDES_RANGE_DAYS_FORWARD = 90;

/** Default events lookback / lookahead window (in days) for the detail view. */
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

export default function StaffDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  const { data: session } = useSession();
  const role = session?.user?.role;

  const [confirmOpen, setConfirmOpen] = useState(false);

  const { data, isLoading, isError, error } = useStaff(id);

  const del = useDeleteStaff({
    onSuccess: () => {
      setConfirmOpen(false);
      router.push('/staff');
    },
  });

  if (!id) return null;

  if (isLoading) {
    return (
      <section className="space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-40 w-full" />
      </section>
    );
  }

  if (isError) {
    return (
      <section className="space-y-4">
        <header className="flex items-center gap-3">
          <Button asChild variant="ghost" size="sm">
            <Link href="/staff">
              <ArrowLeft className="h-4 w-4" />
              一覧へ
            </Link>
          </Button>
        </header>
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      </section>
    );
  }

  if (!data) return null;

  // Role-based UI gating: backend already enforces 403, but hiding the affordances
  // avoids "click → fail" confusion. Backend policy (see backend/app/api/v1/staff.py):
  //   - PATCH /staff/{id}  -> admin/manager only
  //   - DELETE /staff/{id} -> admin only
  // Staff users (even on their own record) cannot edit; they read-only.
  const isPrivileged = role === 'admin' || role === 'manager';
  const canEdit = isPrivileged;
  const canDelete = role === 'admin';

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link href="/staff">
            <ArrowLeft className="h-4 w-4" />
            一覧へ
          </Link>
        </Button>
        <h1 className="font-serif text-2xl font-bold text-text-primary">{data.name}</h1>
        <span className="rounded bg-bg-muted px-2 py-1 text-xs text-text-secondary">
          {statusLabel(data.status)}
        </span>
        <div className="ml-auto flex gap-2">
          {canEdit && (
            <Button asChild variant="outline" size="sm">
              <Link href={`/staff/${data.id}/edit`}>
                <Pencil className="h-4 w-4" />
                編集
              </Link>
            </Button>
          )}
          {canDelete && (
            <Button variant="destructive" size="sm" onClick={() => setConfirmOpen(true)}>
              <Trash2 className="h-4 w-4" />
              削除
            </Button>
          )}
        </div>
      </header>

      {del.isError && (
        <Alert variant="destructive">
          <AlertTitle>削除に失敗しました</AlertTitle>
          <AlertDescription>
            {del.error instanceof Error ? del.error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      <BasicInfoCard staff={data} />

      <ShiftsCard staffId={data.id} canEdit={canEdit} />

      <OverridesCard staffId={data.id} canEdit={canEdit} />

      <EventsCard staffId={data.id} canEdit={canEdit} />

      <MentorCard
        staffId={data.id}
        canEdit={canEdit}
        currentMentorIdHint={data.mentor_id ?? null}
      />

      <DeleteConfirmModal
        open={confirmOpen}
        title="スタッフを削除しますか？"
        description={`${data.name} を削除すると一覧から外れます。論理削除のため後で復元可能です。`}
        confirming={del.isPending}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => del.mutate(data.id)}
      />
    </section>
  );
}

function BasicInfoCard({ staff }: { staff: StaffRead }) {
  // v2 (§4.2) 残置 8 項目のみ表示。メンターは「詳細」セクションへ移設済み。
  // 削除済み: can_double_team / home_address+lat/lng / areas /
  //           max_per_day / skill_level / assignment_volume.
  return (
    <Card>
      <CardHeader>
        <CardTitle>基本情報</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2">
          <Row label="スタッフコード" value={staff.code ?? '--'} />
          <Row label="氏名" value={staff.name} />
          <Row label="カナ" value={staff.kana ?? '--'} />
          <Row label="性別" value={sexLabel(staff.sex)} />
          <Row label="役割" value={roleLabel(staff.role)} />
          <Row label="状態" value={statusLabel(staff.status)} />
          <Row label="主拠点" value={staff.primary_office_id ?? '--'} />
          <Row label="登録日時" value={formatDate(staff.created_at)} />
          {staff.note && (
            <div className="md:col-span-2">
              <dt className="text-xs text-text-muted">備考</dt>
              <dd className="whitespace-pre-wrap text-text-primary">{staff.note}</dd>
            </div>
          )}
        </dl>
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="text-text-primary">{value}</dd>
    </div>
  );
}

function ShiftsCard({ staffId, canEdit }: { staffId: string; canEdit: boolean }) {
  const { data, isLoading, isError, error } = useStaffShifts(staffId);
  const [open, setOpen] = useState(false);

  // The bulk PUT contract guarantees 7 rows back, but during onboarding the
  // server may return an empty array — render the dialog's defaults instead.
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

function OverridesCard({ staffId, canEdit }: { staffId: string; canEdit: boolean }) {
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
            onOpenChange={(next) => {
              if (!next) setEditing(null);
            }}
          />
        </>
      )}
    </Card>
  );
}

function EventsCard({ staffId, canEdit }: { staffId: string; canEdit: boolean }) {
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
            onOpenChange={(next) => {
              if (!next) setEditing(null);
            }}
          />
        </>
      )}
    </Card>
  );
}

function MentorCard({
  staffId,
  canEdit,
  currentMentorIdHint,
}: {
  staffId: string;
  canEdit: boolean;
  /** Falls back to staff.mentor_id while the dedicated endpoint is loading. */
  currentMentorIdHint: string | null;
}) {
  const { data, isLoading, isError, error } = useMentor(staffId);
  const [open, setOpen] = useState(false);

  const mentorId = data?.mentor_staff_id ?? currentMentorIdHint;
  const mentorName = data?.mentor_staff_name ?? null;

  // v2 (§4.2): メンターは「基本情報」ではなく「詳細」セクションに置く。
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>詳細 — メンター（新人スタッフのみ設定）</CardTitle>
        {canEdit && (
          <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
            <Pencil className="h-4 w-4" />
            変更
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : mentorId ? (
          <div className="rounded border border-border-default p-3 text-sm">
            <div className="text-xs text-text-muted">担当メンター</div>
            <div className="text-text-primary">{mentorName ?? mentorId}</div>
          </div>
        ) : (
          <Alert>
            <AlertTitle>メンター未設定</AlertTitle>
            <AlertDescription>
              新人スタッフのみメンターを設定してください。通常スタッフでは未設定で構いません。
            </AlertDescription>
          </Alert>
        )}
      </CardContent>

      {canEdit && (
        <MentorAssignDialog
          staffId={staffId}
          currentMentorId={mentorId}
          open={open}
          onOpenChange={setOpen}
        />
      )}
    </Card>
  );
}

function formatDate(value: string): string {
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleString('ja-JP', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return value;
  }
}
