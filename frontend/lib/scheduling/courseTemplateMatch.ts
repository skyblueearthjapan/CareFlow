/**
 * courseTemplateMatch — コースコード → コーステンプレートの解決。
 *
 * `CourseDayTablePanel.findCourseForTemplate`（template → 週次 course）の
 * **逆向き**。提案 API (`propose-slots`) が返す `course_code` から、書き込み API
 * (`place-and-fix` / `visit-move-week-only`) に渡す `course_template_id` を引く。
 *
 * 一致条件は本家と同じ規則・同じ優先順:
 *   1. exact: `template.label`(大文字) === `course_code`(大文字)
 *      例 label='A' & code='A' / label='M2' & code='M2'
 *   2. M 溢れ: code が `M2`..`M9` で専用 template が無いとき label='M' に流す
 *   2b. 臨時溢れ: code が `臨2`..`臨9` のとき先頭 '臨' の template に流す
 *   3. legacy 1 文字: code が 1 文字で template.label の先頭 1 文字と一致
 *      （旧運用: label='Aコース' → code='A'）
 *
 * 1 が 2/3 より先に当たること（専用 template があればそちらが勝つ）が肝心なので、
 * 「先に全 template を exact で走査 → 無ければ次の規則」という段階で書く。
 */

export interface CourseTemplateLite {
  id: string;
  label: string;
  office_id: string;
}

function labelUpper(t: CourseTemplateLite): string {
  return (t.label || '').trim().toUpperCase();
}

/**
 * 拠点 × コースコードに対応するテンプレートを返す。見つからなければ null。
 *
 * 拠点跨ぎでは解決しない（コースは拠点の持ち物のため）。
 */
export function matchCourseTemplate(
  templates: CourseTemplateLite[],
  officeId: string | null | undefined,
  courseCode: string | null | undefined,
): CourseTemplateLite | null {
  const codeUp = (courseCode ?? '').trim().toUpperCase();
  if (!officeId || codeUp === '') return null;
  const scoped = templates.filter((t) => t.office_id === officeId);

  // 1) exact
  const exact = scoped.find((t) => labelUpper(t) === codeUp);
  if (exact) return exact;

  // 2) M 溢れ (M2..M9 → 'M')
  if (/^M\d+$/.test(codeUp)) {
    const m = scoped.find((t) => labelUpper(t) === 'M');
    if (m) return m;
  }

  // 2b) 臨時溢れ (臨2..臨9 → 先頭 '臨')
  if (/^臨\d$/.test(codeUp)) {
    const rin = scoped.find((t) => labelUpper(t).slice(0, 1) === '臨');
    if (rin) return rin;
  }

  // 3) legacy 1 文字 (code='A' → label='Aコース')
  if (codeUp.length === 1) {
    const legacy = scoped.find((t) => labelUpper(t).slice(0, 1) === codeUp);
    if (legacy) return legacy;
  }

  return null;
}
