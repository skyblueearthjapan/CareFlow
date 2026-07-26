'use client';

/**
 * InboundControls — カイポケ → CareFlow 取り込みの操作部。
 *
 * 対象週チップ・❶差分取得・SummaryChip 群・❷曜日選択チップ・❸ dry-run/実取り込み・
 * dry-run 結果テーブル・二重確認ダイアログを描く。6列プレビュー (InboundDiffView) は
 * 下段のカレンダー枠 (InboundCalendar) に分離した。状態・ハンドラは useInbound が持つ。
 */
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type {
  ApplyInboundResultItem,
  EventsInboundApplyResult,
  EventsInboundChange,
  EventsInboundPreview,
} from '@/lib/schemas/integration';

import { type InboundVm, WEEKDAYS, fmtWeekLabel } from './useInbound';

const OUTCOME_META: Record<string, { label: string; cls: string }> = {
  cancelled: { label: 'キャンセル', cls: 'bg-error-bg text-error' },
  updated: { label: '更新', cls: 'bg-success-bg text-success' },
  added: { label: '追加', cls: 'bg-info-bg text-info' },
  deleted: { label: '削除', cls: 'bg-error-bg text-error' },
  skipped: { label: 'スキップ', cls: 'bg-bg-muted text-text-muted' },
  failed: { label: '失敗', cls: 'bg-error-bg text-error' },
};

const EVENT_ACTION_META: Record<string, { label: string; cls: string }> = {
  add: { label: '追加', cls: 'bg-info-bg text-info' },
  update: { label: '変更', cls: 'bg-warning-bg text-warning-strong' },
  delete: { label: '削除', cls: 'bg-error-bg text-error' },
};

type WeekOption = 'this' | 'next';

export function InboundControls({ vm }: { vm: InboundVm }) {
  const {
    busy,
    credentialsConfigured,
    thisMonday,
    nextMonday,
    selectedWeek,
    weekStart,
    thisElig,
    nextElig,
    currentElig,
    eligible,
    diffInbound,
    applyInbound,
    weekDays,
    daysWithDiff,
    sheetId,
    summary,
    selectedDays,
    setSelectedDays,
    dryRunResult,
    setDryRunResult,
    confirm,
    setConfirm,
    handleWeekChange,
    runDiff,
    runApply,
    hasSelectedDays,
    selectedDayLabels,
    massCancelWarning,
    mode,
    setMode,
    replaceInbound,
    replacePlan,
    applyEvents,
    eventsPlan,
    eventsError,
    eventsDryRunResult,
    hasEventChanges,
    fetching,
  } = vm;

  const applying = applyInbound.isPending || applyEvents.isPending || replaceInbound.isPending;
  const canDryRun = mode === 'diff' && (hasSelectedDays || hasEventChanges);
  const showApplyArea =
    mode === 'replace' ? replacePlan !== null : sheetId !== null || eventsPlan !== null;
  const hasDryRun = dryRunResult !== null || eventsDryRunResult !== null;
  const canReplaceApply = mode === 'replace' && replacePlan !== null;

  return (
    <div>
      {/* ── ヘッダー ── */}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">カイポケから取り込む</h2>
        <span className="inline-flex items-center rounded px-2 py-1 text-xs font-medium bg-warning-bg text-warning-strong">
          カイポケが正
        </span>
      </div>
      <p className="mb-4 text-sm text-text-secondary">
        カイポケでの直し込みをらく助に取り込みます。
        <strong className="text-text-primary">今週の予定表だけ</strong>
        を直します（定期パターンは変わりません）。
      </p>

      {/* 接続設定未完了の案内 */}
      {!credentialsConfigured && (
        <div className="mb-4 rounded-md border border-amber-500/50 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          先に接続設定（上のカード）を完了してください。
        </div>
      )}

      {/* ── 週選択チップ ── */}
      <div className="mb-5">
        <p className="mb-2 text-xs font-medium text-text-secondary">対象週</p>
        <div className="flex flex-wrap gap-2">
          {(['this', 'next'] as WeekOption[]).map((w) => {
            const mon = w === 'this' ? thisMonday : nextMonday;
            const elig = w === 'this' ? thisElig : nextElig;
            const isEligible = elig.data?.eligible ?? false;
            const isLoading = elig.isLoading;
            const isSelected = selectedWeek === w;
            return (
              <button
                key={w}
                type="button"
                disabled={isLoading || !isEligible}
                onClick={() => handleWeekChange(w)}
                className={[
                  'rounded-full border px-3 py-1.5 text-sm font-medium transition-colors',
                  isSelected && isEligible
                    ? 'border-brand-primary bg-brand-primary text-white'
                    : isEligible
                      ? 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted'
                      : 'cursor-not-allowed border-border-subtle bg-bg-muted text-text-muted opacity-60',
                ].join(' ')}
              >
                {w === 'this' ? '今週' : '来週'}
                <span className="ml-2 text-xs tabular-nums">{fmtWeekLabel(mon)}</span>
              </button>
            );
          })}
        </div>
        {!currentElig.isLoading && !eligible && (
          <p className="mt-2 text-xs text-text-muted">
            過去・今週はいつでも取り込めます。未来の週は、先に④反映（送る）を済ませると
            取り込めるようになります（計画中の週を消してしまう事故防止）。
          </p>
        )}
      </div>

      {/* ── 操作エリア（eligible でない場合はグレーアウト） ── */}
      <div className={eligible ? '' : 'pointer-events-none opacity-40'}>
        {/* ── 取り込みモード選択（2026-07-26 PO確定） ── */}
        <div className="mb-4">
          <p className="mb-2 text-xs font-medium text-text-secondary">取り込み方法</p>
          <div className="flex flex-wrap gap-2" data-testid="inbound-mode-toggle">
            {(
              [
                ['diff', '差分取り込み', '訪問を残したまま直す（打刻などの実績を守る）'],
                ['replace', '置換取り込み', 'この週を白紙にしてカイポケで丸ごと上書き'],
              ] as const
            ).map(([m, label, desc]) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={[
                  'rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                  mode === m
                    ? 'border-brand-primary bg-brand-primary/10 text-text-primary'
                    : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted',
                ].join(' ')}
              >
                <span className="block font-medium">{label}</span>
                <span className="block text-[11px] text-text-muted">{desc}</span>
              </button>
            ))}
          </div>
          {mode === 'replace' && (
            <p className="mt-2 text-xs text-warning-strong">
              ⚠ 置換取り込みでは、らく助側のこの週の情報（訪問予定）は
              <strong>すべて削除される可能性がございます</strong>
              。打刻などの実績が付いた週は安全のため実行できません（差分取り込みを使ってください）。
            </p>
          )}
        </div>

        {/* ❶ 差分取得（訪問 → イベントを直列で取得） */}
        <div className="mb-5 space-y-2">
          <Button
            size="sm"
            onClick={() => void runDiff()}
            disabled={!credentialsConfigured || !eligible || fetching || busy}
          >
            {fetching
              ? diffInbound.isPending || replaceInbound.isPending
                ? '訪問の現況を取得中…（1/2）'
                : 'イベントの現況を取得中…（2/2）'
              : mode === 'replace'
                ? '❶ カイポケの現況を取得して置換プレビューを見る（訪問＋イベント）'
                : '❶ カイポケの現況を取得して差分を見る（訪問＋イベント）'}
          </Button>
          {fetching && (
            <p className="text-xs text-text-muted">
              カイポケから訪問とイベント（個別業務）を順番に取得しています。合計で約2分かかります。
            </p>
          )}
          {sheetId && summary !== null && (
            <div className="flex flex-wrap items-center gap-1.5">
              <SummaryChip label="キャンセル候補" value={summary.delete ?? 0} tone="error" />
              <SummaryChip
                label="時間変更"
                value={(summary.edit ?? 0) + (summary.date_change ?? 0)}
                tone="warning"
              />
              <SummaryChip label="カイポケのみ" value={summary.add ?? 0} tone="success" />
              {(summary.unresolved_patient ?? 0) > 0 && (
                <SummaryChip
                  label="要確認"
                  value={summary.unresolved_patient ?? 0}
                  tone="warning"
                />
              )}
            </div>
          )}
          {massCancelWarning && summary !== null && (
            <Alert variant="destructive" data-testid="mass-cancel-warning">
              <AlertTitle>⚠ キャンセル候補が異常に多いです（{summary.delete ?? 0}件）</AlertTitle>
              <AlertDescription>
                カイポケにこの週のスケジュールが入力されていない可能性があります。
                このまま取り込むと、らく助のこの週の予定が大量にキャンセルされます。
                カイポケ側でこの週が表示されることを確認してから、取り込む曜日を
                <strong>手動で</strong>選んでください（安全のため自動選択を止めています）。
              </AlertDescription>
            </Alert>
          )}
          {diffInbound.isError && (
            <Alert variant="destructive">
              <AlertTitle>訪問の差分取得に失敗しました</AlertTitle>
              <AlertDescription>
                {diffInbound.error instanceof Error ? diffInbound.error.message : '不明なエラー'}
              </AlertDescription>
            </Alert>
          )}
          {replaceInbound.isError && (
            <Alert variant="destructive">
              <AlertTitle>置換プレビュー/実行に失敗しました</AlertTitle>
              <AlertDescription>
                {replaceInbound.error instanceof Error
                  ? replaceInbound.error.message
                  : '不明なエラー'}
              </AlertDescription>
            </Alert>
          )}
          {mode === 'replace' && replacePlan && (
            <div className="space-y-2" data-testid="replace-plan-panel">
              <div className="flex flex-wrap items-center gap-1.5">
                <SummaryChip label="削除（白紙化）" value={replacePlan.wiped} tone="error" />
                <SummaryChip label="カイポケから挿入" value={replacePlan.inserted} tone="success" />
                {replacePlan.coursesReassigned > 0 && (
                  <SummaryChip
                    label="コース担当変更"
                    value={replacePlan.coursesReassigned}
                    tone="warning"
                  />
                )}
                {replacePlan.coursesCreated > 0 && (
                  <SummaryChip
                    label="コース新設"
                    value={replacePlan.coursesCreated}
                    tone="success"
                  />
                )}
                {replacePlan.tempCourses > 0 && (
                  <SummaryChip label="臨時コース" value={replacePlan.tempCourses} tone="warning" />
                )}
                {replacePlan.skipped.length > 0 && (
                  <SummaryChip label="対象外" value={replacePlan.skipped.length} tone="warning" />
                )}
              </div>
              {replacePlan.traineeSolo.length > 0 && (
                <Alert data-testid="trainee-solo-warning">
                  <AlertTitle>
                    ⚠ 新人の単独訪問が含まれています（
                    {replacePlan.traineeSolo.reduce((n, t) => n + t.count, 0)}件）
                  </AlertTitle>
                  <AlertDescription>
                    {replacePlan.traineeSolo
                      .map((t) => `${t.staffName}（${t.count}件）`)
                      .join('・')}{' '}
                    — カイポケの実態どおり取り込みますが、実際に独り立ちしているなら
                    スタッフ編集で新人フラグをOFFにすることを検討してください
                    （らく助の自動割当は新人フラグがONの間、この方を候補にしません）。
                  </AlertDescription>
                </Alert>
              )}
              {replacePlan.skipped.length > 0 && (
                <div className="overflow-x-auto rounded-lg border border-border-default">
                  <table className="min-w-full text-xs">
                    <thead>
                      <tr className="border-b border-border-default bg-bg-muted">
                        <th className="px-3 py-2 text-left font-medium text-text-secondary">
                          利用者
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-text-secondary">
                          担当
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-text-secondary">
                          日付
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-text-secondary">
                          挿入できない理由
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {replacePlan.skipped.map((s, i) => (
                        <tr
                          key={`${s.userName}-${s.date}-${s.start}-${i}`}
                          className="border-b border-border-subtle"
                        >
                          <td className="px-3 py-2 text-text-primary">{s.userName || '—'}</td>
                          <td className="px-3 py-2 text-text-secondary">{s.staffName || '—'}</td>
                          <td className="px-3 py-2 tabular-nums text-text-secondary">
                            {s.date} {s.start}
                          </td>
                          <td className="px-3 py-2 text-text-muted">{s.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          {eventsError && (
            <Alert variant="destructive">
              <AlertTitle>イベント（個別業務）の取得に失敗しました</AlertTitle>
              <AlertDescription>
                {eventsError}
                {sheetId ? ' — 訪問の差分は取得済みのため、訪問だけ取り込めます。' : ''}
              </AlertDescription>
            </Alert>
          )}
        </div>

        {/* ── イベント（個別業務）差分 ── */}
        {eventsPlan && (
          <EventsPlanSection plan={eventsPlan} />
        )}

        {/* ❷ 曜日選択 */}
        {sheetId && (
          <div className="mb-5 space-y-3">
            <div>
              <p className="mb-2 text-xs font-medium text-text-secondary">
                取り込む曜日（差分のある曜日のみ選択可）
              </p>
              <div className="flex flex-wrap gap-1.5">
                {weekDays.map((d, i) => {
                  const hasItems = daysWithDiff.has(d);
                  const isChosen = selectedDays.has(d);
                  return (
                    <button
                      key={d}
                      type="button"
                      disabled={!hasItems}
                      onClick={() => {
                        const next = new Set(selectedDays);
                        if (isChosen) next.delete(d);
                        else next.add(d);
                        setSelectedDays(next);
                        setDryRunResult(null);
                      }}
                      className={[
                        'rounded border px-2.5 py-1 text-xs font-medium transition-colors',
                        isChosen
                          ? 'border-brand-primary bg-brand-primary text-white'
                          : hasItems
                            ? 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted'
                            : 'cursor-not-allowed border-border-subtle bg-bg-muted text-text-muted opacity-50',
                      ].join(' ')}
                    >
                      {WEEKDAYS[i]}
                    </button>
                  );
                })}
                <button
                  type="button"
                  onClick={() => {
                    setSelectedDays(new Set(weekDays.filter((d) => daysWithDiff.has(d))));
                    setDryRunResult(null);
                  }}
                  className="rounded border border-border-default bg-bg-base px-2.5 py-1 text-xs font-medium text-text-primary hover:bg-bg-muted"
                >
                  全部
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ❸ dry-run + 実取り込み（訪問＋イベントを直列適用）。
            置換モードは ❶ が dry-run 相当のため、確認ダイアログへ直行する。 */}
        {showApplyArea && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {mode === 'replace' ? (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setConfirm(true)}
                  disabled={!credentialsConfigured || !canReplaceApply || applying || busy}
                  data-testid="replace-apply-button"
                >
                  {applying ? '実行中…' : '❸ この週を置換して取り込む'}
                </Button>
              ) : (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void runApply(true)}
                    disabled={!credentialsConfigured || !canDryRun || applying || busy}
                  >
                    {applying && !hasDryRun ? '実行中…' : '❸ dry-run で確認'}
                  </Button>
                  {hasDryRun && (
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => setConfirm(true)}
                      disabled={busy || applying}
                    >
                      取り込む
                    </Button>
                  )}
                  {!canDryRun && (
                    <span className="text-xs text-text-muted">
                      取り込む対象がありません（訪問は曜日を選択・イベントは差分があるときに有効）
                    </span>
                  )}
                </>
              )}
            </div>

            {applyInbound.isError && (
              <Alert variant="destructive">
                <AlertTitle>訪問の実行に失敗しました</AlertTitle>
                <AlertDescription>
                  {applyInbound.error instanceof Error
                    ? applyInbound.error.message
                    : '不明なエラー'}
                </AlertDescription>
              </Alert>
            )}
            {applyEvents.isError && (
              <Alert variant="destructive">
                <AlertTitle>イベントの実行に失敗しました</AlertTitle>
                <AlertDescription>
                  {applyEvents.error instanceof Error ? applyEvents.error.message : '不明なエラー'}
                </AlertDescription>
              </Alert>
            )}

            {/* dry-run 結果テーブル（訪問） */}
            {dryRunResult && (
              <div className="overflow-x-auto rounded-lg border border-border-default">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="border-b border-border-default bg-bg-muted">
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">
                        利用者
                      </th>
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">日付</th>
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">操作</th>
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">結果</th>
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">詳細</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dryRunResult.results.map((r) => (
                      <DryRunRow key={r.itemId} result={r} />
                    ))}
                  </tbody>
                </table>
                <div className="border-t border-border-default bg-bg-muted px-3 py-2 text-xs text-text-secondary">
                  訪問 — キャンセル: {dryRunResult.cancelled} / 更新: {dryRunResult.updated} /
                  追加: {dryRunResult.added} / スキップ: {dryRunResult.skipped} / 失敗:{' '}
                  {dryRunResult.failed}
                </div>
              </div>
            )}

            {/* dry-run 結果テーブル（イベント） */}
            {eventsDryRunResult && (
              <EventsDryRunTable result={eventsDryRunResult} />
            )}
          </div>
        )}
      </div>

      {/* ── 二重確認ダイアログ ── */}
      <Dialog open={confirm} onOpenChange={(o) => !o && setConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {mode === 'replace'
                ? 'この週を置換して取り込みますか？'
                : 'カイポケの確定内容を取り込みますか？'}
            </DialogTitle>
            <DialogDescription className="space-y-2">
              <span className="block">
                対象週:{' '}
                <span className="font-medium text-text-primary">{fmtWeekLabel(weekStart)}</span>
              </span>
              {mode === 'replace' && replacePlan && (
                <>
                  <span className="block">
                    訪問（置換）:{' '}
                    <span className="font-medium text-text-primary">
                      らく助の {replacePlan.wiped} 件を削除 → カイポケの{' '}
                      {replacePlan.inserted} 件で置き換え
                    </span>
                  </span>
                  <span className="block font-medium text-error">
                    ⚠ らく助側のこの週の情報（訪問予定）はすべて削除される可能性がございます。
                    削除された予定は元に戻せません。カイポケの内容が正として書き込まれます。
                  </span>
                </>
              )}
              {mode === 'diff' && hasSelectedDays && (
                <span className="block">
                  訪問の対象曜日:{' '}
                  <span className="font-medium text-text-primary">{selectedDayLabels}</span>
                </span>
              )}
              {hasEventChanges && eventsPlan && (
                <span className="block">
                  イベント:{' '}
                  <span className="font-medium text-text-primary">
                    追加 {eventsPlan.adds} / 変更 {eventsPlan.updates} / 削除 {eventsPlan.deletes}
                    （週全体）
                  </span>
                </span>
              )}
              <span className="block text-error">
                らく助のスケジュールに実際に書き込まれます。この操作は Ctrl+Z の対象外です
                {mode === 'diff' && hasSelectedDays ? '（定期パターンは変わりません）' : ''}。
              </span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(false)}>
              やめる
            </Button>
            <Button variant="destructive" disabled={applying} onClick={() => void runApply(false)}>
              取り込む
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SummaryChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: 'success' | 'warning' | 'error';
}) {
  const cls =
    tone === 'success'
      ? 'bg-success-bg text-success'
      : tone === 'error'
        ? 'bg-error-bg text-error'
        : 'bg-warning-bg text-warning-strong';
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs ${cls}`}>
      {label} <span className="font-mono font-bold tabular-nums">{value}</span>
    </span>
  );
}

/** イベント（個別業務）差分のプレビューセクション。週丸ごと取り込み（曜日絞りなし）。 */
function EventsPlanSection({ plan }: { plan: EventsInboundPreview }) {
  const hasChanges = plan.changes.length > 0;
  return (
    <div className="mb-5 space-y-2" data-testid="events-plan-section">
      <p className="text-xs font-medium text-text-secondary">
        イベント（個別業務） — 休み・面談・会議など職員の予定（週全体をまとめて取り込み）
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        <SummaryChip label="追加" value={plan.adds} tone="success" />
        <SummaryChip label="変更" value={plan.updates} tone="warning" />
        <SummaryChip label="削除" value={plan.deletes} tone="error" />
        {plan.memoCount > 0 && (
          <span className="inline-flex items-center gap-1 rounded bg-bg-muted px-2 py-1 text-xs text-text-secondary">
            📝 メモ <span className="font-mono font-bold tabular-nums">{plan.memoCount}</span>
          </span>
        )}
      </div>
      {plan.unmatched.length > 0 && (
        <p className="text-xs text-text-muted">
          らく助未登録のため対象外:{' '}
          {plan.unmatched.map((u) => `${u.staffName}（${u.count}件）`).join('・')}
        </p>
      )}
      {!hasChanges && (
        <p className="text-xs text-text-muted">イベントの差分はありません（らく助と一致）。</p>
      )}
      {hasChanges && (
        <div className="overflow-x-auto rounded-lg border border-border-default">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="border-b border-border-default bg-bg-muted">
                <th className="px-3 py-2 text-left font-medium text-text-secondary">職員</th>
                <th className="px-3 py-2 text-left font-medium text-text-secondary">日付</th>
                <th className="px-3 py-2 text-left font-medium text-text-secondary">時間</th>
                <th className="px-3 py-2 text-left font-medium text-text-secondary">内容</th>
                <th className="px-3 py-2 text-left font-medium text-text-secondary">操作</th>
              </tr>
            </thead>
            <tbody>
              {plan.changes.map((c) => (
                <EventChangeRow key={`${c.action}-${c.externalId}`} change={c} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function EventChangeRow({ change }: { change: EventsInboundChange }) {
  const meta = EVENT_ACTION_META[change.action] ?? {
    label: change.action,
    cls: 'bg-bg-muted text-text-muted',
  };
  const timeLabel = change.isMemo ? '📝 メモ' : `${change.start}〜${change.end}`;
  const beforeTime =
    change.action === 'update' && change.beforeStart && change.beforeEnd
      ? `${change.beforeStart}〜${change.beforeEnd} → `
      : '';
  return (
    <tr className="border-b border-border-subtle">
      <td className="px-3 py-2 text-text-primary">{change.staffName}</td>
      <td className="px-3 py-2 tabular-nums text-text-secondary">{change.date}</td>
      <td className="px-3 py-2 tabular-nums text-text-secondary">
        {beforeTime}
        {timeLabel}
      </td>
      <td className="px-3 py-2 text-text-primary">{change.title || '—'}</td>
      <td className="px-3 py-2">
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${meta.cls}`}
        >
          {meta.label}
        </span>
      </td>
    </tr>
  );
}

/** イベント dry-run 結果テーブル。 */
function EventsDryRunTable({ result }: { result: EventsInboundApplyResult }) {
  return (
    <div
      className="overflow-x-auto rounded-lg border border-border-default"
      data-testid="events-dryrun-table"
    >
      <table className="min-w-full text-xs">
        <thead>
          <tr className="border-b border-border-default bg-bg-muted">
            <th className="px-3 py-2 text-left font-medium text-text-secondary">職員</th>
            <th className="px-3 py-2 text-left font-medium text-text-secondary">日付</th>
            <th className="px-3 py-2 text-left font-medium text-text-secondary">内容</th>
            <th className="px-3 py-2 text-left font-medium text-text-secondary">結果</th>
            <th className="px-3 py-2 text-left font-medium text-text-secondary">詳細</th>
          </tr>
        </thead>
        <tbody>
          {result.results.map((r) => {
            const meta = OUTCOME_META[r.outcome] ?? {
              label: r.outcome,
              cls: 'bg-bg-muted text-text-muted',
            };
            return (
              <tr key={r.externalId} className="border-b border-border-subtle">
                <td className="px-3 py-2 text-text-primary">{r.staffName || '—'}</td>
                <td className="px-3 py-2 tabular-nums text-text-secondary">{r.date || '—'}</td>
                <td className="px-3 py-2 text-text-primary">{r.title || '—'}</td>
                <td className="px-3 py-2">
                  <span
                    className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${meta.cls}`}
                  >
                    {meta.label}
                  </span>
                </td>
                <td className="px-3 py-2 text-text-muted">{r.detail ?? ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="border-t border-border-default bg-bg-muted px-3 py-2 text-xs text-text-secondary">
        イベント — 追加: {result.added} / 更新: {result.updated} / 削除: {result.deleted} /
        スキップ: {result.skipped} / 失敗: {result.failed}
      </div>
    </div>
  );
}

function DryRunRow({ result }: { result: ApplyInboundResultItem }) {
  const meta = OUTCOME_META[result.outcome] ?? {
    label: result.outcome,
    cls: 'bg-bg-muted text-text-muted',
  };
  return (
    <tr className="border-b border-border-subtle">
      <td className="px-3 py-2 text-text-primary">{result.patientName ?? '—'}</td>
      <td className="px-3 py-2 tabular-nums text-text-secondary">{result.date ?? '—'}</td>
      <td className="px-3 py-2 text-text-secondary">{result.action}</td>
      <td className="px-3 py-2">
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${meta.cls}`}
        >
          {meta.label}
        </span>
      </td>
      <td className="px-3 py-2 text-text-muted">{result.detail ?? ''}</td>
    </tr>
  );
}
