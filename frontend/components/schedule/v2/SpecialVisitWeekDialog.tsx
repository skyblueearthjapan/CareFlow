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
 * セルの見せ方 (2026-09-08 PO 指示 — HANDOFF §8 8-1):
 *   1 セルは上下 2 段。**上段「予定」** = 既に盤面にある訪問 (時刻・コース・担当)
 *   を 14px のカードで出す (無ければ「予定なし」/ 退避中の日は「プールへ退避中
 *   （この週は盤面に無し）」)。**下段「追加枠」** = ○/● の状態を言葉で書く。
 *   用語は次の 4 つだけに揃える (凡例も同じ言葉):
 *     予定 / プール待ち（この日に 1 回追加・時間未定） /
 *     配置済み HH:MM コース / 空き
 *   ○ は「システムの提案」ではなく「人がその日の保留プールに積んだ 1 回分
 *   (時間は未定)」であることを、文言でそのまま表す。
 *
 * カレンダーの操作 (2026-09-07 改訂 —
 * `special-visit-week-ux-investigation-2026-09-07.md` §3-2):
 *   セルのクリックは **即実行しない**。その日をどうするかのメニューを開く。
 *   - 追加枠なし → 「この日を保留プールに追加する」(POST marks)。
 *                  既に予定がある日は「同じ日に 2 回目を追加しますか？」の確認を挟む。
 *   - ○ (プール待ち) → 「この日の配置先を決める…（プールから盤面へ）」
 *                      「プールから外す」(確認 → DELETE marks/{id})
 *   - ● (配置済み) → 「配置を変更する…」(新しい配置を決めてから入れ替える)
 *                    「配置を取り消す（訪問も削除）」(確認 → force=true)
 *   取り消し系は必ず確認ダイアログを挟む (`window.confirm` は使わない)。
 *   当日以前 (JST) の日は配置できない = メニューの「配置先を決める…」を止める。
 *   - 固定訪問の退避トグル   → 「固定どおり」⇄「この日はプールへ退避」
 *                             (POST displace / POST marks/{id}/restore。
 *                              配置済み退避の解除は確認 → force=true)
 *
 * 週合計 (設計書 §3): 固定訪問の残数 + extra ○ (pool/placed 両方) + displaced
 * チケット数。**合計値は BE の `week.total` が正**で FE は再計算しない
 * (= 判定ロジックの二重持ちを避ける)。行末には内訳
 * 「固定 N ＋ プール待ち M ＝ T 回（目標 X）」を添えるが、T は常に BE の値を出す
 * (内訳と食い違っても BE を優先する)。
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
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
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
import { SpecialVisitPlaceLauncher, todayIsoJst } from './SpecialVisitPlaceLauncher';

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

/**
 * 端末ローカルの「今日」。**期間作成フォームの開始日の初期値専用**。
 * 過去日判定は JST 固定の `todayIsoJst` を使う (端末の時差で判定を変えない)。
 */
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

/**
 * 行末に添える内訳 (PO 指示 2026-09-08) —「固定 N ＋ プール待ち M」。
 *
 * - N = その週の固定訪問の残数 (退避した日は BE が `fixed_visits: []` を返すので
 *       自然に 0 になる)。
 * - M = 追加枠 (○ プール待ち ＋ ● 配置済み) ＋ 退避チケット。
 *
 * **数え方は BE (`special_visits.py` の calendar) と 1:1 に揃える**: BE は週の
 * 月〜土 6 日を期間の内外に関わらず合計するため、ここでも**期間外の日を外さない**
 * (外すと開始日が週の途中の週で「N ＋ M ＝ T」の式が目に見えて破綻する)。
 * セル側の期間外グレーアウトは表示だけの話なので別扱いで良い。
 *
 * 合計そのものは BE の `week.total` が正なので、ここでは返さない。
 */
function weekBreakdown(week: SpecialCalendarWeek): { fixed: number; pooled: number } {
  let fixed = 0;
  let pooled = 0;
  for (const d of week.days) {
    fixed += d.fixed_visits.length;
    if (liveMark(d.extra_mark)) pooled += 1;
    if (liveMark(d.displaced_mark)) pooled += 1;
  }
  return { fixed, pooled };
}

function weekRowLabel(week: SpecialCalendarWeek): string {
  const monday = parseISODate(week.week_monday);
  if (!monday) return `W${week.iso_week}`;
  return `${monday.getMonth() + 1}/${monday.getDate()}週`;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** "2026-08-04" + 曜日 → "8/4（火）"。確認文とメニュー見出しに使う. */
function formatDayLabel(dateIso: string, weekday: number): string {
  const m = dateIso.match(/^\d{4}-(\d{2})-(\d{2})$/);
  const md = m ? `${Number(m[1])}/${Number(m[2])}` : dateIso;
  const wd = WEEKDAY_LABELS[weekday] ?? '';
  return wd ? `${md}（${wd}）` : md;
}

/** ● の小書き ("14:00 稲毛A")。配置先が不明なら空文字. */
function placedSummaryLabel(mark: SpecialVisitMark): string {
  if (!mark.placed_summary) return '';
  const time = trimSeconds(mark.placed_summary.start_time);
  const course = mark.placed_summary.course_label;
  return course ? `${time} ${course}` : time;
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
        className="max-w-6xl"
        aria-describedby="special-visit-week-description"
        data-testid="special-visit-week-dialog"
      >
        <DialogHeader>
          <DialogTitle>
            特別訪問週間
            <span className="ml-2 text-sm font-normal text-text-secondary">{patientName}</span>
          </DialogTitle>
          <DialogDescription id="special-visit-week-description">
            期間と週の目標回数を決め、追加したい日を保留プールに積みます（○＝プール待ち・時間未定）。
            配置先はこのカレンダーの ○ から決められます。基本の固定訪問はそのまま生きています。
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
        {/* PO指示 2026-07-29: 開始ボタンは期間チップの右隣 (少し間をあける)。
            期間を直したあと、そのまま直感的に開始できる配置にする。 */}
        <div className="flex flex-wrap items-center gap-2">
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
          <Button
            type="button"
            onClick={handleCreate}
            disabled={createMut.isPending}
            className="ml-6"
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

/**
 * 配置モーダル (＋訪問) を開く対象。
 *
 * ● の付け替えは `markId=null` + `replacingMarkId` で開く。**新しい訪問が
 * できてから**ランチャー側が旧マーク (と訪問) を消して作り直す = 先に壊さない。
 */
interface PlaceTarget {
  markId: string | null;
  replacingMarkId?: string | null;
  replacingVisitId?: string | null;
  /** 入れ替え対象の開始時刻 ("HH:MM")。同じ時刻は入れ替えられない (重複)。 */
  replacingStartHM?: string | null;
  date: string;
  isoYear: number;
  isoWeek: number;
  weekday: number;
}

function PeriodCalendar({ period, weeks, isLoading, isError }: PeriodCalendarProps) {
  const createMark = useCreateSpecialVisitMark();
  const deleteMark = useDeleteSpecialVisitMark();
  const displaceMut = useDisplaceSpecialVisit();
  const restoreMut = useRestoreSpecialVisitMark();

  /** 確認ダイアログ (○ の取消 / ● の取消 / 配置の決め直し)。 */
  const [confirm, setConfirm] = React.useState<ConfirmSpec | null>(null);
  /** 「配置先を決める…」で開く ＋訪問モーダルの対象。 */
  const [placeTarget, setPlaceTarget] = React.useState<PlaceTarget | null>(null);

  const busy =
    createMark.isPending || deleteMark.isPending || displaceMut.isPending || restoreMut.isPending;

  /** JST の今日。セルの過去日判定はこれ 1 本で行う (端末のタイムゾーンに依らない)。 */
  const todayIso = React.useMemo(() => todayIsoJst(), []);

  /**
   * 未配置の追加枠 (= プールに積まれている ○) の件数。
   * 期間外の日はセルと同じ規則で数えない (行の週が期間の端に掛かるため)。
   */
  const poolCount = React.useMemo(
    () =>
      weeks.reduce(
        (n, w) =>
          n +
          w.days.filter(
            (d) =>
              liveMark(d.extra_mark)?.status === 'pool' &&
              d.date >= period.start_date &&
              d.date <= period.end_date &&
              period.status === 'active',
          ).length,
        0,
      ),
    [weeks, period.start_date, period.end_date, period.status],
  );

  /**
   * 「この日を保留プールに追加する」。
   *
   * 既に予定がある日は **2 回目の訪問**になるので確認を挟む (PO 指示 2026-09-08)。
   * 予定が無い日は従来どおり即作成する (1 操作で済ませる)。
   */
  const handleAddMark = React.useCallback(
    (week: SpecialCalendarWeek, day: SpecialCalendarDay) => {
      const create = () =>
        createMark.mutate(
          {
            periodId: period.id,
            payload: { iso_year: week.iso_year, iso_week: week.iso_week, weekday: day.weekday },
          },
          {
            onError: (err) => toast.error(`追加できませんでした: ${errorMessage(err)}`),
          },
        );

      if (day.fixed_visits.length > 0) {
        const times = day.fixed_visits.map((fv) => trimSeconds(fv.start_time)).join('・');
        setConfirm({
          title: '同じ日に 2 回目を追加しますか？',
          body: `この日は ${times} の予定があります。保留プールにもう 1 回分を追加します。`,
          confirmLabel: '追加する',
          onConfirm: create,
        });
        return;
      }
      create();
    },
    [createMark, period.id],
  );

  /** ○ / ● の取消。どちらも確認ダイアログを挟む (誤操作で消えないように)。 */
  const handleRemoveMark = React.useCallback(
    (mark: SpecialVisitMark, day: SpecialCalendarDay) => {
      const placed = mark.status === 'placed';
      const label = formatDayLabel(day.date, day.weekday);
      setConfirm({
        title: placed ? '配置を取り消しますか？' : 'プールから外しますか？',
        body: placed
          ? `${label}の配置済みの追加枠を取り消します。配置した訪問も削除されます。`
          : `${label}のプール待ちの追加枠を保留プールから外します。`,
        confirmLabel: placed ? '取り消す' : '外す',
        onConfirm: () =>
          deleteMark.mutate(
            { markId: mark.id, force: placed },
            { onError: (err) => toast.error(`取消できませんでした: ${errorMessage(err)}`) },
          ),
      });
    },
    [deleteMark],
  );

  /** 未配置の ○ → ＋訪問モーダルで配置先を決める。 */
  const handleOpenPlace = React.useCallback(
    (mark: SpecialVisitMark, week: SpecialCalendarWeek, day: SpecialCalendarDay) => {
      setPlaceTarget({
        markId: mark.id,
        date: day.date,
        isoYear: week.iso_year,
        isoWeek: week.iso_week,
        weekday: day.weekday,
      });
    },
    [],
  );

  /**
   * 配置済み ● の付け直し。**先に壊さない**: 配置モーダルをそのまま開き、
   * 新しい訪問ができてからランチャーが「今の配置を取り消す → 追加枠を作り直す →
   * 紐付ける」の順で入れ替える (途中でやめても今の予定は残る)。
   */
  const handleChangePlacement = React.useCallback(
    (mark: SpecialVisitMark, week: SpecialCalendarWeek, day: SpecialCalendarDay) => {
      const label = formatDayLabel(day.date, day.weekday);
      setConfirm({
        title: '配置を決め直しますか？',
        body: `${label}の新しい配置を決めてから、いまの訪問を入れ替えます。`,
        confirmLabel: '決め直す',
        onConfirm: () =>
          setPlaceTarget({
            markId: null,
            replacingMarkId: mark.id,
            replacingVisitId: mark.placed_visit_id,
            replacingStartHM: mark.placed_summary
              ? trimSeconds(mark.placed_summary.start_time)
              : null,
            date: day.date,
            isoYear: week.iso_year,
            isoWeek: week.iso_week,
            weekday: day.weekday,
          }),
      });
    },
    [],
  );

  const handleToggleDisplace = React.useCallback(
    (week: SpecialCalendarWeek, day: SpecialCalendarDay) => {
      const current = liveMark(day.displaced_mark);
      if (current) {
        // 退避解除 (「固定どおり」へ戻す). 配置済みは確認 → force=true.
        const placed = current.status === 'placed';
        const restore = () =>
          restoreMut.mutate(
            { markId: current.id, force: placed },
            { onError: (err) => toast.error(`戻せませんでした: ${errorMessage(err)}`) },
          );
        if (placed) {
          setConfirm({
            title: '固定どおりに戻しますか？',
            body: `${formatDayLabel(day.date, day.weekday)}の退避した枠は既に別の時間へ配置されています。戻すと配置済みの訪問は削除されます。`,
            confirmLabel: '戻す',
            onConfirm: restore,
          });
          return;
        }
        restore();
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
      {/* 凡例と次の一手 (§3-2 3): 何をすればよいかを常時出す。 */}
      <div className="space-y-1" data-testid="svw-legend">
        <p className="text-sm text-text-secondary">
          予定 ＝ 既に盤面にある訪問　○ プール待ち（時間未定）　● 配置済み　空き ＝
          予定も追加枠もない日
        </p>
        <p className="text-sm font-medium text-text-primary" data-testid="svw-pool-count">
          保留プールに {poolCount} 件あります（時間未定）
        </p>
      </div>

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
          <div className="min-w-[62rem]">
            {/* ヘッダ行 */}
            <div className="grid grid-cols-[5rem_repeat(6,1fr)_12rem] gap-1 pb-1">
              <div />
              {WEEKDAY_LABELS.map((label) => (
                <div key={label} className="px-1 text-center text-sm font-semibold text-text-muted">
                  {label}
                </div>
              ))}
              <div className="px-1 text-center text-sm font-semibold text-text-muted">週合計</div>
            </div>

            {weeks.map((week, wi) => {
              const byWd = daysByWeekday(week);
              const breakdown = weekBreakdown(week);
              // 合計は BE が正 (内訳と食い違っても week.total を出す)。
              const totalState: 'below' | 'met' | 'over' =
                week.total < period.weekly_target
                  ? 'below'
                  : week.total === period.weekly_target
                    ? 'met'
                    : 'over';
              return (
                <div
                  key={`${week.iso_year}-${week.iso_week}`}
                  className="grid grid-cols-[5rem_repeat(6,1fr)_12rem] items-stretch gap-1 py-1"
                  data-testid={`svw-week-row-${wi}`}
                >
                  <div className="tnum flex flex-col justify-center px-1 text-sm text-text-secondary">
                    <span className="font-semibold">{weekRowLabel(week)}</span>
                    <span className="text-xs text-text-muted">
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
                        todayIso={todayIso}
                        onAddMark={(d) => handleAddMark(week, d)}
                        onRemoveMark={handleRemoveMark}
                        onToggleDisplace={(d) => handleToggleDisplace(week, d)}
                        onOpenPlace={(mark, d) => handleOpenPlace(mark, week, d)}
                        onChangePlacement={(mark, d) => handleChangePlacement(mark, week, d)}
                      />
                    );
                  })}

                  {/* 行末 = 週合計の内訳 (PO 指示 2026-09-08)。未達=赤・一致=緑✓・超過=橙。 */}
                  <div className="flex flex-col items-center justify-center gap-1 px-1">
                    <div
                      data-testid={`svw-total-${wi}`}
                      data-met={week.target_met ? 'true' : 'false'}
                      data-state={totalState}
                      className={`tnum w-full rounded-md px-2 py-1 text-center text-xs leading-snug ${
                        totalState === 'below'
                          ? 'bg-error-bg text-error'
                          : totalState === 'met'
                            ? 'bg-success-bg text-success'
                            : 'bg-warning-bg text-warning'
                      }`}
                    >
                      固定 {breakdown.fixed} ＋ プール待ち {breakdown.pooled} ＝ {week.total}{' '}
                      回（目標 {period.weekly_target}）{totalState === 'met' ? ' ✓' : ''}
                    </div>
                    {totalState === 'over' ? (
                      <Badge
                        variant="warning"
                        className="px-1.5 py-0 text-xs"
                        data-testid={`svw-total-over-${wi}`}
                      >
                        目標超
                      </Badge>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <p className="text-sm text-text-muted">
        セルをクリックすると、その日にできることが出ます。プール待ち (○)
        は「この日の配置先を決める…（プールから盤面へ）」でこの画面から盤面に入れられます。固定訪問は「この日の固定訪問をプールへ退避」で一時的に外せます
        (恒久パターンは変わりません)。
      </p>

      {/* 取消・決め直しの確認 (window.confirm は使わない — §3-2 1)。 */}
      {confirm ? <ConfirmDialog spec={confirm} onClose={() => setConfirm(null)} /> : null}

      {/* 「配置先を決める…」= ＋訪問モーダル (患者・日付・反映先を固定して開く)。 */}
      {placeTarget ? (
        <SpecialVisitPlaceLauncher
          open
          onOpenChange={(o) => {
            if (!o) setPlaceTarget(null);
          }}
          patientId={period.patient_id}
          periodId={period.id}
          markId={placeTarget.markId}
          replacingMarkId={placeTarget.replacingMarkId ?? null}
          replacingVisitId={placeTarget.replacingVisitId ?? null}
          replacingStartHM={placeTarget.replacingStartHM ?? null}
          date={placeTarget.date}
          isoYear={placeTarget.isoYear}
          isoWeek={placeTarget.isoWeek}
          weekday={placeTarget.weekday}
          onPlaced={() => setPlaceTarget(null)}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 確認ダイアログ (取り消し系は必ずここを通す)
// ---------------------------------------------------------------------------

interface ConfirmSpec {
  title: string;
  body: string;
  /** 実行ボタンの文言 (「取り消す」「決め直す」「終了する」)。 */
  confirmLabel: string;
  onConfirm: () => void;
}

/**
 * `window.confirm` の置き換え。**開いているときだけマウントする**
 * (テストの Dialog モックが open を見ないため、条件付きマウントで揃える)。
 */
function ConfirmDialog({ spec, onClose }: { spec: ConfirmSpec; onClose: () => void }) {
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-w-md" data-testid="svw-confirm">
        <DialogHeader>
          <DialogTitle>{spec.title}</DialogTitle>
          <DialogDescription>{spec.body}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            data-testid="svw-confirm-cancel"
          >
            やめる
          </Button>
          <Button
            type="button"
            onClick={() => {
              onClose();
              spec.onConfirm();
            }}
            data-testid="svw-confirm-ok"
          >
            {spec.confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 期間そのものの操作 (目標変更 / 延長 / 終了)
// ---------------------------------------------------------------------------

function PeriodControls({ period }: { period: SpecialVisitPeriod }) {
  const updateMut = useUpdateSpecialVisitPeriod();
  const [target, setTarget] = React.useState<number>(period.weekly_target);
  const [endDate, setEndDate] = React.useState<string>(period.end_date);
  /** 「…」メニュー (期間の終了はここへ隠す — 目立つ位置に置かない)。 */
  const [moreOpen, setMoreOpen] = React.useState(false);
  const [confirmEnd, setConfirmEnd] = React.useState(false);

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
      <div className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-text-secondary">開始日</span>
        <span className="tnum text-sm text-text-primary" data-testid="svw-period-start">
          {period.start_date}
        </span>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-text-secondary">終了日 (延長できます)</span>
        <Input
          type="date"
          value={endDate}
          onChange={(e) => setEndDate(e.target.value)}
          className="w-44"
          data-testid="svw-period-end-date"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-text-secondary">目標回数</span>
        <Input
          type="number"
          min={1}
          max={7}
          value={target}
          onChange={(e) => setTarget(clampTarget(e.target.value))}
          className="w-24"
          data-testid="svw-period-target"
        />
      </label>
      <Button
        type="button"
        onClick={handleSave}
        disabled={!dirty || updateMut.isPending}
        data-testid="svw-period-save"
      >
        保存
      </Button>

      {/* 期間の終了は「…」の中 (誤操作の重い操作を常時表示しない — §3-2 4)。 */}
      <Popover open={moreOpen} onOpenChange={setMoreOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            aria-label="その他の操作"
            data-testid="svw-period-more"
          >
            …
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="end"
          className="w-56 p-1"
          role="menu"
          aria-label="期間の操作"
          data-testid="svw-period-more-menu"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setMoreOpen(false);
              setConfirmEnd(true);
            }}
            disabled={updateMut.isPending}
            data-testid="svw-period-end"
            className="w-full rounded px-2 py-2 text-left text-sm text-text-primary hover:bg-bg-muted disabled:opacity-50"
          >
            期間を終了する
          </button>
        </PopoverContent>
      </Popover>

      {period.note ? <p className="w-full text-sm text-text-muted">メモ: {period.note}</p> : null}

      {confirmEnd ? (
        <ConfirmDialog
          spec={{
            title: '期間を終了しますか？',
            body: 'プール待ちの追加枠は保留プールから消えます。',
            confirmLabel: '終了する',
            onConfirm: handleEnd,
          }}
          onClose={() => setConfirmEnd(false)}
        />
      ) : null}
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
  /** JST の今日 (YYYY-MM-DD)。当日以前は配置できない。 */
  todayIso: string;
  onAddMark: (day: SpecialCalendarDay) => void;
  onRemoveMark: (mark: SpecialVisitMark, day: SpecialCalendarDay) => void;
  onToggleDisplace: (day: SpecialCalendarDay) => void;
  onOpenPlace: (mark: SpecialVisitMark, day: SpecialCalendarDay) => void;
  onChangePlacement: (mark: SpecialVisitMark, day: SpecialCalendarDay) => void;
}

/** セルメニューの 1 行 (ラベル全体がクリック領域・§3-5)。 */
const menuItemCls =
  'w-full rounded px-2 py-2 text-left text-sm text-text-primary hover:bg-bg-muted disabled:opacity-50';

function CalendarCell({
  weekIndex,
  weekday,
  day,
  period,
  busy,
  todayIso,
  onAddMark,
  onRemoveMark,
  onToggleDisplace,
  onOpenPlace,
  onChangePlacement,
}: CalendarCellProps) {
  const testIdBase = `svw-cell-${weekIndex}-${weekday}`;
  const [menuOpen, setMenuOpen] = React.useState(false);

  // 期間外の日 (行の週が期間の端に掛かる場合) はグレーアウトして操作させない.
  const outOfRange =
    !day ||
    day.date < period.start_date ||
    day.date > period.end_date ||
    period.status !== 'active';

  if (!day || outOfRange) {
    return (
      <div
        className="min-h-[7rem] rounded border border-border-subtle bg-bg-muted/60 opacity-50"
        data-testid={testIdBase}
        data-out-of-range="true"
      />
    );
  }

  const extra = liveMark(day.extra_mark);
  const displaced = liveMark(day.displaced_mark);
  const hasPreferred = day.preferred.length > 0;
  const placed = extra?.status === 'placed';
  const hasFixed = day.fixed_visits.length > 0;
  /**
   * 予定も追加枠も無い日 = 「空き」。
   * 色は付けず文言だけで表す (緑にすると週合計の「目標達成」の緑と意味がぶつかる)。
   */
  const isFree = !hasFixed && !extra && !displaced;
  const dayLabel = formatDayLabel(day.date, weekday);
  /**
   * 当日以前は配置できない (＋訪問の登録系と同じ規則)。
   * BE の place-and-fix は過去日を弾かないので、入口で塞ぐのは FE の責任。
   */
  const isPast = day.date <= todayIso;

  /**
   * 下段「追加枠」の一行説明 (PO 指示 2026-09-08 の言葉づかいに固定)。
   * ○ は提案ではなく「人が保留プールに積んだ 1 回分・時間未定」。
   */
  const caption = extra
    ? placed
      ? `配置済み ${placedSummaryLabel(extra)}`.trim()
      : 'プール待ち（この日に 1 回追加・時間未定）'
    : isFree
      ? '空き'
      : '＋ 保留プールに追加';

  /** メニューから選んだら閉じてから実行する (1 操作 = 1 選択)。 */
  const pick = (run: () => void) => {
    setMenuOpen(false);
    run();
  };

  return (
    <div
      className={`flex min-h-[7rem] flex-col rounded border border-border-default ${
        hasPreferred ? 'bg-brand-primary-50' : 'bg-bg-base'
      }`}
      data-testid={testIdBase}
      data-out-of-range="false"
      data-preferred={hasPreferred ? 'true' : 'false'}
      data-free={isFree ? 'true' : 'false'}
    >
      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={busy}
            data-testid={
              extra ? `svw-mark-${weekIndex}-${weekday}` : `svw-empty-${weekIndex}-${weekday}`
            }
            data-status={extra ? extra.status : 'none'}
            aria-label={`${dayLabel} の操作`}
            className="flex min-h-[7rem] flex-1 flex-col gap-1 rounded p-1.5 text-left hover:bg-bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary-light disabled:opacity-50"
          >
            <span className="flex items-center justify-between text-xs">
              <span className="tnum text-text-muted">{day.date.slice(5)}</span>
              {hasPreferred ? (
                <span className="tnum text-brand-primary" title="ご希望の時間帯">
                  希望 {trimSeconds(day.preferred[0]!.start)}
                </span>
              ) : null}
            </span>

            {/* ── 上段「予定」= 既に盤面にある訪問 (時刻・コース・担当)。 ── */}
            <span className="flex flex-col gap-0.5">
              <span className="text-xs font-semibold text-text-muted">予定</span>
              {hasFixed ? (
                day.fixed_visits.map((fv, i) => (
                  <span
                    key={`${fv.visit_id ?? 'pfv'}-${i}`}
                    className={`block rounded border border-border-subtle bg-bg-muted px-1.5 py-0.5 text-sm leading-tight text-text-primary ${
                      displaced ? 'line-through opacity-60' : ''
                    }`}
                    data-testid={`svw-fixed-${weekIndex}-${weekday}-${i}`}
                    data-displaced={displaced ? 'true' : 'false'}
                  >
                    <span className="tnum">{trimSeconds(fv.start_time)}</span>
                    {fv.course_label ? <span className="ml-1">{fv.course_label}</span> : null}
                    {fv.staff_name ? <span className="ml-1">{fv.staff_name}</span> : null}
                  </span>
                ))
              ) : displaced ? (
                /*
                 * 退避した日は BE が `fixed_visits: []` を返す (生成済み週は訪問を
                 * 論理削除・未生成週は PFV の投影自体を止める — `special_visits.py`
                 * の calendar)。つまり「何が退避されたか」は今の API では分からない
                 * ので、「予定なし」と嘘をつかずに退避中であることだけを書く。
                 * 恒久対応は calendar API の `displaced_mark` に退避元のスナップ
                 * ショット (時刻・コース・担当) を載せること。
                 */
                <span
                  className="rounded bg-warning-bg px-1.5 py-0.5 text-sm leading-tight text-warning"
                  data-testid={`svw-displaced-note-${weekIndex}-${weekday}`}
                >
                  プールへ退避中（この週は盤面に無し）
                </span>
              ) : (
                <span
                  className="text-sm leading-tight text-text-muted"
                  data-testid={`svw-no-visit-${weekIndex}-${weekday}`}
                >
                  予定なし
                </span>
              )}

              {displaced ? (
                <Badge
                  className="w-fit border-transparent bg-warning-bg px-1.5 py-0 text-xs text-warning"
                  data-testid={`svw-displaced-badge-${weekIndex}-${weekday}`}
                >
                  プールへ退避中
                </Badge>
              ) : null}
            </span>

            {/* ── 下段「追加枠」= ○ (プール待ち) / ● (配置済み) / 空き。 ── */}
            <span className="mt-auto flex flex-col items-center gap-0.5 border-t border-border-subtle pt-1">
              <span className="w-full text-left text-xs font-semibold text-text-muted">追加枠</span>
              <span
                aria-hidden="true"
                className={
                  extra
                    ? placed
                      ? 'flex h-6 w-6 items-center justify-center rounded-full bg-brand-primary text-sm text-white'
                      : 'flex h-6 w-6 items-center justify-center rounded-full border border-brand-primary text-sm text-brand-primary'
                    : 'text-sm text-text-muted'
                }
              >
                {extra ? (placed ? '●' : '○') : '＋'}
              </span>
              <span className="sr-only">
                {extra ? (placed ? '配置済みの追加枠' : 'プール待ちの追加枠') : '追加枠なし'}
              </span>
              <span
                className="text-center text-sm leading-tight text-text-secondary"
                data-testid={`svw-caption-${weekIndex}-${weekday}`}
              >
                {caption}
              </span>
            </span>
          </button>
        </PopoverTrigger>

        <PopoverContent
          align="start"
          className="w-64 p-1"
          role="menu"
          aria-label={`${dayLabel} の操作`}
          data-testid={`svw-menu-${weekIndex}-${weekday}`}
        >
          <p className="px-2 pb-1 pt-0.5 text-xs font-semibold text-text-muted">{dayLabel}</p>

          {extra ? (
            <>
              <button
                type="button"
                role="menuitem"
                onClick={() =>
                  pick(() => (placed ? onChangePlacement(extra, day) : onOpenPlace(extra, day)))
                }
                disabled={busy || isPast}
                data-testid={`svw-menu-place-${weekIndex}-${weekday}`}
                className={menuItemCls}
              >
                {placed ? '配置を変更する…' : 'この日の配置先を決める…（プールから盤面へ）'}
              </button>
              {isPast ? (
                <p
                  className="px-2 pb-1 text-xs text-text-muted"
                  data-testid={`svw-menu-past-${weekIndex}-${weekday}`}
                >
                  過去日は配置できません
                </p>
              ) : null}
              <button
                type="button"
                role="menuitem"
                onClick={() => pick(() => onRemoveMark(extra, day))}
                disabled={busy}
                data-testid={`svw-menu-cancel-${weekIndex}-${weekday}`}
                className={menuItemCls}
              >
                {placed ? '配置を取り消す（訪問も削除）' : 'プールから外す'}
              </button>
            </>
          ) : (
            <button
              type="button"
              role="menuitem"
              onClick={() => pick(() => onAddMark(day))}
              disabled={busy}
              data-testid={`svw-menu-add-${weekIndex}-${weekday}`}
              className={menuItemCls}
            >
              この日を保留プールに追加する
            </button>
          )}

          {day.fixed_visits.length > 0 || displaced ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => pick(() => onToggleDisplace(day))}
              disabled={busy}
              data-testid={`svw-displace-toggle-${weekIndex}-${weekday}`}
              data-displaced={displaced ? 'true' : 'false'}
              className={menuItemCls}
            >
              {displaced ? '固定どおりに戻す' : 'この日の固定訪問をプールへ退避'}
            </button>
          ) : null}
        </PopoverContent>
      </Popover>
    </div>
  );
}
