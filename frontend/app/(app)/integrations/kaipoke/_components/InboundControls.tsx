'use client';

/**
 * InboundControls — カイポケ → らく助 取り込みの操作部 (smart-inbound・2026-07-26)。
 *
 * モード選択なしの3ステップ: ❶取得 → ❷統合プレビュー → ❸取り込む。
 * システムが日単位で自動判別する (打刻あり日=🔒差分・なし日=置換)。
 * イベント (個別業務) も同じボタンに相乗り。状態・ハンドラは useInbound が持つ。
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
  EventsInboundChange,
  EventsInboundPreview,
  SmartInboundPreview,
} from '@/lib/schemas/integration';

import { type InboundVm, fmtDayLabel, fmtWeekLabel } from './useInbound';

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
    handleWeekChange,
    smartPreview,
    smartPlan,
    eventsPlan,
    eventsError,
    hasEventChanges,
    confirm,
    setConfirm,
    runDiff,
    runApply,
    fetching,
    applying,
    canApply,
  } = vm;

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
        カイポケの最新スケジュールをらく助へ取り込みます。
        <strong className="text-text-primary">
          打刻実績のある日は行を守って直し（🔒差分）、まだ実績のない日はカイポケの内容で
          丸ごと書き直します（置換）
        </strong>
        — どちらにするかはシステムが日ごとに自動判別します。
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
        {/* ❶ 取得（訪問 → イベントを直列で取得） */}
        <div className="mb-5 space-y-2">
          <Button
            size="sm"
            onClick={() => void runDiff()}
            disabled={!credentialsConfigured || !eligible || fetching || busy}
          >
            {fetching
              ? smartPreview.isPending
                ? '訪問の現況を取得中…（1/2）'
                : 'イベントの現況を取得中…（2/2）'
              : '❶ カイポケの現況を取得して差分を見る（訪問＋イベント）'}
          </Button>
          {fetching && (
            <p className="text-xs text-text-muted">
              カイポケから訪問とイベント（個別業務）を順番に取得しています。合計で約2分かかります。
            </p>
          )}
          {smartPreview.isError && (
            <Alert variant="destructive">
              <AlertTitle>訪問の取得に失敗しました</AlertTitle>
              <AlertDescription>
                {smartPreview.error instanceof Error ? smartPreview.error.message : '不明なエラー'}
              </AlertDescription>
            </Alert>
          )}
          {eventsError && (
            <Alert variant="destructive">
              <AlertTitle>イベント（個別業務）の取得に失敗しました</AlertTitle>
              <AlertDescription>
                {eventsError}
                {smartPlan ? ' — 訪問の差分は取得済みのため、訪問だけ取り込めます。' : ''}
              </AlertDescription>
            </Alert>
          )}

          {/* ❷ 統合プレビュー */}
          {smartPlan && <SmartPlanPanel plan={smartPlan} />}
        </div>

        {/* イベント（個別業務）差分 */}
        {eventsPlan && <EventsPlanSection plan={eventsPlan} />}

        {/* ❸ 取り込み */}
        {(smartPlan !== null || eventsPlan !== null) && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setConfirm(true)}
                disabled={!credentialsConfigured || !canApply || applying || busy}
                data-testid="smart-apply-button"
              >
                {applying ? '実行中…' : '❸ らく助へ取り込む'}
              </Button>
              {!canApply && (
                <span className="text-xs text-text-muted">取り込む対象がありません</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── 確認ダイアログ ── */}
      <Dialog open={confirm} onOpenChange={(o) => !o && setConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>カイポケの内容を取り込みますか？</DialogTitle>
            <DialogDescription className="space-y-2">
              <span className="block">
                対象週:{' '}
                <span className="font-medium text-text-primary">{fmtWeekLabel(weekStart)}</span>
              </span>
              {smartPlan && smartPlan.protectedDays.length > 0 && (
                <span className="block">
                  🔒実績のある日（{smartPlan.protectedDays.map(fmtDayLabel).join('・')}）:{' '}
                  <span className="font-medium text-text-primary">
                    行を残したまま差分を反映します（打刻の記録は守られます）
                  </span>
                </span>
              )}
              {smartPlan && smartPlan.replaceDays.length > 0 && smartPlan.replace && (
                <>
                  <span className="block">
                    置換する日（{smartPlan.replaceDays.map(fmtDayLabel).join('・')}）:{' '}
                    <span className="font-medium text-text-primary">
                      らく助の {smartPlan.replace.wiped} 件を削除 → カイポケの{' '}
                      {smartPlan.replace.inserted} 件で置き換え
                    </span>
                  </span>
                  <span className="block font-medium text-error">
                    ⚠ 置換する日のらく助側の情報（訪問予定）はすべて削除される可能性がございます。
                    削除された予定は元に戻せません。
                  </span>
                </>
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
                らく助のスケジュールに実際に書き込まれます。この操作は Ctrl+Z
                の対象外です（定期パターンは変わりません）。
              </span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(false)}>
              やめる
            </Button>
            <Button variant="destructive" disabled={applying} onClick={() => void runApply()}>
              取り込む
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** ❷ 統合プレビュー: 日別の自動判別バッジ + 差分/置換のサマリ。 */
function SmartPlanPanel({ plan }: { plan: SmartInboundPreview }) {
  const diff = plan.diffSummary;
  return (
    <div className="space-y-2" data-testid="smart-plan-panel">
      {/* 日別バッジ (なぜその方式なのかが見える) */}
      <div className="flex flex-wrap items-center gap-1.5">
        {plan.protectedDays.map((d) => (
          <span
            key={d}
            className="inline-flex items-center gap-1 rounded border border-info/40 bg-info-bg px-2 py-0.5 text-[11px] font-medium text-info"
            title="打刻実績があるため、行を残したまま差分で直します"
          >
            🔒 {fmtDayLabel(d)} 差分
          </span>
        ))}
        {plan.replaceDays.map((d) => (
          <span
            key={d}
            className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-muted px-2 py-0.5 text-[11px] font-medium text-text-secondary"
            title="打刻実績がないため、カイポケの内容で丸ごと書き直します"
          >
            {fmtDayLabel(d)} 置換
          </span>
        ))}
      </div>

      {/* 実績日 (差分) のサマリ */}
      {plan.protectedDays.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-medium text-text-secondary">実績日の差分:</span>
          <SummaryChip label="キャンセル候補" value={diff.delete ?? 0} tone="error" />
          <SummaryChip
            label="時間変更"
            value={(diff.edit ?? 0) + (diff.date_change ?? 0)}
            tone="warning"
          />
          <SummaryChip label="カイポケのみ" value={diff.add ?? 0} tone="success" />
          {(diff.unresolved_patient ?? 0) > 0 && (
            <SummaryChip label="要確認" value={diff.unresolved_patient ?? 0} tone="warning" />
          )}
        </div>
      )}

      {/* 置換日のサマリ */}
      {plan.replace && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="replace-plan-panel">
          <span className="text-xs font-medium text-text-secondary">置換日の計画:</span>
          <SummaryChip label="削除（白紙化）" value={plan.replace.wiped} tone="error" />
          <SummaryChip label="カイポケから挿入" value={plan.replace.inserted} tone="success" />
          {plan.replace.coursesReassigned > 0 && (
            <SummaryChip
              label="コース担当変更"
              value={plan.replace.coursesReassigned}
              tone="warning"
            />
          )}
          {plan.replace.coursesCreated > 0 && (
            <SummaryChip label="コース新設" value={plan.replace.coursesCreated} tone="success" />
          )}
          {plan.replace.tempCourses > 0 && (
            <SummaryChip label="臨時コース" value={plan.replace.tempCourses} tone="warning" />
          )}
          {plan.replace.skipped.length > 0 && (
            <SummaryChip label="対象外" value={plan.replace.skipped.length} tone="warning" />
          )}
        </div>
      )}

      {/* ⚠新人の単独訪問 (取り込むが新人フラグ見直しを促す) */}
      {plan.replace && plan.replace.traineeSolo.length > 0 && (
        <Alert data-testid="trainee-solo-warning">
          <AlertTitle>
            ⚠ 新人の単独訪問が含まれています（
            {plan.replace.traineeSolo.reduce((n, t) => n + t.count, 0)}件）
          </AlertTitle>
          <AlertDescription>
            {plan.replace.traineeSolo.map((t) => `${t.staffName}（${t.count}件）`).join('・')} —
            カイポケの実態どおり取り込みますが、実際に独り立ちしているなら
            スタッフ編集で新人フラグをOFFにすることを検討してください。
          </AlertDescription>
        </Alert>
      )}

      {/* 対象外の一覧 (隠さない) */}
      {plan.replace && plan.replace.skipped.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border-default">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="border-b border-border-default bg-bg-muted">
                <th className="px-3 py-2 text-left font-medium text-text-secondary">利用者</th>
                <th className="px-3 py-2 text-left font-medium text-text-secondary">担当</th>
                <th className="px-3 py-2 text-left font-medium text-text-secondary">日付</th>
                <th className="px-3 py-2 text-left font-medium text-text-secondary">
                  挿入できない理由
                </th>
              </tr>
            </thead>
            <tbody>
              {plan.replace.skipped.map((s, i) => (
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
  );
}

/** イベント（個別業務）差分のプレビューセクション。週丸ごと取り込み。 */
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
