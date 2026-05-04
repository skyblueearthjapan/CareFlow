import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });

export const metadata: Metadata = {
  title: 'CareLink',
  description: '訪問看護スケジューリング — Warm & Human',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja" data-density="standard" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
