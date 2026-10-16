/**
 * 登録の位置のずれのお知らせ (設計 docs/plans/location-drift-align-2026-10-17.md §5)。
 *
 * 訪問モニターの訪問の詳細 (住所の行の下) と患者画面に出す。管理者だけ
 * (モニターは BE が管理者にだけ `location_drift` を入れる・患者画面は呼び出し側で絞る)。
 *
 *   ⚠ 登録の位置が、実際に QR を読んだ場所と約 440m 離れています（打刻 6 回・3 日分）
 *      [打刻の場所に合わせる]  [地図で見比べる ↗]
 *
 * [打刻の場所に合わせる] は確認のダイアログを出し、[合わせる] で実行する (座標は BE が
 * 計算し直す)。済んだら toast に [元に戻す] を付ける。文言は「合わせる」(「直す・修正」は
 * 使わない)。
 */
'use client';

import { ExternalLink, TriangleAlert } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { googleMapsCompareUrl } from '@/lib/maps';
import { useAlignPatientLocation, useRevertPatientLocation } from '@/lib/queries/patientLocation';
import type { LocationDrift } from '@/lib/schemas/monitor';
import { cn } from '@/lib/utils';

/** 距離の見せ方: 1km 未満は 10m 単位、それ以上は 0.1km 単位 (「約」を付けて出す)。 */
export function formatDriftDistance(m: number): string {
  if (m < 1000) return `${Math.round(m / 10) * 10}m`;
  return `${(Math.round(m / 100) / 10).toFixed(1)}km`;
}

/** 合わせた後の toast ([元に戻す] 付き) を出しておく時間。 */
export const UNDO_TOAST_MS = 10_000;

interface LocationDriftNoticeProps {
  patientId: string;
  /** ずれ (null / undefined なら何も出さない)。 */
  drift: LocationDrift | null | undefined;
  /** 今の登録座標 (経路の出発地)。無ければ提案の位置だけを地図で開く。 */
  registered: { lat: number; lng: number } | null;
  className?: string;
}

export function LocationDriftNotice({ drift, ...rest }: LocationDriftNoticeProps) {
  // ずれが無いときは何も描かない (query の仕組みも呼ばない)。
  if (!drift) return null;
  return <LocationDriftNoticeBody drift={drift} {...rest} />;
}

function LocationDriftNoticeBody({
  patientId,
  drift,
  registered,
  className,
}: LocationDriftNoticeProps & { drift: LocationDrift }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const align = useAlignPatientLocation(patientId);
  const revert = useRevertPatientLocation(patientId);

  const count = `打刻 ${drift.scans} 回・${drift.days} 日分`;
  const message =
    drift.distance_m != null
      ? `登録の位置が、実際に QR を読んだ場所と約 ${formatDriftDistance(drift.distance_m)} 離れています（${count}）`
      : `登録の位置がまだありません。実際に QR を読んだ場所に合わせられます（${count}）`;
  const compareUrl = googleMapsCompareUrl(registered, {
    lat: drift.suggested_lat,
    lng: drift.suggested_lng,
  });

  const handleRevert = () => {
    // toast の [元に戻す] はお知らせが消えた後 (再取得でずれが無くなった後) にも押される。
    revert
      .mutateAsync()
      .then(() => toast.success('登録の位置を元に戻しました'))
      .catch((e: unknown) => toast.error(`元に戻せませんでした: ${apiErrorMessage(e)}`));
  };

  const handleAlign = async () => {
    try {
      await align.mutateAsync();
      setConfirmOpen(false);
      toast.success('登録の位置を合わせました', {
        // 押す間があるよう 10 秒出す (既定は短い)。
        duration: UNDO_TOAST_MS,
        action: { label: '元に戻す', onClick: handleRevert },
      });
    } catch (e) {
      // BE の理由 (409「ずれは見つかりませんでした」など) をそのまま見せる。
      toast.error(`合わせられませんでした: ${apiErrorMessage(e)}`);
    }
  };

  return (
    <div
      className={cn(
        'rounded border border-border-warning bg-warning-bg p-3 text-[13px] leading-relaxed text-warning-strong',
        className,
      )}
      role="status"
      data-testid="location-drift-notice"
    >
      <p className="m-0 flex items-start gap-1">
        <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>{message}</span>
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onClick={() => setConfirmOpen(true)}
          disabled={align.isPending}
          data-testid="location-drift-align"
        >
          打刻の場所に合わせる
        </Button>
        <a
          href={compareUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 rounded-md border border-brand-primary bg-bg-base px-2 py-1 text-xs font-semibold text-brand-primary-hover hover:bg-brand-primary-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
          data-testid="location-drift-compare"
        >
          地図で見比べる
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          <span className="sr-only">（新しいタブで開きます）</span>
        </a>
      </div>

      <Dialog open={confirmOpen} onOpenChange={(o) => !align.isPending && setConfirmOpen(o)}>
        <DialogContent data-testid="location-drift-confirm">
          <DialogHeader>
            <DialogTitle>登録の位置を打刻の場所に合わせますか？</DialogTitle>
            <DialogDescription>
              次の打刻から、この位置で判定します。過去の打刻の判定は変わりません。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={align.isPending}
            >
              やめる
            </Button>
            <Button
              onClick={() => void handleAlign()}
              disabled={align.isPending}
              data-testid="location-drift-confirm-align"
            >
              {align.isPending ? '合わせています…' : '合わせる'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
