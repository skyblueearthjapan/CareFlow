'use client';

/**
 * Shared form fields used by both the new and edit staff pages.
 *
 * Kept under `_components/` so Next.js route conventions don't treat it as a
 * routable segment.
 *
 * v2 (W1-BE2): backend は §4.2 の 9 項目のみ受け付ける (``extra="forbid"``)。
 * 削除済み: ``can_double_team`` / ``home_address`` / ``home_lat`` / ``home_lng`` /
 * ``areas`` / ``max_per_day`` / ``skill_level`` / ``assignment_volume``。
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

export interface StaffFormState {
  code: string;
  name: string;
  kana: string;
  /** Empty string means "未指定" (sent as null to backend). */
  sex: SexValue | '';
  status: StatusValue;
  role: RoleValue;
  primary_office_id: string;
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

        <Field label="主拠点" error={errors.primary_office_id}>
          <OfficeCombobox
            value={form.primary_office_id}
            onChange={(v) => set('primary_office_id', v)}
          />
        </Field>

        <Field label="メンター" error={errors.mentor_id} hint="新人スタッフのみ設定">
          <StaffCombobox
            value={form.mentor_id}
            onChange={(v) => set('mentor_id', v)}
            excludeId={currentStaffId}
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
