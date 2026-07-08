/**
 * Wave 40 — PasswordChangeForm vitest unit tests.
 *
 * テストケース:
 *   1. 3 つの input + 送信ボタンが表示される
 *   2. 短すぎる新パスワード → BE 呼び出し前にエラー表示
 *   3. 確認用が一致しない → エラー表示
 *   4. 新 == 旧 → エラー表示 (BE 呼び出し前)
 *   5. 成功時に onSuccess が呼ばれる
 *   6. 401 (現パス間違い) → 「現在のパスワードが正しくありません」を表示
 *   7. 422 (BE バリデーション) → エラー表示
 *   8. 429 (rate limit) → 注意メッセージを表示
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// next-auth.useSession を mock (Form 内では useChangePassword 経由でしか触らないが
// 念のため tokens を握れるようにしておく)。
// signOut も必須: fetcher は「refresh 後の再試行でも 401」のとき signOut() を呼ぶため、
// 未定義だと TypeError になり ApiError(401) がフォームまで届かない。
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(() => ({
    data: { accessToken: 'access-tok', refreshToken: 'refresh-tok' },
    status: 'authenticated',
  })),
  signOut: vi.fn(),
}));

// sonner の toast は今回 form 側では使わないが、page 側からの呼び出し互換のため
// no-op に差し替えておく。
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import { PasswordChangeForm } from '../_components/PasswordChangeForm';
import { ApiError } from '@/lib/api-client';

function withQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PasswordChangeForm', () => {
  it('1. 3 つのフィールドと送信ボタンを描画する', () => {
    render(withQuery(<PasswordChangeForm />));

    expect(screen.getByLabelText('現在のパスワード')).toBeInTheDocument();
    expect(screen.getByLabelText('新しいパスワード')).toBeInTheDocument();
    expect(screen.getByLabelText('新しいパスワード (確認)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'パスワードを変更' })).toBeInTheDocument();
  });

  it('2. 新パスワードが短すぎると BE を呼ばずクライアントエラーを表示する', async () => {
    const user = userEvent.setup();
    render(withQuery(<PasswordChangeForm />));

    await user.type(screen.getByLabelText('現在のパスワード'), 'oldpass99');
    await user.type(screen.getByLabelText('新しいパスワード'), 'ab1');
    await user.type(screen.getByLabelText('新しいパスワード (確認)'), 'ab1');
    await user.click(screen.getByRole('button', { name: 'パスワードを変更' }));

    expect(await screen.findByText('8文字以上で入力してください')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('3. 確認用が一致しないとエラーを表示する', async () => {
    const user = userEvent.setup();
    render(withQuery(<PasswordChangeForm />));

    await user.type(screen.getByLabelText('現在のパスワード'), 'oldpass99');
    await user.type(screen.getByLabelText('新しいパスワード'), 'newpass99');
    await user.type(screen.getByLabelText('新しいパスワード (確認)'), 'mismatch99');
    await user.click(screen.getByRole('button', { name: 'パスワードを変更' }));

    expect(await screen.findByText('新しいパスワードと一致しません')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('4. 新パスワードが現パスワードと同じだとクライアント側で弾く', async () => {
    const user = userEvent.setup();
    render(withQuery(<PasswordChangeForm />));

    const same = 'samepass99';
    await user.type(screen.getByLabelText('現在のパスワード'), same);
    await user.type(screen.getByLabelText('新しいパスワード'), same);
    await user.type(screen.getByLabelText('新しいパスワード (確認)'), same);
    await user.click(screen.getByRole('button', { name: 'パスワードを変更' }));

    expect(await screen.findByText('現在のパスワードと同じものは使えません')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('5. 成功時に onSuccess を呼び出す', async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const onSuccess = vi.fn();
    render(withQuery(<PasswordChangeForm onSuccess={onSuccess} />));

    await user.type(screen.getByLabelText('現在のパスワード'), 'oldpass99');
    await user.type(screen.getByLabelText('新しいパスワード'), 'newpass99');
    await user.type(screen.getByLabelText('新しいパスワード (確認)'), 'newpass99');
    await user.click(screen.getByRole('button', { name: 'パスワードを変更' }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, opts] = fetchMock.mock.calls[0];
    expect((opts as RequestInit).method).toBe('POST');
    expect((opts as RequestInit).body).toBe(
      JSON.stringify({ current_password: 'oldpass99', new_password: 'newpass99' }),
    );
  });

  it('6. 401 で「現在のパスワードが正しくありません」を表示する', async () => {
    const user = userEvent.setup();
    // The fetcher transparently retries once via /auth/refresh on 401, so we
    // need to satisfy that round-trip and have the *retried* change-password
    // also return 401. Sequence:
    //   1. POST /change-password → 401
    //   2. POST /auth/refresh    → 200 with new access token
    //   3. POST /change-password (retry) → 401  ← what the page actually sees
    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Current password is incorrect' }), {
          status: 401,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ access_token: 'new-tok' }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Current password is incorrect' }), {
          status: 401,
        }),
      );

    render(withQuery(<PasswordChangeForm />));

    await user.type(screen.getByLabelText('現在のパスワード'), 'wrongpass99');
    await user.type(screen.getByLabelText('新しいパスワード'), 'newpass99');
    await user.type(screen.getByLabelText('新しいパスワード (確認)'), 'newpass99');
    await user.click(screen.getByRole('button', { name: 'パスワードを変更' }));

    expect(await screen.findByText('現在のパスワードが正しくありません')).toBeInTheDocument();
  });

  it('7. 422 で BE のメッセージをエラー欄に出す', async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'New password must differ from the current one' }), {
        status: 422,
      }),
    );

    render(withQuery(<PasswordChangeForm />));

    await user.type(screen.getByLabelText('現在のパスワード'), 'oldpass99');
    await user.type(screen.getByLabelText('新しいパスワード'), 'goodpass1');
    await user.type(screen.getByLabelText('新しいパスワード (確認)'), 'goodpass1');
    await user.click(screen.getByRole('button', { name: 'パスワードを変更' }));

    expect(
      await screen.findByText('New password must differ from the current one'),
    ).toBeInTheDocument();
  });

  it('8. 429 で rate-limit メッセージを出す', async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'rate limited' }), { status: 429 }),
    );

    render(withQuery(<PasswordChangeForm />));

    await user.type(screen.getByLabelText('現在のパスワード'), 'oldpass99');
    await user.type(screen.getByLabelText('新しいパスワード'), 'newpass99');
    await user.type(screen.getByLabelText('新しいパスワード (確認)'), 'newpass99');
    await user.click(screen.getByRole('button', { name: 'パスワードを変更' }));

    expect(
      await screen.findByText('短時間に試行が多すぎます。15 分後に改めてお試しください。'),
    ).toBeInTheDocument();
  });

  it('9. ApiError 以外のエラーは globalError に表示する (regression)', async () => {
    const user = userEvent.setup();
    fetchMock.mockRejectedValueOnce(new Error('network down'));

    render(withQuery(<PasswordChangeForm />));

    await user.type(screen.getByLabelText('現在のパスワード'), 'oldpass99');
    await user.type(screen.getByLabelText('新しいパスワード'), 'newpass99');
    await user.type(screen.getByLabelText('新しいパスワード (確認)'), 'newpass99');
    await user.click(screen.getByRole('button', { name: 'パスワードを変更' }));

    expect(await screen.findByText('network down')).toBeInTheDocument();
  });

  // sanity: ApiError import is alive so jest doesn't strip it out
  it('ApiError is importable', () => {
    expect(typeof ApiError).toBe('function');
  });
});
