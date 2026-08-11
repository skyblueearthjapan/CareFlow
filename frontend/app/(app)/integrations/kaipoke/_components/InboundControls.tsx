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

import { type InboundVm, fmtDayLabel, fmtRelativeWeek, fmtWeekLabel } from './useInbound';

const EVENT_ACTION_META: Record<string, { label: string; cls: string }> = {
  add: { label: '追加', cls: 'bg-info-bg text-info' },
  update: { label: '変更', cls: 'bg-warning-bg text-warning-strong' },
  delete: { label: '削除', cls: 'bg-error-bg text-error' },
};

export function InboundControls({ vm }: { vm: InboundVm }) {
  const {
    busy,
    credentialsConfigured,
    weekOffset,
    weekStart,
    currentElig,
    eligible,
    canGoNext,
    changeWeek,
    goToThisWeek,
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
    snapshots,
    restoreSnapshot,
    restoring,
    inboundHistory,
  } = vm;

  return (
    <div className="flex h-full flex-col">
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

      {/* ── 週送りナビ（過去・未来とも無制限 / 2026-08-09 開放） ── */}
      <div className="mb-5">
        <p className="mb-2 text-xs font-medium text-text-secondary">対象週</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => changeWeek(-1)}
            disabled={fetching || applying || busy}
            data-testid="inbound-week-prev"
          >
            ◀ 前の週
          </Button>
          <span
            className="inline-flex items-center gap-2 rounded-md border border-border-default bg-bg-base px-3 py-1.5 text-sm"
            data-testid="inbound-week-label"
          >
            <span
              className={[
                'rounded px-1.5 py-0.5 text-xs font-medium',
                weekOffset === 0
                  ? 'bg-brand-primary text-white'
                  : weekOffset > 0
                    ? 'bg-info-bg text-info'
                    : 'bg-bg-muted text-text-secondary',
              ].join(' ')}
            >
              {fmtRelativeWeek(weekOffset)}
            </span>
            <span className="font-medium tabular-nums text-text-primary">
              {fmtWeekLabel(weekStart)}
            </span>
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => changeWeek(1)}
            disabled={!canGoNext || fetching || applying || busy}
            data-testid="inbound-week-next"
          >
            次の週 ▶
          </Button>
          {weekOffset !== 0 && (
            <button
              type="button"
              onClick={goToThisWeek}
              disabled={fetching || applying || busy}
              className="text-xs text-text-secondary underline underline-offset-2 hover:text-text-primary"
            >
              今週へ戻る
            </button>
          )}
        </div>
        <p className="mt-2 text-xs text-text-muted">
          過去・未来ともどの週でも取り込めます（過去分はカイポケが請求と紐づくため残っています。
          未来週はカイポケに入力済みの計画を映せます）。
        </p>
        {/* 未来週専用の警告 (2026-08-09 開放とセットの安全装置)。 */}
        {weekOffset > 0 && (
          <p
            className="mt-1 rounded-md border border-amber-500/50 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900"
            data-testid="inbound-future-week-warning"
          >
            ⚠ 未来週です。カイポケ側の内容が「正」として取り込まれ、らく助側でこの週に
            計画中の内容は上書き（削除・キャンセル候補）になります。カイポケにこの週の
            スケジュールが入力済みであることを確認してから取り込んでください。
            なお、取り込みの直前の盤面は毎回自動保存されるため、間違えた場合は
            「取り込み前に戻す」（取り込み後にこのカードに表示）でいつでも復元できます。
          </p>
        )}
      </div>

      {/* ── 取り込み前に戻す (PO 決定 2026-08-09: 間違えて取り込んでも戻せる) ── */}
      {snapshots.length > 0 && (
        <div
          className="mb-5 rounded-md border border-border-default bg-bg-muted/40 px-3 py-2"
          data-testid="inbound-snapshot-section"
        >
          <p className="mb-1.5 text-xs font-medium text-text-secondary">
            取り込み前に戻す
            <span className="ml-2 font-normal text-text-muted">
              取り込みのたびに直前の盤面を自動保存しています（直近 5 回分）。
              間違えて取り込んだときはここから復元できます。
            </span>
          </p>
          <ul className="space-y-1">
            {snapshots.map((sn) => {
              const taken = new Date(sn.createdAt);
              const label = `${taken.getMonth() + 1}/${taken.getDate()} ${String(taken.getHours()).padStart(2, '0')}:${String(taken.getMinutes()).padStart(2, '0')}`;
              return (
                <li key={sn.id} className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="tnum font-medium text-text-primary">{label}</span>
                  <span className="text-text-muted">取り込み直前・訪問 {sn.visitsCount} 件</span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    disabled={restoring || busy || fetching || applying}
                    onClick={() => void restoreSnapshot(sn.id, label)}
                    data-testid={`inbound-snapshot-restore-${sn.id}`}
                  >
                    {restoring ? '復元中…' : 'この時点に戻す'}
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

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
      {/* ── 直近の取り込み (PO 要望 2026-08-09: どの週を取り込んだかを枠内で見える化)。
          mt-auto でカード最下部へ — flex-1 で伸びた余白が履歴スペースになる。 ── */}
      {inboundHistory.length > 0 && (
        <div
          className="mt-auto border-t border-border-default pt-3"
          data-testid="inbound-history-section"
        >
          <p className="mb-2 text-xs font-medium text-text-secondary">直近の取り込み</p>
          <ul className="space-y-1.5">
            {inboundHistory.map((h) => {
              const at = new Date(h.at);
              const atLabel = `${at.getMonth() + 1}/${at.getDate()} ${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
              const mon = new Date(h.weekStart + 'T00:00:00');
              return (
                <li key={h.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
                  <span className="tnum shrink-0 text-text-muted">{atLabel}</span>
                  <span className="tnum min-w-0 flex-1 truncate font-medium text-text-primary">
                    {fmtWeekLabel(mon)} の週
                  </span>
                  <span className="text-text-secondary">{h.opLabel}</span>
                  <span
                    className={
                      h.status === 'failed'
                        ? 'font-bold text-error'
                        : h.status === 'completed'
                          ? 'text-success'
                          : 'text-text-muted'
                    }
                  >
                    {h.status === 'completed' ? '成功' : h.status === 'failed' ? '失敗' : h.status}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

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
              {weekOffset > 0 && (
                <span className="block font-medium text-error" data-testid="confirm-future-week">
                  ⚠ 未来週への取り込みです。らく助側でこの週に計画中の内容は
                  カイポケの内容で上書きされます。
                </span>
              )}
              <span className="block text-error">
                らく助のスケジュールに実際に書き込まれます。この操作は Ctrl+Z
                の対象外です（定期パターンは変わりません）。
              </span>
              <span className="block text-text-secondary">
                取り込みの直前の盤面は自動保存されます。間違えた場合は
                「取り込み前に戻す」で復元できます（打刻実績が記録された週を除く）。
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

      {/* ⛔ NG スタッフ衝突 (取り込むが管理者に確認を促す・patient-ng-staff-design.md §9) */}
      {plan.replace && plan.replace.ngConflicts.length > 0 && (
        <Alert data-testid="ng-conflict-warning">
          <AlertTitle>
            ⛔ NGスタッフの組み合わせが含まれています（{plan.replace.ngConflicts.length}件）
          </AlertTitle>
          <AlertDescription>
            {plan.replace.ngConflicts
              .map(
                (c) =>
                  `${c.patientName || '利用者'}様 ← ${c.staffName || '担当者'}（${c.date}${
                    c.courseCode ? `・コース${c.courseCode}` : ''
                  }）`,
              )
              .join('・')}{' '}
            — カイポケの実態どおり取り込みますが、取り込み後に担当の調整をご検討ください。
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
