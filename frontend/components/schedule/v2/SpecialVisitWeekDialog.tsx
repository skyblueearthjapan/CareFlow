'use client';

/**
 * SpecialVisitWeekDialog — 特別訪問週間の設定モーダル (大型・共通コンポーネント).
 *
 * 正典: `docs/plans/special-visit-week-design.md` §6-1.
 *
 * 入り口は 2 箇所:
 *   ① 患者マスタ編集 (`app/(app)/patients/_components/PatientForm.tsx`)
 *   ② スケジュール画面の患者詳細 (`PatientScheduleDetailDialog`)
 *
 * 画面は期間の有無で 2 モード:
 *   - 期間未設定 → 作成フォーム (開始日 / 期間チップ / 目標回数 / メモ)
 *   - 期間あり   → カレンダー (行=期間内の各 ISO 週・列=月〜土)
 *
 * カレンダーの操作:
 *   - 空きスペースクリック  → ○追加 (POST marks)
 *   - ○ / ● クリック        → 取消 (DELETE marks/{id}。● は確認して force=true)
 *   - 固定訪問の退避トグル   → 「固定どおり」⇄「この日はプールへ退避」
 *                             (POST displace / POST marks/{id}/restore。
 *                              配置済み退避の解除は確認 → force=true)
 *
 * 週合計 (設計書 §3): 固定訪問の残数 + extra ○ (pool/placed 両方) + displaced
 * チケット数。BE が `week.total` / `week.target_met` として返すので FE は
 * 再計算しない (= 判定ロジックの二重持ちを避ける)。
 *
 * 意匠は既存トークンのみ (bg-bg-base / border-border-default / text-text-* /
 * brand-primary / success-bg / error-bg)。固定訪問カードは性別情報が calendar API
 * に含まれないため、性別ウォッシュではなく中立カード (bg-bg-muted) で描く。
 */
import * as React from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  useCreateSpecialVisitMark,
  useCreateSpecialVisitPeriod,
  useDeleteSpecialVisitMark,
  useDisplaceSpecialVisit,
  useRestoreSpecialVisitMark,
  useSpecialVisitCalendar,
  useSpecialVisitPeriods,
  useUpdateSpecialVisitPeriod,
} from '@/lib/queries/specialVisitWeek';
import type {
  SpecialCalendarDay,
  SpecialCalendarWeek,
  SpecialVisitMark,
  SpecialVisitPeriod,
} from '@/lib/schemas/specialVisitWeek';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

// ---------------------------------------------------------------------------
// 日付ヘルパー (すべて "YYYY-MM-DD" 文字列で扱う — 文字列比較で大小判定できる)
// ---------------------------------------------------------------------------

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

function toISODate(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

export function todayISODate(): string {
  return toISODate(new Date());
}

function parseISODate(iso: string): Date | null {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

/** 期間クイック選択チップ. `days` 指定は「開始日を含む N 日間」. */
export interface PeriodPreset {
  label: string;
  days?: number;
  months?: number;
}

export const PERIOD_PRESETS: readonly PeriodPreset[] = [
  { label: '1週間', days: 7 },
  { label: '2週間', days: 14 },
  { label: '3週間', days: 21 },
  { label: '4週間', days: 28 },
  { label: '1ヶ月', months: 1 },
  { label: '2ヶ月', months: 2 },
] as const;

/**
 * 開始日 + プリセット → 終了日 (含む) を計算する.
 *
 * 週指定は「開始日を含む N*7 日間」なので `start + days - 1`。
 * 月指定は「翌月同日の前日」= `addMonths(start, n) - 1 日`。
 */
export function computeEndDate(startDate: string, preset: PeriodPreset): string {
  const start = parseISODate(startDate);
  if (!start) return startDate;
  const d = new Date(start.getTime());
  if (preset.months) {
    d.setMonth(d.getMonth() + preset.months);
  } else {
    d.setDate(d.getDate() + (preset.days ?? 7));
  }
  d.setDate(d.getDate() - 1);
  return toISODate(d);
}

/** "HH:MM:SS" → "HH:MM". */
function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

/** 有効な (取消済みでない) マークだけを返す. */
function liveMark(mark: SpecialVisitMark | null): SpecialVisitMark | null {
  if (!mark) return null;
  return mark.status === 'cancelled' ? null : mark;
}

/** 週の days を weekday → day の Map に正規化する (欠落曜日に強くする). */
function daysByWeekday(week: SpecialCalendarWeek): Map<number, SpecialCalendarDay> {
  const m = new Map<number, SpecialCalendarDay>();
  for (const d of week.days) m.set(d.weekday, d);
  return m;
}

function weekRowLabel(week: SpecialCalendarWeek): string {
  const monday = parseISODate(week.week_monday);
  if (!monday) return `W${week.iso_week}`;
  return `${monday.getMonth() + 1}/${monday.getDate()}週`;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** 目標回数は BE が 1〜7 (週の日数上限) に制限しているので入力側でも丸める. */
function clampTarget(raw: string): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 1;
  return Math.min(7, Math.max(1, Math.round(n)));
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SpecialVisitWeekDialogProps {
  patientId: string;
  patientName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SpecialVisitWeekDialog({
  patientId,
  patientName,
  open,
  onOpenChange,
}: SpecialVisitWeekDialogProps) {
  const periodsQuery = useSpecialVisitPeriods(open ? patientId : null);
  const periods = periodsQuery.data ?? [];
  const activePeriod: SpecialVisitPeriod | null =
    periods.find((p) => p.status === 'active') ?? null;

  const calendarQuery = useSpecialVisitCalendar(open ? (activePeriod?.id ?? null) : null);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-5xl"
        aria-describedby="special-visit-week-description"
        data-testid="special-visit-week-dialog"
      >
        <DialogHeader>
          <DialogTitle>
            特別訪問週間
            <span className="ml-2 text-sm font-normal text-text-secondary">{patientName}</span>
          </DialogTitle>
          <DialogDescription id="special-visit-week-description">
            期間と週の目標回数を決めて、カレンダーに ○ を付けると追加の訪問枠がプールに積まれます。
            基本の固定訪問はそのまま生きています。
          </DialogDescription>
        </DialogHeader>

        {periodsQuery.isLoading ? (
          <div
            className="flex items-center gap-2 py-8 text-sm text-text-secondary"
            data-testid="svw-loading"
          >
            <Loader2 className="h-4 w-4 animate-spin" />
            読み込み中…
          </div>
        ) : periodsQuery.isError ? (
          <div
            className="rounded border border-border-error bg-error-bg p-3 text-sm text-error"
            data-testid="svw-error"
          >
            期間の取得に失敗しました
          </div>
        ) : activePeriod ? (
          <PeriodCalendar
            period={activePeriod}
            weeks={calendarQuery.data?.weeks ?? []}
            isLoading={calendarQuery.isLoading}
            isError={calendarQuery.isError}
          />
        ) : (
          <PeriodCreateForm patientId={patientId} />
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            閉じる
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 期間未設定 → 作成フォーム
// ---------------------------------------------------------------------------

/** 既定のプリセット (2週間). PO 指定が無いため中庸の長さを初期選択にする. */
const DEFAULT_PRESET_LABEL = '2週間';

function PeriodCreateForm({ patientId }: { patientId: string }) {
  const [startDate, setStartDate] = React.useState<string>(() => todayISODate());
  const [presetLabel, setPresetLabel] = React.useState<string>(DEFAULT_PRESET_LABEL);
  const [weeklyTarget, setWeeklyTarget] = React.useState<number>(5);
  const [note, setNote] = React.useState<string>('');

  const createMut = useCreateSpecialVisitPeriod();

  const preset = PERIOD_PRESETS.find((p) => p.label === presetLabel) ?? PERIOD_PRESETS[0]!;
  const endDate = computeEndDate(startDate, preset);

  const handleCreate = React.useCallback(() => {
    createMut.mutate(
      {
        patient_id: patientId,
        start_date: startDate,
        end_date: endDate,
        weekly_target: weeklyTarget,
        note: note.trim() ? note.trim() : null,
      },
      {
        onSuccess: () => toast.success('特別訪問週間を開始しました'),
        onError: (err) => toast.error(`開始できませんでした: ${errorMessage(err)}`),
      },
    );
  }, [createMut, patientId, startDate, endDate, weeklyTarget, note]);

  return (
    <div className="space-y-4" data-testid="svw-create-form">
      <p className="text-sm text-text-secondary">
        この患者はまだ特別訪問週間を設定していません。期間と目標を決めて開始してください。
      </p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-text-secondary">開始日</span>
          <Input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            data-testid="svw-start-date"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-text-secondary">目標回数 (週N回以上)</span>
          <Input
            type="number"
            min={1}
            max={7}
            value={weeklyTarget}
            onChange={(e) => setWeeklyTarget(clampTarget(e.target.value))}
            data-testid="svw-weekly-target"
          />
        </label>
      </div>

      <div className="space-y-2">
        <span className="text-sm font-medium text-text-secondary">期間</span>
        <div className="flex flex-wrap gap-2" data-testid="svw-preset-chips">
          {PERIOD_PRESETS.map((p) => {
            const selected = p.label === presetLabel;
            return (
              <button
                key={p.label}
                type="button"
                onClick={() => setPresetLabel(p.label)}
                data-testid={`svw-preset-${p.label}`}
                data-selected={selected ? 'true' : 'false'}
                className={
                  selected
                    ? 'rounded-full border border-brand-primary bg-brand-primary px-3 py-1 text-xs font-medium text-white'
                    : 'rounded-full border border-border-default bg-bg-base px-3 py-1 text-xs text-text-primary hover:bg-bg-muted'
                }
              >
                {p.label}
              </button>
            );
          })}
        </div>
        <p className="text-xs text-text-muted tnum" data-testid="svw-computed-range">
          {startDate} 〜 {endDate}
        </p>
      </div>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-text-secondary">メモ (任意)</span>
        <textarea
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          data-testid="svw-note"
          className="w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-visible:border-brand-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary-light"
        />
      </label>

      <div className="flex justify-end">
        <Button
          type="button"
          onClick={handleCreate}
          disabled={createMut.isPending}
          data-testid="svw-create-button"
        >
          {createMut.isPending ? (
            <>
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              作成中…
            </>
          ) : (
            '特別訪問週間を開始する'
          )}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 期間あり → カレンダー
// ---------------------------------------------------------------------------

interface PeriodCalendarProps {
  period: SpecialVisitPeriod;
  weeks: SpecialCalendarWeek[];
  isLoading: boolean;
  isError: boolean;
}

function PeriodCalendar({ period, weeks, isLoading, isError }: PeriodCalendarProps) {
  const createMark = useCreateSpecialVisitMark();
  const deleteMark = useDeleteSpecialVisitMark();
  const displaceMut = useDisplaceSpecialVisit();
  const restoreMut = useRestoreSpecialVisitMark();

  const busy =
    createMark.isPending || deleteMark.isPending || displaceMut.isPending || restoreMut.isPending;

  const handleAddMark = React.useCallback(
    (week: SpecialCalendarWeek, day: SpecialCalendarDay) => {
      createMark.mutate(
        {
          periodId: period.id,
          payload: { iso_year: week.iso_year, iso_week: week.iso_week, weekday: day.weekday },
        },
        {
          onError: (err) => toast.error(`追加できませんでした: ${errorMessage(err)}`),
        },
      );
    },
    [createMark, period.id],
  );

  const handleRemoveMark = React.useCallback(
    (mark: SpecialVisitMark) => {
      const placed = mark.status === 'placed';
      if (placed) {
        const ok = window.confirm(
          'この枠は既にスケジュールへ配置されています。取り消すと配置済みの訪問も削除されます。よろしいですか？',
        );
        if (!ok) return;
      }
      deleteMark.mutate(
        { markId: mark.id, force: placed },
        { onError: (err) => toast.error(`取消できませんでした: ${errorMessage(err)}`) },
      );
    },
    [deleteMark],
  );

  const handleToggleDisplace = React.useCallback(
    (week: SpecialCalendarWeek, day: SpecialCalendarDay) => {
      const current = liveMark(day.displaced_mark);
      if (current) {
        // 退避解除 (「固定どおり」へ戻す). 配置済みは確認 → force=true.
        const placed = current.status === 'placed';
        if (placed) {
          const ok = window.confirm(
            '退避した枠は既に別の時間へ配置されています。固定どおりに戻すと配置済みの訪問は削除されます。よろしいですか？',
          );
          if (!ok) return;
        }
        restoreMut.mutate(
          { markId: current.id, force: placed },
          { onError: (err) => toast.error(`戻せませんでした: ${errorMessage(err)}`) },
        );
        return;
      }
      displaceMut.mutate(
        {
          periodId: period.id,
          payload: { iso_year: week.iso_year, iso_week: week.iso_week, weekday: day.weekday },
        },
        { onError: (err) => toast.error(`退避できませんでした: ${errorMessage(err)}`) },
      );
    },
    [displaceMut, restoreMut, period.id],
  );

  return (
    <div className="space-y-4" data-testid="svw-calendar">
      <PeriodControls period={period} />

      {isLoading ? (
        <div
          className="flex items-center gap-2 py-8 text-sm text-text-secondary"
          data-testid="svw-calendar-loading"
        >
          <Loader2 className="h-4 w-4 animate-spin" />
          カレンダーを読み込み中…
        </div>
      ) : isError ? (
        <div
          className="rounded border border-border-error bg-error-bg p-3 text-sm text-error"
          data-testid="svw-calendar-error"
        >
          カレンダーの取得に失敗しました
        </div>
      ) : weeks.length === 0 ? (
        <div className="py-6 text-center text-sm text-text-muted">
          この期間に対象の週がありません。
        </div>
      ) : (
        <div className="overflow-x-auto">
          <div className="min-w-[52rem]">
            {/* ヘッダ行 */}
            <div className="grid grid-cols-[5rem_repeat(6,1fr)_7rem] gap-1 pb-1">
              <div />
              {WEEKDAY_LABELS.map((label) => (
                <div key={label} className="px-1 text-center text-xs font-semibold text-text-muted">
                  {label}
                </div>
              ))}
              <div className="px-1 text-center text-xs font-semibold text-text-muted">週合計</div>
            </div>

            {weeks.map((week, wi) => {
              const byWd = daysByWeekday(week);
              return (
                <div
                  key={`${week.iso_year}-${week.iso_week}`}
                  className="grid grid-cols-[5rem_repeat(6,1fr)_7rem] items-stretch gap-1 py-1"
                  data-testid={`svw-week-row-${wi}`}
                >
                  <div className="flex flex-col justify-center px-1 text-xs text-text-secondary tnum">
                    <span className="font-semibold">{weekRowLabel(week)}</span>
                    <span className="text-[10px] text-text-muted">
                      {week.iso_year}-W{pad2(week.iso_week)}
                    </span>
                  </div>

                  {WEEKDAY_LABELS.map((_label, wd) => {
                    const day = byWd.get(wd) ?? null;
                    return (
                      <CalendarCell
                        key={wd}
                        weekIndex={wi}
                        weekday={wd}
                        day={day}
                        period={period}
                        busy={busy}
                        onAddMark={(d) => handleAddMark(week, d)}
                        onRemoveMark={handleRemoveMark}
                        onToggleDisplace={(d) => handleToggleDisplace(week, d)}
                      />
                    );
                  })}

                  <div className="flex items-center justify-center">
                    {week.target_met ? (
                      <Badge
                        variant="success"
                        data-testid={`svw-total-${wi}`}
                        data-met="true"
                        className="tnum"
                      >
                        {week.total}回 ✓
                      </Badge>
                    ) : (
                      <Badge
                        data-testid={`svw-total-${wi}`}
                        data-met="false"
                        className="border-transparent bg-error-bg text-error tnum"
                      >
                        {week.total}回 / 目標{period.weekly_target}
                      </Badge>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <p className="text-xs text-text-muted">
        ○ = 追加枠 (未配置・プールに積まれます) / ● = 配置済み。空いているところをクリックすると ○
        を足せます。固定訪問は「この日はプールへ退避」で一時的に外せます
        (恒久パターンは変わりません)。
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 期間そのものの操作 (目標変更 / 延長 / 終了)
// ---------------------------------------------------------------------------

function PeriodControls({ period }: { period: SpecialVisitPeriod }) {
  const updateMut = useUpdateSpecialVisitPeriod();
  const [target, setTarget] = React.useState<number>(period.weekly_target);
  const [endDate, setEndDate] = React.useState<string>(period.end_date);

  // 期間が差し替わった (別患者を開いた等) ら入力を追従させる.
  React.useEffect(() => {
    setTarget(period.weekly_target);
    setEndDate(period.end_date);
  }, [period.id, period.weekly_target, period.end_date]);

  const dirty = target !== period.weekly_target || endDate !== period.end_date;

  const handleSave = React.useCallback(() => {
    updateMut.mutate(
      {
        periodId: period.id,
        payload: {
          ...(target !== period.weekly_target ? { weekly_target: target } : {}),
          ...(endDate !== period.end_date ? { end_date: endDate } : {}),
        },
      },
      {
        onSuccess: () => toast.success('特別訪問週間の設定を更新しました'),
        onError: (err) => toast.error(`更新できませんでした: ${errorMessage(err)}`),
      },
    );
  }, [updateMut, period.id, period.weekly_target, period.end_date, target, endDate]);

  const handleEnd = React.useCallback(() => {
    const ok = window.confirm(
      '特別訪問週間を終了します。まだ配置していない ○ はプールから消えます。よろしいですか？',
    );
    if (!ok) return;
    updateMut.mutate(
      { periodId: period.id, payload: { status: 'ended' } },
      {
        onSuccess: () => toast.success('特別訪問週間を終了しました'),
        onError: (err) => toast.error(`終了できませんでした: ${errorMessage(err)}`),
      },
    );
  }, [updateMut, period.id]);

  return (
    <section
      className="flex flex-wrap items-end gap-3 rounded border border-border-default bg-bg-muted/40 p-3"
      data-testid="svw-period-controls"
    >
      <div className="flex flex-col gap-1 text-xs">
        <span className="font-medium text-text-secondary">開始日</span>
        <span className="tnum text-sm text-text-primary" data-testid="svw-period-start">
          {period.start_date}
        </span>
      </div>
      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium text-text-secondary">終了日 (延長できます)</span>
        <Input
          type="date"
          value={endDate}
          onChange={(e) => setEndDate(e.target.value)}
          className="h-8 w-40"
          data-testid="svw-period-end-date"
        />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium text-text-secondary">目標回数</span>
        <Input
          type="number"
          min={1}
          max={7}
          value={target}
          onChange={(e) => setTarget(clampTarget(e.target.value))}
          className="h-8 w-24"
          data-testid="svw-period-target"
        />
      </label>
      <Button
        type="button"
        size="sm"
        onClick={handleSave}
        disabled={!dirty || updateMut.isPending}
        data-testid="svw-period-save"
      >
        更新
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={handleEnd}
        disabled={updateMut.isPending}
        data-testid="svw-period-end"
      >
        期間を終了する
      </Button>
      {period.note ? <p className="w-full text-xs text-text-muted">メモ: {period.note}</p> : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// カレンダーの 1 セル
// ---------------------------------------------------------------------------

interface CalendarCellProps {
  weekIndex: number;
  weekday: number;
  day: SpecialCalendarDay | null;
  period: SpecialVisitPeriod;
  busy: boolean;
  onAddMark: (day: SpecialCalendarDay) => void;
  onRemoveMark: (mark: SpecialVisitMark) => void;
  onToggleDisplace: (day: SpecialCalendarDay) => void;
}

function CalendarCell({
  weekIndex,
  weekday,
  day,
  period,
  busy,
  onAddMark,
  onRemoveMark,
  onToggleDisplace,
}: CalendarCellProps) {
  const testIdBase = `svw-cell-${weekIndex}-${weekday}`;

  // 期間外の日 (行の週が期間の端に掛かる場合) はグレーアウトして操作させない.
  const outOfRange =
    !day ||
    day.date < period.start_date ||
    day.date > period.end_date ||
    period.status !== 'active';

  if (!day || outOfRange) {
    return (
      <div
        className="min-h-[4.5rem] rounded border border-border-subtle bg-bg-muted/60 opacity-50"
        data-testid={testIdBase}
        data-out-of-range="true"
      />
    );
  }

  const extra = liveMark(day.extra_mark);
  const displaced = liveMark(day.displaced_mark);
  const hasPreferred = day.preferred.length > 0;

  return (
    <div
      className={`flex min-h-[4.5rem] flex-col gap-1 rounded border border-border-default p-1 ${
        hasPreferred ? 'bg-brand-primary-50' : 'bg-bg-base'
      }`}
      data-testid={testIdBase}
      data-out-of-range="false"
      data-preferred={hasPreferred ? 'true' : 'false'}
    >
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-text-muted tnum">{day.date.slice(5)}</span>
        {hasPreferred ? (
          <span className="text-[10px] text-brand-primary tnum" title="ご希望の時間帯">
            希望 {trimSeconds(day.preferred[0]!.start)}
          </span>
        ) : null}
      </div>

      {/* 固定訪問カード (小). 退避中は打ち消し線 + バッジ. */}
      {day.fixed_visits.map((fv, i) => (
        <div
          key={`${fv.visit_id ?? 'pfv'}-${i}`}
          className={`rounded border border-border-subtle bg-bg-muted px-1 py-0.5 text-[10px] leading-tight text-text-primary ${
            displaced ? 'line-through opacity-60' : ''
          }`}
          data-testid={`svw-fixed-${weekIndex}-${weekday}-${i}`}
          data-displaced={displaced ? 'true' : 'false'}
        >
          <span className="tnum">{trimSeconds(fv.start_time)}</span>
          {fv.course_label ? <span className="ml-1">{fv.course_label}</span> : null}
        </div>
      ))}

      {displaced ? (
        <Badge
          className="border-transparent bg-warning-bg px-1.5 py-0 text-[10px] text-warning"
          data-testid={`svw-displaced-badge-${weekIndex}-${weekday}`}
        >
          プールへ退避中
        </Badge>
      ) : null}

      {day.fixed_visits.length > 0 ? (
        <button
          type="button"
          onClick={() => onToggleDisplace(day)}
          disabled={busy}
          data-testid={`svw-displace-toggle-${weekIndex}-${weekday}`}
          data-displaced={displaced ? 'true' : 'false'}
          className="rounded border border-border-default px-1 py-0.5 text-[10px] text-text-secondary hover:bg-bg-muted disabled:opacity-50"
        >
          {displaced ? '固定どおりに戻す' : 'この日はプールへ退避'}
        </button>
      ) : null}

      {/* ○ / ● または 空きスペース (クリックで ○ 追加) */}
      {extra ? (
        <button
          type="button"
          onClick={() => onRemoveMark(extra)}
          disabled={busy}
          data-testid={`svw-mark-${weekIndex}-${weekday}`}
          data-status={extra.status}
          title={
            extra.status === 'placed' ? '配置済み — クリックで取消' : '未配置 — クリックで取消'
          }
          className={`flex flex-col items-center rounded px-1 py-0.5 text-xs disabled:opacity-50 ${
            extra.status === 'placed'
              ? 'bg-brand-primary text-white'
              : 'border border-brand-primary text-brand-primary hover:bg-bg-muted'
          }`}
        >
          <span aria-hidden="true">{extra.status === 'placed' ? '●' : '○'}</span>
          <span className="sr-only">
            {extra.status === 'placed' ? '配置済みの追加枠' : '未配置の追加枠'}
          </span>
          {extra.status === 'placed' && extra.placed_summary ? (
            <span className="text-[10px] tnum">
              {trimSeconds(extra.placed_summary.start_time)}
              {extra.placed_summary.course_label ? ` ${extra.placed_summary.course_label}` : ''}
            </span>
          ) : null}
        </button>
      ) : (
        <button
          type="button"
          onClick={() => onAddMark(day)}
          disabled={busy}
          data-testid={`svw-empty-${weekIndex}-${weekday}`}
          aria-label={`${WEEKDAY_LABELS[weekday]} に追加枠を足す`}
          className="mt-auto min-h-[1.25rem] flex-1 rounded border border-dashed border-border-subtle text-[10px] text-text-muted hover:border-brand-primary hover:text-brand-primary disabled:opacity-50"
        >
          ＋
        </button>
      )}
    </div>
  );
}
