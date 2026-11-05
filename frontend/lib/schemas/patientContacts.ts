/**
 * 患者様の連絡先 (電話・メール) の API の形。
 *
 * 設計 docs/plans/contacts-phone-email-design-2026-11-05.md §5 に 1:1 で合わせる。
 * すべて管理者だけの API (`require_role("admin")`)。
 */
import { z } from 'zod';

export const CONTACT_RELATIONS = ['self', 'family', 'facility', 'care_manager', 'other'] as const;
export type ContactRelation = (typeof CONTACT_RELATIONS)[number];

export const CONTACT_RELATION_LABEL: Record<ContactRelation, string> = {
  self: 'ご本人',
  family: 'ご家族',
  facility: '施設',
  care_manager: 'ケアマネ',
  other: 'その他',
};

export const NOTIFY_CHANNELS = ['sms', 'email'] as const;
export type NotifyChannel = (typeof NOTIFY_CHANNELS)[number];
export type EffectiveChannel = NotifyChannel | 'none';

export const CONSENT_STATUSES = ['unconfirmed', 'confirmed', 'ng'] as const;
export type ConsentStatus = (typeof CONSENT_STATUSES)[number];

export const CONSENT_LABEL: Record<ConsentStatus, string> = {
  unconfirmed: '未確認',
  confirmed: '確認済み',
  ng: 'NG 回答',
};

/** 連絡先は 3 件まで。 */
export const MAX_PATIENT_CONTACTS = 3;

export const contactReadSchema = z.object({
  id: z.string(),
  sort_order: z.number(),
  relation: z.enum(CONTACT_RELATIONS),
  name: z.string().nullable().optional(),
  relation_note: z.string().nullable().optional(),
  phone: z.string().nullable().optional(),
  phone_display: z.string().nullable().optional(),
  phone_kind: z.string().nullable().optional(),
  sms_capable: z.boolean().nullable().optional(),
  email: z.string().nullable().optional(),
  email_carrier: z.boolean().nullable().optional(),
  is_notify_target: z.boolean(),
  notify_channel: z.enum(NOTIFY_CHANNELS),
  effective_channel: z.enum(['sms', 'email', 'none']),
  consent_status: z.enum(CONSENT_STATUSES),
  consent_on: z.string().nullable().optional(),
});
export type ContactRead = z.infer<typeof contactReadSchema>;

export const contactListSchema = z.object({
  contacts: z.array(contactReadSchema),
  /** PUT の返り値だけ。自動で移したときの日本語の知らせ。 */
  notices: z.array(z.string()).optional().default([]),
});
export type ContactList = z.infer<typeof contactListSchema>;

export interface ContactWrite {
  id?: string;
  relation: ContactRelation;
  name?: string | null;
  relation_note?: string | null;
  phone?: string | null;
  email?: string | null;
  is_notify_target: boolean;
  notify_channel: NotifyChannel;
  consent_status: ConsentStatus;
}

export const contactSummaryItemSchema = z.object({
  patient_id: z.string(),
  contact_count: z.number(),
  targets: z.number(),
  sms: z.number(),
  email: z.number(),
  no_means: z.number(),
  pending_consent: z.number(),
  ng: z.number(),
});
export type ContactSummaryItem = z.infer<typeof contactSummaryItemSchema>;

export const contactSummarySchema = z.object({
  items: z.array(contactSummaryItemSchema),
});
