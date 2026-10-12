'use client';

import { MobileAgentGuard } from '@/components/agent/MobileAgentGuard';
import { MobileAgentHome } from '@/components/agent/MobileAgent';

export default function MobileAgentPage() {
  return (
    <MobileAgentGuard>
      <MobileAgentHome />
    </MobileAgentGuard>
  );
}
