'use client';

/**
 * Staff master — detail view (Phase 3-7..3-11 / W10-FE2).
 *
 * All sections are backed by live endpoints:
 *   - Weekly shifts        (3-8)  GET/PUT  /api/v1/staff/:id/shifts
 *   - Weekly overrides     (3-9)  GET/POST/PATCH/DELETE /api/v1/staff/:id/overrides
 *   - Events / 研修日       (3-10) /api/v1/staff/:id/events
 *   - 新人同行サマリ        GET /api/v1/trainee-accompaniment-defaults ほか
 *     (旧 W10 companion-assignments は新人同行 Phase 2 で撤去・後継は
 *      TraineeAccompanimentSummary — docs/plans/trainee-accompaniment-design.md §7.5)
 *
 * W10-FE2 変更点:
 *   - 旧「メンター割付」セクション → 「同行スタッフ割付」(閲覧専用) に置換
 *   - 基本情報に「新人」バッジ追加 (is_trainee=true 時)
 *   - mentor_id 参照を全削除 (BE Phase 1 で列廃止済)
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
import { RecordNavigator, type NavigatorRecord } from '@/components/RecordNavigator';
import { useStaffEvents } from '@/lib/queries/staff-events';
import { useStaffOverrides, type OverrideRange } from '@/lib/queries/staff-overrides';
import { useStaffShifts } from '@/lib/queries/staff-shifts';
import { useDeleteStaff, useStaff, useStaffList } from '@/lib/queries/staff';
import { useOffices } from '@/lib/queries/offices';
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
import { OverrideAddDialog } from './_components/OverrideAddDialog';
import { OverrideEditDialog } from './_components/OverrideEditDialog';
import { ShiftsEditDialog } from './_components/ShiftsEditDialog';
import { TraineeAccompanimentSummary } from './_components/TraineeAccompanimentSummary';
import { isAdminRole } from '@/lib/rbac';

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
  const isPrivileged = isAdminRole(role);
  const canEdit = isPrivileged;
  const canDelete = role === 'admin';

  return (
    <section className="space-y-6">
      {/* grid 3 列 (1fr - auto - 1fr) で RecordNavigator を viewport 中央に固定. */}
      <header className="grid grid-cols-1 items-center gap-3 lg:grid-cols-[1fr_auto_1fr]">
        <div className="flex flex-wrap items-center gap-3">
          <Button asChild variant="ghost" size="sm">
            <Link href="/staff">
              <ArrowLeft className="h-4 w-4" />
              一覧へ
            </Link>
          </Button>
          <h1 className="font-serif text-2xl font-bold text-text-primary">{data.name}</h1>
          {data.is_trainee && (
            <span className="rounded bg-warning px-2 py-0.5 text-xs font-semibold text-warning-foreground">
              新人
            </span>
          )}
          <span className="rounded bg-bg-muted px-2 py-1 text-xs text-text-secondary">
            {statusLabel(data.status)}
          </span>
        </div>
        <div className="flex justify-center">
          {navRecords.length > 0 && (
            <RecordNavigator
              currentId={data.id}
              records={navRecords}
              hrefTemplate="/staff/{id}"
              entityLabel="スタッフ"
            />
          )}
        </div>
        <div className="flex gap-2 lg:justify-end">
          {/* RB (PO決定 2026-07-08): 全ロール同一表示・操作は権限どおり (disabled)。
              編集は staff にも見せて無効化。削除は admin 限定センシティブのため非表示のまま。 */}
          {canEdit ? (
            <Button asChild variant="outline" size="sm">
              <Link href={`/staff/${data.id}/edit`}>
                <Pencil className="h-4 w-4" />
                編集
              </Link>
            </Button>
          ) : (
            <Button variant="outline" size="sm" disabled title="編集権限がありません">
              <Pencil className="h-4 w-4" />
              編集
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

      {/* 新人同行サマリ — is_trainee=true のときのみ表示 (§7.5・閲覧専用) */}
      {data.is_trainee && <TraineeAccompanimentCard staffId={data.id} />}

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
  const { offices } = useOffices();
  // 主担当拠点 表示用: ID → 「拠点名 (住所)」 (編集 form と同じ可読形式).
  const primaryOfficeLabel = useMemo(() => {
    const oid = staff.primary_office_id;
    if (!oid) return '--';
    const office = offices.find((o) => o.id === oid);
    if (!office) return oid;
    const addr = office.address?.trim();
    return addr ? `${office.name} (${addr})` : office.name;
  }, [staff.primary_office_id, offices]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>基本情報</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2">
          <Row label="スタッフコード" value={staff.code ?? '--'} />
          <Row label="氏名" value={staff.name} badge={staff.is_trainee ? '新人' : undefined} />
          <Row label="カナ" value={staff.kana ?? '--'} />
          <Row label="性別" value={sexLabel(staff.sex)} />
          <Row label="役割" value={roleLabel(staff.role)} />
          <Row label="状態" value={statusLabel(staff.status)} />
          <Row label="新人フラグ" value={staff.is_trainee ? '新人 (同行スタッフ要)' : '通常'} />
          <Row label="主担当拠点" value={primaryOfficeLabel} />
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

function Row({ label, value, badge }: { label: string; value: string; badge?: string }) {
  return (
    <div>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="flex items-center gap-1.5 text-text-primary">
        {value}
        {badge && (
          <span className="rounded bg-warning px-1.5 py-0.5 text-xs font-semibold text-warning-foreground">
            {badge}
          </span>
        )}
      </dd>
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
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canEdit}
          title={canEdit ? undefined : '編集権限がありません'}
          onClick={() => setOpen(true)}
        >
          <Pencil className="h-4 w-4" />
          編集
        </Button>
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
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canEdit}
          title={canEdit ? undefined : '編集権限がありません'}
          onClick={() => setAddOpen(true)}
        >
          <Plus className="h-4 w-4" />
          追加
        </Button>
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
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!canEdit}
                  title={canEdit ? undefined : '編集権限がありません'}
                  onClick={() => setEditing(o)}
                >
                  <Pencil className="h-4 w-4" />
                  編集
                </Button>
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
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canEdit}
          title={canEdit ? undefined : '編集権限がありません'}
          onClick={() => setAddOpen(true)}
        >
          <Plus className="h-4 w-4" />
          追加
        </Button>
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
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!canEdit}
                  title={canEdit ? undefined : '編集権限がありません'}
                  onClick={() => setEditing(e)}
                >
                  <Pencil className="h-4 w-4" />
                  編集
                </Button>
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

/**
 * 新人同行サマリカード (§7.5・閲覧専用).
 * is_trainee=true の場合のみ StaffDetailPage から呼び出される。
 */
function TraineeAccompanimentCard({ staffId }: { staffId: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>新人同行</CardTitle>
      </CardHeader>
      <CardContent>
        <TraineeAccompanimentSummary staffId={staffId} />
      </CardContent>
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
