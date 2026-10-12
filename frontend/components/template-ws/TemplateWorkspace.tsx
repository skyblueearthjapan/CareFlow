'use client';

/**
 * 型の作業場 — 日付の無い仮の 1 週間で、コースの型（固定訪問の枠）を試してから使う。
 * 人もらく助エージェントも同じ下書きを編集する（変更は履歴に残り、3 秒ごとに画面へ反映）。
 * 出口は 2 つ: ①固定訪問（型）に登録 ②この週のコースに入れる。下書きの編集に承認は要らない。
 *
 * 並び（盤面に幅を回す・PO 2026-10-13）: 見出しの行に 拠点（全拠点も）と下書きの切り替え →
 * 道具の行 → 数の物差し → 最新の編集の帯（最適化なら［くわしく］で報告）→ 盤面（月〜土）→
 * 盤面の下に、たためる「まだ枠の無い方」「編集の履歴」。右はらく助エージェントの列（境目で幅を変えられる）。
 *
 * 幅の計算（1600px・サイドバーを開いた形 232px・右の列 380px）: 1600 − 外の余白 28 − サイドバー 232
 * − 本文の余白 48 = 1292px。右の列 380 と境目 12 を引いて盤面は 900px。盤面の列は 76 + 6 × 120 = 796px が
 * いちばん狭い形なので、月〜土は横にスクロールせずに収まる（1 列 ≈ 135px）。
 */
import { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { useAgentDock } from '@/components/agent/screenContext';
import { useResizableWidth } from '@/components/agent/useResizableWidth';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { isoWeekFromLocalDate } from '@/lib/format/isoWeek';
import { useOffices } from '@/lib/queries/offices';
import {
  ALL_OFFICES,
  useCommit,
  useCreateDraft,
  useDraft,
  useDraftActions,
  useDrafts,
  useJob,
  usePatchDraft,
  useTemplateAvailability,
  useWeekApply,
} from '@/lib/queries/templateWs';
import { latestStepKeys } from '@/lib/templateWs/helpers';
import type { Job, SlotCard } from '@/lib/templateWs/types';

import { CommitDialog, CompareDialog, InputsDialog, OptimiseDialog, WhatifDialog } from './Dialogs';
import { MetricsHeader } from './MetricsHeader';
import { OptimiseReportPanel } from './OptimiseReport';
import { ResizeHandle } from './ResizeHandle';
import { DraftSwitcher, Fold, HistoryList, LatestStepCard, SlotDetail, Tray, UndoRedo } from './SidePanels';
import { TemplateBoard } from './TemplateBoard';

type DialogName = 'optimise' | 'whatif' | 'compare' | 'inputs' | 'commit' | null;

/** らく助エージェントの右の列の幅（境目で変えられる・最小 300px〜画面の 50%）。 */
export const DOCK_DEFAULT_PX = 380;
export const DOCK_WIDTH_KEY = 'rk-agent-dock-width';

export function TemplateWorkspace() {
  const avail = useTemplateAvailability();
  const offices = useOffices({ limit: 50 });
  const officeIds = avail.data ?? [];
  const [officePick, setOfficePick] = useState<string | null>(null);
  const officeId = officePick ?? officeIds[0] ?? null;

  const drafts = useDrafts(officeId);
  const [pickedId, setPickedId] = useState<string | null>(null);
  const list = drafts.data ?? [];
  const selectedId = pickedId && list.some((d) => d.id === pickedId) ? pickedId : (list[0]?.id ?? null);
  const [compareWith, setCompareWith] = useState<string | null>(null);

  const viewQ = useDraft(selectedId, compareWith);
  const view = viewQ.data ?? null;
  // らく助エージェントは、この画面では右の列に収まる(↘ で右下へたたむ・AGENTS.md §11-2)
  const agentDock = useAgentDock({
    draftId: selectedId,
    label: view?.name ?? list.find((d) => d.id === selectedId)?.name ?? null,
  });
  const dockW = useResizableWidth({ storageKey: DOCK_WIDTH_KEY, defaultWidth: DOCK_DEFAULT_PX, min: 300 });
  const act = useDraftActions(selectedId);
  const create = useCreateDraft();
  const patch = usePatchDraft();
  const commit = useCommit(selectedId);
  const weekApply = useWeekApply(selectedId);

  const [dialog, setDialog] = useState<DialogName>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [dismissedSeq, setDismissedSeq] = useState<number>(0);
  const [jobId, setJobId] = useState<string | null>(null);
  // 最適化の報告: undefined = 閉じている・null = いちばん新しい最適化・数 = その手順
  const [reportSeq, setReportSeq] = useState<number | null | undefined>(undefined);
  const [flashKey, setFlashKey] = useState<string | null>(null);
  useEffect(() => {
    if (!flashKey) return;
    const t = window.setTimeout(() => setFlashKey(null), 2500);
    return () => window.clearTimeout(t);
  }, [flashKey]);

  const now = useMemo(() => isoWeekFromLocalDate(new Date()), []);

  const onJobDone = (j: Job) => {
    setJobId(null);
    act.refresh();
    if (j.status === 'done') {
      toast.success(`最適化しました（変わった枠 ${j.result?.changed ?? 0} 件）。帯の［くわしく］で何をどう変えたか見られます`);
    } else toast.error(j.error ?? '最適化できませんでした');
  };
  const job = useJob(jobId, onJobDone);
  const running = !!jobId && !['done', 'failed'].includes(job.data?.status ?? 'queued');

  const guard = async <T,>(p: Promise<T>, ok?: string): Promise<T | null> => {
    try {
      const r = await p;
      if (ok) toast.success(ok);
      return r;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        toast.warning('下書きがほかで変わりました。最新の状態を読み込みました。もう一度どうぞ');
        act.refresh();
      } else {
        toast.error(apiErrorMessage(e));
      }
      return null;
    }
  };

  if (avail.isLoading) return <p className="p-4 text-sm text-text-muted">読み込み中…</p>;
  if (officeIds.length === 0) {
    return (
      <p className="p-4 text-sm text-text-secondary" data-testid="template-ws-off">
        型の作業場は、この事業所ではまだ使えません（管理者のみ・準備中）。
      </p>
    );
  }

  const busy =
    act.move.isPending || act.whatif.isPending || act.undo.isPending || act.redo.isPending || running;
  const readOnly = !view || view.status !== 'draft';
  const selectedSlot: SlotCard | null =
    (view && selectedKey && view.days.flatMap((d) => d.slots).find((s) => s.key === selectedKey)) || null;
  const showLatest = view?.latest_step && view.latest_step.seq !== dismissedSeq ? view.latest_step : null;
  const highlight = showLatest ? latestStepKeys(view!) : new Set<string>();
  const officeName = (id: string) => offices.allOffices.find((o) => o.id === id)?.name ?? id;

  const doMove = (slot: SlotCard, courseId: string, start: number) =>
    view &&
    guard(
      act.move.mutateAsync({
        key: slot.key,
        course_id: courseId,
        start: `${Math.floor(start / 60)}:${String(start % 60).padStart(2, '0')}`,
        expected_version: view.version,
      }),
    ).then((r) => {
      if (r?.new_issues?.length) toast.info(`気をつけること: ${r.new_issues.slice(0, 2).join(' / ')}`);
    });

  const resetDraft = () => {
    setCompareWith(null);
    setSelectedKey(null);
    setReportSeq(undefined);
  };

  return (
    <div className="flex lg:h-full lg:min-h-0" data-testid="template-workspace">
      <div className="flex min-w-0 flex-1 flex-col gap-2 lg:min-h-0 lg:overflow-y-auto" data-testid="template-main">
        <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1
            className="text-lg font-semibold"
            title="日付の無い仮の 1 週間です。ここでの変更は、登録するまで本物の型にも今週の予定にも影響しません。"
          >
            型の作業場
          </h1>
          {officeIds.length > 1 ? (
            <select
              value={officeId ?? ''}
              onChange={(e) => {
                setOfficePick(e.target.value);
                setPickedId(null);
                resetDraft();
              }}
              className="h-9 rounded border border-border-default bg-bg-base px-2 text-sm"
              aria-label="拠点"
              data-testid="office-select"
            >
              {officeIds.map((id) => (
                <option key={id} value={id}>
                  {officeName(id)}
                </option>
              ))}
              <option value={ALL_OFFICES}>全拠点（{officeIds.map(officeName).join('・')}）</option>
            </select>
          ) : null}
          <DraftSwitcher
            drafts={list}
            selectedId={selectedId}
            busy={create.isPending}
            onSelect={(id) => {
              setPickedId(id);
              resetDraft();
            }}
            onCreate={(name) =>
              officeId &&
              void guard(create.mutateAsync({ officeId, name }), '下書きを作りました').then((v) => v && setPickedId(v.id))
            }
            onRename={(id, name) => void guard(patch.mutateAsync({ id, name }))}
            onArchive={(id) => void guard(patch.mutateAsync({ id, archived: true }), 'しまいました')}
          />
        </header>
        <p className="text-xs text-text-muted">
          日付の無い仮の 1 週間です。ここでの変更は、登録するまで本物の型にも今週の予定にも影響しません。
          {officeId === ALL_OFFICES ? '全拠点の下書きは、拠点ごとのコースを並べて一緒に試せます（動かせるのは同じ拠点のコースの中だけ）。' : ''}
        </p>

        {!view ? (
          <p className="text-sm text-text-muted">
            {selectedId ? '読み込み中…' : '「＋新しい下書き」で、いまの型を写した仮の 1 週間を作れます。'}
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2" data-testid="template-toolbar">
              <Button type="button" size="sm" disabled={readOnly || busy} onClick={() => setDialog('optimise')} data-testid="tb-optimise">
                最適化
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={readOnly || busy} onClick={() => setDialog('whatif')} data-testid="tb-whatif">
                もしも
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setDialog('compare')} data-testid="tb-compare">
                見比べ
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setDialog('inputs')} data-testid="tb-inputs">
                計算の前提
              </Button>
              <UndoRedo
                view={view}
                disabled={readOnly || busy}
                onUndo={() => void guard(act.undo.mutateAsync({ expected_version: view.version }))}
                onRedo={() => void guard(act.redo.mutateAsync({ expected_version: view.version }))}
              />
              {view.compare.draft_id ? (
                <Button type="button" size="sm" variant="outline" onClick={() => setCompareWith(null)}>
                  盤面の比較をやめる（いまの型に戻す）
                </Button>
              ) : null}
              <Button type="button" size="sm" className="ml-auto" disabled={readOnly || busy} onClick={() => setDialog('commit')} data-testid="tb-commit">
                この下書きを使う…
              </Button>
            </div>

            {running ? (
              <p className="flex items-center gap-2 rounded bg-info-bg px-3 py-1.5 text-sm text-info-strong" role="status" data-testid="running">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                計算しています（数十秒かかります）。終わるまで、ほかの変更は控えてください。
              </p>
            ) : null}
            {view.status === 'committed' ? (
              <p className="rounded bg-success-bg px-3 py-1.5 text-sm text-success">この下書きは型に登録済みです。</p>
            ) : null}

            <MetricsHeader view={view} />
            {showLatest ? (
              <LatestStepCard
                step={showLatest}
                onDismiss={() => setDismissedSeq(showLatest.seq)}
                onOpenReport={(seq) => setReportSeq(seq)}
              />
            ) : null}
            {reportSeq !== undefined ? (
              <OptimiseReportPanel
                draftId={view.id}
                seq={reportSeq}
                onClose={() => setReportSeq(undefined)}
                onFlash={(key) => {
                  setFlashKey(key);
                  setSelectedKey(key);
                }}
              />
            ) : null}
            {selectedSlot ? (
              <SlotDetail
                key={selectedSlot.key + view.version}
                slot={selectedSlot}
                view={view}
                readOnly={readOnly}
                busy={busy}
                onMove={(c, s) => void doMove(selectedSlot, c, s)}
                onClose={() => setSelectedKey(null)}
              />
            ) : null}

            <div className="flex min-h-[18rem] flex-col lg:max-h-[78vh]">
              <TemplateBoard
                view={view}
                readOnly={readOnly || busy}
                highlight={highlight}
                selectedKey={selectedKey}
                flashKey={flashKey}
                onSelect={setSelectedKey}
                onDropToCourse={(slot, courseId) => slot.start != null && void doMove(slot, courseId, slot.start)}
                onWeekdayRefused={() => toast.info('曜日は動かせません（コースと、希望の範囲の中の時刻だけ動かせます）')}
              />
            </div>

            <div className="grid gap-2 md:grid-cols-2">
              <Fold title="まだ枠の無い方" count={`${view.tray.length} 名`} testId="fold-tray">
                <Tray view={view} />
              </Fold>
              <Fold title="編集の履歴" count={`${view.steps.length} 件`} testId="fold-history">
                <HistoryList view={view} onOpenReport={(seq) => setReportSeq(seq)} />
              </Fold>
            </div>

            <OptimiseDialog
              open={dialog === 'optimise'}
              onClose={() => setDialog(null)}
              onRun={(weekdays, seconds) => {
                setDialog(null);
                void guard(act.optimise.mutateAsync({ weekdays, seconds, expected_version: view.version })).then(
                  (j) => j && setJobId(j.id),
                );
              }}
            />
            {dialog === 'whatif' ? (
              <WhatifDialog
                open
                view={view}
                staff={view.staff}
                onClose={() => setDialog(null)}
                onApply={(p) => {
                  setDialog(null);
                  void guard(act.whatif.mutateAsync({ ...p, expected_version: view.version }), 'もしもの条件を変えました');
                }}
              />
            ) : null}
            {dialog === 'compare' ? (
              <CompareDialog
                open
                draft={view}
                others={list}
                onClose={() => setDialog(null)}
                onShowOnBoard={(id) => {
                  setCompareWith(id);
                  setDialog(null);
                }}
              />
            ) : null}
            {dialog === 'inputs' ? (
              <InputsDialog open draftId={view.id} offices={view.offices ?? []} onClose={() => setDialog(null)} />
            ) : null}
            {dialog === 'commit' ? (
              <CommitDialog
                open
                draftId={view.id}
                version={view.version}
                isoYear={now.isoYear}
                isoWeek={now.isoWeek}
                busy={commit.isPending || weekApply.isPending}
                onClose={() => setDialog(null)}
                onCommit={() =>
                  void guard(commit.mutateAsync(view.version), '型に登録しました。次に「週を生成」した週から反映されます').then(
                    (r) => r && setDialog(null),
                  )
                }
                onWeekApply={(hash) => {
                  void guard(
                    weekApply.mutateAsync({ isoYear: now.isoYear, isoWeek: now.isoWeek, weekHash: hash, expectedVersion: view.version }),
                    'この週のコースに入れました。アプリの「戻る」で戻せます',
                  ).then((r) => r && setDialog(null));
                }}
              />
            ) : null}
          </>
        )}
      </div>

      {agentDock.docked ? (
        <ResizeHandle handleProps={dockW.handleProps} label="らく助エージェントの列の幅" testId="agent-dock-resize" />
      ) : null}
      <aside
        ref={agentDock.ref}
        className={agentDock.docked ? 'flex flex-none flex-col lg:h-full lg:min-h-0' : 'hidden'}
        style={agentDock.docked ? { width: dockW.width } : undefined}
        aria-label="らく助エージェントに相談"
        data-testid="agent-dock"
      />
    </div>
  );
}
