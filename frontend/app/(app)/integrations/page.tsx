'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';

import { Card } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import KaipokePage from './kaipoke/page';
import GeocodingPage from './geocoding/page';
import AiLogsPage from './ai/page';

const VALID_TABS = ['kaipoke', 'geocoding', 'ai'] as const;
type TabKey = (typeof VALID_TABS)[number];

export default function IntegrationsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const raw = searchParams?.get('tab') ?? 'kaipoke';
  const tab = (VALID_TABS.includes(raw as TabKey) ? raw : 'kaipoke') as TabKey;

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
          <TabsTrigger value="geocoding">Geocoding キャッシュ</TabsTrigger>
          <TabsTrigger value="ai">AI ログ</TabsTrigger>
        </TabsList>

        <TabsContent value="kaipoke">
          <Card className="p-5">
            <KaipokePage />
          </Card>
        </TabsContent>
        <TabsContent value="geocoding">
          <Card className="p-5">
            <GeocodingPage />
          </Card>
        </TabsContent>
        <TabsContent value="ai">
          <Card className="p-5">
            <AiLogsPage />
          </Card>
        </TabsContent>
      </Tabs>

      <p className="text-xs text-text-muted">
        詳しいジョブ操作は <Link className="text-brand-primary hover:underline" href="/integrations/kaipoke">Kaipoke ジョブ画面</Link> から。
      </p>
    </section>
  );
}
