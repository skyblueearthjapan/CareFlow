'use client';

import { useState } from 'react';
import { signOut, useSession } from 'next-auth/react';
import { LogOut, Send } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { MobileSection } from '@/components/mobile/MobileSection';
import { useUIStore } from '@/lib/stores/ui';
import { useMyShifts } from '@/lib/queries/me';
import { useCreateShiftRequest, useShiftRequests } from '@/lib/queries/shift-requests';
import { roleLabel } from '@/lib/schemas/staff';
import { clearAllSessionData } from '@/lib/checkin-storage';
import type { AppRole } from '@/types/auth';

function ProfileRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2">
      <span className="text-xs text-text-muted">{label}</span>
      <span className="text-sm text-text-primary text-right truncate">{value}</span>
    </div>
  );
}

/** Default target month = next month (`YYYY-MM-01`) in local TZ. */
function nextMonthFirstIso(): string {
  const d = new Date();
  d.setMonth(d.getMonth() + 1, 1);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${yyyy}-${mm}-01`;
}

/** Build YYYY-MM-DD (first-of-month) options for the next 6 months. */
function monthOptions(): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = [];
  const base = new Date();
  base.setDate(1);
  for (let i = 1; i <= 6; i++) {
    const d = new Date(base.getFullYear(), base.getMonth() + i, 1);
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    out.push({
      value: `${yyyy}-${mm}-01`,
      label: `${yyyy}年${mm}月`,
    });
  }
  return out;
}

export default function MobileMyPage() {
  const { data: session } = useSession();
  const user = session?.user;
  const staffId = user?.staffId ?? null;

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

  // ---- Shift request form state ----
  const [targetMonth, setTargetMonth] = useState<string>(nextMonthFirstIso());
  const [content, setContent] = useState('');
  const createRequest = useCreateShiftRequest(staffId ?? '');
  const { data: history, isLoading: historyLoading } = useShiftRequests(staffId);

  async function handleSubmitShiftRequest() {
    if (!staffId) {
      toast.error('スタッフIDを取得できませんでした');
      return;
    }
    if (!content.trim()) {
      toast.error('内容を入力してください');
      return;
    }
    try {
      await createRequest.mutateAsync({
        target_month: targetMonth,
        content: content.trim(),
      });
      setContent('');
      toast.success('シフト希望を提出しました');
    } catch (err) {
      toast.error('提出に失敗しました', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return (
    <MobileSection title="マイページ">
      <Card className="p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-primary/15 font-serif text-lg font-bold text-brand-primary">
            {(user?.name ?? '?').slice(0, 1)}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium text-text-primary">{user?.name ?? '--'}</p>
            <p className="truncate text-xs text-text-muted">{user?.email ?? '--'}</p>
          </div>
          <Badge variant="secondary">{roleLabel(role)}</Badge>
        </div>
        <div className="mt-3 divide-y divide-border-default">
          <ProfileRow label="所属事業所" value={staffName ?? (user?.staffId ? '読込中…' : '--')} />
          <ProfileRow label="ロール" value={roleLabel(role)} />
        </div>
      </Card>

      <Card className="p-4">
        <h2 className="font-serif text-base font-bold text-text-primary">シフト希望提出</h2>
        <p className="mt-1 text-xs text-text-muted">
          翌週以降のシフト希望を入力してください。提出後は管理者が内容を確認します。
        </p>
        <div className="mt-3 space-y-2">
          <Label htmlFor="shift-month" className="text-xs">
            対象月
          </Label>
          <select
            id="shift-month"
            value={targetMonth}
            onChange={(e) => setTargetMonth(e.target.value)}
            className="w-full rounded-md border border-border-default bg-bg-base p-2 text-sm text-text-primary"
          >
            {monthOptions().map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <div className="mt-3 space-y-2">
          <Label htmlFor="shift-content" className="text-xs">
            希望内容
          </Label>
          <textarea
            id="shift-content"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={4}
            maxLength={2000}
            placeholder="例: 月・火 終日OK / 水 午後NG"
            className="w-full resize-none rounded-md border border-border-default bg-bg-base p-2 text-sm text-text-primary placeholder:text-text-muted"
          />
          <p className="text-right text-[10px] text-text-muted">{content.length} / 2000</p>
        </div>
        <Button
          type="button"
          onClick={handleSubmitShiftRequest}
          disabled={!staffId || !content.trim() || createRequest.isPending}
          className="mt-2 w-full"
        >
          <Send className="mr-1 h-4 w-4" />
          {createRequest.isPending ? '送信中…' : '提出する'}
        </Button>

        <div className="mt-4 border-t border-border-default pt-3">
          <h3 className="text-sm font-medium text-text-primary">提出履歴</h3>
          {historyLoading && (
            <div className="mt-2 space-y-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          )}
          {!historyLoading && history && history.length === 0 && (
            <p className="mt-2 text-xs text-text-muted">まだ提出はありません。</p>
          )}
          {history && history.length > 0 && (
            <ul className="mt-2 space-y-2">
              {history.map((h) => (
                <li key={h.id} className="rounded-md border border-border-default bg-bg-base p-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-text-primary">
                      {h.target_month.slice(0, 7).replace('-', '/')}
                    </span>
                    <Badge
                      variant={h.status === 'reviewed' ? 'success' : 'secondary'}
                      className="text-[10px]"
                    >
                      {h.status === 'reviewed' ? '確認済' : '提出済'}
                    </Badge>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-xs text-text-secondary line-clamp-3">
                    {h.content}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>

        {!staffId && (
          <Alert className="mt-3" variant="destructive">
            <AlertTitle>提出できません</AlertTitle>
            <AlertDescription>
              スタッフIDが紐付いていないアカウントです。管理者にご連絡ください。
            </AlertDescription>
          </Alert>
        )}
      </Card>

      <Card className="p-4 space-y-4">
        <h2 className="font-serif text-base font-bold text-text-primary">表示設定</h2>
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
            <p className="text-xs text-text-muted">次回のPC表示で適用されます</p>
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
          // PHI-adjacent: drop every `checkin:` / `checkin-pending:` /
          // `visit-memo:` key before the auth event so a user-switch on a
          // shared device cannot read prior records. The auth.ts
          // `events.signOut` callback also runs this as a belt-and-braces —
          // duplicate calls are a no-op.
          clearAllSessionData();
          void signOut({ callbackUrl: '/login' });
        }}
      >
        <LogOut className="h-4 w-4" />
        ログアウト
      </Button>
    </MobileSection>
  );
}
