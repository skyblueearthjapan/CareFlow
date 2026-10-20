/**
 * 受け入れ枠 (数週続けて空いているか) — 画面での集計と文言。
 *
 * 正典: docs/plans/acceptance-strict-design-2026-10-20.md §2-6・§5。
 * サーバー (GET /api/v1/acceptance-matrix/strict) は職員 × 日 × 時台の「入れる」を
 * 休みの扱いの両方で返す。担い手・見る週・休みの扱いはここで集計する
 * (切り替えのたびにサーバーへ問い合わせない)。バックエンドの `summarize`
 * (`backend/app/services/scheduling/acceptance_strict.py`) と同じ決まり。
 */
import { z } from 'zod';

export const WEEK_CHOICES = [4, 6, 8] as const;
export type WeekChoice = (typeof WEEK_CHOICES)[number];
export type LeaveMode = 'real' | 'ignore';
export type StaffKind = 'staff' | 'manager' | 'trainee';
export type Mark = '○' | '△' | '×' | '－';

const modeCellSchema = z.object({
  state: z.enum(['free', 'tight', 'full', 'off']),
  hours: z.array(z.number()),
  why: z.string().nullable().optional(),
  why_kind: z.string().nullable().optional(),
  unknown_leave: z.string().nullable().optional(),
});

const staffDayCellSchema = z.object({
  n: z.number(),
  state: z.enum(['ok', 'not_employed']),
  real: modeCellSchema.optional(),
  ignore: modeCellSchema.optional(),
});

const jobSchema = z.object({
  id: z.string(),
  status: z.string(),
  scope: z.string().nullable().optional(),
  done: z.number(),
  total: z.number().nullable().optional(),
  current: z.object({ kind: z.string(), period_start: z.string() }).nullable().optional(),
  failed: z.array(
    z.object({ kind: z.string(), period_start: z.string(), reason: z.string().nullable() }),
  ),
  error: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
});

export const strictPayloadSchema = z.object({
  office_id: z.string(),
  today: z.string(),
  plan_only: z.boolean().optional(),
  weekdays: z.array(z.number()),
  weeks: z.array(z.object({ iso_year: z.number(), iso_week: z.number(), monday: z.string() })),
  rules: z.object({
    visit_min: z.number(),
    cap: z.number(),
    buffer_min: z.number(),
    travel_guess_min: z.number(),
    lunch_min: z.number(),
    lunch_from: z.string(),
    lunch_to: z.string(),
    hours: z.tuple([z.number(), z.number()]),
  }),
  staff: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      kind: z.enum(['staff', 'manager', 'trainee']),
      default_selected: z.boolean(),
    }),
  ),
  days: z.array(
    z.object({
      date: z.string(),
      state: z.enum(['ok', 'holiday', 'no_data']),
      holiday_name: z.string().nullable(),
      src: z.string().nullable(),
      tasks_from: z.string().nullable(),
    }),
  ),
  cells: z.record(z.string(), z.record(z.string(), staffDayCellSchema)),
  overrides: z.array(
    z.object({
      weekday: z.number(),
      hour: z.number(),
      time_slot: z.string(),
      status: z.enum(['available', 'consult', 'unavailable']),
      note: z.string().nullable(),
    }),
  ),
  sources: z.object({
    oldest_fetched_at: z.string().nullable(),
    snapshots: z.array(
      z.object({
        kind: z.string(),
        period_start: z.string(),
        status: z.string(),
        fetched_at: z.string(),
      }),
    ),
    unmatched_staff_names: z.record(z.string(), z.number()),
    dropped_events: z.number(),
    last_job: jobSchema.nullable(),
  }),
});

export const refreshJobSchema = jobSchema;
export type StrictPayload = z.infer<typeof strictPayloadSchema>;
export type StrictStaff = StrictPayload['staff'][number];
export type StrictDay = StrictPayload['days'][number];
export type StrictOverride = StrictPayload['overrides'][number];
export type RefreshJob = z.infer<typeof jobSchema>;
export type StaffDayCell = z.infer<typeof staffDayCellSchema>;

export const DOW = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** △ になる最少の週数 (4→3・6→4・8→5、ずれたら ceil(週数×5/8))。 */
export function triangleMin(nWeeks: number): number {
  const table: Record<number, number> = { 4: 3, 6: 4, 8: 5 };
  return table[nWeeks] ?? Math.ceil((nWeeks * 5) / 8);
}

/** 見られた週 counted・見る週 total のうち ok 週で入れたときの記号。 */
export function grade(ok: number, counted: number, total: number): Mark {
  if (counted * 2 < total) return '－';
  if (ok === counted) return '○';
  if (ok >= triangleMin(counted)) return '△';
  return '×';
}

/** 'YYYY-MM-DD' → [年, 月, 日]。 */
function ymd(iso: string): [number, number, number] {
  const [y = 0, m = 1, d = 1] = iso.split('-').map(Number);
  return [y, m, d];
}

function addDaysIso(iso: string, days: number): string {
  const [y, m, d] = ymd(iso);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

/** 'YYYY-MM-DD' → '10/19（月）'。 */
export function dayLabel(iso: string): string {
  const [y, m, d] = ymd(iso);
  const wd = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
  return `${m}/${d}（${DOW[wd]}）`;
}

/** 見る週の月曜 (古い順)。 */
export function selectedMondays(payload: StrictPayload, nWeeks: number): string[] {
  return payload.weeks
    .map((w) => w.monday)
    .slice(0, nWeeks)
    .sort();
}

/** 期間の表示 (例「9/28（月）〜 10/24（土）」)。 */
export function periodLabel(payload: StrictPayload, nWeeks: number): string {
  const mondays = selectedMondays(payload, nWeeks);
  if (mondays.length === 0) return '';
  const lastWd = Math.max(...payload.weekdays, 0);
  const first = mondays[0] ?? '';
  const last = mondays[mondays.length - 1] ?? first;
  return `${dayLabel(first)} 〜 ${dayLabel(addDaysIso(last, lastWd))}`;
}

export interface WeekDetail {
  date: string;
  counted: boolean;
  /** 数えないときの理由 (祝日・データなし・担い手が勤務でない)。 */
  skipReason: string | null;
  free: { name: string; note: string }[];
  off: { name: string; text: string }[];
  busy: { name: string; text: string }[];
  notes: string[];
}

export interface MatrixCell {
  weekday: number;
  hour: number;
  mark: Mark;
  ok: number;
  counted: number;
  total: number;
  perWeek: number[];
}

export interface Summary {
  hours: number[];
  weekdays: number[];
  cells: Map<string, MatrixCell>;
  countedByWeekday: Map<number, number>;
  counts: { ok: number; mid: number; ng: number; none: number };
}

export const cellKey = (weekday: number, hour: number): string => `${weekday}-${hour}`;

function dayDate(monday: string, weekday: number): string {
  return addDaysIso(monday, weekday);
}

/** 画面のマスの判定 (§2-6)。 */
export function summarize(
  payload: StrictPayload,
  selected: ReadonlySet<string>,
  nWeeks: number,
  mode: LeaveMode,
): Summary {
  const mondays = selectedMondays(payload, nWeeks);
  const dayState = new Map(payload.days.map((d) => [d.date, d.state]));
  const ids = payload.staff.map((s) => s.id).filter((id) => selected.has(id));
  const [from, to] = payload.rules.hours;
  const hours: number[] = [];
  for (let h = from; h <= to; h++) hours.push(h);
  const cells = new Map<string, MatrixCell>();
  const countedByWeekday = new Map<number, number>();
  const counts = { ok: 0, mid: 0, ng: 0, none: 0 };
  for (const wd of payload.weekdays) {
    const counted = mondays
      .map((m) => dayDate(m, wd))
      .filter(
        (d) => dayState.get(d) === 'ok' && ids.some((id) => payload.cells[id]?.[d]?.state === 'ok'),
      );
    countedByWeekday.set(wd, counted.length);
    for (const h of hours) {
      const perWeek = counted.map(
        (d) =>
          ids.filter((id) => {
            const c = payload.cells[id]?.[d];
            return c?.state === 'ok' && (c[mode]?.hours ?? []).includes(h);
          }).length,
      );
      const ok = perWeek.filter((n) => n > 0).length;
      const mark = grade(ok, counted.length, mondays.length);
      if (mark === '○') counts.ok++;
      else if (mark === '△') counts.mid++;
      else if (mark === '×') counts.ng++;
      else counts.none++;
      cells.set(cellKey(wd, h), {
        weekday: wd,
        hour: h,
        mark,
        ok,
        counted: counted.length,
        total: mondays.length,
        perWeek,
      });
    }
  }
  return { hours, weekdays: payload.weekdays, cells, countedByWeekday, counts };
}

/** マスの内訳 (週ごと・新しい順)。 */
export function cellDetail(
  payload: StrictPayload,
  selected: ReadonlySet<string>,
  nWeeks: number,
  mode: LeaveMode,
  weekday: number,
  hour: number,
): WeekDetail[] {
  const days = new Map(payload.days.map((d) => [d.date, d]));
  const staff = payload.staff.filter((s) => selected.has(s.id));
  return selectedMondays(payload, nWeeks)
    .map((m) => dayDate(m, weekday))
    .reverse()
    .map((date) => {
      const day = days.get(date);
      const detail: WeekDetail = {
        date,
        counted: false,
        skipReason: null,
        free: [],
        off: [],
        busy: [],
        notes: [],
      };
      if (!day || day.state === 'no_data') {
        detail.skipReason = 'カイポケの記録が無い日のため数えていません';
        return detail;
      }
      if (day.state === 'holiday') {
        detail.skipReason = `祝日（${day.holiday_name ?? ''}）のため数えていません`;
        return detail;
      }
      const working = staff.filter((s) => payload.cells[s.id]?.[date]?.state === 'ok');
      if (working.length === 0) {
        detail.skipReason = '選んだ担い手の勤務日でないため数えていません';
        return detail;
      }
      detail.counted = true;
      if (day.src === 'plan_fallback') detail.notes.push('実績の入力待ちのため予定で見ています');
      if (day.tasks_from === 'staff_events')
        detail.notes.push('らく助に取り込み済みの個別業務で見ています');
      for (const s of working) {
        const cell = payload.cells[s.id]?.[date];
        const rec = cell?.[mode];
        if (!cell || !rec) continue;
        if (mode === 'real' && rec.unknown_leave)
          detail.notes.push(
            `${s.name}: 種類が分からない休み（${rec.unknown_leave}）の時間を塞いでいます`,
          );
        if (rec.state === 'off') {
          detail.off.push({ name: s.name, text: rec.why || '休み' });
        } else if (rec.state === 'full') {
          detail.busy.push({ name: s.name, text: `${cell.n} 件で満杯` });
        } else if (rec.hours.includes(hour)) {
          detail.free.push({ name: s.name, note: rec.why ?? '' });
        } else if (rec.state === 'tight') {
          detail.busy.push({ name: s.name, text: `${cell.n} 件・隙間なし` });
        } else {
          detail.busy.push({ name: s.name, text: `${cell.n} 件・この時間台は空きなし` });
          if (rec.why) detail.off.push({ name: s.name, text: rec.why });
        }
      }
      return detail;
    });
}

export type Preset = 'staff' | 'managers' | 'trainees' | 'all';

/** まとめボタン「職員だけ／＋マネージャー／＋新人／全員」。 */
export function presetSelection(staff: StrictStaff[], preset: Preset): Set<string> {
  const pick = (s: StrictStaff) =>
    preset === 'all' ||
    s.kind === 'staff' ||
    (preset === 'managers' && s.kind === 'manager') ||
    (preset === 'trainees' && s.kind === 'trainee');
  return new Set(staff.filter(pick).map((s) => s.id));
}

export const KIND_LABELS: Record<StaffKind, string> = {
  staff: '職員',
  manager: 'マネージャー',
  trainee: '新人',
};

/** 決まりの文章の数字 (選んだ週数と rules から差し込む)。 */
export function rulesText(rules: StrictPayload['rules'], nWeeks: number) {
  const tri = triangleMin(nWeeks);
  const triText = tri === nWeeks - 1 ? `${tri} 週` : `${tri}〜${nWeeks - 1} 週`;
  const pad = rules.buffer_min + rules.travel_guess_min;
  return {
    intro: `毎週決まって通う新しい患者さんを、何曜日の何時台なら受け入れられるかを、今週を含む直近 ${nWeeks} 週のカイポケの記録（終わった日は実績、今日から先は予定）から出しています。`,
    ok: `見た ${nWeeks} 週のすべてで、その時間台に新しい訪問（${rules.visit_min} 分）を入れられる人が 1 人以上いました。`,
    mid: `${nWeeks} 週のうち ${triText}で入れられる人がいました。（6 週なら 4〜5 週、8 週なら 5〜7 週）`,
    ng: 'それより少ない週しか入れられる人がいませんでした。',
    none: '祝日やデータの無い日が多く、判定できませんでした。',
    how: `その人の勤務の時間の中で、前後の訪問との間に ${pad} 分（ゆとり ${rules.buffer_min} 分＋移動の見込み ${rules.travel_guess_min} 分）をあけて ${rules.visit_min} 分の訪問が入ること。${rules.lunch_from}〜${rules.lunch_to} の中に ${rules.lunch_min} 分の昼休みが残ること。朝会などの予定と重ならないこと。その日の訪問が ${rules.cap} 件に達していないこと。休み・半休の日はその時間を除きます。祝日は数えません。`,
    travel:
      '新しい患者さんのお住まいによる移動は見ていません。お住まいが遠いときは、提案（新しい患者さんの枠を探す）で確かめてください。',
    short: `直近 ${nWeeks} 週のカイポケの記録で、${nWeeks} 週すべてに空きのある人がいた時間 = ○、${tri} 週 = △。訪問 ${rules.visit_min} 分・前後 ${pad} 分・1 日 ${rules.cap} 件まで。`,
  };
}

const OVERRIDE_MARK: Record<StrictOverride['status'], Mark> = {
  available: '○',
  consult: '△',
  unavailable: '×',
};

export function overrideMark(status: StrictOverride['status']): Mark {
  return OVERRIDE_MARK[status];
}

/** 毎週の上書きを優先したマスの記号と、自動の判定と食い違うか。 */
export function effectiveMark(
  auto: MatrixCell,
  override: StrictOverride | undefined,
): { mark: Mark; manual: boolean; conflict: boolean } {
  if (!override) return { mark: auto.mark, manual: false, conflict: false };
  const mark = overrideMark(override.status);
  return { mark, manual: true, conflict: mark !== auto.mark };
}

export function overrideMap(overrides: StrictOverride[]): Map<string, StrictOverride> {
  return new Map(overrides.map((o) => [cellKey(o.weekday, o.hour), o]));
}

const KIND_NAMES: Record<string, string> = {
  plan_csv: '予定',
  actual_csv: '実績',
  tasks: '個別業務',
};

function isoWeekOf(iso: string): number {
  const [y, m, d] = ymd(iso);
  const dt = new Date(Date.UTC(y, m - 1, d));
  const day = (dt.getUTCDay() + 6) % 7;
  dt.setUTCDate(dt.getUTCDate() - day + 3);
  const firstThursday = new Date(Date.UTC(dt.getUTCFullYear(), 0, 4));
  return (
    1 +
    Math.round(
      ((dt.getTime() - firstThursday.getTime()) / 86400000 -
        3 +
        ((firstThursday.getUTCDay() + 6) % 7)) /
        7,
    )
  );
}

/** 取る本の名前 (例「10 月の実績」「第 41 週の個別業務」)。 */
export function sourceLabel(kind: string, periodStart: string): string {
  const name = KIND_NAMES[kind] ?? kind;
  if (kind === 'tasks') return `第 ${isoWeekOf(periodStart)} 週の${name}`;
  return `${Number(periodStart.slice(5, 7))} 月の${name}`;
}

/** 進み具合 (例「3 / 6 本・10 月の実績を取っています」)。 */
export function progressLabel(job: RefreshJob): string {
  const total = job.total ?? 0;
  const head = `${job.done} / ${total} 本`;
  if (job.current && (job.status === 'running' || job.status === 'pending'))
    return `${head}・${sourceLabel(job.current.kind, job.current.period_start)}を取っています`;
  return head;
}

/** 取れなかったものの文言。 */
export function failedMessages(job: RefreshJob | null | undefined): string[] {
  if (!job) return [];
  return job.failed.map((f) =>
    f.kind === 'tasks'
      ? `${sourceLabel(f.kind, f.period_start)}が取れませんでした。らく助に取り込み済みの個別業務で見ています`
      : `${sourceLabel(f.kind, f.period_start)}が取れませんでした。前に取ったものがあればそれで見ています`,
  );
}

/** 'YYYY-MM-DDTHH:MM...' (UTC) → JST の「10/20 9:12」。 */
export function jstLabel(iso: string | null | undefined): string {
  if (!iso) return '';
  const hasZone = /([zZ]|[+-]\d\d:?\d\d)$/.test(iso);
  const dt = new Date(hasZone ? iso : `${iso}Z`);
  const j = new Date(dt.getTime() + 9 * 3600 * 1000);
  return `${j.getUTCMonth() + 1}/${j.getUTCDate()} ${j.getUTCHours()}:${String(j.getUTCMinutes()).padStart(2, '0')}`;
}

/** 今回の取り直しより前に取ったまま使っている材料 (古い材料)。 */
export function staleSources(payload: StrictPayload): string[] {
  const job = payload.sources.last_job;
  if (!job?.started_at) return [];
  const started = new Date(job.started_at).getTime();
  return payload.sources.snapshots
    .filter((s) => new Date(s.fetched_at).getTime() < started)
    .map((s) => `${sourceLabel(s.kind, s.period_start)}（${jstLabel(s.fetched_at)} に取ったもの）`);
}

/** 17〜18 時台 (JST) か (送信・取り込みと重なりやすい)。 */
export function isEveningJst(now: Date = new Date()): boolean {
  const hour = new Date(now.getTime() + 9 * 3600 * 1000).getUTCHours();
  return hour === 17 || hour === 18;
}

export const EVENING_CONFIRM =
  '夕方は送信・取り込みと重なりやすい時間です。急がなければ他の時間に取り直してください。それでも取り直しますか？';
export const REFRESH_WAIT_NOTE =
  '取り直しの間（最大 15 分）は、カイポケへの送信・取り込みが待たされます';

/** 職員の権限などで見られる文言に「直す」「修正」を使わない (試験用に一覧を出す)。 */
export const STATIC_TEXTS = [EVENING_CONFIRM, REFRESH_WAIT_NOTE];
