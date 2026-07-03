'use client';

/**
 * AssignWarningDialog — Phase G-91 (確認レビューフロー / コースカード型レビュー).
 *
 * 「自動スタッフ割り当て」 (旧称: 自動スタッフ割付) を、 問題のあるコースだけ管理者が
 * 最終判断する確認レビューフローに作り替えたダイアログ。
 * 直前の「埋めて事後警告」 (Phase G-89) を置換する。
 *
 * 表示対象 (review_items) は 2 種:
 *   🔴 性別 (gender / 重度): 適合性別の同拠点スタッフが居ないコース。
 *      候補スタッフ (= 性別無視時の候補) を提示し、 「割り当てる」 ボタン →
 *      確認モーダル 1 回 (= 計 2 ステップ) を踏んで承認する。
 *   🟡 連続 (consecutive / 軽度): 患者の直近担当者と同じになるコース。
 *      候補スタッフを提示し、 チェックボックスで承認する (追加モーダル無し)。
 *      件数が多いときのために「一斉承認」ボタンで全件を一括チェックできる
 *      (2026-07: 十数件を 1 件ずつチェックする手間の解消)。 性別 (重度) は
 *      誤操作リスクが高いため一斉承認の対象外 (従来どおり 2 ステップ個別承認)。
 *
 * apply = 承認されたカードを ``POST /api/v1/schedule/apply-staff-review`` で
 * 一括反映する (呼び出し側 onApply に委譲)。 自動スタッフ割り当てと同一の _persist
 * 経路で VSA / course_status / primary・secondary 同期 / 2 名体制 / trainee
 * companion を全て反映する (= 旧 PATCH /courses ループのリグレッションを解消)。
 *
 * review_items が空なら呼び出し側でこのダイアログを出さず、 success toast のみ。
 */
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { ReviewItem } from '@/lib/queries/assign_staff_only';
import { cn } from '@/lib/utils';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function fmtWeekday(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? `?${weekday}`;
}

/** "09:00:00" → "09:00" (秒を落とす). null/undefined はダッシュ. */
function fmtTime(t: string | null | undefined): string {
  if (!t) return '—';
  return t.slice(0, 5);
}

/** 性別制限ラベル ('female_only' → '女性のみ' 等). */
function fmtSexRestriction(sr: string | null | undefined): string | null {
  if (!sr) return null;
  if (sr === 'female_only') return '女性のみ';
  if (sr === 'male_only') return '男性のみ';
  return sr;
}

/** 承認された 1 カード (= apply 対象). */
export interface ApprovedReviewItem {
  course_id: string;
  candidate_staff_id: string;
}

export interface AssignWarningDialogProps {
  open: boolean;
  onClose: () => void;
  reviewItems: ReviewItem[];
  /** 承認カードに対し PATCH /courses/{id} を発行する (呼び出し側で実装). */
  onApply: (approved: ApprovedReviewItem[]) => Promise<void> | void;
  /** apply 実行中フラグ (= ボタン無効化). */
  applying?: boolean;
}

export function AssignWarningDialog({
  open,
  onClose,
  reviewItems,
  onApply,
  applying = false,
}: AssignWarningDialogProps) {
  // 承認済み course_id 集合 (= チェック / 確認モーダル通過分).
  const [approved, setApproved] = React.useState<Set<string>>(() => new Set());
  // 性別カードの確認モーダル対象 (= 「割り当てる」 を押したカード).
  const [confirmTarget, setConfirmTarget] = React.useState<ReviewItem | null>(null);

  // ダイアログ open 時に承認状態をリセットする.
  React.useEffect(() => {
    if (open) {
      setApproved(new Set());
      setConfirmTarget(null);
    }
  }, [open]);

  const genderItems = reviewItems.filter((i) => i.reason === 'gender');
  const consecutiveItems = reviewItems.filter((i) => i.reason === 'consecutive');

  const toggleApproved = (courseId: string, next: boolean) => {
    setApproved((prev) => {
      const s = new Set(prev);
      if (next) s.add(courseId);
      else s.delete(courseId);
      return s;
    });
  };

  const handleConfirmGender = () => {
    if (confirmTarget) {
      toggleApproved(confirmTarget.course_id, true);
      setConfirmTarget(null);
    }
  };

  // 一斉承認 (連続のみ): 🟡 連続カードを全件まとめてチェックする。
  // 全件承認済みのときはトグルで全件解除に切り替わる。 🔴 性別は対象外。
  const allConsecutiveApproved =
    consecutiveItems.length > 0 && consecutiveItems.every((i) => approved.has(i.course_id));
  const handleToggleAllConsecutive = () => {
    setApproved((prev) => {
      const s = new Set(prev);
      for (const item of consecutiveItems) {
        if (allConsecutiveApproved) s.delete(item.course_id);
        else s.add(item.course_id);
      }
      return s;
    });
  };

  const handleApply = async () => {
    // 修正B: 承認カードの linked partner (= 2 名体制の相方 Y) も自動で apply 対象に
    // 含める (co-select)。 clean な未 commit partner (assigned_staff_id=None) は BE の
    // partner 補完では DB から拾えないため、 FE で review_item の candidate を明示的に
    // 同梱して X+Y 一括 _persist させ、 secondary 対称解決で half-assigned を防ぐ。
    const itemByCourse = new Map(reviewItems.map((i) => [i.course_id, i]));
    const selected = new Map<string, string>();
    for (const item of reviewItems) {
      if (!approved.has(item.course_id)) continue;
      // 承認カード本体.
      if (!selected.has(item.course_id)) selected.set(item.course_id, item.candidate_staff_id);
      // linked partner も candidate 付きで co-select (先勝ち)。
      for (const linkedId of item.linked_course_ids ?? []) {
        const linked = itemByCourse.get(linkedId);
        if (linked && !selected.has(linkedId)) {
          selected.set(linkedId, linked.candidate_staff_id);
        }
      }
    }
    const list: ApprovedReviewItem[] = Array.from(selected, ([course_id, candidate_staff_id]) => ({
      course_id,
      candidate_staff_id,
    }));
    await onApply(list);
  };

  const approvedCount = approved.size;

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
        <DialogContent
          className="max-h-[88vh] max-w-3xl overflow-y-auto"
          data-testid="assign-warning-dialog"
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <span aria-hidden>📋</span>
              自動スタッフ割り当てのレビュー
            </DialogTitle>
            <DialogDescription>
              問題のないコースは自動で確定しました。 以下のコースは管理者の判断が必要です。
              内容を確認し、 割り当てるコースを選んで「選んだ内容で割り当て」 を押してください。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5 py-1">
            {/* 🔴 性別セクション */}
            {genderItems.length > 0 ? (
              <section data-testid="assign-review-gender-section">
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>🔴</span>
                  性別 ({genderItems.length} 件) — 確認のうえ割り当て
                </h3>
                <ul className="space-y-2">
                  {genderItems.map((item) => (
                    <ReviewCard
                      key={item.course_id}
                      item={item}
                      approved={approved.has(item.course_id)}
                      onApproveGender={() => setConfirmTarget(item)}
                      onUnapprove={() => toggleApproved(item.course_id, false)}
                    />
                  ))}
                </ul>
              </section>
            ) : null}

            {/* 🟡 連続セクション */}
            {consecutiveItems.length > 0 ? (
              <section data-testid="assign-review-consecutive-section">
                <h3 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>🟡</span>
                  連続 ({consecutiveItems.length} 件) — チェックで割り当て
                  {/* 一斉承認: 連続カードを全件まとめてチェック (全件承認済みならトグルで解除). */}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="ml-auto"
                    onClick={handleToggleAllConsecutive}
                    data-testid="assign-review-consecutive-approve-all"
                  >
                    {allConsecutiveApproved ? '一斉承認を解除' : '一斉承認'}
                  </Button>
                </h3>
                <ul className="space-y-2">
                  {consecutiveItems.map((item) => (
                    <ReviewCard
                      key={item.course_id}
                      item={item}
                      approved={approved.has(item.course_id)}
                      onToggleConsecutive={(next) => toggleApproved(item.course_id, next)}
                    />
                  ))}
                </ul>
              </section>
            ) : null}

            {reviewItems.length === 0 ? (
              <div className="py-4 text-center text-xs text-text-muted">
                レビュー対象はありません。
              </div>
            ) : null}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={applying}
              data-testid="assign-review-cancel"
            >
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={handleApply}
              disabled={applying || approvedCount === 0}
              data-testid="assign-review-apply"
            >
              選んだ内容で割り当て{approvedCount > 0 ? ` (${approvedCount})` : ''}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 🔴 性別カードの確認モーダル (1 回). */}
      <Dialog
        open={confirmTarget !== null}
        onOpenChange={(o) => (!o ? setConfirmTarget(null) : undefined)}
      >
        <DialogContent className="max-w-md" data-testid="assign-review-gender-confirm">
          <DialogHeader>
            <DialogTitle>本当に割り当てますか？</DialogTitle>
            <DialogDescription>
              {confirmTarget ? (
                <>
                  性別制限のあるコース「{confirmTarget.course_code} /{' '}
                  {fmtWeekday(confirmTarget.weekday)}」 に{' '}
                  <span className="font-medium text-text-primary">
                    {confirmTarget.candidate_staff_name}
                  </span>{' '}
                  を割り当てます。 適合する性別のスタッフが居ないため、 管理者の判断で割り当てます。
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmTarget(null)}
              data-testid="assign-review-gender-confirm-cancel"
            >
              やめる
            </Button>
            <Button
              type="button"
              onClick={handleConfirmGender}
              data-testid="assign-review-gender-confirm-ok"
            >
              割り当てる
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

interface ReviewCardProps {
  item: ReviewItem;
  approved: boolean;
  /** 🟡 連続: チェックボックス切替. */
  onToggleConsecutive?: (next: boolean) => void;
  /** 🔴 性別: 「割り当てる」 ボタン (= 確認モーダルを開く). */
  onApproveGender?: () => void;
  /** 🔴 性別: 承認解除 (= 「割り当てる」 後に取り消す). */
  onUnapprove?: () => void;
}

function ReviewCard({
  item,
  approved,
  onToggleConsecutive,
  onApproveGender,
  onUnapprove,
}: ReviewCardProps) {
  const isGender = item.reason === 'gender';
  return (
    <li
      className={cn(
        'rounded-md border p-3 text-xs',
        isGender ? 'border-error/40 bg-error/5' : 'border-warning/40 bg-warning/5',
        approved && 'ring-2 ring-brand-primary',
      )}
      data-testid="assign-review-card"
      data-reason={item.reason}
      data-approved={approved ? 'true' : 'false'}
    >
      {/* ヘッダ行: 拠点 / コード / 曜日 + 候補スタッフ (右上). */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge variant={isGender ? 'destructive' : 'warning'} className="text-[10px]">
          {isGender ? '性別' : '連続'}
        </Badge>
        <span className="font-medium text-text-primary">{item.course_code}</span>
        <span className="text-text-muted">{fmtWeekday(item.weekday)}</span>
        {item.office_name ? <span className="text-text-muted">{item.office_name}</span> : null}
        <span className="ml-auto text-text-secondary">
          担当（候補）:{' '}
          <span className="font-medium text-text-primary">{item.candidate_staff_name}</span>
        </span>
      </div>

      {/* visit 一覧 (原因患者をマーク). */}
      <ul className="space-y-1">
        {item.visits.map((v) => {
          const sr = fmtSexRestriction(v.sex_restriction);
          return (
            <li
              key={v.patient_id}
              className={cn(
                'flex flex-wrap items-center gap-2 rounded border border-border-default bg-bg-base px-2 py-1',
                v.is_cause && 'border-l-4',
                v.is_cause && (isGender ? 'border-l-error' : 'border-l-warning'),
              )}
              data-testid="assign-review-visit"
              data-cause={v.is_cause ? 'true' : 'false'}
            >
              <span className="text-text-muted">{fmtTime(v.start_time)}</span>
              <span className="font-medium text-text-primary">{v.patient_name}</span>
              {sr ? (
                <Badge variant="outline" className="text-[10px]">
                  {sr}
                </Badge>
              ) : null}
              {v.is_cause ? (
                <Badge
                  variant={isGender ? 'destructive' : 'warning'}
                  className="ml-auto text-[10px]"
                >
                  {isGender ? '性別NG' : '連続'}
                </Badge>
              ) : null}
            </li>
          );
        })}
      </ul>

      {/* アクション行. */}
      <div className="mt-2 flex items-center justify-end gap-2">
        {isGender ? (
          approved ? (
            <div className="flex items-center gap-2">
              <Badge variant="success" className="text-[10px]">
                割り当て予定
              </Badge>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onUnapprove}
                data-testid="assign-review-gender-unapprove"
              >
                取り消す
              </Button>
            </div>
          ) : (
            <Button
              type="button"
              size="sm"
              onClick={onApproveGender}
              data-testid="assign-review-gender-approve"
            >
              割り当てる
            </Button>
          )
        ) : (
          <label className="flex cursor-pointer items-center gap-2 text-text-secondary">
            <Checkbox
              checked={approved}
              onCheckedChange={(c) => onToggleConsecutive?.(c === true)}
              data-testid="assign-review-consecutive-checkbox"
            />
            このコースを割り当てる
          </label>
        )}
      </div>
    </li>
  );
}
