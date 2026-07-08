'use client';

/**
 * IntegrationSettingsMenu — 連携ページ右上の「設定」メニュー（admin専用）。
 *
 * PO要望 (2026-07-09): カイポケのログイン情報 (法人ID / ユーザーID / パスワード) が
 * ページ本文に常時見えているのはセキュリティ上こわい。
 * → 右上の「設定」→「接続設定」→ ダイアログ、という二段階の裏側配置にする。
 *
 * ページ自体が admin 限定 (Sidebar strictAdmin + ページ内ガード + BE RBAC) なので
 * これは「権限の壁」ではなく「うっかり触る/肩越しに見える」を減らす配置上の工夫。
 */
import { useState } from 'react';
import { KeyRound, Settings } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

import { ConnectionSettingsForm } from './ConnectionSettingsForm';

export function IntegrationSettingsMenu({
  /** 未設定のときはメニューに注意ドットを出し、気づけるようにする。 */
  needsAttention = false,
}: {
  needsAttention?: boolean;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <>
      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="relative"
            aria-label="連携の設定"
            data-testid="integration-settings-menu"
          >
            <Settings className="h-4 w-4" strokeWidth={1.75} />
            設定
            {needsAttention && (
              <span
                aria-hidden="true"
                className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-warning ring-2 ring-bg-base"
              />
            )}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-56 p-1.5">
          <Button
            type="button"
            variant="ghost"
            className="w-full justify-start text-sm"
            data-testid="open-connection-settings"
            onClick={() => {
              setMenuOpen(false);
              setDialogOpen(true);
            }}
          >
            <KeyRound className="mr-2 h-4 w-4" strokeWidth={1.75} />
            接続設定
            {needsAttention && (
              <span className="ml-auto text-[10px] font-bold text-warning-strong">未設定</span>
            )}
          </Button>
        </PopoverContent>
      </Popover>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>接続設定</DialogTitle>
            <DialogDescription>
              カイポケへのログイン情報です。この設定だけで他の事業所様でも利用できます。
            </DialogDescription>
          </DialogHeader>
          {/* 開いたときだけマウントして、フォーム状態 (入力途中のパスワード等) を持ち越さない。 */}
          {dialogOpen && <ConnectionSettingsForm />}
        </DialogContent>
      </Dialog>
    </>
  );
}
