'use client';

/**
 * Wave 40 — `/settings/password`
 *
 * User-facing self-service password change. Reachable from the user dropdown
 * in the header. On success the user is signed out and bounced to /login —
 * the security-industry default for self-rotation flows.
 */

import { signOut } from 'next-auth/react';
import { toast } from 'sonner';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Card } from '@/components/ui/card';

import { PasswordChangeForm } from './_components/PasswordChangeForm';

export default function PasswordSettingsPage() {
  const handleSuccess = async () => {
    toast.success('パスワードを変更しました。再度ログインしてください');
    // Wait a tick so the toast is visible before the redirect, then bounce
    // to /login. signOut clears NextAuth state including the JWT carrying
    // the now-stale must_change_password flag.
    await new Promise((resolve) => setTimeout(resolve, 300));
    await signOut({ callbackUrl: '/login' });
  };

  return (
    <section className="mx-auto w-full max-w-md space-y-4">
      <header className="space-y-1">
        <RakusukeTitle
          pose="think"
          title="パスワード変更"
          subtitle="現在のパスワードと新しいパスワードを入力してください。変更後は再度ログインが必要です。"
        />
      </header>

      <Card className="p-6">
        <PasswordChangeForm onSuccess={handleSuccess} />
      </Card>
    </section>
  );
}
