/**
 * 「休み・勤務表」(/admin/leave) の表示の言葉と小さな計算 (設計 §5・モック「休み・勤務表（案A）」)。
 *
 * 数え方そのもの (その日休む人・休日数・必要休日数) はバックエンドが返す値を使い、
 * ここでは並べ方・まとめ方・言葉だけを決める。
 */
import { AM_OFF_UNTIL, jstToday, PM_OFF_FROM } from '@/lib/leave';
import type {
  LeaveMonthDay,
  LeavePendingItem,
  LeaveRequestType,
  LeaveTypeCode,
} from '@/lib/schemas/leave';

/** 押すと入るものに並べる休みの種類 (モックの順)。 */
export const LEAVE_STAMP_TYPES: readonly LeaveTypeCode[] = ['off', 'am_off', 'pm_off', 'paid_off'];

export const LEAVE_LABEL: Record<LeaveRequestType, string> = {
  off: '公休',
  am_off: '午前休',
  pm_off: '午後休',
  paid_off: '有給',
  cancel: '休みの取り消し',
};

/** マスに出す短い名前。 */
export const LEAVE_SHORT: Record<LeaveRequestType, string> = {
  off: '休',
  am_off: '午前',
  pm_off: '午後',
  paid_off: '有',
  cancel: '取消',
};

export const LEAVE_DESC: Record<LeaveRequestType, string> = {
  off: '終日の休み',
  am_off: `${AM_OFF_UNTIL} から勤務`,
  pm_off: `${PM_OFF_FROM} から休み`,
  paid_off: '有給休暇（休日数には数えない）',
  cancel: '休みを取り消して出勤する',
};

const WEEKDAYS_FROM_SUN = ['日', '月', '火', '水', '木', '金', '土'] as const;
/** バックエンドの weekday (0=月〜6=日) → 曜日の字。 */
export const WEEKDAY_FROM_MON = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** 'YYYY-MM-DD' → 曜日の字 (端末の時刻帯によらない)。 */
export function weekdayJp(iso: string): string {
  const [y = 0, m = 1, d = 1] = iso.split('-').map(Number);
  return WEEKDAYS_FROM_SUN[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]!;
}

/** 'YYYY-MM-DD' → '11/12（木）'。 */
export function fmtDayJp(iso: string): string {
  const [, m = 1, d = 1] = iso.split('-').map(Number);
  return `${m}/${d}（${weekdayJp(iso)}）`;
}

/** 'YYYY-MM' → '2026年11月'。 */
export function fmtMonthJp(ym: string): string {
  const [y = 0, m = 1] = ym.split('-').map(Number);
  return `${y}年${m}月`;
}

/** 'YYYY-MM' の月の数字。 */
export function monthNumber(ym: string): number {
  return Number(ym.split('-')[1] ?? '1');
}

/** 'YYYY-MM' を delta か月ずらす。 */
export function addMonths(ym: string, delta: number): string {
  const [y = 0, m = 1] = ym.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

/** 'YYYY-MM' か ('YYYY-MM-DD' の月) として読めれば 'YYYY-MM'、読めなければ null。 */
export function parseMonthParam(value: string | null | undefined): string | null {
  if (!value) return null;
  const m = /^(\d{4})-(\d{2})(?:-\d{2})?$/.exec(value);
  if (!m) return null;
  const mm = Number(m[2]);
  return mm >= 1 && mm <= 12 ? `${m[1]}-${m[2]}` : null;
}

/** 管理者がいま組んでいる月 (JST の来月) 'YYYY-MM'。 */
export function upcomingLeaveMonth(now: Date = new Date()): string {
  return addMonths(jstToday(now).slice(0, 7), 1);
}

/** 0.5 刻みの数を '10' / '10.5' に。 */
export function fmtNum(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** 'HH:MM:SS' → 'H:MM'。 */
export function fmtClock(value: string | null | undefined): string {
  if (!value) return '--';
  const [h = '0', m = '00'] = value.split(':');
  return `${Number(h)}:${m}`;
}

/** ISO 日時 → 'M/D H:MM' (端末の時刻帯 = JST)。 */
export function fmtDateTimeShort(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export const cellKey = (staffId: string, date: string): string => `${staffId}|${date}`;

/** その日の申請をすべて承認したあとの「その日休む人」(申請ごとの増減を足す)。 */
export function offCountIfAllApproved(
  day: LeaveMonthDay,
  items: readonly LeavePendingItem[],
): number {
  return items.reduce((n, it) => n + (it.off_count_if_approved - it.off_count_now), day.off_count);
}

export interface PendingGroup {
  key: string;
  /** 日付順のとき。 */
  date?: string;
  /** 職員順のとき。 */
  staffId?: string;
  staffName?: string;
  items: LeavePendingItem[];
}

/** 申請中を日付順 (日ごと) または職員順 (人ごと) にまとめる。 */
export function groupPending(
  items: readonly LeavePendingItem[],
  mode: 'date' | 'staff',
): PendingGroup[] {
  const sorted = [...items].sort((a, b) =>
    mode === 'date'
      ? a.date.localeCompare(b.date) || a.staff_order - b.staff_order
      : a.staff_order - b.staff_order || a.date.localeCompare(b.date),
  );
  const groups: PendingGroup[] = [];
  for (const it of sorted) {
    const key = mode === 'date' ? it.date : it.staff_id;
    const last = groups[groups.length - 1];
    if (last && last.key === key) {
      last.items.push(it);
      continue;
    }
    groups.push(
      mode === 'date'
        ? { key, date: it.date, items: [it] }
        : { key, staffId: it.staff_id, staffName: it.staff_name, items: [it] },
    );
  }
  return groups;
}
