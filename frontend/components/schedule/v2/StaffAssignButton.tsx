'use client';

/**
 * StaffAssignButton — Layer 3 スタッフ割付実行ボタン (W4-FE9).
 *
 * 設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.4 と
 * API 契約 ``docs/plans/v2-api-contracts.md`` §4.6
 * (`POST /api/v1/courses/assign-staff`) に対応する UI。
 *
 * 機能:
 *   - 「スタッフ割付を実行」ボタン
 *   - クリック → 確認ダイアログ → POST /courses/assign-staff
 *   - 成功時に sonner toast、`onAssigned` コールバックで親 (CourseProposal)
 *     に AssignmentEntry[] を返す。親は曜日 × コース別のスタッフ表示に展開する。
 *   - 失敗時は toast.error
 *
 * RBAC: admin / manager のみ enable。staff は `disabled` で抑止する
 *      (Backend 側でも 403 が返るが、誤押下を防ぐため UI でもガード)。
 *
 * BE 並行作業 (W4-BE9) 中の挙動:
 *   - エンドポイントが 404 を返した場合は toast.error で通知し、
 *     画面状態 (assignedStaffByCode) はそのまま維持する。
 */
import { useState } from 'react';
import { Users } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ApiError } from '@/lib/api-client';
import {
  useAssignStaff,
  type AssignStaffResponse,
  type StaffAssignmentEntry,
} from '@/lib/queries/courses';

export interface StaffAssignButtonProps {
  /** ISO 8601 年週 (CourseProposal と共有). */
  isoYear: number;
  isoWeek: number;
  /** RBAC: admin / manager のみ true. */
  canEdit: boolean;
  /**
   * 成功時に呼ばれる。親 (CourseProposal) は assignments を
   * `weekday × course_id → staff_id` の Map に展開し、
   * `assignedStaffByCode` 経路で各 CourseRow に渡す想定。
   */
  onAssigned?: (response: AssignStaffResponse) => void;
  /** 表示中の総コース数 (確認ダイアログのメトリクス). */
  totalCourses?: number;
}

export function StaffAssignButton({
  isoYear,
  isoWeek,
  canEdit,
  onAssigned,
  totalCourses,
}: StaffAssignButtonProps) {
  const [open, setOpen] = useState(false);
  const mutation = useAssignStaff();
  const isPending = mutation.isPending;

  const handleOpen = () => {
    if (!canEdit || isPending) return;
    setOpen(true);
  };

  const handleClose = () => {
    if (isPending) return;
    setOpen(false);
    mutation.reset();
  };

  const handleConfirm = async () => {
    try {
      const res = await mutation.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
      });
      const count = res.assignments.length;
      toast.success(
        `スタッフ割付を実行しました: ${count} 件 (ローテ分散 ${res.rotation_score.toFixed(2)} / 総距離 ${res.total_distance_km.toFixed(1)} km)`,
      );
      onAssigned?.(res);
      setOpen(false);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`スタッフ割付に失敗しました: ${msg}`);
    }
  };

  return (
    <>
      <Button
        type="button"
        variant="default"
        onClick={handleOpen}
        disabled={!canEdit || isPending}
        className="gap-1"
      >
        <Users className="h-4 w-4" aria-hidden />
        {isPending ? '割付中…' : 'スタッフ割付を実行'}
      </Button>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) handleClose();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>スタッフ割付を実行</DialogTitle>
            <DialogDescription>
              当該週の確定済みコース構成 (course_fixed) に対して、 ハンガリアン法 +
              ローテーション分散でスタッフを自動割付します。 既存の割付は上書きされ、各コースは{' '}
              <code>staff_assigned</code> 状態に進みます。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2 py-2 text-sm">
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-md border border-border-default p-3 text-center">
                <div className="text-xs text-text-muted">対象週</div>
                <div className="tnum text-lg font-semibold text-text-primary">
                  {isoYear}-W{String(isoWeek).padStart(2, '0')}
                </div>
              </div>
              <div className="rounded-md border border-border-default p-3 text-center">
                <div className="text-xs text-text-muted">表示中コース数</div>
                <div className="tnum text-lg font-semibold text-brand-primary">
                  {totalCourses ?? '—'}
                </div>
              </div>
            </div>
            <p className="text-text-secondary">
              ハード制約 (性別 / 勤務曜日 / 1 コース 1 スタッフ / マネージャー除外) は
              全て満たすよう BE 側で自動チェックされます。
            </p>
            <p className="text-text-muted">
              ※ 異常時は割付後に各コース行の「変更」から手動上書きしてください。
            </p>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={handleClose} disabled={isPending}>
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={handleConfirm}
              disabled={isPending || !canEdit}
              className="gap-1"
            >
              <Users className="h-4 w-4" aria-hidden />
              {isPending ? '実行中…' : '割付を実行する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// 補助ユーティリティ — assignments を「曜日 → code → staff_id」へ展開する
// CourseProposal が直接使えるよう re-export している。
// ─────────────────────────────────────────────────────────────────────────

/**
 * AssignStaffResponse.assignments を `weekday → course_id → staff_id` に展開する。
 *
 * CourseProposal 側で「現在表示中の weekday」「各 course の id」と突き合わせ、
 * `assignedStaffByCode` (Record<CourseCodeV2, ReactNode>) を組み立てるのに使う。
 */
export function indexAssignmentsByWeekdayAndCourse(
  entries: StaffAssignmentEntry[],
): Map<number, Map<string, string>> {
  const out = new Map<number, Map<string, string>>();
  for (const e of entries) {
    let dayMap = out.get(e.weekday);
    if (!dayMap) {
      dayMap = new Map<string, string>();
      out.set(e.weekday, dayMap);
    }
    dayMap.set(e.course_id, e.staff_id);
  }
  return out;
}
