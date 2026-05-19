'use client';

import { useRouter } from 'next/navigation';

import { OfficeForm } from '../_components/OfficeForm';
import { Card } from '@/components/ui/card';
import { useCreateOffice } from '@/lib/queries/offices';
import type { OfficeCreate } from '@/lib/schemas/office';

export default function NewOfficePage() {
  const router = useRouter();
  const create = useCreateOffice();

  const handleSubmit = async (values: OfficeCreate) => {
    const office = await create.mutateAsync(values);
    router.push(`/offices/${office.id}`);
  };

  return (
    <section className="space-y-4">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">拠点を新規作成</h1>
        <p className="text-sm text-text-secondary">事業所情報と担当エリアを登録します</p>
      </header>

      <Card className="p-5">
        <OfficeForm onSubmit={handleSubmit} submitting={create.isPending} error={create.error} />
      </Card>
    </section>
  );
}
