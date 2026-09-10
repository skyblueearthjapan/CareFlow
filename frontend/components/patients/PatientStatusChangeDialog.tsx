'use client';

/**
 * PatientStatusChangeDialog — 患者ステータス変更の確認ダイアログ（Phase 1）。
 *
 * 正典 = `docs/plans/patient-status-schedule-design-2026-09-09.md` §3-6 / §6-C / §7-4。
 * 作法 = `add-visit-anywhere-design.md` §3-5（幅・14px・h-9・行全体クリック）。
 *
 * 位置づけ:
 *   ステータスは「予定の蛇口」。稼働中 → それ以外にした瞬間に当日以降の予定が取り消され、
 *   稼働中に戻した瞬間に固定訪問の型から作り直される。**人が決める瞬間は保存時の 1 回だけ**
 *   なので、消える件数・特別訪問週間・復帰時の作成件数をここで必ず見せる。
 *
 * 重要な取り決め:
 *   - 閉じた（キャンセル / オーバーレイ / Esc）ら **API は 1 本も飛ばない**（テストで固定）。
 *     影響件数の GET もダイアログが開いている間だけ有効。
 *   - 特別訪問週間の扱い（残す / 終了する）は PO 決定 Q12 により
 *     「固定ルールにしない・必ず確認して管理者の判断に従う」。既定は **残す**で、
 *     見落とされない位置（枠で囲った専用ブロック）に出す。
 *   - ステータスの実体変更は `POST /patients/{id}/status-change` が行う。呼び出し元は
 *     `onDone(result)` を受けてから **status を除いた** PATCH を流す
 *     （`lib/hooks/usePatientStatusGate.ts`）。
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
import { jstDateString, formatJstMonthDay } from '@/lib/format/patientStatus';
import { useChangePatientStatus, usePatientStatusImpact } from '@/lib/queries/patients';
import {
  STATUS_LABEL,
  statusChangeDirection,
  type PatientStatus,
  type StatusDirection,
} from '@/lib/schemas/patient';
import type {
  StatusChangeRequest,
  StatusChangeResult,
  StatusImpact,
} from '@/lib/schemas/patientStatus';

// ─── Props ───────────────────────────────────────────────────────────────────

export interface PatientStatusChangeDialogProps {
  open: boolean;
  patientId: string;
  patientName: string;
  /** 変更前のステータス（正規化済み）。 */
  fromStatus: PatientStatus;
  /** これから保存しようとしているステータス。 */
  toStatus: PatientStatus;
  /** キャンセル / オーバーレイ / Esc。API は飛ばさない。 */
  onCancel: () => void;
  /** status-change 成功。呼び出し元が残りのフォーム項目を PATCH する。 */
  onDone: (result: StatusChangeResult) => void;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** `visits.excluded` のキー → 日本語。未知キーはそのまま出す（BE 追加に追従）。 */
const EXCLUDED_LABEL: Record<string, string> = {
  checked_in: '打刻済み',
  in_progress: '訪問中',
  completed: '実施済み',
  week_pinned: '青ピン（今週固定）',
};

function excludedText(excluded: Record<string, number>): string | null {
  const parts = Object.entries(excluded)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${EXCLUDED_LABEL[k] ?? k} ${n} 件`);
  return parts.length > 0 ? parts.join('・') : null;
}

/** 週ラベル。BE が `label`（例 `9/14週`）を返すが、欠けたら ISO 週で代替する。 */
function weekLabel(w: { label?: string; iso_year: number; iso_week: number }): string {
  return w.label && w.label.length > 0 ? w.label : `${w.iso_year} 年 W${w.iso_week}`;
}

/** `2026-09-03` → `9/3`。 */
function mdLabel(dateStr: string | null | undefined): string {
  return formatJstMonthDay(dateStr) ?? dateStr ?? '';
}

/** ラジオ / チェックの行（ラベル全体がクリック領域・設計 §3-5）。 */
const rowCls =
  'flex cursor-pointer items-start gap-2 rounded border border-border-default px-3 py-2 text-sm hover:bg-bg-muted';

// ─── Component ───────────────────────────────────────────────────────────────

export function PatientStatusChangeDialog({
  open,
  patientId,
  patientName,
  fromStatus,
  toStatus,
  onCancel,
  onDone,
}: PatientStatusChangeDialogProps) {
  const direction: StatusDirection = statusChangeDirection(fromStatus, toStatus);

  const [when, setWhen] = React.useState<'today' | 'tomorrow'>('today');
  const [specialAction, setSpecialAction] = React.useState<'keep' | 'end'>('keep');
  const [regenerate, setRegenerate] = React.useState(true);
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  // 開き直すたびに既定へ戻す（前回「終了する」を選んだ状態が次の患者に残らない）。
  React.useEffect(() => {
    if (open) {
      setWhen('today');
      setSpecialAction('keep');
      setRegenerate(true);
      setErrorMessage(null);
    }
  }, [open]);

  const today = jstDateString(0);
  const tomorrow = jstDateString(1);
  const fromDate = when === 'today' ? today : tomorrow;

  // 特別訪問週間の選択も BE に渡す: `end` のとき `visits.total` は ⭐ 配置分を
  // **含んだ** 数字で返る。FE で placed_future_visits を足さない（設計 §7-3 (a)）。
  const impactQuery = usePatientStatusImpact(patientId, toStatus, fromDate, {
    enabled: open,
    specialPeriodAction: specialAction,
  });
  const impact: StatusImpact | undefined = impactQuery.data;

  const changeMut = useChangePatientStatus(patientId);

  const handleConfirm = async () => {
    setErrorMessage(null);
    const body: StatusChangeRequest =
      direction === 'reactivate'
        ? { status: toStatus, from_date: fromDate, regenerate }
        : direction === 'deactivate'
          ? { status: toStatus, from_date: fromDate, special_period_action: specialAction }
          : { status: toStatus, from_date: fromDate };
    try {
      const result = await changeMut.mutateAsync(body);
      onDone(result);
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : '不明なエラー');
    }
  };

  const busy = impactQuery.isLoading || changeMut.isPending;

  const headline =
    direction === 'reactivate'
      ? `${patientName}様を稼働中に戻します`
      : `${patientName}様を${STATUS_LABEL[toStatus]}にします`;

  const confirmLabel =
    direction === 'reactivate' ? '稼働中に戻す' : `${STATUS_LABEL[toStatus]}にする`;

  const description =
    direction === 'deactivate'
      ? '取り消す予定と特別訪問週間の扱いを確認してください。'
      : direction === 'reactivate'
        ? '固定訪問の型から作り直す予定を確認してください。'
        : '予定への影響はありません。';

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle data-testid="patient-status-headline">{headline}</DialogTitle>
          <DialogDescription
            className="text-sm text-text-secondary"
            data-testid="patient-status-desc"
          >
            {description}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm text-text-primary" data-testid="patient-status-body">
          {direction === 'none' ? (
            <p>
              {STATUS_LABEL[fromStatus]} から {STATUS_LABEL[toStatus]}{' '}
              に変更します。予定への影響はありません。
            </p>
          ) : impact?.direction === 'none' ? (
            <p data-testid="patient-status-already">
              {patientName}様はすでに{STATUS_LABEL[toStatus]}
              です（画面の表示が古い可能性があります）。 変更はありません。
            </p>
          ) : (
            <>
              {/* ── いつから ─────────────────────────────────────────── */}
              <fieldset className="space-y-2">
                <legend className="text-sm font-semibold text-text-secondary">
                  {direction === 'deactivate' ? '取り消す範囲' : '戻す範囲'}
                </legend>
                <label className={rowCls}>
                  <input
                    type="radio"
                    name="patient-status-when"
                    className="mt-0.5 h-4 w-4"
                    checked={when === 'today'}
                    onChange={() => setWhen('today')}
                    data-testid="patient-status-when-today"
                  />
                  <span>今日から（{mdLabel(today)}〜）</span>
                </label>
                <label className={rowCls}>
                  <input
                    type="radio"
                    name="patient-status-when"
                    className="mt-0.5 h-4 w-4"
                    checked={when === 'tomorrow'}
                    onChange={() => setWhen('tomorrow')}
                    data-testid="patient-status-when-tomorrow"
                  />
                  <span>明日から（{mdLabel(tomorrow)}〜・今日の予定は残す）</span>
                </label>
              </fieldset>

              {/* ── 影響 ─────────────────────────────────────────────── */}
              {impactQuery.isLoading ? (
                <p className="text-text-muted" data-testid="patient-status-impact-loading">
                  影響を確認中…
                </p>
              ) : impactQuery.isError ? (
                <div
                  className="space-y-2 rounded-md border border-amber-400 bg-amber-50/60 px-3 py-2"
                  data-testid="patient-status-impact-error"
                >
                  <p className="text-amber-900">
                    ⚠ 影響件数を取得できませんでした（
                    {impactQuery.error instanceof Error
                      ? impactQuery.error.message
                      : '不明なエラー'}
                    ）。このまま進めることもできますが、件数は確認できません。
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9"
                    onClick={() => void impactQuery.refetch()}
                    disabled={impactQuery.isFetching}
                    data-testid="patient-status-impact-retry"
                  >
                    {impactQuery.isFetching ? '再取得中…' : '再試行'}
                  </Button>
                </div>
              ) : impact ? (
                direction === 'deactivate' ? (
                  <DeactivateSummary impact={impact} />
                ) : (
                  <ReactivateSummary
                    impact={impact}
                    regenerate={regenerate}
                    onRegenerateChange={setRegenerate}
                  />
                )
              ) : null}

              {/* ── 特別訪問週間（必ず目に入る位置・PO 決定 Q12） ────── */}
              {direction === 'deactivate' && impact?.special_period ? (
                <fieldset
                  className="space-y-2 rounded-md border-2 border-amber-400 bg-amber-50/60 px-3 py-2"
                  data-testid="patient-status-special-period"
                >
                  <legend className="px-1 text-sm font-semibold text-amber-900">
                    特別訪問週間（{mdLabel(impact.special_period.start_date)}〜
                    {mdLabel(impact.special_period.end_date)}・○ {impact.special_period.pool_marks}{' '}
                    枚・配置済み {impact.special_period.placed_marks} 件）
                  </legend>
                  <p className="text-sm text-amber-900">
                    どうするか選んでください（既定は「残す」）。
                  </p>
                  <label className={`${rowCls} bg-bg-base`}>
                    <input
                      type="radio"
                      name="patient-status-special"
                      className="mt-0.5 h-4 w-4"
                      checked={specialAction === 'keep'}
                      onChange={() => setSpecialAction('keep')}
                      data-testid="patient-status-special-keep"
                    />
                    <span>残す（既定・○ はプールに残り、退院後にそのまま使えます）</span>
                  </label>
                  <label className={`${rowCls} bg-bg-base`}>
                    <input
                      type="radio"
                      name="patient-status-special"
                      className="mt-0.5 h-4 w-4"
                      checked={specialAction === 'end'}
                      onChange={() => setSpecialAction('end')}
                      data-testid="patient-status-special-end"
                    />
                    <span>
                      終了する（○ {impact.special_period.pool_marks} 枚を取消・配置済みの今後{' '}
                      {impact.special_period.placed_future_visits} 件も取消）
                    </span>
                  </label>
                </fieldset>
              ) : null}

              {direction === 'reactivate' ? (
                <p className="text-xs text-text-muted" data-testid="patient-status-special-hint">
                  退院直後に訪問回数を増やす場合は、保存後に患者画面の「特別訪問週間」から設定してください。
                </p>
              ) : null}
            </>
          )}

          {errorMessage ? (
            <p className="text-sm text-red-700" data-testid="patient-status-error">
              変更に失敗しました: {errorMessage}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={changeMut.isPending}
            data-testid="patient-status-cancel"
          >
            やめる
          </Button>
          <Button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={busy}
            data-testid="patient-status-confirm"
          >
            {changeMut.isPending ? '変更中…' : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Sub views ───────────────────────────────────────────────────────────────

function DeactivateSummary({ impact }: { impact: StatusImpact }) {
  const excluded = excludedText(impact.visits.excluded);
  return (
    <section
      className="space-y-1 rounded-md border border-border-default bg-bg-muted/40 px-3 py-2"
      data-testid="patient-status-impact"
    >
      <p className="font-medium text-text-primary" data-testid="patient-status-cancel-total">
        取消する予定 {impact.visits.total} 件
      </p>
      {impact.visits.by_week.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-text-secondary">
          {impact.visits.by_week.map((w) => (
            <li key={`${w.iso_year}-${w.iso_week}`}>
              {weekLabel(w)} {w.count} 件
            </li>
          ))}
        </ul>
      ) : null}
      {impact.visits.pair_groups > 0 ? (
        <p className="text-text-secondary">
          2 名体制のペア {impact.visits.pair_groups} 組はまとめて取消します
        </p>
      ) : null}
      {excluded ? (
        <p className="text-text-secondary" data-testid="patient-status-excluded">
          取消しない予定: {excluded}
        </p>
      ) : null}
      <p className="text-text-secondary">
        固定訪問の型は残ります（{impact.fixed_visit_rows} 行・復帰時にそのまま使えます）
      </p>
      {impact.pending_requests > 0 ? (
        <p className="text-text-secondary" data-testid="patient-status-pending-requests">
          未処理の申請 {impact.pending_requests} 件は自動で却下します
        </p>
      ) : null}
      <p className="text-text-secondary" data-testid="patient-status-kaipoke">
        カイポケ送信対象 {impact.kaipoke_weeks} 週（次の突合で削除差分になります）
      </p>
      {/* op-log の「戻る」はステータス連動の取消には効かない (BE 決定)。
          間違えたときの出口を必ず書いておく。 */}
      <p className="text-text-secondary" data-testid="patient-status-undo-note">
        取り消した予定は盤面の「戻る」では戻せません。元に戻すには患者様のステータスを稼働中に戻してください。
      </p>
    </section>
  );
}

function ReactivateSummary({
  impact,
  regenerate,
  onRegenerateChange,
}: {
  impact: StatusImpact;
  regenerate: boolean;
  onRegenerateChange: (next: boolean) => void;
}) {
  const weeks = impact.regenerate?.weeks ?? [];
  const total = impact.regenerate?.total ?? 0;
  return (
    <section className="space-y-2" data-testid="patient-status-impact">
      <label className={rowCls}>
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4"
          checked={regenerate}
          onChange={(e) => onRegenerateChange(e.target.checked)}
          data-testid="patient-status-regenerate"
        />
        <span>固定訪問の型から予定を作る（既定 ON）</span>
      </label>
      <div className="space-y-1 rounded-md border border-border-default bg-bg-muted/40 px-3 py-2">
        <p className="font-medium text-text-primary" data-testid="patient-status-regen-total">
          作る予定 {total} 件
        </p>
        {weeks.length > 0 ? (
          <ul className="list-disc space-y-0.5 pl-5 text-text-secondary">
            {weeks.map((w) => (
              <li key={`${w.iso_year}-${w.iso_week}`}>
                {weekLabel(w)} {w.count} 件
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-text-secondary">生成済みの週がないため、次の週生成から入ります</p>
        )}
      </div>
    </section>
  );
}
