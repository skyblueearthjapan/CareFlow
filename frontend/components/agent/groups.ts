import type { SessionSummary } from './types';

export const GROUP_ORDER = ['決めてほしいもの', '今日', '昨日', '今週', 'それより前'] as const;
export type GroupName = (typeof GROUP_ORDER)[number];

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}
/** 今週 = 月曜始まり */
function startOfWeek(d: Date): number {
  const day = (d.getDay() + 6) % 7;
  return startOfDay(d) - day * 86_400_000;
}

export function groupOf(s: SessionSummary, now: Date = new Date()): GroupName {
  if (s.needs_you) return '決めてほしいもの';
  const t = new Date(s.last_active_at).getTime();
  const today = startOfDay(now);
  if (t >= today) return '今日';
  if (t >= today - 86_400_000) return '昨日';
  if (t >= startOfWeek(now)) return '今週';
  return 'それより前';
}

export interface SessionGroup {
  name: GroupName;
  items: SessionSummary[];
}

/** 一覧を 決めてほしいもの / 今日 / 昨日 / 今週 / それより前 に分ける(空の群は出さない)。新しい順。 */
export function groupSessions(
  list: SessionSummary[],
  now: Date = new Date(),
  query = '',
): SessionGroup[] {
  const q = query.trim().toLowerCase();
  const filtered = q ? list.filter((s) => s.title.toLowerCase().includes(q)) : list;
  const sorted = [...filtered].sort(
    (a, b) => new Date(b.last_active_at).getTime() - new Date(a.last_active_at).getTime(),
  );
  const map = new Map<GroupName, SessionSummary[]>();
  for (const s of sorted) {
    const g = groupOf(s, now);
    map.set(g, [...(map.get(g) ?? []), s]);
  }
  return GROUP_ORDER.filter((g) => map.has(g)).map((g) => ({ name: g, items: map.get(g)! }));
}
