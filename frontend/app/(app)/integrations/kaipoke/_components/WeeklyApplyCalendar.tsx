'use client';

/**
 * WeeklyApplyCalendar — 送る側「この週の予定（コース別）」の週カレンダー描画。
 *
 * 旧 WeeklyApplyPanel の WeekScheduleView 部分を、下段の大きなカレンダー枠に描くため
 * 分離した (中身は不変)。週切替は操作部 (WeeklyApplyControls) の週セレクタに連動する。
 */
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Rakusuke } from '@/components/brand/Rakusuke';
import { Skeleton } from '@/components/ui/skeleton';

import { WeekScheduleView } from './WeekScheduleView';
import { type WeeklyApplyVm } from './useWeeklyApply';

export function WeeklyApplyCalendar({ vm }: { vm: WeeklyApplyVm }) {
  const { weekStart, schedule, scheduleRows } = vm;

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary">この週の予定（コース別）</h3>
        <span className="text-xs text-text-muted">
          {schedule.isLoading ? '読み込み中…' : `${scheduleRows.length}件`}
        </span>
      </div>
      {schedule.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : scheduleRows.length === 0 ? (
        <Alert className="flex items-center gap-3">
          <Rakusuke pose="calendar" className="h-12 shrink-0" />
          <div>
            <AlertTitle>この週の予定はありません</AlertTitle>
            <AlertDescription>
              らく助でこの週のスケジュールを生成し、スタッフ割当まで済ませてください。
            </AlertDescription>
          </div>
        </Alert>
      ) : (
        <WeekScheduleView weekStart={weekStart} rows={scheduleRows} />
      )}
    </div>
  );
}
