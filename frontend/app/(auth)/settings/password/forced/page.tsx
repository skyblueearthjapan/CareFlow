'use client';

/**
 * Wave 40 — `/settings/password/forced`
 *
 * Reached via the middleware redirect when the session JWT carries
 * `mustChangePassword=true` (admin reset, or first login on a freshly created
 * account). The layout deliberately strips the sidebar / app header so the
 * user has only two affordances: change the password, or sign out.
 *
 * On success we:
 *   1. tell NextAuth to flip `mustChangePassword=false` on the active token
 *      via `update({ mustChangePassword: false })` so the middleware lets us
 *      back through into the app, then
 *   2. push the user to /dashboard.
 */

import { Suspense } from 'react';
import { useRouter } from 'next/navigation';
import { signOut, useSession } from 'next-auth/react';
import { toast } from 'sonner';
import { LogOut, ShieldAlert } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

import { PasswordChangeForm } from '@/app/(app)/settings/password/_components/PasswordChangeForm';

function ForcedPasswordContent() {
  const router = useRouter();
  const { update } = useSession();

  const handleSuccess = async () => {
    toast.success('パスワードを変更しました');
    // Tell next-auth to clear the flag on the active JWT so the middleware
    // permits `/dashboard` (and everywhere else) again. The BE has already
    // written must_change_password=false to the DB — this update is purely
    // client-side cache.
    try {
      await update({ mustChangePassword: false });
    } catch {
      // If the token update fails for any reason, fall back to a full sign-out
      // so the user can re-login and pick up the fresh flag from /login.
      await signOut({ callbackUrl: '/login' });
      return;
    }
    router.replace('/dashboard');
  };

  const handleSignOut = () => {
    void signOut({ callbackUrl: '/login' });
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg-app px-4 py-8">
      <Card className="w-full max-w-md space-y-6 p-8">
        <header className="space-y-2">
          <div className="flex items-center gap-2">
            {/* eslint-disable-next-line @next/next/no-img-element -- 静的ブランド画像 */}
            <img src="/brand/rakusuke-icon-round.png" alt="" className="h-8 w-auto" />
            <span className="font-serif text-lg font-bold text-text-primary">らく助</span>
          </div>
          <div className="rounded-md border border-warning bg-warning/10 p-3 text-sm text-text-primary">
            <div className="flex items-start gap-2">
              <ShieldAlert
                className="mt-0.5 h-5 w-5 shrink-0 text-warning"
                strokeWidth={1.75}
                aria-hidden
              />
              <div>
                <p className="font-medium">パスワードの変更が必要です</p>
                <p className="mt-1 text-text-secondary">
                  仮パスワード、または初回ログインでアクセスしています。続行する前に新しいパスワードを設定してください。
                </p>
              </div>
            </div>
          </div>
        </header>

        <PasswordChangeForm onSuccess={handleSuccess} submitLabel="パスワードを設定" noAutoFocus />

        <div className="border-t border-border-default pt-4">
          <Button
            type="button"
            variant="ghost"
            className="w-full justify-center text-sm text-text-secondary"
            onClick={handleSignOut}
          >
            <LogOut className="mr-2 h-4 w-4" strokeWidth={1.75} />
            ログアウト
          </Button>
        </div>
      </Card>
    </main>
  );
}

export default function ForcedPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ForcedPasswordContent />
    </Suspense>
  );
}
