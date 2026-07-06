/**
 * visit.note の表示フィルタ。
 *
 * スケジューラが管理用に書き込む内部メタデータは現場画面に見せない:
 *   - "Layer1: fixed pattern" 等 (layer1_expander)
 *   - "reset_to_fixed_v2 iso_year=... iso_week=..." (auto_allocator_v2)
 * それ以外 (特別訪問週間: / [2名体制…] など人間向けの日本語 note) は表示する。
 * モバイルの訪問カード・訪問詳細で共用。
 */

export function isInternalVisitNote(note: string): boolean {
  return /^(Layer1:|reset_to_fixed)/.test(note.trim());
}

/** 表示してよい note を返す (内部メタデータ・空文字は null)。 */
export function displayVisitNote(note: string | null | undefined): string | null {
  if (!note) return null;
  const t = note.trim();
  if (!t || isInternalVisitNote(t)) return null;
  return note;
}
