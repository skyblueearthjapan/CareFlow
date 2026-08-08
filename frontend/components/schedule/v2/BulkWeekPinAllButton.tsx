'use client';

/**
 * BulkWeekPinAllButton — 青ピン (週のピン) の一括操作ボタン (PO 決定 2026-08-08)。
 *
 * 旧・赤ピン一括ボタンと対だったが、赤=完全固定は患者マスタへ統合され (2026-08-09)、
 * デザイン。並べて置かれるため、意匠は赤と完全に揃える:
 *   - Button variant="outline" size="sm" + 先頭にピンアイコン
 *   - 2 ボタン (掛ける / 外す) + 二段階確認ダイアログ (件数提示 → 最終確認)
 * 見分けは **アイコンの色 (青)** と **ラベルの「今週」接頭辞** で付ける:
 *   - 赤: 「全件ピン留め」「全件ピン留め解除」 = 型 (毎週) に効く
 *   - 青: 「今週全件固定」「今週全件解除」     = 今週の実配置だけに効く
 *
 * 件数は BE の dry_run で取る (赤はクライアント側で全 PFV を fetch して数えるが、
 * 青は週の visits が対象で BE に集約済みのため 1 リクエストで済む)。
 *
 * PO 決定 2026-08-09: 実体が visits.week_pinned フラグになり、カイポケ取込 (import)
 * を含む planned 全件が対象になった (取込週で 119 件中 5 件しか固定されない問題の解消)。
 * 解除しても取込の出所 (source='import') は失われず、取込の保護は続く。
 *
 * 解除の確認には「次の週生成で固定訪問スケジュールの時刻に戻る」ことを明示する
 * (黙って戻ると「勝手に動く」の再来になるため)。
 */
import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { PushPin, PushPinOff } from '@/components/ui/push-pin';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useBulkVisitWeekPin } from '@/lib/queries/visit_week_pin';

// ─────────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────────

export interface BulkWeekPinAllButtonProps {
  /** admin / manager のみ操作可 (BE 側は require_role でも検査済)。 */
  canEdit: boolean;
  /** 対象の ISO 週 (パネルが表示中の週)。 */
  isoYear: number;
  isoWeek: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function BulkWeekPinAllButton({ canEdit, isoYear, isoWeek }: BulkWeekPinAllButtonProps) {
  // ダイアログ状態: null=閉 / true=固定の確認中 / false=解除の確認中。
  const [target, setTarget] = useState<boolean | null>(null);
  // 二段階確認: 1=件数提示 / 2=最終確認 (赤の一括ボタンと同じ流儀)。
  const [step, setStep] = useState<1 | 2>(1);
  // 1 段目に出す対象件数 (dry_run の結果)。
  const [targetCount, setTargetCount] = useState(0);

  const bulk = useBulkVisitWeekPin();
  const isPending = bulk.isPending;

  const closeDialog = () => {
    if (isPending) return;
    setTarget(null);
    setStep(1);
    setTargetCount(0);
  };

  const handleClick = async (nextTarget: boolean) => {
    if (isPending) return;
    try {
      // dry_run で件数だけ取得 (何も変更しない)。
      const res = await bulk.mutateAsync({ isoYear, isoWeek, pinned: nextTarget, dryRun: true });
      if (res.target_count === 0) {
        toast.info(
          nextTarget ? '既に今週は全件固定済みです' : '今週固定されている訪問はありません',
        );
        return;
      }
      setTargetCount(res.target_count);
      setTarget(nextTarget);
      setStep(1);
    } catch (e) {
      toast.error(`対象件数の取得に失敗しました: ${apiErrorMessage(e)}`);
    }
  };

  const handleConfirmStep2 = async () => {
    if (target === null) return;
    const actionLabel = target ? '今週固定' : '今週固定の解除';
    try {
      const res = await bulk.mutateAsync({ isoYear, isoWeek, pinned: target });
      toast.success(
        target
          ? `${res.updated_count} 件を今週固定しました（固定訪問スケジュールは変更していません）`
          : `${res.updated_count} 件の今週固定を解除しました（型の管理下の訪問は次の週生成で型の時刻に戻ります）`,
      );
      closeDialog();
    } catch (e) {
      toast.error(`${actionLabel}に失敗しました: ${apiErrorMessage(e)}`);
    }
  };

  const dialogOpen = target !== null;
  const actionLabel = target === true ? '今週全件固定' : target === false ? '今週全件解除' : '';

  // 青ピンの意匠: 赤い丸頭 (fill-error) を info 色へ差し替え (カードのトグルと同じ手法)。
  const bluePinCls = 'h-4 w-4 text-info [&_.fill-error]:fill-[var(--info)]';

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void handleClick(true)}
        disabled={!canEdit || isPending}
        className="gap-1.5"
        aria-label="今週の訪問を全件この時刻で固定"
        title="今週の訪問をすべて今の時刻で固定します（固定訪問スケジュールは変更しません）"
        data-testid="bulk-week-pin-all-lock-button"
      >
        {isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <PushPin className={bluePinCls} aria-hidden />
        )}
        全件固定
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void handleClick(false)}
        disabled={!canEdit || isPending}
        className="gap-1.5"
        aria-label="今週の全件固定を解除"
        title="今週固定をすべて解除します（次の週生成で固定訪問スケジュールの時刻に戻ります）"
        data-testid="bulk-week-pin-all-unlock-button"
      >
        {isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <PushPinOff className="h-4 w-4 text-info" aria-hidden />
        )}
        全件解除
      </Button>

      <Dialog open={dialogOpen} onOpenChange={(o) => (!o ? closeDialog() : undefined)}>
        <DialogContent
          className="max-w-md"
          aria-describedby="bulk-week-pin-dialog-desc"
          data-testid="bulk-week-pin-confirm-dialog"
        >
          <DialogHeader>
            <DialogTitle>
              {step === 1 ? `${actionLabel}の確認` : `${actionLabel}を実行しますか?`}
            </DialogTitle>
            <DialogDescription id="bulk-week-pin-dialog-desc">
              {step === 1 ? (
                target === true ? (
                  <>
                    {isoYear}年 第{isoWeek}週の訪問
                    <span className="font-semibold"> {targetCount} 件 </span>
                    を今の時刻で固定します（固定訪問スケジュールは変更しません）。続行しますか?
                  </>
                ) : (
                  <>
                    {isoYear}年 第{isoWeek}週の今週固定
                    <span className="font-semibold"> {targetCount} 件 </span>
                    を解除します。
                    <span className="font-semibold text-warning">
                      解除後、型の管理下にある訪問は次の週生成で固定訪問スケジュールの時刻に戻ります
                      （カイポケ取込分は取込内容のまま残ります）。
                    </span>
                    続行しますか?
                  </>
                )
              ) : (
                <>本当に実行しますか? この操作は audit_log に記録されます。</>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={closeDialog}
              disabled={isPending}
              data-testid="bulk-week-pin-cancel-button"
            >
              キャンセル
            </Button>
            {step === 1 ? (
              <Button
                type="button"
                onClick={() => setStep(2)}
                disabled={isPending}
                data-testid="bulk-week-pin-step1-confirm-button"
              >
                続行
              </Button>
            ) : (
              <Button
                type="button"
                onClick={() => void handleConfirmStep2()}
                disabled={isPending}
                data-testid="bulk-week-pin-step2-confirm-button"
              >
                {isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden /> : null}
                実行する
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
