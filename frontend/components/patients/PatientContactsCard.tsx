/**
 * 患者の編集の「連絡先」カード (管理者だけ)。
 *
 * 見本 docs/mockups/contact-phone-email-mock-2026-11-05.html「患者の編集」。
 * 中身は親が持つ (controlled)。保存は親が患者の保存の後に PUT する。
 */
'use client';

import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  addContact,
  allTargets,
  contactLabel,
  contactMeans,
  effectiveChannel,
  onlySelf,
  removeContact,
  setConsent,
  setNotifyTarget,
  type ContactDraft,
  type EditResult,
} from '@/lib/contacts/patientContacts';
import {
  PHONE_ERROR_LABEL,
  PHONE_KIND_LABEL,
  classifyEmail,
  classifyPhone,
  type PhoneResult,
} from '@/lib/contacts/phone';
import {
  CONSENT_LABEL,
  CONSENT_STATUSES,
  CONTACT_RELATIONS,
  CONTACT_RELATION_LABEL,
  MAX_PATIENT_CONTACTS,
  type ConsentStatus,
  type ContactRelation,
  type NotifyChannel,
} from '@/lib/schemas/patientContacts';
import { cn } from '@/lib/utils';

type PillTone = 'ok' | 'warn' | 'err' | 'info' | 'mute' | 'acc';

const PILL_CLASS: Record<PillTone, string> = {
  ok: 'bg-success-bg text-success',
  warn: 'bg-warning-bg text-warning-strong',
  err: 'bg-error-bg text-error',
  info: 'bg-info-bg text-info-strong',
  mute: 'bg-bg-muted text-text-muted',
  acc: 'bg-brand-primary/10 text-brand-primary',
};

export function Pill({
  tone,
  children,
  testId,
}: {
  tone: PillTone;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <span
      className={cn(
        'inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium',
        PILL_CLASS[tone],
      )}
      data-testid={testId}
    >
      {children}
    </span>
  );
}

const PHONE_KIND_TONE: Record<string, PillTone> = {
  mobile: 'ok',
  mobile_new: 'warn',
  ip: 'mute',
  data: 'mute',
  freedial: 'mute',
  landline: 'info',
};

/** 電話番号の見分けの結果 (入力欄の下)。 */
export function PhoneJudge({ result }: { result: PhoneResult | null }) {
  if (!result) return null;
  if (!result.ok) {
    return (
      <span className="flex flex-wrap items-center gap-1" data-testid="phone-judge">
        <Pill tone="err">{PHONE_ERROR_LABEL[result.error]}</Pill>
        <span className="text-xs text-text-muted">保存の前に確かめてください</span>
      </span>
    );
  }
  const sms =
    result.smsCapable === true ? (
      <Pill tone="ok">SMS 可</Pill>
    ) : result.smsCapable === false ? (
      <Pill tone="mute">SMS 不可</Pill>
    ) : (
      <Pill tone="warn">SMS は確認中</Pill>
    );
  return (
    <span className="flex flex-wrap items-center gap-1" data-testid="phone-judge">
      <Pill tone={PHONE_KIND_TONE[result.kind] ?? 'mute'}>{PHONE_KIND_LABEL[result.kind]}</Pill>
      {sms}
      <span className="tnum text-xs text-text-muted">
        保存：{result.digits}　見せ方：{result.display}
      </span>
    </span>
  );
}

/** メールの見分けの結果 (入力欄の下)。 */
export function EmailJudge({ raw }: { raw: string }) {
  const r = classifyEmail(raw);
  if (!r) return null;
  if (!r.ok) {
    return (
      <span data-testid="email-judge">
        <Pill tone="err">メールの形が違う</Pill>
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-1" data-testid="email-judge">
      <Pill tone="ok">メール可</Pill>
      {r.carrier ? (
        <span className="text-xs text-warning-strong">
          携帯会社のアドレス：迷惑メールの設定で届かないことがあります
        </span>
      ) : null}
    </span>
  );
}

/** 「11/5 に確認（自動で記録）」。 */
export function consentDateText(status: ConsentStatus, on: string | null | undefined): string {
  if (status === 'unconfirmed' || !on) return '';
  const m = Number(on.slice(5, 7));
  const d = Number(on.slice(8, 10));
  return `${m}/${d} に${status === 'confirmed' ? '確認' : 'NG 回答'}`;
}

interface PatientContactsCardProps {
  value: ContactDraft[];
  onChange: (next: ContactDraft[]) => void;
  /** 「本人（〇〇 様）」に出す氏名 (フォームの今の値)。 */
  patientName: string;
  disabled?: boolean;
  /** 連絡先の保存の誤り (患者の保存とは別に出す)。 */
  errorMessage?: string | null;
  /** 連絡先だけを保存し直す (患者の保存の後に連絡先だけ失敗したとき)。 */
  onRetry?: () => void;
  retrying?: boolean;
}

const inputClass =
  'flex h-10 w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light disabled:cursor-not-allowed disabled:opacity-60';

export function PatientContactsCard({
  value,
  onChange,
  patientName,
  disabled = false,
  errorMessage = null,
  onRetry,
  retrying = false,
}: PatientContactsCardProps) {
  const [notice, setNotice] = React.useState<string | null>(null);

  const apply = (r: EditResult) => {
    setNotice(r.notice);
    onChange(r.contacts);
  };
  const patch = (i: number, p: Partial<ContactDraft>) => {
    setNotice(null);
    onChange(value.map((c, k) => (k === i ? { ...c, ...p } : c)));
  };

  const targets = value.map((c, i) => (c.is_notify_target ? contactLabel(value, i) : null));
  const targetText = targets.filter(Boolean).join('・') || 'なし';
  const nonNgCount = value.filter((c) => c.consent_status !== 'ng').length;
  const hasSelf = value.some((c) => c.relation === 'self');
  const name = patientName.trim();

  return (
    <Card className="p-5 space-y-4" data-testid="patient-contacts-card">
      <h2 className="font-serif text-lg font-bold text-text-primary">連絡先</h2>
      <p className="text-xs text-text-muted">
        予定のお知らせを送る先です。1
        件目はご本人です。ご本人が携帯電話を持っていないときは、ご家族や施設の方を足してください。3
        件まで登録できます。
      </p>

      {errorMessage ? (
        <div
          className="space-y-2 rounded-md border border-border-error bg-error-bg px-3 py-2 text-sm text-error"
          role="alert"
          data-testid="contacts-error"
        >
          <p className="whitespace-pre-wrap">{errorMessage}</p>
          {onRetry ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onRetry}
              disabled={retrying}
              data-testid="contacts-retry"
            >
              {retrying ? '保存中…' : '連絡先をもう一度保存する'}
            </Button>
          ) : null}
        </div>
      ) : null}

      {value.length > 0 ? (
        <div
          className="space-y-2 rounded-md border border-brand-primary bg-brand-primary/5 px-3 py-2 text-sm"
          data-testid="contacts-summary"
        >
          <div>
            <b>お知らせを送る先：</b>
            <span data-testid="contacts-targets">{targetText}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => apply(onlySelf(value))}
            >
              ご本人だけ
            </Button>
            {value.length >= 2 ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={disabled}
                onClick={() => apply(allTargets(value))}
              >
                登録した全員に送る（{nonNgCount} 件）
              </Button>
            ) : null}
          </div>
          {notice ? (
            <p
              className="rounded-md bg-info-bg px-2 py-1 text-xs text-info-strong"
              data-testid="contacts-notice"
            >
              ⓘ {notice}
            </p>
          ) : null}
        </div>
      ) : notice ? (
        <p
          className="rounded-md bg-info-bg px-2 py-1 text-xs text-info-strong"
          data-testid="contacts-notice"
        >
          ⓘ {notice}
        </p>
      ) : null}

      <div className="space-y-3">
        {value.map((c, i) => {
          const self = c.relation === 'self';
          const phone = classifyPhone(c.phone);
          const means = contactMeans(c);
          const chan = effectiveChannel(c);
          let status: React.ReactNode = null;
          if (c.is_notify_target) {
            if (chan === 'none') status = <Pill tone="err">送れる番号・メールがありません</Pill>;
            else if (c.consent_status !== 'confirmed')
              status = <Pill tone="warn">同意の確認待ち（まだ送りません）</Pill>;
            else status = <Pill tone="ok">{chan === 'sms' ? 'SMS' : 'メール'}で送ります</Pill>;
          } else if (c.consent_status === 'ng') {
            status = <Pill tone="mute">NG 回答のため送りません</Pill>;
          }
          const dateText = consentDateText(c.consent_status, c.consent_on);
          return (
            <div
              key={c.key}
              className={cn(
                'space-y-3 rounded-md border p-3',
                c.is_notify_target
                  ? 'border-brand-primary bg-brand-primary/5'
                  : 'border-border-default',
              )}
              data-testid={`contact-row-${i}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex flex-wrap items-center gap-3 font-bold text-text-primary">
                  {i + 1} 件目
                  <label className="inline-flex cursor-pointer items-center gap-1 text-sm font-medium">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-border-default accent-brand-primary"
                      checked={c.is_notify_target}
                      disabled={disabled || c.consent_status === 'ng'}
                      onChange={(e) => apply(setNotifyTarget(value, i, e.target.checked))}
                      data-testid={`contact-target-${i}`}
                    />
                    お知らせを送る
                  </label>
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  {status}
                  {value.length > 1 ? (
                    <button
                      type="button"
                      className="text-sm text-brand-primary underline disabled:opacity-50"
                      disabled={disabled}
                      onClick={() => apply(removeContact(value, i))}
                      data-testid={`contact-remove-${i}`}
                    >
                      外す
                    </button>
                  ) : null}
                </span>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-text-secondary">送り先</span>
                  <select
                    className={inputClass}
                    value={c.relation}
                    disabled={disabled}
                    onChange={(e) => patch(i, { relation: e.target.value as ContactRelation })}
                    data-testid={`contact-relation-${i}`}
                  >
                    {CONTACT_RELATIONS.map((r) => (
                      <option key={r} value={r} disabled={r === 'self' && !self && hasSelf}>
                        {CONTACT_RELATION_LABEL[r]}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-text-secondary">お名前・続柄や施設名</span>
                  {self ? (
                    <>
                      <Input
                        value={`本人（${name || '氏名'} 様）`}
                        disabled
                        readOnly
                        aria-label="お名前"
                        data-testid={`contact-self-name-${i}`}
                      />
                      <span className="text-xs text-text-muted">ご本人は自動で入ります</span>
                    </>
                  ) : (
                    <span className="grid grid-cols-2 gap-2">
                      <Input
                        value={c.name}
                        placeholder="お名前"
                        aria-label="お名前"
                        maxLength={120}
                        disabled={disabled}
                        onChange={(e) => patch(i, { name: e.target.value })}
                        data-testid={`contact-name-${i}`}
                      />
                      <Input
                        value={c.relation_note}
                        placeholder="例：長女、〇〇ホーム"
                        aria-label="続柄・施設名"
                        maxLength={120}
                        disabled={disabled}
                        onChange={(e) => patch(i, { relation_note: e.target.value })}
                        data-testid={`contact-note-${i}`}
                      />
                    </span>
                  )}
                </div>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-text-secondary">電話番号</span>
                  <Input
                    type="tel"
                    value={c.phone}
                    placeholder="例：090-1234-5678"
                    disabled={disabled}
                    onChange={(e) => patch(i, { phone: e.target.value })}
                    data-testid={`contact-phone-${i}`}
                  />
                  <PhoneJudge result={phone} />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-text-secondary">メールアドレス</span>
                  <Input
                    type="email"
                    value={c.email}
                    placeholder="携帯電話がないときに"
                    disabled={disabled}
                    onChange={(e) => patch(i, { email: e.target.value })}
                    data-testid={`contact-email-${i}`}
                  />
                  <EmailJudge raw={c.email} />
                </label>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {c.is_notify_target ? (
                  <div className="flex flex-col gap-1 text-sm">
                    <span className="font-medium text-text-secondary">お知らせの手段</span>
                    <div
                      role="group"
                      aria-label="お知らせの手段"
                      className="inline-flex w-fit divide-x divide-border-default overflow-hidden rounded-md border border-border-default"
                    >
                      {(
                        [
                          ['sms', 'SMS', means.sms],
                          ['email', 'メール', means.email],
                        ] as const
                      ).map(([k, label, ok]) => (
                        <button
                          key={k}
                          type="button"
                          aria-pressed={chan === k}
                          disabled={disabled || !ok}
                          onClick={() => patch(i, { notify_channel: k as NotifyChannel })}
                          className={cn(
                            'px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:text-text-muted',
                            chan === k
                              ? 'bg-brand-primary/10 font-bold text-brand-primary'
                              : 'bg-bg-base text-text-primary hover:bg-bg-muted',
                          )}
                          data-testid={`contact-channel-${k}-${i}`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <span className="text-xs text-text-muted">
                      携帯の番号があれば SMS が先に選ばれます。
                    </span>
                  </div>
                ) : (
                  <div />
                )}
                <div className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-text-secondary">送ってよいかの同意</span>
                  <span className="flex flex-wrap items-center gap-2">
                    <select
                      className={cn(inputClass, 'w-auto')}
                      value={c.consent_status}
                      disabled={disabled}
                      onChange={(e) => apply(setConsent(value, i, e.target.value as ConsentStatus))}
                      data-testid={`contact-consent-${i}`}
                    >
                      {CONSENT_STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {CONSENT_LABEL[s]}
                        </option>
                      ))}
                    </select>
                    {dateText ? (
                      <span
                        className="text-xs text-text-muted"
                        data-testid={`contact-consent-date-${i}`}
                      >
                        {dateText}（自動で記録）
                      </span>
                    ) : null}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || value.length >= MAX_PATIENT_CONTACTS}
          onClick={() => apply(addContact(value))}
          data-testid="contacts-add"
        >
          ＋ 連絡先を足す
        </Button>
      </div>
      <p className="text-xs text-text-muted">
        ⓘ「お知らせを送る」は連絡先ごとに付けられ、2 件・3 件にも付けられます。少なくとも 1
        件には必ず送ります。最後の 1
        件を外すと、ほかの連絡先へ自動で移ります。付けない連絡先は、電話をかけるときの控えとして残ります。
      </p>
      <p className="text-xs text-text-muted">
        ⓘ 送り先が 2 件なら、SMS の料金も 2 通分（1 通 11 円）になります。
      </p>
    </Card>
  );
}
