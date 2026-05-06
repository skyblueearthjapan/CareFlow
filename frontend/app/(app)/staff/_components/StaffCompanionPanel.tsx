'use client';

/**
 * StaffCompanionPanel (W10-FE1 Phase 3)
 *
 * 同行スタッフ割付グリッドを提供するコンポーネント。
 * is_trainee=true のスタッフ編集画面で表示される。
 *
 * 仕様:
 * - 7 曜日 × 午前/午後 グリッド (full は別行で対応)
 * - 各セル: 「なし」+ companion 候補スタッフ一覧 (useCompanionCandidates)
 * - シフト時刻を自動算出表示 (シフト未設定時は「(シフト未設定)」注記)
 * - 「保存」: PUT (zod 検証 → 422 detail 表示)、成功時 toast.success
 * - 「リセット (全削除)」: 確認ダイアログ → DELETE
 * - staff role → フィールド disable + 保存ボタン非表示 (readonly)
 */

import * as React from 'react';
import { useSession } from 'next-auth/react';

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
import {
  useCompanionAssignments,
  useUpdateCompanionAssignments,
  useDeleteCompanionAssignments,
  useCompanionCandidates,
} from '@/lib/queries/staff_companion';
import { useStaffShifts } from '@/lib/queries/staff-shifts';
import { WEEKDAY_LABELS } from '@/lib/schemas/staff';
import {
  staffCompanionAssignmentsBulkPutSchema,
  type StaffCompanionAssignmentV2Read,
  type StaffCompanionPart,
} from '@/lib/schemas/v2/staff_companion_assignment';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PARTS: StaffCompanionPart[] = ['am', 'pm'];
const PART_LABELS: Record<StaffCompanionPart, string> = {
  am: '午前',
  pm: '午後',
  full: '終日',
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Grid state: [weekday][part] = companion_staff_id or '' (none) */
type GridState = Record<number, Record<StaffCompanionPart, string>>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function emptyGrid(): GridState {
  const g: GridState = {};
  for (let d = 0; d < 7; d++) {
    g[d] = { am: '', pm: '', full: '' };
  }
  return g;
}

function assignmentsToGrid(items: StaffCompanionAssignmentV2Read[]): GridState {
  const g = emptyGrid();
  for (const it of items) {
    if (it.weekday >= 0 && it.weekday <= 6) {
      // emptyGrid guarantees all 0..6 keys exist
      g[it.weekday]![it.part] = it.companion_staff_id;
    }
  }
  return g;
}

function gridToItems(
  grid: GridState,
): { weekday: number; part: StaffCompanionPart; companion_staff_id: string }[] {
  const items: { weekday: number; part: StaffCompanionPart; companion_staff_id: string }[] = [];
  for (let d = 0; d < 7; d++) {
    for (const part of ['am', 'pm', 'full'] as StaffCompanionPart[]) {
      const id = grid[d]![part];
      if (id) {
        items.push({ weekday: d, part, companion_staff_id: id });
      }
    }
  }
  return items;
}

// ---------------------------------------------------------------------------
// Shift time label helper
// ---------------------------------------------------------------------------

interface ShiftInfo {
  weekday: number;
  is_on: boolean;
  start_time?: string | null;
  end_time?: string | null;
}

function shiftTimeLabel(shifts: ShiftInfo[] | undefined, weekday: number): string {
  if (!shifts) return '';
  const s = shifts.find((r) => r.weekday === weekday);
  if (!s || !s.is_on) return '(シフト未設定)';
  if (!s.start_time || !s.end_time) return '(シフト未設定)';
  return `(${s.start_time}〜${s.end_time})`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface StaffCompanionPanelProps {
  staffId: string;
}

export function StaffCompanionPanel({ staffId }: StaffCompanionPanelProps) {
  const { data: session } = useSession();
  const sessionRole = session?.user?.role;
  const readonly = sessionRole === 'staff';

  const { data: assignments, isLoading, isError, error } = useCompanionAssignments(staffId);
  const { data: candidates = [] } = useCompanionCandidates(staffId);
  const { data: shiftsData } = useStaffShifts(staffId);

  const update = useUpdateCompanionAssignments(staffId);
  const del = useDeleteCompanionAssignments(staffId);

  const [grid, setGrid] = React.useState<GridState>(emptyGrid());
  const [validationErrors, setValidationErrors] = React.useState<string[]>([]);
  const [resetOpen, setResetOpen] = React.useState(false);

  // Sync grid when server data arrives
  React.useEffect(() => {
    if (assignments) {
      setGrid(assignmentsToGrid(assignments));
    }
  }, [assignments]);

  const setCellValue = (weekday: number, part: StaffCompanionPart, value: string) => {
    setGrid((prev) => {
      const prevRow = prev[weekday] ?? { am: '', pm: '', full: '' };
      return {
        ...prev,
        [weekday]: {
          ...prevRow,
          [part]: value,
        } as Record<StaffCompanionPart, string>,
      };
    });
    setValidationErrors([]);
  };

  const handleSave = async () => {
    setValidationErrors([]);
    const items = gridToItems(grid);
    const parsed = staffCompanionAssignmentsBulkPutSchema.safeParse({ items });
    if (!parsed.success) {
      const msgs = parsed.error.issues.map((i) => i.message);
      setValidationErrors(msgs);
      return;
    }
    try {
      await update.mutateAsync(parsed.data);
      toast.success('同行スタッフ割付を保存しました');
    } catch (err) {
      toast.error(`保存に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
    }
  };

  const handleReset = async () => {
    try {
      await del.mutateAsync();
      setGrid(emptyGrid());
      setResetOpen(false);
      toast.success('同行スタッフ割付を削除しました');
    } catch (err) {
      toast.error(`削除に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
    }
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>同行スタッフ割付</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>同行スタッフ割付</CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>同行スタッフ割付</CardTitle>
          {readonly && (
            <span className="rounded-full bg-bg-muted px-2 py-0.5 text-xs text-text-secondary">
              閲覧のみ
            </span>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          {validationErrors.length > 0 && (
            <Alert variant="destructive">
              <AlertTitle>入力エラー</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4">
                  {validationErrors.map((msg, i) => (
                    <li key={i}>{msg}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}

          {update.isError && (
            <Alert variant="destructive">
              <AlertTitle>保存に失敗しました</AlertTitle>
              <AlertDescription>
                {update.error instanceof Error ? update.error.message : '不明なエラー'}
              </AlertDescription>
            </Alert>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">曜日</th>
                  {PARTS.map((part) => (
                    <th key={part} className="px-3 py-2 font-medium">
                      {PART_LABELS[part]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {WEEKDAY_LABELS.map((label, weekday) => {
                  const shiftLabel = shiftTimeLabel(shiftsData?.shifts, weekday);
                  return (
                    <tr key={weekday} className="border-b border-border-default last:border-0">
                      <td className="px-3 py-2 font-medium text-text-primary">
                        <span>{label}</span>
                        {shiftLabel && (
                          <span className="ml-1 text-xs text-text-muted">{shiftLabel}</span>
                        )}
                      </td>
                      {PARTS.map((part) => (
                        <td key={part} className="px-3 py-2">
                          <CompanionSelect
                            value={grid[weekday]?.[part] ?? ''}
                            candidates={candidates}
                            disabled={readonly || update.isPending || del.isPending}
                            onChange={(v) => setCellValue(weekday, part, v)}
                            shiftsData={shiftsData?.shifts}
                          />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {!readonly && (
            <div className="flex items-center justify-between border-t border-border-default pt-4">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setResetOpen(true)}
                disabled={update.isPending || del.isPending}
              >
                リセット (全削除)
              </Button>
              <Button
                type="button"
                onClick={handleSave}
                disabled={update.isPending || del.isPending}
              >
                {update.isPending ? '保存中…' : '保存'}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Reset confirmation dialog */}
      <Dialog open={resetOpen} onOpenChange={setResetOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>同行スタッフ割付を削除しますか？</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-text-secondary">
            全曜日の同行スタッフ割付がリセットされます。この操作は取り消せません。
          </p>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setResetOpen(false)}
              disabled={del.isPending}
            >
              キャンセル
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleReset}
              disabled={del.isPending}
            >
              {del.isPending ? '削除中…' : '削除する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// CompanionSelect — single cell select
// ---------------------------------------------------------------------------

interface StaffRead {
  id: string;
  name: string;
  status: string;
}

interface ShiftRow {
  weekday?: number;
  is_on: boolean;
  start_time?: string | null;
  end_time?: string | null;
}

interface CompanionSelectProps {
  value: string;
  candidates: StaffRead[];
  disabled: boolean;
  onChange: (v: string) => void;
  /** Optional: to display shift times next to candidate name */
  shiftsData?: ShiftRow[];
}

function CompanionSelect({ value, candidates, disabled, onChange }: CompanionSelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className="flex h-9 w-full min-w-[120px] rounded-md border border-border-default bg-bg-base px-2 py-1 text-sm text-text-primary focus-visible:border-brand-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary-light disabled:cursor-not-allowed disabled:opacity-50"
      aria-label="同行スタッフを選択"
    >
      <option value="">なし</option>
      {candidates.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
        </option>
      ))}
    </select>
  );
}
