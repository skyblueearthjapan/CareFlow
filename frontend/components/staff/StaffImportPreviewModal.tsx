'use client';

import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type {
  ShiftExcelImportRow,
  StaffExcelImportResponse,
  StaffExcelImportRow,
} from '@/lib/schemas/staffExcel';

// ─── helpers ─────────────────────────────────────────────────────────────────

/** Format a raw cell value for human display. */
function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return '(空)';
  if (v === '<CLEAR>') return '(NULL に変更)';
  return String(v);
}

const OPERATION_LABEL: Record<string, string> = {
  new: '新規',
  update: '更新',
  delete: '削除',
  error: 'エラー',
  noop: '変更なし',
};

/** weekday (0=月..6=日) → 日本語ラベル. backend schema.WEEKDAY_LABEL と一致. */
const WEEKDAY_JA = ['月', '火', '水', '木', '金', '土', '日'] as const;

// ─── row renderers ────────────────────────────────────────────────────────────

function StaffRowCard({ row }: { row: StaffExcelImportRow }) {
  const opLabel = OPERATION_LABEL[row.operation] ?? row.operation;
  const label = row.staff_code ? `${row.staff_code} ` : '';

  return (
    <div className="rounded-md border border-border-default p-3 text-sm">
      {row.operation === 'error' ? (
        <p className="font-medium text-error">
          ⚠️ Excel 行 {row.row_number}: {label}— {row.error_message ?? '不明なエラー'}
        </p>
      ) : (
        <>
          <p className="font-medium text-text-primary">
            Excel 行 {row.row_number}: {label}を{opLabel}
          </p>
          {row.changes.length > 0 ? (
            <ul className="mt-1.5 space-y-0.5 text-text-secondary">
              {row.changes.map((c, i) => (
                <li key={i}>
                  📝 <span className="font-mono text-xs">{c.field}</span>: 「{fmtValue(c.old_value)}
                  」→「{fmtValue(c.new_value)}」
                </li>
              ))}
            </ul>
          ) : null}
        </>
      )}
    </div>
  );
}

function ShiftRowCard({ row }: { row: ShiftExcelImportRow }) {
  const opLabel = OPERATION_LABEL[row.operation] ?? row.operation;
  const label = row.staff_code ? `${row.staff_code} ` : '';

  return (
    <div className="rounded-md border border-border-default p-3 text-sm">
      {row.operation === 'error' ? (
        <p className="font-medium text-error">
          ⚠️ Excel 行 {row.row_number}: {label}— {row.error_message ?? '不明なエラー'}
        </p>
      ) : (
        <>
          <p className="font-medium text-text-primary">
            Excel 行 {row.row_number}: {label}
            {row.weekday !== null ? `${WEEKDAY_JA[row.weekday] ?? row.weekday}曜 ` : ''}を{opLabel}
          </p>
          {row.changes.length > 0 ? (
            <ul className="mt-1.5 space-y-0.5 text-text-secondary">
              {row.changes.map((c, i) => (
                <li key={i}>
                  📝 <span className="font-mono text-xs">{c.field}</span>: 「{fmtValue(c.old_value)}
                  」→「{fmtValue(c.new_value)}」
                </li>
              ))}
            </ul>
          ) : null}
        </>
      )}
    </div>
  );
}

// ─── tab panel ────────────────────────────────────────────────────────────────

type StaffTab = 'new' | 'update' | 'delete' | 'error';

function StaffTabPanel({ rows, tab }: { rows: StaffExcelImportRow[]; tab: StaffTab }) {
  const filtered = rows.filter((r) => r.operation === tab);
  if (filtered.length === 0) {
    return <p className="py-4 text-center text-sm text-text-muted">該当行なし</p>;
  }
  return (
    <div className="space-y-2">
      {tab === 'error' ? (
        <p className="rounded-md border border-error/40 bg-error/5 p-2 text-xs text-error">
          ⚠️ これらの行は反映されません (skip)。Excel を修正して再度インポートしてください。
        </p>
      ) : null}
      {filtered.map((r) => (
        <StaffRowCard key={r.row_number} row={r} />
      ))}
    </div>
  );
}

function ShiftTabPanel({ rows }: { rows: ShiftExcelImportRow[] }) {
  const active = rows.filter((r) => r.operation !== 'noop');
  if (active.length === 0) {
    return <p className="py-4 text-center text-sm text-text-muted">勤務シフト変更なし</p>;
  }
  return (
    <div className="space-y-2">
      {active.map((r, i) => (
        <ShiftRowCard key={i} row={r} />
      ))}
    </div>
  );
}

// ─── main modal ──────────────────────────────────────────────────────────────

export interface StaffImportPreviewModalProps {
  open: boolean;
  preview: StaffExcelImportResponse | null;
  isApplying: boolean;
  onApply: () => void;
  onClose: () => void;
}

export function StaffImportPreviewModal({
  open,
  preview,
  isApplying,
  onApply,
  onClose,
}: StaffImportPreviewModalProps) {
  const [tab, setTab] = useState<string>('new');

  if (!preview) return null;

  const { summary, staff_rows, shift_rows } = preview;

  // partial commit: error 行は skip (反映されない). 有効行 (new/update/delete) のみ反映.
  const applyCount =
    summary.staff_new +
    summary.staff_update +
    summary.staff_delete +
    summary.shift_new +
    summary.shift_update +
    summary.shift_delete;
  const errorCount = summary.staff_error + summary.shift_error;
  const applyButtonLabel =
    errorCount > 0
      ? `✅ 反映可能な ${applyCount} 件のみ反映する (エラー ${errorCount} 件は skip)`
      : `✅ ${applyCount} 件を反映する`;

  const shiftActive = shift_rows.filter((r) => r.operation !== 'noop').length;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent
        className="max-w-2xl max-h-[90vh] flex flex-col"
        aria-describedby="staff-import-preview-desc"
      >
        <DialogHeader>
          <DialogTitle>スタッフマスタ Excel インポート プレビュー</DialogTitle>
        </DialogHeader>

        {/* Summary */}
        <div
          id="staff-import-preview-desc"
          className="rounded-md border border-border-default bg-bg-muted/40 p-3 text-sm space-y-1"
        >
          <p className="font-medium text-text-primary">サマリ</p>
          <p className="text-text-secondary">
            スタッフ:&nbsp;
            <span className="text-success">🆕 新規 {summary.staff_new}</span>&nbsp;
            <span className="text-info">✏️ 更新 {summary.staff_update}</span>&nbsp;
            <span className="text-text-muted">🗑️ 削除 {summary.staff_delete}</span>&nbsp;
            <span className={summary.staff_error > 0 ? 'text-error' : 'text-text-muted'}>
              ⚠️ エラー {summary.staff_error}
            </span>
          </p>
          <p className="text-text-secondary">
            勤務シフト:&nbsp;
            <span className="text-success">🆕 新規 {summary.shift_new}</span>&nbsp;
            <span className="text-info">✏️ 更新 {summary.shift_update}</span>&nbsp;
            <span className="text-text-muted">🗑️ 削除 {summary.shift_delete}</span>&nbsp;
            <span className={summary.shift_error > 0 ? 'text-error' : 'text-text-muted'}>
              ⚠️ エラー {summary.shift_error}
            </span>
          </p>
        </div>

        {/* Tabs */}
        <Tabs value={tab} onValueChange={setTab} className="flex-1 min-h-0 flex flex-col">
          <TabsList className="w-full justify-start h-auto flex-wrap gap-1 shrink-0">
            <TabsTrigger value="new" className="gap-1.5">
              新規
              {summary.staff_new > 0 ? (
                <Badge variant="success" className="text-xs px-1.5 py-0">
                  {summary.staff_new}
                </Badge>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value="update" className="gap-1.5">
              更新
              {summary.staff_update > 0 ? (
                <Badge variant="info" className="text-xs px-1.5 py-0">
                  {summary.staff_update}
                </Badge>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value="delete" className="gap-1.5">
              削除
              {summary.staff_delete > 0 ? (
                <Badge variant="secondary" className="text-xs px-1.5 py-0">
                  {summary.staff_delete}
                </Badge>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value="error" className="gap-1.5">
              エラー
              {summary.staff_error > 0 ? (
                <Badge variant="destructive" className="text-xs px-1.5 py-0">
                  {summary.staff_error}
                </Badge>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value="shift" className="gap-1.5">
              勤務シフト変更
              {shiftActive > 0 ? (
                <Badge variant="secondary" className="text-xs px-1.5 py-0">
                  {shiftActive}
                </Badge>
              ) : null}
            </TabsTrigger>
          </TabsList>

          <div className="flex-1 overflow-y-auto mt-2 pr-1">
            <TabsContent value="new">
              <StaffTabPanel rows={staff_rows} tab="new" />
            </TabsContent>
            <TabsContent value="update">
              <StaffTabPanel rows={staff_rows} tab="update" />
            </TabsContent>
            <TabsContent value="delete">
              <StaffTabPanel rows={staff_rows} tab="delete" />
            </TabsContent>
            <TabsContent value="error">
              <StaffTabPanel rows={staff_rows} tab="error" />
            </TabsContent>
            <TabsContent value="shift">
              <ShiftTabPanel rows={shift_rows} />
            </TabsContent>
          </div>
        </Tabs>

        <DialogFooter className="shrink-0 border-t border-border-default pt-3">
          <Button variant="outline" onClick={onClose} disabled={isApplying}>
            キャンセル
          </Button>
          <Button onClick={onApply} disabled={isApplying || applyCount === 0}>
            {isApplying ? '反映中...' : applyButtonLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
