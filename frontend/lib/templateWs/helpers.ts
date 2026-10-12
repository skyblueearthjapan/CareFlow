/** コースの作業場の純粋な部品（盤面の組み立て・数の差・表示用の文字）。 */
import type { DayView, DraftView, Metrics, SlotCard, Step } from './types';

export const WEEKDAYS = ['月', '火', '水', '木', '金', '土'] as const;

export function fmtMin(m: number | null | undefined): string {
  if (m == null) return '--:--';
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
}

/** "9:30" / "09:30" → 分。形が違えば null。 */
export function parseHm(s: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(s.trim());
  if (!m) return null;
  const v = Number(m[1]) * 60 + Number(m[2]);
  return Number(m[2]) < 60 && v < 24 * 60 ? v : null;
}

export const NO_COURSE = '__none__';

/** 「コースなし」の行か（全拠点では拠点ごとに `__none__:<拠点 ID>`）。 */
export function isNoCourse(id: string): boolean {
  return id === NO_COURSE || id.startsWith(`${NO_COURSE}:`);
}

export interface GridRow {
  id: string;
  label: string;
  manager: boolean;
  /** 全拠点の下書きのときの拠点（見出しの行に使う）。1 拠点なら null。 */
  officeId: string | null;
  office: string | null;
  /** weekday → セル */
  cells: Record<number, { slots: SlotCard[]; staff: string | null; open: boolean }>;
}

/**
 * 行 = コース（A〜E・M）＋ コースなし、列 = 月〜土。人のいない日のコースは薄く（open=false）。
 * 全拠点の下書きは拠点ごとに並べ、コースなしの行も拠点ごとに分ける。
 */
export function buildGrid(view: DraftView): GridRow[] {
  const multi = !!view.all_offices && (view.offices?.length ?? 0) > 1;
  const rows: GridRow[] = view.courses.map((c) => ({
    id: c.id,
    label: c.label,
    manager: c.manager,
    officeId: multi ? (c.office_id ?? null) : null,
    office: multi ? (c.office ?? null) : null,
    cells: {},
  }));
  const offices = multi ? (view.offices ?? []) : [{ id: '', label: '' }];
  const nones = new Map<string, GridRow>(
    offices.map((o) => [
      o.id,
      {
        id: multi ? `${NO_COURSE}:${o.id}` : NO_COURSE,
        label: 'コースなし',
        manager: false,
        officeId: multi ? o.id : null,
        office: multi ? o.label : null,
        cells: {},
      },
    ]),
  );
  const noneOf = (oid: string | undefined) => nones.get(multi ? (oid ?? '') : '') ?? [...nones.values()][0]!;
  const byId = new Map(rows.map((r) => [r.id, r]));
  const all = [...rows, ...nones.values()];
  for (const d of view.days) {
    const empty = () => ({ slots: [] as SlotCard[], staff: null as string | null, open: false });
    for (const r of all) r.cells[d.weekday] = empty();
    for (const c of d.courses) {
      const row = byId.get(c.id);
      if (row) row.cells[d.weekday] = { slots: [], staff: c.staff, open: true };
    }
    for (const s of d.slots) {
      if (!s.placed) continue;
      const row = (s.course_id && byId.get(s.course_id)) || noneOf(s.office_id);
      (row.cells[d.weekday] ??= empty()).slots.push(s);
    }
    for (const r of all) {
      r.cells[d.weekday]?.slots.sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
    }
  }
  const used = (r: GridRow) => Object.values(r.cells).some((c) => c.slots.length > 0);
  if (!multi) {
    const none = [...nones.values()][0]!;
    return used(none) ? [...rows, none] : rows;
  }
  return offices.flatMap((o) => {
    const none = nones.get(o.id)!;
    return [...rows.filter((r) => r.officeId === o.id), ...(used(none) ? [none] : [])];
  });
}

/** 盤面の見出しの数: 曜日ごとの置いた枠（全拠点なら拠点ごとも）。 */
export function dayCounts(view: DraftView, weekday: number): { total: number; byOffice: { label: string; n: number }[] } {
  const slots = view.days.find((d) => d.weekday === weekday)?.slots.filter((s) => s.placed) ?? [];
  const offices = view.all_offices ? (view.offices ?? []) : [];
  return {
    total: slots.length,
    byOffice: offices.map((o) => ({ label: o.label, n: slots.filter((s) => s.office_id === o.id).length })),
  };
}

/** 最適化にかかる時間の見込み（秒）。backend の optimise.plan_seconds と同じ式。 */
export const MAX_TOTAL_SECONDS = 180;
export function estimateSeconds(seconds: number, offices: number, days: number): number {
  const problems = Math.max(1, offices) * Math.max(1, days);
  let per = Math.max(2, Math.ceil(seconds / Math.max(1, offices)));
  if (per * problems > MAX_TOTAL_SECONDS) per = Math.max(1, Math.floor(MAX_TOTAL_SECONDS / problems));
  return per * problems;
}

export type Tone = 'good' | 'bad' | 'same';

/** 小さいほどよい数の前後 → 色の向き。 */
export function tone(before: number | undefined, after: number | undefined): Tone {
  if (before == null || after == null || before === after) return 'same';
  return after < before ? 'good' : 'bad';
}

export const METRIC_ITEMS: { key: keyof Metrics; label: string }[] = [
  { key: 'conflicts', label: '移動が間に合わない組' },
  { key: 'over_cap', label: '上限超え' },
  { key: 'no_course', label: 'コースの無い枠' },
  { key: 'unstaffed', label: '人のいないコース' },
  { key: 'travel_min', label: '移動の合計（分）' },
];

export function dayChangedCount(d: DayView): number {
  return d.slots.filter((s) => s.changed).length;
}

/** 最新の編集の見出し（「あなた」「らく助エージェント」）。 */
export function stepTitle(s: Step): string {
  const what: Record<Step['kind'], string> = {
    move: '枠を動かしました',
    optimise: '最適化しました',
    whatif: 'もしもの条件を変えました',
    add: '枠の無い方を足しました',
    remove: '足した方を外しました',
    commit: '登録しました',
  };
  return `${s.actor_label}が${what[s.kind]}`;
}

/** 最新の編集で変わった枠のキー（盤面で目立たせる）。 */
export function latestStepKeys(view: DraftView): Set<string> {
  const s = view.latest_step;
  return new Set(s && !s.undone ? s.changes.map((c) => c.key) : []);
}
