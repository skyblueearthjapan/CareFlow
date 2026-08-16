'use client';

import { Clock } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { MobileSection } from '@/components/mobile/MobileSection';
import { MobileVisitCard } from '@/components/mobile/MobileVisitCard';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { useCheckinFlush } from '@/lib/queries/checkinFlush';
import { todayIso, useMyVisits, type MyVisit } from '@/lib/queries/me';

function isUnvisited(v: MyVisit): boolean {
  return v.status === 'planned' || v.status === '';
}

export default function MobileTodayPage() {
  const today = todayIso();
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
