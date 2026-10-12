'use client';

/** /schedule/template — 型の作業場（管理者のみ・拠点の feature flag template_workspace が ON の拠点だけ）。 */
import { useSession } from 'next-auth/react';

import { TemplateWorkspace } from '@/components/template-ws/TemplateWorkspace';
import { isAdminRole } from '@/lib/rbac';

export default function TemplateWorkspacePage() {
  const { data: session } = useSession();
  if (!isAdminRole(session?.user?.role ?? 'staff')) {
    return <p className="p-4 text-sm text-text-secondary">型の作業場は管理者だけが使えます。</p>;
  }
  return (
    <section className="flex flex-col gap-3 lg:h-full lg:min-h-0" data-testid="template-ws-page">
      <TemplateWorkspace />
    </section>
  );
}
