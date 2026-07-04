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
import { SameAddressLinksSection } from '@/components/patients/SameAddressLinksSection';
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
import { useResolveOffice } from '@/lib/queries/offices';
import type { OfficeResolveResponse } from '@/lib/schemas/office';

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
  /**
   * Phase G-21: 対象患者の ID. 指定時のみ最下部に
   * <SameAddressLinksSection /> を描画する (新規作成画面では undefined).
   */
  patientId?: string;
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
  patientId,
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
    setValue,
    watch,
    formState: { errors, isDirty },
  } = form;

  // ── W12-FE: 住所から主担当拠点を自動判定 ───────────────────────────────────
  /** 'auto': 住所変更に応じて primary_office_id を自動セット
   *  'manual': ユーザーが手動で選択した → 以降は自動セットしない
   *
   * W-6 項目4: 編集モード (defaultValues に primary_office_id が既に入っている) では
   * 'manual' 初期化する。これにより編集ページを開いた直後の住所 watch 初回発火で、
   * 手動設定済みの主担当拠点が resolve 結果に上書きされるのを防ぐ。
   * 新規作成 / 拠点未設定の患者は従来どおり 'auto' (住所から自動ご案内)。 */
  const [officeMode, setOfficeMode] = React.useState<'auto' | 'manual'>(() =>
    defaultValues?.primary_office_id ? 'manual' : 'auto',
  );
  const [resolveResult, setResolveResult] = React.useState<OfficeResolveResponse | null>(null);
  const resolveMut = useResolveOffice();

  const watchedAddress = watch('address');

  // M-1 fix: officeMode を ref で参照することで debounce タイマー中の手動選択を反映する
  // (タイマー設定時点の officeMode を closure に閉じ込めると、debounce 中にユーザーが
  // OfficeCombobox を手動変更しても タイマー発火時に '古い auto' のままで自動上書きしてしまう)
  const officeModeRef = React.useRef(officeMode);
  React.useEffect(() => {
    officeModeRef.current = officeMode;
  }, [officeMode]);

  React.useEffect(() => {
    const address = watchedAddress?.trim();
    if (!address) {
      setResolveResult(null);
      return;
    }
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const result = await resolveMut.mutateAsync(address);
          setResolveResult(result);
          // 自動セット: officeMode='auto' かつ confidence !== 'none' のみ
          // ref 経由で最新値を読み、debounce 中の手動切替も尊重する
          if (
            officeModeRef.current === 'auto' &&
            result.confidence !== 'none' &&
            result.office_id
          ) {
            setValue('primary_office_id', result.office_id, { shouldDirty: true });
          }
        } catch {
          // silent — best-effort, ユーザー手動選択にフォールバック
        }
      })();
    }, 600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchedAddress]);
  // ── /W12-FE ────────────────────────────────────────────────────────────────

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
      // リセット時に自動判定モードに戻す
      setOfficeMode('auto');
      setResolveResult(null);
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
          {/* Phase G-86: 患者コードは任意 (空欄で backend が自動採番)。 */}
          <Field label="患者コード" hint="任意・空欄で自動採番" error={errors.code?.message}>
            <Input {...register('code')} placeholder="空欄で自動採番" />
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
                  onChange={(v) => {
                    field.onChange(v);
                    // ユーザーが手動選択 → 以降は住所変更で自動セットしない
                    setOfficeMode('manual');
                  }}
                  disabled={submitting}
                />
              )}
            />
            {/* W12-FE: 自動判定ヒント */}
            {resolveResult && officeMode === 'auto' && resolveResult.confidence !== 'none' && (
              <p className="text-xs text-text-muted">
                🏢 拠点エリア: {resolveResult.office_name} (自動判定: {resolveResult.confidence})
              </p>
            )}
            {resolveResult && resolveResult.confidence === 'none' && (
              <p className="text-xs text-warning">⚠ 拠点エリア外: 手動で選択してください</p>
            )}
            {officeMode === 'manual' && (
              <button
                type="button"
                onClick={() => {
                  setOfficeMode('auto');
                  // 自動判定モードに戻したとき、現在の住所で再判定を促すため resolveResult を維持
                  // (次の住所変更 or 再マウントで再トリガーされる)
                }}
                className="text-xs text-brand-primary underline"
              >
                自動判定に戻す
              </button>
            )}
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
          {/* Wave 18 Phase B-7: 2 名体制必須フラグ */}
          <Field
            label="複数スタッフでの訪問が必要"
            error={errors.requires_multiple_staff?.message}
            hint="2 名以上での訪問が必要な場合にチェック"
          >
            <Controller
              control={control}
              name="requires_multiple_staff"
              render={({ field }) => (
                <label
                  className="inline-flex items-center gap-2 text-sm text-text-secondary"
                  data-testid="requires-multiple-staff-label"
                >
                  <Checkbox
                    checked={!!field.value}
                    onCheckedChange={(c) => field.onChange(c === true)}
                    disabled={submitting}
                    aria-label="2 名以上での訪問が必要"
                    data-testid="requires-multiple-staff-checkbox"
                  />
                  2 名以上での訪問が必要
                </label>
              )}
            />
          </Field>
        </div>
      </Card>

      <Card className="p-5 space-y-4">
        <h2 className="font-serif text-lg font-bold text-text-primary">希望訪問パターン</h2>
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

      {/* Phase G-21: 同住所紐付け (edit 画面のみ; patientId が渡された場合に描画). */}
      {patientId ? <SameAddressLinksSection patientId={patientId} /> : null}

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
