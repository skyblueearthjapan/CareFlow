/**
 * _proposeSlotUtils — propose-slots 候補の採用 (fixed-visits 確定) 共有ユーティリティ.
 *
 * ``ProposeNewModal`` と ``PoolCandidateList`` で重複していた純関数を集約し、
 * 片方だけ修正されてズレる (shotgun surgery) のを防ぐ. UI/フックは含まない純ロジックのみ.
 *   - slotKey / slotDurationMin: 候補の一意キー / 所要分.
 *   - buildCourseTemplateIdResolver: (office_id, course_code) → course_template_id 解決.
 *   - proposedSlotToFixedVisitItem: 採用枠 → PFV(normal) item.
 *   - existingFixedVisitToItem: 既存 PFV → bulk-put item (保持用).
 *   - mergeAdoptedIntoNormalFixedVisits: 採用枠を既存 normal 枠にマージ (採用曜日の
 *     slot_index=0 を置換, 他曜日/他slotは保持) — 他曜日の固定枠を消さない安全な確定.
 *   - sortFixedVisitItems: (weekday, start_time) 昇順ソート.
 */
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type {
  PatientFixedVisitV2Base,
  PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';
import type { ProposeSlotItem } from '@/lib/schemas/v2/propose_slots';

/** 候補スロットの一意キー (office × weekday × course × start). */
export const slotKey = (s: ProposeSlotItem): string =>
  `${s.office_id}-${s.weekday}-${s.course_code}-${s.start_time}`;

/** 枠の start_time〜end_time から所要分を算出. パース不能なら null. */
export function slotDurationMin(s: ProposeSlotItem): number | null {
  const parse = (hm: string | null | undefined): number | null => {
    if (!hm) return null;
    const m = /^(\d{1,2}):(\d{2})/.exec(hm);
    if (!m) return null;
    return Number(m[1]) * 60 + Number(m[2]);
  };
  const a = parse(s.start_time);
  const b = parse(s.end_time);
  if (a === null || b === null || b <= a) return null;
  return b - a;
}

export type CourseTemplateIdResolver = (officeId: string, code: string) => string | null;

/**
 * 関係拠点の course-templates 群から (office_id, label[大文字]) → course_template_id の
 * 解決関数を組み立てる. ``templateLists`` は各拠点の取得結果 (useQueries の data 配列).
 */
export function buildCourseTemplateIdResolver(
  templateLists: ReadonlyArray<readonly CourseTemplateRead[] | undefined>,
): CourseTemplateIdResolver {
  const m = new Map<string, string>();
  for (const list of templateLists) {
    for (const t of list ?? []) {
      if (t.deleted_at) continue;
      m.set(`${t.office_id}:${(t.label ?? '').trim().toUpperCase()}`, t.id);
    }
  }
  return (officeId: string, code: string): string | null =>
    m.get(`${officeId}:${code.trim().toUpperCase()}`) ?? null;
}

/** 採用枠 1 件 → bulk PUT item (mode='normal'). course_template_id は解決できれば付与. */
export function proposedSlotToFixedVisitItem(
  s: ProposeSlotItem,
  resolveCourseTemplateId: CourseTemplateIdResolver,
  serviceFallbackMin: number,
): PatientFixedVisitV2Base {
  const duration = slotDurationMin(s) ?? serviceFallbackMin;
  const tplId = resolveCourseTemplateId(s.office_id, s.course_code);
  return {
    weekday: s.weekday,
    start_time: s.start_time,
    duration_min: duration,
    slot_index: 0,
    ...(tplId ? { course_template_id: tplId } : {}),
  };
}

/** 既存 normal 固定枠 1 件 → bulk PUT item (slot_index / course_template_id 等を引き継ぐ). */
export function existingFixedVisitToItem(v: PatientFixedVisitV2Read): PatientFixedVisitV2Base {
  return {
    weekday: v.weekday,
    start_time: v.start_time,
    duration_min: v.duration_min,
    slot_index: v.slot_index ?? 0,
    ...(v.course_template_id ? { course_template_id: v.course_template_id } : {}),
    ...(v.sub_office_id ? { sub_office_id: v.sub_office_id } : {}),
    ...(typeof v.is_pinned === 'boolean' ? { is_pinned: v.is_pinned } : {}),
  };
}

/** (weekday, start_time) 昇順ソート (新しい配列を返す). */
export function sortFixedVisitItems(items: PatientFixedVisitV2Base[]): PatientFixedVisitV2Base[] {
  return [...items].sort(
    (a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time),
  );
}

/**
 * 採用枠 (item 化済) を既存 normal 枠にマージする.
 *   - 採用曜日の slot_index=0 は採用枠で置換.
 *   - 採用しなかった曜日 / 同曜日の slot_index!=0 (2名体制相方等) は保持.
 * backend PUT /fixed-visits の全削除→INSERT で他曜日の枠が消えるのを防ぐ.
 */
export function mergeAdoptedIntoNormalFixedVisits(
  existing: PatientFixedVisitV2Read[],
  adoptedItems: PatientFixedVisitV2Base[],
): PatientFixedVisitV2Base[] {
  const adoptedWeekdays = new Set(adoptedItems.map((i) => i.weekday));
  const preserved = existing
    .filter((v) => !adoptedWeekdays.has(v.weekday) || (v.slot_index ?? 0) !== 0)
    .map(existingFixedVisitToItem);
  return sortFixedVisitItems([...preserved, ...adoptedItems]);
}
