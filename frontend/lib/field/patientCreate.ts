/**
 * 現場ボード `/m` — 新規患者カルテ登録 (StageB) の pending `patient_create`
 * payload を組み立てる純関数群。
 *
 * 提案シート (SuggestSheet) で入力したカルテ項目 + 希望スケジュール (weekly_pattern)
 * + 採用した枠 (proposed_visits) を 1 つの payload にまとめる。承認 (ApprovePanel →
 * useApproveRequest) 時に backend の `patient_create` applier がこの payload を読んで
 * 患者作成 + normal PFV 確定を同一 TX で行う。
 *
 * UI に依存しない pure function として切り出し、単体テスト (payload 構造 / proposed_visits
 * 生成) を容易にする。
 */
import { WEEKDAY_CODE_TO_INT } from '@/lib/queries/fieldBoard';
import type { WeekdayKey } from '@/lib/schemas/patient';
import type { ProposeSlotItem } from '@/lib/schemas/v2/propose_slots';

/**
 * pending `patient_create` payload の proposed_visits 1 件。
 * backend applier が読む shape ({weekday,start_time,duration_min,course_code}) に一致させる。
 */
export interface ProposedVisit {
  /** 0=Mon..6=Sun (backend は int 曜日で受ける)。 */
  weekday: number;
  /** "HH:MM" の開始時刻。 */
  start_time: string;
  /** 訪問所要 (分)。 */
  duration_min: number;
  /** コースコード (採用枠のコース)。 */
  course_code: string;
}

/** カルテ入力項目 (提案シートの新規モードで入力する患者マスタ準拠の値)。 */
export interface KarteInput {
  name: string;
  code: string;
  kana: string;
  sex: '' | 'male' | 'female' | 'unknown';
  insurance: '' | 'medical' | 'care';
  address: string;
  lat: number | null;
  lng: number | null;
  sex_restriction: '' | 'female_only' | 'male_only';
  requires_multiple_staff: boolean;
  /**
   * 住所から解決した担当拠点 (propose の `resolved_office_id`)。
   * 値があるときのみ payload に載せ、患者の primary_office_id を設定する
   * (NULL だと後続スケジュールで拠点未割当になり不利)。
   */
  primary_office_id: string | null;
}

/** 希望スケジュール (weekly_pattern) を構成する値。 */
export interface DesiredSchedule {
  frequency_per_week: number;
  visit_frequency: 'every' | 'biweekly' | 'monthly';
  preferred_weekdays: WeekdayKey[];
  service_minutes: number;
  time_type: string;
  preferred_start: string | null;
  preferred_end: string | null;
}

/** "HH:MM" + 分 → "HH:MM" の終了時刻 (proposed_visits は start のみ持つので duration から算出はしない)。 */

/**
 * 採用した提案枠 (曜日 → ProposeSlotItem) を proposed_visits 配列へ変換する。
 *
 * - 曜日ごとに 1 枠 (週N日なら N 件)。weekday は backend int 規約 (Mon=0)。
 * - duration_min は枠の start/end 差分 (分) から算出する。end が無い/不正なら
 *   `fallbackMinutes` (= 希望サービス時間) を使う。
 * - weekday 昇順で安定ソートして返す。
 */
export function buildProposedVisits(
  adopted: Map<WeekdayKey, ProposeSlotItem>,
  fallbackMinutes: number,
): ProposedVisit[] {
  const out: ProposedVisit[] = [];
  for (const [, slot] of adopted) {
    const duration = slotDurationMin(slot) ?? fallbackMinutes;
    out.push({
      weekday: slot.weekday,
      start_time: slot.start_time,
      duration_min: duration,
      course_code: slot.course_code,
    });
  }
  out.sort((a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time));
  return out;
}

/** 枠の start_time〜end_time から所要分を算出。パースできなければ null。 */
export function slotDurationMin(slot: ProposeSlotItem): number | null {
  const s = parseHM(slot.start_time);
  const e = parseHM(slot.end_time);
  if (s === null || e === null || e <= s) return null;
  return e - s;
}

function parseHM(hm: string | null | undefined): number | null {
  if (!hm) return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(hm);
  if (!m) return null;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  if (!Number.isFinite(h) || !Number.isFinite(mm)) return null;
  return h * 60 + mm;
}

/**
 * pending `patient_create` の payload を組み立てる。
 *
 * - カルテ項目 (code/name/kana/sex/insurance/address/lat/lng/sex_restriction/
 *   requires_multiple_staff/primary_office_id) + status:'active' を平坦に格納。
 * - weekly_pattern は希望スケジュールから構成 (患者マスタ準拠の dict)。
 * - proposed_visits は採用枠から生成 (承認時に normal PFV 確定に使われる)。
 * - patient_name は ApprovePanel のヘッドライン解決用に冗長に持たせる
 *   (payloadHeadline は patient_name / name を拾う)。
 *
 * 空文字の任意項目は省略 (undefined) し、payload を軽くする。
 */
export function buildPatientCreatePayload(
  karte: KarteInput,
  schedule: DesiredSchedule,
  proposedVisits: ProposedVisit[],
): Record<string, unknown> {
  const weekly_pattern: Record<string, unknown> = {
    frequency_per_week: schedule.frequency_per_week,
    visit_frequency: schedule.visit_frequency,
    preferred_weekdays: schedule.preferred_weekdays,
    service_minutes: schedule.service_minutes,
    time_type: schedule.time_type,
  };
  if (schedule.preferred_start) weekly_pattern.preferred_start = schedule.preferred_start;
  if (schedule.preferred_end) weekly_pattern.preferred_end = schedule.preferred_end;

  const payload: Record<string, unknown> = {
    code: karte.code.trim(),
    name: karte.name.trim(),
    patient_name: karte.name.trim(),
    status: 'active',
    requires_multiple_staff: karte.requires_multiple_staff,
    weekly_pattern,
    proposed_visits: proposedVisits,
  };
  const kana = karte.kana.trim();
  if (kana) payload.kana = kana;
  if (karte.sex) payload.sex = karte.sex;
  if (karte.insurance) payload.insurance = karte.insurance;
  const address = karte.address.trim();
  if (address) payload.address = address;
  if (karte.lat !== null) payload.lat = karte.lat;
  if (karte.lng !== null) payload.lng = karte.lng;
  if (karte.sex_restriction) payload.sex_restriction = karte.sex_restriction;
  // 住所から解決した拠点 (propose の resolved_office_id) があれば伝播。
  // applier は payload.get("primary_office_id") を読んで患者の拠点を設定する。
  if (karte.primary_office_id) payload.primary_office_id = karte.primary_office_id;

  return payload;
}

// ═══════════════════════════════════════════════════════════════════════════
// Phase G-84: 空き枠への直接配置 (Slot Direct Placement)
// ═══════════════════════════════════════════════════════════════════════════
//
// 空き枠タップ → PlacementSheet → pending (patient_create / patient_visit_add)。
// 枠座標 (placement) + 警告 (warnings) + 強行理由 (override_reason) を payload に
// 載せ、承認カード (ApprovePanel) で「どの枠に・誰の隣に・どの警告か」を転記する。
// 警告判定はフロント=サーバ共通の真実 (スペック「警告判定ルール」表) に従う。

/**
 * 空き枠タップ時に CourseSlots / AgendaBoard から PlacementSheet へ伝播する
 * 枠コンテキスト。情報は board API のレスポンスから既に手元にある。
 */
export interface SlotPlacementContext {
  officeId: string;
  officeName: string;
  /**
   * 表示 / 参考用の **週次 `Course.id`** (board API の `BoardCourse.course_id`)。
   * これは `CourseTemplate.id` (= PFV の `course_template_id`) とは別実体であり、
   * テンプレ id ではない (board API はテンプレ id を返さない)。コース解決は
   * proposed_visits の `course_code` トークン (`parse_course_token` 規約) で行うため、
   * この値は placement メタには載せない (表示・キー安定化用途のみ)。
   */
  courseId: string | null;
  /**
   * 拠点付きコーストークン (applier の `parse_course_token` 規約: 例 "稲A")。
   * board の course_code が裸 "A" の場合は office_name から短縮名を前置する。
   * コース解決はこのトークンで行う (proposed_visits.course_code に載る)。
   * トークン化できない場合は表示用の裸コードを保持する。
   */
  courseCode: string;
  courseLabel: string;
  /** 0=Mon..6=Sun (その日の曜日)。 */
  weekday: number;
  /** FreeGap.startMin (0 時起点の分)。 */
  gapStartMin: number;
  /** FreeGap.endMin (0 時起点の分)。 */
  gapEndMin: number;
  /** gap 直前 visit (移動時間の起点 / 表示用)。無ければ null。 */
  prevVisit: { patientId: string; patientName: string; endTime: string } | null;
  /** gap 直後 visit (表示用)。無ければ null。 */
  nextVisit: { patientName: string; startTime: string } | null;
  /** co.capacity.remaining (容量超過の赤判定用)。 */
  capacityRemaining: number;
  /** そのコースの既存 visit 占有 (時刻重複の赤判定用)。 */
  existingVisits: { startTime: string; endTime: string }[];
}

/** 承認カード用の枠座標メタ (payload.placement)。backend と shape を共有する。 */
export interface PlacementMeta {
  office_id: string;
  office_name: string;
  /**
   * 本来は `CourseTemplate.id` を載せるフィールドだが、board API は週次 `Course.id`
   * しか返さずテンプレ id を解決できないため常に `null`。コース解決は
   * `course_code` トークン (proposed_visits.course_code) で行う。
   */
  course_template_id: string | null;
  course_code: string;
  course_label: string;
  weekday: number;
  start_time: string;
  duration_min: number;
  prev_visit: { patient_name: string; end_time: string } | null;
  next_visit: { patient_name: string; start_time: string } | null;
}

/** 配置警告 1 件 (payload.warnings)。level=red は理由必須、yellow は承知チェック。 */
export interface PlacementWarning {
  level: 'red' | 'yellow';
  code:
    | 'capacity_full'
    | 'time_overlap'
    | 'travel_shortage'
    | 'sex_restriction'
    | 'multi_staff'
    | 'outside_hours';
  message: string;
}

/** computePlacementWarnings が読む配置案 (枠 ctx に加え 開始時刻 / 所要 / 任意属性)。 */
export interface PlacementDraft {
  ctx: SlotPlacementContext;
  /** 配置開始時刻 "HH:MM"。 */
  startTime: string;
  /** 訪問所要 (分)。 */
  durationMin: number;
  /**
   * 直前訪問 → 配置先の移動 + バッファー所要 (分)。travel-estimate 由来。
   * null / 未指定なら移動不足 (travel_shortage) は判定しない。
   */
  travelTotalMin?: number | null;
  /** 性別制限 (患者属性)。コース/担当と不一致なら yellow。判定不能なら省略。 */
  sexRestriction?: '' | 'female_only' | 'male_only';
  /** 複数スタッフ必須だが単独想定なら yellow。 */
  requiresMultipleStaff?: boolean;
}

// 営業枠 (フロント表示用の目安。freeGaps.BUSINESS_BLOCKS と同値)。昼休み 12:00-13:00。
const PLACEMENT_AM: readonly [number, number] = [9 * 60 + 30, 12 * 60];
const PLACEMENT_PM: readonly [number, number] = [13 * 60, 18 * 60];

/** "HH:MM" → 0 時起点の分。不正は null。 */
function hmToMin(hm: string | null | undefined): number | null {
  if (!hm) return null;
  const m = /^(\d{1,2}):(\d{2})/.exec(hm.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  if (!Number.isFinite(h) || !Number.isFinite(mm)) return null;
  return h * 60 + mm;
}

/** 2 区間 [aS,aE) と [bS,bE) が重なるか (端点接触は重複としない)。 */
function overlaps(aS: number, aE: number, bS: number, bE: number): boolean {
  return aS < bE && bS < aE;
}

/**
 * 配置案から警告 (赤/黄) を算出する純関数。スペック「警告判定ルール」表に準拠。
 *
 * - 🔴 capacity_full: capacityRemaining <= 0。
 * - 🔴 time_overlap : [start, start+dur) が既存 visit と重複。
 * - 🟡 travel_shortage: start < prevVisit.end + travelTotalMin。
 * - 🟡 sex_restriction: 性別制限が指定されている (判定可能なら不一致扱い)。
 * - 🟡 multi_staff   : requiresMultipleStaff だが単独想定。
 * - 🟡 outside_hours : 営業枠 (09:30-12:00 / 13:00-18:00) 逸脱・昼休み跨ぎ。
 *
 * 戻り値は赤 → 黄の順 (UI で赤帯を上に出す)。
 */
export function computePlacementWarnings(draft: PlacementDraft): PlacementWarning[] {
  const { ctx, startTime, durationMin } = draft;
  const red: PlacementWarning[] = [];
  const yellow: PlacementWarning[] = [];

  const start = hmToMin(startTime);
  const end = start === null ? null : start + durationMin;

  // 🔴 容量超過。
  if (ctx.capacityRemaining <= 0) {
    red.push({
      level: 'red',
      code: 'capacity_full',
      message: 'このコースは満員です（空き枠なし）',
    });
  }

  // 🔴 時刻重複。
  if (start !== null && end !== null) {
    for (const v of ctx.existingVisits) {
      const vs = hmToMin(v.startTime);
      const ve = hmToMin(v.endTime);
      if (vs === null || ve === null || ve <= vs) continue;
      if (overlaps(start, end, vs, ve)) {
        red.push({
          level: 'red',
          code: 'time_overlap',
          message: `既存の訪問（${v.startTime}〜${v.endTime}）と時刻が重なります`,
        });
        break;
      }
    }
  }

  // 🟡 移動不足。
  if (
    start !== null &&
    ctx.prevVisit &&
    draft.travelTotalMin !== null &&
    draft.travelTotalMin !== undefined
  ) {
    const prevEnd = hmToMin(ctx.prevVisit.endTime);
    if (prevEnd !== null && start < prevEnd + draft.travelTotalMin) {
      yellow.push({
        level: 'yellow',
        code: 'travel_shortage',
        message: `直前の訪問から移動時間が不足しています（目安 ${draft.travelTotalMin}分）`,
      });
    }
  }

  // 🟡 性別制限。
  if (draft.sexRestriction) {
    yellow.push({
      level: 'yellow',
      code: 'sex_restriction',
      message: '性別制限のある患者です（担当の性別をご確認ください）',
    });
  }

  // 🟡 複数スタッフ。
  if (draft.requiresMultipleStaff) {
    yellow.push({
      level: 'yellow',
      code: 'multi_staff',
      message: '2名体制が必要な患者です（単独想定の枠です）',
    });
  }

  // 🟡 営業枠逸脱 / 昼休み跨ぎ。
  if (start !== null && end !== null) {
    const inAm = start >= PLACEMENT_AM[0] && end <= PLACEMENT_AM[1];
    const inPm = start >= PLACEMENT_PM[0] && end <= PLACEMENT_PM[1];
    if (!inAm && !inPm) {
      yellow.push({
        level: 'yellow',
        code: 'outside_hours',
        message: '営業枠（09:30-12:00 / 13:00-18:00）の外、または昼休みを跨ぎます',
      });
    }
  }

  return [...red, ...yellow];
}

/** 配置案から payload.placement (枠座標メタ) を組み立てる純関数。 */
export function buildPlacementMeta(
  ctx: SlotPlacementContext,
  startTime: string,
  durationMin: number,
): PlacementMeta {
  return {
    office_id: ctx.officeId,
    office_name: ctx.officeName,
    // board は週次 Course.id しか返さず CourseTemplate.id を解決できないため null 固定。
    // コース解決は proposed_visits の course_code トークンで行う (ctx.courseId は誤投入しない)。
    course_template_id: null,
    course_code: ctx.courseCode,
    course_label: ctx.courseLabel,
    weekday: ctx.weekday,
    start_time: startTime,
    duration_min: durationMin,
    prev_visit: ctx.prevVisit
      ? { patient_name: ctx.prevVisit.patientName, end_time: ctx.prevVisit.endTime }
      : null,
    next_visit: ctx.nextVisit
      ? { patient_name: ctx.nextVisit.patientName, start_time: ctx.nextVisit.startTime }
      : null,
  };
}

/** placement 系 payload の任意ブロック (patient_create / patient_visit_add 共通)。 */
export interface PlacementExtras {
  placement: PlacementMeta;
  warnings: PlacementWarning[];
  /** 赤警告を強行した場合の理由 (赤があれば必須)。 */
  overrideReason: string | null;
}

/** placement / warnings / override_reason を payload に注入する (純関数, in-place 非変更)。 */
function withPlacementExtras(
  payload: Record<string, unknown>,
  extras: PlacementExtras,
): Record<string, unknown> {
  const out = { ...payload };
  out.placement = extras.placement;
  out.warnings = extras.warnings;
  const reason = extras.overrideReason?.trim();
  out.override_reason = reason ? reason : null;
  return out;
}

/**
 * 既存患者の枠配置の「配置方法」(Phase G-85)。
 * - `'permanent'`: 毎週固定。normal PFV にマージ (G-84 までの既定挙動・後方互換)。
 * - `'one_time'` : その週だけの単発。backend が対象週に Visit を 1 件 INSERT する。
 */
export type VisitAddScope = 'permanent' | 'one_time';

/**
 * 単発 (scope=='one_time') のとき payload に同梱する対象週座標 (Phase G-85)。
 * backend は course_id (= 週次 `Course.id`, フロント `ctx.courseId`) と iso_year/iso_week
 * から materialize 先の週次 Course / visit_date を解決する。permanent では同梱しない。
 */
export interface OneTimePlacement {
  /** 週次 `Course.id` (= `SlotPlacementContext.courseId`)。materialize 先の Course。 */
  courseId: string;
  /** ISO 週年。FieldSheets が board から保持。 */
  isoYear: number;
  /** ISO 週番号。FieldSheets が board から保持。 */
  isoWeek: number;
}

/**
 * 既存患者の枠配置 pending `patient_visit_add` payload を組み立てる純関数。
 *
 * backend `_apply_patient_visit_add` が読む shape:
 *   { patient_id, patient_name, proposed_visits:[1枠], scope, placement, warnings, override_reason }
 * proposed_visits は**タップした 1 枠のみ**。
 *
 * Phase G-85: `scope` で配置方法を切り替える。
 * - `'permanent'` (既定): normal PFV にマージ (同一 weekday は置換、無ければ追加 =
 *   PUT 同等の冪等上書き)。course_id/iso は載せない (後方互換)。
 * - `'one_time'`: payload に `course_id` (= `oneTime.courseId`) / `iso_year` / `iso_week`
 *   を載せ、backend がその週に Visit を 1 件 INSERT する (PFV は触らない)。
 */
export function buildPatientVisitAddPayload(
  patientId: string,
  patientName: string,
  proposedVisit: ProposedVisit,
  extras: PlacementExtras,
  scope: VisitAddScope = 'permanent',
  oneTime?: OneTimePlacement | null,
): Record<string, unknown> {
  const base: Record<string, unknown> = {
    patient_id: patientId,
    patient_name: patientName.trim(),
    proposed_visits: [proposedVisit],
    scope,
  };
  // 単発のみ対象週座標を同梱 (permanent では載せない = 後方互換)。
  if (scope === 'one_time' && oneTime) {
    base.course_id = oneTime.courseId;
    base.iso_year = oneTime.isoYear;
    base.iso_week = oneTime.isoWeek;
  }
  return withPlacementExtras(base, extras);
}

/**
 * 新規患者の枠配置 pending `patient_create` payload を組み立てる純関数。
 *
 * {@link buildPatientCreatePayload} の出力に placement / warnings / override_reason
 * を足すだけ (proposed_visits は呼び出し側で 1 枠を渡す想定)。
 */
export function buildPatientCreatePlacementPayload(
  karte: KarteInput,
  schedule: DesiredSchedule,
  proposedVisits: ProposedVisit[],
  extras: PlacementExtras,
): Record<string, unknown> {
  const base = buildPatientCreatePayload(karte, schedule, proposedVisits);
  return withPlacementExtras(base, extras);
}

/** weekday int → 日本語 1 文字 (月..日)。 */
export const WEEKDAY_INT_TO_JA: Record<number, string> = (() => {
  const ja = ['月', '火', '水', '木', '金', '土', '日'];
  const out: Record<number, string> = {};
  for (const [code, n] of Object.entries(WEEKDAY_CODE_TO_INT)) {
    void code;
    out[n] = ja[n] ?? '';
  }
  return out;
})();
