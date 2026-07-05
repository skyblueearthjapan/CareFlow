'use client';

/**
 * EmergencyStopButton — 非常停止（確認ダイアログ付き）。
 *
 * kaipoke は単一スロットで、停止は「現在処理中の1件が完了してから」効く
 * グレースフル停止。旧 GAS の巨大赤ボタンを、CareFlow のトーンで落ち着いた
 * 確認フローに置き換えたもの。
 */
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

export function EmergencyStopButton({
  onConfirm,
  pending,
}: {
  onConfirm: () => void;
  pending: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button variant="destructive" onClick={() => setOpen(true)} disabled={pending}>
        非常停止
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>実行を停止しますか？</DialogTitle>
            <DialogDescription>
              現在処理中の1件が完了した時点で、安全に停止します。すでにカイポケへ反映済みの
              変更は取り消されません（この操作は Ctrl+Z の対象外です）。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              続行する
            </Button>
            <Button
              variant="destructive"
              disabled={pending}
              onClick={() => {
                onConfirm();
                setOpen(false);
              }}
            >
              停止する
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
