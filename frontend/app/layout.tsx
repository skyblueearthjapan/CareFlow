import type { Metadata, Viewport } from 'next';
import Script from 'next/script';
import './globals.css';
import { Providers } from './providers';

/**
 * フォント (mac-ui-crossplatform-design.md §2-A・2026-08-29):
 * Noto Sans JP を **ランタイムで Google Fonts から読み込む** (<link>)。以前は
 * next/font の Inter だけを読み込み (しかも生成変数 --font-inter は未参照で無効)、
 * 日本語は mac=Hiragino Sans / Windows=Yu Gothic UI と OS ごとに別フォントで
 * 描画されていた。OS 差を消すため (field) レイアウトと同じ方式に統一する。
 * next/font/google は CJK サブセットをオフラインのビルドコンテナが取得できず
 * 失敗するため使わない。
 */
const APP_FONTS_HREF =
  // Serif (見出し) は従来どおり OS フォールバック (見た目を変えない・レビュー MED-5)。
  'https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&display=swap';

export const metadata: Metadata = {
  title: 'らく助 — 訪問看護 楽々スケジュール',
  description: '訪問看護のスケジュール管理をもっと楽しく、もっとスムーズに！',
  applicationName: 'らく助',
  appleWebApp: {
    capable: true,
    title: 'らく助',
    statusBarStyle: 'default',
  },
};

export const viewport: Viewport = {
  // ブラウザ/PWAのトップバー色。操作色 #e15a7f より一段淡い上品ピンク (シート原色) — PO要望 2026-07-10
  themeColor: '#f8b4c6',
};

// Static SW registration script (no dynamic content; safe to inline).
// `afterInteractive` Script strategy already runs post-load — no extra `load` listener needed.
//
// controllerchange → reload: 新ビルドの SW が有効化 (skipWaiting + clients.claim) されると
// 旧ビルドのタブは「旧チャンク 404 → Application error」で固まるため、制御が切り替わった
// 瞬間に 1 回だけ自動リロードして新ビルドへ乗り換える。初回インストール時
// (直前まで controller 無し) はリロードしない。
const SW_REGISTER_SRC = `if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(console.error);var hadController=!!navigator.serviceWorker.controller;var reloaded=false;navigator.serviceWorker.addEventListener('controllerchange',function(){if(!hadController||reloaded)return;reloaded=true;window.location.reload();});}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja" data-density="standard">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link rel="stylesheet" href={APP_FONTS_HREF} />
      </head>
      <body>
        <Providers>{children}</Providers>
        {process.env.NODE_ENV === 'production' && (
          <Script id="sw-register" strategy="afterInteractive">
            {SW_REGISTER_SRC}
          </Script>
        )}
      </body>
    </html>
  );
}
