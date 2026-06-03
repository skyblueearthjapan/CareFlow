/**
 * (field) ルートグループ — CareFlow Mobile 現場ボード専用の独立シェル。
 *
 * 既存の (app) / (mobile) シェル (サイドバー・ボトムナビ) を一切使わない
 * フルスクリーンレイアウト。フォント (Noto Serif JP / Noto Sans JP /
 * JetBrains Mono) は **このグループにのみ** CSS 変数として適用し、
 * アプリ他画面のフォントには影響させない。
 */
import type { Metadata, Viewport } from 'next';

import './field.css';

// フォント (Noto Sans/Serif JP・JetBrains Mono) は **ランタイムで Google Fonts
// から読み込む** (<link>)。next/font/google はビルド時にフォントを取得するが、
// 巨大な CJK サブセットをオフラインのビルドコンテナが取得できず失敗するため不使用。
// font-family は field.css の .cf-field-root スコープ内でのみ参照され、他画面に漏れない。
const FIELD_FONTS_HREF =
  'https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&family=Noto+Serif+JP:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap';

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
    <>
      {/* Next.js は layout 内の <link> を <head> へ巻き上げる。preconnect でフォント取得を高速化。 */}
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      <link rel="stylesheet" href={FIELD_FONTS_HREF} />
      <div className="cf-field-root">{children}</div>
    </>
  );
}
