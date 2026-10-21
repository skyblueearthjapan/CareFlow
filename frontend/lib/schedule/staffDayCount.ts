/**
 * 職員ごとの「その日の訪問の件数」(schedule-unit-switch-design-2026-10-21.md §3)。
 *
 * 職員リスト (StaffWeekBoard) と職員タイムライン (StaffTimelineView) が同じ数を出すための
 * 単一の出所。行の帰属 (`resolveVisitRowKey`) もここに置き、両方の盤面が使う。
 *
 * 決まり:
 *   - 行の帰属 = 訪問の担当 → (manual でなければ) コース担当 → 「（担当なし）」。
 *   - 取消 (`status='cancelled'`) は「取消を表示」に関係なく数えない。
 *   - 非稼働の利用者の予定 (入院中のまま残った予定など) も数えない = 盤面に出ている稼働の予定だけ。
 *   - 2 名体制の 2 人目・同行は本人の件数に入れず、別に「＋同行 n」で数える。
 *   - 同じ建物の別の方は別々に数える (2 件)。同じ方へ 1 日に 2 回は 2 件で「（M名）」を添える。
 */
import { classifyVisitDisplay } from '@/lib/schedule/visitVisibility';

/** 「（担当なし）」行のキー。`courseDnd.ts` の `UNASSIGNED_ROW_KEY` と同じ値 (循環を避けて直書き)。 */
export const UNASSIGNED_COUNT_ROW_KEY = '__unassigned__';

/** 数えるのに要る最小限の形 (盤面用の訪問型をそのまま渡せる)。 */
export interface DayCountVisit {
  id: string;
  patient_id: string;
  /** 0=月..5=土。 */
  weekday: number;
  course_template_id: string;
  status?: string | null;
  source?: string | null;
  patient_status?: string | null;
  patient_status_since?: string | null;
  visit_date?: string | null;
  primary_staff_id?: string | null;
  manual_staff_override?: boolean | null;
}

/** 同行 1 件 (この職員がこの訪問に同行する)。 */
export interface DayCountCompanion {
  staffId: string;
  visitId: string;
}

export interface StaffDayCount {
  /** 本人の訪問の件数 (取消・非稼働・同行・2 名体制の 2 人目を除く)。 */
  visits: number;
  /** その件数の中の利用者の人数 (同じ方へ 2 回なら visits より少ない)。 */
  patients: number;
  /** 同行・2 名体制の 2 人目 (件数とは別)。 */
  companions: number;
}

/**
 * 訪問の行帰属。訪問自身の primary を最優先 → （manual でなければ）コース担当 → 「（担当なし）」。
 * (臨時テンプレは複数スタッフの臨N コースを束ねるため primary が正。
 *  primary 空＋manual 真は「明示の担当なし」= コース担当に読み替えない・休み設計 §3-2)
 */
export function resolveVisitRowKey(
  visit: Pick<
    DayCountVisit,
    'primary_staff_id' | 'manual_staff_override' | 'course_template_id' | 'weekday'
  >,
  assignedStaffByTemplateWeekday?: Map<string, string>,
): string {
  return (
    visit.primary_staff_id ??
    (visit.manual_staff_override
      ? undefined
      : assignedStaffByTemplateWeekday?.get(`${visit.course_template_id}:${visit.weekday}`)) ??
    UNASSIGNED_COUNT_ROW_KEY
  );
}

/** 件数の Map のキー (`${rowKey}:${weekday}`)。 */
export function staffDayCountKey(rowKey: string, weekday: number): string {
  return `${rowKey}:${weekday}`;
}

/** 件数に入る訪問か (取消でなく、稼働の利用者の予定)。 */
export function isCountedVisit(v: DayCountVisit, today?: string): boolean {
  if (v.status === 'cancelled') return false;
  return classifyVisitDisplay(v, { today }) === 'normal';
}

export interface CountStaffDaysOptions {
  assignedStaffByTemplateWeekday?: Map<string, string>;
  /** 同行 (訪問の `accompaniments[]`)。 */
  companions?: readonly DayCountCompanion[];
  /**
   * 2 名体制の組 (visit_group_id) → 組の訪問 id を盤面と同じ順に並べたもの
   * (CourseDayTablePanel の `visitsByGroupId` = コースの記号の順・同じなら id の順)。
   * 先頭が 1 人目、2 番目以降が 2 人目。
   */
  orderedGroupMembers?: Map<string, readonly string[]>;
  /** 今日 (JST・`YYYY-MM-DD`)。試験用の注入口。 */
  today?: string;
}

/**
 * 2 名体制の組のうち「2 人目」の訪問 id。盤面の並び (`orderedGroupMembers`) で、
 * 数える訪問だけを残したときの 2 番目以降。
 */
function pairSecondIds(
  countedIds: ReadonlySet<string>,
  orderedGroupMembers: Map<string, readonly string[]>,
): Set<string> {
  const out = new Set<string>();
  for (const ids of orderedGroupMembers.values()) {
    const members = ids.filter((id) => countedIds.has(id));
    for (const id of members.slice(1)) out.add(id);
  }
  return out;
}

/**
 * (行 × 曜日) ごとの件数。キーは `staffDayCountKey(rowKey, weekday)`。
 * 1 件も無いセルは Map に載らない (読む側は 0 として扱う)。
 */
export function countStaffDays(
  visits: readonly DayCountVisit[],
  {
    assignedStaffByTemplateWeekday,
    companions = [],
    orderedGroupMembers,
    today,
  }: CountStaffDaysOptions = {},
): Map<string, StaffDayCount> {
  const counted = visits
    .filter((v) => isCountedVisit(v, today))
    .map((v) => ({ v, rowKey: resolveVisitRowKey(v, assignedStaffByTemplateWeekday) }));
  const seconds = orderedGroupMembers
    ? pairSecondIds(new Set(counted.map((x) => x.v.id)), orderedGroupMembers)
    : new Set<string>();

  const out = new Map<string, StaffDayCount>();
  const patientsByKey = new Map<string, Set<string>>();
  const cell = (key: string): StaffDayCount => {
    let c = out.get(key);
    if (!c) {
      c = { visits: 0, patients: 0, companions: 0 };
      out.set(key, c);
    }
    return c;
  };

  for (const { v, rowKey } of counted) {
    const key = staffDayCountKey(rowKey, v.weekday);
    const c = cell(key);
    if (seconds.has(v.id) && rowKey !== UNASSIGNED_COUNT_ROW_KEY) {
      c.companions += 1;
      continue;
    }
    c.visits += 1;
    const ps = patientsByKey.get(key) ?? new Set<string>();
    ps.add(v.patient_id);
    patientsByKey.set(key, ps);
    c.patients = ps.size;
  }

  const countedById = new Map(counted.map((x) => [x.v.id, x]));
  const seen = new Set<string>();
  for (const comp of companions) {
    const hit = countedById.get(comp.visitId);
    // 取消・非稼働の訪問への同行は数えない。本人の行の訪問なら二重に数えない。
    if (!hit || hit.rowKey === comp.staffId) continue;
    const dedupe = `${comp.staffId}:${comp.visitId}`;
    if (seen.has(dedupe)) continue;
    seen.add(dedupe);
    cell(staffDayCountKey(comp.staffId, hit.v.weekday)).companions += 1;
  }
  return out;
}

/** 週 (月〜土) の本人の件数の合計。 */
export function weekVisitTotal(counts: Map<string, StaffDayCount>, rowKey: string): number {
  let n = 0;
  for (let wd = 0; wd < 6; wd += 1) n += counts.get(staffDayCountKey(rowKey, wd))?.visits ?? 0;
  return n;
}

/** 「N件」/ 同じ方へ 2 回行く日だけ「N件（M名）」。 */
export function formatDayCount(c: Pick<StaffDayCount, 'visits' | 'patients'>): string {
  return c.patients < c.visits ? `${c.visits}件（${c.patients}名）` : `${c.visits}件`;
}

/**
 * 件数の色。上限 (設定の「1コースの最大人数」) が読めないときは 'plain' (色を付けない)。
 * ちょうど上限 = 'full' (満杯)、超え = 'over' (超過)。
 */
export type DayCountTone = 'plain' | 'normal' | 'full' | 'over';

export function dayCountTone(n: number, limit: number | null | undefined): DayCountTone {
  if (limit == null || !Number.isFinite(limit) || limit <= 0) return 'plain';
  if (n > limit) return 'over';
  if (n === limit) return 'full';
  return 'normal';
}
