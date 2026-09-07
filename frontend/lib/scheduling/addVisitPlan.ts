/**
 * addVisitPlan — 「＋訪問（任意日付の訪問追加）」の純粋ロジック。
 *
 * 正典 = `docs/plans/add-visit-anywhere-design.md`（§0 PO 決定 1〜12・§3・§4・§5）。
 *
 * ここには **React も API 呼び出しも置かない**。`AddVisitAnywhereDialog` は
 * この関数群だけを使って「どの日付をどのコースにどう入れるか」を組み立て、
 * 実行 (`place-and-fix` / `visit-move-week-only` / `fixed-visits`) は親が行う。
 *
 * 設計の要点:
 *   - 提案は `propose-slots` 1 本に寄せる（§4 新規ロジックを作らない）。
 *     `time_type='固定'` は `preferred_start` に厳密一致する枠だけを返すので、
 *     「この時刻に入れるコースはどれか」がそのまま聞ける。
 *   - `propose-slots` は 1 リクエスト = 1 週。複数日付は ISO 週でまとめて呼ぶ。
 *   - 曜日は 0=月..6=日（`weekdayOfIso` と同じ規約）。API へは `Mon`..`Sun` で渡す。
 */
import { WEEKDAY_KEYS, WEEKDAY_LABELS_JA, type WeekdayKey } from '@/lib/schemas/patient';
import { isoWeekFromLocalDate } from '@/lib/format/isoWeek';
import { parseIsoDate, weekdayOfIso } from '@/components/schedule/v2/cockpit/reconcileMarkers';
import type {
  ExcludedSummaryItem,
  ProposeSlotItem,
  ProposeSlotsRequest,
} from '@/lib/schemas/v2/propose_slots';

// ───────────────────────────────────────────────────────────────────────────
// 型
// ───────────────────────────────────────────────────────────────────────────

/** 反映先 (§3-3 ⑤ / PO 決定 2)。 */
export type AddVisitScope =
  /** (a) 固定訪問スケジュール（型）も変える。日付が 1 つのときだけ選べる。 */
  | 'pattern'
  /** (b) その週の既存訪問を動かす。 */
  | 'week'
  /** (c) 新しく 1 件追加する。 */
  | 'new';

/** 反映先 (b) で「動かす元」に選べる既存訪問の最小形。 */
export interface VisitLite {
  id: string;
  /** YYYY-MM-DD。 */
  visit_date: string;
  /** HH:MM。 */
  start_time: string;
  /** HH:MM。 */
  end_time: string;
  primary_staff_id: string | null;
  staff_name?: string | null;
  course_id?: string | null;
  course_label?: string | null;
  /** 青ピン。true の訪問は動かせない (BE 422 と一致・§5)。 */
  week_pinned: boolean;
  /** `planned` / `completed` / `cancelled` 等。動かせるのは `planned` だけ。 */
  status: string;
  source: string;
}

/** 「動かす元」に選べる訪問か (§5: planned・青ピンでない・当日以前でない)。 */
export function isMovableSourceVisit(visit: VisitLite, todayIso: string): boolean {
  return visit.status === 'planned' && !visit.week_pinned && visit.visit_date > todayIso;
}

/** 1 日付ぶんの決定。 */
export interface AddVisitPlanItem {
  /** YYYY-MM-DD。 */
  date: string;
  isoYear: number;
  isoWeek: number;
  /** 0=月..6=日。 */
  weekday: number;
  /** HH:MM。 */
  startHM: string;
  minutes: number;
  /** 入れる先の拠点。M は常に自拠点 (§5)。 */
  officeId: string | null;
  /** 解決できなかったときは null (= 臨・コースなし)。 */
  courseTemplateId: string | null;
  courseLabel: string;
  /** M（担当なし）を受け皿にしたか。 */
  isM: boolean;
  /** 「他拠点（要確認）」から選んだか (PO 決定 11)。 */
  isOtherOffice: boolean;
  /** 2 名体制の患者なら 2 (`place-and-fix` の `staff_count`)。 */
  staffCount: 1 | 2;
  /** 2 名体制のときの相方コース (`slot.partner_course_template_id`)。 */
  partnerCourseTemplateId: string | null;
  /**
   * 2 名体制の患者を M（担当なし）へ入れるとき true。`place-and-fix` は staff_count=2 に
   * 異なる 2 テンプレートを要求する (同一 M ×2 は 422) ため、M では 1 名分だけ登録し
   * もう 1 名は盤面/プールで手当てしてもらう (レビュー指摘 2026-09-07)。
   */
  mSingleStaffFallback: boolean;
  /** M 配置理由（任意入力・PO 決定 10）。空欄は null。 */
  reason: string | null;
  scope: AddVisitScope;
  /** scope='week' のときの動かす元。無ければ null (= 実質 'new')。 */
  sourceVisit: VisitLite | null;
  /**
   * 主担当拠点で候補 0 件だったときの理由**コード**
   * (`capacity_full` 等。日本語化は `excludedReasonLabel`)。0 件でなければ null。
   */
  noCandidateReason: string | null;
}

export interface AddVisitPlan {
  patientId: string;
  items: AddVisitPlanItem[];
}

/** 日付を ISO 週でまとめた 1 グループ (= `propose-slots` 1 リクエスト分)。 */
export interface AddVisitDateGroup {
  isoYear: number;
  isoWeek: number;
  /** 昇順・重複なし。 */
  dates: string[];
  /** 0=月..6=日。昇順・重複なし。 */
  weekdays: number[];
}

/** `buildProposeRequest` に渡す患者の最小形。 */
export interface AddVisitProposePatient {
  id: string;
  lat: number | null;
  lng: number | null;
  sex_restriction: string | null;
  requires_multiple_staff: boolean;
}

// ───────────────────────────────────────────────────────────────────────────
// 日付 / ISO 週
// ───────────────────────────────────────────────────────────────────────────

/**
 * 'YYYY-MM-DD' → ISO 週 + 曜日 (0=月..6=日)。
 *
 * 既存の `isoWeekFromLocalDate` / `parseIsoDate` / `weekdayOfIso` を再利用する
 * （日付計算をこのファイルで作り直さない）。
 */
export function isoWeekOfDate(dateIso: string): {
  isoYear: number;
  isoWeek: number;
  weekday: number;
} {
  const { isoYear, isoWeek } = isoWeekFromLocalDate(parseIsoDate(dateIso));
  return { isoYear, isoWeek, weekday: weekdayOfIso(dateIso) };
}

/**
 * 日付列を ISO 週でまとめる（`propose-slots` は 1 リクエスト = 1 週のため）。
 *
 * 重複は除き、グループも各グループ内の日付も昇順。
 */
export function groupDatesByIsoWeek(dates: string[]): AddVisitDateGroup[] {
  const uniqueSorted = Array.from(new Set(dates)).sort();
  const byKey = new Map<string, AddVisitDateGroup>();
  for (const date of uniqueSorted) {
    const { isoYear, isoWeek, weekday } = isoWeekOfDate(date);
    const key = `${isoYear}-${isoWeek}`;
    const group = byKey.get(key) ?? { isoYear, isoWeek, dates: [], weekdays: [] };
    group.dates.push(date);
    if (!group.weekdays.includes(weekday)) group.weekdays.push(weekday);
    byKey.set(key, group);
  }
  const groups = Array.from(byKey.values());
  for (const g of groups) g.weekdays.sort((a, b) => a - b);
  groups.sort((a, b) => a.isoYear - b.isoYear || a.isoWeek - b.isoWeek);
  return groups;
}

/** 0=月..6=日 → 'Mon'..'Sun'。範囲外は 'Mon' に丸める。 */
export function weekdayCodeOf(weekday: number): WeekdayKey {
  return WEEKDAY_KEYS[weekday] ?? 'Mon';
}

/** 'Mon'..'Sun' → 0=月..6=日。未知コードは -1。 */
export function weekdayIndexOfCode(code: string): number {
  return WEEKDAY_KEYS.indexOf(code as WeekdayKey);
}

/** 'YYYY-MM-DD' → 「9/14(月)」。 */
export function formatDateLabel(dateIso: string): string {
  const [, m, d] = dateIso.split('-');
  const wd = WEEKDAY_LABELS_JA[weekdayCodeOf(weekdayOfIso(dateIso))];
  return `${Number.parseInt(m ?? '0', 10)}/${Number.parseInt(d ?? '0', 10)}(${wd})`;
}

// ───────────────────────────────────────────────────────────────────────────
// 提案リクエスト
// ───────────────────────────────────────────────────────────────────────────

/**
 * `POST /schedule/v2/propose-slots` のリクエストを組む (§3-3 ④)。
 *
 * `time_type='固定'` + `preferred_start` で「その時刻ちょうどに入る枠」だけを
 * 返させ、`preferred_weekdays` にその週で選んだ曜日を並べる。
 * `office_ids` は必ず渡す（空にすると全拠点になる・§8）。
 */
export function buildProposeRequest(args: {
  patient: AddVisitProposePatient;
  isoYear: number;
  isoWeek: number;
  weekdays: number[];
  startHM: string;
  minutes: number;
  officeIds: string[];
}): ProposeSlotsRequest {
  const weekdays = Array.from(new Set(args.weekdays)).sort((a, b) => a - b);
  return {
    lat: args.patient.lat,
    lng: args.patient.lng,
    service_minutes: args.minutes,
    time_type: '固定',
    preferred_start: args.startHM,
    preferred_weekdays: weekdays.map(weekdayCodeOf),
    requires_multiple_staff: args.patient.requires_multiple_staff,
    sex_restriction: args.patient.sex_restriction,
    iso_year: args.isoYear,
    iso_week: args.isoWeek,
    office_ids: args.officeIds,
    existing_patient_id: args.patient.id,
    limit: 50,
    include_overcapacity: true,
  };
}

/** slot の曜日 (0=月..6=日)。`weekday_code` を正とし、無ければ `weekday`。 */
function slotWeekday(slot: ProposeSlotItem): number {
  const byCode = weekdayIndexOfCode(slot.weekday_code);
  return byCode >= 0 ? byCode : slot.weekday;
}

/**
 * 週まとめで返ってきた slots を日付ごとに振り分ける（曜日で対応付け）。
 *
 * グループ内の全日付がキーになる（候補 0 件の日は空配列）。
 *
 * **並びは BE のランキングをそのまま尊重する**（`score` で並べ替えない。
 * ソルバは score 以外の要素も込みで順位を決めているため、FE で並べ替えると
 * 他の提案画面と食い違う）。唯一の並べ替えは **定員超 (`overcapacity`) を
 * 後ろへ回す安定分割** だけ（方式b: 超過候補は通常候補の後）。
 */
export function mapSlotsToDates(
  slots: ProposeSlotItem[],
  group: Pick<AddVisitDateGroup, 'dates'>,
): Map<string, ProposeSlotItem[]> {
  const byWeekday = new Map<number, ProposeSlotItem[]>();
  slots.forEach((slot) => {
    const wd = slotWeekday(slot);
    const bucket = byWeekday.get(wd);
    if (bucket) bucket.push(slot);
    else byWeekday.set(wd, [slot]);
  });
  const out = new Map<string, ProposeSlotItem[]>();
  for (const date of group.dates) {
    const bucket = byWeekday.get(weekdayOfIso(date)) ?? [];
    out.set(date, [
      ...bucket.filter((s) => !s.overcapacity),
      ...bucket.filter((s) => s.overcapacity),
    ]);
  }
  return out;
}

/** M コース (担当なし) のコードか。`M` / `M2`..`M9` (溢れ) を含む。 */
export function isMCourseCode(code: string | null | undefined): boolean {
  return /^M\d*$/.test((code ?? '').trim().toUpperCase());
}

/**
 * 候補 0 件の日の代表理由コードを選ぶ。
 *
 * BE `propose_slots_service._EXCLUSION_REASON_PRIORITY` と同じ優先度で、
 * その曜日の `excluded_summary` から 1 つ選ぶ。該当が無ければ null。
 */
export function pickExcludedReason(summary: ExcludedSummaryItem[], weekday: number): string | null {
  const mine = summary.filter((s) => s.weekday === weekday);
  if (mine.length === 0) return null;
  for (const reason of EXCLUSION_REASON_PRIORITY) {
    if (mine.some((s) => s.reason === reason)) return reason;
  }
  return mine[0]?.reason ?? null;
}

// ───────────────────────────────────────────────────────────────────────────
// 反映先 (b) の「動かす元」
// ───────────────────────────────────────────────────────────────────────────

function daysBetween(aIso: string, bIso: string): number {
  const a = parseIsoDate(aIso).getTime();
  const b = parseIsoDate(bIso).getTime();
  return Math.round((a - b) / 86_400_000);
}

/**
 * 動かす元の既定 (PO 決定 9): **同じ曜日 → 無ければ最も近い日付**。
 * 同距離なら早い日付、さらに同じなら早い開始時刻。候補が無ければ null。
 *
 * 青ピン (`week_pinned`) の除外は呼び出し側で行う（BE 422 と一致・§5）。
 */
export function pickDefaultSourceVisit(
  candidates: VisitLite[],
  targetDate: string,
): VisitLite | null {
  if (candidates.length === 0) return null;
  const targetWeekday = weekdayOfIso(targetDate);
  const sameWeekday = candidates.filter((v) => weekdayOfIso(v.visit_date) === targetWeekday);
  const pool = sameWeekday.length > 0 ? sameWeekday : candidates;
  const sorted = [...pool].sort((a, b) => {
    const da = Math.abs(daysBetween(a.visit_date, targetDate));
    const db = Math.abs(daysBetween(b.visit_date, targetDate));
    if (da !== db) return da - db;
    if (a.visit_date !== b.visit_date) return a.visit_date < b.visit_date ? -1 : 1;
    if (a.start_time !== b.start_time) return a.start_time < b.start_time ? -1 : 1;
    return 0;
  });
  return sorted[0] ?? null;
}

/**
 * 選んだ日付すべてに「動かす元」を **1 件ずつ** 割り当てる (§3-3 ⑤(b))。
 *
 * 1 件の訪問を 2 つの日付の元にはできない（同じ訪問を 2 回動かすことになる）。
 * そこで日付を昇順に見て、使った訪問はプールから外す。
 *   1. 手動指定 (`overrides`) を先に確保する（先に来た日付が勝つ）。
 *   2. 残りの日付を PO 決定 9 の既定規則（同曜日 → 最も近い日付）で埋める。
 *   3. プールが尽きた日付は null = 呼び出し側で scope 'new' に落ちる。
 */
export function assignSourceVisits(args: {
  dates: string[];
  /** その日付が属する週の「動かせる」訪問 (青ピン等は除外済み)。 */
  candidatesByDate: (date: string) => VisitLite[];
  /** 手動で選んだ元 (date → visit id)。 */
  overrides?: Record<string, string>;
}): Map<string, VisitLite | null> {
  const ordered = Array.from(new Set(args.dates)).sort();
  const overrides = args.overrides ?? {};
  const used = new Set<string>();
  const out = new Map<string, VisitLite | null>();

  for (const date of ordered) {
    const id = overrides[date];
    if (!id || used.has(id)) continue;
    const hit = args.candidatesByDate(date).find((v) => v.id === id);
    if (!hit) continue;
    used.add(hit.id);
    out.set(date, hit);
  }
  for (const date of ordered) {
    if (out.has(date)) continue;
    const pool = args.candidatesByDate(date).filter((v) => !used.has(v.id));
    const picked = pickDefaultSourceVisit(pool, date);
    if (picked) used.add(picked.id);
    out.set(date, picked);
  }
  return out;
}

/**
 * (a)「型も変える」を選べるか (§3-3 ⑤)。
 * 型は曜日単位なので、複数日付の組み合わせとは矛盾する → 日付 1 つのときだけ。
 */
export function canChoosePatternScope(dates: string[]): boolean {
  return new Set(dates).size === 1;
}

// ───────────────────────────────────────────────────────────────────────────
// 所要時間 / 理由ラベル
// ───────────────────────────────────────────────────────────────────────────

/**
 * 所要時間の選択肢 (PO 決定 12): **5 分刻み・15〜120 分**。
 * 患者の基本時間が 5 分刻み外 (例 35 は刻み内・38 等) のときは選択肢に加える。
 */
export function durationOptions(baseMinutes?: number | null): number[] {
  const options: number[] = [];
  for (let m = 15; m <= 120; m += 5) options.push(m);
  if (
    typeof baseMinutes === 'number' &&
    Number.isFinite(baseMinutes) &&
    baseMinutes > 0 &&
    !options.includes(baseMinutes)
  ) {
    options.push(baseMinutes);
    options.sort((a, b) => a - b);
  }
  return options;
}

/**
 * `excluded_summary[].reason` の優先度 (BE
 * `propose_slots_service._EXCLUSION_REASON_PRIORITY` と同じ順)。
 */
export const EXCLUSION_REASON_PRIORITY = [
  'capacity_full',
  'pair_blocked',
  'travel_shortage',
  'lunch_window',
  'no_pair_slot',
  'no_gap',
] as const;

/**
 * 除外理由コードの日本語ラベル。
 *
 * `lib/queries/fieldBoard.ts` の `proposeWarningLabel` は **warnings** の語彙で、
 * こちらの `excluded_summary` とは別語彙のため独立して持つ（§4 の 2 点目）。
 * 未知コードはそのまま返す（寛容表示）。
 */
const EXCLUDED_REASON_LABEL_JA: Record<string, string> = {
  capacity_full: '定員いっぱい',
  pair_blocked: '同住所ペアの枠が取れない',
  travel_shortage: '移動時間が足りない',
  lunch_window: '昼休みと重なる',
  no_pair_slot: '2名体制の相方枠が無い',
  no_gap: '空き時間が無い',
  course_closed: 'コースが稼働していない',
};

export function excludedReasonLabel(code: string): string {
  return EXCLUDED_REASON_LABEL_JA[code] ?? code;
}
