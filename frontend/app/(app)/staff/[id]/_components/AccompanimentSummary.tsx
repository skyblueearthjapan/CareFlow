'use client';

/**
 * 同行サマリ — 新人同行 v1.1 §7.5 / general-accompaniment-design.md §4。
 *
 * スタッフ詳細に「毎週の既定」(曜日 × コースコード/テンプレ名) と「今週の同行」
 * 実効一覧を表示する。同行者は新人に限らないため、リンク・既定が 1 件でもあれば
 * (新人フラグに関係なく) 表示される。
 *
 * 追加/変更はスケジュール画面の同行モードが主導線。ここには「今週の同行」の
 * **個別解除** だけを置く (週 PUT から当該 1 件を差し引いて再送する)。
 *
 * 週 PUT は BE が「残る全リンク」を再検査するため、解除でも NG/性別の 422
 * (`constraint_confirmation_required`) や重複 422 (`accompaniment_overlap`) が
 * 返りうる。司令塔 (`useAccompanimentController`) と同じ確認フロー + 日本語の
 * 理由表示をここにも配線する (生の英語 422 を現場に見せない)。
 */
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { CalendarClock, CalendarCheck, ArrowRight } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ConstraintOverrideConfirmDialog } from '@/components/schedule/v2/ConstraintOverrideConfirmDialog';
import {
  ackFlag,
  useConstraintConfirmRetry,
  type ConstraintConfirmText,
} from '@/components/schedule/v2/useConstraintConfirmRetry';
import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { isoWeekFromLocalDate } from '@/lib/format/isoWeek';
import {
  useTraineeAccompanimentDefaults,
  useTraineeAccompaniments,
  useUpdateTraineeAccompaniments,
} from '@/lib/queries/trainee_accompaniments';
import {
  formatAccompanimentConflict,
  parseAccompanimentConflictDetail,
  parseOverlapDetail,
  type TraineeAccompanimentItem,
} from '@/lib/schemas/trainee_accompaniment';
import { WEEKDAY_LABELS } from '@/lib/schemas/staff';

/** 解除経路の NG/性別 確認ダイアログ文言。 */
export const ACCOMPANIMENT_RELEASE_CONSTRAINT_TEXT: ConstraintConfirmText = {
  title: 'それでも同行を解除しますか？',
  description: '解除後に残る同行がNGスタッフ/性別制限に該当します',
  confirmLabel: '解除する',
};

/**
 * 週リンク一覧を PUT の course_ids / visit_ids へ畳む (解除は差し引いて再送)。
 *
 * リンク先 id が解決できない行は `unresolved` に集める。**黙って落とすと
 * その同行まで一緒に消える**ため、呼び出し側は 1 件でもあれば解除を中断する
 * (破壊的操作は安全側に倒す)。
 */
export function buildWeekLinkIds(
  items: readonly TraineeAccompanimentItem[],
  excludeId?: string,
): { course_ids: string[]; visit_ids: string[]; unresolved: TraineeAccompanimentItem[] } {
  const course_ids: string[] = [];
  const visit_ids: string[] = [];
  const unresolved: TraineeAccompanimentItem[] = [];
  for (const it of items) {
    if (excludeId && it.id === excludeId) continue;
    if (it.target_type === 'course') {
      if (it.course?.id) course_ids.push(it.course.id);
      else unresolved.push(it);
    } else if (it.target_type === 'visit') {
      if (it.visit?.id) visit_ids.push(it.visit.id);
      else unresolved.push(it);
    }
  }
  return { course_ids, visit_ids, unresolved };
}

export function AccompanimentSummary({
  staffId,
  canEdit = false,
}: {
  staffId: string;
  canEdit?: boolean;
}) {
  const { data, isLoading, isError, error } = useTraineeAccompanimentDefaults(staffId);

  // 今週の実効リンク (§7.5 後段)。ブラウザのローカル日付 (JST) 基準。
  const { isoYear, isoWeek } = useMemo(() => isoWeekFromLocalDate(new Date()), []);
  const weekQuery = useTraineeAccompaniments({
    isoYear,
    isoWeek,
    traineeStaffId: staffId,
  });

  const [releasingId, setReleasingId] = useState<string | null>(null);
  const [releaseErrors, setReleaseErrors] = useState<string[]>([]);
  const updateMut = useUpdateTraineeAccompaniments();
  const constraintConfirm = useConstraintConfirmRetry();

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

  /**
   * 今週の同行を 1 件解除する (週 PUT の減算)。
   * NG/性別 422 → 確認ダイアログ → ack つきで再送 / 重複 422 → 日本語の理由表示。
   */
  const handleRelease = async (item: TraineeAccompanimentItem, acknowledge = false) => {
    setReleaseErrors([]);
    const { course_ids, visit_ids, unresolved } = buildWeekLinkIds(weekQuery.data ?? [], item.id);
    if (unresolved.length > 0) {
      // 送ると解決できなかったリンクまで巻き添えで消える → 中断して人に返す。
      setReleaseErrors([
        `今週の同行のうち ${unresolved.length} 件がリンク先を特定できないため、安全のため解除を中止しました。画面を再読み込みしても直らない場合は管理者へご連絡ください。`,
      ]);
      return;
    }
    setReleasingId(item.id);
    try {
      await updateMut.mutateAsync({
        staff_id: staffId,
        iso_year: isoYear,
        iso_week: isoWeek,
        course_ids,
        visit_ids,
        // 毎週の既定には触れない (今週ぶんの解除のみ)。
        defaults: null,
        ...ackFlag(acknowledge),
      });
    } catch (err) {
      // NG/性別 → 確認ダイアログ (OK なら ack つきで再送)。
      if (!acknowledge) {
        const captured = constraintConfirm.capture(
          err,
          () => handleRelease(item, true),
          ACCOMPANIMENT_RELEASE_CONSTRAINT_TEXT,
        );
        if (captured) return;
      }
      if (err instanceof ApiError && err.status === 422) {
        const conflict = parseAccompanimentConflictDetail(err.body);
        if (conflict) {
          setReleaseErrors(conflict.conflicts.map(formatAccompanimentConflict));
          return;
        }
        const legacy = parseOverlapDetail(err.body);
        if (legacy) {
          setReleaseErrors(
            legacy.overlaps.map(
              (o) =>
                `${o.date} ${o.a.patient_name ?? '—'} と ${o.b.patient_name ?? '—'} の時間が重なるため解除できません`,
            ),
          );
          return;
        }
      }
      setReleaseErrors([apiErrorMessage(err, '解除に失敗しました')]);
    } finally {
      setReleasingId(null);
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-text-secondary">
        他スタッフの訪問に「同行」として付く設定です。新人・一般スタッフのどちらも
        同行できます。設定・変更はスケジュール画面の「同行」モードから行います。
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
        <p className="text-sm text-text-muted">毎週の既定はまだ設定されていません。</p>
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
        {releaseErrors.length > 0 && (
          <Alert variant="destructive" className="mb-2" data-testid="accompaniment-release-error">
            <AlertTitle>解除できませんでした</AlertTitle>
            <AlertDescription>
              <ul className="space-y-1">
                {releaseErrors.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
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
                {canEdit && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="ml-auto"
                    onClick={() => void handleRelease(item)}
                    disabled={releasingId !== null}
                    data-testid={`accompaniment-release-${item.id}`}
                    title={
                      item.source === 'default'
                        ? '今週ぶんだけ解除します（毎週の既定は残ります）'
                        : '今週のこの同行を解除します'
                    }
                  >
                    {releasingId === item.id ? '解除中…' : '解除'}
                  </Button>
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

      {/* NG スタッフ §7-2: 解除後に残る同行が NG/性別に抵触したときの確認 (ack 再送)。 */}
      <ConstraintOverrideConfirmDialog {...constraintConfirm.dialogProps} />
    </div>
  );
}
