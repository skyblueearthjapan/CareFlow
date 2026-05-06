/**
 * PatientFixedVisitsPanel (W9-FE1 Phase 3)
 *
 * 患者編集画面に「週間訪問パターン (固定枠)」セクションを提供するコンポーネント。
 *
 * 仕様:
 * - タブで normal / special を切替
 * - 各曜日: チェックボックス + start_time picker + duration_min select
 * - 「希望から自動生成」: patient.weekly_pattern を読みフォーム初期値に反映
 * - 「現スケから取込」: POST /from-week (Phase 2 連携)
 * - 「リセット」: DELETE (確認ダイアログあり)
 * - 「保存」: PUT (zod 検証 → 422 detail 表示)
 * - staff role は読み取り専用 (フィールド disable)
 * - admin/manager は編集可
 */
'use client';

import * as React from 'react';
import { useSession } from 'next-auth/react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { toast } from '@/components/ui/sonner';

import {
  useFixedVisits,
  useUpdateFixedVisits,
  useDeleteFixedVisits,
  useApplyFromWeek,
} from '@/lib/queries/patient_fixed_visits';
import {
  patientFixedVisitsBulkPutSchema,
  PATIENT_FIXED_VISIT_MODES,
  type PatientFixedVisitMode,
  type PatientFixedVisitV2Base,
  type PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';
import type { WeeklyPattern } from '@/lib/schemas/patient';

// ─── Constants ───────────────────────────────────────────────────────────────

const WEEKDAY_LABELS: Record<number, string> = {
  0: '月',
  1: '火',
  2: '水',
  3: '木',
  4: '金',
  5: '土',
  6: '日',
};

/** 15 分ステップの時刻選択肢 (00:00 〜 23:45) */
const TIME_OPTIONS: string[] = (() => {
  const opts: string[] = [];
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += 15) {
      opts.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
    }
  }
  return opts;
})();

const DURATION_OPTIONS = [15, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360, 480] as const;

// ─── Types ───────────────────────────────────────────────────────────────────

/** フォーム内部で使う 1 曜日行の状態 */
interface DayRow {
  enabled: boolean;
  start_time: string;
  duration_min: number;
}

type DayRows = Record<number, DayRow>;

// ─── Helpers ─────────────────────────────────────────────────────────────────

function emptyDayRows(): DayRows {
  const rows: DayRows = {};
  for (let i = 0; i < 7; i++) {
    rows[i] = { enabled: false, start_time: '09:00', duration_min: 30 };
  }
  return rows;
}

function readsToDayRows(reads: PatientFixedVisitV2Read[]): DayRows {
  const rows = emptyDayRows();
  for (const r of reads) {
    if (r.weekday >= 0 && r.weekday <= 6) {
      // start_time は HH:MM:SS の場合もあるので先頭 5 文字に切り詰める
      rows[r.weekday] = {
        enabled: true,
        start_time: r.start_time.slice(0, 5),
        duration_min: r.duration_min,
      };
    }
  }
  return rows;
}

function dayRowsToItems(rows: DayRows): PatientFixedVisitV2Base[] {
  return Object.entries(rows)
    .filter(([, row]) => row.enabled)
    .map(([weekday, row]) => ({
      weekday: Number(weekday),
      start_time: row.start_time,
      duration_min: row.duration_min,
    }));
}

/** 患者の weekly_pattern (希望パターン) から DayRows を生成する */
function weeklyPatternToDayRows(pattern: WeeklyPattern | null | undefined): DayRows {
  const rows = emptyDayRows();
  if (!pattern) return rows;

  const WEEKDAY_KEY_MAP: Record<string, number> = {
    Mon: 0,
    Tue: 1,
    Wed: 2,
    Thu: 3,
    Fri: 4,
    Sat: 5,
    Sun: 6,
  };

  const preferred = pattern.preferred_weekdays ?? [];
  const startTime = pattern.preferred_start ?? '09:00';
  const duration = pattern.service_minutes ?? 30;

  for (const wd of preferred) {
    const idx = WEEKDAY_KEY_MAP[wd];
    if (idx !== undefined) {
      rows[idx] = {
        enabled: true,
        start_time: startTime.slice(0, 5),
        duration_min: Math.max(1, Math.min(480, duration)),
      };
    }
  }
  return rows;
}

// ─── Sub-component: WeekGrid ─────────────────────────────────────────────────

interface WeekGridProps {
  rows: DayRows;
  onChange: (rows: DayRows) => void;
  disabled?: boolean;
  errors: Record<number, string>;
}

function WeekGrid({ rows, onChange, disabled, errors }: WeekGridProps) {
  const update = (weekday: number, patch: Partial<DayRow>) => {
    const current = rows[weekday] ?? { enabled: false, start_time: '09:00', duration_min: 30 };
    onChange({ ...rows, [weekday]: { ...current, ...patch } as DayRow });
  };

  return (
    <div className="space-y-2">
      {[0, 1, 2, 3, 4, 5, 6].map((wd) => {
        const row = rows[wd] ?? { enabled: false, start_time: '09:00', duration_min: 30 };
        return (
          <div
            key={wd}
            className="flex items-center gap-3 rounded-md border border-border-default px-3 py-2"
          >
            <span className="w-5 text-center text-sm font-medium text-text-secondary">
              {WEEKDAY_LABELS[wd]}
            </span>
            <Checkbox
              checked={row.enabled}
              onCheckedChange={(c) => update(wd, { enabled: c === true })}
              disabled={disabled}
              aria-label={`${WEEKDAY_LABELS[wd]}曜日 訪問あり`}
            />
            {row.enabled ? (
              <>
                <div className="flex items-center gap-1">
                  <select
                    value={row.start_time}
                    onChange={(e) => update(wd, { start_time: e.target.value })}
                    disabled={disabled}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary"
                    aria-label={`${WEEKDAY_LABELS[wd]} 開始時刻`}
                  >
                    {TIME_OPTIONS.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                  <span className="text-xs text-text-muted">開始</span>
                </div>
                <div className="flex items-center gap-1">
                  <select
                    value={row.duration_min}
                    onChange={(e) => update(wd, { duration_min: Number(e.target.value) })}
                    disabled={disabled}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary"
                    aria-label={`${WEEKDAY_LABELS[wd]} 所要時間`}
                  >
                    {DURATION_OPTIONS.map((d) => (
                      <option key={d} value={d}>
                        {d} 分
                      </option>
                    ))}
                  </select>
                </div>
                {errors[wd] ? <span className="text-xs text-error">{errors[wd]}</span> : null}
              </>
            ) : (
              <span className="text-xs text-text-muted">訪問なし</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Sub-component: ModePanel ─────────────────────────────────────────────────

interface ModePanelProps {
  patientId: string;
  mode: PatientFixedVisitMode;
  weeklyPattern?: WeeklyPattern | null;
  readonly?: boolean;
}

function ModePanel({ patientId, mode, weeklyPattern, readonly }: ModePanelProps) {
  const { data: reads = [], isLoading } = useFixedVisits(patientId, mode);
  const updateMut = useUpdateFixedVisits(patientId);
  const deleteMut = useDeleteFixedVisits(patientId);
  const fromWeekMut = useApplyFromWeek(patientId);

  const [rows, setRows] = React.useState<DayRows>(emptyDayRows);
  const [fieldErrors, setFieldErrors] = React.useState<Record<number, string>>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);

  // サーバーデータが変化したらフォームを同期
  React.useEffect(() => {
    if (!isLoading) {
      setRows(readsToDayRows(reads));
      setFieldErrors({});
      setFormError(null);
    }
  }, [reads, isLoading]);

  // ── 希望から自動生成 ──────────────────────────────────────────────────
  const handleAutoFill = () => {
    const newRows = weeklyPatternToDayRows(weeklyPattern);
    setRows(newRows);
    setFieldErrors({});
    setFormError(null);
    toast.success('希望パターンをフォームに反映しました (まだ保存されていません)');
  };

  // ── 現スケから取込 (Phase 2) ──────────────────────────────────────────
  const handleFromWeek = async () => {
    // 直近 ISO 週を計算
    const now = new Date();
    const jan4 = new Date(now.getFullYear(), 0, 4);
    const dayOfWeek = jan4.getDay() || 7;
    const startOfWeek1 = new Date(jan4);
    startOfWeek1.setDate(jan4.getDate() - dayOfWeek + 1);
    const diff = now.getTime() - startOfWeek1.getTime();
    const isoWeek = Math.floor(diff / (7 * 24 * 60 * 60 * 1000)) + 1;
    const isoYear = now.getFullYear();

    try {
      await fromWeekMut.mutateAsync({ iso_year: isoYear, iso_week: isoWeek, mode });
      toast.success('現在のスケジュールから固定枠を取り込みました');
    } catch (e) {
      const msg = e instanceof Error ? e.message : '取込に失敗しました';
      toast.error(msg);
    }
  };

  // ── 保存 ─────────────────────────────────────────────────────────────
  const handleSave = async () => {
    setFieldErrors({});
    setFormError(null);

    const items = dayRowsToItems(rows);
    const result = patientFixedVisitsBulkPutSchema.safeParse({ mode, items });

    if (!result.success) {
      const errs: Record<number, string> = {};
      let generalError = '';
      for (const issue of result.error.issues) {
        const path = issue.path;
        if (typeof path[0] === 'number' && path[1] === 'weekday') {
          const item = items[path[0]];
          if (item !== undefined) {
            errs[item.weekday] = issue.message;
          }
        } else {
          generalError = issue.message;
        }
      }
      setFieldErrors(errs);
      if (generalError) setFormError(generalError);
      return;
    }

    try {
      await updateMut.mutateAsync(result.data);
      toast.success('固定枠を保存しました');
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存に失敗しました';
      setFormError(msg);
      toast.error(`保存に失敗しました: ${msg}`);
    }
  };

  // ── リセット (DELETE) ─────────────────────────────────────────────────
  const handleDelete = async () => {
    setDeleteDialogOpen(false);
    try {
      await deleteMut.mutateAsync(mode);
      setRows(emptyDayRows());
      setFieldErrors({});
      setFormError(null);
      toast.success(`${mode === 'normal' ? '通常' : '特別週'}の固定枠を削除しました`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '削除に失敗しました';
      toast.error(`削除に失敗しました: ${msg}`);
    }
  };

  const isBusy = updateMut.isPending || deleteMut.isPending || fromWeekMut.isPending;

  return (
    <div className="space-y-4">
      {isLoading ? (
        <p className="text-sm text-text-muted">読み込み中...</p>
      ) : (
        <WeekGrid
          rows={rows}
          onChange={setRows}
          disabled={readonly || isBusy}
          errors={fieldErrors}
        />
      )}

      {formError ? <p className="text-xs text-error">{formError}</p> : null}

      {!readonly && (
        <div className="flex flex-wrap gap-2 pt-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleAutoFill}
            disabled={isBusy}
          >
            希望から自動生成
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleFromWeek()}
            disabled={isBusy || fromWeekMut.isPending}
          >
            {fromWeekMut.isPending ? '取込中...' : '現在のスケジュールから取込'}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="text-error hover:bg-error/10"
            onClick={() => setDeleteDialogOpen(true)}
            disabled={isBusy}
          >
            リセット (全削除)
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => void handleSave()}
            disabled={isBusy || isLoading}
          >
            {updateMut.isPending ? '保存中...' : '保存'}
          </Button>
        </div>
      )}

      {/* 削除確認ダイアログ */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent aria-describedby="delete-dialog-desc">
          <DialogHeader>
            <DialogTitle>固定枠を削除しますか？</DialogTitle>
          </DialogHeader>
          <p id="delete-dialog-desc" className="text-sm text-text-secondary">
            {mode === 'normal' ? '通常週' : '特別週'}の固定枠をすべて削除します。
            この操作は元に戻せません。
          </p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDeleteDialogOpen(false)}>
              キャンセル
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void handleDelete()}
              disabled={deleteMut.isPending}
            >
              {deleteMut.isPending ? '削除中...' : '削除する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

export interface PatientFixedVisitsPanelProps {
  /** 対象患者の ID */
  patientId: string;
  /** 患者の週間訪問希望パターン (希望から自動生成 ボタンで使用) */
  weeklyPattern?: WeeklyPattern | null;
}

export function PatientFixedVisitsPanel({
  patientId,
  weeklyPattern,
}: PatientFixedVisitsPanelProps) {
  const { data: session } = useSession();
  const role = session?.user?.role;
  const readonly = role !== 'admin' && role !== 'manager';

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-lg font-bold text-text-primary">
          週間訪問パターン (固定枠)
        </h2>
        {readonly && (
          <span className="text-xs text-text-muted bg-bg-muted rounded px-2 py-0.5">閲覧のみ</span>
        )}
      </div>

      <Tabs defaultValue="normal">
        <TabsList>
          {PATIENT_FIXED_VISIT_MODES.map((m) => (
            <TabsTrigger key={m} value={m}>
              {m === 'normal' ? '通常' : '特別週'}
            </TabsTrigger>
          ))}
        </TabsList>

        {PATIENT_FIXED_VISIT_MODES.map((m) => (
          <TabsContent key={m} value={m}>
            <ModePanel
              patientId={patientId}
              mode={m}
              weeklyPattern={weeklyPattern}
              readonly={readonly}
            />
          </TabsContent>
        ))}
      </Tabs>
    </Card>
  );
}
