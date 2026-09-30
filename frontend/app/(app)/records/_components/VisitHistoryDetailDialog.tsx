'use client';

/**
 * 打刻履歴の詳細ダイアログ（設計 `visit-history-design-2026-09-30.md` §4 /
 * `actual-time-adjust-design-2026-09-30.md` §8-2 / モックの詳細ダイアログ）。
 *
 * 到着・退出・滞在のタイルと、訪問した看護師・予定の担当・記録の方法を出す。
 * 到着の記録がある訪問には「実績の時刻を合わせる」枠を出し、到着 / 退出それぞれの
 * 実績時刻を合わせる・読取時刻に戻す。予定は動かさない。
 *
 * 合わせられるかどうか（`adjust_allowed`）と時刻の範囲は BE が決める。画面は範囲を
 * 複製せず、422 / 403 / 409 の `detail` をそのまま出す。
 *
 * 文言は「合わせる」「調整」（遅れて記録されるのは看護師の誤りではない — PO 決定）。
 * 文字・余白は `add-visit-anywhere-design.md` §3-5（本文 14px・補足 12px まで・
 * 入力の高さ 36px）。
 */

import { useState, type ReactNode } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { jstHm } from '@/lib/format/actualTime';
import {
  useAdjustVisitActualTime,
  useResetVisitActualTime,
  type ActualTimeKind,
  type AdjustReasonCode,
  type VisitHistoryRow,
} from '@/lib/queries/visit-history';

import {
  ACTUAL_KIND_LABEL,
  ADJUST_REASON_LABEL,
  ADJUST_REASON_OPTIONS,
  adjustmentOf,
  formatAdjustedAt,
  formatHistoryDateLong,
  isLocationReview,
  plannedMinutes,
  plannedRange,
  reasonCodeOfAdjustment,
} from './visitHistoryFormat';

interface VisitHistoryDetailDialogProps {
  /** 開く行。null で閉じる。 */
  row: VisitHistoryRow | null;
  onClose: () => void;
  /** 見ている人が管理者か。合わせられない理由の文言を分けるのに使う。 */
  viewerIsAdmin?: boolean;
  /** 実績の時刻を合わせた / 戻した直後（一覧の取り直しが始まった時点）に呼ぶ。 */
  onAdjusted?: (visitId: string) => void;
}

/** 自由記述の上限（字）。BE の `reason_text` と同じ。 */
const REASON_TEXT_MAX = 200;

/**
 * 合わせられない訪問で、無効化した操作に添える理由。
 *
 * BE の規則（設計 §6-1）: 削除済みの訪問は誰も合わせられない。管理者はそれ以外すべて、
 * スタッフは自分が担当または記録した訪問で 7 日前まで。つまり管理者に `false` が返るのは
 * 削除済みのときだけなので、スタッフ向けの理由（担当・7 日）を管理者に見せない。
 */
function notAllowedReason(row: VisitHistoryRow, viewerIsAdmin: boolean): string {
  // 項目そのものが無い応答（古い BE）は、理由を決めつけない。
  if (row.adjust_allowed == null) return 'この訪問の実績は、いまは合わせられません';
  if (viewerIsAdmin) return 'この訪問は削除されているため、実績の時刻は合わせられません';
  return 'この訪問の実績は合わせられません（合わせられるのは、自分が担当または記録した訪問で、7 日前までのものです。削除された訪問も合わせられません）';
}

/** 無効でも title（理由）が出るようにする。既定の Button は無効時にポインタを切る。 */
const disabledHintCls = 'disabled:pointer-events-auto disabled:cursor-not-allowed';

const fieldCls =
  'h-9 rounded-md border border-border-default bg-bg-base px-2 text-sm text-text-primary disabled:cursor-not-allowed disabled:opacity-50';

function Tile({ label, value, note }: { label: string; value: ReactNode; note: string }) {
  return (
    <div className="rounded-lg border border-border-default px-3 py-2.5">
      <div className="text-xs text-text-secondary">{label}</div>
      <div className="tnum text-2xl font-bold leading-snug text-text-primary">{value}</div>
      <div className="min-h-4 text-xs text-text-secondary">{note}</div>
    </div>
  );
}

/** その側（到着 / 退出）の実績時刻・読取時刻・調整の有無。時刻は JST の `HH:MM`。 */
function sideOf(row: VisitHistoryRow, kind: ActualTimeKind) {
  const arrival = kind === 'arrival';
  const at = jstHm(arrival ? row.arrival_at : row.departure_at);
  const adjusted = !!(arrival ? row.arrival_adjusted : row.departure_adjusted);
  const manual = !arrival && !!row.departure_manual;
  // 読取時刻の項目が無い応答（古い BE）は、調整が無ければ実績 = 読取。
  const readAt =
    jstHm(arrival ? row.arrival_read_at : row.departure_read_at) ??
    (adjusted || manual ? null : at);
  return { at, readAt, adjusted, manual };
}

export function VisitHistoryDetailDialog({
  row,
  onClose,
  viewerIsAdmin = false,
  onAdjusted,
}: VisitHistoryDetailDialogProps) {
  const planned = row ? plannedRange(row) : null;
  const plannedMin = row ? plannedMinutes(row) : null;
  const qrLess = row?.checkin_source === 'manual';
  const arr = row ? sideOf(row, 'arrival') : null;
  const dep = row ? sideOf(row, 'departure') : null;
  const history = (row?.adjustments ?? []).filter(
    (a) => a.kind === 'arrival' || a.kind === 'departure',
  );

  return (
    <Dialog
      open={row !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {row && arr && dep && (
        <DialogContent className="max-w-2xl gap-4 text-sm" data-testid="history-detail-dialog">
          <div className="space-y-1 pr-8">
            <DialogTitle>{row.patient_name ?? '—'} 様</DialogTitle>
            <DialogDescription className="text-text-secondary">
              {formatHistoryDateLong(row.visit_date)}　予定 {planned ?? 'なし（予定外の訪問）'}
              {row.office_name ? `　${row.office_name}` : ''}
            </DialogDescription>
          </div>

          <div className="grid grid-cols-3 gap-2.5">
            <Tile
              label="到着"
              value={arr.at ?? '—'}
              note={
                !arr.at
                  ? '打刻なし'
                  : arr.adjusted
                    ? `調整後（読取 ${arr.readAt ?? '—'}）`
                    : qrLess
                      ? '手入力の時刻'
                      : 'QR 読取時刻'
              }
            />
            <Tile
              label="退出"
              value={dep.at ?? '—'}
              note={
                !dep.at
                  ? row.state === 'in_progress'
                    ? '訪問中'
                    : '打刻なし'
                  : dep.manual
                    ? '手入力（読み取りなし）'
                    : dep.adjusted
                      ? `調整後（読取 ${dep.readAt ?? '—'}）`
                      : '退出の記録'
              }
            />
            <Tile
              label="滞在"
              value={
                row.stay_minutes == null ? (
                  '—'
                ) : (
                  <>
                    {row.stay_minutes}
                    <span className="ml-1 text-sm font-medium">分</span>
                  </>
                )
              }
              note={plannedMin == null ? '' : `予定 ${plannedMin} 分`}
            />
          </div>

          <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1.5">
            <dt className="text-text-secondary">訪問した看護師</dt>
            <dd className="flex flex-wrap items-center gap-2">
              {row.actual_staff_name ?? '—'}
              {row.is_substitute && <Badge variant="info">代行</Badge>}
            </dd>
            <dt className="text-text-secondary">予定の担当</dt>
            <dd>{row.planned_staff_name ?? '—'}</dd>
            <dt className="text-text-secondary">記録の方法</dt>
            <dd className="flex flex-wrap items-center gap-2">
              {qrLess ? 'QR なし（手入力）' : row.checkin_source === 'qr' ? 'QR 読み取り' : '—'}
              {isLocationReview(row.match_status) && <Badge variant="warning">場所 要確認</Badge>}
            </dd>
            {(row.remarks?.length ?? 0) > 0 && (
              <>
                <dt className="text-text-secondary">備考</dt>
                <dd data-testid="history-detail-remarks">{(row.remarks ?? []).join('、')}</dd>
              </>
            )}
            {history.length > 0 && (
              <>
                <dt className="text-text-secondary">調整の履歴</dt>
                <dd className="space-y-1.5" data-testid="history-detail-adjustments">
                  {history.map((a) => {
                    const side = a.kind === 'arrival' ? arr : dep;
                    const reason = [a.reason_label, a.reason_text].filter(Boolean).join('・');
                    return (
                      <div key={a.kind}>
                        <div className="tnum">
                          {[formatAdjustedAt(a.created_at), a.by_name].filter(Boolean).join('　')}
                          {'　'}
                          {ACTUAL_KIND_LABEL[a.kind as ActualTimeKind]}{' '}
                          {side.readAt ? `読取 ${side.readAt}` : '読み取りなし'} → {side.at ?? '—'}
                        </div>
                        {reason && <div className="text-text-secondary">理由: {reason}</div>}
                      </div>
                    );
                  })}
                </dd>
              </>
            )}
          </dl>

          {/* 到着の記録が無い訪問は合わせる対象が無い（到着を後から入れるのは対象外）。 */}
          {arr.at && (
            <AdjustBox
              // 取り直した一覧で時刻が変わったら、入力欄を新しい値から作り直す。
              key={[
                row.visit_id,
                row.arrival_at,
                row.departure_at,
                arr.adjusted,
                dep.adjusted,
                dep.manual,
              ].join('|')}
              row={row}
              naReason={notAllowedReason(row, viewerIsAdmin)}
              onAdjusted={onAdjusted}
            />
          )}
        </DialogContent>
      )}
    </Dialog>
  );
}

/** 「実績の時刻を合わせる」枠。到着 / 退出を 1 行ずつ、別々に保存する。 */
function AdjustBox({
  row,
  naReason,
  onAdjusted,
}: {
  row: VisitHistoryRow;
  /** 合わせられないときに添える理由。 */
  naReason: string;
  onAdjusted?: (visitId: string) => void;
}) {
  const adjust = useAdjustVisitActualTime();
  const reset = useResetVisitActualTime();
  const [error, setError] = useState<string | null>(null);
  const allowed = row.adjust_allowed === true;
  const pending = adjust.isPending || reset.isPending;

  const save = async (
    kind: ActualTimeKind,
    time: string,
    reasonCode: AdjustReasonCode,
    reasonText: string,
  ) => {
    setError(null);
    try {
      await adjust.mutateAsync({ visitId: row.visit_id, kind, time, reasonCode, reasonText });
      onAdjusted?.(row.visit_id);
      toast.success(`${ACTUAL_KIND_LABEL[kind]}を ${time} に合わせました`);
    } catch (e) {
      // 範囲外（422）・期間外（403）・記録なし（409）の理由は BE の文言をそのまま出す。
      setError(apiErrorMessage(e));
    }
  };

  const undo = async (kind: ActualTimeKind, manual: boolean) => {
    setError(null);
    try {
      await reset.mutateAsync({ visitId: row.visit_id, kind });
      onAdjusted?.(row.visit_id);
      toast.success(manual ? '手入力の退出時刻を消しました' : '読取時刻に戻しました');
    } catch (e) {
      setError(apiErrorMessage(e));
    }
  };

  return (
    <section
      className="rounded-xl border border-dashed border-brand-primary bg-brand-primary-50 px-4 py-3.5"
      title={allowed ? undefined : naReason}
      data-testid="history-adjust-box"
    >
      <h3 className="text-base font-bold text-text-primary">実績の時刻を合わせる</h3>
      <p className="mb-2 mt-0.5 text-xs text-text-secondary">
        お宅に着いてから QR
        を読み取るまでに時間があったときなどに、実績の時刻を実際に合わせます。読み取った時刻は消さずに残り、予定は変わりません。
      </p>
      {!allowed && (
        <p className="mb-2 text-xs font-medium text-text-secondary" data-testid="history-adjust-na">
          {naReason}
        </p>
      )}

      <AdjustRow
        row={row}
        kind="arrival"
        allowed={allowed}
        naReason={naReason}
        pending={pending}
        onSave={save}
        onReset={undo}
      />
      <AdjustRow
        row={row}
        kind="departure"
        allowed={allowed}
        naReason={naReason}
        pending={pending}
        onSave={save}
        onReset={undo}
      />

      {error && (
        <p
          role="alert"
          className="mt-2 whitespace-pre-wrap rounded-md border border-border-error bg-error-bg px-3 py-2 text-sm text-error"
          data-testid="history-adjust-error"
        >
          {error}
        </p>
      )}
    </section>
  );
}

function AdjustRow({
  row,
  kind,
  allowed,
  naReason,
  pending,
  onSave,
  onReset,
}: {
  row: VisitHistoryRow;
  kind: ActualTimeKind;
  allowed: boolean;
  naReason: string;
  pending: boolean;
  onSave: (
    kind: ActualTimeKind,
    time: string,
    reasonCode: AdjustReasonCode,
    reasonText: string,
  ) => void;
  onReset: (kind: ActualTimeKind, manual: boolean) => void;
}) {
  const label = ACTUAL_KIND_LABEL[kind];
  const { at, readAt, adjusted, manual } = sideOf(row, kind);
  const current = adjustmentOf(row, kind);
  const options = ADJUST_REASON_OPTIONS[kind];

  // 理由の初期値: いまの調整の理由。無ければ到着は「インターホン待ち」、退出は読み取りの
  // 有無で「読み取りが後になった」/「読み取りなし」。
  const currentCode = reasonCodeOfAdjustment(current);
  const initialReason: AdjustReasonCode =
    currentCode && options.includes(currentCode)
      ? currentCode
      : kind === 'arrival'
        ? 'intercom_wait'
        : readAt
          ? 'read_later'
          : 'no_read';
  const initialText = current?.reason_text ?? '';

  const [time, setTime] = useState(at ?? '');
  const [reason, setReason] = useState<AdjustReasonCode>(initialReason);
  const [text, setText] = useState(initialText);

  const hasAdjustment = adjusted || manual;
  const timeChanged = time !== (at ?? '');
  const reasonChanged = reason !== initialReason || text.trim() !== initialText.trim();
  // 時刻が同じなら、いまの調整の理由を変えるときだけ保存できる。
  const dirty = timeChanged || (hasAdjustment && reasonChanged);
  const valid = /^\d{2}:\d{2}$/.test(time);
  const disabledTitle = allowed ? undefined : naReason;

  const submit = () => {
    // 読取時刻と同じ時刻にするのは「読取時刻に戻す」と同じ（調整を残さない）。
    if (adjusted && readAt && time === readAt) onReset(kind, false);
    else onSave(kind, time, reason, reason === 'other' ? text : '');
  };

  return (
    <div
      className="border-t border-brand-primary-light py-2.5"
      data-testid={`history-adjust-${kind}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-9 shrink-0 text-sm font-bold text-text-primary">{label}</span>
        <Input
          type="time"
          aria-label={`${label}の時刻`}
          className="tnum h-9 w-[124px] px-2"
          value={time}
          disabled={!allowed || pending}
          title={disabledTitle}
          onChange={(e) => setTime(e.target.value)}
        />
        <select
          aria-label={`${label}の理由`}
          className={fieldCls}
          value={reason}
          disabled={!allowed || pending}
          title={disabledTitle}
          onChange={(e) => setReason(e.target.value as AdjustReasonCode)}
        >
          {options.map((code) => (
            <option key={code} value={code}>
              {ADJUST_REASON_LABEL[code]}
            </option>
          ))}
        </select>
        <Button
          type="button"
          size="sm"
          className={`h-9 ${disabledHintCls}`}
          disabled={!allowed || pending || !valid || !dirty}
          title={disabledTitle}
          onClick={submit}
          data-testid={`history-adjust-save-${kind}`}
        >
          {label}を保存
        </Button>
        {hasAdjustment && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={`h-9 ${disabledHintCls}`}
            disabled={!allowed || pending}
            title={disabledTitle}
            onClick={() => onReset(kind, manual)}
            data-testid={`history-adjust-reset-${kind}`}
          >
            {manual ? '手入力の時刻を消す' : '読取時刻に戻す'}
          </Button>
        )}
      </div>
      {reason === 'other' && (
        <Input
          type="text"
          aria-label={`${label}の理由（自由記述）`}
          placeholder={`理由（任意・${REASON_TEXT_MAX} 字まで）`}
          maxLength={REASON_TEXT_MAX}
          className="mt-2 h-9"
          value={text}
          disabled={!allowed || pending}
          title={disabledTitle}
          onChange={(e) => setText(e.target.value)}
        />
      )}
      <p
        className="tnum mt-1 pl-11 text-xs text-text-secondary"
        data-testid={`history-adjust-note-${kind}`}
      >
        {readAt
          ? `読取 ${readAt}${adjusted ? ' ・ 調整あり' : ''}`
          : kind === 'arrival'
            ? '調整あり'
            : manual
              ? '読み取りなし ・ 手入力の時刻'
              : '読み取りなし ・ 退出時刻を入れられます'}
      </p>
    </div>
  );
}
