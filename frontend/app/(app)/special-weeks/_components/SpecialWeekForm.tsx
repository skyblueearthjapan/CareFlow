/**
 * Shared form for new/edit special week pages.
 *
 * - react-hook-form + useFieldArray for the dynamic items table.
 * - Header section: 患者ID / 週開始 / 週終了 / モード / status / 理由.
 * - Items section: 日付 / 曜日 / time_type / 時刻 / 分 / 必要スタッフ.
 *
 * TODO(W3-E follow-up):
 *   - Replace `patient_id` text input with the W1-F Combobox primitive
 *     (患者検索) once it is wired up to the patients query.
 */
'use client';

import * as React from 'react';
import {
  useFieldArray,
  useForm,
  type Resolver,
  type SubmitHandler,
} from 'react-hook-form';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  APPLY_MODE_OPTIONS,
  INDIVIDUAL_CHANGE_OPTIONS,
  STATUS_OPTIONS,
  TIME_TYPE_OPTIONS,
  emptySpecialWeekFormValues,
  emptySpecialWeekItemFormValues,
  type SpecialWeekFormValues,
} from '@/lib/schemas/special-week';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

interface SpecialWeekFormProps {
  defaultValues?: SpecialWeekFormValues;
  onSubmit: (values: SpecialWeekFormValues) => Promise<void> | void;
  onCancel?: () => void;
  submitting?: boolean;
  errorMessage?: string | null;
  submitLabel?: string;
  /** Edit mode: hide patient_id field (cannot change parent patient). */
  lockPatient?: boolean;
}

export function SpecialWeekForm({
  defaultValues,
  onSubmit,
  onCancel,
  submitting = false,
  errorMessage = null,
  submitLabel = '保存',
  lockPatient = false,
}: SpecialWeekFormProps) {
  const form = useForm<SpecialWeekFormValues>({
    defaultValues: defaultValues ?? emptySpecialWeekFormValues,
    mode: 'onBlur',
    // No zodResolver — items hold string-typed inputs that the schema only
    // coerces on submit (`prepareCreatePayload` -> `specialWeekCreateSchema.parse`).
    resolver: undefined as unknown as Resolver<SpecialWeekFormValues>,
  });

  const {
    register,
    control,
    handleSubmit,
    formState: { errors },
  } = form;

  const { fields, append, remove } = useFieldArray({
    control,
    name: 'items',
  });

  const submitHandler: SubmitHandler<SpecialWeekFormValues> = async (values) => {
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
        <h2 className="font-serif text-lg font-bold text-text-primary">
          ヘッダ情報
        </h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {!lockPatient ? (
            <Field label="患者 ID" required error={errors.patient_id?.message}>
              <Input
                {...register('patient_id', { required: '患者IDは必須です' })}
                placeholder="UUID"
              />
            </Field>
          ) : null}
          <Field label="週開始日" required error={errors.week_start?.message}>
            <Input
              type="date"
              {...register('week_start', { required: '週開始日は必須です' })}
            />
          </Field>
          <Field label="週終了日" required error={errors.week_end?.message}>
            <Input
              type="date"
              {...register('week_end', { required: '週終了日は必須です' })}
            />
          </Field>
          <Field label="適用モード" error={errors.apply_mode?.message}>
            <SelectInput
              {...register('apply_mode')}
              options={APPLY_MODE_OPTIONS.map(
                (v) => [v, v === 'ADD' ? '上乗せ (ADD)' : '置換 (REPLACE)'] as const,
              )}
            />
          </Field>
          <Field label="ステータス" error={errors.status?.message}>
            <SelectInput
              {...register('status')}
              options={STATUS_OPTIONS.map(
                (v) =>
                  [
                    v,
                    v === 'draft'
                      ? '下書き'
                      : v === 'applied'
                        ? '適用済'
                        : 'キャンセル',
                  ] as const,
              )}
            />
          </Field>
          <Field
            label="理由 / メモ"
            error={errors.reason?.message}
            className="md:col-span-2"
          >
            <textarea
              {...register('reason')}
              rows={2}
              className="w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light"
            />
          </Field>
        </div>
      </Card>

      <Card className="p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-lg font-bold text-text-primary">
            訪問明細 ({fields.length} 件)
          </h2>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => append({ ...emptySpecialWeekItemFormValues })}
          >
            + 行を追加
          </Button>
        </div>

        {fields.length === 0 ? (
          <p className="text-sm text-text-muted">
            訪問明細がまだありません。「+ 行を追加」で1件目を作成してください。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-2 py-2 font-medium">訪問日</th>
                  <th className="px-2 py-2 font-medium">曜日</th>
                  <th className="px-2 py-2 font-medium">タイプ</th>
                  <th className="px-2 py-2 font-medium">開始</th>
                  <th className="px-2 py-2 font-medium">終了</th>
                  <th className="px-2 py-2 font-medium">分</th>
                  <th className="px-2 py-2 font-medium">人数</th>
                  <th className="px-2 py-2 font-medium">個別変更</th>
                  <th className="px-2 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {fields.map((row, index) => (
                  <tr
                    key={row.id}
                    className="border-b border-border-default last:border-0 align-top"
                  >
                    <td className="px-2 py-2">
                      <Input
                        type="date"
                        {...register(`items.${index}.visit_date` as const, {
                          required: true,
                        })}
                        className="min-w-[140px]"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <SelectInput
                        {...register(`items.${index}.weekday` as const)}
                        options={WEEKDAY_LABELS.map(
                          (lbl, i) => [String(i), lbl] as const,
                        )}
                        className="min-w-[68px]"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <SelectInput
                        {...register(`items.${index}.time_type` as const)}
                        options={TIME_TYPE_OPTIONS.map((v) => [v, v] as const)}
                        className="min-w-[96px]"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Input
                        type="time"
                        {...register(`items.${index}.start_time` as const)}
                        className="min-w-[110px]"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Input
                        type="time"
                        {...register(`items.${index}.end_time` as const)}
                        className="min-w-[110px]"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Input
                        type="number"
                        min={1}
                        max={600}
                        {...register(`items.${index}.service_minutes` as const)}
                        className="min-w-[72px] tnum"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Input
                        type="number"
                        min={1}
                        max={10}
                        {...register(
                          `items.${index}.required_staff_count` as const,
                        )}
                        className="min-w-[64px] tnum"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <SelectInput
                        {...register(
                          `items.${index}.individual_change_handling` as const,
                        )}
                        options={[
                          ['', '--'],
                          ...INDIVIDUAL_CHANGE_OPTIONS.map(
                            (v) => [v, v] as const,
                          ),
                        ]}
                        className="min-w-[112px]"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => remove(index)}
                      >
                        削除
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
