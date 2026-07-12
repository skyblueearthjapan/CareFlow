/**
 * 新人同行の時間重複判定 (§7.1 リアルタイム重複判定・クライアント側)。
 *
 * 実効同行訪問集合 (コース選択のコース内全訪問 ∪ 個別選択) を同一日で
 * 時間区間 [start, end) が交差するペアとして検出する。同一人物が同時刻に
 * 2 箇所へ行くのは物理的に不可能なため、重複が 1 件でも残る間は確定を
 * ブロックする (§0 決定事項 6・BE でも 422 で二重防御)。
 *
 * 純粋関数 (React 非依存・単体テスト可能)。
 */

/** 重複判定に必要な 1 訪問ぶんの最小情報。 */
export interface AccompanimentOverlapEntry {
  visitId: string;
  /** 同一日グルーピングのキー (0=月..6=日、または日付文字列でもよい)。 */
  dayKey: string | number;
  /** 表示用の日付ラベル (例: '7/14(月)')。メッセージにのみ使う。 */
  dayLabel: string;
  /** 開始 (0時起点の分)。null の訪問は判定から除外。 */
  startMin: number | null;
  /** 終了 (0時起点の分)。null の訪問は判定から除外。 */
  endMin: number | null;
  patientName: string | null;
  /** コースラベル (例: '稲毛A')。メッセージ表示用。 */
  courseLabel: string | null;
  /**
   * 同住所グループキー (buildSameAddressKey(lat,lng)・null=座標なし)。
   * 同住所×同時刻ペアは「90分の間に2人とも回る」運用ルールが既にあり
   * (SAME_ADDRESS_PAIR_MIN_OCCUPANCY)、同行者も同じ玄関に居られるため
   * 時間重複として**ブロックしない** (PO報告 2026-07-12)。
   */
  sameAddressKey?: string | null;
}

export interface AccompanimentOverlapPair {
  dayLabel: string;
  a: AccompanimentOverlapEntry;
  b: AccompanimentOverlapEntry;
}

export interface AccompanimentOverlapResult {
  /** 重複に関与する全 visitId (赤枠表示用)。 */
  overlapVisitIds: Set<string>;
  /** ペアごとの人間可読メッセージ (下部バー表示用)。 */
  messages: string[];
  pairs: AccompanimentOverlapPair[];
}

/** 'HH:MM' 表示 (分 → 時刻)。null は空文字。 */
function hhmm(min: number | null): string {
  if (min === null) return '';
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** 患者名 + コースラベルの表示 (例: '山田様（稲毛A）')。 */
function nameWithCourse(e: AccompanimentOverlapEntry): string {
  const name = e.patientName ?? '—';
  return e.courseLabel ? `${name}（${e.courseLabel}）` : name;
}

function overlaps(a: AccompanimentOverlapEntry, b: AccompanimentOverlapEntry): boolean {
  if (a.startMin === null || a.endMin === null || b.startMin === null || b.endMin === null) {
    return false;
  }
  // 半開区間 [start, end) の交差。端点接触 (a.end === b.start) は重複としない。
  return a.startMin < b.endMin && b.startMin < a.endMin;
}

/**
 * 実効同行訪問集合から時間重複ペアを検出する。
 *
 * 同一 visitId が重複して渡されても 1 件に畳む (コース選択 ∪ 個別選択の和で
 * 二重に入り得るため)。
 */
export function computeAccompanimentOverlaps(
  entries: readonly AccompanimentOverlapEntry[],
): AccompanimentOverlapResult {
  // visitId 重複排除。
  const uniq = new Map<string, AccompanimentOverlapEntry>();
  for (const e of entries) {
    if (!uniq.has(e.visitId)) uniq.set(e.visitId, e);
  }
  const list = [...uniq.values()];

  // 同一日ごとに束ねる。
  const byDay = new Map<string, AccompanimentOverlapEntry[]>();
  for (const e of list) {
    const key = String(e.dayKey);
    const arr = byDay.get(key);
    if (arr) arr.push(e);
    else byDay.set(key, [e]);
  }

  const overlapVisitIds = new Set<string>();
  const pairs: AccompanimentOverlapPair[] = [];
  const messages: string[] = [];

  for (const dayEntries of byDay.values()) {
    // 開始時刻昇順で走査 (メッセージの順序安定化)。
    const sorted = [...dayEntries].sort(
      (a, b) => (a.startMin ?? 0) - (b.startMin ?? 0) || a.visitId.localeCompare(b.visitId),
    );
    for (let i = 0; i < sorted.length; i++) {
      for (let j = i + 1; j < sorted.length; j++) {
        const a = sorted[i]!;
        const b = sorted[j]!;
        if (!overlaps(a, b)) continue;
        // 同住所ペア (90分占有ルール) は物理矛盾ではないため重複扱いしない。
        // 両方に座標キーがあり一致する場合のみ免除 (キー欠落時は保守的にブロック)。
        if (a.sameAddressKey != null && a.sameAddressKey === b.sameAddressKey) continue;
        overlapVisitIds.add(a.visitId);
        overlapVisitIds.add(b.visitId);
        pairs.push({ dayLabel: a.dayLabel, a, b });
        messages.push(
          `⚠ 時間が重複しています: ${a.dayLabel} ${hhmm(a.startMin)} ${nameWithCourse(a)} × ` +
            `${hhmm(b.startMin)} ${nameWithCourse(b)} — 同時には行けません`,
        );
      }
    }
  }

  return { overlapVisitIds, messages, pairs };
}
