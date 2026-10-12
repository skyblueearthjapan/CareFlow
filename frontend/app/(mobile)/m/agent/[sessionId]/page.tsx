'use client';

import { useParams } from 'next/navigation';

import { MobileAgentGuard } from '@/components/agent/MobileAgentGuard';
import { MobileAgentConversation } from '@/components/agent/MobileAgent';

export default function MobileAgentSessionPage() {
  const params = useParams<{ sessionId: string }>();
  return (
    <MobileAgentGuard>
      <MobileAgentConversation sessionId={params.sessionId} />
    </MobileAgentGuard>
  );
}
