'use client';

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Clock, QrCode } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { toast } from '@/components/ui/sonner';
import { CheckInButton } from '@/components/mobile/CheckInButton';
import { MobileSection } from '@/components/mobile/MobileSection';
import { MobileVisitCard } from '@/components/mobile/MobileVisitCard';
import { QrScanner } from '@/components/mobile/QrScanner';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { extractQrToken } from '@/lib/qr-token';
import { useCheckinFlush } from '@/lib/queries/checkinFlush';
import { todayIso, useMyVisits, type MyVisit } from '@/lib/queries/me';

function isUnvisited(v: MyVisit): boolean {
  return v.status === 'planned' || v.status === '';
}

export default function MobileTodayPage() {
  const today = todayIso();
  const router = useRouter();
  const {
    data: visits,
    isLoading,
    isError,
    error,
  } = useMyVisits({
    date: today,
  });

  const sorted = [...(visits ?? [])].sort((a, b) => a.start_time.localeCompare(b.start_time));

  // 圏外で退避した打刻 (訪問詳細の到着/退出・/q の予定外) をここで再送する。
  // 一覧は退避後に必ず戻ってくる場所なので、「電波が戻り次第、自動で送信します」の
  // 主トリガーになる (マウント時 + online イベント)。
  const { pendingCount } = useCheckinFlush();

  // 本日の担当訪問が無い患者 (担当外・予定外) の QR を読むための独立入口。
  // 読み取ったら振り分けは既存の `/q/{token}` に全部任せる (担当 visit 直行 /
  // 代行 / 予定外の選択 / エラー案内)。この画面は「読んで飛ばす」だけに徹する。
  const [scanning, setScanning] = useState(false);

  const handleScanned = useCallback(
    (raw: string) => {
      // QrScanner 側でも抽出済みだが、生トークン/URL のどちらで来ても壊れないよう
      // ここでも通す (extractQrToken は生トークンを素通しする冪等な関数)。
      const token = extractQrToken(raw);
      setScanning(false);
      if (!token) {
        toast.error('らく助のQRではありません', {
          description: '患者宅の玄関にあるQRコードを読み取ってください',
        });
        return;
      }
      router.push(`/q/${encodeURIComponent(token)}`);
    },
    [router],
  );

  // 全画面スキャナ (訪問詳細と同じ流儀で、開いている間は一覧を差し替える)。
  // 手動フォールバック (onManual) は渡さない = 担当外は QR 必須 (設計 決定#6)。
  if (scanning) {
    return (
      <QrScanner
        targetLabel="担当外・予定外の訪問"
        onScan={handleScanned}
        onCancel={() => setScanning(false)}
      />
    );
  }

  return (
    <MobileSection pose="visit" title="今日の訪問" subtitle={`${today} ・ ${sorted.length}件`}>
      {pendingCount > 0 && (
        <div
          className="flex items-center gap-2 rounded-md bg-warning/10 px-3 py-2 text-xs text-warning"
          data-testid="today-pending-banner"
        >
          <Clock className="h-3.5 w-3.5 shrink-0" />
          未送信 {pendingCount} 件・電波が戻ると自動で送信します
        </div>
      )}

      {/* 一覧の状態 (読込中/エラー/0件) に関わらず常に出す — 予定に無い訪問こそ
          「本日の訪問はありません」の画面から入ることが多い。 */}
      <div className="space-y-1.5">
        <CheckInButton tone="outline" onClick={() => setScanning(true)}>
          <QrCode className="h-5 w-5" />
          QRを読み取る
        </CheckInButton>
        <p className="text-center text-xs text-text-muted">
          予定に無い訪問・担当外の訪問はこちらから
        </p>
      </div>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      {!isLoading && !isError && sorted.length === 0 && (
        <Card className="p-6">
          <RakusukeNote
            pose="joy"
            title="本日の訪問はありません"
            comment="おつかれさまでした！ゆっくり休んでくださいね"
          />
        </Card>
      )}

      <div className="space-y-2">
        {sorted.map((v) => (
          <MobileVisitCard key={v.id} visit={v} highlight={isUnvisited(v)} />
        ))}
      </div>
    </MobileSection>
  );
}
