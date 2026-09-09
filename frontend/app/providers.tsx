'use client';

import { useState } from 'react';
import { SessionProvider } from 'next-auth/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';

import { getQueryClient } from '@/lib/query-client';
import { Toaster } from '@/components/ui/sonner';
import { SessionErrorGuard } from '@/components/SessionErrorGuard';
import { CloudflareAccessBanner } from '@/components/CloudflareAccessBanner';
import { PatientNotActiveGateProvider } from '@/components/schedule/v2/PatientNotActiveGate';

interface ProvidersProps {
  children: React.ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  // `useState` ensures the QueryClient is stable across re-renders without
  // leaking between users on the server (getQueryClient handles that).
  const [queryClient] = useState(() => getQueryClient());

  return (
    <SessionProvider>
      <SessionErrorGuard>
        <QueryClientProvider client={queryClient}>
          {/* Cloudflare Access 切れの再ログイン導線 — 全 UI (PC/現場ボード/モバイル) 共通 */}
          <CloudflareAccessBanner />
          {/* 非稼働患者の入口ガード (422 patient_not_active → 稼働中にして続ける)。
              PC / 現場ボード / モバイルの 3 UI は本 Providers を共有するのでここに 1 回だけ置く。 */}
          <PatientNotActiveGateProvider>{children}</PatientNotActiveGateProvider>
          {process.env.NODE_ENV === 'development' && (
            <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />
          )}
          <Toaster />
        </QueryClientProvider>
      </SessionErrorGuard>
    </SessionProvider>
  );
}
