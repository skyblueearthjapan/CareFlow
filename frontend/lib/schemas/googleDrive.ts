/**
 * Google ドライブの接続 (勤務表のスプレッドシート・休み・勤務表の設計 §7-3) の zod schemas。
 *
 * Mirrors the backend contract (`/api/v1/google-drive/*`・管理者のみ):
 *   GET    /status        接続の状態
 *   POST   /connect       許可の画面の URL
 *   PUT    /folder        Picker で選んだ保存先フォルダ
 *   POST   /picker-token  Picker 用の短い寿命のアクセストークン・API キー・アプリ ID
 *   DELETE /connection    切断
 */
import { z } from 'zod';

export const googleDriveStatusSchema = z.object({
  /** .env にクライアント ID・秘密・リダイレクト URI がそろっているか。 */
  configured: z.boolean(),
  /** Picker の API キーとアプリ ID がそろっているか。 */
  picker_configured: z.boolean(),
  status: z.enum(['not_connected', 'connected', 'needs_reconnect']),
  folder_id: z.string().nullable(),
  folder_name: z.string().nullable(),
  /** 保存先フォルダがドライブに見つからない。 */
  folder_missing: z.boolean(),
  shared_drive_id: z.string().nullable(),
  connected_by_name: z.string().nullable(),
  last_error: z.string().nullable(),
  updated_at: z.string().nullable(),
});
export type GoogleDriveStatus = z.infer<typeof googleDriveStatusSchema>;

export const googleDriveConnectStartSchema = z.object({
  authorization_url: z.string().url(),
});

export const googleDrivePickerTokenSchema = z.object({
  access_token: z.string(),
  expires_in: z.number(),
  api_key: z.string(),
  app_id: z.string(),
});
export type GoogleDrivePickerToken = z.infer<typeof googleDrivePickerTokenSchema>;

/** 戻り (/settings/google-drive?result=error&reason=...) の理由 → 画面の文。 */
export const GOOGLE_DRIVE_CALLBACK_REASON: Record<string, string> = {
  denied:
    'Google の画面で許可されませんでした。もう一度「Google ドライブに接続」を押してください。',
  state:
    '接続の確認ができませんでした（時間が経った・別のブラウザーで開いた）。もう一度お試しください。',
  forbidden: '管理者のアカウントで接続してください。',
  exchange:
    'Google から許可を受け取れませんでした。許可の画面で「Google ドライブ」のチェックを入れて、もう一度お試しください。',
  invalid: 'Google からの戻りが正しくありませんでした。もう一度お試しください。',
  google: 'Google の画面で問題が起きました。もう一度お試しください。',
};
