/**
 * 事務所からのやること — 画面で共通に使う表示の小道具。
 *
 * 正典 = docs/plans/patient-tasks-design-2026-11-01.md §3・モック docs/mockups/patient-tasks-mock.html。
 */
import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import type { PatientTaskRead, TaskVisitBrief } from '@/lib/schemas/patient-task';

const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'] as const;

/** YYYY-MM-DD → `11/2`。 */
export function taskMd(ymd: string): string {
  const [, m, d] = ymd.split('-');
  return `${Number(m)}/${Number(d)}`;
}

/** YYYY-MM-DD → `11/2（月）`。 */
export function taskMdWd(ymd: string): string {
  const [y, m, d] = ymd.split('-').map(Number);
  const wd = WEEKDAYS[new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1)).getUTCDay()] ?? '';
  return `${taskMd(ymd)}（${wd}）`;
}

/** 今日 (JST・YYYY-MM-DD)。 */
export function taskTodayJst(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

function ymdToUtc(ymd: string): Date {
  const [y, m, d] = ymd.split('-').map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
}

/** YYYY-MM-DD に n 日足す。 */
export function taskAddDays(ymd: string, n: number): string {
  const dt = ymdToUtc(ymd);
  dt.setUTCDate(dt.getUTCDate() + n);
  return dt.toISOString().slice(0, 10);
}

/** その月の末日 (YYYY-MM-DD)。 */
export function taskMonthEnd(ymd: string): string {
  const dt = ymdToUtc(ymd);
  return new Date(Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth() + 1, 0))
    .toISOString()
    .slice(0, 10);
}

/**
 * 期日の早押し (patient-tasks-due-design §3・モック B)。日付 (この日から) をもとに数える。
 * 今週中 = その週の土曜 (日付が土曜ならその日)・1 週間・2 週間・月末。
 */
export function dueQuickPicks(start: string): { label: string; date: string }[] {
  const wd = ymdToUtc(start).getUTCDay();
  return [
    { label: '今週中', date: taskAddDays(start, (6 - wd + 7) % 7) },
    { label: '1 週間', date: taskAddDays(start, 7) },
    { label: '2 週間', date: taskAddDays(start, 14) },
    { label: '月末', date: taskMonthEnd(start) },
  ];
}

export type DueState = { kind: 'over' | 'today' | 'left'; days: number };

/** 期日の状態 (ref の日から見て)。期日なしは null。days は過ぎた日数／残りの日数。 */
export function taskDueState(due: string | null | undefined, ref: string): DueState | null {
  if (!due) return null;
  const d = Math.round((ymdToUtc(due).getTime() - ymdToUtc(ref).getTime()) / 86_400_000);
  if (d < 0) return { kind: 'over', days: -d };
  if (d === 0) return { kind: 'today', days: 0 };
  return { kind: 'left', days: d };
}

/**
 * 預かってからの日数の 1 行 (`12/2（水）の訪問から・5 日`)。日数 = today − 最初の訪問の日。
 * 当日は「今日から」(patient-tasks-hold-first-visitor-design §7-3)。
 */
export function heldSinceLabel(visitDate: string, today: string): string {
  const days = Math.round((ymdToUtc(today).getTime() - ymdToUtc(visitDate).getTime()) / 86_400_000);
  return `${taskMdWd(visitDate)}の訪問から・${days <= 0 ? '今日から' : `${days} 日`}`;
}

/** ISO 日時 → `11/2 09:31` (JST)。 */
export function taskDateTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return '';
  const parts = new Intl.DateTimeFormat('ja-JP', {
    timeZone: 'Asia/Tokyo',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(dt);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  return `${get('month')}/${get('day')} ${get('hour')}:${get('minute')}`;
}

/** その訪問に行く人の名前 (`佐藤 花子・鈴木 一郎`)。 */
export function visitStaffNames(brief: TaskVisitBrief): string {
  return brief.staff
    .map((s) => s.name)
    .filter((n): n is string => !!n)
    .join('・');
}

/** 出す訪問の 1 行 (`11/5（木）14:00・鈴木 一郎`)。 */
export function visitBriefLabel(brief: TaskVisitBrief): string {
  const names = visitStaffNames(brief);
  return `${taskMdWd(brief.visit_date)} ${brief.start_time}${names ? `・${names}` : ''}`;
}

/** 項目の名前 (項目なし = 伝言)。 */
export function taskTitle(t: Pick<PatientTaskRead, 'item_type_name' | 'is_message'>): string {
  return t.is_message ? '伝言' : (t.item_type_name ?? '（項目なし）');
}

/** 完了した人と時刻 (`佐藤 花子 11/2 09:31`)。 */
export function taskDoneLabel(t: Pick<PatientTaskRead, 'done_by_name' | 'done_at'>): string {
  return [t.done_by_name ?? '', taskDateTime(t.done_at)].filter(Boolean).join(' ');
}

/**
 * 完了・戻す・取り消すの失敗の説明。409 = ほかの人が先に操作した (同時押しなど)、
 * 422 = この訪問には出ていない (先の訪問の分は max(日付, 今日) 以降の最初の訪問にだけ出る)。
 * どちらも hooks が一覧を読み直すので、その旨を添える。
 */
export function taskActionErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 409) {
    return `${apiErrorMessage(err)}。ほかの人が先に操作したようです。いまの状態に合わせました。`;
  }
  if (err instanceof ApiError && err.status === 422) {
    return `${apiErrorMessage(err)}。いまの状態に合わせました。`;
  }
  return apiErrorMessage(err);
}
