import type { Metadata, Viewport } from 'next';
import Script from 'next/script';
import { Inter } from 'next/font/google';
import './globals.css';
import { Providers } from './providers';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });

export const metadata: Metadata = {
  title: 'CareFlow',
  description: '訪問看護スケジューリング — Warm & Human',
  applicationName: 'CareFlow',
  appleWebApp: {
    capable: true,
    title: 'CareFlow',
    statusBarStyle: 'default',
  },
};

export const viewport: Viewport = {
  themeColor: '#0d9488',
};

// Static SW registration script (no dynamic content; safe to inline).
// `afterInteractive` Script strategy already runs post-load — no extra `load` listener needed.
const SW_REGISTER_SRC = `if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(console.error);}`;

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
