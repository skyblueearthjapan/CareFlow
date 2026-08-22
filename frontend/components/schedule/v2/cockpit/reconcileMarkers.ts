/**
 * 運転席のゴーストマーカー — 既存 `ReconcileMarker` (KaipokeReconcilePanel) の拡張。
 *
 * 既存パネルは**触らない**契約 (week-cockpit-design.md §4) のため、型の拡張と
 * 差分項目 → マーカー変換をここに置く。StaffWeekBoard は `ReconcileMarkersByCell`
 * を受け取るので、`CockpitMarker` はその上位互換 (構造的部分型) になっている。
 *
 * 方向の読み方 (シートの before/after は方向で意味が入れ替わる):
 *   inbound  (🔄突合の⇩取込候補) … before = らく助(今) / after = カイポケ
 *   outbound (●未送信)          … before = カイポケ(現況) / after = らく助(今)
 * この対応付けは表示側 (DiffDetailCard) が `direction` で行う。
 */
import type { ReconcileMarker, ReconcileMarkersByCell } from '../KaipokeReconcilePanel';
import type { CockpitCorrectionItem, UnsentEvent } from '@/lib/schemas/v2/cockpit';

/** 差分の片側 (before / after)。 */
export interface CockpitMarkerSide {
  /** 解決できた担当スタッフ。CSV 上の氏名しか無い場合は null。 */
  staff_id: string | null;
  /** 表示用の担当者名 (CSV 原文・staff_id が引けないときの唯一の手がかり)。 */
  staff_name?: string;
  /** その側のコース/サービス種別 (差分カードで左右を比べるため側ごとに持つ)。 */
  course_label?: string;
  /** YYYY-MM-DD。当週の実日付に解決済み。 */
  date: string;
  start: string;
  end: string;
}

/** 訪問にも使えるよう拡張したゴーストマーカー。 */
export type CockpitMarker = ReconcileMarker & {
  kind: 'visit' | 'event';
  patient_name?: string;
  course_label?: string;
  before?: CockpitMarkerSide;
  after?: CockpitMarkerSide;
};

export type CockpitMarkersByCell = Map<string, CockpitMarker[]>;

/** 差分シートの向き。 */
export type DiffDirection = 'inbound' | 'outbound';

const WD_JA = '月火水木金土日';

/** 差分項目 (before/after) からフィールドを取り出す (既存パネルと同じ整理)。 */
export function itemField(
  item: Pick<CockpitCorrectionItem, 'before' | 'after'>,
  key: string,
  side?: 'before' | 'after',
): string {
  const rec = (side ? item[side] : (item.after ?? item.before)) as
    | Record<string, unknown>
    | null
    | undefined;
  const v = rec?.[key];
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

/** 'YYYY-MM-DD' → ローカル Date (タイムゾーンのずれない曜日計算用)。 */
export function parseIsoDate(dateIso: string): Date {
  const [y, m, d] = dateIso.split('-');
  return new Date(Number(y), Number(m) - 1, Number(d));
}

/** ISO 日付 → 0=月..6=日。 */
export function weekdayOfIso(dateIso: string): number {
  return (parseIsoDate(dateIso).getDay() + 6) % 7;
}

/** M/d(曜) 表記。 */
export function fmtMd(dateIso: string): string {
  const [, m, d] = dateIso.split('-');
  return `${Number.parseInt(m ?? '0', 10)}/${Number.parseInt(d ?? '0', 10)}(${WD_JA[weekdayOfIso(dateIso)]})`;
}

/** 「日」(1-31) を当週 (月曜起点 7 日) の実日付へ解決する。 */
export function resolveDayInWeek(dayRaw: string, weekStartIso: string): string | null {
  const day = Number.parseInt(dayRaw, 10);
  if (!Number.isFinite(day)) return null;
  const start = parseIsoDate(weekStartIso);
  for (let i = 0; i < 7; i++) {
    const cur = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    if (cur.getDate() === day) {
      const mm = String(cur.getMonth() + 1).padStart(2, '0');
      const dd = String(cur.getDate()).padStart(2, '0');
      return `${cur.getFullYear()}-${mm}-${dd}`;
    }
  }
  return null;
}

// ───────────────────────────────────────────────────────────────────────────
// ●未送信の突合キー (SyncBar → 盤面/タイムラインの ● ドット)
//
// 未送信サマリは CSV 由来で visit_id を持たないことがあるため、
// 「日付 × 開始時刻 × 患者名」で盤面の訪問と突き合わせる。
// ───────────────────────────────────────────────────────────────────────────

/** 氏名の表記ゆれ (半角/全角スペース) を吸収する。 */
export function normalizeNameKey(name: string): string {
  return name.replace(/[\s　]+/g, '');
}

/** 訪問 1 件の未送信キー。 */
export function unsentVisitKey(dateIso: string, startTime: string, patientName: string): string {
  return `v|${dateIso}|${startTime.slice(0, 5)}|${normalizeNameKey(patientName)}`;
}

/** イベント 1 件の未送信キー (イベントは staff_event の id が引ける)。 */
export function unsentEventKey(eventId: string): string {
  return `e|${eventId}`;
}

/** 差分項目の action → マーカーの action。 */
function markerAction(action: string): ReconcileMarker['action'] {
  if (action === 'add') return 'add';
  if (action === 'delete') return 'delete';
  return 'update'; // edit / date_change
}

export interface CorrectionItemToMarkerOptions {
  weekStartIso: string;
  /** 氏名 → staff_id。CSV には氏名しか無いため呼び出し側が用意する。 */
  staffIdByName?: Map<string, string>;
}

/**
 * 差分項目 (CorrectionItemRead + date_iso) → ゴーストマーカー。
 * 日付が当週に解決できない項目は null (盤面に置き場が無い)。
 */
export function correctionItemToMarker(
  item: CockpitCorrectionItem,
  { weekStartIso, staffIdByName }: CorrectionItemToMarkerOptions,
): CockpitMarker | null {
  const action = markerAction(item.action);
  const side = (which: 'before' | 'after'): CockpitMarkerSide | undefined => {
    const rec = item[which];
    if (rec == null) return undefined;
    // date_change は before/after で「日」が違う。必ずその側の date を先に解決し、
    // date_iso (BE が付ける after 側の実日付) はフォールバックに留める
    // (先に見ると before まで after の日付になってしまう)。
    const dateIso =
      resolveDayInWeek(itemField(item, 'date', which), weekStartIso) ??
      (which === 'after' ? (item.date_iso ?? null) : null);
    if (!dateIso) return undefined;
    const staffName = itemField(item, 'staff1', which);
    return {
      staff_id: staffIdByName?.get(staffName) ?? null,
      staff_name: staffName,
      date: dateIso,
      start: itemField(item, 'start_time', which),
      end: itemField(item, 'end_time', which),
      course_label: itemField(item, 'service_type', which) || undefined,
    };
  };

  const before = action === 'add' ? undefined : side('before');
  const after = action === 'delete' ? undefined : side('after');
  const anchor = after ?? before;
  if (!anchor) return null;

  return {
    kind: 'visit',
    action,
    externalId: item.id,
    title: itemField(item, 'user_name') || '（患者不明）',
    patient_name: itemField(item, 'user_name') || undefined,
    course_label: itemField(item, 'service_type') || undefined,
    start: anchor.start,
    end: anchor.end,
    beforeStart: before?.start ?? null,
    beforeEnd: before?.end ?? null,
    before,
    after,
  };
}

/**
 * イベント取込差分 (events-inbound-preview の change) → ゴーストマーカー。
 * inbound 方向のため before=らく助(今) / after=カイポケ。
 */
export function eventChangeToMarker(c: {
  action: ReconcileMarker['action'];
  externalId: string;
  staffId: string;
  staffName?: string;
  date: string;
  start: string;
  end: string;
  title: string;
  beforeStart?: string | null;
  beforeEnd?: string | null;
}): CockpitMarker {
  const side = (start: string, end: string): CockpitMarkerSide => ({
    staff_id: c.staffId,
    staff_name: c.staffName,
    date: c.date,
    start,
    end,
  });
  const before =
    c.action === 'add' ? undefined : side(c.beforeStart ?? c.start, c.beforeEnd ?? c.end);
  const after = c.action === 'delete' ? undefined : side(c.start, c.end);
  return {
    kind: 'event',
    action: c.action,
    externalId: c.externalId,
    title: c.title,
    start: c.start,
    end: c.end,
    beforeStart: c.beforeStart ?? null,
    beforeEnd: c.beforeEnd ?? null,
    before,
    after,
  };
}

/** ●未送信イベント → ゴーストマーカー。 */
export function unsentEventToMarker(ev: UnsentEvent): CockpitMarker {
  const side: CockpitMarkerSide = {
    staff_id: ev.staff_id,
    staff_name: ev.staff_name,
    date: ev.date,
    start: ev.start_time,
    end: ev.end_time,
  };
  return {
    kind: 'event',
    action: ev.kind === 'add' ? 'add' : 'delete',
    externalId: ev.id,
    title: ev.title,
    start: ev.start_time,
    end: ev.end_time,
    beforeStart: null,
    beforeEnd: null,
    before: ev.kind === 'delete' ? side : undefined,
    after: ev.kind === 'add' ? side : undefined,
  };
}

/**
 * マーカー配列 → 盤面セル (`${staffId}:${weekday}`) 索引。
 * before / after のどちらの側もセルを持つので両方に置く (ゴーストの矢印用)。
 */
export function toMarkersByCell(markers: CockpitMarker[]): ReconcileMarkersByCell {
  const map: CockpitMarkersByCell = new Map();
  const put = (staffId: string | null | undefined, dateIso: string, marker: CockpitMarker) => {
    if (!staffId) return;
    const key = `${staffId}:${weekdayOfIso(dateIso)}`;
    const arr = map.get(key) ?? [];
    if (!arr.includes(marker)) arr.push(marker);
    map.set(key, arr);
  };
  for (const m of markers) {
    if (m.before) put(m.before.staff_id, m.before.date, m);
    if (m.after) put(m.after.staff_id, m.after.date, m);
  }
  return map as ReconcileMarkersByCell;
}
