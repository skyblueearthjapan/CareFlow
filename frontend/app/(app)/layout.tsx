import { AppShell } from '@/components/AppShell';
import { AiFab } from '@/components/AiFab';

export default function AppGroupLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell>
      {children}
      <AiFab />
    </AppShell>
  );
}
