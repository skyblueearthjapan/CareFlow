'use client';

/** スケジュール画面の道具のところに置く「コースの作業場」へのリンク。管理者で、使える拠点があるときだけ出る。 */
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { useTemplateAvailability } from '@/lib/queries/templateWs';

export function TemplateWorkspaceLink({ canEdit }: { canEdit: boolean }) {
  const avail = useTemplateAvailability();
  if (!canEdit || !avail.data || avail.data.length === 0) return null;
  return (
    <Button asChild type="button" variant="outline" size="sm">
      <Link href="/schedule/template" data-testid="template-workspace-link">
        コースの作業場
      </Link>
    </Button>
  );
}
