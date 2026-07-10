'use client';

import { Suspense } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

import { GeocodingCacheList } from './_components/GeocodingCacheList';

function IntegrationsPageInner() {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

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
      <Card className="p-5">
        <GeocodingCacheList />
      </Card>
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
      <RakusukeTitle
        pose="idea"
        title="連携ユーティリティ"
        subtitle="ジオコーディングキャッシュ（管理用）"
      />
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
