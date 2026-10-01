'use client';

/**
 * 「週を作る」ダイアログ (docs/plans/copy-week-design-2026-09-30.md §4-1〜4-3・
 * 見た目の正典 docs/mockups/copy-week-mock.html)。
 *
 *   1 作り方を選ぶ   … 固定訪問から生成 (今までの「週を生成」) / 前の週をコピー
 *   2 写す元の週を選ぶ … 過去 8 週のうち訪問のある週 (直近を初期選択・祝日は警告)
 *   3 内容を確かめる  … 曜日別の件数・写さない内訳・固定訪問に無い訪問 (1 件ずつ外せる)
 *                        ・固定訪問から補う (初期オフ)・自動スタッフ割当 (初期オン)
 *
 * 「固定訪問から生成」を選んだら ``onChooseFixed`` を呼ぶだけ (動作は今までと同じ)。
 * 文字と操作部の大きさは add-visit-anywhere-design.md §3-5 の基準
 * (本文 14px・見出し 16px・補足 12px・チェック 16px・行 32px 以上)。
 */
import { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  type CopySkipCounts,
  type CopyWeekResult,
  useCopyWeek,
  useCopyWeekPreview,
  useCopyWeekSources,
} from '@/lib/queries/copy_week';
import { cn } from '@/lib/utils';

const WD = ['月', '火', '水', '木', '金', '土', '日'];

function parseYmd(s: string): Date {
  const [y = 1970, m = 1, d = 1] = s.split('-').map(Number);
  return new Date(y, m - 1, d);
}
export function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function md(s: string): string {
  const d = parseYmd(s);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
function mdw(s: string): string {
  const d = parseYmd(s);
  return `${d.getMonth() + 1}/${d.getDate()}(${WD[(d.getDay() + 6) % 7]})`;
}
function addDays(s: string, n: number): string {
  const d = parseYmd(s);
  d.setDate(d.getDate() + n);
  return ymd(d);
}
function hm(t: string): string {
  return t.slice(0, 5);
}
/** 「10/5(月)〜10/11(日)」 */
export function weekRangeLabel(monday: string): string {
  return `${mdw(monday)}〜${mdw(addDays(monday, 6))}`;
}

const SKIP_LABELS: Array<[keyof CopySkipCounts, string]> = [
  ['cancelled', '取消'],
  ['unplanned', '予定外'],
  ['special_extra', '特別訪問週間の追加分'],
  ['inactive_patient', '止まっている利用者'],
  ['user_excluded', 'ここで外したもの'],
  ['kept_conflict', '残す取消と同じ時刻'],
  ['kept_same_day', '同じ日に残す訪問があるため写さない'],
  ['pair_partner', '2 名体制の相方を写さないため'],
  ['occupied_day', 'その日に訪問がある'],
  ['past_day', '今日より前の日'],
];

export interface MakeWeekDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 写す先 = 画面で開いている週の月曜 (YYYY-MM-DD) */
  targetWeekStart: string;
  /** 今週より前の週か (コピーは今週以降だけ) */
  isPastWeek: boolean;
  onChooseFixed: () => void;
  onCopied: (result: CopyWeekResult) => void;
}

export function MakeWeekDialog({
  open,
  onOpenChange,
  targetWeekStart,
  isPastWeek,
  onChooseFixed,
  onCopied,
}: MakeWeekDialogProps) {
  const [step, setStep] = useState<0 | 1 | 2>(0);
  const [how, setHow] = useState<'fixed' | 'copy'>(isPastWeek ? 'fixed' : 'copy');
  const [source, setSource] = useState<string | null>(null);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [fill, setFill] = useState(false);
  const [assign, setAssign] = useState(true);

  const copyMut = useCopyWeek();
  const resetCopy = copyMut.reset;

  // 開くたびに最初から (前回の選択・前回のエラーを持ち越すと別の週へ写す事故の元)。
  useEffect(() => {
    if (open) {
      resetCopy();
      setStep(0);
      setHow(isPastWeek ? 'fixed' : 'copy');
      setSource(null);
      setExcluded([]);
      setFill(false);
      setAssign(true);
    }
  }, [open, isPastWeek, resetCopy]);

  const sourcesQuery = useCopyWeekSources(targetWeekStart, open && how === 'copy');
  const sources = useMemo(() => sourcesQuery.data?.items ?? [], [sourcesQuery.data]);
  const selectedSource = source ?? sources[0]?.week_start ?? null;
  const selectedItem = sources.find((s) => s.week_start === selectedSource) ?? null;

  const previewQuery = useCopyWeekPreview(
    open && step === 2 && selectedSource
      ? {
          sourceWeekStart: selectedSource,
          targetWeekStart,
          excludeVisitIds: excluded,
          fillFromFixed: fill,
        }
      : null,
  );
  const pv = previewQuery.data;

  const targetLabel = weekRangeLabel(targetWeekStart);
  const total = pv ? pv.copy_count + pv.fill_count : 0;
  const skippedTotal = pv ? SKIP_LABELS.reduce((n, [k]) => n + pv.skipped[k], 0) : 0;
  const allExtrasOn = pv ? pv.not_in_fixed.every((r) => !r.excluded) : true;

  const toggleRow = (ids: string[], include: boolean) =>
    setExcluded((cur) =>
      include ? cur.filter((id) => !ids.includes(id)) : [...new Set([...cur, ...ids])],
    );
  const toggleAllExtras = (include: boolean) => {
    const ids = (pv?.not_in_fixed ?? []).flatMap((r) => r.visit_ids);
    toggleRow(ids, include);
  };

  const run = async () => {
    if (!selectedSource || !pv) return;
    const res = await copyMut.mutateAsync({
      sourceWeekStart: selectedSource,
      targetWeekStart,
      excludeVisitIds: excluded,
      fillFromFixed: fill,
      assignStaff: assign,
      expectedCounts: {
        copy_count: pv.copy_count,
        fill_count: pv.fill_count,
        replace_count: pv.existing.replace,
        needs_manual_count: pv.needs_manual_staff.length,
      },
    });
    onOpenChange(false);
    onCopied(res);
  };

  const steps = ['1 作り方を選ぶ', '2 写す元の週を選ぶ', '3 内容を確かめる'];

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && copyMut.isPending) return;
        onOpenChange(o);
      }}
    >
      <DialogContent
        className="flex max-h-[90vh] max-w-4xl flex-col overflow-hidden p-0"
        data-testid="make-week-dialog"
      >
        <DialogHeader className="border-b border-border-default px-6 pb-3 pt-5">
          <DialogTitle className="text-lg">
            {step === 0
              ? `${md(targetWeekStart)} の週を作る`
              : step === 1
                ? '写す元の週を選ぶ'
                : '内容を確かめる'}
          </DialogTitle>
          <DialogDescription className="text-sm">
            {step === 0
              ? targetLabel
              : step === 1
                ? `${targetLabel} の週へ写します。直近の週が最初から選ばれています。`
                : `${selectedSource ? md(selectedSource) : ''} の週 → ${targetLabel} の週`}
          </DialogDescription>
          <ol className="mt-2 flex gap-2 text-sm text-text-muted" aria-label="手順">
            {steps.map((s, i) => (
              <li
                key={s}
                className={cn(i === step && 'font-bold text-brand-primary-hover')}
                aria-current={i === step ? 'step' : undefined}
              >
                {s}
                {i < steps.length - 1 ? <span className="ml-2">›</span> : null}
              </li>
            ))}
          </ol>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4 text-sm" data-testid="make-week-body">
          {step === 0 ? (
            <div className="grid gap-3 md:grid-cols-2">
              <button
                type="button"
                onClick={() => setHow('fixed')}
                className={cn(
                  'rounded-xl border-2 p-4 text-left',
                  how === 'fixed'
                    ? 'border-brand-primary bg-brand-primary-50'
                    : 'border-border-default hover:border-brand-primary-light',
                )}
                aria-pressed={how === 'fixed'}
                data-testid="make-week-how-fixed"
              >
                <p className="text-base font-bold">固定訪問から生成</p>
                <p className="mt-1 text-text-secondary">
                  固定訪問（マスタ）のとおりに作ります。今までの「週を生成」と同じです。
                </p>
                <ul className="mt-2 list-disc pl-5 text-text-secondary">
                  <li>時刻は固定訪問の時刻</li>
                </ul>
              </button>
              <button
                type="button"
                onClick={() => !isPastWeek && setHow('copy')}
                disabled={isPastWeek}
                className={cn(
                  'rounded-xl border-2 p-4 text-left disabled:cursor-not-allowed disabled:opacity-50',
                  how === 'copy'
                    ? 'border-brand-primary bg-brand-primary-50'
                    : 'border-border-default hover:border-brand-primary-light',
                )}
                aria-pressed={how === 'copy'}
                data-testid="make-week-how-copy"
              >
                <p className="text-base font-bold">前の週をコピー</p>
                <p className="mt-1 text-text-secondary">
                  実際に回した週の予定を、そのまま次の週に写します。
                </p>
                <ul className="mt-2 list-disc pl-5 text-text-secondary">
                  <li>時刻はその週で確定した時刻</li>
                  <li>担当は写さず、ローテーションで割り当て直します</li>
                  {isPastWeek ? <li>写す先は今週以降の週だけです</li> : null}
                </ul>
              </button>
            </div>
          ) : null}

          {step === 1 ? (
            <div className="space-y-3">
              {sourcesQuery.isLoading ? (
                <p className="flex items-center gap-2 text-text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> 読み込み中…
                </p>
              ) : sources.length === 0 ? (
                <p className="text-text-secondary" data-testid="make-week-no-sources">
                  過去 8 週に訪問のある週がありません。「固定訪問から生成」で作ってください。
                </p>
              ) : (
                <table className="w-full border-collapse" data-testid="make-week-sources">
                  <thead>
                    <tr className="text-left text-xs text-text-secondary">
                      <th className="w-8 py-2" />
                      <th className="py-2">週</th>
                      <th className="py-2">訪問</th>
                      <th className="py-2">利用者</th>
                      <th className="py-2">取消</th>
                      <th className="py-2">予定外</th>
                      <th className="py-2">QR の記録</th>
                      <th className="py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {sources.map((s, i) => {
                      const on = s.week_start === selectedSource;
                      return (
                        <tr
                          key={s.week_start}
                          onClick={() => setSource(s.week_start)}
                          className={cn(
                            'cursor-pointer border-t border-border-default',
                            on ? 'bg-brand-primary-50' : 'hover:bg-bg-muted',
                          )}
                          data-testid={`make-week-source-${s.week_start}`}
                        >
                          <td className="py-2.5">
                            <input
                              type="radio"
                              name="copy-source"
                              checked={on}
                              onChange={() => setSource(s.week_start)}
                              className="h-4 w-4 accent-[var(--brand-primary)]"
                              aria-label={`${md(s.week_start)} の週`}
                            />
                          </td>
                          <td className="py-2.5">
                            <b>{md(s.week_start)} の週</b>
                            {i === 0 ? (
                              <span className="ml-2 rounded-full bg-bg-muted px-2 py-0.5 text-xs text-text-secondary">
                                直近
                              </span>
                            ) : null}
                          </td>
                          <td className="tnum py-2.5">{s.visits} 件</td>
                          <td className="tnum py-2.5">{s.patients} 名</td>
                          <td className="tnum py-2.5">{s.cancelled} 件</td>
                          <td className="tnum py-2.5">{s.unplanned} 件</td>
                          <td className="py-2.5">
                            {s.qr_arrivals > 0 ? (
                              <span className="rounded-full bg-success-bg px-2 py-0.5 text-xs font-bold text-success">
                                到着 {s.qr_arrivals} 件
                              </span>
                            ) : (
                              <span className="rounded-full bg-bg-muted px-2 py-0.5 text-xs text-text-secondary">
                                なし
                              </span>
                            )}
                          </td>
                          <td className="py-2.5">
                            {s.holidays.length > 0 ? (
                              <span className="rounded-full bg-warning-bg px-2 py-0.5 text-xs font-bold text-warning-strong">
                                祝日あり（{s.holidays.map((h) => md(h.date)).join('・')}）
                              </span>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {selectedItem && selectedItem.holidays.length > 0 ? (
                <p
                  className="rounded-md border border-border-warning bg-warning-bg px-3 py-2 text-warning-strong"
                  data-testid="make-week-source-holiday-warning"
                >
                  {md(selectedItem.week_start)} の週は祝日があります（
                  {selectedItem.holidays.map((h) => `${md(h.date)} ${h.name}`).join('・')}
                  ）。祝日で普段と違う曜日・時刻になっている訪問が、そのまま写ります。
                </p>
              ) : null}
            </div>
          ) : null}

          {step === 2 ? (
            !pv ? (
              previewQuery.isError ? (
                <div className="space-y-2" role="alert" data-testid="make-week-preview-error">
                  <p className="text-error">
                    内容を読み込めませんでした: {previewQuery.error?.message ?? ''}
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void previewQuery.refetch()}
                    data-testid="make-week-preview-retry"
                  >
                    もう一度読み込む
                  </Button>
                </div>
              ) : (
                <p className="flex items-center gap-2 text-text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  内容を読み込み中…
                </p>
              )
            ) : (
              <div className="space-y-4" data-testid="make-week-confirm">
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  <div className="rounded-lg border border-border-default px-3 py-2">
                    <p className="text-xs text-text-secondary">写す訪問</p>
                    <p className="tnum text-2xl font-bold" data-testid="make-week-total">
                      {total}
                      <small className="ml-1 text-sm font-normal text-text-secondary">件</small>
                    </p>
                    <p className="text-xs text-text-secondary">利用者 {pv.patients} 名</p>
                  </div>
                  <div className="rounded-lg border border-border-default px-3 py-2">
                    <p className="text-xs text-text-secondary">写さない</p>
                    <p className="tnum text-2xl font-bold">
                      {skippedTotal}
                      <small className="ml-1 text-sm font-normal text-text-secondary">件</small>
                    </p>
                    <p className="text-xs text-text-secondary" data-testid="make-week-skipped">
                      {SKIP_LABELS.filter(([k]) => pv.skipped[k] > 0)
                        .map(([k, label]) => `${label} ${pv.skipped[k]}`)
                        .join('・') || 'ありません'}
                    </p>
                  </div>
                  <div className="rounded-lg border border-border-default px-3 py-2">
                    <p className="text-xs text-text-secondary">担当</p>
                    <p className="pt-1 text-lg font-bold">写しません</p>
                    <p className="text-xs text-text-secondary">自動割当でローテーション</p>
                  </div>
                  <div className="rounded-lg border border-border-default px-3 py-2">
                    <p className="text-xs text-text-secondary">写す先の今の訪問</p>
                    <p className="tnum text-2xl font-bold">
                      {pv.existing.total}
                      <small className="ml-1 text-sm font-normal text-text-secondary">件</small>
                    </p>
                    <p className="text-xs text-text-secondary" data-testid="make-week-existing">
                      {pv.existing.total === 0
                        ? '置き換えはありません'
                        : pv.mode === 'add_only'
                          ? 'すべて残します'
                          : `置き換え ${pv.existing.replace}・残す ${pv.existing.total - pv.existing.replace}`}
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2" data-testid="make-week-by-weekday">
                  {pv.by_weekday.map((d) => (
                    <span
                      key={d.date}
                      className="rounded-md border border-border-default px-2.5 py-1"
                    >
                      {mdw(d.date)} <b className="tnum">{d.count}</b> 件
                    </span>
                  ))}
                </div>

                {pv.mode === 'add_only' ? (
                  <p
                    className="rounded-md border border-border-warning bg-warning-bg px-3 py-2 text-warning-strong"
                    data-testid="make-week-add-only"
                  >
                    この週には打刻（訪問の記録）があるため、今ある訪問は置き換えずに、訪問の無い日にだけ足します。打刻のある週は「コピー前に戻す」を使えません。
                  </p>
                ) : pv.existing.total > 0 ? (
                  <p className="text-text-secondary" data-testid="make-week-replace-note">
                    今ある訪問のうち、打刻済み・カイポケ取込済み・青ピン・取消の訪問は残し、それ以外の{' '}
                    {pv.existing.replace} 件を置き換えます。
                  </p>
                ) : null}

                {pv.target_holidays.length > 0 ? (
                  <p className="rounded-md border border-border-warning bg-warning-bg px-3 py-2 text-warning-strong">
                    写す先の週に祝日があります（
                    {pv.target_holidays.map((h) => `${md(h.date)} ${h.name}`).join('・')}
                    ）。祝日の訪問もそのまま写ります。
                  </p>
                ) : null}

                {pv.temp_course_count > 0 ? (
                  <p className="text-text-secondary">
                    臨時コースの訪問 {pv.temp_course_count}{' '}
                    件は、利用者の拠点の既定のコース（週を生成するときと同じ決め方）へ入れます。
                  </p>
                ) : null}

                {pv.needs_manual_staff.length > 0 ? (
                  <section
                    className="overflow-hidden rounded-lg border border-border-warning"
                    data-testid="make-week-needs-manual"
                  >
                    <h3 className="flex flex-wrap items-center justify-between gap-2 bg-warning-bg px-4 py-2 text-base font-bold text-warning-strong">
                      担当を手で付ける必要がある訪問 {pv.needs_manual_staff.length} 件
                      <small className="text-xs font-normal">
                        コースなし（担当なし）で入ります。写した後に「担当なし」から割り当ててください。
                      </small>
                    </h3>
                    <ul className="max-h-48 overflow-y-auto px-4 py-1">
                      {pv.needs_manual_staff.map((m, i) => (
                        <li
                          key={`${m.patient_id}-${m.target_date}-${m.start_time}-${i}`}
                          className="flex flex-wrap items-center gap-x-2.5 border-b border-border-default py-1.5 last:border-b-0"
                        >
                          <span className="tnum min-w-[150px] text-text-secondary">
                            {mdw(m.target_date)} {hm(m.start_time)}–{hm(m.end_time)}
                          </span>
                          <span>{m.patient_name}</span>
                          {m.origin === 'fill' ? (
                            <span className="text-xs text-text-secondary">
                              （固定訪問から補う）
                            </span>
                          ) : null}
                          <span className="text-xs text-text-secondary">{m.reason}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                <section className="overflow-hidden rounded-lg border border-border-default">
                  <h3 className="flex flex-wrap items-center justify-between gap-2 bg-bg-muted px-4 py-2 text-base font-bold">
                    固定訪問に無い訪問
                    <small className="text-xs font-normal text-text-secondary">
                      {pv.not_in_fixed.length} 件 ・ その週だけ追加・曜日を動かした可能性があります
                    </small>
                  </h3>
                  <div className="px-4 py-2">
                    {pv.not_in_fixed.length === 0 ? (
                      <p className="py-1.5 text-text-secondary">ありません。</p>
                    ) : (
                      <>
                        <label className="flex cursor-pointer items-start gap-2.5 py-1.5">
                          <input
                            type="checkbox"
                            className="mt-0.5 h-4 w-4 accent-[var(--brand-primary)]"
                            checked={allExtrasOn}
                            onChange={(e) => toggleAllExtras(e.target.checked)}
                            data-testid="make-week-extras-all"
                          />
                          <span>
                            これらも次の週へ写す
                            <small className="block text-xs text-text-secondary">
                              外すと、その訪問は次の週に入りません。1 件ずつ外すこともできます。
                            </small>
                          </span>
                        </label>
                        <ul className="max-h-48 overflow-y-auto border-t border-border-default">
                          {pv.not_in_fixed.map((r) => (
                            <li
                              key={r.visit_ids.join('-')}
                              className="border-b border-border-default"
                            >
                              <label className="flex cursor-pointer items-center gap-2.5 py-1.5">
                                <input
                                  type="checkbox"
                                  className="h-4 w-4 accent-[var(--brand-primary)]"
                                  checked={!r.excluded}
                                  onChange={(e) => toggleRow(r.visit_ids, e.target.checked)}
                                  data-testid={`make-week-extra-${r.visit_ids[0]}`}
                                />
                                <span className="tnum min-w-[150px] text-text-secondary">
                                  {mdw(r.target_date)} {hm(r.start_time)}–{hm(r.end_time)}
                                </span>
                                <span>{r.patient_name}</span>
                                {r.visit_ids.length > 1 ? (
                                  <span className="text-xs text-text-secondary">（2 名体制）</span>
                                ) : null}
                              </label>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                </section>

                <section className="overflow-hidden rounded-lg border border-border-default">
                  <h3 className="flex flex-wrap items-center justify-between gap-2 bg-bg-muted px-4 py-2 text-base font-bold">
                    写す元の週に無かった固定訪問
                    <small className="text-xs font-normal text-text-secondary">
                      {pv.missing_fixed_count} 件
                      {pv.missing_patients_without_visits > 0
                        ? `（うち ${pv.missing_patients_without_visits} 名は訪問が 1 件もありません）`
                        : ''}
                    </small>
                  </h3>
                  <div className="px-4 py-2">
                    <label className="flex cursor-pointer items-start gap-2.5 py-1.5">
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 accent-[var(--brand-primary)]"
                        checked={fill}
                        disabled={pv.missing_fixed_count === 0}
                        onChange={(e) => setFill(e.target.checked)}
                        data-testid="make-week-fill"
                      />
                      <span>
                        固定訪問から補う
                        <small className="block text-xs text-text-secondary">
                          {pv.missing_fixed_count === 0
                            ? '補う固定訪問はありません。'
                            : `入れないと、この ${pv.missing_fixed_count} 件は次の週に入りません。新しく契約した利用者や、${selectedSource ? md(selectedSource) : ''} の週にお休みだった利用者が含まれます。固定訪問そのものは変えません。`}
                        </small>
                      </span>
                    </label>
                    {pv.missing_fixed.length > 0 ? (
                      <ul className="max-h-40 overflow-y-auto border-t border-border-default">
                        {pv.missing_fixed.map((m) => (
                          <li
                            key={`${m.patient_id}-${m.weekday}`}
                            className="flex items-center gap-2.5 border-b border-border-default py-1.5"
                          >
                            <span className="tnum min-w-[150px] text-text-secondary">
                              {mdw(m.target_date)} {hm(m.start_time)}–{hm(m.end_time)}
                            </span>
                            <span>{m.patient_name}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                </section>

                <section className="overflow-hidden rounded-lg border border-border-default">
                  <h3 className="bg-bg-muted px-4 py-2 text-base font-bold">担当の割り当て</h3>
                  <div className="px-4 py-2">
                    <label className="flex cursor-pointer items-start gap-2.5 py-1.5">
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 accent-[var(--brand-primary)]"
                        checked={assign}
                        onChange={(e) => setAssign(e.target.checked)}
                        data-testid="make-week-assign"
                      />
                      <span>
                        写した後に続けて「自動スタッフ割当」を実行する
                        <small className="block text-xs text-text-secondary">
                          過去 4
                          週の担当を見て、同じ利用者に同じスタッフが続かないように割り当てます。外した場合は担当なしで始まります。
                        </small>
                      </span>
                    </label>
                  </div>
                </section>

                <p className="rounded-md border border-border-warning bg-warning-bg px-3 py-2 text-warning-strong">
                  実行前に {targetLabel} の週を保存します。
                  {pv.mode === 'replace' ? '作った後でも「コピー前に戻す」で元に戻せます。' : ''}
                  固定訪問（マスタ）は変わりません。カイポケへはまだ送りません（●未送信として出ます）。
                </p>
              </div>
            )
          ) : null}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border-default px-6 py-3">
          {step > 0 ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => setStep((s) => (s === 2 ? 1 : 0))}
              disabled={copyMut.isPending}
              data-testid="make-week-back"
            >
              戻る
            </Button>
          ) : (
            <span />
          )}
          {step === 0 ? (
            <Button
              type="button"
              onClick={() => {
                if (how === 'fixed') {
                  onOpenChange(false);
                  onChooseFixed();
                } else {
                  setStep(1);
                }
              }}
              data-testid="make-week-next"
            >
              {how === 'fixed' ? '固定訪問から生成する' : '次へ'}
            </Button>
          ) : step === 1 ? (
            <Button
              type="button"
              onClick={() => setStep(2)}
              disabled={!selectedSource}
              data-testid="make-week-next"
            >
              次へ
            </Button>
          ) : (
            <Button
              type="button"
              onClick={() => void run().catch(() => undefined)}
              disabled={!pv || previewQuery.isFetching || copyMut.isPending || total === 0}
              data-testid="make-week-run"
            >
              {copyMut.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : null}
              {total} 件を {targetLabel} の週へ写す
            </Button>
          )}
        </div>
        {copyMut.isError ? (
          <p className="px-6 pb-3 text-sm text-error" role="alert" data-testid="make-week-error">
            写せませんでした: {copyMut.error?.message}
          </p>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
