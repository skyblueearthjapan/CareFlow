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
import { PatientNgStaffSection } from '@/components/patients/PatientNgStaffSection';
import { SameAddressLinksSection } from '@/components/patients/SameAddressLinksSection';
import { SpecialVisitWeekDialog } from '@/components/schedule/v2/SpecialVisitWeekDialog';
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
import {
  useResolveOffice,
  useOffices,
  useAddOfficeAreaCity,
  useDismissAreaPrompt,
} from '@/lib/queries/offices';
import type { OfficeResolveResponse } from '@/lib/schemas/office';
import { toast } from '@/components/ui/sonner';

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
   * Phase G-21: 対象患者の ID. 指定時のみ「訪問条件」Card 内に
   * <SameAddressLinksSection /> / <PatientNgStaffSection /> を描画する
   * (新規作成画面では undefined → 非表示。保存後に設定してもらう).
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
  const watchedName = watch('name');

  // ── 特別訪問週間 (special-visit-week) ────────────────────────────────────────
  // 編集モード (= patientId が渡されている既存患者) でのみ導線を出す。
  // 新規作成では患者 ID がまだ無く期間を作れないためボタン自体を出さない。
  // ダイアログは開いたときにだけマウントする (クエリを無駄に走らせない)。
  const [specialWeekOpen, setSpecialWeekOpen] = React.useState(false);

  // ── W-7: 地域ルールの学習 (未カバー地域を手動選択した瞬間に一度だけ聞く) ──────
  const watchedOfficeId = watch('primary_office_id');
  const { offices } = useOffices({ limit: 500 });
  const addAreaCityMut = useAddOfficeAreaCity();
  const dismissAreaMut = useDismissAreaPrompt();
  /** このセッション中に登録/却下して閉じた City id (再表示しないため) */
  const [handledCityIds, setHandledCityIds] = React.useState<Set<string>>(() => new Set());

  const selectedOfficeName = React.useMemo(
    () => offices.find((o) => o.id === watchedOfficeId)?.name ?? '',
    [offices, watchedOfficeId],
  );

  const matchedCity = resolveResult?.matched_city ?? null;
  // 発火4条件: confidence=none × City 特定済 × 未却下 × 手動で拠点選択済
  const showRegionCallout =
    resolveResult?.confidence === 'none' &&
    matchedCity != null &&
    resolveResult.prompt_dismissed !== true &&
    officeMode === 'manual' &&
    !!watchedOfficeId &&
    !handledCityIds.has(matchedCity.id);

  const closeRegionCallout = React.useCallback((cityId: string) => {
    setHandledCityIds((prev) => {
      const next = new Set(prev);
      next.add(cityId);
      return next;
    });
  }, []);

  const handleRegisterRegion = React.useCallback(async () => {
    if (!matchedCity || !watchedOfficeId) return;
    try {
      await addAreaCityMut.mutateAsync({ officeId: watchedOfficeId, cityId: matchedCity.id });
      toast.success(
        `${matchedCity.name}を${selectedOfficeName || 'この拠点'}の担当地域に登録しました。次からこの地域は自動で振り分けられます。`,
      );
      closeRegionCallout(matchedCity.id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '不明なエラー';
      // callout は残して再試行できるようにする
      toast.error(`担当地域の登録に失敗しました: ${msg}`);
    }
  }, [matchedCity, watchedOfficeId, selectedOfficeName, addAreaCityMut, closeRegionCallout]);

  const handleDismissRegion = React.useCallback(async () => {
    if (!matchedCity) return;
    try {
      await dismissAreaMut.mutateAsync(matchedCity.id);
      closeRegionCallout(matchedCity.id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '不明なエラー';
      toast.error(`設定の保存に失敗しました: ${msg}`);
    }
  }, [matchedCity, dismissAreaMut, closeRegionCallout]);
  // ── /W-7 ─────────────────────────────────────────────────────────────────────

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
            {/* W-7: 地域ルールの学習 — 静かなインフォ調の呼びかけ (アンバー系) */}
            {showRegionCallout && matchedCity && (
              <div
                data-testid="region-rule-callout"
                className="mt-2 rounded-md border border-border-warning bg-warning-bg p-3 text-xs text-warning-strong"
              >
                <p className="leading-relaxed">
                  この地域（{matchedCity.name}）は、まだどの拠点の担当エリアにも登録されていません。
                  {selectedOfficeName || '選択中の拠点'}
                  の担当地域として登録しますか？ 次からこの地域の患者様は自動で振り分けられます。
                </p>
                <div className="mt-2 flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    data-testid="region-rule-register"
                    disabled={addAreaCityMut.isPending}
                    onClick={() => void handleRegisterRegion()}
                  >
                    担当地域に登録する
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    data-testid="region-rule-dismiss"
                    disabled={dismissAreaMut.isPending}
                    onClick={() => void handleDismissRegion()}
                  >
                    今回だけ
                  </Button>
                </div>
              </div>
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

        {/* 同住所紐付け / NGスタッフ も「訪問条件」の一部として同じ Card に収める
            (Phase G-21 / patient-ng-staff-design.md §8-1)。編集モード
            (patientId あり) のみ描画し、新規作成では保存後に設定してもらう。 */}
        {patientId ? (
          <>
            <SameAddressLinksSection patientId={patientId} embedded />
            <PatientNgStaffSection patientId={patientId} embedded />
          </>
        ) : null}
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

      {/* 特別訪問週間 — 編集モード (既存患者) のみ。期間と週の目標を決めて
          カレンダーに ○ を足す上乗せ型の機能 (恒久パターンは触らない)。 */}
      {patientId ? (
        <Card className="p-5 space-y-3">
          <h2 className="font-serif text-lg font-bold text-text-primary">特別訪問週間</h2>
          <p className="text-sm text-text-secondary">
            退院直後など、一定期間だけ訪問を増やしたいときに使います。基本の固定訪問はそのままです。
          </p>
          <Button
            type="button"
            variant="outline"
            onClick={() => setSpecialWeekOpen(true)}
            disabled={submitting}
            data-testid="patient-form-special-visit-week-button"
          >
            特別訪問週間
          </Button>
        </Card>
      ) : null}

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

      {/* Radix Dialog は portal で body 直下に描かれるため、form の内側に置いても
          送信ボタン等と干渉しない。開いたときだけマウントする。 */}
      {patientId && specialWeekOpen ? (
        <SpecialVisitWeekDialog
          patientId={patientId}
          patientName={watchedName ?? ''}
          open
          onOpenChange={setSpecialWeekOpen}
        />
      ) : null}
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
