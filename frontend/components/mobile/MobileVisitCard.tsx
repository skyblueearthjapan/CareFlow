import Link from 'next/link';
import { ChevronRight, MapPin } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import type { MyVisit } from '@/lib/queries/me';

/** Map backend `status` → Japanese label + Badge variant. */
function statusMeta(status: string): {
  label: string;
  variant: 'default' | 'secondary' | 'success' | 'warning' | 'info';
} {
  switch (status) {
    case 'in_progress':
    case 'checked_in':
      return { label: '訪問中', variant: 'info' };
    case 'done':
    case 'completed':
    case 'checked_out':
      return { label: '完了', variant: 'success' };
    case 'cancelled':
      return { label: '取消', variant: 'secondary' };
    case 'planned':
    default:
      return { label: '未訪問', variant: 'warning' };
  }
}

/** Display "HH:MM" from a backend "HH:MM:SS" time string. */
function shortTime(t: string): string {
  return t.length >= 5 ? t.slice(0, 5) : t;
}

interface MobileVisitCardProps {
  visit: MyVisit;
  /** Optional address line (Patient.address — fetched separately if needed). */
  address?: string | null;
  /** When true, render with extra emphasis (used on /m/today for unvisited). */
  highlight?: boolean;
}

export function MobileVisitCard({ visit, address, highlight }: MobileVisitCardProps) {
  const meta = statusMeta(visit.status);
  return (
    <Link
      href={`/m/today/${visit.id}`}
      className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary rounded-lg"
      aria-label={`${visit.patient_name ?? '患者'} の訪問詳細`}
    >
      <Card
        className={cn(
          'flex items-center gap-3 p-4 transition-colors hover:bg-bg-muted',
          highlight && 'border-l-4 border-l-warning',
        )}
      >
        <div className="flex flex-col items-center justify-center w-14 shrink-0">
          <span className="font-serif text-lg font-bold tnum text-text-primary">
            {shortTime(visit.start_time)}
          </span>
          <span className="text-[11px] text-text-muted tnum">{shortTime(visit.end_time)}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate font-medium text-text-primary">
              {visit.patient_name ?? '(患者名未設定)'}
            </p>
            <Badge variant={meta.variant}>{meta.label}</Badge>
          </div>
          {address && (
            <p className="mt-1 flex items-center gap-1 text-xs text-text-muted truncate">
              <MapPin className="h-3 w-3 shrink-0" />
              <span className="truncate">{address}</span>
            </p>
          )}
          {visit.note && <p className="mt-1 text-xs text-text-secondary truncate">{visit.note}</p>}
        </div>
        <ChevronRight className="h-4 w-4 shrink-0 text-text-muted" />
      </Card>
    </Link>
  );
}
