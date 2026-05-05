'use client';

/**
 * Shared form fields used by both the new and edit staff pages.
 *
 * W1-FE2 (v0.9 §4.2) で v2 仕様に沿ってフィールドを削減:
 *   - 削除: can_double_team / 自宅住所 + lat/lng / 得意エリア /
 *           1日最大訪問数 / スキル / 割付ボリューム (6 項目)
 *   - 移設: メンターを「基本情報」から「詳細」セクションへ移動
 *   - 状態: 在籍 / 休職 / 退職 の 3 値セレクト (default 在籍)
 *
 * Kept under `_components/` so Next.js route conventions don't treat it as a
 * routable segment.
 */
import { OfficeCombobox } from '@/components/master/OfficeCombobox';
import { StaffCombobox } from '@/components/master/StaffCombobox';
import { Input } from '@/components/ui/input';
import {
  type STAFF_ROLE_VALUES,
  type STAFF_SEX_VALUES,
  type STAFF_STATUS_VALUES,
} from '@/lib/schemas/staff';

type SexValue = (typeof STAFF_SEX_VALUES)[number];
type RoleValue = (typeof STAFF_ROLE_VALUES)[number];
type StatusValue = (typeof STAFF_STATUS_VALUES)[number];

/**
 * v2 (§4.2 残置 9 項目) に対応した form state.
 * 削除済みフィールドはここに含めない。
 */
export interface StaffFormState {
  code: string;
  name: string;
  kana: string;
  /** Empty string means "未指定" (sent as null to backend). */
  sex: SexValue | '';
  status: StatusValue;
  role: RoleValue;
  primary_office_id: string;
  /**
   * 詳細セクションで設定するメンター ID (新人スタッフのみ).
   * v2 仕様で「基本情報」からは外し、「詳細」セクション末尾に配置する。
   */
  mentor_id: string;
  note: string;
}

interface Option<T extends string> {
  value: T;
  label: string;
}

interface StaffFormFieldsProps {
  form: StaffFormState;
  errors: Record<string, string>;
  onChange: (next: StaffFormState) => void;
  sexOptions: Option<SexValue>[];
  roleOptions: Option<RoleValue>[];
  statusOptions: Option<StatusValue>[];
  /** Current staff id (edit mode) — excluded from mentor combobox so a
   *  staff member cannot be assigned as their own mentor. */
  currentStaffId?: string;
}

export function StaffFormFields({
  form,
  errors,
  onChange,
  sexOptions,
  roleOptions,
  statusOptions,
  currentStaffId,
}: StaffFormFieldsProps) {
  const set = <K extends keyof StaffFormState>(key: K, value: StaffFormState[K]) =>
    onChange({ ...form, [key]: value });

  return (
    <div className="space-y-6">
      {/* ─── 基本情報 (v2 §4.2 残置項目のみ — メンターは「詳細」へ移設) ─── */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field label="氏名" required error={errors.name}>
          <Input
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="例: 山田 花子"
            required
            maxLength={120}
          />
        </Field>

        <Field label="カナ" error={errors.kana}>
          <Input
            value={form.kana}
            onChange={(e) => set('kana', e.target.value)}
            placeholder="例: ヤマダ ハナコ"
            maxLength={120}
          />
        </Field>

        <Field label="スタッフコード" error={errors.code}>
          <Input
            value={form.code}
            onChange={(e) => set('code', e.target.value)}
            placeholder="例: S001"
            maxLength={64}
          />
        </Field>

        <Field label="性別" error={errors.sex}>
          <SelectInput
            value={form.sex}
            onChange={(v) => set('sex', v as SexValue | '')}
            options={[{ value: '', label: '未指定' }, ...sexOptions]}
          />
        </Field>

        <Field label="役割" required error={errors.role}>
          <SelectInput
            value={form.role}
            onChange={(v) => set('role', v as RoleValue)}
            options={roleOptions}
          />
        </Field>

        <Field label="状態" required error={errors.status}>
          <SelectInput
            value={form.status}
            onChange={(v) => set('status', v as StatusValue)}
            options={statusOptions}
          />
        </Field>

        <Field label="主拠点" error={errors.primary_office_id} className="md:col-span-2">
          <OfficeCombobox
            value={form.primary_office_id}
            onChange={(v) => set('primary_office_id', v)}
          />
        </Field>

        <Field label="備考" error={errors.note} className="md:col-span-2">
          <textarea
            value={form.note}
            onChange={(e) => set('note', e.target.value)}
            rows={3}
            className="w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-visible:border-brand-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary-light"
          />
        </Field>
      </div>

      {/* ─── 詳細 (§4.2 メンター移設先) ─── */}
      <div className="space-y-3 border-t border-border-default pt-4">
        <h3 className="text-sm font-semibold text-text-primary">詳細</h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field
            label="メンター（新人スタッフのみ設定）"
            error={errors.mentor_id}
            hint="新人スタッフのみ、指導役のメンターを設定してください"
            className="md:col-span-2"
          >
            <StaffCombobox
              value={form.mentor_id}
              onChange={(v) => set('mentor_id', v)}
              excludeId={currentStaffId}
            />
          </Field>
        </div>
      </div>
    </div>
  );
}

interface FieldProps {
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}

function Field({ label, required, error, hint, className, children }: FieldProps) {
  return (
    <div className={className}>
      {label && (
        <label className="mb-1 block text-sm font-medium text-text-primary">
          {label}
          {required && <span className="ml-1 text-error">*</span>}
        </label>
      )}
      {children}
      {hint && !error && <p className="mt-1 text-xs text-text-muted">{hint}</p>}
      {error && <p className="mt-1 text-xs text-error">{error}</p>}
    </div>
  );
}

function SelectInput<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: Option<T>[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      className="flex h-10 w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary focus-visible:border-brand-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary-light"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
