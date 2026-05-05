/**
 * Shared form for new/edit patient pages.
 *
 * - react-hook-form + zodResolver(patientCreateSchema)
 * - Sections: 基本情報 / 連絡先 / 保険 / 訪問条件 / 備考
 * - Re-used by `patients/new/page.tsx` and `patients/[id]/edit/page.tsx`.
 *
 * TODO(Wave 2):
 *   - weekly_pattern を構造化エディタに差し替え (現状 textarea で JSON 直書き)
 *   - NG スタッフ / 同行希望スタッフ UI
 */
'use client';

import * as React from 'react';
import { useForm, type Resolver, type SubmitHandler } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  INSURANCE_OPTIONS,
  SEX_OPTIONS,
  SEX_RESTRICTION_OPTIONS,
  STATUS_OPTIONS,
  emptyPatientFormValues,
  patientFormSchema,
  type PatientFormValues,
} from '@/lib/schemas/patient';

interface PatientFormProps {
  /** Pre-filled values (edit mode). */
  defaultValues?: PatientFormValues;
  /** Submit handler — receives validated form values. */
  onSubmit: (values: PatientFormValues) => Promise<void> | void;
  /** Called by Cancel button (defaults to history.back). */
  onCancel?: () => void;
  /** Submission state, drives button disabled + label. */
  submitting?: boolean;
  /** Optional API error to surface above the form. */
  errorMessage?: string | null;
  /** Submit button label (default "保存"). */
  submitLabel?: string;
}

export function PatientForm({
  defaultValues,
  onSubmit,
  onCancel,
  submitting = false,
  errorMessage = null,
  submitLabel = '保存',
}: PatientFormProps) {
  const form = useForm<PatientFormValues>({
    // `patientFormSchema` matches the textarea/checkbox shape react-hook-form
    // actually binds to (weekly_pattern: string, special_week: boolean). The
    // dict-shape conversion happens in `prepareFormPayload` before the
    // create/update schemas validate the wire payload.
    resolver: zodResolver(patientFormSchema) as Resolver<PatientFormValues>,
    defaultValues: defaultValues ?? emptyPatientFormValues,
    mode: 'onBlur',
  });

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = form;

  const submitHandler: SubmitHandler<PatientFormValues> = async (values) => {
    // weekly_pattern is a textarea (JSON string) but the backend column is
    // JSONB. Validate parseability here so a malformed payload surfaces as a
    // form error instead of a 422 round-trip.
    const wp = values.weekly_pattern?.trim() ?? '';
    if (wp) {
      try {
        const parsed: unknown = JSON.parse(wp);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error('JSON オブジェクトを入力してください');
        }
      } catch (err) {
        setError('weekly_pattern', {
          type: 'manual',
          message: `JSON が不正です: ${
            err instanceof Error ? err.message : String(err)
          }`,
        });
        return;
      }
    }
    await onSubmit(values);
  };

  return (
    <form onSubmit={handleSubmit(submitHandler)} className="space-y-6">
      {errorMessage ? (
        <Alert variant="destructive">
          <AlertTitle>保存に失敗しました</AlertTitle>
          <AlertDescription>{errorMessage}</AlertDescription>
        </Alert>
      ) : null}

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">基本情報</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="患者コード" required error={errors.code?.message}>
            <Input {...register('code')} placeholder="例: P-0001" />
          </Field>
          <Field label="氏名" required error={errors.name?.message}>
            <Input {...register('name')} placeholder="例: 山田 太郎" />
          </Field>
          <Field label="カナ" error={errors.kana?.message}>
            <Input {...register('kana')} placeholder="ヤマダ タロウ" />
          </Field>
          <Field label="性別" error={errors.sex?.message}>
            <SelectInput {...register('sex')} options={[['', '--'], ...SEX_OPTIONS.map((v) => [v, v] as const)]} />
          </Field>
          <Field label="年齢" error={errors.age?.message}>
            <Input type="number" min={0} max={150} {...register('age')} />
          </Field>
          <Field label="状態" error={errors.status?.message}>
            <SelectInput
              {...register('status')}
              options={STATUS_OPTIONS.map((v) => [v, v === 'active' ? '有効' : '無効'] as const)}
            />
          </Field>
        </div>
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">連絡先</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="住所" error={errors.address?.message} className="md:col-span-2">
            <Input {...register('address')} />
          </Field>
          <Field label="緯度 (lat)" error={errors.lat?.message}>
            <Input type="number" step="any" {...register('lat')} />
          </Field>
          <Field label="経度 (lng)" error={errors.lng?.message}>
            <Input type="number" step="any" {...register('lng')} />
          </Field>
          <p className="text-xs text-text-muted md:col-span-2">
            ※ Phase 5 で住所→緯度経度の自動変換を予定。現状は手動入力。
          </p>
        </div>
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">保険・拠点</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="保険区分" error={errors.insurance?.message}>
            <SelectInput
              {...register('insurance')}
              options={[['', '--'], ...INSURANCE_OPTIONS.map((v) => [v, v] as const)]}
            />
          </Field>
          <Field label="主担当拠点 ID" error={errors.primary_office_id?.message}>
            <Input {...register('primary_office_id')} placeholder="UUID" />
          </Field>
        </div>
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">訪問条件</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="必要スタッフ数" required error={errors.required_staff_count?.message}>
            <Input
              type="number"
              min={1}
              max={10}
              {...register('required_staff_count')}
            />
          </Field>
          <Field label="性別制限" error={errors.sex_restriction?.message}>
            <SelectInput
              {...register('sex_restriction')}
              options={[
                ['', '--'],
                ...SEX_RESTRICTION_OPTIONS.map((v) => [v, v] as const),
              ]}
            />
          </Field>
          <Field label="NG時間 (開始)" error={errors.ng_time_start?.message}>
            <Input type="time" {...register('ng_time_start')} />
          </Field>
          <Field label="NG時間 (終了)" error={errors.ng_time_end?.message}>
            <Input type="time" {...register('ng_time_end')} />
          </Field>
        </div>
        <div className="rounded-md border border-dashed border-border-default bg-bg-muted/40 p-3">
          <p className="text-xs text-text-muted">
            週間訪問パターン (weekly_pattern) — Wave 2 で構造化エディタに差し替え予定。
            現状は JSON 文字列で直接編集してください。
          </p>
          {/* TODO(Wave 2): structured weekly pattern editor + backend column. */}
          <textarea
            {...register('weekly_pattern')}
            rows={3}
            placeholder='例: {"mon":["09:00"],"wed":["14:00"]}'
            className="mt-2 w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm font-mono text-text-primary placeholder:text-text-muted focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light"
          />
          {errors.weekly_pattern?.message ? (
            <p className="mt-1 text-xs text-error">
              {String(errors.weekly_pattern.message)}
            </p>
          ) : null}
          <label className="mt-2 inline-flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" {...register('special_week')} />
            特別週 (special_week)
          </label>
        </div>
        {/* TODO(Wave 2): NG スタッフ / 同行希望スタッフ UI */}
        <p className="text-xs text-text-muted">
          NGスタッフ・同行希望スタッフ設定 — Wave 2 で実装予定。
        </p>
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">備考</h2>
        <textarea
          {...register('note')}
          rows={4}
          className="w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light"
        />
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => (onCancel ? onCancel() : window.history.back())}
          disabled={submitting}
        >
          キャンセル
        </Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? '保存中…' : submitLabel}
        </Button>
      </div>
    </form>
  );
}

interface FieldProps {
  label: string;
  required?: boolean;
  error?: string;
  className?: string;
  children: React.ReactNode;
}

function Field({ label, required, error, className, children }: FieldProps) {
  return (
    <label className={`flex flex-col gap-1 text-sm ${className ?? ''}`}>
      <span className="font-medium text-text-secondary">
        {label}
        {required ? <span className="ml-1 text-error">*</span> : null}
      </span>
      {children}
      {error ? <span className="text-xs text-error">{error}</span> : null}
    </label>
  );
}

interface SelectInputProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {
  options: ReadonlyArray<readonly [string, string]>;
}

const SelectInput = React.forwardRef<HTMLSelectElement, SelectInputProps>(
  ({ options, className, ...props }, ref) => (
    <select
      ref={ref}
      className={`flex h-10 w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light ${className ?? ''}`}
      {...props}
    >
      {options.map(([value, label]) => (
        <option key={value} value={value}>
          {label}
        </option>
      ))}
    </select>
  ),
);
SelectInput.displayName = 'SelectInput';
