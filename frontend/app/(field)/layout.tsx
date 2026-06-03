/**
 * (field) ルートグループ — CareFlow Mobile 現場ボード専用の独立シェル。
 *
 * 既存の (app) / (mobile) シェル (サイドバー・ボトムナビ) を一切使わない
 * フルスクリーンレイアウト。フォント (Noto Serif JP / Noto Sans JP /
 * JetBrains Mono) は **このグループにのみ** CSS 変数として適用し、
 * アプリ他画面のフォントには影響させない。
 */
import type { Metadata, Viewport } from 'next';
import { Noto_Sans_JP, Noto_Serif_JP, JetBrains_Mono } from 'next/font/google';

import './field.css';

const notoSans = Noto_Sans_JP({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-field-sans',
  display: 'swap',
});
const notoSerif = Noto_Serif_JP({
  subsets: ['latin'],
  weight: ['500', '600', '700'],
  variable: '--font-field-serif',
  display: 'swap',
});
const jetBrains = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-field-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'CareFlow — 現場ボード',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // 現場ボードは独立フルスクリーン: ピンチズーム抑止で誤操作を防ぐ。
  maximumScale: 1,
  themeColor: '#0d9488',
};

export default function FieldLayout({ children }: { children: React.ReactNode }) {
  return (
    <div
      className={`${notoSans.variable} ${notoSerif.variable} ${jetBrains.variable} cf-field-root`}
    >
      {children}
    </div>
  );
}
