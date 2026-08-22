'use client';

/**
 * SubstitutePanel — 急な休みの代替候補パネル (週空間 Phase E・運転席)。
 *
 * モックの `openOffPanel()` を部品化。「その日のコースを誰に渡すか」を
 * コース (group) ごとに ◎/△/× で選ばせる。
 *
 *   ◎ ok   … ハード制約すべて OK かつ時間の重なり無し
 *   △ warn … 時間の重なり / イベントの重なりだけ
 *   × ng   … 休み・非勤務日・NG スタッフ・性別・新人・拠点不可
 *
 * 実行は親 (FE-C) が既存 API で行う (契約書 D8):
 *   コース丸ごと = PATCH /courses/{id} / 1 件ずつ = visit-assign-staff-week /
 *   休み登録     = POST /staff/{id}/overrides
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { useSubstituteCandidates } from '@/lib/queries/cockpit';
import { parseIsoDate } from './reconcileMarkers';
import {
  SUBSTITUTE_STATUS_MARK,
  type SubstituteApplyPayload,
  type SubstituteCandidate,
  type SubstituteGroup,
} from '@/lib/schemas/v2/cockpit';

const WD_JA = ['月', '火', '水', '木', '金', '土', '日'] as const;

const STATUS_CLS: Record<SubstituteCandidate['status'], string> = {
  ok: 'text-success',
  warn: 'text-warning-strong',
  ng: 'text-error',
};

function fmtDate(dateIso: string): string {
  const d = parseIsoDate(dateIso);
  const wd = WD_JA[(d.getDay() + 6) % 7] ?? '';
  return `${d.getMonth() + 1}/${d.getDate()}(${wd})`;
}

/** 候補の一言説明。理由が無い ◎ は「空き」。 */
function reasonText(c: SubstituteCandidate): string {
  if (c.reasons.length > 0) return c.reasons.map((r) => r.message).join('・');
  return c.status === 'ok' ? '空き' : '';
}

export interface SubstitutePanelProps {
  /** 休む本人。 */
  staff: { id: string; name: string };
  /** 対象日 (YYYY-MM-DD)。 */
  date: string;
  /** 権限が無いときは候補取得も付け替えもしない。既定 true。 */
  canEdit?: boolean;
  onClose: () => void;
  /** 候補を選んだときの付け替え指示 (group 単位)。 */
  onApply: (payload: SubstituteApplyPayload) => void;
  /**
   * 「休みにする」= staff_weekly_override の作成。
   * Promise を返すと**休みの登録が終わってから**付け替えを実行する
   * (休み前に付け替えると、休み判定が入る前の状態で候補が動く)。
   */
  onMarkOff: () => void | Promise<void>;
}

export function SubstitutePanel({
  staff,
  date,
  canEdit = true,
  onClose,
  onApply,
  onMarkOff,
}: SubstitutePanelProps) {
  const [markOff, setMarkOff] = React.useState(true);
  const [doneGroups, setDoneGroups] = React.useState<Set<string>>(new Set());
  const markedOffRef = React.useRef(false);

  const query = useSubstituteCandidates({ staff_id: staff.id, date }, canEdit);
  const data = query.data;
  const groups = data?.groups ?? [];

  const groupKey = (g: SubstituteGroup, i: number) => g.course_id ?? `__none__${i}`;

  /** 休み登録は 1 回だけ (複数コースを続けて付け替えても override は 1 件)。 */
  const markOffOnce = async (): Promise<void> => {
    if (!markOff || markedOffRef.current) return;
    markedOffRef.current = true;
    await onMarkOff();
  };

  const apply = async (g: SubstituteGroup, i: number, toStaffId: string | null) => {
    // 休みの登録 → 付け替え の順。await しないと「休みなのに担当のまま」の
    // 中間状態が観測されうる (レビュー M-12)。
    await markOffOnce();
    onApply({
      toStaffId,
      groups: [{ courseId: g.course_id, visitIds: g.visits.map((v) => v.visit_id) }],
    });
    setDoneGroups((prev) => new Set(prev).add(groupKey(g, i)));
  };

  return (
    <section
      className="rounded-lg border-2 border-brand-primary/40 bg-bg-base shadow-sm"
      data-testid="substitute-panel"
      aria-label={`${staff.name} の代替候補`}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle px-3 py-2">
        <h3 className="text-sm font-bold text-text-primary">
          🛌 {staff.name} を {fmtDate(date)} 休みに
        </h3>
        <label className="flex items-center gap-1 text-[11px] text-text-secondary">
          <input
            type="checkbox"
            checked={markOff}
            onChange={(e) => setMarkOff(e.target.checked)}
            data-testid="substitute-mark-off"
          />
          休みにする（この日の勤務を外す）
        </label>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={onClose}
          data-testid="substitute-close"
          aria-label="代替候補を閉じる"
        >
          ×
        </Button>
      </div>

      <div className="space-y-3 px-3 py-2">
        <p className="text-[11px] text-text-secondary">
          この日の予定を誰に渡しますか？ 固定イベント（朝会など）は自動で除外されます。
        </p>

        {/* enabled=false のとき v5 の status は 'pending' のまま止まるので
            fetchStatus で「本当に取りに行っているか」を見る (空回りの文言を出さない)。 */}
        {query.isPending && query.fetchStatus === 'fetching' ? (
          <p className="text-[12px] text-text-muted" data-testid="substitute-loading">
            候補を探しています…
          </p>
        ) : null}
        {query.isError ? (
          <p className="text-[12px] text-error" data-testid="substitute-error">
            候補の取得に失敗しました:{' '}
            {query.error instanceof Error ? query.error.message : String(query.error)}
          </p>
        ) : null}

        {(data?.warnings ?? []).map((w) => (
          <p key={w} className="text-[11px] text-warning-strong" data-testid="substitute-warning">
            ⚠ {w}
          </p>
        ))}

        {data && groups.length === 0 ? (
          <div data-testid="substitute-empty">
            <RakusukeNote
              pose="joy"
              size="sm"
              title="この日は渡す予定がありません"
              comment="そのまま休みにできます"
            />
            <Button
              type="button"
              size="sm"
              className="w-full"
              disabled={!canEdit}
              onClick={() => {
                markedOffRef.current = true;
                void onMarkOff();
              }}
              data-testid="substitute-only-off"
            >
              🛌 休みにする
            </Button>
          </div>
        ) : null}

        {groups.map((g, i) => {
          const key = groupKey(g, i);
          const done = doneGroups.has(key);
          return (
            <div
              key={key}
              className="rounded border border-border-subtle px-2 py-1.5"
              data-testid={`substitute-group-${key}`}
            >
              <div className="mb-1 flex flex-wrap items-center gap-x-2 text-[12px]">
                <b className="text-text-primary">{g.course_label}</b>
                <span className="text-text-muted">{g.visits.length}件</span>
                <span className="tnum text-[11px] text-text-muted">
                  {g.visits.map((v) => `${v.start_time} ${v.patient_name}`).join(' / ')}
                </span>
                {done ? (
                  <span className="text-[11px] font-bold text-success">✓ 付け替えました</span>
                ) : null}
              </div>

              {g.candidates.length === 0 ? (
                <p className="text-[11px] text-text-muted">候補がいません（担当なしへ戻せます）</p>
              ) : null}

              <ul className="space-y-1">
                {g.candidates.map((c) => (
                  <li key={c.staff_id}>
                    <button
                      type="button"
                      // × はハード制約違反 (休み・非勤務日・NG・性別・新人・拠点)。
                      // 押させない (BE も 422 で弾く)。理由は title で読める。
                      disabled={!canEdit || done || c.status === 'ng'}
                      title={
                        c.status === 'ng'
                          ? `この方には渡せません: ${reasonText(c) || '制約に合いません'}`
                          : reasonText(c) || undefined
                      }
                      onClick={() => void apply(g, i, c.staff_id)}
                      data-testid={`substitute-cand-${key}-${c.staff_id}`}
                      className="flex w-full flex-wrap items-center gap-x-2 rounded border border-border-default px-2 py-1 text-left text-[11px] hover:border-brand-primary/60 disabled:opacity-50"
                    >
                      <span className={`text-sm font-bold ${STATUS_CLS[c.status]}`}>
                        {SUBSTITUTE_STATUS_MARK[c.status]}
                      </span>
                      <span className="font-bold text-text-primary">{c.name}</span>
                      {c.office_name ? (
                        <span className="text-text-muted">{c.office_name}</span>
                      ) : null}
                      <span className="min-w-0 flex-1 truncate text-text-secondary">
                        {reasonText(c)}
                      </span>
                      <span className="tnum text-[10px] text-text-muted">
                        この日 {c.load_today}件 ・ 相性 {c.score.toFixed(1)}
                      </span>
                    </button>
                  </li>
                ))}
                <li>
                  <button
                    type="button"
                    disabled={!canEdit || done}
                    onClick={() => void apply(g, i, null)}
                    data-testid={`substitute-unassign-${key}`}
                    className="flex w-full items-center gap-x-2 rounded border border-dashed border-border-default px-2 py-1 text-left text-[11px] hover:border-brand-primary/60 disabled:opacity-50"
                  >
                    <span className="text-sm font-bold text-text-muted">—</span>
                    <span className="text-text-primary">（担当なし）へ戻す</span>
                    <span className="text-text-muted">あとで決める</span>
                  </button>
                </li>
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}
