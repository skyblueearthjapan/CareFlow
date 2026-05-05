'use client';

import { signOut, useSession } from 'next-auth/react';
import { LogOut } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { MobileSection } from '@/components/mobile/MobileSection';
import { useUIStore } from '@/lib/stores/ui';
import { useMyShifts } from '@/lib/queries/me';
import { roleLabel } from '@/lib/schemas/staff';
import { clearAllCheckins } from '@/lib/checkin-storage';
import type { AppRole } from '@/types/auth';

function ProfileRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2">
      <span className="text-xs text-text-muted">{label}</span>
      <span className="text-sm text-text-primary text-right truncate">
        {value}
      </span>
    </div>
  );
}

export default function MobileMyPage() {
  const { data: session } = useSession();
  const user = session?.user;

  const density = useUIStore((s) => s.density);
  const setDensity = useUIStore((s) => s.setDensity);
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);

  // Fetch the staff record so 所属事業所 shows a human name instead of a raw
  // UUID. Backend doesn't yet expose office name, so name is the closest stable
  // label we have until /api/v1/staff/{id} grows an office join.
  const { data: shiftsData } = useMyShifts();
  const staffName = shiftsData?.staff?.name ?? null;

  const role = (user?.role as AppRole | undefined) ?? 'staff';

  return (
    <MobileSection title="マイページ">
      <Card className="p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-primary/15 font-serif text-lg font-bold text-brand-primary">
            {(user?.name ?? '?').slice(0, 1)}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium text-text-primary">
              {user?.name ?? '--'}
            </p>
            <p className="truncate text-xs text-text-muted">
              {user?.email ?? '--'}
            </p>
          </div>
          <Badge variant="secondary">{roleLabel(role)}</Badge>
        </div>
        <div className="mt-3 divide-y divide-border-default">
          <ProfileRow
            label="所属事業所"
            value={staffName ?? (user?.staffId ? '読込中…' : '--')}
          />
          <ProfileRow label="ロール" value={roleLabel(role)} />
        </div>
      </Card>

      <Card className="p-4">
        <h2 className="font-serif text-base font-bold text-text-primary">
          シフト希望提出
        </h2>
        <p className="mt-1 text-xs text-text-muted">
          翌週以降のシフト希望を入力できます。
        </p>
        {/* M5: explicit "coming soon" notice — disabled controls alone read as
            broken UI on mobile. Until /api/v1/staff/me/shift-requests ships,
            staff should keep submitting via Kaipoke as today. */}
        <Alert className="mt-3">
          <AlertTitle>この機能は近日公開予定です</AlertTitle>
          <AlertDescription>
            現状はカイポケ側で提出してください。
          </AlertDescription>
        </Alert>
        <textarea
          disabled
          rows={3}
          placeholder="例: 月・火 終日OK / 水 午後NG"
          className="mt-3 w-full resize-none rounded-md border border-border-default bg-bg-muted p-2 text-sm text-text-primary placeholder:text-text-muted disabled:cursor-not-allowed"
        />
        <Button
          type="button"
          disabled
          className="mt-2 w-full"
          variant="outline"
        >
          保存 (準備中)
        </Button>
      </Card>

      <Card className="p-4 space-y-4">
        <h2 className="font-serif text-base font-bold text-text-primary">
          表示設定
        </h2>
        <div className="flex items-center justify-between">
          <div>
            <Label htmlFor="density-toggle" className="text-sm">
              コンパクト表示
            </Label>
            <p className="text-xs text-text-muted">行間を詰めます</p>
          </div>
          <Switch
            id="density-toggle"
            checked={density === 'compact'}
            onCheckedChange={(v) => setDensity(v ? 'compact' : 'comfortable')}
          />
        </div>
        <div className="flex items-center justify-between">
          <div>
            <Label htmlFor="sidebar-toggle" className="text-sm">
              サイドバーを畳む (PC)
            </Label>
            <p className="text-xs text-text-muted">
              次回のPC表示で適用されます
            </p>
          </div>
          <Switch
            id="sidebar-toggle"
            checked={sidebarCollapsed}
            onCheckedChange={setSidebarCollapsed}
          />
        </div>
      </Card>

      <Button
        type="button"
        variant="outline"
        className="w-full"
        onClick={() => {
          // PHI-adjacent: drop every `checkin:*` key before the auth event
          // so a user-switch on a shared device cannot read prior records.
          // The auth.ts `events.signOut` callback also runs this as a
          // belt-and-braces — duplicate calls are a no-op.
          clearAllCheckins();
          void signOut({ callbackUrl: '/login' });
        }}
      >
        <LogOut className="h-4 w-4" />
        ログアウト
      </Button>
    </MobileSection>
  );
}
