/**
 * addVisitExecutor — 「＋訪問（任意日付の訪問追加）」の実行オーケストレータ。
 *
 * 正典 = `docs/plans/add-visit-anywhere-design.md`（§2-2 書き込み API・§3-3 ⑤・§8）。
 *
 * `AddVisitAnywhereDialog` が組み立てた `AddVisitPlan` を **日付順に 1 件ずつ**
 * 実行する。React も TanStack Query も参照しない純関数で、API 呼び出しは
 * `deps` として注入される（テストは fake deps を渡すだけで済む）。
 *
 * 設計の要点:
 *   - 反映先ごとに叩く API が違う (§2-2):
 *       'new'     → `POST /schedule/place-and-fix` (`fix_pattern=false`)
 *                   コース未解決 (臨) のときだけ `POST /visits`
 *       'week'    → `POST /schedule/v2/visit-move-week-only`
 *       'pattern' → `PUT /patients/{id}/fixed-visits` (`pattern_and_week`)
 *   - **1 件でも失敗したらその時点で止める** (§3-3 ⑤ 末尾)。成功分 (`done`)・
 *     失敗 (`failed`)・未実行 (`skipped`) を返し、呼び出し側が結果画面に並べる。
 *   - NG スタッフ / 性別制限の 422 (`constraint_confirmation_required`) は
 *     `failed.kind='constraint'` として **区別して** 返す。呼び出し側は確認
 *     ダイアログを通してから `opts.startIndex = failed.index` +
 *     `opts.acknowledge = true` で再実行する (§3-3 の acknowledge 再送)。
 */
import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { parseConstraintConfirmationDetail } from '@/lib/schemas/patient_ng_staff';
import type { PlaceAndFixRequest, PlaceAndFixResponse } from '@/lib/schemas/v2/place_and_fix';
import type {
  PatientFixedVisitsBulkPut,
  PatientFixedVisitV2Base,
  PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';
import type { VisitCreate } from '@/lib/schemas/visit';
import type {
  VisitMoveWeekOnlyRequest,
  VisitMoveWeekOnlyResponse,
} from '@/lib/queries/visitMoveWeekOnly';
import { fmtHM, parseHM } from '@/lib/scheduling/freeGaps';

import { isoWeekOfDate, type AddVisitPlan, type AddVisitPlanItem } from './addVisitPlan';

// ───────────────────────────────────────────────────────────────────────────
// 型
// ───────────────────────────────────────────────────────────────────────────

/** 実際に叩いた API の種類 (結果画面の文言の元)。 */
export type AddVisitExecKind =
  /** place-and-fix `fix_pattern=false` で 1 件足した。 */
  | 'new'
  /** POST /visits (コース未解決 = 臨) で 1 件足した。 */
  | 'new_manual'
  /** visit-move-week-only でその週の既存訪問を動かした。 */
  | 'week'
  /** PUT fixed-visits で型を変えた (+ その週を作り直した)。 */
  | 'pattern';

export interface AddVisitExecDone {
  item: AddVisitPlanItem;
  kind: AddVisitExecKind;
  /** 作成/更新できた訪問 id。move は BE が件数しか返さないため空 (§ 下記注記)。 */
  visitIds?: string[];
}

export interface AddVisitExecFailure {
  item: AddVisitPlanItem;
  /**
   * 'constraint' = NG スタッフ / 性別制限の 422。確認して acknowledge 再送できる。
   * 'error'      = それ以外 (再送しても同じ)。
   */
  kind: 'constraint' | 'error';
  /** `plan.items` を日付順に並べ直したときの位置 (= 再実行の `startIndex`)。 */
  index: number;
  error: unknown;
  /** 画面に出せる日本語メッセージ。 */
  message: string;
  /** kind='constraint' のときの警告一覧 (確認ダイアログの材料)。 */
  detail: ReturnType<typeof parseConstraintConfirmationDetail>;
}

export interface AddVisitExecResult {
  /** この実行で成功した分 (日付順)。 */
  done: AddVisitExecDone[];
  /** 最初に失敗した 1 件。無ければ undefined。 */
  failed?: AddVisitExecFailure;
  /** 失敗で止まったため実行しなかった分。 */
  skipped: AddVisitPlanItem[];
  /** 日付順に並べ直した items (呼び出し側が index を解釈するための正)。 */
  ordered: AddVisitPlanItem[];
}

export interface AddVisitExecDeps {
  placeAndFix: (req: PlaceAndFixRequest) => Promise<PlaceAndFixResponse>;
  moveWeekOnly: (req: VisitMoveWeekOnlyRequest) => Promise<VisitMoveWeekOnlyResponse>;
  putFixedVisits: (patientId: string, body: PatientFixedVisitsBulkPut) => Promise<unknown>;
  getFixedVisits: (patientId: string) => Promise<PatientFixedVisitV2Read[]>;
  createVisit: (body: VisitCreate) => Promise<{ id: string }>;
  patchVisitNote: (visitId: string, note: string) => Promise<unknown>;
}

export interface AddVisitExecOptions {
  /** 1 ユーザー操作 = 1 UUID (op-log のグループ化・undo の単位)。 */
  opGroupId: string;
  /**
   * 確認ダイアログを通した 1 件だけを acknowledge で通す (§3-3 NG/性別)。
   * **その index の項目にだけ** `acknowledge_constraint_warnings` を付ける
   * （後続の別の日まで黙って通してしまわないように）。
   */
  acknowledgeIndex?: number;
  /** ここから再開する (前回 `failed.index` を渡す)。既定 0。 */
  startIndex?: number;
  onProgress?: (index: number, total: number) => void;
}

/** M 配置理由を `visits.note` に残すときの接頭辞 (PO 決定 10)。 */
export const M_REASON_NOTE_PREFIX = 'M配置理由: ';

/**
 * `visit-move-week-only` が 200 + `visits_moved: 0` を返したとき。
 * BE は「(patient, old_date, old_start) に一致する訪問が無い」を **エラーにしない**
 * ため、ここで失敗に倒さないと「動かしたつもり」で結果画面が緑になる。
 */
export const MOVE_SOURCE_MISSING_MESSAGE =
  '移動元の予定が見つかりませんでした（すでに動かされた可能性があります）';

/** 移動 (b) はコースを必ず付け替える。据え置き移動は旧曜日に残るので送らない。 */
export const MOVE_COURSE_UNRESOLVED_MESSAGE = '移動先のコースを特定できません';

/** 動かす元が対象週の外 = 計画の組み立てがずれている (L1 ガード)。 */
export const MOVE_SOURCE_WRONG_WEEK_MESSAGE = '移動元の予定が対象週にありません';

// ───────────────────────────────────────────────────────────────────────────
// 内部ヘルパー
// ───────────────────────────────────────────────────────────────────────────

/** 日付 (同日は開始時刻) の昇順。実行順の正。 */
export function orderPlanItems(items: AddVisitPlanItem[]): AddVisitPlanItem[] {
  return [...items].sort((a, b) => {
    if (a.date !== b.date) return a.date < b.date ? -1 : 1;
    if (a.startHM !== b.startHM) return a.startHM < b.startHM ? -1 : 1;
    return 0;
  });
}

function endHMOf(startHM: string, minutes: number): string {
  const start = parseHM(startHM);
  // 時刻が読めない計画は組み立て側で弾かれている。保険として 09:00 起点に落とす。
  return fmtHM((start ?? 9 * 60) + minutes);
}

function toFailure(item: AddVisitPlanItem, index: number, err: unknown): AddVisitExecFailure {
  const detail =
    err instanceof ApiError && err.status === 422
      ? parseConstraintConfirmationDetail(err.body)
      : null;
  return {
    item,
    index,
    error: err,
    detail,
    kind: detail ? 'constraint' : 'error',
    message: apiErrorMessage(err),
  };
}

/** place-and-fix レスポンスから訪問 id を拾う (配列形式が正・単数は後方互換)。 */
function visitIdsOf(res: PlaceAndFixResponse): string[] {
  const ids = (res.visits ?? []).map((v) => v.id).filter((id): id is string => Boolean(id));
  if (ids.length > 0) return ids;
  return res.visit?.id ? [res.visit.id] : [];
}

/**
 * 既存の固定訪問行に「この曜日の slot 0」を差し替えた items を組む (§3-3 ⑤(a))。
 *
 * PUT は「当該 (patient, mode) を全削除 → INSERT」の冪等上書きなので、
 * **既存行をすべて送り直す**必要がある。読んだ行の意味のあるフィールドだけを
 * `PatientFixedVisitV2Base` へ写し、対象曜日 slot 0 だけを置換 (無ければ追加) する。
 */
export function buildPatternItems(
  existing: PatientFixedVisitV2Read[],
  replacement: PatientFixedVisitV2Base,
): PatientFixedVisitV2Base[] {
  const slot = replacement.slot_index ?? 0;
  const isTarget = (row: PatientFixedVisitV2Read) =>
    row.weekday === replacement.weekday && (row.slot_index ?? 0) === slot;
  const kept: PatientFixedVisitV2Base[] = existing
    .filter((row) => !isTarget(row))
    .map((row) => ({
      weekday: row.weekday,
      start_time: row.start_time.slice(0, 5),
      duration_min: row.duration_min,
      course_template_id: row.course_template_id ?? null,
      sub_office_id: row.sub_office_id ?? null,
      slot_index: row.slot_index ?? 0,
      is_pinned: row.is_pinned ?? false,
      movability: row.movability ?? 'unknown',
    }));
  // 時刻とコースだけを差し替える操作なので、**その枠の可動域は引き継ぐ**
  // (赤ピン = movability 'locked' の枠を黙って自由枠に落とさない・M2)。
  // `is_pinned` は movability のミラー (pin-and-movability-spec.md)。
  const replaced = existing.find(isTarget) ?? null;
  const movability = replaced?.movability ?? replacement.movability ?? 'unknown';
  const next: PatientFixedVisitV2Base = {
    ...replacement,
    movability,
    is_pinned: movability === 'locked',
  };
  return [...kept, next].sort(
    (a, b) => a.weekday - b.weekday || (a.slot_index ?? 0) - (b.slot_index ?? 0),
  );
}

// ───────────────────────────────────────────────────────────────────────────
// 本体
// ───────────────────────────────────────────────────────────────────────────

/**
 * 計画を日付順に実行する。最初の失敗で止める (§3-3 ⑤)。
 *
 * `opts.startIndex` を渡すと途中から再開する (constraint 確認後の acknowledge
 * 再送)。返す `done` は **この呼び出しで成功した分だけ** なので、再開した
 * 呼び出し側は前回の `done` と足し合わせて結果画面に出すこと。
 */
export async function executeAddVisitPlan(
  plan: AddVisitPlan,
  deps: AddVisitExecDeps,
  opts: AddVisitExecOptions,
): Promise<AddVisitExecResult> {
  const ordered = orderPlanItems(plan.items);
  const start = Math.max(0, opts.startIndex ?? 0);
  const done: AddVisitExecDone[] = [];

  for (let i = start; i < ordered.length; i += 1) {
    const item = ordered[i];
    if (!item) continue;
    opts.onProgress?.(i, ordered.length);
    try {
      const result = await executeOne(
        plan.patientId,
        item,
        deps,
        opts,
        opts.acknowledgeIndex === i,
      );
      done.push(result);
    } catch (err) {
      return {
        done,
        failed: toFailure(item, i, err),
        skipped: ordered.slice(i + 1),
        ordered,
      };
    }
  }
  return { done, skipped: [], ordered };
}

async function executeOne(
  patientId: string,
  item: AddVisitPlanItem,
  deps: AddVisitExecDeps,
  opts: AddVisitExecOptions,
  /** この 1 件だけ確認済み (M1)。 */
  acknowledge: boolean,
): Promise<AddVisitExecDone> {
  // (a) 型も変える。日付 1 つのときだけモーダルが出す scope。
  if (item.scope === 'pattern') {
    const existing = await deps.getFixedVisits(patientId);
    const items = buildPatternItems(existing, {
      weekday: item.weekday,
      start_time: item.startHM,
      duration_min: item.minutes,
      course_template_id: item.courseTemplateId,
      // 他拠点 (要確認) を選んだときだけサブ拠点を刻む (§5・Phase E-5)。
      sub_office_id: item.isOtherOffice ? item.officeId : null,
      slot_index: 0,
      is_pinned: false,
      movability: 'unknown',
    });
    await deps.putFixedVisits(patientId, {
      mode: 'normal',
      items,
      // 欠陥 6 の再発防止: 「今日の週」ではなく**選んだ日付の週**を作り直す。
      change_scope: 'pattern_and_week',
      iso_year: item.isoYear,
      iso_week: item.isoWeek,
      ...(acknowledge ? { acknowledge_constraint_warnings: true } : {}),
    });
    return { item, kind: 'pattern' };
  }

  // (b) その週の既存訪問を動かす。元が無ければ (c) と同じ扱い。
  if (item.scope === 'week' && item.sourceVisit) {
    const src = item.sourceVisit;
    // L1: 元が対象週の外にあると、BE は (old_weekday, old_start) で別の週の
    //   訪問を探しに行って空振りする。組み立て側の取り違えをここで止める。
    const srcWeek = isoWeekOfDate(src.visit_date);
    if (srcWeek.isoYear !== item.isoYear || srcWeek.isoWeek !== item.isoWeek) {
      throw new Error(MOVE_SOURCE_WRONG_WEEK_MESSAGE);
    }
    // M4: コース据え置きの移動は BE が旧曜日のコース ID を残す (§1 欠陥 3)。
    //   移動先コースが決まらないなら送らない。
    if (!item.courseTemplateId) throw new Error(MOVE_COURSE_UNRESOLVED_MESSAGE);
    const res = await deps.moveWeekOnly({
      iso_year: item.isoYear,
      iso_week: item.isoWeek,
      patient_id: patientId,
      old_weekday: srcWeek.weekday,
      old_start_time: src.start_time.slice(0, 5),
      new_weekday: item.weekday,
      new_start_time: item.startHM,
      new_course_template_id: item.courseTemplateId,
      op_group_id: opts.opGroupId,
      ...(acknowledge ? { acknowledge_constraint_warnings: true } : {}),
    });
    // C1: BE は元が見つからなくても 200 + visits_moved:0 を返す。成功にしない。
    if ((res?.visits_moved ?? 0) === 0) throw new Error(MOVE_SOURCE_MISSING_MESSAGE);
    // NOTE: レスポンスは件数だけで動いた訪問の id が分からない。M 配置理由
    //   (PO 決定 10) の note は id 無しには PATCH できないので移動では残さない。
    return { item, kind: 'week', visitIds: [] };
  }

  // (c) 新しく 1 件追加する。コースが決まっていれば place-and-fix (§8)。
  if (item.courseTemplateId) {
    const partner = item.staffCount === 2 ? item.partnerCourseTemplateId : null;
    const res = await deps.placeAndFix({
      patient_id: patientId,
      ...(item.staffCount === 2 && partner
        ? { course_template_ids: [item.courseTemplateId, partner] }
        : { course_template_id: item.courseTemplateId }),
      iso_year: item.isoYear,
      iso_week: item.isoWeek,
      weekday: item.weekday,
      start_time: item.startHM,
      duration_min: item.minutes,
      staff_count: item.staffCount,
      // 型は変えない = 今週だけ (source='manual_week')。
      fix_pattern: false,
      op_group_id: opts.opGroupId,
      ...(acknowledge ? { acknowledge_constraint_warnings: true } : {}),
    });
    const visitIds = visitIdsOf(res);
    await patchReasonNotes(item, visitIds, deps);
    return { item, kind: 'new', visitIds };
  }

  // コース未解決 (臨) — place-and-fix は course_template_id 必須なので POST /visits。
  // L4: 理由は作成時に載せる (作ってから PATCH する往復を作らない)。
  const note = reasonNoteOf(item);
  const created = await deps.createVisit({
    patient_id: patientId,
    visit_date: item.date,
    start_time: item.startHM,
    end_time: endHMOf(item.startHM, item.minutes),
    type: 'regular',
    status: 'planned',
    // 今週だけの追加 (PFV 不変・週生成でも保護される・§2-3)。
    source: 'manual_week',
    course_id: null,
    primary_staff_id: null,
    ...(note ? { note } : {}),
  });
  return { item, kind: 'new_manual', visitIds: created.id ? [created.id] : [] };
}

/** M を受け皿にした日の `visits.note` (PO 決定 10)。理由が無ければ null。 */
function reasonNoteOf(item: AddVisitPlanItem): string | null {
  const reason = (item.reason ?? '').trim();
  if (!item.isM || reason === '') return null;
  return `${M_REASON_NOTE_PREFIX}${reason}`;
}

/** place-and-fix で作った訪問へ理由を後付けする (作成 API に note が無いため)。 */
async function patchReasonNotes(
  item: AddVisitPlanItem,
  visitIds: string[],
  deps: AddVisitExecDeps,
): Promise<void> {
  const note = reasonNoteOf(item);
  if (!note) return;
  for (const id of visitIds) {
    await deps.patchVisitNote(id, note);
  }
}
