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
 *
 * W10-FE1: is_trainee フラグ追加。「メンター」ラベルを「同行スタッフ」に変更。
 *
 * 連絡先 (contacts-phone-email-design-2026-11-05 §7): 電話 1 つ・連絡用メール 1 つ・
 * お知らせ SMS／メール／受け取らない (今は使わない)。見分けは患者様と同じ。
 */
import { OfficeCombobox } from '@/components/master/OfficeCombobox';
import { EmailJudge, PhoneJudge } from '@/components/patients/PatientContactsCard';
import { Input } from '@/components/ui/input';
import { classifyEmail, classifyPhone } from '@/lib/contacts/phone';
import {
  STAFF_QUALIFICATION_VALUES,
  type StaffNotifyChannel,
  type STAFF_ROLE_VALUES,
  type STAFF_SEX_VALUES,
  type STAFF_STATUS_VALUES,
  type StaffQualification,
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
  /** Wave 10: 新人スタッフフラグ (§4.2.x). 旧 mentor_id は廃止 */
  is_trainee: boolean;
  /** K-1b カイポケ「職種」列。空文字 = 未設定 (backend には null で送る) */
  qualification: StaffQualification | '';
  note: string;
  /** 連絡先の電話 (入力のまま。保存のときにサーバーが数字だけにそろえる)。 */
  phone: string;
  /** 連絡用メール (ログイン用とは別)。 */
  contact_email: string;
  notify_channel: StaffNotifyChannel;
}

/** 連絡先の形の誤り (保存の前に止める)。key は StaffFormState の欄名。 */
export function staffContactErrors(form: StaffFormState): Record<string, string> {
  const out: Record<string, string> = {};
  const p = classifyPhone(form.phone);
  if (p && !p.ok) out.phone = '電話番号の形が違います';
  const e = classifyEmail(form.contact_email);
  if (e && !e.ok) out.contact_email = 'メールアドレスの形が違います';
  return out;
}

const NOTIFY_OPTIONS: ReadonlyArray<readonly [StaffNotifyChannel, string]> = [
  ['sms', 'SMS'],
  ['email', 'メール'],
  ['none', '受け取らない'],
];

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
  /** 新規登録時のみ true: コード空欄で自動採番される旨のヒントを出す。 */
  codeAutoHint?: boolean;
}

export function StaffFormFields({
  form,
  errors,
  onChange,
  sexOptions,
  roleOptions,
  statusOptions,
  codeAutoHint = false,
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
            placeholder={codeAutoHint ? '空欄で自動採番' : '例: S001'}
            maxLength={64}
          />
          {codeAutoHint && (
            <p className="mt-1 text-xs text-text-muted">
              空欄のまま登録すると、次の番号（S001 形式）が自動で割り当てられます
            </p>
          )}
        </Field>

        <Field label="性別" error={errors.sex}>
          <SelectInput
            value={form.sex}
            onChange={(v) => set('sex', v as SexValue | '')}
            options={[{ value: '', label: '未指定' }, ...sexOptions]}
          />
        </Field>

        <Field
          label="業務ロール"
          required
          error={errors.role}
          hint="マネージャーは自動スタッフ割当の対象外になります（埋まらないコースの救済にだけ入ります）。ログイン権限（管理者/一般）とは無関係です"
        >
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

        {/* 資格 (K-1b カイポケ「職種」列)。准看護師のときだけカイポケの
            サービス内容が「・准看」になる (kaipoke-service-content-design.md §1-2)。 */}
        <Field
          label="資格"
          error={errors.qualification}
          hint="カイポケの職種・サービス内容（正看/准看）に反映されます"
        >
          <SelectInput
            value={form.qualification}
            onChange={(v) => set('qualification', v as StaffQualification | '')}
            options={[
              { value: '' as StaffQualification | '', label: '未設定' },
              ...STAFF_QUALIFICATION_VALUES.map((v) => ({
                value: v as StaffQualification | '',
                label: v,
              })),
            ]}
            testId="staff-qualification-select"
          />
        </Field>

        <Field label="主担当拠点" error={errors.primary_office_id}>
          <OfficeCombobox
            value={form.primary_office_id}
            onChange={(v) => set('primary_office_id', v)}
          />
        </Field>

        <Field
          label="新人スタッフ"
          error={errors.is_trainee}
          hint="同行スタッフ割付が必要な場合にオン"
        >
          <label className="flex cursor-pointer items-center gap-2 pt-1">
            <input
              type="checkbox"
              checked={form.is_trainee}
              onChange={(e) => set('is_trainee', e.target.checked)}
              className="h-4 w-4 rounded border-border-default accent-brand-primary"
            />
            <span className="text-sm text-text-primary">新人フラグをオン</span>
          </label>
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

      <section
        className="space-y-4 border-t border-border-default pt-4"
        data-testid="staff-contacts-section"
      >
        <h3 className="font-serif text-base font-bold text-text-primary">連絡先</h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="電話番号" error={errors.phone}>
            <Input
              type="tel"
              value={form.phone}
              onChange={(e) => set('phone', e.target.value)}
              placeholder="例：090-1234-5678"
              data-testid="staff-phone-input"
            />
            <div className="mt-1">
              <PhoneJudge result={classifyPhone(form.phone)} />
            </div>
          </Field>
          <Field label="メールアドレス（連絡用）" error={errors.contact_email}>
            <Input
              type="email"
              value={form.contact_email}
              onChange={(e) => set('contact_email', e.target.value)}
              data-testid="staff-contact-email-input"
            />
            <div className="mt-1">
              <EmailJudge raw={form.contact_email} />
            </div>
          </Field>
          <Field
            label="らく助からのお知らせ"
            error={errors.notify_channel}
            hint="今は使いません。休みの承認や急な代わりのお知らせを送るときのために、置いておくだけです。"
          >
            <div
              role="group"
              aria-label="らく助からのお知らせ"
              className="inline-flex w-fit divide-x divide-border-default overflow-hidden rounded-md border border-border-default"
            >
              {NOTIFY_OPTIONS.map(([v, label]) => (
                <button
                  key={v}
                  type="button"
                  aria-pressed={form.notify_channel === v}
                  onClick={() => set('notify_channel', v)}
                  className={`px-3 py-1.5 text-sm ${
                    form.notify_channel === v
                      ? 'bg-brand-primary/10 font-bold text-brand-primary'
                      : 'bg-bg-base text-text-primary hover:bg-bg-muted'
                  }`}
                  data-testid={`staff-notify-${v}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </Field>
        </div>
        <p className="text-xs text-text-muted">
          ⓘ ログインに使うメールアドレス（アカウントの欄）とは別です。連絡用のアドレスを入れます。
        </p>
      </section>
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
  testId,
}: {
  value: T;
  onChange: (v: T) => void;
  options: Option<T>[];
  testId?: string;
}) {
  return (
    <select
      value={value}
      data-testid={testId}
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
