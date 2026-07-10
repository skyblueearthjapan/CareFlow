'use client';

import { Suspense, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { signIn } from 'next-auth/react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get('callbackUrl') ?? '/dashboard';

  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await signIn('credentials', {
        identifier,
        password,
        redirect: false,
        callbackUrl,
      });
      if (!result || result.error) {
        setError('メール／スタッフIDまたはパスワードが正しくありません');
        return;
      }
      router.replace(result.url ?? callbackUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'login failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div>
        <label htmlFor="identifier" className="mb-1 block text-sm text-text-secondary">
          メール または スタッフID
        </label>
        <Input
          id="identifier"
          type="text"
          autoComplete="username"
          placeholder="メールアドレス または スタッフID（例: S001）"
          required
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="password" className="mb-1 block text-sm text-text-secondary">
          パスワード
        </label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      {error && (
        <p role="alert" aria-live="polite" className="text-sm text-error">
          {error}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={submitting}>
        {submitting ? 'ログイン中…' : 'ログイン'}
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main
      className="flex min-h-screen items-center justify-center px-4"
      style={{ background: 'linear-gradient(180deg, #fff7f9 0%, #ffeef2 100%)' }}
    >
      <Card className="w-full max-w-sm p-8 pt-6">
        <div className="mb-6 text-center">
          {/* eslint-disable-next-line @next/next/no-img-element -- 静的ブランド画像 */}
          <img
            src="/brand/rakusuke-main.png"
            alt=""
            className="mx-auto h-32 w-auto"
            aria-hidden
          />
          <h1 className="sr-only">らく助 — 訪問看護 楽々スケジュール</h1>
          {/* eslint-disable-next-line @next/next/no-img-element -- 静的ブランド画像 */}
          <img
            src="/brand/rakusuke-logo-type.png"
            alt="らく助 — 訪問看護 楽々スケジュール"
            className="mx-auto mt-2 w-44"
          />
        </div>
        <Suspense fallback={<div className="h-32" aria-hidden />}>
          <LoginForm />
        </Suspense>
      </Card>
    </main>
  );
}
