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
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import { capacityKeyForWeekday } from '@/lib/schemas/v2/course_template';
import type { CourseV2Read } from '@/lib/queries/courses';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';
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
  // capacity が 0 の曜日 (例: 日曜) は描画しない (親が事前に判定するが念のため)
  if (!capKey) return null;

  const headerLabel = officeName
    ? `${officeName}-${template.label} コース (6)`
    : `${template.label} コース (6)`;

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
            <span className="text-sm font-semibold text-text-primary">{headerLabel}</span>
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
                  const label = ev
                    ? `${s.name} [${ev.type} ${ev.start_time}-${ev.end_time}]`
                    : s.name;
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

  // Phase G-6: 同住所×同時刻ペア判定 (detectSameAddressPair を共通使用).
  // 異なる patient_id × 同 same_address_group_id × 2 件以上 で true.
  const hasSameAddressPair = useMemo(() => detectSameAddressPair(occupants), [occupants]);

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
        title={hasSameAddressPair ? '同住所×同時刻ペア' : undefined}
        aria-label={hasSameAddressPair ? '同住所×同時刻ペア' : undefined}
        className={cn(
          'col-span-2 grid grid-cols-subgrid border-t border-border-default/40 transition-colors',
          isOver && canEdit ? 'bg-brand-primary/10 ring-1 ring-brand-primary ring-inset' : '',
          // Phase G-6: 同住所×同時刻ペアを黄色枠で強調 (リスト表示 WeekdayScheduleCard と
          // 同じ視覚言語). ドロップ中の brand-primary ring と衝突しないよう ring 系は
          // 片方のみ active になる順序.
          !isOver && hasSameAddressPair ? 'bg-yellow-50/60 ring-2 ring-yellow-400 ring-inset' : '',
        )}
        style={{ gridColumn: 'span 2 / span 2' }}
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
}

function OccupantPatientInfo({
  visit,
  canEdit,
  onDeleteVisit,
  onPatientClick,
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

  return (
    // CareFlow #UX-2026W21 fix: 氏名 + 住所 + 条件 を 1 行に横並び (リスト VisitRowContent と統一).
    // 相方注記等の追加情報は OccupantNameDraggable 内部で別行表示される.
    <div className="flex flex-row flex-wrap items-baseline gap-x-2 gap-y-0 min-w-0">
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
}: OccupantNameDraggableProps) {
  // Phase G-15: 性別制限による患者名色 (inline style で確実反映)
  const sexStyle: React.CSSProperties =
    sexRestrictionLabel === '女性のみ'
      ? { color: '#dc2626', fontWeight: 600 }
      : sexRestrictionLabel === '男性のみ'
        ? { color: '#2563eb', fontWeight: 600 }
        : {};
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: visitDraggableId(visitId),
    disabled: !canEdit,
    data: { kind: 'placed-visit', visitId },
  });

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
        isDragging ? 'opacity-40' : '',
      )}
      data-multi-staff={isMultiStaff ? 'true' : undefined}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0">
        <div
          ref={setNodeRef}
          data-testid={`course-occupant-name-${visitId}`}
          data-draggable-visit-id={visitId}
          data-visit-group-slot={groupSlotLabel ?? ''}
          title={tooltip}
          {...listeners}
          {...attributes}
          onClick={(e) => {
            // Phase G-18: 患者名クリックで詳細ポップアップを開く
            // (DnD は distance:6 で drag 開始するため、 通常クリックは drag 発生せず onClick が発火)
            if (onPatientClick && patientId) {
              e.stopPropagation();
              onPatientClick(patientId);
            }
          }}
          className={cn(
            'truncate select-none touch-none',
            canEdit ? 'cursor-grab active:cursor-grabbing' : '',
            // 詳細クリック可能なら hover で underline + pointer (drag handle と両立)
            onPatientClick && patientId
              ? 'hover:underline underline-offset-2 hover:cursor-pointer'
              : '',
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
      {canEdit && onDeleteVisit && (
        <button
          type="button"
          className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:bg-red-100 rounded flex-shrink-0"
          onClick={(e) => {
            e.stopPropagation();
            onDeleteVisit(visitId, label);
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
