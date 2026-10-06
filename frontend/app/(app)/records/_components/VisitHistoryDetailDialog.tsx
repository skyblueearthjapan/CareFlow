'use client';

/**
 * 打刻履歴の詳細ダイアログ（設計 `visit-history-design-2026-09-30.md` §4 /
 * `actual-time-adjust-design-2026-09-30.md` §8-2 / モックの詳細ダイアログ）。
 *
 * 到着・退出・滞在のタイルと、訪問した看護師・予定の担当・記録の方法を出す。
 * 到着の記録がある訪問には「実績の時刻を合わせる」枠を出し、到着 / 退出それぞれの
 * 実績時刻を合わせる・読取時刻に戻す。予定は動かさない。打刻なしの訪問には、管理者だけ
 * 「到着・退出を手で入れる」枠（`pc-actual-time-edit-design-2026-10-06.md` D2）。
 * 枠は訪問モニターと共通の部品（`components/records/ActualTimeAdjustBox`）。
 *
 * 合わせられるかどうか（`adjust_allowed`）と時刻の範囲は BE が決める。画面は範囲を
 * 複製せず、422 / 403 / 409 の `detail` をそのまま出す。
 *
 * 文言は「合わせる」「調整」（遅れて記録されるのは看護師の誤りではない — PO 決定）。
 * 文字・余白は `add-visit-anywhere-design.md` §3-5（本文 14px・補足 12px まで・
 * 入力の高さ 36px）。
 */

import { useState, type ReactNode } from 'react';

import { ActualTimeAdjustBox } from '@/components/records/ActualTimeAdjustBox';
import { SignatureViewer } from '@/components/records/SignatureViewer';
import { targetFromHistoryRow } from '@/components/records/actualTimeAdjust';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { jstHm, lateDeliveryLabel } from '@/lib/format/actualTime';
import { useCheckinSettingsPublic } from '@/lib/queries/checkinSettings';
import { actualTimeLimitsFrom } from '@/lib/schemas/checkinSettings';
import type { ActualTimeKind, VisitHistoryRow } from '@/lib/queries/visit-history';

import {
  ACTUAL_KIND_LABEL,
  formatAdjustedAt,
  formatHistoryDateLong,
  isLocationReview,
  plannedMinutes,
  plannedRange,
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

/**
 * 合わせられない訪問で、無効化した操作に添える理由。
 *
 * BE の規則（設計 §6-1）: 削除済みの訪問は誰も合わせられない。管理者はそれ以外すべて、
 * スタッフは自分が担当または記録した訪問で 7 日前まで (日数は `checkin_settings` の設定・
 * `windowDays`)。つまり管理者に `false` が返るのは削除済みのときだけなので、スタッフ向けの
 * 理由（担当・日数）を管理者に見せない。
 */
function notAllowedReason(
  row: VisitHistoryRow,
  viewerIsAdmin: boolean,
  windowDays: number,
): string {
  // 項目そのものが無い応答（古い BE）は、理由を決めつけない。
  if (row.adjust_allowed == null) return 'この訪問の実績は、いまは合わせられません';
  if (viewerIsAdmin) return 'この訪問は削除されているため、実績の時刻は合わせられません';
  return `この訪問の実績は合わせられません（合わせられるのは、自分が担当または記録した訪問で、${windowDays} 日前までのものです。削除された訪問も合わせられません）`;
}

function Tile({ label, value, note }: { label: string; value: ReactNode; note: string }) {
  return (
    <div className="rounded-lg border border-border-default px-3 py-2.5">
      <div className="text-xs text-text-secondary">{label}</div>
      <div className="tnum text-2xl font-bold leading-snug text-text-primary">{value}</div>
      <div className="min-h-4 text-xs text-text-secondary">{note}</div>
    </div>
  );
}

/** 調整後の注記に「遅れて届いた（…）」を添える（調整してあっても遅れて届いた事実は消さない）。 */
function withLate(note: string, late: string | null): string {
  return late ? `${note}・${late}` : note;
}

/** その側（到着 / 退出）の実績時刻・読取時刻・調整の有無。時刻は JST の `HH:MM`。 */
function sideOf(row: VisitHistoryRow, kind: ActualTimeKind) {
  const arrival = kind === 'arrival';
  const at = jstHm(arrival ? row.arrival_at : row.departure_at);
  const adjusted = !!(arrival ? row.arrival_adjusted : row.departure_adjusted);
  const manual = !!(arrival ? row.arrival_manual : row.departure_manual);
  // 読取時刻の項目が無い応答（古い BE）は、調整が無ければ実績 = 読取。
  const readAt =
    jstHm(arrival ? row.arrival_read_at : row.departure_read_at) ??
    (adjusted || manual ? null : at);
  // 圏外で退避して後から届いた打刻 —「遅れて届いた（10/2 8:30 受信）」。
  const late = lateDeliveryLabel(
    arrival ? row.arrival_late_received_at : row.departure_late_received_at,
  );
  return { at, readAt, adjusted, manual, late };
}

export function VisitHistoryDetailDialog({
  row,
  onClose,
  viewerIsAdmin = false,
  onAdjusted,
}: VisitHistoryDetailDialogProps) {
  // スタッフが合わせられる期間 (日)。案内の文言に使う (判定そのものはサーバの adjust_allowed)。
  const { data: checkinPublic } = useCheckinSettingsPublic();
  const windowDays = actualTimeLimitsFrom(checkinPublic).staffAdjustWindowDays;
  const planned = row ? plannedRange(row) : null;
  const plannedMin = row ? plannedMinutes(row) : null;
  const qrLess = row?.checkin_source === 'manual';
  // サインで記録 (signature-checkin-design §5-1 Q2): 退出 =「サイン」、そのときの
  // ボタンで記録した到着 =「QRなし（サイン）」。
  const signed = row?.departure_source === 'signature';
  // 開いているサインの ID (押した行のもの)。行を切り替えても、別の行の画像を勝手に
  // 取りに行かない (取りに行くたびに見た記録が残る)。
  const [viewedSignatureId, setViewedSignatureId] = useState<string | null>(null);
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
                  : arr.manual
                    ? '手入力（読み取りなし）'
                    : arr.adjusted
                      ? withLate(`調整後（読取 ${arr.readAt ?? '—'}）`, arr.late)
                      : qrLess
                        ? signed
                          ? 'QRなし（サイン）'
                          : '手入力の時刻'
                        : (arr.late ?? 'QR 読取時刻')
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
                      ? withLate(`調整後（読取 ${dep.readAt ?? '—'}）`, dep.late)
                      : (dep.late ?? (signed ? 'サイン' : '退出の記録'))
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
              {signed
                ? `到着 ${qrLess ? 'QRなし（サイン）' : 'QR 読み取り'}・退出 サイン`
                : qrLess
                  ? 'QR なし（手入力）'
                  : row.checkin_source === 'qr'
                    ? 'QR 読み取り'
                    : '—'}
              {isLocationReview(row.match_status) && <Badge variant="warning">場所 要確認</Badge>}
            </dd>
            {row.signature_id && (
              <>
                <dt className="text-text-secondary">サイン</dt>
                <dd className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setViewedSignatureId(row.signature_id ?? null)}
                    className="inline-flex h-9 items-center rounded-md border border-brand-primary px-3 text-sm font-semibold text-brand-primary-hover hover:bg-brand-primary-50"
                    data-testid="history-detail-view-signature"
                  >
                    サインを見る
                  </button>
                  <span className="text-xs text-text-secondary">
                    管理者も職員も、過去の日も見られます。見た記録が残ります。
                  </span>
                </dd>
              </>
            )}
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

          {/* 合わせる枠（部品はモニターの詳細パネルと共通）。打刻なしの訪問は、管理者だけ
              「到着・退出を手で入れる」（pc-actual-time-edit-design D2）。 */}
          <ActualTimeAdjustBox
            // 取り直した一覧で時刻が変わったら、入力欄を新しい値から作り直す。
            key={[
              row.visit_id,
              row.arrival_at,
              row.departure_at,
              arr.adjusted,
              dep.adjusted,
              arr.manual,
              dep.manual,
            ].join('|')}
            target={targetFromHistoryRow(row)}
            naReason={notAllowedReason(row, viewerIsAdmin, windowDays)}
            onAdjusted={onAdjusted}
          />
          <SignatureViewer
            signatureId={
              viewedSignatureId !== null && viewedSignatureId === row.signature_id
                ? viewedSignatureId
                : null
            }
            onClose={() => setViewedSignatureId(null)}
            title={`サイン — ${row.patient_name ?? '—'} 様`}
            meta={
              dep.at
                ? `${formatHistoryDateLong(row.visit_date)} 退出 ${dep.at} にサイン`
                : undefined
            }
          />
        </DialogContent>
      )}
    </Dialog>
  );
}
