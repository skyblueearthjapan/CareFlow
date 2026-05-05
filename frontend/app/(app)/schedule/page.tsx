'use client';

/**
 * 週ビュー (Phase 4-8 / W2-A)
 *
 * - 上部コントロール: WeekSelector + 拠点 / スタッフ / 患者フィルタ + 割当実行ボタン
 * - グリッド: スタッフ × 日 (横軸=月〜日)
 * - 訪問チップクリックで `VisitEditDialog`
 * - 「未割当」セクション (primary_staff_id が NULL の visit)
 *
 * staff role はバックエンド側で自分の visit のみに絞られるため、フィルタ UI
 * では拠点/スタッフ列を hide し、患者フィルタのみ提供する。
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
import { Skeleton } from '@/components/ui/skeleton';
import { useOffices } from '@/lib/queries/offices';
import { usePatients } from '@/lib/queries/patients';
import { useStaffList } from '@/lib/queries/staff';
import { useVisits } from '@/lib/queries/visits';
import type { VisitRead } from '@/lib/schemas/visit';

import { AllocateRunDialog } from '@/components/schedule/AllocateRunDialog';
import { UnassignedList } from '@/components/schedule/UnassignedList';
import { VisitEditDialog } from '@/components/schedule/VisitEditDialog';
import {
  addDays,
  toWeekStart,
  WeekSelector,
} from '@/components/schedule/WeekSelector';
import { WeekGrid } from '@/components/schedule/WeekGrid';

function formatIsoDate(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

const SOFT_CAP_PER_DAY = 6;

export default function SchedulePage() {
  const { data: session } = useSession();
  const role = session?.user?.role ?? 'staff';
  const canRunAllocate = role === 'admin' || role === 'manager';
  const canDeleteVisit = role === 'admin';

  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const [officeFilter, setOfficeFilter] = useState<string>('');
  const [staffFilter, setStaffFilter] = useState<string>('');
  const [patientFilter, setPatientFilter] = useState<string>('');
  const [editingVisit, setEditingVisit] = useState<VisitRead | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [allocateOpen, setAllocateOpen] = useState(false);

  const weekStartIso = formatIsoDate(weekStart);
  const weekEndIso = formatIsoDate(addDays(weekStart, 6));

  const visitsQuery = useVisits({
    week_start: weekStartIso,
    week_end: weekEndIso,
    staff_id: staffFilter || undefined,
    patient_id: patientFilter || undefined,
  });
  const staffQuery = useStaffList({ limit: 200 });
  const officesQuery = useOffices({ limit: 200 });
  const patientsQuery = usePatients({ limit: 200 });

  const allStaff = staffQuery.data ?? [];
  const allOffices = officesQuery.offices ?? [];
  const allPatients = patientsQuery.data?.items ?? [];

  // Apply filters as row-level filters on staff (the visit chip itself
  // doesn't carry an office id; staff.primary_office_id is the closest
  // proxy until W1-G wires office onto visits). Staff role only sees
  // their own row to avoid empty rows for the rest of the team.
  const sessionStaffId = session?.user?.staffId ?? null;
  const visibleStaff = useMemo(() => {
    let rows = allStaff;
    if (role === 'staff' && sessionStaffId) {
      rows = rows.filter((s) => s.id === sessionStaffId);
    }
    if (officeFilter) {
      rows = rows.filter((s) => s.primary_office_id === officeFilter);
    }
    if (staffFilter) {
      rows = rows.filter((s) => s.id === staffFilter);
    }
    return rows;
  }, [allStaff, officeFilter, staffFilter, role, sessionStaffId]);

  const visits = visitsQuery.data?.items ?? [];

  const assigned = useMemo(
    () => visits.filter((v) => !!v.primary_staff_id),
    [visits],
  );
  const unassigned = useMemo(
    () => visits.filter((v) => !v.primary_staff_id),
    [visits],
  );

  const officeOptions: ComboboxOption[] = [
    { label: 'すべての拠点', value: '' },
    ...allOffices.map((o) => ({ label: o.name, value: o.id })),
  ];
  const staffOptions: ComboboxOption[] = [
    { label: 'すべてのスタッフ', value: '' },
    ...allStaff.map((s) => ({ label: s.name, value: s.id })),
  ];
  const patientOptions: ComboboxOption[] = [
    { label: 'すべての患者', value: '' },
    ...allPatients.map((p) => ({ label: p.name, value: p.id })),
  ];

  const officeLabelById = useMemo(() => {
    const map = new Map<string, string>();
    for (const o of allOffices) map.set(o.id, o.name);
    return (id: string | null | undefined) =>
      id ? map.get(id) ?? null : null;
  }, [allOffices]);

  const handleVisitClick = (v: VisitRead) => {
    setEditingVisit(v);
    setEditOpen(true);
  };

  const handleEditClose = () => {
    setEditOpen(false);
    // Keep the row in state momentarily so the dialog can fade out cleanly.
    setTimeout(() => setEditingVisit(null), 100);
  };

  const isLoading = visitsQuery.isLoading || staffQuery.isLoading;

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl font-bold text-text-primary">
            スケジュール
          </h1>
          <p className="text-sm text-text-secondary">
            週単位の訪問予定を表示・編集します。
          </p>
        </div>
        {canRunAllocate ? (
          <Button type="button" onClick={() => setAllocateOpen(true)}>
            割当実行
          </Button>
        ) : null}
      </header>

      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <WeekSelector weekStart={weekStart} onChange={setWeekStart} />
        </div>
        {role !== 'staff' ? (
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <div>
              <Combobox
                options={officeOptions}
                value={officeFilter}
                onChange={setOfficeFilter}
                placeholder="拠点で絞込"
              />
            </div>
            <div>
              <Combobox
                options={staffOptions}
                value={staffFilter}
                onChange={setStaffFilter}
                placeholder="スタッフで絞込"
              />
            </div>
            <div>
              <Combobox
                options={patientOptions}
                value={patientFilter}
                onChange={setPatientFilter}
                placeholder="患者で絞込"
              />
            </div>
          </div>
        ) : (
          <div className="mt-3">
            <Combobox
              options={patientOptions}
              value={patientFilter}
              onChange={setPatientFilter}
              placeholder="患者で絞込"
            />
          </div>
        )}
      </Card>

      {visitsQuery.isError ? (
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {visitsQuery.error instanceof Error
              ? visitsQuery.error.message
              : '不明なエラー'}
          </AlertDescription>
        </Alert>
      ) : null}

      {visitsQuery.data?.truncated ? (
        <Alert variant="warning">
          <AlertTitle>表示が省略されている可能性があります</AlertTitle>
          <AlertDescription>
            訪問件数が上限 (500) に達しました。期間を絞ってご確認ください。
          </AlertDescription>
        </Alert>
      ) : null}

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : visits.length === 0 ? (
        <Card className="p-8 text-center text-sm text-text-muted">
          対象週に訪問がありません。
        </Card>
      ) : (
        <WeekGrid
          weekStart={weekStart}
          staff={visibleStaff}
          visits={assigned}
          maxPerDay={SOFT_CAP_PER_DAY}
          onVisitClick={handleVisitClick}
          officeLabel={officeLabelById}
        />
      )}

      <UnassignedList visits={unassigned} onSelect={handleVisitClick} />

      <VisitEditDialog
        open={editOpen}
        visit={editingVisit}
        staff={allStaff}
        canDelete={canDeleteVisit}
        onClose={handleEditClose}
      />

      <AllocateRunDialog
        open={allocateOpen}
        weekStart={weekStart}
        onClose={() => setAllocateOpen(false)}
      />
    </section>
  );
}
