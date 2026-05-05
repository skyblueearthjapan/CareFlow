'use client';

/**
 * Shared form fields used by both the new and edit staff pages.
 *
 * Kept under `_components/` so Next.js route conventions don't treat it as a
 * routable segment.
 */
import { Input } from '@/components/ui/input';
import {
  STAFF_ASSIGNMENT_VOLUME_VALUES,
  STAFF_SKILL_LEVEL_VALUES,
  type STAFF_ROLE_VALUES,
  type STAFF_SEX_VALUES,
  type STAFF_STATUS_VALUES,
} from '@/lib/schemas/staff';

type SexValue = (typeof STAFF_SEX_VALUES)[number];
type RoleValue = (typeof STAFF_ROLE_VALUES)[number];
type StatusValue = (typeof STAFF_STATUS_VALUES)[number];
type SkillLevelValue = (typeof STAFF_SKILL_LEVEL_VALUES)[number];
type AssignmentVolumeValue = (typeof STAFF_ASSIGNMENT_VOLUME_VALUES)[number];

export interface StaffFormState {
  code: string;
  name: string;
  kana: string;
  /** Empty string means "未指定" (sent as null to backend). */
  sex: SexValue | '';
  status: StatusValue;
  role: RoleValue;
  primary_office_id: string;
  can_double_team: boolean;
  mentor_id: string;
  // W3-D additions — input strings; converted to nullable values on submit.
  home_address: string;
  home_lat: string;
  home_lng: string;
  /** Comma-separated list of area codes; trimmed/split on submit. */
  areas: string;
  max_per_day: string;
  skill_level: SkillLevelValue | '';
  assignment_volume: AssignmentVolumeValue | '';
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
}

export function StaffFormFields({
  form,
  errors,
  onChange,
  sexOptions,
  roleOptions,
  statusOptions,
}: StaffFormFieldsProps) {
  const set = <K extends keyof StaffFormState>(key: K, value: StaffFormState[K]) =>
    onChange({ ...form, [key]: value });

  // Render `areas` chips for quick visual feedback alongside the comma-separated input.
  const areaChips = form.areas
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);

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
          maxLength={32}
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

      <Field label="主拠点 ID" error={errors.primary_office_id} hint="UUID。Wave 2 で拠点ピッカーに置換予定">
        <Input
          value={form.primary_office_id}
          onChange={(e) => set('primary_office_id', e.target.value)}
          placeholder="例: 11111111-2222-3333-4444-555555555555"
        />
      </Field>

      <Field label="メンター ID" error={errors.mentor_id} hint="新人スタッフのみ設定">
        <Input
          value={form.mentor_id}
          onChange={(e) => set('mentor_id', e.target.value)}
          placeholder="例: 11111111-2222-3333-4444-555555555555"
        />
      </Field>

      <Field label="" className="md:col-span-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.can_double_team}
            onChange={(e) => set('can_double_team', e.target.checked)}
            className="h-4 w-4 rounded border-border-default"
          />
          <span>2人体制での訪問が可能</span>
        </label>
      </Field>
    </div>

    {/* W3-D: 拠点・能力 section */}
    <div className="space-y-3 border-t border-border-default pt-4">
      <h3 className="text-sm font-semibold text-text-primary">拠点・能力</h3>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field
          label="自宅住所"
          error={errors.home_address}
          hint="住所→緯度経度自動取得 (Phase 5 TODO)"
          className="md:col-span-2"
        >
          <Input
            value={form.home_address}
            onChange={(e) => set('home_address', e.target.value)}
            placeholder="例: 東京都新宿区西新宿2-8-1"
            maxLength={255}
          />
        </Field>

        <Field label="自宅 緯度" error={errors.home_lat}>
          <Input
            type="number"
            step="0.0000001"
            value={form.home_lat}
            onChange={(e) => set('home_lat', e.target.value)}
            placeholder="例: 35.689487"
          />
        </Field>

        <Field label="自宅 経度" error={errors.home_lng}>
          <Input
            type="number"
            step="0.0000001"
            value={form.home_lng}
            onChange={(e) => set('home_lng', e.target.value)}
            placeholder="例: 139.691711"
          />
        </Field>

        <Field
          label="得意エリア"
          error={errors.areas}
          hint="カンマ区切りで入力 (例: A1, A2, B1)"
          className="md:col-span-2"
        >
          <Input
            value={form.areas}
            onChange={(e) => set('areas', e.target.value)}
            placeholder="例: A1, A2, B1"
          />
          {areaChips.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {areaChips.map((chip) => (
                <span
                  key={chip}
                  className="inline-flex items-center rounded-full border border-border-default bg-bg-muted px-2 py-0.5 text-xs text-text-primary"
                >
                  {chip}
                </span>
              ))}
            </div>
          )}
        </Field>

        <Field label="1日最大訪問数" required error={errors.max_per_day}>
          <Input
            type="number"
            min={1}
            max={20}
            value={form.max_per_day}
            onChange={(e) => set('max_per_day', e.target.value)}
            placeholder="6"
          />
        </Field>

        <Field label="スキル" error={errors.skill_level}>
          <SelectInput
            value={form.skill_level}
            onChange={(v) => set('skill_level', v as SkillLevelValue | '')}
            options={[
              { value: '', label: '未指定' },
              ...STAFF_SKILL_LEVEL_VALUES.map((v) => ({ value: v, label: v })),
            ]}
          />
        </Field>

        <Field label="割付ボリューム" error={errors.assignment_volume}>
          <SelectInput
            value={form.assignment_volume}
            onChange={(v) => set('assignment_volume', v as AssignmentVolumeValue | '')}
            options={[
              { value: '', label: '未指定' },
              ...STAFF_ASSIGNMENT_VOLUME_VALUES.map((v) => ({ value: v, label: v })),
            ]}
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
