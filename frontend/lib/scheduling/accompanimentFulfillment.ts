/**
 * 新人同行による「2人目 (slot1)」充足判定。
 *
 * 複数名対応患者 (`requires_multiple_staff=true`) は保留プールで slot0/slot1 の
 * 2 カードに割れる (PoolPanel `computeCardSlots`)。片側 (slot0) のみ配置済みだと
 * 「②カード (2人目未配置)」が残る。この 2 人目を新人同行 (trainee accompaniment)
 * で賄っている場合、②カードはプールから消える (= 実質配置済み扱い) べきである。
 *
 * ここではその充足判定を**純関数**として切り出す (単体テスト可能・UI非依存)。
 *
 * ── 判定粒度の設計判断 ─────────────────────────────────────────────
 *   保留プールのカード粒度は **患者単位** である (PoolGroupedByWeekday は患者ごと
 *   に 1 カード、`assignedSlotsByPatient` は patient_id キー)。曜日単位のカードは
 *   存在しないため、充足判定も患者単位で行う:
 *     「当該患者のその週の配置済み訪問がすべて同行つきなら slot1 を充足扱い」。
 *   これは既存の slot モデル (slot1 が埋まれば②カード消滅) と一貫する。
 *   ②カードが出るのは has0 && !has1 のときのみで、その場合すべての配置済み訪問は
 *   単独 (ペアなし) なので「全訪問が同行つき」= 2 人目全充足を意味する。
 *
 * ── 同行つき判定 (§7.2 と同じ材料) ────────────────────────────────
 *   1 訪問について、以下のいずれかで「同行つき」とみなす:
 *     - visit 直リンク一致 (target_type='visit' の visit.id)
 *     - コースリンク一致 (target_type='course' の course.id === 訪問の course_id)
 *     - course.id が引けない場合のフォールバック: template_id + weekday 一致
 *
 * ── 未確定分の扱い ────────────────────────────────────────────────
 *   判定材料は **保存済みリンク (サーバデータ) のみ**。同行モード中の未確定な
 *   選択は反映しない (確定 PUT 後の invalidate で再取得され充足判定が更新される)。
 */
import type { TraineeAccompanimentItem } from '@/lib/schemas/trainee_accompaniment';
import type { SlotIndex } from '@/lib/schemas/v2/patient_fixed_visit';

/** 充足判定に必要な最小の訪問情報。 */
export interface FulfillmentVisit {
  /** visit id (visit 直リンク照合用)。 */
  id: string;
  /** 配置先コースインスタンス id (コースリンク照合用)。未配置なら null。 */
  courseId: string | null;
  /** コーステンプレート id (course.id フォールバック照合用)。 */
  courseTemplateId: string | null;
  /** 0=月..6=日 (course.id フォールバック照合用)。 */
  weekday: number | null;
}

/** 保存済み同行リンクの索引 (充足判定用・O(1) 照合)。 */
export interface AccompanimentLinkIndex {
  /** target_type='visit' のリンク先 visit.id 集合。 */
  visitIds: Set<string>;
  /** target_type='course' のリンク先 course.id 集合。 */
  courseIds: Set<string>;
  /** course リンクの `${template_id}:${weekday}` 集合 (course.id フォールバック)。 */
  courseTemplateWeekdays: Set<string>;
}

/** 同行リンク配列から照合用の索引を組む。 */
export function buildAccompanimentLinkIndex(
  items: readonly TraineeAccompanimentItem[] | null | undefined,
): AccompanimentLinkIndex {
  const visitIds = new Set<string>();
  const courseIds = new Set<string>();
  const courseTemplateWeekdays = new Set<string>();
  for (const it of items ?? []) {
    if (it.target_type === 'visit' && it.visit?.id) {
      visitIds.add(it.visit.id);
    } else if (it.target_type === 'course' && it.course) {
      if (it.course.id) courseIds.add(it.course.id);
      if (it.course.template_id) {
        courseTemplateWeekdays.add(`${it.course.template_id}:${it.course.weekday}`);
      }
    }
  }
  return { visitIds, courseIds, courseTemplateWeekdays };
}

/** 索引が空 (= 保存済みリンクが 1 件も無い) か。 */
export function isEmptyAccompanimentLinkIndex(index: AccompanimentLinkIndex): boolean {
  return (
    index.visitIds.size === 0 &&
    index.courseIds.size === 0 &&
    index.courseTemplateWeekdays.size === 0
  );
}

/** 1 訪問が同行つきか (visit 直リンク or コースリンク or template+weekday)。 */
export function isVisitAccompanied(
  visit: FulfillmentVisit,
  index: AccompanimentLinkIndex,
): boolean {
  if (visit.id && index.visitIds.has(visit.id)) return true;
  if (visit.courseId && index.courseIds.has(visit.courseId)) return true;
  if (
    visit.courseTemplateId != null &&
    visit.weekday != null &&
    index.courseTemplateWeekdays.has(`${visit.courseTemplateId}:${visit.weekday}`)
  ) {
    return true;
  }
  return false;
}

/**
 * 複数名対応患者の「2人目 (slot1)」が新人同行で充足しているか (患者単位)。
 *
 * - placedVisits = 当該患者のその週の配置済み訪問。
 * - すべての配置済み訪問が同行つきなら true (= slot1 充足扱い → ②カードを消す)。
 * - 配置済み訪問が 0 件なら false (充足しようがない)。
 */
export function isSecondStaffFulfilledByAccompaniment(
  placedVisits: readonly FulfillmentVisit[],
  index: AccompanimentLinkIndex,
): boolean {
  if (placedVisits.length === 0) return false;
  return placedVisits.every((v) => isVisitAccompanied(v, index));
}

/**
 * `assignedSlotsByPatient` に「同行で充足した slot1」を上乗せしたマップを返す。
 *
 * 複数名対応患者のうち、②カードが出る条件 (has0 && !has1) を満たし、かつ配置済み
 * 訪問がすべて同行つきの患者について slot 1 を追加する。これにより PoolPanel の
 * `computeCardSlots` が両 slot 埋まりと判定し、②カードがプールから消える。
 *
 * 何も追加しない場合は `base` をそのまま返す (参照同一性を保ちre-render抑制)。
 */
/**
 * 同行モード中に②カードをクリックしたときの「一括トグル計画」を純関数で求める。
 *
 * - `selectableVisitIds` = トグル対象の訪問 (既にコース選択に内包される分は
 *   呼出側で除外済みとする)。
 * - すべて選択済みなら「全解除」、そうでなければ「全選択」に倒す
 *   (タイムライン選択と同じ「全選択 / 全解除」トグル言語)。
 *
 * 戻り値:
 *   - `toggleIds`: 目標状態にするために `toggleVisit` を呼ぶべき訪問 id。
 *   - `allSelected`: トグル前に全選択済みだったか (= ring 表示用)。
 */
export function planSecondStaffToggle(
  selectableVisitIds: readonly string[],
  isSelected: (visitId: string) => boolean,
): { toggleIds: string[]; allSelected: boolean } {
  const allSelected =
    selectableVisitIds.length > 0 && selectableVisitIds.every((id) => isSelected(id));
  const targetSelect = !allSelected;
  const toggleIds = selectableVisitIds.filter((id) => isSelected(id) !== targetSelect);
  return { toggleIds, allSelected };
}

export function augmentAssignedSlotsWithAccompaniment(
  base: Map<string, Set<SlotIndex>>,
  multiStaffPatientIds: readonly string[],
  placedVisitsByPatient: Map<string, FulfillmentVisit[]>,
  index: AccompanimentLinkIndex,
): Map<string, Set<SlotIndex>> {
  if (isEmptyAccompanimentLinkIndex(index)) return base;
  let next: Map<string, Set<SlotIndex>> | null = null;
  for (const pid of multiStaffPatientIds) {
    const slots = base.get(pid);
    const has0 = slots?.has(0) ?? false;
    const has1 = slots?.has(1) ?? false;
    // ②カードが出るのは has0 && !has1 のときだけ。それ以外は触らない。
    if (!has0 || has1) continue;
    const placed = placedVisitsByPatient.get(pid) ?? [];
    if (!isSecondStaffFulfilledByAccompaniment(placed, index)) continue;
    if (!next) next = new Map(base);
    const s = new Set<SlotIndex>(slots ?? []);
    s.add(1);
    next.set(pid, s);
  }
  return next ?? base;
}
