'use client';

/**
 * BulkPinAllPfvsButton — Phase G-34 一括ピン留め / 一括ピン留め解除ボタン.
 *
 * /schedule ヘッダーに「全患者の PFV を一括ピン留め / 一括ピン留め解除」する 2 つのボタンを
 * 提供する. admin / manager のみ表示 (BE 側は role 検査済だが FE 側でも非表示にする).
 *
 * 動作 (一段階の Dialog で 2 段階の確認を要求する):
 *   1. ボタンクリック
 *   2. 全 active 患者の PFV を fetch (= patients list → 各患者の /fixed-visits)
 *   3. すべて targetState なら toast「既に全件 XXX 状態です」で早期 return
 *   4. Dialog 表示 (1 段目): 「全 active 患者の固定枠 N 件を ... 続行しますか?」
 *      → 「続行」 で 2 段目に切替
 *   5. Dialog (2 段目): 「本当に実行しますか? この操作は audit_log に記録されます。」
 *      → 「実行」 で POST /api/v1/patients/fixed-visits/pin/bulk
 *   6. 成功 toast + invalidate (useBulkPinPfvs 内で 'patients' / 'courses' / 'visits' を無効化)
 *
 * Backend:
 *   - POST /api/v1/patients/fixed-visits/pin/bulk (admin / manager)
 *   - payload: [{pfv_id, is_pinned}, ...]
 *   - audit_logs に 1 件ごとに ``action="pfv_pin_toggle"`` を記録
 */
import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { useSession } from 'next-auth/react';

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
import { fetcher } from '@/lib/api/fetcher';
import { useBulkPinPfvs } from '@/lib/queries/g21';
import type { PatientRead } from '@/lib/schemas/patient';
import type { PatientFixedVisitV2Read } from '@/lib/schemas/v2/patient_fixed_visit';

// ─────────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────────

export interface BulkPinAllPfvsButtonProps {
  /** admin / manager のみ表示する (BE 側は require_role でも検査済). */
  canEdit: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal: PFV 全件 fetch ヘルパー (= on click 時のみ実行).
// ─────────────────────────────────────────────────────────────────────────────

const PATIENT_LIST_HARD_CAP = 500;

/**
 * 全 active 患者の PFV を fetch する (on demand).
 *
 * 1. GET /api/v1/patients?limit=500&offset=0
 * 2. status='active' でフィルタ
 * 3. 各患者の GET /api/v1/patients/{id}/fixed-visits を並列 fetch
 * 4. flat 配列で返す
 *
 * 数十 patient × 数件 PFV 程度を想定. button click 時にのみ走り、 page 表示時は
 * 走らないため (= 既存 schedule のレンダリング負荷には影響なし).
 */
async function fetchAllActivePfvs(opts: {
  accessToken: string | null;
  refreshToken: string | null;
}): Promise<PatientFixedVisitV2Read[]> {
  const { accessToken, refreshToken } = opts;
  const patients = await fetcher<PatientRead[]>(
    `/api/v1/patients?limit=${PATIENT_LIST_HARD_CAP}&offset=0`,
    { accessToken, refreshToken },
  );
  const activePatients = patients.filter((p) => p.status === 'active');

  const results = await Promise.all(
    activePatients.map((p) =>
      fetcher<PatientFixedVisitV2Read[]>(`/api/v1/patients/${p.id}/fixed-visits`, {
        accessToken,
        refreshToken,
      }),
    ),
  );
  return results.flat();
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function BulkPinAllPfvsButton({ canEdit }: BulkPinAllPfvsButtonProps) {
  // ダイアログ状態.
  // null: 閉じている / true: ピン留め確認中 / false: ピン留め解除確認中.
  const [target, setTarget] = useState<boolean | null>(null);
  // 二段階確認のステップ: 1 = 1 段目 (件数提示) / 2 = 2 段目 (最終確認).
  const [step, setStep] = useState<1 | 2>(1);
  // クリック直後 → PFV fetch → ダイアログ表示までのローディング.
  const [isLoading, setIsLoading] = useState(false);
  // 対象 PFV id 一覧 (= 1 段目 dialog open 時に確定).
  const [targetPfvIds, setTargetPfvIds] = useState<string[]>([]);

  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const bulkPin = useBulkPinPfvs();

  // RB (PO決定 2026-07-08): 全ロール同一表示。staff には disabled ボタンとして見せる。

  const isPending = bulkPin.isPending;

  const closeDialog = () => {
    if (isPending) return;
    setTarget(null);
    setStep(1);
    setTargetPfvIds([]);
  };

  const handleClick = async (nextTarget: boolean) => {
    if (isLoading || isPending) return;
    setIsLoading(true);
    try {
      const pfvs = await fetchAllActivePfvs({ accessToken, refreshToken });
      // 既に全件 targetState ならば早期 return.
      const needUpdate = pfvs.filter((p) => Boolean(p.is_pinned) !== nextTarget);
      if (needUpdate.length === 0) {
        toast.info(nextTarget ? '既に全件ピン留め状態です' : '既に全件ピン留め解除状態です');
        return;
      }
      setTargetPfvIds(needUpdate.map((p) => p.id));
      setTarget(nextTarget);
      setStep(1);
    } catch (e) {
      toast.error(
        e instanceof Error
          ? `固定枠の取得に失敗しました: ${e.message}`
          : '固定枠の取得に失敗しました',
      );
    } finally {
      setIsLoading(false);
    }
  };

  const handleConfirmStep1 = () => {
    setStep(2);
  };

  const handleConfirmStep2 = async () => {
    if (target === null || targetPfvIds.length === 0) return;
    const items = targetPfvIds.map((pfv_id) => ({ pfv_id, is_pinned: target }));
    const actionLabel = target ? 'ピン留め' : 'ピン留め解除';
    try {
      await bulkPin.mutateAsync(items);
      toast.success(`${targetPfvIds.length} 件を${actionLabel}しました`);
      setTarget(null);
      setStep(1);
      setTargetPfvIds([]);
    } catch (e) {
      toast.error(
        e instanceof Error
          ? `${actionLabel}に失敗しました: ${e.message}`
          : `${actionLabel}に失敗しました`,
      );
    }
  };

  const dialogOpen = target !== null;
  const actionLabel = target === true ? 'ピン留め' : target === false ? 'ピン留め解除' : '';

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void handleClick(true)}
        disabled={!canEdit || isLoading || isPending}
        className="gap-1.5"
        aria-label="全患者の固定訪問スケジュール（固定枠）の時間を一括ピン留め"
        title="固定訪問スケジュール（毎週の型）の時間をすべてピン留めします。今週の盤面ではなく固定枠に対する操作です"
        data-testid="bulk-pin-all-lock-button"
      >
        {isLoading || isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <PushPin className="h-4 w-4" aria-hidden />
        )}
        時間を全ピン留め
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void handleClick(false)}
        disabled={!canEdit || isLoading || isPending}
        className="gap-1.5"
        aria-label="全患者の固定訪問スケジュール（固定枠）のピン留めを一括解除"
        title="固定訪問スケジュール（毎週の型）のピン留めをすべて外します。今週の盤面ではなく固定枠に対する操作です"
        data-testid="bulk-pin-all-unlock-button"
      >
        {isLoading || isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <PushPinOff className="h-4 w-4" aria-hidden />
        )}
        全ピン解除
      </Button>

      <Dialog open={dialogOpen} onOpenChange={(o) => (!o ? closeDialog() : undefined)}>
        <DialogContent
          className="max-w-md"
          aria-describedby="bulk-pin-dialog-desc"
          data-testid="bulk-pin-confirm-dialog"
        >
          <DialogHeader>
            <DialogTitle>
              {step === 1 ? `全件${actionLabel}の確認` : `${actionLabel}を実行しますか?`}
            </DialogTitle>
            <DialogDescription id="bulk-pin-dialog-desc">
              {step === 1 ? (
                <>
                  全 active 患者の固定枠
                  <span className="font-semibold"> {targetPfvIds.length} 件 </span>
                  を一括{actionLabel}します。続行しますか?
                </>
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
              data-testid="bulk-pin-cancel-button"
            >
              キャンセル
            </Button>
            {step === 1 ? (
              <Button
                type="button"
                onClick={handleConfirmStep1}
                disabled={isPending}
                data-testid="bulk-pin-step1-confirm-button"
              >
                続行
              </Button>
            ) : (
              <Button
                type="button"
                onClick={() => void handleConfirmStep2()}
                disabled={isPending}
                data-testid="bulk-pin-step2-confirm-button"
              >
                {isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden /> : null}
                実行
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
