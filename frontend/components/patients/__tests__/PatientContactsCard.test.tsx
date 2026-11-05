/**
 * 患者の編集の「連絡先」カード (見本 contact-phone-email-mock-2026-11-05「患者の編集」)。
 * 番号はすべて見本。
 */
import * as React from 'react';
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { PatientContactsCard } from '../PatientContactsCard';
import { contactSummaryKind } from '../PatientContactsView';
import { todayJst, type ContactDraft } from '@/lib/contacts/patientContacts';

function d(p: Partial<ContactDraft>): ContactDraft {
  return {
    key: Math.random().toString(36),
    relation: 'family',
    name: '',
    relation_note: '',
    phone: '',
    email: '',
    is_notify_target: false,
    notify_channel: 'sms',
    consent_status: 'unconfirmed',
    consent_on: null,
    ...p,
  };
}

function Harness({ initial }: { initial: ContactDraft[] }) {
  const [value, setValue] = React.useState(initial);
  return <PatientContactsCard value={value} onChange={setValue} patientName="見本 花子" />;
}

const SELF = d({
  key: 'a',
  relation: 'self',
  phone: '080-2222-3333',
  is_notify_target: true,
  consent_status: 'confirmed',
  consent_on: '2026-11-02',
  orig_consent_status: 'confirmed',
  orig_consent_on: '2026-11-02',
});
const FAMILY = d({ key: 'b', name: '見本 由美', relation_note: '長女', phone: '090-1234-5678' });

describe('PatientContactsCard', () => {
  it('ご本人は「本人（氏名 様）」で入力できない・同意の日を出す', () => {
    render(<Harness initial={[SELF, FAMILY]} />);
    const selfName = screen.getByTestId('contact-self-name-0') as HTMLInputElement;
    expect(selfName.value).toBe('本人（見本 花子 様）');
    expect(selfName).toBeDisabled();
    expect(screen.getByTestId('contact-consent-date-0')).toHaveTextContent(
      '11/2 に確認（自動で記録）',
    );
    expect(screen.getByTestId('contacts-targets')).toHaveTextContent('1 件目（ご本人）');
    expect(screen.getByTestId('contact-row-0')).toHaveTextContent('SMSで送ります');
  });

  it('最後の 1 件を外すとほかへ移り、知らせを出す', () => {
    render(
      <Harness
        initial={[
          { ...SELF, is_notify_target: false },
          { ...FAMILY, is_notify_target: true },
        ]}
      />,
    );
    fireEvent.click(screen.getByTestId('contact-target-1'));
    expect(screen.getByTestId('contact-target-0')).toBeChecked();
    expect(screen.getByTestId('contact-target-1')).not.toBeChecked();
    expect(screen.getByTestId('contacts-notice')).toHaveTextContent('1 件目（ご本人）へ移しました');
  });

  it('1 件だけのときは外せない', () => {
    render(<Harness initial={[SELF]} />);
    fireEvent.click(screen.getByTestId('contact-target-0'));
    expect(screen.getByTestId('contact-target-0')).toBeChecked();
    expect(screen.getByTestId('contacts-notice')).toHaveTextContent('少なくとも 1 件に送ります');
  });

  it('NG 回答は四角を使えなくし、送る先を移す・日付は今日', () => {
    render(<Harness initial={[SELF, FAMILY]} />);
    fireEvent.change(screen.getByTestId('contact-consent-0'), { target: { value: 'ng' } });
    expect(screen.getByTestId('contact-target-0')).toBeDisabled();
    expect(screen.getByTestId('contact-target-0')).not.toBeChecked();
    expect(screen.getByTestId('contact-target-1')).toBeChecked();
    const [, m, day] = todayJst().split('-');
    expect(screen.getByTestId('contact-consent-date-0')).toHaveTextContent(
      `${Number(m)}/${Number(day)} にNG 回答（自動で記録）`,
    );
    expect(screen.getByTestId('contact-row-0')).toHaveTextContent('NG 回答のため送りません');
  });

  it('ご本人だけ・登録した全員に送る', () => {
    render(<Harness initial={[SELF, FAMILY]} />);
    fireEvent.click(screen.getByRole('button', { name: '登録した全員に送る（2 件）' }));
    expect(screen.getByTestId('contact-target-1')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'ご本人だけ' }));
    expect(screen.getByTestId('contact-target-0')).toBeChecked();
    expect(screen.getByTestId('contact-target-1')).not.toBeChecked();
  });

  it('手段は使える方だけ押せる・電話の見分けを出す', () => {
    render(<Harness initial={[{ ...SELF, phone: '043-123-4567', email: 'a@docomo.ne.jp' }]} />);
    const row = screen.getByTestId('contact-row-0');
    expect(screen.getByTestId('contact-channel-sms-0')).toBeDisabled();
    expect(screen.getByTestId('contact-channel-email-0')).toHaveAttribute('aria-pressed', 'true');
    expect(within(row).getByTestId('phone-judge')).toHaveTextContent('固定電話');
    expect(within(row).getByTestId('email-judge')).toHaveTextContent('迷惑メールの設定');
    fireEvent.change(screen.getByTestId('contact-phone-0'), { target: { value: '090-123-456' } });
    expect(within(row).getByTestId('phone-judge')).toHaveTextContent('桁が足りない');
  });

  it('足すのは 3 件まで・2 件目からはご家族・ご本人は 1 件だけ選べる', () => {
    render(<Harness initial={[SELF]} />);
    fireEvent.click(screen.getByTestId('contacts-add'));
    fireEvent.click(screen.getByTestId('contacts-add'));
    expect(screen.getByTestId('contacts-add')).toBeDisabled();
    const rel = screen.getByTestId('contact-relation-1') as HTMLSelectElement;
    expect(rel.value).toBe('family');
    const selfOption = within(rel).getByRole('option', { name: 'ご本人' }) as HTMLOptionElement;
    expect(selfOption.disabled).toBe(true);
  });

  it('外すと送る先を残りへ移す', () => {
    render(
      <Harness
        initial={[
          { ...SELF, is_notify_target: false },
          { ...FAMILY, is_notify_target: true },
        ]}
      />,
    );
    fireEvent.click(screen.getByTestId('contact-remove-1'));
    expect(screen.queryByTestId('contact-row-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('contact-target-0')).toBeChecked();
    expect(screen.queryByTestId('contact-remove-0')).not.toBeInTheDocument();
  });
});

describe('contactSummaryKind', () => {
  const base = {
    patient_id: 'p',
    contact_count: 1,
    targets: 1,
    sms: 0,
    email: 0,
    no_means: 0,
    pending_consent: 0,
    ng: 0,
  };
  it.each([
    [undefined, 'none'],
    [{ ...base, contact_count: 0, targets: 0 }, 'none'],
    [{ ...base, sms: 1 }, 'sms_email'],
    [{ ...base, email: 1, pending_consent: 1, targets: 2 }, 'sms_email'],
    [{ ...base, no_means: 1, pending_consent: 1 }, 'none'],
    [{ ...base, pending_consent: 1 }, 'pending'],
    [{ ...base, targets: 0, ng: 1 }, 'ng'],
  ] as const)('%o → %s', (s, kind) => {
    expect(contactSummaryKind(s)).toBe(kind);
  });
});
