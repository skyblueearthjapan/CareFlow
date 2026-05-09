'use client';

/**
 * Wave 40 — Self-service password change form.
 *
 * Shared by:
 *   - /settings/password         (normal flow, sidebar/header visible)
 *   - /settings/password/forced  (post-admin-reset / first-login flow,
 *                                 reached via middleware redirect)
 *
 * On success the caller decides what to do next via `onSuccess` (typically
 * sign-out to /login for the normal flow, or `update()` + dashboard for the
 * forced flow).
 */

import { useState } from 'react';
import { z } from 'zod';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ApiError } from '@/lib/api-client';
import { useChangePassword } from '@/lib/queries/password';

// Same rule as `backend/app/schemas/auth.py::PasswordChangeRequest` so server
// + client agree on what's valid before the round-trip.
const newPasswordRule = z
  .string()
  .min(8, '8文字以上で入力してください')
  .regex(/(?=.*[A-Za-z])(?=.*\d)/, '英字と数字をそれぞれ1文字以上含めてください');

const formSchema = z
  .object({
    current_password: z.string().min(1, '現在のパスワードを入力してください'),
    new_password: newPasswordRule,
    confirm_password: z.string().min(1, '確認用パスワードを入力してください'),
  })
  .refine((data) => data.new_password === data.confirm_password, {
    path: ['confirm_password'],
    message: '新しいパスワードと一致しません',
  })
  .refine((data) => data.new_password !== data.current_password, {
    path: ['new_password'],
    message: '現在のパスワードと同じものは使えません',
  });

type FormValues = z.infer<typeof formSchema>;

type FieldErrors = Partial<Record<keyof FormValues, string>>;

export interface PasswordChangeFormProps {
  /** Called after the BE returns 204. The caller is responsible for the
   *  follow-up navigation (sign-out + redirect, or dashboard, etc.). */
  onSuccess?: () => void | Promise<void>;
  /** Override the primary button label (defaults to「パスワードを変更」). */
  submitLabel?: string;
  /** Disable autoFocus on the first field (used by the forced flow which
   *  prefers the warning banner to draw the eye first). */
  noAutoFocus?: boolean;
}

export function PasswordChangeForm({
  onSuccess,
  submitLabel = 'パスワードを変更',
  noAutoFocus = false,
}: PasswordChangeFormProps) {
  const [values, setValues] = useState<FormValues>({
    current_password: '',
    new_password: '',
    confirm_password: '',
  });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [globalError, setGlobalError] = useState<string | null>(null);

  const mutation = useChangePassword({
    onSuccess: async () => {
      // Clear the form so a stray re-submit doesn't replay the previous values.
      setValues({ current_password: '', new_password: '', confirm_password: '' });
      setFieldErrors({});
      setGlobalError(null);
      try {
        await onSuccess?.();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '完了処理に失敗しました');
      }
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setFieldErrors({ current_password: '現在のパスワードが正しくありません' });
          setGlobalError(null);
          return;
        }
        if (err.status === 422) {
          // BE Pydantic / route guard messages — map to the new_password
          // field which is the only one server-validated post-401.
          const detail =
            err.body && typeof err.body === 'object' && 'detail' in err.body
              ? (err.body as { detail: unknown }).detail
              : undefined;
          const text = Array.isArray(detail)
            ? detail.map((d: { msg?: string }) => d.msg ?? '').join(' / ')
            : typeof detail === 'string'
              ? detail
              : '入力内容を確認してください';
          setFieldErrors({ new_password: text });
          setGlobalError(null);
          return;
        }
        if (err.status === 429) {
          setGlobalError('短時間に試行が多すぎます。15 分後に改めてお試しください。');
          return;
        }
      }
      setGlobalError(err instanceof Error ? err.message : 'パスワード変更に失敗しました');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setGlobalError(null);
    const parsed = formSchema.safeParse(values);
    if (!parsed.success) {
      const errs: FieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof FormValues | undefined;
        if (key && !errs[key]) errs[key] = issue.message;
      }
      setFieldErrors(errs);
      return;
    }
    setFieldErrors({});
    mutation.mutate({
      current_password: parsed.data.current_password,
      new_password: parsed.data.new_password,
    });
  };

  const onChange = (key: keyof FormValues) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setValues((prev) => ({ ...prev, [key]: e.target.value }));
    if (fieldErrors[key]) setFieldErrors((prev) => ({ ...prev, [key]: undefined }));
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      <div className="space-y-1.5">
        <Label htmlFor="current_password">現在のパスワード</Label>
        <Input
          id="current_password"
          type="password"
          autoComplete="current-password"
          autoFocus={!noAutoFocus}
          value={values.current_password}
          onChange={onChange('current_password')}
          aria-invalid={!!fieldErrors.current_password}
          aria-describedby={fieldErrors.current_password ? 'current_password-error' : undefined}
        />
        {fieldErrors.current_password && (
          <p id="current_password-error" role="alert" className="text-sm text-error">
            {fieldErrors.current_password}
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="new_password">新しいパスワード</Label>
        <Input
          id="new_password"
          type="password"
          autoComplete="new-password"
          value={values.new_password}
          onChange={onChange('new_password')}
          aria-invalid={!!fieldErrors.new_password}
          aria-describedby={fieldErrors.new_password ? 'new_password-error' : 'new_password-help'}
        />
        {fieldErrors.new_password ? (
          <p id="new_password-error" role="alert" className="text-sm text-error">
            {fieldErrors.new_password}
          </p>
        ) : (
          <p id="new_password-help" className="text-xs text-text-muted">
            8文字以上、英字と数字をそれぞれ1文字以上含む必要があります。
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="confirm_password">新しいパスワード (確認)</Label>
        <Input
          id="confirm_password"
          type="password"
          autoComplete="new-password"
          value={values.confirm_password}
          onChange={onChange('confirm_password')}
          aria-invalid={!!fieldErrors.confirm_password}
          aria-describedby={fieldErrors.confirm_password ? 'confirm_password-error' : undefined}
        />
        {fieldErrors.confirm_password && (
          <p id="confirm_password-error" role="alert" className="text-sm text-error">
            {fieldErrors.confirm_password}
          </p>
        )}
      </div>

      {globalError && (
        <Alert variant="destructive">
          <AlertDescription>{globalError}</AlertDescription>
        </Alert>
      )}

      <Button type="submit" className="w-full" disabled={mutation.isPending}>
        {mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        {mutation.isPending ? '送信中…' : submitLabel}
      </Button>
    </form>
  );
}
