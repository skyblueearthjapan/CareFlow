'use client';

/**
 * Staff master — detail view (Phase 3-7..3-11).
 *
 * Shows the basic record plus four read-only sections:
 *   - Weekly shifts        (3-8)  -> backend table exists, no REST yet
 *   - Weekly overrides     (3-9)  -> backend table exists, no REST yet
 *   - Events / 研修日       (3-10) -> backend table exists, no REST yet
 *   - Mentor assignments   (3-11) -> backend table exists, no REST yet
 *
 * Until those endpoints land in Wave 2 we render mock fixtures shaped like
 * the zod schemas (so the UI is the only thing to swap out later).
 */
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useState } from 'react';
import { ArrowLeft, Pencil, Trash2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useDeleteStaff, useStaff } from '@/lib/queries/staff';
import {
  WEEKDAY_LABELS,
  roleLabel,
  sexLabel,
  statusLabel,
  type MentorAssignment,
  type StaffEvent,
  type StaffRead,
  type StaffShift,
  type StaffWeeklyOverride,
} from '@/lib/schemas/staff';

import { DeleteConfirmModal } from '../_components/DeleteConfirmModal';

// ---------------------------------------------------------------------------
// Mock fixtures — TODO(Wave 2): replace with real GET /api/v1/staff/:id/...
// endpoints once the backend exposes them. The shape matches the zod schemas
// in lib/schemas/staff.ts.
// ---------------------------------------------------------------------------
function buildMockShifts(staffId: string): StaffShift[] {
  // Mon-Fri on, weekend off — typical pattern.
  return WEEKDAY_LABELS.map((_, weekday) => ({
    staff_id: staffId,
    weekday,
    is_on: weekday < 5,
    start_time: weekday < 5 ? '09:00' : null,
    end_time: weekday < 5 ? '17:00' : null,
  }));
}

const MOCK_OVERRIDES: StaffWeeklyOverride[] = [];
const MOCK_EVENTS: StaffEvent[] = [];
const MOCK_MENTOR_ASSIGNMENTS: MentorAssignment[] = [];

export default function StaffDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  const { data: session } = useSession();
  const role = session?.user?.role;
  const sessionStaffId = session?.user?.staffId ?? null;

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

  const mockShifts = buildMockShifts(data.id);

  // Role-based UI gating: backend already enforces 403, but hiding the affordances
  // avoids "click → fail" confusion. admin/manager get full access; a staff user
  // may only edit their own record (and never delete).
  const isPrivileged = role === 'admin' || role === 'manager';
  const isOwnRecord = role === 'staff' && sessionStaffId === data.id;
  const canEdit = isPrivileged || isOwnRecord;
  const canDelete = isPrivileged;

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

      <Card>
        <CardHeader>
          <CardTitle>週間シフト</CardTitle>
          <p className="text-xs text-text-muted">
            {/* TODO(Wave 2): replace with GET /api/v1/staff/:id/shifts */}
            読み取り表示のみ。編集 UI は Wave 2。
          </p>
        </CardHeader>
        <CardContent>
          <ShiftsTable shifts={mockShifts} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>今後の休み・時間変更</CardTitle>
          <p className="text-xs text-text-muted">
            {/* TODO(Wave 2): replace with GET /api/v1/staff/:id/weekly-overrides */}
            読み取り表示のみ。編集 UI は Wave 2。
          </p>
        </CardHeader>
        <CardContent>
          <OverridesList overrides={MOCK_OVERRIDES} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>研修日 / イベント</CardTitle>
          <p className="text-xs text-text-muted">
            {/* TODO(Wave 2): replace with GET /api/v1/staff/:id/events */}
            読み取り表示のみ。編集 UI は Wave 2。
          </p>
        </CardHeader>
        <CardContent>
          <EventsList events={MOCK_EVENTS} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>メンター割付</CardTitle>
          <p className="text-xs text-text-muted">
            {/* TODO(Wave 2): replace with GET /api/v1/staff/:id/mentor-assignments */}
            読み取り表示のみ。編集 UI は Wave 2。
          </p>
        </CardHeader>
        <CardContent>
          <MentorList assignments={MOCK_MENTOR_ASSIGNMENTS} />
        </CardContent>
      </Card>

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
          <Row label="2人体制対応" value={staff.can_double_team ? '可' : '不可'} />
          <Row label="メンター ID" value={staff.mentor_id ?? '--'} />
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

function ShiftsTable({ shifts }: { shifts: StaffShift[] }) {
  return (
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
        {shifts.map((s) => (
          <tr key={s.weekday} className="border-b border-border-default last:border-0">
            <td className="px-3 py-2">{WEEKDAY_LABELS[s.weekday]}</td>
            <td className="px-3 py-2">{s.is_on ? '〇' : '休'}</td>
            <td className="px-3 py-2 tnum text-text-secondary">{s.start_time ?? '--'}</td>
            <td className="px-3 py-2 tnum text-text-secondary">{s.end_time ?? '--'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OverridesList({ overrides }: { overrides: StaffWeeklyOverride[] }) {
  if (overrides.length === 0) {
    return <p className="text-sm text-text-muted">登録された変更はありません</p>;
  }
  return (
    <ul className="space-y-2 text-sm">
      {overrides.map((o) => (
        <li key={o.id} className="rounded border border-border-default p-3">
          <div className="font-medium">
            {o.iso_year}年 第{o.iso_week}週 {WEEKDAY_LABELS[o.weekday]}曜日
          </div>
          <div className="text-text-secondary">
            {o.override_type === 'off'
              ? '休み'
              : `時間変更: ${o.start_time ?? '--'} ~ ${o.end_time ?? '--'}`}
          </div>
          {o.reason && <div className="text-xs text-text-muted">{o.reason}</div>}
        </li>
      ))}
    </ul>
  );
}

function EventsList({ events }: { events: StaffEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-text-muted">登録された研修・イベントはありません</p>;
  }
  return (
    <ul className="space-y-2 text-sm">
      {events.map((e) => (
        <li key={e.id} className="rounded border border-border-default p-3">
          <div className="font-medium">{e.title ?? e.event_type}</div>
          <div className="text-text-secondary">
            {formatDate(e.starts_at)} 〜 {formatDate(e.ends_at)}
          </div>
          {e.note && <div className="text-xs text-text-muted">{e.note}</div>}
        </li>
      ))}
    </ul>
  );
}

function MentorList({ assignments }: { assignments: MentorAssignment[] }) {
  if (assignments.length === 0) {
    return <p className="text-sm text-text-muted">メンター割付はありません</p>;
  }
  return (
    <ul className="space-y-2 text-sm">
      {assignments.map((a) => (
        <li key={a.id} className="rounded border border-border-default p-3">
          <div>
            メンター {a.mentor_id.slice(0, 8)} → メンティ {a.mentee_id.slice(0, 8)}
          </div>
          <div className="text-text-secondary">
            {a.start_date} 〜 {a.end_date ?? '継続中'}
          </div>
        </li>
      ))}
    </ul>
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
