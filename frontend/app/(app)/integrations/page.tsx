'use client';

import { Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { AiLogsList } from './_components/AiLogsList';
import { GeocodingCacheList } from './_components/GeocodingCacheList';
import { KaipokeJobsList } from './_components/KaipokeJobsList';

const ALL_TABS = ['kaipoke', 'geocoding', 'ai'] as const;
type TabKey = (typeof ALL_TABS)[number];

function IntegrationsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';
  const validTabs: readonly TabKey[] = isAdmin ? ALL_TABS : ['kaipoke'];

  const raw = searchParams?.get('tab') ?? 'kaipoke';
  const tab = (validTabs.includes(raw as TabKey) ? raw : 'kaipoke') as TabKey;

  const setTab = (next: string) => {
    const usp = new URLSearchParams(searchParams?.toString() ?? '');
    usp.set('tab', next);
    router.replace(`/integrations?${usp.toString()}`);
  };

  return (
    <section className="space-y-4">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">連携センター</h1>
        <p className="text-sm text-text-secondary">
          Kaipoke 取り込み/反映ジョブ・ジオコーディングキャッシュ・AI 解釈ログ
        </p>
      </header>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="kaipoke">Kaipoke ジョブ</TabsTrigger>
          {isAdmin && <TabsTrigger value="geocoding">Geocoding キャッシュ</TabsTrigger>}
          {isAdmin && <TabsTrigger value="ai">AI ログ</TabsTrigger>}
        </TabsList>

        <TabsContent value="kaipoke">
          <Card className="p-5">
            {tab === 'kaipoke' && <KaipokeJobsList />}
          </Card>
        </TabsContent>
        {isAdmin && (
          <TabsContent value="geocoding">
            <Card className="p-5">
              {tab === 'geocoding' && <GeocodingCacheList />}
            </Card>
          </TabsContent>
        )}
        {isAdmin && (
          <TabsContent value="ai">
            <Card className="p-5">
              {tab === 'ai' && <AiLogsList />}
            </Card>
          </TabsContent>
        )}
      </Tabs>

      <p className="text-xs text-text-muted">
        詳しいジョブ操作は <Link className="text-brand-primary hover:underline" href="/integrations/kaipoke">Kaipoke ジョブ画面</Link> から。
      </p>
    </section>
  );
}

export default function IntegrationsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-3">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-32 w-full" />
        </div>
      }
    >
      <IntegrationsPageInner />
    </Suspense>
  );
}
