'use client';

/**
 * SubstitutePanel — 「🛌 休みにする」の確認モーダル (PO 決定 2026-08-23)。
 *
 * 旧版は「コースごとに ◎/△/× の候補を選ぶ」一覧パネルだったが、現場の操作は
 * ほぼ **「休みにして、その日の予定は担当なしに戻す」** だけだった。1 人ずつの
 * 提案は選択肢が多すぎて逆に迷う、という PO 指摘を受けて、
 *
 *   ① 何が起きるかを 1 文で見せる (「予定 N件（コース …）は担当なしに戻します」)
 *   ② **その日を丸ごと引き受けられる人**がいるときだけ、その人を最大 3 名出す
 *   ③ 既定は [担当なしに戻す]
 *
 * の 3 点に絞ったモーダルへ作り替えた。実行は親が新 API
 * ``POST /schedule/v2/staff-off-week`` を **1 回だけ** 呼ぶ (休みの登録と
 * 付け替えが 1 トランザクション = 「戻る」1 回で全部戻る)。
 *
 * 「丸ごと引き受けられる人」の判定は既存の ``substitute-candidates`` を流用:
 * その日の全グループで ``status='ok'`` (ハード制約 OK かつ時間の重なり無し) の
 * スタッフだけを採る。1 グループでも △/× ならその人は出さない。
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useSubstituteCandidates } from '@/lib/queries/cockpit';
import { parseIsoDate } from './reconcileMarkers';
import type { SubstituteCandidate, SubstituteGroup } from '@/lib/schemas/v2/cockpit';

const WD_JA = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** 出す「丸ごと引き受けられる人」の上限 (多すぎると選べない)。 */
const MAX_WHOLE_DAY_CANDIDATES = 3;

function fmtDate(dateIso: string): string {
  const d = parseIsoDate(dateIso);
  const wd = WD_JA[(d.getDay() + 6) % 7] ?? '';
  return `${d.getMonth() + 1}/${d.getDate()}(${wd})`;
}

/**
 * 付替の対象になる訪問だけを残す。
 *
 * BE (`staff-off-week`) が動かすのは ``status='planned'`` だけ — 打刻済み・完了・
 * 取消済みは据え置く。件数表示と青ピン判定をここで揃えておかないと、
 * 「予定 3件」と出したのに 2 件しか動かない (逆に、実施済みの青ピンで実行不可に
 * 見える) といった食い違いが出る。
 */
export function plannedVisitsOf(group: SubstituteGroup): SubstituteGroup['visits'] {
  return group.visits.filter((v) => v.status === 'planned');
}

/** その日の全グループで ◎ のスタッフ (= コース丸ごと引き受けられる人)。 */
export function pickWholeDayCandidates(groups: SubstituteGroup[]): SubstituteCandidate[] {
  if (groups.length === 0) return [];
  const okIn = (g: SubstituteGroup) =>
    new Map(g.candidates.filter((c) => c.status === 'ok').map((c) => [c.staff_id, c]));
  const first = okIn(groups[0]!);
  const rest = groups.slice(1).map(okIn);
  const survivors: { cand: SubstituteCandidate; score: number }[] = [];
  for (const [staffId, cand] of first) {
    if (!rest.every((m) => m.has(staffId))) continue;
    // 並びは全グループ合計の相性 (score) が高い順。
    const score = rest.reduce((sum, m) => sum + (m.get(staffId)?.score ?? 0), cand.score);
    survivors.push({ cand, score });
  }
  survivors.sort((a, b) => b.score - a.score);
  return survivors.slice(0, MAX_WHOLE_DAY_CANDIDATES).map((s) => s.cand);
}

export interface SubstitutePanelProps {
  /** 休む本人。 */
  staff: { id: string; name: string };
  /** 対象日 (YYYY-MM-DD)。 */
  date: string;
  /** 権限が無いときは候補取得も実行もしない。既定 true。 */
  canEdit?: boolean;
  /** 実行中 (ボタンの二度押し防止)。 */
  submitting?: boolean;
  onClose: () => void;
  /**
   * 実行。``toStaffId = null`` は「この日の予定を担当なしに戻す」。
   * 親は ``POST /schedule/v2/staff-off-week`` を 1 回だけ呼ぶ。
   */
  onApply: (toStaffId: string | null) => void;
}

export function SubstitutePanel({
  staff,
  date,
  canEdit = true,
  submitting = false,
  onClose,
  onApply,
}: SubstitutePanelProps) {
  const query = useSubstituteCandidates({ staff_id: staff.id, date }, canEdit);
  const data = query.data;
  const groups = React.useMemo(() => data?.groups ?? [], [data]);

  // 対象は planned のみ (BE と同じ規則)。打刻済み・完了・取消済みは据え置かれる。
  const plannedVisits = groups.flatMap((g) => plannedVisitsOf(g));
  const visitCount = plannedVisits.length;
  const courseLabels = groups
    .filter((g) => plannedVisitsOf(g).length > 0)
    .map((g) => g.course_label)
    .filter(Boolean);
  const pinnedNames = plannedVisits.filter((v) => v.week_pinned).map((v) => v.patient_name);
  const blocked = pinnedNames.length > 0;

  const wholeDay = React.useMemo(() => pickWholeDayCandidates(groups), [groups]);
  // 候補の取得が終わるまでは押させない: 「0 件」と「まだ分からない」を混同すると、
  // 青ピンや予定を見落としたまま実行してしまう。
  const loading = canEdit && (query.isPending || query.isError);
  const disabled = !canEdit || submitting || blocked || loading;

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent className="max-w-md" data-testid="substitute-panel">
        <DialogHeader>
          <DialogTitle className="text-sm">
            🛌 {staff.name}さんを {fmtDate(date)} 休みにします
          </DialogTitle>
          <DialogDescription className="text-[12px]" data-testid="substitute-summary">
            {loading ? (
              // 「0 件」と「まだ分からない」を同じ文言にしない (H4)。
              <>この日の予定を確認しています…</>
            ) : visitCount > 0 ? (
              <>
                この日の予定 {visitCount}件
                {courseLabels.length > 0 ? `（${courseLabels.join('・')}）` : ''}
                は担当なしに戻します。
              </>
            ) : (
              <>この日は渡す予定がありません。そのまま休みにできます。</>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {/* enabled=false のとき v5 の status は 'pending' のまま止まるので
              fetchStatus で「本当に取りに行っているか」を見る (空回りの文言を出さない)。 */}
          {query.isPending && query.fetchStatus === 'fetching' ? (
            <p className="text-[12px] text-text-muted" data-testid="substitute-loading">
              この日の予定を確認しています…
            </p>
          ) : null}
          {query.isError ? (
            <div className="space-y-1" data-testid="substitute-error">
              <p className="text-[12px] text-error">
                予定の確認に失敗しました:{' '}
                {query.error instanceof Error ? query.error.message : String(query.error)}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void query.refetch()}
                data-testid="substitute-retry"
              >
                もう一度確認する
              </Button>
            </div>
          ) : null}

          {(data?.warnings ?? []).map((w) => (
            <p key={w} className="text-[11px] text-warning-strong" data-testid="substitute-warning">
              ⚠ {w}
            </p>
          ))}

          {/* 青ピン (今週固定) は「今週はこの人・この位置のまま」の宣言。
              休みにすると担当が外れるので、解除してもらうまで実行させない。 */}
          {blocked ? (
            <p
              className="rounded border border-error/40 bg-error/5 px-2 py-1.5 text-[12px] text-error"
              data-testid="substitute-pinned"
            >
              今週固定（青ピン）の予定が {pinnedNames.length}件あります（
              {pinnedNames.map((n) => `${n}様`).join('・')}
              ）。青ピンを外してから休みにしてください。
            </p>
          ) : null}

          {/* 丸ごと引き受けられる人がいるときだけ出す (1 人ずつの提案はしない)。 */}
          {wholeDay.length > 0 ? (
            <div className="space-y-1" data-testid="substitute-whole-day">
              <p className="text-[11px] text-text-secondary">
                この日のコースを丸ごと引き受けられる方がいます。
              </p>
              {wholeDay.map((c) => (
                <Button
                  key={c.staff_id}
                  type="button"
                  size="sm"
                  variant="outline"
                  className="w-full justify-start"
                  disabled={disabled}
                  onClick={() => onApply(c.staff_id)}
                  data-testid={`substitute-cand-${c.staff_id}`}
                >
                  <span className="font-bold">{c.name}さんに割り当てる</span>
                  <span className="ml-2 text-[11px] text-text-muted">
                    空き{c.office_name ? ` ・ ${c.office_name}` : ''}
                  </span>
                </Button>
              ))}
            </div>
          ) : null}
        </div>

        <DialogFooter className="gap-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onClose}
            data-testid="substitute-cancel"
          >
            やめる
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={disabled}
            onClick={() => onApply(null)}
            data-testid="substitute-unassign"
          >
            {visitCount > 0 ? '担当なしに戻す' : '休みにする'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
