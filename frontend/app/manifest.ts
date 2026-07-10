import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'らく助 — 訪問看護 楽々スケジュール',
    short_name: 'らく助',
    description: '訪問看護のスケジュール管理をもっと楽しく、もっとスムーズに！',
    start_url: '/',
    display: 'standalone',
    background_color: '#faf7f2',
    theme_color: '#e15a7f',
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
      {
        src: '/icons/maskable-512.png',
        sizes: '512x512',
        type: 'image/png',
        purpose: 'maskable',
      },
    ],
  };
}
