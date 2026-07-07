'use client';

/**
 * CourseDayTable — Wave 17 Phase B-2: 1 つの (曜日 × コース) テーブル.
 *
 * CareFlow #UX-2026W21: リスト表示と語彙統一するため 5 → 3 列構成に縮約:
 *   - 時間帯 (固定): 09:30, 09:45, ... 18:00 の 35 行 (15 分刻み)
 *   - 複数: 👥 アイコン (`patient_requires_multiple_staff=true` 時のみ)
 *   - 患者情報: 氏名 (drag handle) / 住所 / 🕐 時刻条件 / 👩/👨 性別制限 を
 *               縦積み. リスト `WeekdayScheduleCard.VisitRowContent` と
 *               同じ語彙 (共通 `formatTimeCondition` を import).
 *
 * ヘッダー:
 *   - 「{office}-{label} コース ({capacity})」
 *   - 担当スタッフ dropdown (admin/manager only)
 *
 * dnd-kit:
 *   - 各行の 2 つの「データ列セル」(複数 / 患者情報) 全体が 1 つの droppable.
 *   - id = `course-day-cell:${weekday}:${course_template_id}:${HH:MM}`
 *   - 親 (CourseDayTablePanel) が DragEnd を受け取り place-and-fix を呼ぶ。
 */
import { useMemo } from 'react';
import { useDraggable, useDroppable } from '@dnd-kit/core';
import { Info, X } from 'lucide-react';

import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { PushPin, PushPinOff } from '@/components/ui/push-pin';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import { capacityKeyForWeekday } from '@/lib/schemas/v2/course_template';
import type { FreeGap } from '@/lib/scheduling/freeGaps';
import type { CourseV2Read } from '@/lib/queries/courses';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';
import { formatTimeCondition } from '@/components/schedule/WeekdayScheduleCard';
import { PinScopeMenu, type PinScope } from './PinScopeMenu';

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

const TIME_SLOTS = buildCourseTimeSlots();

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
// Types — 1 行分の表示用 visit データ
// ─────────────────────────────────────────────────────────────────────────

/**
 * Wave 38: 2 名体制患者の「相方の現在地」.
 * - `kind: 'pool'`: 相方がプールに残っている (= 未配置 / orphan)
 * - `kind: 'cell'`: 相方が別セルに配置済み. cellLabel + time を表示する.
 *
 * 用途:
 *   - スケジュール側 (CourseDayTable): visit セル下部に「相方: ...」注記を出す.
 *   - プール側 (PatientCard): 残カードに「① 配置済み: 本店-A 15:00」を出す.
 */
export type PartnerLocation = { kind: 'pool' } | { kind: 'cell'; cellLabel: string; time: string };

/** CourseDayTable の 1 セル分の visit 表示用データ. */
export interface CourseGridVisit {
  id: string;
  patient_id: string;
  patient_name: string | null;
  patient_address: string | null;
  /**
   * Wave 18 Phase B-1: 「複数」表示は **患者マスタ** の
   * `patient.requires_multiple_staff` を真値とする (visit.required_staff_count
   * から離脱)。同一スロットに 2 件以上 visit が並ぶ場合も `CourseTimeRow` で
   * 「複数」扱い (重なり = 同時刻 2 件処理) は維持する。
   */
  patient_requires_multiple_staff: boolean;
  /**
   * Wave 18 Phase B-2: 「条件」列に **患者マスタ** の `sex_restriction`
   * (女性のみ / 男性のみ) を template.notes と統合表示するため事前正規化済み
   * のラベル (例: '女性のみ' / null)。
   */
  patient_sex_restriction_label: string | null;
  /**
   * 旧フィールド (互換のため残す。Wave 18 以前の挙動: required_staff_count >= 2
   * を「複数」と扱っていた)。直近のテストやログ確認用に残置するが、表示判定からは
   * 除外する。
   */
  required_staff_count: number;
  /**
   * Phase E-1: 曜日別テーブルの「条件」列にて time_type / preferred_start /
   * preferred_end を表示する (患者リストや患者詳細ページと統一表現).
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
   *    セル下部に "相方: 本店-A 15:00" を text-text-muted の小さな文字で表示する.
   * - `{ kind: 'pool' }` → 相方がプールに残存. "相方: プール" + 警告色.
   * - `null` / `undefined` → 相方表示なし (= 通常患者 or visit_group なし系).
   *
   * 通常患者 (requires_multiple_staff=false) では常に null. 左ボーダー強調も付かない.
   */
  partner_location?: PartnerLocation | null;
  /**
   * Phase G-6: 同住所バケット key (lat/lng を 0.001 桁で丸めた "lat:lng" 文字列).
   * 同 start_slot 内で **異なる患者** が同じ key を持つ visit を複数 (≥2) 持てば
   * 「同住所×同時刻ペア」と判定し、 セルを黄色枠 (border-2 border-yellow-400) で
   * 強調する. リスト表示 (WeekdayScheduleCard) と同じ視覚言語. lat/lng なし患者は
   * null. 同一患者の 2-staff 重複 (visit_group) は除外する.
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
   * true: 🔒 (active) + visit セル背景を黄色で強調.
   * false/null: 🔓 (hover で出現).
   * fixed_visit_id が無い場合は常に false.
   */
  is_pinned?: boolean | null;
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
   * T-1 縦タイムライン用 (schedule-timeline-redesign-design.md)。テーブル表示は
   * `start_slot` (15分丸め) を使うが、タイムラインは時間比例のため実時刻が要る。
   * いずれも省略可 (欠落時タイムラインはその訪問を描かない・テーブルには無影響)。
   */
  start_time?: string | null;
  end_time?: string | null;
  /** T-1: 患者の性別 (patient.sex: male/female/unknown)。カード地色に使う。 */
  patient_sex?: string | null;
}

// ─────────────────────────────────────────────────────────────────────────
// Phase G-6: 同住所×同時刻ペア判定 (CourseTimeRow + テスト共通用)
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
// CareFlow #UX-2026W21: 「患者情報」列の time_type 表示は
// `WeekdayScheduleCard` 由来の共通 `formatTimeCondition` を直接 import する.
// 旧 `formatPatientTimeCondition` は語彙不一致 (🕐 prefix なし / '(~12:00)' 補足なし)
// だったため、共通関数のエイリアスとして残置 (後方互換 — 既存呼出箇所はゼロ).
// ─────────────────────────────────────────────────────────────────────────

/** @deprecated `formatTimeCondition` (WeekdayScheduleCard 経由) を直接使ってください. */
export const formatPatientTimeCondition = formatTimeCondition;

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export interface CourseDayTableProps {
  weekday: number;
  template: CourseTemplateRead;
  /** 当該 (template, weekday) に紐づく週次コース行 (assigned_staff 等の表示用). null = 未生成. */
  course: CourseV2Read | null;
  officeName: string;
  /** 当該 (template_id, weekday) でこの曜日に該当する visits. */
  visits: CourseGridVisit[];
  /** 拠点に属する active staff (担当 dropdown 用). */
  staffOptions: StaffRead[];
  /**
   * Wave 27 Phase B-2/B-3: staffId → EventRead[] のマップ。
   * 担当 dropdown の option に event ラベルを付与し、セル level の warning バッジを出す。
   */
  staffEventsByStaff?: Map<string, EventRead[]>;
  canEdit: boolean;
  /** 担当 dropdown 変更時のハンドラ. course が null のときは null 渡し (新規 case). */
  onChangeAssignedStaff: (staffId: string | null) => void;
  /** dropdown が pending 中 (PATCH /courses/{id} 中) は disabled に. */
  isStaffMutating: boolean;
  /**
   * Wave 36: visit の × ボタンクリック時のハンドラ.
   * canEdit=true のときのみ表示される。
   */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  /**
   * 患者氏名横の info アイコン (🔍) クリック時のハンドラ.
   * 患者の固定枠 vs 今週 visits を比較する PatientScheduleDetailDialog を開く用途.
   * canEdit に依らず常に表示する (閲覧専用ロールでも詳細は見たい).
   */
  onPatientClick?: (patientId: string) => void;
  /**
   * Wave U-2: 「今週のみ」チップ (source='manual_week') クリック時の昇格ハンドラ.
   * 当該患者の今週配置を固定訪問週間 (毎週の型) に反映する。
   * canEdit=true のときのみチップを操作可能にする。
   */
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  /**
   * Phase G-21: 🔒 完全固定 toggle ハンドラ.
   * - visit.fixed_visit_id が無い場合は呼ばれない (button が disabled).
   * - canEdit=false の場合は呼ばれない (button が disabled).
   * - 親は scope に応じて PATCH /patients/fixed-visits/{pfv_id}/pin (= 'day') か
   *   POST /patients/fixed-visits/pin/bulk (= 'all-days', 患者の全 PFV) を発火する.
   *
   * Phase G-47: 第 3 引数 `scope` + 第 4 引数 `patientId` を追加.
   *   - scope='day'      → 当該 PFV のみ反転 (= 既存挙動).
   *   - scope='all-days' → 当該患者の全曜日 PFV を bulk 反転.
   */
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
  /**
   * Phase G-25: staff の所属拠点名を表示するための office_id → name map.
   * 担当 dropdown の各 option に拠点名を併記する用途 (= 全拠点 staff 解放と組合せ).
   */
  officeNameById?: Map<string, string>;
  /**
   * Phase G-55: このコースの実効定員 (= effectiveCapacity) と配置済み件数。
   * - max:    そのコースの実効定員 (開講 A-E は 6 / M系は静的 capacity)。
   * - filled: 当該 (template, weekday) に配置済みの visit 件数。
   * 「空きN枠」表示と頭数ゲート (remaining = max - filled <= 0 → 満員) に使う。
   * 未指定時は空き表示を出さない (= 既存テスト / 旧呼出の後方互換)。
   */
  capacity?: { filled: number; max: number };
  /**
   * Phase G-55: 営業枠から既存 visit を除いた空き時間帯 (≥60分, start 昇順)。
   * 共有 util `@/lib/scheduling/freeGaps` の computeFreeGaps で親が算出して渡す。
   * remaining>0 のときのみ表示する (頭数ゲートは capacity から本コンポーネントで判定)。
   */
  freeGaps?: FreeGap[];
}

export function CourseDayTable({
  weekday,
  template,
  course,
  officeName,
  visits,
  staffOptions,
  staffEventsByStaff,
  canEdit,
  onChangeAssignedStaff,
  isStaffMutating,
  onDeleteVisit,
  onPatientClick,
  onPromoteWeekOnly,
  onTogglePin,
  officeNameById,
  capacity,
  freeGaps,
}: CourseDayTableProps) {
  // visits を slot ("HH:MM") → CourseGridVisit[] にバケット化.
  const occupants = useMemo(() => {
    const m = new Map<string, CourseGridVisit[]>();
    for (const v of visits) {
      const arr = m.get(v.start_slot) ?? [];
      arr.push(v);
      m.set(v.start_slot, arr);
    }
    return m;
  }, [visits]);

  // Wave 27/39: staffEventsByStaff (default {}) は親が指す Map をそのまま使う.
  // useMemo で安定化することで下流の useMemo deps が毎レンダー再計算しない.
  const eventsMap = useMemo(
    () => staffEventsByStaff ?? new Map<string, EventRead[]>(),
    [staffEventsByStaff],
  );

  const assignedStaffId = course?.assigned_staff_id ?? null;
  const courseExists = course != null;

  // ── Wave 39: 担当スタッフの当日イベントを「start_slot 行に 1 ブロック」表示するため
  // start_slot → events[] のマップを構築する。各 event は rowSpan で複数行に span。
  const assignedStaffDayEvents = useMemo(
    () => (assignedStaffId ? getStaffEventsForWeekday(assignedStaffId, weekday, eventsMap) : []),
    [assignedStaffId, weekday, eventsMap],
  );

  /**
   * Wave 39: event の (rowIndex, rowSpan) を計算する.
   * - rowIndex: TIME_SLOTS の index (0-based) — start_time が 15 分境界に
   *   一致しない場合は floor で切り下げ.
   * - rowSpan: ceil((endMin - flooredStartMin) / 15) — 終了時刻が境界外でも
   *   1 セル分はカバーする.
   * - 範囲外 (event が table 表示範囲外) のものはスキップ.
   */
  const eventBlocks = useMemo(() => {
    type EventBlock = { event: EventRead; rowIndex: number; rowSpan: number };
    const blocks: EventBlock[] = [];
    for (const ev of assignedStaffDayEvents) {
      const start = floorToCourseSlot(ev.start_time);
      if (!start) continue;
      const rowIndex = TIME_SLOTS.indexOf(start);
      if (rowIndex < 0) continue;
      const startMin = toMinutes(ev.start_time);
      const endMin = toMinutes(ev.end_time);
      if (startMin == null || endMin == null) continue;
      const startFlooredMin = toMinutes(start) ?? startMin;
      const span = Math.max(1, Math.ceil((endMin - startFlooredMin) / TIME_SLOT_MINUTES));
      // テーブル末尾を超える span は table 末尾までクリップ.
      const clamped = Math.min(span, TIME_SLOTS.length - rowIndex);
      blocks.push({ event: ev, rowIndex, rowSpan: clamped });
    }
    return blocks;
  }, [assignedStaffDayEvents]);

  const capKey = capacityKeyForWeekday(weekday);
  // capKey 判定の前に freeGaps から grid 配置情報を計算しても副作用は無いが、
  // hooks 規則上 useMemo は早期 return より前で呼ぶ必要があるため、ここに置く。
  // ただし capacity に依存する showFreeSlots ゲートは下で適用するので、ここでは
  // 「時刻 → grid 行」へのマッピングのみ行う (gap 自体は props.freeGaps をそのまま使用)。
  // capacity が 0 の曜日 (例: 日曜) は描画しない (親が事前に判定するが念のため)
  if (!capKey) return null;

  // Phase G-55: 実効定員 / 配置済み / 残枠 (= 頭数の空き)。capacity 未指定時は
  // 従来通り定員 6 と仮定し、空き表示はスキップする (= shows summary only when wired).
  const capMax = capacity?.max ?? 6;
  const capFilled = capacity?.filled ?? 0;
  const remaining = Math.max(0, capMax - capFilled);
  // 頭数ゲート: remaining<=0 (満員) のときは時間 gap があっても空き時間帯を出さない。
  const showFreeSlots = capacity != null && remaining > 0;
  const gapsToShow = showFreeSlots ? (freeGaps ?? []) : [];

  const headerLabel = officeName
    ? `${officeName}-${template.label} コース (${capMax})`
    : `${template.label} コース (${capMax})`;

  return (
    <section
      className="overflow-hidden rounded border border-border-default"
      data-testid={`course-day-table-${weekday}-${template.id}`}
      data-course-template-id={template.id}
      data-weekday={weekday}
    >
      {/* ヘッダー: コース名 + 担当 dropdown (Phase G-16: 性別色付け) */}
      {(() => {
        const assignedStaff = staffOptions.find((s) => s.id === assignedStaffId) ?? null;
        const staffSexStyle: React.CSSProperties =
          assignedStaff?.sex === 'female'
            ? { color: '#dc2626', fontWeight: 700 }
            : assignedStaff?.sex === 'male'
              ? { color: '#2563eb', fontWeight: 700 }
              : {};
        return (
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-default bg-bg-muted/40 px-3 py-2">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-text-primary">{headerLabel}</span>
              {/* Phase G-55: 頭数の空き (= remaining 枠) / 満員 を親機の Badge で表示。
                  capacity が wire されたときのみ出す (旧呼出は非表示 = 後方互換)。 */}
              {capacity != null ? (
                remaining > 0 ? (
                  <Badge
                    variant="success"
                    className="tnum"
                    data-testid={`course-day-capacity-${weekday}-${template.id}`}
                    data-remaining={remaining}
                    title={`配置 ${capFilled} 名 / 定員 ${capMax} 名 (空き ${remaining} 枠)`}
                  >
                    {capFilled}/{capMax}・空き{remaining}枠
                  </Badge>
                ) : (
                  <Badge
                    variant="secondary"
                    className="tnum"
                    data-testid={`course-day-capacity-${weekday}-${template.id}`}
                    data-remaining={0}
                    title={`配置 ${capFilled} 名 / 定員 ${capMax} 名 (満員)`}
                  >
                    {capFilled}/{capMax}・満員
                  </Badge>
                )
              ) : null}
            </span>
            <label className="flex items-center gap-1 text-[11px] text-text-secondary">
              <span aria-hidden>👤</span>
              <span>担当:</span>
              <select
                value={assignedStaffId ?? ''}
                onChange={(e) => {
                  const v = e.target.value;
                  onChangeAssignedStaff(v === '' ? null : v);
                }}
                disabled={!canEdit || !courseExists || isStaffMutating}
                className="rounded border border-border-default bg-bg-base px-1.5 py-0.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
                style={staffSexStyle}
                data-testid={`course-staff-select-${weekday}-${template.id}`}
                aria-label={`${headerLabel} の担当スタッフ`}
                title={
                  courseExists
                    ? '担当スタッフを変更します'
                    : '「週を生成」を実行するとコースが作成され担当を変更できます'
                }
              >
                <option value="">未割当</option>
                {staffOptions.map((s) => {
                  const dayEvents = getStaffEventsForWeekday(s.id, weekday, eventsMap);
                  const ev = dayEvents[0];
                  // Phase G-25: 全拠点 staff 解放のため、 各 option に所属拠点 + role を併記
                  const staffOfficeName =
                    (s.primary_office_id && officeNameById?.get(s.primary_office_id)) || null;
                  const roleSuffix = s.role === 'manager' ? '・管理者' : '';
                  const officeSuffix = staffOfficeName
                    ? ` [${staffOfficeName}${roleSuffix}]`
                    : roleSuffix
                      ? ` [${roleSuffix.replace('・', '')}]`
                      : '';
                  const baseName = `${s.name}${officeSuffix}`;
                  const label = ev
                    ? `${baseName} (${ev.type} ${ev.start_time}-${ev.end_time})`
                    : baseName;
                  return (
                    <option key={s.id} value={s.id}>
                      {label}
                    </option>
                  );
                })}
              </select>
            </label>
          </div>
        );
      })()}

      {/*
        Phase G-55: 空き時間帯 (≥60分) は「サマリ帯」をやめ、時間グリッド内の
        該当時刻位置 (午前=上 / 午後=下) にマーカーを配置する (モバイル AgendaBoard
        と同じ「時刻順の位置に空きを出す」semantics)。実際の描画はグリッド内の
        freeGapBlocks (gridRow 配置) で行う。
        - 頭数ゲート (remaining<=0 = 満員) のときは出さない (gapsToShow=[])。
        - 親機意匠: 薄い teal 背景 + amber 左ボーダー + 等幅時刻。warm 配色は持ち込まない。
        - pointer-events-none で下層 cell の droppable / dnd を妨げない (gap は空セル領域)。
      */}

      {/*
        3 列テーブル (CareFlow #UX-2026W21):
          - 時間帯 (固定)
          - 患者情報 (氏名 / 住所 / 時刻条件 / 性別制限 を縦積み — リスト
                    `WeekdayScheduleCard` の `VisitRowContent` と語彙統一)
          - 複数 (👥 icon を中央表示、最右に配置)
      */}
      <div className="overflow-x-auto">
        <div
          className="grid border-t border-border-default text-[11px]"
          style={{
            gridTemplateColumns: '60px minmax(180px, 1fr) 48px',
          }}
        >
          {/* 列ヘッダー */}
          <div className="border-b border-r border-border-default bg-bg-muted px-1.5 py-1 text-[10px] font-semibold text-text-muted">
            時間帯
          </div>
          <div className="border-b border-r border-border-default bg-bg-muted px-1.5 py-1 text-[10px] font-semibold text-text-muted">
            患者情報
          </div>
          <div className="border-b border-border-default bg-bg-muted px-1.5 py-1 text-center text-[10px] font-semibold text-text-muted">
            複数
          </div>

          {/* 35 行 */}
          {TIME_SLOTS.map((time) => (
            <CourseTimeRow
              key={time}
              weekday={weekday}
              templateId={template.id}
              time={time}
              occupants={occupants.get(time) ?? []}
              canEdit={canEdit}
              assignedStaffId={assignedStaffId}
              staffEventsByStaff={eventsMap}
              onDeleteVisit={onDeleteVisit}
              onPatientClick={onPatientClick}
              onPromoteWeekOnly={onPromoteWeekOnly}
              onTogglePin={onTogglePin}
            />
          ))}

          {/*
            Wave 39: スタッフイベントの 1 ブロック表示 (rowSpan).
            - grid-row: ${rowIndex + 2} / span ${rowSpan} で複数 15min 行をまとめて 1 ブロック化.
            - grid-column: 2 / span 2 (時間帯列を除く 2 列分: 複数 / 患者情報) に重ねる.
            - draggable=true (event:{id}) で時刻スライド + 担当者変更を可能にする.
            - pointer-events-none を付けない (= drag 可能, ただし下層 cell の droppable 判定を
              妨げないよう visit 配置時のクリック要件は無いので問題なし).
          */}
          {eventBlocks.map(({ event: ev, rowIndex, rowSpan }) => (
            <CourseEventBlock
              key={`event-block-${ev.id}`}
              event={ev}
              rowIndex={rowIndex}
              rowSpan={rowSpan}
              canEdit={canEdit}
            />
          ))}

          {/*
            Phase G-55: 空き時間帯マーカー (時刻位置配置).
            - gridRow: ${rowIndex + 2} / span ${rowSpan} で gap の時刻位置に配置
              (column header が +1 行を占有するので +2 オフセット)。
            - grid-column: 2 / span 2 (時間帯列を除く 患者情報 + 複数 の 2 列) に重ねる。
            - pointer-events-none: 下層の空セル droppable / dnd を妨げない。
            - 満員 (showFreeSlots=false) のときは gapsToShow=[] なので何も出ない。
          */}
          {gapsToShow.map((gap) => {
            const block = freeGapToGridBlock(gap.startMin, gap.endMin);
            if (!block) return null;
            return (
              <div
                key={`free-gap-${gap.startMin}`}
                style={{
                  gridRow: `${block.rowIndex + 2} / span ${block.rowSpan}`,
                  gridColumn: '2 / span 2',
                }}
                className="pointer-events-none z-0 m-px flex items-start gap-1 rounded border-l-2 border-amber-500 bg-brand-primary/5 px-1.5 py-0.5 text-[10px] font-medium leading-tight text-brand-primary"
                data-testid={`course-day-free-gap-${weekday}-${template.id}-${gap.startMin}`}
                data-free-gap-start={gap.startMin}
                title={`空き時間帯 ${gap.label}`}
              >
                <span className="tnum">{gap.label}</span>
                <span className="text-text-secondary">空き時間</span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────

interface CourseTimeRowProps {
  weekday: number;
  templateId: string;
  time: string;
  occupants: CourseGridVisit[];
  canEdit: boolean;
  /** Wave 27 Phase B-3: 担当スタッフ ID (course.assigned_staff_id). */
  assignedStaffId?: string | null;
  /** Wave 27 Phase B-3: staffId → EventRead[] のマップ. */
  staffEventsByStaff?: Map<string, EventRead[]>;
  /** Wave 36: visit × ボタンクリックハンドラ. */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  /** 患者氏名横の info アイコンクリックハンドラ. */
  onPatientClick?: (patientId: string) => void;
  /** Wave U-2: 「今週のみ」チップの昇格ハンドラ. */
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  /** Phase G-21 / Phase G-47: 🔒 完全固定 toggle ハンドラ (scope + patientId 付き). */
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
}

function CourseTimeRow({
  weekday,
  templateId,
  time,
  occupants,
  canEdit,
  assignedStaffId,
  staffEventsByStaff,
  onDeleteVisit,
  onPatientClick,
  onPromoteWeekOnly,
  onTogglePin,
}: CourseTimeRowProps) {
  // Wave 27 Phase B-3: 担当スタッフがこのスロット時間帯にイベントを持つか判定
  // (visit と event の重複時の ⚠ バッジ用に残す。Wave 39 で event の本文表示は
  // 親側 `eventBlocks` (rowSpan 1 ブロック) に統合した).
  const eventsMap = staffEventsByStaff ?? new Map<string, EventRead[]>();
  const staffDayEvents = assignedStaffId
    ? getStaffEventsForWeekday(assignedStaffId, weekday, eventsMap)
    : [];
  const conflictingEvent =
    assignedStaffId && occupants.length > 0 ? hasEventConflict(time, staffDayEvents) : null;

  const droppableId = courseDayCellDroppableId(weekday, templateId, time);
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled: !canEdit,
    data: { kind: 'course-day-cell', weekday, courseTemplateId: templateId, time },
  });

  // 30 分境界 (HH:00 / HH:30) は時刻ラベル強調. それ以外は薄く.
  const showLabel = time.endsWith(':00') || time.endsWith(':30');

  // この行の grid 行番号 (0-based slot index + 2; column header が row 1 を占有).
  // ★この行の 2 セル (時刻列 / 患者情報列) を **明示的に gridRow 配置** する。
  // 空き時間マーカー / イベントブロックは `gridRow: N / span M` で明示配置されるため、
  // 行セルを auto-placement のままにすると CSS Grid の配置順 (明示→自動) により
  // 行セルがオーバーレイと衝突して押し出され、行全体がズレる (UI 崩れの原因)。
  // 行セルも明示配置にすることで、オーバーレイは「同じセルに重なるだけ」に戻る。
  const gridRowLine = TIME_SLOTS.indexOf(time) + 2;

  // Phase G-6: 同住所×同時刻ペア判定 (detectSameAddressPair を共通使用).
  // 異なる patient_id × 同 same_address_group_id × 2 件以上 で true.
  const hasSameAddressPair = useMemo(() => detectSameAddressPair(occupants), [occupants]);

  // Phase G-21: セル内に「完全固定」visit が 1 件以上含まれていれば黄色背景で強調.
  const hasPinnedVisit = useMemo(
    () => occupants.some((o) => o.is_pinned === true && !!o.fixed_visit_id),
    [occupants],
  );

  return (
    <>
      {/* 時間帯列 (固定 / 全スロットラベル表示) */}
      <div
        className={cn(
          'border-r border-t border-border-default/60 px-1.5 py-0.5 text-right tnum',
          showLabel
            ? 'bg-bg-muted/30 text-[10px] font-semibold text-text-secondary'
            : 'text-[10px] text-text-muted',
        )}
        style={{ gridColumn: 1, gridRow: gridRowLine }}
      >
        {time}
      </div>

      {/*
        複数 / 患者情報 — まとめて 1 つの droppable で囲む.
        CareFlow #UX-2026W21: 5 列 (氏名/住所/複数/条件) → 2 列 (複数/患者情報) に縮約.
        患者情報セルは リスト `WeekdayScheduleCard.VisitRowContent` の語彙 (氏名 + 住所
        + 🕐 時刻条件 + 👩/👨 性別制限) に揃える.
      */}
      <div
        ref={setNodeRef}
        data-droppable={droppableId}
        data-weekday={weekday}
        data-time={time}
        data-course-template-id={templateId}
        data-occupant-count={occupants.length}
        data-same-address-pair={hasSameAddressPair ? 'true' : undefined}
        data-has-pinned-visit={hasPinnedVisit ? 'true' : undefined}
        title={hasSameAddressPair ? '同住所×同時刻ペア' : undefined}
        aria-label={hasSameAddressPair ? '同住所×同時刻ペア' : undefined}
        className={cn(
          'col-span-2 grid grid-cols-subgrid border-t border-border-default/40 transition-colors',
          isOver && canEdit ? 'bg-brand-primary/10 ring-1 ring-brand-primary ring-inset' : '',
          // Phase G-6: 同住所×同時刻ペアを黄色枠で強調 (リスト表示 WeekdayScheduleCard と
          // 同じ視覚言語). ドロップ中の brand-primary ring と衝突しないよう ring 系は
          // 片方のみ active になる順序.
          !isOver && hasSameAddressPair ? 'bg-yellow-50/60 ring-2 ring-yellow-400 ring-inset' : '',
          // Phase G-21: 完全固定 visit を含むセルは薄い黄色背景で強調 (= 移動禁止の視覚化).
          // 同住所ペアと併存するときは同住所ペアのリング表示を優先.
          !isOver && !hasSameAddressPair && hasPinnedVisit ? 'bg-yellow-50/50' : '',
        )}
        style={{ gridColumn: '2 / span 2', gridRow: gridRowLine }}
      >
        {occupants.length === 0 ? (
          // ── 空セル: 2 列を空のまま描画 (droppable hit-area 維持) ─────
          // Wave 39: 担当スタッフの event は親側 `eventBlocks` (rowSpan 1 ブロック) で
          //   描画するためここでは出さない。空セルは droppable のみ。
          <>
            <div className="border-r border-border-default/40 px-1 py-0.5 text-[10px] leading-tight text-text-secondary truncate" />
            <div className="px-1 py-0.5 text-center text-[10px] leading-tight text-text-secondary" />
          </>
        ) : (
          // ── 1 件以上: 患者情報列 + 複数列 ────────────────────────────
          // 同一スロットに 2 件以上の visit がある場合、各列を visit ごとに縦積み.
          // CareFlow #UX-2026W21 fix: 「複数」列を最右に配置 (DOM 順序で制御).
          <>
            {/* 患者情報列: 氏名 / 住所 / 🕐 時刻条件 / 👩/👨 性別制限 を縦積み.
                リスト `WeekdayScheduleCard.VisitRowContent` と語彙統一. */}
            <div className="border-r border-border-default/40 px-1 py-0.5 text-[11px] leading-tight text-text-primary">
              <div className="flex flex-col gap-1">
                {occupants.map((o) => (
                  <OccupantPatientInfo
                    key={`info-${o.id}`}
                    visit={o}
                    canEdit={canEdit}
                    onDeleteVisit={onDeleteVisit}
                    onPatientClick={onPatientClick}
                    onPromoteWeekOnly={onPromoteWeekOnly}
                    onTogglePin={onTogglePin}
                  />
                ))}
                {/* Wave 27 Phase B-3: 担当スタッフのイベント重複 warning バッジ.
                    visit と event 重複の警告は visit 行に残す. */}
                {conflictingEvent ? (
                  <span
                    className="inline-flex w-fit items-center gap-0.5 rounded bg-yellow-100 px-1 py-0.5 text-[10px] font-semibold text-yellow-800 ring-1 ring-yellow-300"
                    data-testid="event-conflict-badge"
                    title={`担当者: ${conflictingEvent.type} ${conflictingEvent.start_time}-${conflictingEvent.end_time}`}
                  >
                    ⚠ 担当不可
                  </span>
                ) : null}
              </div>
            </div>
            {/* 複数列: 👥 アイコンのみ (true) or 空 (false) - 最右に配置. */}
            <div className="px-1 py-0.5 text-center text-[10px] leading-tight text-text-secondary">
              <div className="flex flex-col gap-0.5">
                {occupants.map((o) => {
                  // Wave 23: 「複数」= patient.requires_multiple_staff のみ.
                  // CareFlow #UX-2026W21: 文字列ラベルからアイコン (👥) 表記へ変更.
                  // - 通常複数 (group_slot=1/2) → 👥
                  // - partner_missing (= 片割れのみ未配置) → ⚠ 警告色付き 👥
                  // - 単独 visit (multi=false) → 空セル
                  const isMulti = o.patient_requires_multiple_staff === true;
                  if (!isMulti) {
                    return (
                      <div
                        key={`multi-${o.id}`}
                        data-testid={`course-occupant-multi-${o.id}`}
                        aria-hidden="true"
                      />
                    );
                  }
                  // tooltip / aria-label: partner 情報を読み上げ可能に.
                  const slotTxt = o.group_slot_label
                    ? ` ${o.group_slot_label === 1 ? '①' : '②'}`
                    : '';
                  const baseTitle = o.partner_missing
                    ? `複数 ① のみ (相方未配置)`
                    : `複数訪問${slotTxt}`;
                  const title = o.partner_label
                    ? `${baseTitle} — ペア: ${o.partner_label}`
                    : baseTitle;
                  const warning = o.partner_missing === true;
                  return (
                    <div
                      key={`multi-${o.id}`}
                      data-testid={`course-occupant-multi-${o.id}`}
                      data-multi-staff="true"
                      data-partner-missing={warning ? 'true' : undefined}
                      title={title}
                      aria-label={title}
                      className={warning ? 'text-warning font-semibold' : ''}
                    >
                      <span aria-hidden="true">👥</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// OccupantPatientInfo — CareFlow #UX-2026W21
// リスト `WeekdayScheduleCard.VisitRowContent` 寄せの 1 visit ぶん「患者情報」.
// 氏名 (draggable + 詳細/削除ボタン) / 住所 / 🕐 時刻条件 / 👩/👨 性別制限 を縦積み.
// ─────────────────────────────────────────────────────────────────────────

interface OccupantPatientInfoProps {
  visit: CourseGridVisit;
  canEdit: boolean;
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  onPatientClick?: (patientId: string) => void;
  /** Wave U-2: 「今週のみ」チップの昇格ハンドラ. */
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  /** Phase G-21 / Phase G-47: 🔒 完全固定 toggle ハンドラ (scope + patientId 付き). */
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
}

function OccupantPatientInfo({
  visit,
  canEdit,
  onDeleteVisit,
  onPatientClick,
  onPromoteWeekOnly,
  onTogglePin,
}: OccupantPatientInfoProps) {
  const timeCondition = formatTimeCondition({
    time_type: visit.patient_time_type,
    preferred_start: visit.patient_preferred_start,
    preferred_end: visit.patient_preferred_end,
  });
  const sexText = visit.patient_sex_restriction_label ?? null;
  // `course-occupant-condition-*` testid を空セルでも維持する (テスト互換).
  const condTooltip = [timeCondition, sexText]
    .filter((s): s is string => typeof s === 'string' && s.length > 0)
    .join(' / ');

  // Phase G-19: 行全体 (氏名 + 住所 + 条件) を drag handle にする.
  // 名前部分の click はそのまま onClick (PointerSensor distance:6 で drag と区別).
  // Phase G-21 final C1: pinned visit (= is_pinned=true) は D&D 経路で
  // 動かせなくする (= 物理削除 + 再作成で is_pinned=false 化されるバイパス防止).
  const isPinnedVisit = visit.is_pinned === true;
  const isCancelled = visit.status === 'cancelled';
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: visitDraggableId(visit.id),
    disabled: !canEdit || isPinnedVisit || isCancelled,
    data: { kind: 'placed-visit', visitId: visit.id },
  });

  return (
    // CareFlow #UX-2026W21 fix: 氏名 + 住所 + 条件 を 1 行に横並び (リスト VisitRowContent と統一).
    // 相方注記等の追加情報は OccupantNameDraggable 内部で別行表示される.
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      data-row-draggable-visit-id={visit.id}
      data-row-draggable-disabled={isPinnedVisit ? 'true' : undefined}
      title={
        isPinnedVisit ? 'ピン留め中のため移動できません. 先にピン留めを外してください.' : undefined
      }
      className={cn(
        'flex flex-row flex-wrap items-baseline gap-x-2 gap-y-0 min-w-0 touch-none select-none',
        canEdit && !isPinnedVisit && !isCancelled ? 'cursor-grab active:cursor-grabbing' : '',
        isPinnedVisit ? 'cursor-not-allowed' : '',
        isDragging ? 'opacity-40' : '',
        isCancelled ? 'opacity-50 line-through' : '',
      )}
    >
      {/* R-2: キャンセル badge — 打消し線の影響を受けないよう no-underline を付与 */}
      {isCancelled ? (
        <span className="inline-flex flex-shrink-0 items-center rounded bg-bg-muted px-1 py-0.5 text-[9px] font-semibold text-text-muted ring-1 ring-border [text-decoration:none]">
          キャンセル
        </span>
      ) : null}
      {/* 氏名 — drag handle + 詳細 / 削除ボタン */}
      <OccupantNameDraggable
        visitId={visit.id}
        patientId={visit.patient_id}
        label={visit.patient_name ?? visit.patient_id}
        canEdit={canEdit}
        onDeleteVisit={onDeleteVisit}
        onPatientClick={onPatientClick}
        groupSlotLabel={visit.group_slot_label}
        partnerLabel={visit.partner_label ?? null}
        partnerLocation={visit.partner_location ?? null}
        isMultiStaff={visit.patient_requires_multiple_staff}
        sexRestrictionLabel={visit.patient_sex_restriction_label}
        fixedVisitId={visit.fixed_visit_id ?? null}
        isPinned={visit.is_pinned === true}
        onTogglePin={onTogglePin}
      />
      {/* 住所 — 📍 prefix + truncate + title で full address */}
      {visit.patient_address ? (
        <span
          className="truncate text-[10px] text-text-muted"
          title={visit.patient_address}
          data-testid={`course-occupant-address-${visit.id}`}
        >
          📍 {visit.patient_address}
        </span>
      ) : null}
      {/* 時刻条件 + 性別制限 — `course-occupant-condition-${id}` testid を維持 */}
      <span
        className="inline-flex flex-row gap-x-1 truncate text-[10px] text-text-secondary"
        data-testid={`course-occupant-condition-${visit.id}`}
        title={condTooltip || undefined}
      >
        {timeCondition ? <span className="truncate">{timeCondition}</span> : null}
        {sexText === '女性のみ' ? (
          <span className="truncate text-pink-600">👩 {sexText}</span>
        ) : sexText === '男性のみ' ? (
          <span className="truncate text-blue-600">👨 {sexText}</span>
        ) : sexText ? (
          <span className="truncate">{sexText}</span>
        ) : null}
      </span>
      {/* Wave U-2: 「今週のみ」チップ (source='manual_week' = この週だけの配置).
          クリックで confirm → 固定訪問週間 (毎週の型) への昇格. canEdit=true のみ操作可. */}
      {visit.source === 'manual_week' ? (
        <button
          type="button"
          disabled={!canEdit || !onPromoteWeekOnly}
          onClick={(e) => {
            e.stopPropagation();
            if (!canEdit || !onPromoteWeekOnly) return;
            const pname = visit.patient_name ?? visit.patient_id;
            if (window.confirm('この配置を固定訪問週間（毎週の型）に反映しますか？')) {
              onPromoteWeekOnly(visit.patient_id, pname);
            }
          }}
          onPointerDown={(e) => {
            // 行 drag を抑止 (クリック先行).
            e.stopPropagation();
          }}
          data-testid="visit-week-only-chip"
          title={
            canEdit
              ? 'この週だけの配置です。クリックで固定訪問週間（毎週の型）に反映できます'
              : 'この週だけの配置です'
          }
          className={cn(
            'inline-flex flex-shrink-0 items-center rounded bg-amber-100 px-1 py-0.5 text-[9px] font-semibold text-amber-800 ring-1 ring-amber-300',
            canEdit && onPromoteWeekOnly ? 'cursor-pointer hover:bg-amber-200' : 'cursor-default',
          )}
        >
          今週のみ
        </button>
      ) : null}
      {/* Phase G-22: 🔒 完全固定 toggle — 時間条件 + 性別制限 の直後に配置 (= リスト表示と統一).
          Phase G-47: click 即時 toggle を廃し、 PinScopeMenu で「曜日のみ / 全曜日」 2 択へ. */}
      {canEdit && onTogglePin && (
        <PinScopeMenu
          pfvId={visit.fixed_visit_id ?? ''}
          patientId={visit.patient_id}
          isPinned={visit.is_pinned === true}
          onSelect={onTogglePin}
          disabled={!visit.fixed_visit_id}
          testIdSuffix={visit.id}
        >
          {({ isOpen }) => (
            <button
              type="button"
              className={cn(
                'transition-opacity p-0.5 rounded flex-shrink-0',
                visit.is_pinned === true || isOpen
                  ? 'opacity-100 hover:bg-yellow-100'
                  : 'opacity-0 group-hover:opacity-60 [@media(hover:none)]:opacity-60 hover:opacity-100 hover:bg-yellow-50',
                !visit.fixed_visit_id ? 'cursor-not-allowed opacity-30' : '',
              )}
              onPointerDown={(e) => {
                // 行 drag を抑止 (Popover 側の click 検知より前に行 drag が発火しないように).
                e.stopPropagation();
              }}
              disabled={!visit.fixed_visit_id}
              aria-label={
                visit.is_pinned === true
                  ? `${visit.patient_name ?? visit.patient_id} のピン留めスコープを選択 (ピン留めを外す)`
                  : !visit.fixed_visit_id
                    ? `${visit.patient_name ?? visit.patient_id} は固定枠が無いためピン留めできません`
                    : `${visit.patient_name ?? visit.patient_id} のピン留めスコープを選択 (ピン留めする)`
              }
              title={
                !visit.fixed_visit_id
                  ? '先に固定枠登録が必要'
                  : visit.is_pinned === true
                    ? 'ピン留めを外すスコープを選択 (この曜日のみ / 全曜日)'
                    : 'ピン留めするスコープを選択 (この曜日のみ / 全曜日)'
              }
              aria-pressed={visit.is_pinned === true}
              aria-haspopup="menu"
              aria-expanded={isOpen}
              data-testid={`pin-visit-btn-${visit.id}`}
              data-pinned={visit.is_pinned === true ? 'true' : 'false'}
              data-pfv-id={visit.fixed_visit_id ?? ''}
            >
              {visit.is_pinned === true ? (
                <PushPin className="h-3.5 w-3.5 text-yellow-700" />
              ) : (
                <PushPinOff className="h-3.5 w-3.5 text-text-muted" />
              )}
            </button>
          )}
        </PinScopeMenu>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Sub-components — Wave 18 Phase B-5: 配置済み visit を draggable に
// ─────────────────────────────────────────────────────────────────────────

interface OccupantNameDraggableProps {
  visitId: string;
  /** 患者ID. onPatientClick で詳細ダイアログを開く起点として使用. */
  patientId?: string | null;
  label: string;
  canEdit: boolean;
  /** Wave 36: visit × ボタンクリックハンドラ. canEdit=true のときのみ表示. */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  /** 患者詳細 (固定枠 vs 今週) ダイアログを開くハンドラ. canEdit に依らず表示. */
  onPatientClick?: (patientId: string) => void;
  /** Wave 37 Phase 3-C: 2 名体制 ① / ② バッジ (visit_group_id 内の slot 番号). */
  groupSlotLabel?: 1 | 2;
  /** Wave 37 Phase 3-C: ペア相手の表示用ラベル (tooltip 用). */
  partnerLabel?: string | null;
  /**
   * Wave 38: 相方の現在地 (= 別セル / プール).
   * - `{kind:'cell',cellLabel,time}` → "相方: {cellLabel} {time}" を text-text-muted で表示.
   * - `{kind:'pool'}` → "相方: プール" を警告色で表示.
   * - `null` → 注記行なし.
   */
  partnerLocation?: PartnerLocation | null;
  /**
   * Wave 38: 当該 visit の患者が requires_multiple_staff=true かどうか.
   * true のときのみ左ボーダー (border-l-4 border-brand-primary/60) を付ける.
   * 通常患者の visit セルは見た目を変えない (regression).
   */
  isMultiStaff?: boolean;
  /**
   * Phase G-15: 性別制限ラベル ('女性のみ' / '男性のみ' / null).
   * 女性のみ → 患者名を赤色 (text-red-600)
   * 男性のみ → 患者名を青色 (text-blue-600)
   * その他 → 標準 (色指定なし)
   */
  sexRestrictionLabel?: string | null;
  /**
   * Phase G-21: ソース PFV id.
   * null → 🔒 toggle は disabled. tooltip で「先に固定枠登録が必要」.
   */
  fixedVisitId?: string | null;
  /** Phase G-21: 完全固定中フラグ (= 🔒 表示). */
  isPinned?: boolean;
  /**
   * Phase G-21 / Phase G-47: 🔒 toggle ハンドラ (pfvId, nextPinned, scope, patientId).
   * NOTE: Phase G-22 で実描画は OccupantPatientInfo 側 (PinScopeMenu) に移設済.
   * 本 props は API 互換維持のため残す.
   */
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
}

/**
 * 配置済み visit の氏名セル (drag handle).
 *
 * - draggableId = `visit:${visitId}` で親が「visit 移動」を識別。
 * - canEdit=false (staff) のときは draggable 無効化。
 * - dragging 中は半透明 + cursor-grabbing。
 *
 * ドロップ先 (course-day-cell:* / pool) は親 onDragEnd で分岐し、
 * delete + place-and-fix の 2 段階で実装する (Wave 19 で atomic 化予定)。
 *
 * Wave 37 Phase 3-C:
 *   - groupSlotLabel が指定されたら患者名右に "①" / "②" バッジを付与する。
 *   - partnerLabel が指定されたら tooltip (title) に "ペア: ..." を表示する。
 */
function OccupantNameDraggable({
  visitId,
  patientId,
  label,
  canEdit,
  onDeleteVisit,
  onPatientClick,
  groupSlotLabel,
  partnerLabel,
  partnerLocation,
  isMultiStaff,
  sexRestrictionLabel,
  fixedVisitId,
  isPinned,
  onTogglePin,
}: OccupantNameDraggableProps) {
  // Phase G-15: 性別制限による患者名色 (inline style で確実反映)
  const sexStyle: React.CSSProperties =
    sexRestrictionLabel === '女性のみ'
      ? { color: '#dc2626', fontWeight: 600 }
      : sexRestrictionLabel === '男性のみ'
        ? { color: '#2563eb', fontWeight: 600 }
        : {};
  // Phase G-19: drag handle は親 OccupantPatientInfo の行全体に移動.
  // ここでは click による詳細ポップアップ展開のみ担当.

  // Wave 38: tooltip に相方の現在地も追加 (cellLabel + time 文字列を結合).
  const partnerLocationText = partnerLocation
    ? partnerLocation.kind === 'cell'
      ? `${partnerLocation.cellLabel} ${partnerLocation.time}`
      : 'プール'
    : null;
  const tooltip = partnerLabel
    ? partnerLocationText
      ? `${label} (ペア: ${partnerLabel} / 相方: ${partnerLocationText})`
      : `${label} (ペア: ${partnerLabel})`
    : partnerLocationText
      ? `${label} (相方: ${partnerLocationText})`
      : label;
  const badge = groupSlotLabel === 1 ? '①' : groupSlotLabel === 2 ? '②' : null;

  // Wave 38: 2 名体制患者の visit セルは左ボーダーで控えめに強調.
  // 通常患者 (isMultiStaff=false / undefined) は完全に regression (= 既存挙動).
  const showPartnerRow = partnerLocation != null;
  const partnerRowWarning = partnerLocation?.kind === 'pool';

  return (
    <div
      className={cn(
        'group flex items-start justify-between gap-0.5',
        // Wave 38: 2 名体制 visit のみ左ボーダー (controlled by isMultiStaff フラグ).
        isMultiStaff ? 'border-l-4 border-brand-primary/60 pl-1' : '',
      )}
      data-multi-staff={isMultiStaff ? 'true' : undefined}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0">
        <div
          data-testid={`course-occupant-name-${visitId}`}
          data-draggable-visit-id={visitId}
          data-visit-group-slot={groupSlotLabel ?? ''}
          title={tooltip}
          onClick={(e) => {
            // Phase G-18 / G-19: 患者名クリックで詳細ポップアップを開く
            // (drag は親 OccupantPatientInfo の行全体で担当)
            if (onPatientClick && patientId) {
              e.stopPropagation();
              onPatientClick(patientId);
            }
          }}
          className={cn(
            'truncate',
            onPatientClick && patientId ? 'hover:underline underline-offset-2 cursor-pointer' : '',
          )}
          style={sexStyle}
        >
          {label}
          {badge ? (
            <span
              className="ml-1 inline-flex items-center text-[10px] font-semibold text-brand-primary"
              data-testid={`course-occupant-group-badge-${visitId}`}
              aria-label={`スロット ${groupSlotLabel}`}
            >
              {badge}
            </span>
          ) : null}
        </div>
        {/*
          Wave 38: 相方の現在地を控えめに併記.
          - cell: text-text-muted の text-[10px]
          - pool: text-warning (警告色) の text-[10px]
        */}
        {showPartnerRow && partnerLocation ? (
          <span
            className={cn(
              'truncate text-[10px] leading-tight',
              partnerRowWarning ? 'text-warning' : 'text-text-muted',
            )}
            data-testid={`course-occupant-partner-location-${visitId}`}
            data-partner-location-kind={partnerLocation.kind}
            title={partnerLocationText ?? undefined}
          >
            {partnerLocation.kind === 'cell'
              ? `相方: ${partnerLocation.cellLabel} ${partnerLocation.time}`
              : '相方: プール'}
          </span>
        ) : null}
      </div>
      {onPatientClick && patientId && (
        <button
          type="button"
          // Wave Next 1 L1: タッチデバイス (coarse pointer = hover 不能) では
          // hover で出現せず操作不能だったため常時表示.
          className="opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity p-0.5 hover:bg-bg-muted rounded flex-shrink-0"
          onClick={(e) => {
            e.stopPropagation();
            onPatientClick(patientId);
          }}
          onPointerDown={(e) => {
            // dnd-kit の pointer activation 抑止 (drag 開始させずクリック先行).
            e.stopPropagation();
          }}
          aria-label={`${label} の固定枠と今週スケジュールを比較`}
          title="患者の固定枠と今週スケジュールを比較"
          data-testid={`patient-detail-btn-${visitId}`}
        >
          <Info className="h-3 w-3 text-brand-primary" />
        </button>
      )}
      {/* Phase G-22: 🔒 toggle は OccupantPatientInfo の条件直後 (条件 + 性別制限 の直後) に移設.
          ここでは描画しない. */}
      {canEdit && onDeleteVisit && (
        <button
          type="button"
          className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:bg-red-100 rounded flex-shrink-0"
          onClick={(e) => {
            e.stopPropagation();
            onDeleteVisit(visitId, label);
          }}
          onPointerDown={(e) => {
            // Phase G-19: 行全体が drag handle になったため、 × ボタンクリック時の
            // pointer down を親に伝搬させない (drag 起動防止).
            e.stopPropagation();
          }}
          aria-label={`${label} の訪問を削除`}
          title="この訪問を削除"
          data-testid={`delete-visit-btn-${visitId}`}
        >
          <X className="h-3 w-3 text-red-500" />
        </button>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Wave 39: スタッフイベントの 1 ブロック表示 (rowSpan + draggable)
// ─────────────────────────────────────────────────────────────────────────

interface CourseEventBlockProps {
  event: EventRead;
  /** TIME_SLOTS 上の 0-based 開始 row index. */
  rowIndex: number;
  /** 何 row 分 span するか (= ceil((endMin - flooredStartMin) / 15)). */
  rowSpan: number;
  canEdit: boolean;
}

/**
 * 1 件のスタッフイベントを「rowSpan で複数行を 1 ブロック」表示するコンポーネント.
 *
 * - grid-row: ${rowIndex + 2} / span ${rowSpan} で時間帯セルに重ねる.
 *   (column header が +1 行を占有するので +2 オフセット).
 * - grid-column: 2 / span 2 で時間列を除いた 2 列分 (複数 / 患者情報) を覆う.
 * - draggable id = `event:${event.id}` で親 panel が D&D を識別する.
 * - 配色: 半透明イエロー背景 + 左ボーダー強調 (event.type ごとに色変えは将来 Wave で).
 */
function CourseEventBlock({ event, rowIndex, rowSpan, canEdit }: CourseEventBlockProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: eventDraggableId(event.id),
    disabled: !canEdit,
    data: { kind: 'staff-event', eventId: event.id },
  });

  const labelLines = formatEventLabelLines(event);
  return (
    <div
      ref={setNodeRef}
      data-testid={`event-block-${event.id}`}
      data-event-id={event.id}
      data-event-row-span={rowSpan}
      data-event-start-time={event.start_time}
      title={`${labelLines.title} ${labelLines.time}`}
      style={{
        gridRow: `${rowIndex + 2} / span ${rowSpan}`,
        gridColumn: '2 / span 2',
      }}
      className={cn(
        'z-10 m-px flex flex-col gap-0.5 overflow-hidden rounded border-l-4 border-yellow-500 bg-yellow-50/80 px-1.5 py-0.5 text-[11px] leading-tight text-yellow-900 shadow-sm ring-1 ring-yellow-300',
        canEdit ? 'cursor-grab active:cursor-grabbing select-none touch-none' : '',
        isDragging ? 'opacity-50 ring-2 ring-yellow-500' : '',
      )}
      {...(canEdit ? listeners : {})}
      {...(canEdit ? attributes : {})}
    >
      <span className="truncate font-semibold">{labelLines.title}</span>
      <span className="truncate text-[10px] text-yellow-800">{labelLines.time}</span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
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

/**
 * Phase G-55: 空き時間帯 (FreeGap) を時間グリッド内の (rowIndex, rowSpan) に変換する。
 * - 時刻軸は 09:30〜18:00 / 15 分刻み (TIME_SLOTS)。
 * - rowIndex: gap.startMin を 15 分境界に切り下げた行 index (0-based)。
 * - rowSpan: gap の長さ (15 分単位、最低 1)。表示範囲を超える分はクリップ。
 * - 範囲外 (gap 全体が表示範囲外) は null。
 * これにより「午前の空きは上、午後の空きは下」と時刻位置にマーカーを置ける。
 */
export function freeGapToGridBlock(
  startMin: number,
  endMin: number,
): { rowIndex: number; rowSpan: number } | null {
  const tableStart = TIME_SLOT_START_HOUR * 60 + TIME_SLOT_START_MINUTE;
  const tableEnd = TIME_SLOT_END_HOUR * 60 + TIME_SLOT_END_MINUTE;
  const s = Math.max(startMin, tableStart);
  const e = Math.min(endMin, tableEnd);
  if (e <= s) return null;
  const flooredFromStart = s - tableStart - ((s - tableStart) % TIME_SLOT_MINUTES);
  const rowIndex = flooredFromStart / TIME_SLOT_MINUTES;
  if (rowIndex < 0 || rowIndex >= TIME_SLOTS.length) return null;
  const span = Math.max(1, Math.ceil((e - (tableStart + flooredFromStart)) / TIME_SLOT_MINUTES));
  const clamped = Math.min(span, TIME_SLOTS.length - rowIndex);
  return { rowIndex, rowSpan: clamped };
}
