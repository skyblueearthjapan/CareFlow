/**
 * Shared form for new/edit patient pages — **v2 削減版** (W1-FE1).
 *
 * 設計仕様書 v0.9 §4.1 / 実装手順書 v0.2 §2 W1-FE1:
 *   - 削除 10 項目 (年齢 / NG時間 / 必要スタッフ数 / エリア / 指定タイプ /
 *     NGスタッフ / 同行希望スタッフ / 継続要望 / 曜日優先度 / NG曜日) を
 *     UI から除去
 *   - 週間訪問パターンに staff_count トグルを追加 (§3.3)
 *   - 特別訪問週間 ON/OFF + 適用週指定 UI を追加 (§3.4 簡素実装)
 *
 * Sections: 基本情報 / 連絡先 / 保険・拠点 / 訪問条件 / 週間パターン /
 *           特別訪問週間 / 備考
 */
'use client';

import * as React from 'react';
import { Controller, useForm, type Resolver, type SubmitHandler } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';

import { AddressGeocodeField } from '@/components/AddressGeocodeField';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Checkbox } from '@/components/ui/checkbox';
import { OfficeCombobox } from '@/components/master/OfficeCombobox';
import {
  INSURANCE_LABELS_JA,
  INSURANCE_VALUES,
  SEX_LABELS_JA,
  SEX_RESTRICTION_LABELS_JA,
  SEX_RESTRICTION_VALUES,
  SEX_VALUES,
  STATUS_LABELS_JA,
  STATUS_VALUES,
  emptyPatientFormValues,
  patientFormSchema,
  type PatientFormValues,
} from '@/lib/schemas/patient';

import { WeeklyPatternEditor } from './WeeklyPatternEditor';

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
    resolver: zodResolver(patientFormSchema) as Resolver<PatientFormValues>,
    defaultValues: defaultValues ?? emptyPatientFormValues,
    mode: 'onBlur',
  });

  const {
    register,
    handleSubmit,
    control,
    watch,
    formState: { errors },
  } = form;

  const submitHandler: SubmitHandler<PatientFormValues> = async (values) => {
    await onSubmit(values);
  };

  const specialWeekEnabled = watch('special_week_enabled');

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
            <SelectInput
              {...register('sex')}
              options={[['', '--'], ...SEX_VALUES.map((v) => [v, SEX_LABELS_JA[v]] as const)]}
            />
          </Field>
          <Field label="状態" error={errors.status?.message}>
            <SelectInput
              {...register('status')}
              options={STATUS_VALUES.map((v) => [v, STATUS_LABELS_JA[v]] as const)}
            />
          </Field>
        </div>
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">連絡先</h2>
        <AddressGeocodeField
          mode="rhf"
          formMethods={form}
          addressFieldName="address"
          latFieldName="lat"
          lngFieldName="lng"
          disabled={submitting}
        />
        {(errors.address?.message || errors.lat?.message || errors.lng?.message) && (
          <p className="text-xs text-error">
            {errors.address?.message ?? errors.lat?.message ?? errors.lng?.message}
          </p>
        )}
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">保険・拠点</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="保険区分" error={errors.insurance?.message}>
            <SelectInput
              {...register('insurance')}
              options={[
                ['', '--'],
                ...INSURANCE_VALUES.map((v) => [v, INSURANCE_LABELS_JA[v]] as const),
              ]}
            />
          </Field>
          <Field label="主担当拠点" error={errors.primary_office_id?.message}>
            <Controller
              control={control}
              name="primary_office_id"
              render={({ field }) => (
                <OfficeCombobox
                  value={field.value ?? ''}
                  onChange={field.onChange}
                  disabled={submitting}
                />
              )}
            />
          </Field>
        </div>
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">訪問条件</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="性別制限" error={errors.sex_restriction?.message}>
            <SelectInput
              {...register('sex_restriction')}
              options={[
                ['', '--'],
                ...SEX_RESTRICTION_VALUES.map((v) => [v, SEX_RESTRICTION_LABELS_JA[v]] as const),
              ]}
            />
          </Field>
        </div>
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">週間訪問パターン</h2>
        <Controller
          control={control}
          name="weekly_pattern"
          render={({ field }) => (
            <WeeklyPatternEditor
              value={field.value}
              onChange={field.onChange}
              disabled={submitting}
            />
          )}
        />
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">特別訪問週間</h2>
        <p className="text-xs text-text-muted">
          通常の週とは異なる訪問頻度を一時的に適用したい週がある場合に設定します。
          「ON」にしたうえで適用週 (例: 2026-W18, 2026-W19) を指定してください。
        </p>
        <Controller
          control={control}
          name="special_week_enabled"
          render={({ field }) => (
            <label className="inline-flex items-center gap-2 text-sm text-text-primary">
              <Checkbox
                checked={!!field.value}
                onCheckedChange={(c) => field.onChange(c === true)}
                disabled={submitting}
              />
              特別訪問週間を有効化する
            </label>
          )}
        />
        {specialWeekEnabled ? (
          <div className="space-y-4">
            <Field
              label="適用週"
              error={errors.special_week_active_input?.message}
              hint='例: "2026-W18, 2026-W19"'
            >
              <Input
                {...register('special_week_active_input')}
                placeholder="2026-W18, 2026-W19"
                disabled={submitting}
              />
            </Field>
            <Controller
              control={control}
              name="special_weekly_pattern"
              render={({ field }) => (
                <WeeklyPatternEditor
                  value={field.value}
                  onChange={field.onChange}
                  disabled={submitting}
                  title="特別訪問週間パターン"
                />
              )}
            />
          </div>
        ) : null}
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
  hint?: string;
  children: React.ReactNode;
}

function Field({ label, required, error, className, hint, children }: FieldProps) {
  return (
    <label className={`flex flex-col gap-1 text-sm ${className ?? ''}`}>
      <span className="font-medium text-text-secondary">
        {label}
        {required ? <span className="ml-1 text-error">*</span> : null}
        {hint ? <span className="ml-2 text-xs font-normal text-text-muted">{hint}</span> : null}
      </span>
      {children}
      {error ? <span className="text-xs text-error">{error}</span> : null}
    </label>
  );
}

interface SelectInputProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
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
