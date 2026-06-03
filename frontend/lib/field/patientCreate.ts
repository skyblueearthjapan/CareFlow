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
