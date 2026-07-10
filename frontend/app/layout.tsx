import type { Metadata, Viewport } from 'next';
import Script from 'next/script';
import { Inter } from 'next/font/google';
import './globals.css';
import { Providers } from './providers';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });

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
  themeColor: '#e15a7f',
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
    <html lang="ja" data-density="standard" className={inter.variable}>
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
