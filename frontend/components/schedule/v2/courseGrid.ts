/**
 * courseGrid — スケジュール盤面 (日タイムライン / 日リスト / 週ビュー) が共有する
 * 純粋ヘルパー・型・定数.
 *
 * 由来: 旧 `CourseDayTable.tsx` (曜日×コースのテーブル UI). Phase 2 でテーブル UI を
 * 撤去した際、コンポーネント以外の共有部分だけをこのモジュールへそのまま切り出した
 * (実装は無変更 / export 名も維持).
 *
 * 収録物:
 *   - 時刻軸定数 (09:30〜18:00 / 15 分刻み) と `buildCourseTimeSlots`
 *   - dnd-kit の draggable / droppable id ヘルパー
 *   - `CourseGridVisit` (1 訪問の表示用データ) / `PartnerLocation`
 *   - 同住所×同時刻ペア判定 `detectSameAddressPair`
 *   - スタッフイベント関連 (`eventTypeLabel` / `formatEventLabel*` /
 *     `getStaffEventsForWeekday` / `hasEventConflict`)
 *   - 時刻ユーティリティ (`toMinutes` / `floorToCourseSlot`)
 */
import type { EventRead } from '@/lib/schemas/staff-events';
import type { Movability } from '@/lib/schemas/v2/patient_fixed_visit';
import { formatTimeCondition } from '@/components/schedule/WeekdayScheduleCard';

// ─────────────────────────────────────────────────────────────────────────
// Constants — 時刻軸 (B-2 / Excel 完全準拠)
// ─────────────────────────────────────────────────────────────────────────

/** 表示時刻範囲: 09:30〜18:00 (両端含む). 15 分刻み → 35 スロット. */
export const TIME_SLOT_START_HOUR = 9;
export const TIME_SLOT_START_MINUTE = 30;
export const TIME_SLOT_END_HOUR = 18;
export const TIME_SLOT_END_MINUTE = 0;
export const TIME_SLOT_MINUTES = 15;

/** 35 行の時刻ラベル ("HH:MM"). */
export function buildCourseTimeSlots(): string[] {
  const out: string[] = [];
  const startTotal = TIME_SLOT_START_HOUR * 60 + TIME_SLOT_START_MINUTE;
  const endTotal = TIME_SLOT_END_HOUR * 60 + TIME_SLOT_END_MINUTE;
  for (let m = startTotal; m <= endTotal; m += TIME_SLOT_MINUTES) {
    const hh = Math.floor(m / 60);
    const mm = m % 60;
    out.push(`${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`);
  }
  return out;
}

// ─────────────────────────────────────────────────────────────────────────
// dnd-kit ID helpers
// ─────────────────────────────────────────────────────────────────────────

export function courseDayCellDroppableId(
  weekday: number,
  courseTemplateId: string,
  time: string,
): string {
  return `course-day-cell:${weekday}:${courseTemplateId}:${time}`;
}

/**
 * Wave 18 Phase B-5: 配置済み visit の draggable id.
 * 親 (CourseDayTablePanel) は `parseVisitDraggableId` で visit 移動を識別する。
 */
export function visitDraggableId(visitId: string): string {
  return `visit:${visitId}`;
}

export function parseVisitDraggableId(id: string): string | null {
  if (!id.startsWith('visit:')) return null;
  return id.slice('visit:'.length);
}

/**
 * Wave 39: スタッフイベントの draggable id.
 * D&D で時刻スライド + 担当者変更を行うため、event ブロックも draggable にする。
 * 親 (CourseDayTablePanel) は `parseEventDraggableId` で event 移動を識別する。
 */
export function eventDraggableId(eventId: string): string {
  return `event:${eventId}`;
}

export function parseEventDraggableId(id: string): string | null {
  if (!id.startsWith('event:')) return null;
  return id.slice('event:'.length);
}

/**
 * "course-day-cell:weekday:course_template_id:HH:MM" を分解.
 * UUID は ':' を含まないので weekday + UUID + hh + mm の 4 セグメント。
 */
export function parseCourseDayCellId(id: string): {
  weekday: number;
  courseTemplateId: string;
  time: string;
} | null {
  if (!id.startsWith('course-day-cell:')) return null;
  const rest = id.slice('course-day-cell:'.length);
  const parts = rest.split(':');
  if (parts.length < 4) return null;
  const weekday = Number.parseInt(parts[0]!, 10);
  const courseTemplateId = parts[1]!;
  const time = `${parts[2]}:${parts[3]}`;
  if (Number.isNaN(weekday) || weekday < 0 || weekday > 6) return null;
  if (!courseTemplateId) return null;
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) return null;
  return { weekday, courseTemplateId, time };
}

// ─────────────────────────────────────────────────────────────────────────
// Types — 1 訪問分の表示用データ
// ─────────────────────────────────────────────────────────────────────────

/**
 * Wave 38: 2 名体制患者の「相方の現在地」.
 * - `kind: 'pool'`: 相方がプールに残っている (= 未配置 / orphan)
 * - `kind: 'cell'`: 相方が別セルに配置済み. cellLabel + time を表示する.
 *
 * 用途:
 *   - スケジュール側: 訪問カード下部に「相方: ...」注記を出す.
 *   - プール側 (PatientCard): 残カードに「① 配置済み: 本店-A 15:00」を出す.
 */
export type PartnerLocation = { kind: 'pool' } | { kind: 'cell'; cellLabel: string; time: string };

/** 盤面 (タイムライン / リスト) の 1 訪問分の表示用データ. */
export interface CourseGridVisit {
  id: string;
  patient_id: string;
  patient_name: string | null;
  patient_address: string | null;
  /**
   * Wave 18 Phase B-1: 「複数」表示は **患者マスタ** の
   * `patient.requires_multiple_staff` を真値とする (visit.required_staff_count
   * から離脱)。
   */
  patient_requires_multiple_staff: boolean;
  /**
   * Wave 18 Phase B-2: `sex_restriction` (女性のみ / 男性のみ) を template.notes と
   * 統合表示するため事前正規化済みのラベル (例: '女性のみ' / null)。
   */
  patient_sex_restriction_label: string | null;
  /**
   * 旧フィールド (互換のため残す。Wave 18 以前の挙動: required_staff_count >= 2
   * を「複数」と扱っていた)。直近のテストやログ確認用に残置するが、表示判定からは
   * 除外する。
   */
  required_staff_count: number;
  /**
   * Phase E-1: time_type / preferred_start / preferred_end を表示する
   * (患者リストや患者詳細ページと統一表現).
   * 患者マスタの `weekly_pattern.time_type` をそのまま転写.
   * 例: '固定' / '時間帯' / '午前' / '午後' / '終日' / null.
   */
  patient_time_type?: string | null;
  /** Phase E-1: '時間帯'/'固定' のときの開始時刻 'HH:MM'. なければ null. */
  patient_preferred_start?: string | null;
  /** Phase E-1: '時間帯' のときの終了時刻 'HH:MM'. なければ null. */
  patient_preferred_end?: string | null;
  /** "HH:MM" (15 分境界に切り下げ済み). */
  start_slot: string;
  /**
   * Wave 37 Phase 3-C: 2 名体制の visit_group_id (BE Phase 2-A で付与).
   * - null  → 単独 visit (= 1 名体制 or 2 名体制 patient で slot 1 が未配置の片割れ)
   * - UUID → 同じ visit_group_id を持つペアが BE 上に存在 (slot 0/1 共有)
   */
  visit_group_id?: string | null;
  /**
   * Wave 37 Phase 3-C: ①/② バッジ用 slot 番号 (1 or 2).
   * 親が同 visit_group_id 内の 2 visit を sort し、先頭=1 / 後尾=2 を割当てる。
   * visit_group_id=null のときは undefined。
   */
  group_slot_label?: 1 | 2;
  /**
   * Wave 37 Phase 3-C: 同 visit_group_id 内の partner visit の表示ラベル (tooltip 用).
   * 例: "佐藤 花子 ② (本店-B コース)"
   */
  partner_label?: string | null;
  /**
   * Wave 37 Phase 3-C: patient.requires_multiple_staff=true なのに同 visit_group_id
   * の partner が存在しない (= slot 1 が未配置) ときに true。
   * 「複数 ① のみ」と警告色で表示する。
   */
  partner_missing?: boolean;
  /**
   * Wave 38: 相方の現在地 (= 別セル / プール).
   * - `{ kind: 'cell', cellLabel: '本店-A', time: '15:00' }` → 相方が別セルに配置済み.
   * - `{ kind: 'pool' }` → 相方がプールに残存. "相方: プール" + 警告色.
   * - `null` / `undefined` → 相方表示なし (= 通常患者 or visit_group なし系).
   *
   * 通常患者 (requires_multiple_staff=false) では常に null. 左ボーダー強調も付かない.
   */
  partner_location?: PartnerLocation | null;
  /**
   * Phase G-6: 同住所バケット key (lat/lng を 0.001 桁で丸めた "lat:lng" 文字列).
   * 同 start_slot 内で **異なる患者** が同じ key を持つ visit を複数 (≥2) 持てば
   * 「同住所×同時刻ペア」と判定し、黄色枠で強調する.
   * lat/lng なし患者は null. 同一患者の 2-staff 重複 (visit_group) は除外する.
   */
  same_address_group_id?: string | null;
  /**
   * Phase G-21: 当該 visit のソース PFV id.
   * - null/undefined : weekly_pattern 由来 (PFV が無い) → pin toggle は disabled.
   * - UUID           : PFV に紐づく → pin toggle が有効.
   */
  fixed_visit_id?: string | null;
  /**
   * Phase G-21: PFV.is_pinned のミラー値.
   * true: 🔒 (active) + 訪問カード背景を黄色で強調.
   * false/null: 🔓 (hover で出現).
   * fixed_visit_id が無い場合は常に false.
   */
  is_pinned?: boolean | null;
  /**
   * PFV.movability のミラー値 (2026-08-07 / PO 要望).
   *
   * 可動域はピン留めの「さらに先」にある固定手段で、'locked' なら提案系エンジンも
   * 自動割当も枠を動かさない。それまで盤面には一切表示されておらず、
   * 「一括ピン解除したが完全固定は守られている」状態が現場から見えなかった。
   *
   * - null/undefined : PFV 非紐付け (weekly_pattern 由来) → 表示しない.
   * - 'unknown'      : 未設定 (既定) → 表示しない (ノイズを増やさない).
   * - それ以外       : 固 / 時 / 曜 の淡いマークを出す.
   */
  movability?: Movability | null;
  /**
   * Wave U-2: visit のソース (入力チャネル).
   * 'manual_week' = この週だけの配置 (型に未反映)。「今週のみ」チップを出す根拠。
   * 欠落 / その他の値ではチップを出さない (寛容)。
   */
  source?: string | null;
  /**
   * R-2: キャンセル表示。'cancelled' のとき grey + 打消し線 + バッジで表示する。
   * 欠落 / その他の値は planned 扱い (寛容)。
   */
  status?: string | null;
  /**
   * T-1 縦タイムライン用 (schedule-timeline-redesign-design.md)。タイムラインは
   * 時間比例のため実時刻が要る。いずれも省略可 (欠落時はその訪問を描かない)。
   */
  start_time?: string | null;
  end_time?: string | null;
  /** T-1: 患者の性別 (patient.sex: male/female/unknown)。カード地色に使う。 */
  patient_sex?: string | null;
}

// ─────────────────────────────────────────────────────────────────────────
// Phase G-6: 同住所×同時刻ペア判定
// ─────────────────────────────────────────────────────────────────────────

/**
 * 同 start_slot に集まった visit 群から「同住所×同時刻ペア」を検出する.
 *
 * **異なる patient_id** で **same_address_group_id** が一致する visit が 2 件以上
 * あれば true. 同一患者の 2-staff 重複 (visit_group の主訪問+副訪問) は除外する.
 *
 * リスト表示 (WeekdayScheduleCard) と同じ視覚言語のため公開関数として export し、
 * テスト + 他コンポーネントから流用可能にする.
 */
export function detectSameAddressPair(
  occupants: ReadonlyArray<Pick<CourseGridVisit, 'patient_id' | 'same_address_group_id'>>,
): boolean {
  if (occupants.length < 2) return false;
  const patientsByKey = new Map<string, Set<string>>();
  for (const o of occupants) {
    const key = o.same_address_group_id;
    if (!key) continue;
    let patients = patientsByKey.get(key);
    if (!patients) {
      patients = new Set<string>();
      patientsByKey.set(key, patients);
    }
    patients.add(o.patient_id);
    if (patients.size >= 2) return true;
  }
  return false;
}

// ─────────────────────────────────────────────────────────────────────────
// Wave 28: event_type → 日本語ラベル変換
// ─────────────────────────────────────────────────────────────────────────

/**
 * DB の event type 文字列 (または EventRead.type) を日本語ラベルに変換。
 * EventRead.type は既に日本語 ('研修' / 'イベント') の場合はそのまま返す。
 */
export const EVENT_TYPE_LABEL: Record<string, string> = {
  training: '研修',
  meeting: '会議',
  event: 'イベント',
  errand: '役所同行',
  interview: '初回面談',
  office: '事務',
  other: 'その他',
  // 既存の日本語 type (EventRead.type) もパスルックアップで対応
  研修: '研修',
  イベント: 'イベント',
};

/** EventRead.type (日本語 or DB snake_case) を日本語ラベルに変換するヘルパー。 */
export function eventTypeLabel(type: string): string {
  return EVENT_TYPE_LABEL[type] ?? type;
}

/**
 * Wave 30: EventRead を「種別: タイトル HH:MM-HH:MM」または「種別 HH:MM-HH:MM」形式に整形。
 * title が存在する場合は「種別: タイトル 開始-終了」、ない場合は「種別 開始-終了」。
 */
export function formatEventLabel(e: {
  type: string;
  title?: string | null;
  start_time: string;
  end_time: string;
}): string {
  const type = eventTypeLabel(e.type);
  const time = `${e.start_time}-${e.end_time}`;
  return e.title ? `${type}: ${e.title} ${time}` : `${type} ${time}`;
}

/**
 * Wave 31: CourseWeekOverview の 2 行表示用ヘルパー。
 * 1 行目: 種別 + タイトル (例: "研修: 接遇マナー")
 * 2 行目: 時刻範囲 (例: "14:00-16:00")
 */
export function formatEventLabelLines(e: {
  type: string;
  title?: string | null;
  start_time: string;
  end_time: string;
}): { title: string; time: string } {
  const type = eventTypeLabel(e.type);
  return {
    title: e.title ? `${type}: ${e.title}` : type,
    time: `${e.start_time}-${e.end_time}`,
  };
}

// ─────────────────────────────────────────────────────────────────────────
// Wave 27 Phase B: Event conflict helpers
// ─────────────────────────────────────────────────────────────────────────

/**
 * 指定スタッフが当該曜日に event を持つ場合、そのラベル文字列を返す。
 * 複数 event がある場合は最初の 1 件のみ使用。
 * weekday: 0=Mon, 1=Tue, ..., 5=Sat (JS getDay: 0=Sun, 1=Mon → 変換必要)
 */
export function getStaffEventsForWeekday(
  staffId: string,
  weekday: number,
  staffEventsByStaff: Map<string, EventRead[]>,
  weekDayDate?: Date,
): EventRead[] {
  const events = staffEventsByStaff.get(staffId) ?? [];
  return events.filter((ev) => {
    const evDate = new Date(ev.date + 'T00:00:00');
    // weekday: 0=Mon → JS getDay: 1; 5=Sat → 6; 6=Sun → 0
    const jsDay = (weekday + 1) % 7;
    // If we have the actual date, match exactly. Otherwise match by weekday.
    if (weekDayDate) {
      return ev.date === weekDayDate.toISOString().slice(0, 10);
    }
    return evDate.getDay() === jsDay;
  });
}

/**
 * visit の start_time と event の start_time/end_time の重複チェック。
 * visit の start_slot (HH:MM) が event の時間帯内に入るかどうか判定。
 */
export function hasEventConflict(visitStartSlot: string, events: EventRead[]): EventRead | null {
  for (const ev of events) {
    if (visitStartSlot >= ev.start_time && visitStartSlot < ev.end_time) {
      return ev;
    }
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────────
// CareFlow #UX-2026W21: time_type 表示は `WeekdayScheduleCard` 由来の共通
// `formatTimeCondition` を直接 import する.
// 旧 `formatPatientTimeCondition` は語彙不一致 (🕐 prefix なし / '(~12:00)' 補足なし)
// だったため、共通関数のエイリアスとして残置 (後方互換 — 既存呼出箇所はゼロ).
// ─────────────────────────────────────────────────────────────────────────

/** @deprecated `formatTimeCondition` (WeekdayScheduleCard 経由) を直接使ってください. */
export const formatPatientTimeCondition = formatTimeCondition;

// ─────────────────────────────────────────────────────────────────────────
// Helpers — 時刻ユーティリティ
// ─────────────────────────────────────────────────────────────────────────

/** "HH:MM[:SS]" → 通算分 (= H*60+M). 不正な入力は null. (Wave 39 helper) */
export function toMinutes(rawTime: string): number | null {
  const m = /^([01]\d|2[0-3]):([0-5]\d)/.exec(rawTime);
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

/** "HH:MM[:SS]" → 15 分境界に切り下げた "HH:MM". 範囲外 (9:30 未満 / 18:00 超) は null. */
export function floorToCourseSlot(rawTime: string): string | null {
  const m = /^([01]\d|2[0-3]):([0-5]\d)/.exec(rawTime);
  if (!m) return null;
  const hh = Number(m[1]);
  const mm = Number(m[2]);
  const total = hh * 60 + mm;
  const start = TIME_SLOT_START_HOUR * 60 + TIME_SLOT_START_MINUTE;
  const end = TIME_SLOT_END_HOUR * 60 + TIME_SLOT_END_MINUTE;
  if (total < start) return null;
  if (total > end) return null;
  const minutesFromStart = total - start;
  const flooredFromStart = minutesFromStart - (minutesFromStart % TIME_SLOT_MINUTES);
  const flooredTotal = start + flooredFromStart;
  const fhh = Math.floor(flooredTotal / 60);
  const fmm = flooredTotal % 60;
  return `${String(fhh).padStart(2, '0')}:${String(fmm).padStart(2, '0')}`;
}
