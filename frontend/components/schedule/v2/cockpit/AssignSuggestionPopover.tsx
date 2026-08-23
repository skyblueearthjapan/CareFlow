'use client';

/**
 * AssignSuggestionPopover — 「（担当なし）」のコースを誰に入れるかの提案
 * (Phase 2-B・設計 `docs/plans/unassigned-suggestions-design.md` §1-1、
 *  モック `docs/mockups/unassigned-suggestions-mock.html` の `#popCourse`)。
 *
 * 保留プールの「効果を表示 → ここに入れそう」と対になる入口で、こちらは
 * **時刻を固定したまま「入れる人」を探す**（急休の逆操作）。
 *
 *   ◎ = コース丸ごと引き受けられる (`whole_ok_staff_ids`) → [このコースを割り当てる]
 *   △ = 一部だけ重なる → 理由のみ (「1件ずつなら可」)
 *   × = 休み / NG / 資格不可 → 既定は折りたたみ
 *
 * API は自分で 1 本だけ叩く (`useAssignCandidates` = read-only)。
 * 実行 (付け替え) は **親** が `onAssignCourse` で受け、訪問ごとの
 * `visit-assign-staff-week` + `PATCH /courses` に落とす (憲法1: 今週だけ)。
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover';
import { useAssignCandidates } from '@/lib/queries/cockpit';
import { SUBSTITUTE_STATUS_MARK } from '@/lib/schemas/v2/cockpit';
import { fmtMd } from './reconcileMarkers';
import type {
  AssignCandidatesRead,
  SubstituteCandidate,
  SubstituteStatus,
} from '@/lib/schemas/v2/cockpit';

/** 表示用にまとめた候補 1 人分。 */
export interface AssignSuggestionRow {
  staffId: string;
  name: string;
  /** ◎ = コース丸ごと可 / △ = 一部重なり / × = 不可。 */
  status: SubstituteStatus;
  /** 「13:30 が重なる」などの理由 (重複除去済み・× のみ複数行になりうる)。 */
  reasons: string[];
  officeName: string | null;
  /** 並び用 (全グループ合計)。 */
  score: number;
}

/**
 * レスポンス (グループ = コース単位の束) を候補 1 人 1 行へ畳む。
 *
 * ◎ は **BE の `whole_ok_staff_ids` が唯一の正**（全訪問 ok の交差）。
 * FE で再計算すると Phase 1 の交差判定と二重ソースになるため、ここでは
 * 「◎ 以外を △/× に振り分ける」ことしかしない:
 *   1 グループでも ng があれば × / それ以外で ◎ でなければ △。
 *
 * 並びは ◎(score 降順) → △ → ×（設計 §6）。純関数 (テストしやすさのため export)。
 */
export function summarizeAssignCandidates(data: AssignCandidatesRead): AssignSuggestionRow[] {
  const wholeOk = new Set(data.whole_ok_staff_ids);
  const acc = new Map<
    string,
    { cand: SubstituteCandidate; score: number; anyNg: boolean; reasons: string[] }
  >();
  for (const g of data.groups) {
    for (const c of g.candidates) {
      const cur = acc.get(c.staff_id);
      const reasons = c.reasons.map((r) => r.message).filter(Boolean);
      if (!cur) {
        acc.set(c.staff_id, {
          cand: c,
          score: c.score,
          anyNg: c.status === 'ng',
          reasons,
        });
        continue;
      }
      cur.score += c.score;
      cur.anyNg = cur.anyNg || c.status === 'ng';
      cur.reasons.push(...reasons);
    }
  }
  const rows: AssignSuggestionRow[] = [];
  for (const [staffId, v] of acc) {
    const status: SubstituteStatus = v.anyNg ? 'ng' : wholeOk.has(staffId) ? 'ok' : 'warn';
    rows.push({
      staffId,
      name: v.cand.name,
      status,
      reasons: [...new Set(v.reasons)],
      officeName: v.cand.office_name ?? null,
      score: v.score,
    });
  }
  const rank: Record<SubstituteStatus, number> = { ok: 0, warn: 1, ng: 2 };
  rows.sort((a, b) => rank[a.status] - rank[b.status] || b.score - a.score);
  return rows;
}

/** 理由が空の ◎ は「空き」とだけ言う (モックの `空き・同拠点` と同じ語彙)。 */
function whyText(row: AssignSuggestionRow): string {
  if (row.reasons.length > 0) return row.reasons.join('・');
  return ['空き', row.officeName].filter(Boolean).join('・');
}

export interface AssignSuggestionPopoverProps {
  /** 対象日 (YYYY-MM-DD)。 */
  date: string;
  /**
   * 評価対象の訪問 (= 「（担当なし）」行に見えている束そのもの)。
   * `course_id` を送らないのは、コースには既に担当の付いた訪問が混ざりうるため
   * (盤面の束と評価対象がズレる)。付け替えも同じ集合を流す。
   */
  visitIds: string[];
  /** 見出しに出すコース名 (例: 稲毛C)。 */
  courseLabel: string;
  /** 見出しの補足に使う件数・時間帯。 */
  visits: { count: number; startTime: string | null; endTime: string | null };
  /** 位置合わせ用のアンカー (バッジ要素)。省略時は画面中央寄りに出る。 */
  anchorEl?: HTMLElement | null;
  /** false で取得も実行もしない (閲覧ロール)。既定 true。 */
  canEdit?: boolean;
  /** 実行中 (ボタンの二度押し防止)。 */
  submitting?: boolean;
  /** 2-D: 候補行の hover をタイムライン/リストのハイライトへ流す。 */
  onHoverCandidate?: (staffId: string | null) => void;
  /** ◎ の [このコースを割り当てる]。 */
  onAssignCourse: (toStaffId: string) => void;
  /** 「1件ずつ分けて入れる」= 閉じて最初の訪問のメニューを開く。 */
  onSplit: () => void;
  onClose: () => void;
}

export function AssignSuggestionPopover({
  date,
  visitIds,
  courseLabel,
  visits,
  anchorEl,
  canEdit = true,
  submitting = false,
  onHoverCandidate,
  onAssignCourse,
  onSplit,
  onClose,
}: AssignSuggestionPopoverProps) {
  const query = useAssignCandidates(
    visitIds.length > 0 ? { date, visit_ids: visitIds } : null,
    canEdit,
  );
  const [showNg, setShowNg] = React.useState(false);

  const rows = React.useMemo(
    () => (query.data ? summarizeAssignCandidates(query.data) : []),
    [query.data],
  );
  const okRows = rows.filter((r) => r.status === 'ok');
  const warnRows = rows.filter((r) => r.status === 'warn');
  const ngRows = rows.filter((r) => r.status === 'ng');
  // 「0 名」と「まだ分からない」を混同させない (SubstitutePanel と同じ規律)。
  const loading = canEdit && query.isPending;

  const span = visits.startTime && visits.endTime ? `${visits.startTime}〜${visits.endTime}` : null;

  const anchorRef = React.useRef<HTMLElement | null>(null);
  anchorRef.current = anchorEl ?? null;

  /**
   * 閉じたらバッジへフォーカスを戻す (L2)。Radix はトリガー要素へ戻してくれるが、
   * ここは仮想アンカー (virtualRef) なのでトリガーが無く、キーボード操作が
   * body へ落ちてしまう。外れた要素への `focus()` は no-op なので安全。
   */
  React.useEffect(
    () => () => {
      anchorEl?.focus?.();
    },
    [anchorEl],
  );

  /**
   * ◎ が 0 名で、△ が「理由なし」だけ = 1 人ずつなら全員入れるのに、
   * **コース内の訪問同士が重なっている**ケース。「引き受けられる人がいない」と
   * 出すと誤読される (人の問題ではなく組み方の問題) ので言い分ける (L3)。
   */
  const overlapWithinCourse =
    okRows.length === 0 && warnRows.length > 0 && warnRows.every((r) => r.reasons.length === 0);

  return (
    <Popover open onOpenChange={(next) => (next ? undefined : onClose())}>
      {/* バッジの位置に出す。アンカーが無ければ Radix の既定位置へフォールバック。 */}
      {anchorEl ? (
        <PopoverAnchor virtualRef={anchorRef as React.RefObject<HTMLElement>} />
      ) : (
        <PopoverAnchor>
          <span />
        </PopoverAnchor>
      )}
      <PopoverContent
        align="start"
        className="w-[420px] max-w-[92vw] p-0"
        data-testid="assign-suggestion-popover"
        aria-label={`${courseLabel} の担当候補`}
        onMouseLeave={() => onHoverCandidate?.(null)}
      >
        <div className="flex items-start gap-2 border-b border-border-subtle px-3 py-2">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-bold text-text-primary">{courseLabel} を誰に？</p>
            <p className="tnum text-[11px] text-text-muted" data-testid="assign-suggestion-sub">
              {fmtMd(date)}・{visits.count}件{span ? `・${span}` : ''}
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label="提案を閉じる"
            data-testid="assign-suggestion-close"
          >
            ✕
          </Button>
        </div>

        <div className="max-h-[46vh] space-y-1.5 overflow-y-auto px-3 py-2">
          {loading ? (
            <p className="text-[12px] text-text-muted" data-testid="assign-suggestion-loading">
              候補を確認しています…
            </p>
          ) : null}
          {query.isError ? (
            <div className="space-y-1" data-testid="assign-suggestion-error">
              <p className="text-[12px] text-error">
                候補の確認に失敗しました:{' '}
                {query.error instanceof Error ? query.error.message : String(query.error)}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void query.refetch()}
                data-testid="assign-suggestion-retry"
              >
                もう一度確認する
              </Button>
            </div>
          ) : null}

          {(query.data?.warnings ?? []).map((w) => (
            <p
              key={w}
              className="text-[11px] text-warning-strong"
              data-testid="assign-suggestion-warning"
            >
              ⚠ {w}
            </p>
          ))}

          {okRows.map((row) => (
            <div
              key={row.staffId}
              className="flex items-center gap-2 rounded border border-border-default px-2 py-1.5"
              data-testid={`assign-suggestion-ok-${row.staffId}`}
              onMouseEnter={() => onHoverCandidate?.(row.staffId)}
            >
              <span className="w-4 shrink-0 text-base font-black text-success" aria-hidden>
                {SUBSTITUTE_STATUS_MARK.ok}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[12px] font-bold text-text-primary">{row.name}</span>
                <span className="block text-[11px] text-text-muted">{whyText(row)}</span>
              </span>
              <Button
                type="button"
                size="sm"
                disabled={!canEdit || submitting}
                onClick={() => onAssignCourse(row.staffId)}
                data-testid={`assign-suggestion-apply-${row.staffId}`}
              >
                このコースを割り当てる
              </Button>
            </div>
          ))}

          {!loading && !query.isError && okRows.length === 0 ? (
            <p
              className="rounded border border-border-warning bg-warning-bg px-2 py-1.5 text-[12px] text-warning-strong"
              data-testid="assign-suggestion-no-whole"
            >
              {overlapWithinCourse
                ? 'コース内で時間が重なるため1人では回れません。「1件ずつ分けて入れる」から患者ごとに入れてください。'
                : 'コースを丸ごと引き受けられる人はいません。「1件ずつ分けて入れる」から患者ごとに入れてください。'}
            </p>
          ) : null}

          {warnRows.map((row) => (
            <div
              key={row.staffId}
              className="flex items-center gap-2 rounded border border-border-default px-2 py-1.5 opacity-70"
              data-testid={`assign-suggestion-warn-${row.staffId}`}
              onMouseEnter={() => onHoverCandidate?.(row.staffId)}
            >
              <span className="w-4 shrink-0 text-base font-black text-warning-strong" aria-hidden>
                {SUBSTITUTE_STATUS_MARK.warn}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[12px] font-bold text-text-primary">{row.name}</span>
                <span className="block text-[11px] text-text-muted">{whyText(row)}</span>
              </span>
              <span className="shrink-0 text-[11px] text-text-muted">1件ずつなら可</span>
            </div>
          ))}

          {ngRows.length > 0 ? (
            <div>
              <button
                type="button"
                className="text-[11px] text-text-muted hover:text-text-secondary"
                onClick={() => setShowNg((v) => !v)}
                aria-expanded={showNg}
                data-testid="assign-suggestion-ng-toggle"
              >
                × 休み・NG・資格不可 {ngRows.length}名を{showNg ? '隠す ▴' : '表示 ▾'}
              </button>
              {showNg ? (
                <ul className="mt-1 space-y-1" data-testid="assign-suggestion-ng-list">
                  {ngRows.map((row) => (
                    <li
                      key={row.staffId}
                      className="flex items-center gap-2 rounded border border-border-subtle px-2 py-1 opacity-60"
                      data-testid={`assign-suggestion-ng-${row.staffId}`}
                    >
                      <span className="w-4 shrink-0 text-base font-black text-info" aria-hidden>
                        {SUBSTITUTE_STATUS_MARK.ng}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-[12px] font-bold text-text-primary">
                          {row.name}
                        </span>
                        <span className="block text-[11px] text-text-muted">{whyText(row)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-2 border-t border-border-subtle px-3 py-2">
          <p className="min-w-0 flex-1 text-[10px] text-text-muted">
            割り当ては今週だけ（毎週の型は変わりません）。「戻る」で復元できます
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onSplit}
            data-testid="assign-suggestion-split"
          >
            1件ずつ分けて入れる
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
