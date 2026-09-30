/**
 * 事業所の情報 (business_profile) zod schemas — mig 0089。
 *
 * Mirrors the backend contract (`/api/v1/business-profile`):
 *   GET → 5 項目 (未設定は null)。全ログインユーザ。
 *   PUT → 部分更新 (省略=不変 / null・空文字=未設定に戻す)。admin のみ。
 *
 * 患者 QR カード (A5) の「お問い合わせ先」とロゴに使う。未設定の項目はカードに載せない。
 * 以前は `lib/qr-print-contact.ts` にお客様の値を直接書いていた (別の事業所へ提供する準備 #1)。
 */
import { z } from 'zod';

export const businessProfileSchema = z.object({
  /** 事業所名 (カード下部の署名)。 */
  station_name: z.string().nullable(),
  /** お問い合わせの電話番号。 */
  contact_tel: z.string().nullable(),
  /** 電話の対応時間。 */
  contact_hours: z.string().nullable(),
  /** 電話の対応日 (休業の注記込み)。 */
  contact_days: z.string().nullable(),
  /** ロゴ画像のパス (`/brand/...`) または `https://` の URL。null はロゴなし。 */
  logo_url: z.string().nullable(),
});

export type BusinessProfile = z.infer<typeof businessProfileSchema>;

export const businessProfileUpdateSchema = businessProfileSchema.partial();
export type BusinessProfileUpdate = z.infer<typeof businessProfileUpdateSchema>;

/** 入力の上限 (BE の max_length と同じ)。 */
export const BUSINESS_PROFILE_MAX_LENGTH: Record<keyof BusinessProfile, number> = {
  station_name: 120,
  contact_tel: 40,
  contact_hours: 60,
  contact_days: 120,
  logo_url: 255,
};

/** ロゴに指定できる形 (アプリに同梱した画像のパス、または https の URL)。BE と同じ規則。 */
export function isAllowedLogoUrl(value: string): boolean {
  const v = value.trim();
  if (v === '') return true;
  if (v.startsWith('//')) return false;
  return v.startsWith('/') || v.startsWith('https://');
}
