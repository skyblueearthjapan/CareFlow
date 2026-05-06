/**
 * Shared form for new/edit patient pages.
 *
 * - react-hook-form + zodResolver(patientFormSchema)
 * - Sections: 基本情報 / 連絡先 / 保険・拠点 / 訪問条件 / 週間パターン / 備考
 * - Re-used by `patients/new/page.tsx` and `patients/[id]/edit/page.tsx`.
 *
 * v2 (W1-BE1): backend は §4.1 の 16 項目のみ受け付ける。
 * 削除済み (form から除去): age / required_staff_count / ng_time_start /
 * ng_time_end / area / ng_staff_ids / preferred_staff_ids / specified_type /
 * continuous_request。
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
  INSURANCE_LABEL,
  INSURANCE_OPTIONS,
  SEX_LABEL,
  SEX_OPTIONS,
  SEX_RESTRICTION_LABEL,
  SEX_RESTRICTION_OPTIONS,
  STATUS_LABEL,
  STATUS_OPTIONS,
  emptyPatientFormValues,
  patientFormSchema,
  type PatientFormValues,
} from '@/lib/schemas/patient';

import { WeeklyPatternEditor } from './WeeklyPatternEditor';

export interface PatientFormHandle {
  /** フォームを強制送信する (EditPageStickyBar の「更新」ボタン用) */
  submitForm: () => void;
  /** フォームを初期値にリセットする (EditPageStickyBar の「破棄」ボタン用) */
  resetForm: () => void;
}

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
  /**
   * isDirty 状態が変わるたびに呼ばれるコールバック (W10-FE3: sticky bar 用)。
   * 親がバーを制御する場合に使う。
   */
  onDirtyChange?: (isDirty: boolean) => void;
  /** フォームのメソッドを親から呼び出すための ref (W10-FE3: sticky bar 用) */
  formRef?: React.Ref<PatientFormHandle>;
}

export function PatientForm({
  defaultValues,
  onSubmit,
  onCancel,
  submitting = false,
  errorMessage = null,
  submitLabel = '保存',
  onDirtyChange,
  formRef,
}: PatientFormProps) {
  const form = useForm<PatientFormValues>({
    // `patientFormSchema` matches the structured/checkbox shape react-hook-form
    // actually binds to (weekly_pattern: WeeklyPattern dict, special_week:
    // boolean). The wire-shape conversion happens in `prepareFormPayload`
    // before the create/update schemas validate the payload.
    resolver: zodResolver(patientFormSchema) as Resolver<PatientFormValues>,
    defaultValues: defaultValues ?? emptyPatientFormValues,
    mode: 'onBlur',
  });

  const {
    register,
    handleSubmit,
    control,
    reset,
    formState: { errors, isDirty },
  } = form;

  const submitHandler: SubmitHandler<PatientFormValues> = async (values) => {
    await onSubmit(values);
  };

  // isDirty が変わるたびに親へ通知する
  React.useEffect(() => {
    onDirtyChange?.(isDirty);
  }, [isDirty, onDirtyChange]);

  // 外部から submit / reset を呼べるように ref を公開する
  React.useImperativeHandle(formRef, () => ({
    submitForm: () => {
      void handleSubmit(submitHandler)();
    },
    resetForm: () => {
      reset(defaultValues ?? emptyPatientFormValues);
    },
  }));

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
              options={[['', '--'], ...SEX_OPTIONS.map((v) => [v, SEX_LABEL[v]] as const)]}
            />
          </Field>
          <Field label="状態" error={errors.status?.message}>
            <SelectInput
              {...register('status')}
              options={STATUS_OPTIONS.map((v) => [v, STATUS_LABEL[v]] as const)}
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
                ...INSURANCE_OPTIONS.map((v) => [v, INSURANCE_LABEL[v]] as const),
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
                ['', 'なし'],
                ...SEX_RESTRICTION_OPTIONS.map((v) => [v, SEX_RESTRICTION_LABEL[v]] as const),
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
        <Controller
          control={control}
          name="special_week"
          render={({ field }) => (
            <label className="inline-flex items-center gap-2 text-sm text-text-secondary">
              <Checkbox
                checked={!!field.value}
                onCheckedChange={(c) => field.onChange(c === true)}
              />
              特別週パターンを使用する (special_weekly_pattern)
            </label>
          )}
        />
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
