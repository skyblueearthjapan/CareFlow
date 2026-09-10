/**
 * 訪問の「見せ方」判定 — 患者ステータス連動 Phase 3「表示の保険」
 * (design 2026-09-09 §3-4 / §6-C Q15)。
 *
 * PO 決定 (Q15): **残骸はバッジ付きで薄く表示・取消済み (status_cancel) は非表示**。
 *   - 連動処理が効いていれば非稼働患者の予定はゼロのはず。残っているなら不整合なので
 *     「入院中」バッジ + 薄色で PO に見せる (隠さない = 原則③)。
 *   - 連動で取り消した予定 (source='status_cancel') は既に「消えたもの」なので
 *     既定では出さない。トグル「非稼働を表示」で残骸点検用に打ち消し線つきで出せる。
 *
 * **日付条件 (PO フィードバック 2026-09-10)**: 「入院中」バッジはステータスを変えた日
 * (`patient_status_since` = `patients.status_changed_at` の JST 日付) **以降** の予定に
 * だけ出す。それ以前の日 = 実際に訪問した日なので従来表示 (`normal`)。`since` が
 * 未記録 (mig 0082 以前) なら **今日 (JST)** を起点にする。`visit_date` を渡さない
 * 呼び出しは従来どおり (日付条件なし) になるので、呼び出し側は必ず訪問日を詰める。
 *
 * 盤面 (日/週タイムライン・週リスト・職員スケジュール・盤面セル)・モニター・
 * モバイル・現場ボードが **同じ関数** を使うための単一ソース。コンポーネント側で
 * ステータス値を列挙しない (ラベルは `inactiveStatusLabel` が唯一の出所)。
 */
import { jstDateString } from '@/lib/format/patientStatus';
import { inactiveStatusLabel } from '@/lib/schemas/patient';
import { isStatusCancelledVisit } from '@/lib/schemas/v2/visit';

/** 判定に使う最小限の形 (visit DTO / 盤面用の表示型のどちらも満たす)。 */
export interface VisitVisibilityInput {
  source?: string | null;
  status?: string | null;
  /** 患者マスタの `patients.status` (BE が訪問 DTO に載せる・旧応答では欠落)。 */
  patient_status?: string | null;
  /**
   * 訪問日 `YYYY-MM-DD` (ISO 日時も可 = 先頭 10 文字を見る)。
   * **欠落したら従来どおり** (日付条件を課さない = バッジを出す)。呼び出し側は
   * DTO のフィールド名がまちまち (`visit_date` / `date` / `visitDate`) なので
   * ここへ詰め替える責任を持つ。
   */
  visit_date?: string | null;
  /**
   * 患者ステータスが今の値になった日 `YYYY-MM-DD` (JST・BE の
   * `patient_status_since` = `patients.status_changed_at`)。
   * 未記録 (mig 0082 以前) は null → 「今日 (JST)」に倒す。
   */
  patient_status_since?: string | null;
}

/** JST の今日 (`YYYY-MM-DD`)。BE の `today_jst` と同じ基準。 */
export function todayJstIso(now: Date = new Date()): string {
  return jstDateString(0, now);
}

/** `YYYY-MM-DD` / ISO 日時 → `YYYY-MM-DD` (比較用)。空・不正は null。 */
function isoDay(value: string | null | undefined): string | null {
  if (!value) return null;
  const day = value.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : null;
}

/**
 * 表示区分。
 *   hidden        … 描かない (既定の status_cancel)
 *   normal        … 従来どおり
 *   inactive      … 非稼働患者の予定が残っている = 不整合。バッジ + 薄色で見せる
 *   status_cancel … 連動で取消済み。トグル ON のときだけ打ち消し線 + バッジで見せる
 */
export type VisitDisplayKind = 'hidden' | 'normal' | 'inactive' | 'status_cancel';

/** 連動取消のバッジ文言 (「今週だけ取消」= manual_cancel の「取消」と区別する)。 */
export const STATUS_CANCEL_BADGE_LABEL = '取消（連動）';

/** 区分ごとの追加クラス (Tailwind)。'' = 従来どおり。 */
export const VISIT_DISPLAY_CLASS: Record<VisitDisplayKind, string> = {
  hidden: '',
  normal: '',
  inactive: 'opacity-60',
  status_cancel: 'line-through opacity-60',
};

/**
 * 患者が非稼働 (入院中・一時休止・解約済み・開始前) と **分かっている** 訪問か。
 * `patient_status` が無い (旧 BE / 部分 DTO) ときは false = 従来表示に倒す。
 */
export function isInactivePatientVisit(v: VisitVisibilityInput): boolean {
  return inactiveStatusLabel(v.patient_status) !== null;
}

/**
 * まだ「予定」として残っている訪問か。完了・不在・取消は残骸ではないので
 * バッジを出さない (過去の実績に「入院中」と書かない)。欠落は予定扱い (寛容)。
 */
function isPlannedLike(status: string | null | undefined): boolean {
  return status == null || status === '' || status === 'planned';
}

/**
 * バッジの起点日 **以降** の訪問か (PO フィードバック 2026-09-10)。
 *
 * 起点 = `patient_status_since` (ステータスを変えた日・JST)。未記録なら **今日**
 * (mig 0082 以前に変えた行。過去日に遡って「入院中」と書かないための保守的な既定)。
 * `visit_date` が無い入力は **従来どおり** true (日付条件を課さない)。
 */
function isOnOrAfterStatusSince(v: VisitVisibilityInput, today: string): boolean {
  const visitDay = isoDay(v.visit_date);
  if (visitDay === null) return true;
  const since = isoDay(v.patient_status_since) ?? today;
  return visitDay >= since;
}

export interface ClassifyVisitDisplayOptions {
  /** トグル「非稼働を表示」。true = 連動取消も残骸点検のために描く。 */
  showInactive?: boolean;
  /** 今日 (JST・`YYYY-MM-DD`)。テスト用の注入口。既定は `todayJstIso()`。 */
  today?: string;
}

/**
 * 1 訪問の表示区分を決める。既存の `isStatusCancelledVisit` フィルタの置き換え
 * (トグル OFF のときの挙動は完全に同じ = 連動取消だけが消える)。
 */
export function classifyVisitDisplay(
  v: VisitVisibilityInput,
  { showInactive = false, today }: ClassifyVisitDisplayOptions = {},
): VisitDisplayKind {
  if (isStatusCancelledVisit(v)) return showInactive ? 'status_cancel' : 'hidden';
  if (
    isInactivePatientVisit(v) &&
    isPlannedLike(v.status) &&
    isOnOrAfterStatusSince(v, today ?? todayJstIso())
  ) {
    return 'inactive';
  }
  return 'normal';
}

/** 盤面に描くか (= 従来の `!isStatusCancelledVisit(v)` と同値)。 */
export function isVisitVisible(
  v: VisitVisibilityInput,
  opts: ClassifyVisitDisplayOptions = {},
): boolean {
  return classifyVisitDisplay(v, opts) !== 'hidden';
}

/**
 * バッジ文言。'inactive' は患者ステータスのラベル (例「入院中」)、
 * 'status_cancel' は「取消（連動）」。それ以外は null (バッジを出さない)。
 */
export function visitDisplayBadgeLabel(
  v: VisitVisibilityInput,
  kind: VisitDisplayKind,
): string | null {
  if (kind === 'status_cancel') return STATUS_CANCEL_BADGE_LABEL;
  if (kind === 'inactive') return inactiveStatusLabel(v.patient_status);
  return null;
}
