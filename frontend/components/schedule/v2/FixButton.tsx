'use client';

/**
 * FixButton — 「固定」ボタン + 確認ダイアログ (W3-FE5).
 *
 * 設計 v0.9 §3.6.2 / §3.6.6 の「時間固定 (Layer 1)」を発火する UI。
 * 押下するとレイアウト件数を表示する確認ダイアログが開き、OK で
 * `useFixSchedule` を実行する。成功時 / 失敗時に sonner toast を出す。
 *
 * RBAC: admin / manager のみ enable。staff は `disabled` 経由で抑止する
 * (Backend 側でも 403 が返るが、誤押下を防ぐため UI でもガード)。
 */
import { useState } from 'react';
import { Lock } from 'lucide-react';
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
import { useFixSchedule, type ScheduleFixRequest } from '@/lib/queries/schedule_v2';

interface FixButtonProps {
  /** ボタン押下時に親が組み立てるリクエストペイロード. */
  buildRequest: () => ScheduleFixRequest;
  /** プール件数 (= 未配置数). 0 でなくても固定はできるが、警告として表示. */
  pendingCount?: number;
  /** 成功時に呼ばれる。親側で patients クエリ refetch やスナップショット
   *  リセットを実行するためのフック (キャッシュ自体は useFixSchedule が無効化済み). */
  onFixed?: (response: { patients_updated: number }) => void;
  disabled?: boolean;
}

export function FixButton({
  buildRequest,
  pendingCount = 0,
  onFixed,
  disabled = false,
}: FixButtonProps) {
  const [open, setOpen] = useState(false);
  const mutation = useFixSchedule();
  const isPending = mutation.isPending;

  // ダイアログを開いた時点で組み立てたペイロードを保持する。
  // (リクエスト中に親 state が変わって件数表示と実送信値がズレるのを防ぐ)
  const [stagedRequest, setStagedRequest] = useState<ScheduleFixRequest | null>(null);

  const handleOpen = () => {
    if (disabled) return;
    setStagedRequest(buildRequest());
    setOpen(true);
  };

  const handleClose = () => {
    if (isPending) return;
    setOpen(false);
    setStagedRequest(null);
    mutation.reset();
  };

  const handleConfirm = async () => {
    if (!stagedRequest) return;
    try {
      const res = await mutation.mutateAsync(stagedRequest);
      toast.success(`${res.patients_updated} 名の週間パターンを更新しました`);
      onFixed?.({ patients_updated: res.patients_updated });
      setOpen(false);
      setStagedRequest(null);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`固定に失敗しました: ${msg}`);
    }
  };

  const layoutCount = stagedRequest?.layout.length ?? 0;
  // 重複 patient_id を 1 と数えた "影響患者数" — 確認ダイアログの主要メトリクス。
  const uniquePatients = stagedRequest
    ? new Set(stagedRequest.layout.map((v) => v.patient_id)).size
    : 0;

  return (
    <>
      <Button type="button" onClick={handleOpen} disabled={disabled} className="gap-1">
        <Lock className="h-4 w-4" aria-hidden />
        固定
      </Button>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) handleClose();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>週レイアウトを固定</DialogTitle>
            <DialogDescription>
              現在の配置を各患者の週間訪問パターン (`weekly_pattern`) に保存します。
              差分の無い患者は更新されません。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2 py-2 text-sm">
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-md border border-border-default p-3 text-center">
                <div className="text-xs text-text-muted">配置件数</div>
                <div className="tnum text-2xl font-semibold text-text-primary">{layoutCount}</div>
              </div>
              <div className="rounded-md border border-border-default p-3 text-center">
                <div className="text-xs text-text-muted">対象患者</div>
                <div className="tnum text-2xl font-semibold text-brand-primary">
                  {uniquePatients}
                </div>
              </div>
            </div>
            <p className="text-text-secondary">{uniquePatients} 名分の週間パターンを更新します。</p>
            {pendingCount > 0 ? (
              <p className="text-warning">
                ※ 保留プールに {pendingCount} 名の未配置患者がいます。これらは保存されません。
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={handleClose} disabled={isPending}>
              キャンセル
            </Button>
            <Button type="button" onClick={handleConfirm} disabled={isPending || !stagedRequest}>
              {isPending ? '保存中…' : '固定する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
