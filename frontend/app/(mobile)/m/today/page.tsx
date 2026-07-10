'use client';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { MobileSection } from '@/components/mobile/MobileSection';
import { MobileVisitCard } from '@/components/mobile/MobileVisitCard';
import { RakusukeNote } from '@/components/brand/Rakusuke';
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

  return (
    <MobileSection pose="visit" title="今日の訪問" subtitle={`${today} ・ ${sorted.length}件`}>
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
