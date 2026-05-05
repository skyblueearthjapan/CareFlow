import { MobileShell } from '@/components/MobileShell';
import { MobileAiFab } from '@/app/(mobile)/m/_components/MobileAiFab';

export default function MobileLayout({ children }: { children: React.ReactNode }) {
  return (
    <MobileShell>
      {children}
      <MobileAiFab />
    </MobileShell>
  );
}
