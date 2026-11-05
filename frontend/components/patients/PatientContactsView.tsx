/**
 * 患者の詳細の「連絡先」の表 (管理者だけ)。
 *
 * 見本 docs/mockups/contact-phone-email-mock-2026-11-05.html「患者の詳細・一覧」。
 * 一覧の「お知らせ」列の札 (`ContactSummaryPill`) もここに置く。
 */
'use client';

import { Card } from '@/components/ui/card';
import { PHONE_KIND_LABEL, type PhoneKind } from '@/lib/contacts/phone';
import { usePatientContacts } from '@/lib/queries/patientContacts';
import {
  CONTACT_RELATION_LABEL,
  type ContactRead,
  type ContactSummaryItem,
} from '@/lib/schemas/patientContacts';

import { Pill, consentDateText } from './PatientContactsCard';

function phoneKindPill(c: ContactRead) {
  const kind = c.phone_kind as PhoneKind | null | undefined;
  if (!kind || !(kind in PHONE_KIND_LABEL)) return null;
  if (kind === 'mobile') return <Pill tone="ok">携帯・SMS 可</Pill>;
  if (kind === 'mobile_new') return <Pill tone="warn">携帯（060）・SMS は確認中</Pill>;
  if (kind === 'landline') return <Pill tone="info">固定電話</Pill>;
  return <Pill tone="mute">{PHONE_KIND_LABEL[kind]}</Pill>;
}

export function PatientContactsView({
  patientId,
  patientName,
}: {
  patientId: string;
  patientName: string;
}) {
  const { data, isLoading, isError } = usePatientContacts(patientId, true);
  const rows = data ?? [];

  return (
    <Card className="p-5 space-y-3" data-testid="patient-contacts-view">
      <h2 className="font-serif text-lg font-bold text-text-primary">連絡先</h2>
      {isLoading ? (
        <p className="text-sm text-text-muted">読み込み中…</p>
      ) : isError ? (
        <p className="text-sm text-error">連絡先を読み込めませんでした</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-text-muted">
          登録されていません（編集の画面の「連絡先」から足せます）
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border-default text-left text-text-secondary">
              <tr>
                <th className="px-3 py-2 font-medium">送り先</th>
                <th className="px-3 py-2 font-medium">お名前</th>
                <th className="px-3 py-2 font-medium">電話</th>
                <th className="px-3 py-2 font-medium">メール</th>
                <th className="px-3 py-2 font-medium">お知らせ</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const self = c.relation === 'self';
                const relation = `${CONTACT_RELATION_LABEL[c.relation]}${
                  !self && c.relation_note ? `（${c.relation_note}）` : ''
                }`;
                const dateText = consentDateText(c.consent_status, c.consent_on);
                return (
                  <tr key={c.id} className="border-b border-border-default align-top last:border-0">
                    <td className="space-y-1 px-3 py-2">
                      {c.is_notify_target ? (
                        <div>
                          <Pill tone="acc">お知らせを送る</Pill>
                        </div>
                      ) : null}
                      <div>{relation}</div>
                    </td>
                    <td className="px-3 py-2">
                      {self ? `本人（${patientName} 様）` : c.name || '--'}
                    </td>
                    <td className="space-y-1 px-3 py-2">
                      <div className="tnum">{c.phone_display ?? c.phone ?? '—'}</div>
                      {phoneKindPill(c)}
                    </td>
                    <td className="space-y-1 px-3 py-2">
                      <div className="break-all">{c.email ?? '—'}</div>
                      {c.email_carrier ? (
                        <div className="text-xs text-warning-strong">
                          携帯会社のアドレス：迷惑メールの設定で届かないことがあります
                        </div>
                      ) : null}
                    </td>
                    <td className="space-y-1 px-3 py-2">
                      {c.consent_status === 'ng' ? (
                        <Pill tone="mute">NG 回答</Pill>
                      ) : !c.is_notify_target ? (
                        <span className="text-xs text-text-muted">控え</span>
                      ) : c.effective_channel === 'none' ? (
                        <Pill tone="err">送れる番号・メールがありません</Pill>
                      ) : (
                        <Pill tone={c.consent_status === 'confirmed' ? 'ok' : 'warn'}>
                          {c.effective_channel === 'sms' ? 'SMS' : 'メール'}
                        </Pill>
                      )}
                      <div className="text-xs text-text-muted">
                        {c.consent_status === 'unconfirmed'
                          ? '同意は未確認'
                          : dateText
                            ? `同意 ${dateText}`
                            : ''}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export type ContactSummaryKind = 'sms_email' | 'pending' | 'none' | 'ng';

/**
 * 一覧の「お知らせ」列の分け方。
 * 送れる (同意済みで手段あり) が 1 件でもあれば SMS／メール。無ければ
 * 連絡先なし・手段なし → 送り先なし、送る先が同意待ち → 同意を確認中、NG 回答だけ → NG 回答。
 */
export function contactSummaryKind(s: ContactSummaryItem | undefined): ContactSummaryKind {
  if (!s || s.contact_count === 0) return 'none';
  if (s.sms > 0 || s.email > 0) return 'sms_email';
  if (s.targets > 0 && s.no_means >= s.targets) return 'none';
  if (s.pending_consent > 0) return 'pending';
  if (s.ng > 0) return 'ng';
  return 'none';
}

export function ContactSummaryPill({ summary }: { summary: ContactSummaryItem | undefined }) {
  const kind = contactSummaryKind(summary);
  if (kind === 'sms_email' && summary) {
    return (
      <span className="flex flex-wrap gap-1" data-testid="contact-summary-pill">
        {summary.sms > 0 ? <Pill tone="ok">SMS {summary.sms} 件</Pill> : null}
        {summary.email > 0 ? (
          <Pill tone="info">{summary.email > 1 ? `メール ${summary.email} 件` : 'メール'}</Pill>
        ) : null}
      </span>
    );
  }
  const map = {
    pending: ['warn', '同意を確認中'],
    ng: ['mute', 'NG 回答'],
    none: ['err', '送り先なし'],
  } as const;
  const [tone, label] = map[kind as keyof typeof map];
  return (
    <span data-testid="contact-summary-pill">
      <Pill tone={tone}>{label}</Pill>
    </span>
  );
}
