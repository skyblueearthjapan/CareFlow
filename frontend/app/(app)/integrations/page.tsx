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

const ALL_TABS = ['geocoding', 'ai'] as const;
type TabKey = (typeof ALL_TABS)[number];

function IntegrationsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

  const raw = searchParams?.get('tab') ?? 'geocoding';
  const tab = (ALL_TABS.includes(raw as TabKey) ? raw : 'geocoding') as TabKey;

  const setTab = (next: string) => {
    const usp = new URLSearchParams(searchParams?.toString() ?? '');
    usp.set('tab', next);
    router.replace(`/integrations?${usp.toString()}`);
  };

  if (!isAdmin) {
    return (
      <section className="space-y-4">
        <Header />
        <Card className="p-5">
          <p className="text-sm text-text-muted">連携ユーティリティは管理者のみ利用できます。</p>
        </Card>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <Header />

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="geocoding">Geocoding キャッシュ</TabsTrigger>
          <TabsTrigger value="ai">AI ログ</TabsTrigger>
        </TabsList>

        <TabsContent value="geocoding">
          <Card className="p-5">{tab === 'geocoding' && <GeocodingCacheList />}</Card>
        </TabsContent>
        <TabsContent value="ai">
          <Card className="p-5">{tab === 'ai' && <AiLogsList />}</Card>
        </TabsContent>
      </Tabs>
    </section>
  );
}

function Header() {
  return (
    <header className="space-y-1">
      <p className="text-xs text-text-muted">
        <Link className="text-brand-primary hover:underline" href="/integrations/kaipoke">
          ← カイポケ ジョブセンター
        </Link>
      </p>
      <h1 className="font-serif text-2xl font-bold text-text-primary">連携ユーティリティ</h1>
      <p className="text-sm text-text-secondary">
        ジオコーディングキャッシュ・AI 解釈ログ（管理用）
      </p>
    </header>
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
