'use client';

/**
 * RegisterPatientButton — W-4 (pool-bulk-insert-design D-4).
 *
 * スケジュール画面ツールバーの「＋新規患者登録」ボタン。旧「＋新規提案」
 * (ProposeNewModal 起動ボタン) の置き換え。ボタンと {@link CreatePatientDialog}
 * を自己完結で持つため、CourseDayTablePanel 側は state を持たず本部品を置くだけでよい。
 */
import * as React from 'react';
import { UserPlus } from 'lucide-react';

import { Button } from '@/components/ui/button';

import { CreatePatientDialog } from './CreatePatientDialog';

export interface RegisterPatientButtonProps {
  disabled?: boolean;
}

export function RegisterPatientButton({ disabled }: RegisterPatientButtonProps) {
  const [open, setOpen] = React.useState(false);

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => setOpen(true)}
        disabled={disabled}
        data-testid="register-patient-button"
      >
        <UserPlus className="mr-1 h-4 w-4" aria-hidden />
        新規患者登録
      </Button>
      <CreatePatientDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}
