'use client';

/**
 * 新人同行サマリ (閲覧専用) — 新人同行 v1.1 §7.5。
 *
 * 旧「同行スタッフ割付」パネルの後継。新人 (is_trainee=true) のスタッフ詳細に
 * 「毎週の既定」(曜日 × コースコード/テンプレ名) と「今週の同行」実効一覧を
 * 閲覧表示する。編集導線はスケジュール画面 (同行モード) への誘導リンクのみ。
 */
import { useMemo } from 'react';
import Link from 'next/link';
import { CalendarClock, CalendarCheck, ArrowRight } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { isoWeekFromLocalDate } from '@/lib/format/isoWeek';
import {
  useTraineeAccompanimentDefaults,
  useTraineeAccompaniments,
} from '@/lib/queries/trainee_accompaniments';
import { WEEKDAY_LABELS } from '@/lib/schemas/staff';

export function TraineeAccompanimentSummary({ staffId }: { staffId: string }) {
  const { data, isLoading, isError, error } = useTraineeAccompanimentDefaults(staffId);

  // 今週の実効リンク (§7.5 後段)。ブラウザのローカル日付 (JST) 基準。
  const { isoYear, isoWeek } = useMemo(() => isoWeekFromLocalDate(new Date()), []);
  const weekQuery = useTraineeAccompaniments({
    isoYear,
    isoWeek,
    traineeStaffId: staffId,
  });

  const defaults = [...(data ?? [])].sort((a, b) => a.weekday - b.weekday);
  const weekItems = useMemo(() => {
    const items = weekQuery.data ?? [];
    // コースリンク→曜日順、患者個別リンク→日付順で安定表示する。
    return [...items].sort((a, b) => {
      const ka = a.course ? `0-${a.course.weekday}` : `1-${a.visit?.date ?? ''}`;
      const kb = b.course ? `0-${b.course.weekday}` : `1-${b.visit?.date ?? ''}`;
      return ka.localeCompare(kb);
    });
  }, [weekQuery.data]);

  return (
    <div className="space-y-4">
      <p className="text-sm text-text-secondary">
        新人はコースを持ちません。先輩の訪問に「同行」として付きます。設定・変更は
        スケジュール画面の「新人同行」モードから行います。
      </p>

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : isError ? (
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      ) : defaults.length === 0 ? (
        <p className="text-sm text-text-muted">
          毎週の既定はまだ設定されていません。
        </p>
      ) : (
        <div>
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-text-primary">
            <CalendarClock className="h-4 w-4" />
            毎週の既定
          </div>
          <table className="w-full text-sm">
            <thead className="border-b border-border-default text-left text-text-secondary">
              <tr>
                <th className="px-3 py-2 font-medium">曜日</th>
                <th className="px-3 py-2 font-medium">同行コース</th>
              </tr>
            </thead>
            <tbody>
              {defaults.map((d) => (
                <tr key={d.id} className="border-b border-border-default last:border-0">
                  <td className="px-3 py-2">{WEEKDAY_LABELS[d.weekday] ?? d.weekday}</td>
                  <td className="px-3 py-2 text-text-primary">
                    {d.course_template_label ?? d.course_template_id}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 今週の同行 (実効一覧・§7.5 後段)。取得失敗時は既定表示を妨げない。 */}
      <div>
        <div className="mb-2 flex items-center gap-2 text-sm font-medium text-text-primary">
          <CalendarCheck className="h-4 w-4" />
          今週の同行（{isoYear}-W{String(isoWeek).padStart(2, '0')}）
        </div>
        {weekQuery.isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : weekQuery.isError ? (
          <p className="text-sm text-text-muted">今週の同行を取得できませんでした。</p>
        ) : weekItems.length === 0 ? (
          <p className="text-sm text-text-muted">今週の同行はありません。</p>
        ) : (
          <ul className="space-y-1 text-sm" data-testid="trainee-week-accompaniments">
            {weekItems.map((item) => (
              <li key={item.id} className="flex items-center gap-2">
                {item.course ? (
                  <span>
                    {WEEKDAY_LABELS[item.course.weekday] ?? item.course.weekday}曜 ・{' '}
                    {item.course.code}コース（丸ごと）
                  </span>
                ) : item.visit ? (
                  <span>
                    {item.visit.date} {item.visit.start ?? ''} {item.visit.patient_name ?? ''}
                  </span>
                ) : null}
                {item.source === 'default' && (
                  <span className="text-xs text-text-muted">（毎週の既定から）</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="border-t border-border-default pt-3">
        <Button asChild variant="outline" size="sm">
          <Link href="/schedule">
            スケジュール画面で同行を編集
            <ArrowRight className="h-4 w-4" />
          </Link>
        </Button>
      </div>
    </div>
  );
}
