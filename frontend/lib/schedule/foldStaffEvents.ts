/**
 * 職員イベントの畳み込み (mobile-staff-schedule-design-2026-09-16.md §3 C-2)。
 *
 * 同じ予定が出所違いで二重に入っていることがある (現場の朝会 = `manual` 31 件と
 * カイポケ取込 `kaipoke` 24 件が併存・調査 §8)。データ側の解消は運用 (§6) だが、
 * **スマホに二重で出るのは見た目の事故**なので表示側で 1 件に畳む。
 *
 * 畳み込みキー: 同一 `staff_id` × 同一 開始時刻 × 同一 `title.trim()`。
 * BE の `EventRead` は `starts_at` ではなく `date` + `start_time` (HH:MM) で
 * 返す契約 (`backend/app/schemas/staff_events.py`) なので、設計書の
 * 「同一 starts_at」はこの 2 つの組で表現する (同値)。
 *
 * 残す行の優先順位 (2026-09-16 レビュー MEDIUM-8・**PO 確認事項**):
 *   1. 生きている行 (`cancelled_at == null`) を優先する
 *   2. 同順位なら `kaipoke` > `manual` > `fixed` — カイポケ由来を正とする
 * = `rank = (cancelled_at == null ? 10 : 0) + priorityOf(source)`。
 *
 * 決定の理由: **取消済みの複製が生きた行を隠さないため**。出所だけで決めると、
 * カイポケ側の朝会を「今週だけ外す」した日に、現場が手で入れた生きている
 * `manual` の予定までスマホから消えてしまう (その日は本当に予定があるのに、
 * 打消線の「今週除外」1 件しか見えない)。どちらを正とするかは運用判断なので
 * PO 確認事項として残す。
 *
 * `cancelled_at` は「いずれかが cancelled」ではなく **残した行の値**をそのまま
 * 使う (行ごと残すので自然にそうなる)。同順位なら先に来た行を残す (安定)。
 */

/** 畳み込みに必要な最小の形 (EventRead / CockpitEventRead がこれを満たす)。 */
export interface FoldableStaffEvent {
  /** 旧 BE 互換で optional。欠けている行同士は同一スタッフ扱い。 */
  staff_id?: string;
  /** YYYY-MM-DD。 */
  date: string;
  /** HH:MM。 */
  start_time: string;
  title: string;
  /** 'manual' | 'kaipoke' | 'fixed' (旧 BE 互換で optional)。 */
  source?: string;
  /** 非 null = 「今週だけ外す」で取消済み。生きている行より下位に落とす。 */
  cancelled_at?: string | null;
}

/** 大きいほど優先して残す。未知の出所は最下位 (0)。 */
const SOURCE_PRIORITY: Record<string, number> = {
  kaipoke: 3,
  manual: 2,
  fixed: 1,
};

function priorityOf(source: string | undefined): number {
  return SOURCE_PRIORITY[source ?? ''] ?? 0;
}

/** 生死 (10) を出所 (0〜3) より上位に置いた順位。大きいほど残す。 */
function rankOf(ev: FoldableStaffEvent): number {
  return (ev.cancelled_at == null ? 10 : 0) + priorityOf(ev.source);
}

function foldKey(ev: FoldableStaffEvent): string {
  return `${ev.staff_id ?? ''}|${ev.date}|${ev.start_time}|${ev.title.trim()}`;
}

/**
 * 重複イベントを 1 件に畳む。入力順 (先に現れたキーの順) は保たれる。
 */
export function foldStaffEvents<T extends FoldableStaffEvent>(events: readonly T[]): T[] {
  const slotByKey = new Map<string, number>();
  const kept: T[] = [];

  for (const ev of events) {
    const key = foldKey(ev);
    const slot = slotByKey.get(key);
    if (slot === undefined) {
      slotByKey.set(key, kept.length);
      kept.push(ev);
      continue;
    }
    // 同じキーが既にある → 生死 → 出所 の順位が高い方だけを残す (同順位は先勝ち)。
    if (rankOf(ev) > rankOf(kept[slot]!)) {
      kept[slot] = ev;
    }
  }

  return kept;
}
