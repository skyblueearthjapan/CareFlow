import Link from 'next/link';
import { ChevronRight, MapPin } from 'lucide-react';
import { useSession } from 'next-auth/react';

import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { displayVisitNote } from '@/lib/visit-note';
import { genderPalette } from '@/lib/scheduling/timeline';
import { visitAccompaniments } from '@/lib/schemas/trainee_accompaniment';
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

export function MobileVisitCard({ visit, address }: MobileVisitCardProps) {
  const meta = statusMeta(visit.status);
  // 内部メタデータ (Layer1: 等) は現場に見せない。
  const note = displayVisitNote(visit.note);
  // R-9 (PO要望 2026-07-10): PC版スケジュール/モニターと同じカード視覚言語へ統一。
  // 地色=患者性別ウォッシュ・左帯/性別ドット=性別色・時刻 tnum・📍住所。
  const pal = genderPalette(visit.patient_sex ?? null);
  const shownAddress = address ?? visit.patient_address ?? null;
  // 同行 (§7.4): 担当側は「同行: ◯◯・◯◯」、同行者本人は「同行」バッジ。
  // 複数名対応 (確定#5): accompaniments[] 優先・旧単数 accompaniment はフォールバック。
  const { data: session } = useSession();
  const myStaffId = session?.user?.staffId ?? null;
  const accompaniments = visitAccompaniments(visit);
  const isTrainee = myStaffId != null && accompaniments.some((a) => a.staff_id === myStaffId);
  const accompanimentNames = accompaniments
    .filter((a) => a.staff_id !== myStaffId)
    .map((a) => a.staff_name ?? '同行スタッフ');
  return (
    <Link
      href={`/m/today/${visit.id}`}
      className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary rounded-lg"
      aria-label={`${visit.patient_name ?? '患者'} の訪問詳細`}
    >
      <Card
        className={cn(
          'flex items-center gap-3 rounded-md border border-l-4 p-4 shadow-[var(--shadow-xs)] transition-shadow hover:shadow-[var(--shadow-sm)]',
          visit.status === 'cancelled' && 'opacity-60',
        )}
        style={{
          // 左帯は常に性別色 (PC版踏襲・PO要望 2026-07-10)。未訪問の区別はバッジが担う。
          background: pal.bg,
          borderColor: pal.ln,
          borderLeftColor: pal.bar,
          color: pal.ink,
        }}
      >
        <div className="flex w-14 shrink-0 flex-col items-center justify-center">
          <span className="font-serif text-lg font-bold tnum">{shortTime(visit.start_time)}</span>
          <span className="text-[11px] opacity-70 tnum">{shortTime(visit.end_time)}</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <i
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ background: pal.bar }}
              aria-hidden="true"
            />
            <p className="truncate font-bold">{visit.patient_name ?? '(患者名未設定)'}</p>
            <Badge variant={meta.variant}>{meta.label}</Badge>
            {isTrainee && (
              <Badge variant="info" data-testid="mobile-visit-accompaniment">
                同行
              </Badge>
            )}
          </div>
          {accompanimentNames.length > 0 && (
            <p
              className="mt-1 truncate text-xs font-medium text-info"
              data-testid="mobile-visit-accompaniment"
            >
              同行: {accompanimentNames.join('・')}
            </p>
          )}
          {shownAddress && (
            <p className="mt-1 flex items-center gap-1 truncate text-xs opacity-80">
              <MapPin className="h-3 w-3 shrink-0" />
              <span className="truncate">{shownAddress}</span>
            </p>
          )}
          {note && <p className="mt-1 truncate text-xs opacity-80">{note}</p>}
        </div>
        <ChevronRight className="h-4 w-4 shrink-0 opacity-60" />
      </Card>
    </Link>
  );
}
