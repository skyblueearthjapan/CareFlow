import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'CareFlow',
    short_name: 'CareFlow',
    description: '訪問看護スケジューリング',
    start_url: '/',
    display: 'standalone',
    background_color: '#faf7f2',
    theme_color: '#0d9488',
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
