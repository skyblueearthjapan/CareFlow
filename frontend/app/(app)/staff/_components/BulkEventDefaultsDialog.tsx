'use client';

/**
 * BulkEventDefaultsDialog — 「固定イベントを一括登録」ダイアログ (Phase 3)。
 *
 * 正典 = docs/plans/staff-event-history-design.md §2 Phase 3 /
 *        docs/mockups/event-defaults-bulk-mock.html 変更A。
 *
 * スタッフ × 曜日の全組を 1 回の操作で登録する **汎用** ダイアログ。
 * 朝会専用の分岐・定数は持たない (PO Q5: 朝会はデータであってコードではない) —
 * 朝会はこのダイアログから登録される 1 件にすぎない。
 */
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/sonner';
import { compareByKana } from '@/lib/kana-sort';
import { useEventTemplates } from '@/lib/queries/event-templates';
import { useBulkCreateEventDefaults } from '@/lib/queries/staff-event-defaults';
import { useStaffList } from '@/lib/queries/staff';
import { useManyStaffShifts } from '@/lib/queries/staff-shifts';
import type { StaffShiftItem } from '@/lib/schemas/staff-shifts';

import { WEEKDAYS_ALL, WeekdayPicker } from './WeekdayPicker';

/** 「☀ 9:00出勤の全員を選択」のしきい値。これ以前の出勤を「朝から」とみなす。 */
export const EARLY_SHIFT_THRESHOLD = '09:00';

/**
 * 「選択中の曜日すべてで出勤していて、開始が {threshold} 以前」か。
 * 曜日が未選択のときは月〜土で判定する。
 */
export function isEarlyShiftStaff(
  shifts: StaffShiftItem[] | undefined,
  weekdays: readonly number[],
  threshold: string = EARLY_SHIFT_THRESHOLD,
): boolean {
  if (!shifts || shifts.length === 0) return false;
  const target = weekdays.length > 0 ? weekdays : WEEKDAYS_ALL;
  return target.every((w) => {
    const row = shifts.find((s) => s.weekday === w);
    return !!row && row.is_on && !!row.start_time && row.start_time <= threshold;
  });
}

/** 対象曜日の出勤開始時刻を要約 (「9:00出勤」/「8:30〜9:00出勤」/「出勤なし」)。 */
export function shiftStartSummary(
  shifts: StaffShiftItem[] | undefined,
  weekdays: readonly number[],
): string {
  if (!shifts) return 'シフト未取得';
  const target = weekdays.length > 0 ? weekdays : WEEKDAYS_ALL;
  const starts = target
    .map((w) => shifts.find((s) => s.weekday === w))
    .filter((s): s is StaffShiftItem => !!s && s.is_on && !!s.start_time)
    .map((s) => s.start_time!);
  if (starts.length === 0) return '出勤なし';
  const uniq = Array.from(new Set(starts)).sort();
  return uniq.length === 1 ? `${uniq[0]}出勤` : `${uniq[0]}〜${uniq[uniq.length - 1]}出勤`;
}

export interface BulkEventDefaultsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function BulkEventDefaultsDialog({ open, onOpenChange }: BulkEventDefaultsDialogProps) {
  const { data: staffData } = useStaffList({ limit: 500 });
  const { data: templateData } = useEventTemplates();
  const bulk = useBulkCreateEventDefaults();

  const [templateId, setTemplateId] = useState('');
  const [title, setTitle] = useState('');
  const [start, setStart] = useState('09:00');
  const [end, setEnd] = useState('09:15');
  const [weekdays, setWeekdays] = useState<number[]>([...WEEKDAYS_ALL]);
  const [selected, setSelected] = useState<string[]>([]);
  const [blocking, setBlocking] = useState(false);

  const staffRows = useMemo(
    () => [...(staffData ?? []).filter((s) => s.status === 'active')].sort(compareByKana),
    [staffData],
  );
  const staffIds = useMemo(() => staffRows.map((s) => s.id), [staffRows]);
  const { byStaffId } = useManyStaffShifts(open ? staffIds : []);

  // 共通ひな形のみ (個人ひな形は「対象スタッフ複数」の一括登録と噛み合わない)。
  const templates = useMemo(
    () => (templateData ?? []).filter((t) => t.is_shared && t.is_active),
    [templateData],
  );

  // 開くたびに初期状態へ戻す。
  useEffect(() => {
    if (!open) return;
    setTemplateId('');
    setTitle('');
    setStart('09:00');
    setEnd('09:15');
    setWeekdays([...WEEKDAYS_ALL]);
    setSelected([]);
    setBlocking(false);
  }, [open]);

  function applyTemplate(id: string) {
    setTemplateId(id);
    const t = templates.find((x) => x.id === id);
    if (!t) return;
    setTitle(t.title);
    if (t.start_time && t.end_time) {
      setStart(t.start_time);
      setEnd(t.end_time);
    }
    setBlocking(t.blocking);
  }

  function toggleStaff(id: string) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function selectEarlyShift() {
    setSelected(staffIds.filter((id) => isEarlyShiftStaff(byStaffId.get(id), weekdays)));
  }

  const trimmed = title.trim();
  const count = selected.length * weekdays.length;
  const timeInvalid = !start || !end || start >= end;
  const canSubmit = !!trimmed && !timeInvalid && count > 0 && !bulk.isPending;

  const preview =
    count > 0
      ? `${selected.length}名 × ${weekdays.length}曜日 = ${count}件の固定イベントを登録します（既に同じ登録がある分は自動でスキップ）`
      : '曜日とスタッフを選んでください';

  async function handleSubmit() {
    try {
      const result = await bulk.mutateAsync({
        staff_ids: selected,
        weekdays,
        start_time: start,
        end_time: end,
        title: trimmed,
        blocking,
      });
      toast.success(
        `${result.created}件登録しました（${result.skipped}件は登録済みのためスキップ）`,
      );
      onOpenChange(false);
    } catch (e) {
      toast.error('一括登録に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onOpenChange(false)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>固定イベントを一括登録</DialogTitle>
          <DialogDescription>
            選んだスタッフ × 曜日のぶんだけ「毎週の固定イベント」を作成します。週生成のたびに自動で予定に入ります（休みの日は自動で不参加になります）。
          </DialogDescription>
        </DialogHeader>

        <div className="grid max-h-[65vh] gap-3 overflow-y-auto pr-1">
          <div className="space-y-1">
            <Label htmlFor="bulk-template">📋 ひな形から選ぶ / 手入力</Label>
            <select
              id="bulk-template"
              value={templateId}
              onChange={(e) => applyTemplate(e.target.value)}
              data-testid="bulk-template-select"
              className="h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm"
            >
              <option value="">— 手入力 —</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                  {t.start_time && t.end_time ? `（${t.start_time}〜${t.end_time}）` : ''}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <Label htmlFor="bulk-title">タイトル</Label>
            <Input
              id="bulk-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={255}
              placeholder="例: 朝会"
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label htmlFor="bulk-start">開始</Label>
              <Input
                id="bulk-start"
                type="time"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="bulk-end">終了</Label>
              <Input
                id="bulk-end"
                type="time"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-1">
            <Label>曜日（複数選択）</Label>
            <WeekdayPicker value={weekdays} onChange={setWeekdays} idPrefix="bulk" />
          </div>

          <div className="space-y-1">
            <Label>対象スタッフ（複数選択）</Label>
            <div className="grid grid-cols-1 gap-x-3 rounded-md border border-border-default p-2 sm:grid-cols-2">
              {staffRows.map((s) => (
                <label
                  key={s.id}
                  className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-bg-muted"
                >
                  <Checkbox
                    checked={selected.includes(s.id)}
                    onCheckedChange={() => toggleStaff(s.id)}
                    aria-label={s.name}
                  />
                  <span className="truncate">{s.name}</span>
                  <span className="shrink-0 text-[11px] text-text-muted">
                    {shiftStartSummary(byStaffId.get(s.id), weekdays)}
                  </span>
                </label>
              ))}
              {staffRows.length === 0 && (
                <p className="px-1 py-1 text-sm text-text-muted">対象スタッフがいません</p>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-1.5 pt-1 text-xs">
              <button
                type="button"
                onClick={selectEarlyShift}
                data-testid="bulk-select-early"
                className="rounded-md border border-border-default px-2 py-1 text-text-secondary hover:bg-bg-muted"
              >
                ☀ 9:00出勤の全員を選択
              </button>
              <button
                type="button"
                onClick={() => setSelected(staffIds)}
                data-testid="bulk-select-all"
                className="rounded-md border border-border-default px-2 py-1 text-text-secondary hover:bg-bg-muted"
              >
                全員
              </button>
              <button
                type="button"
                onClick={() => setSelected([])}
                data-testid="bulk-select-none"
                className="rounded-md border border-border-default px-2 py-1 text-text-secondary hover:bg-bg-muted"
              >
                解除
              </button>
              <label className="ml-auto flex items-center gap-2 text-[13px]">
                <Checkbox
                  checked={blocking}
                  onCheckedChange={(v) => setBlocking(v === true)}
                  aria-label="絶対に潰せないイベントにする"
                />
                🔒 絶対に潰せないイベントにする
              </label>
            </div>
          </div>

          <p
            className="rounded-md border border-success bg-success-bg px-3 py-2 text-sm font-bold text-success"
            data-testid="bulk-preview"
          >
            {preview}
          </p>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            キャンセル
          </Button>
          <Button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            data-testid="bulk-submit"
          >
            {bulk.isPending ? '登録中…' : '一括登録する'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
