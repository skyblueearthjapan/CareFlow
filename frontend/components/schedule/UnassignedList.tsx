'use client';

/**
 * Footer section that lists visits the engine couldn't assign. Clicking a
 * row opens the edit dialog so the operator can fix it manually.
 */
import { Card } from '@/components/ui/card';
import { trimSeconds, type VisitRead } from '@/lib/schemas/visit';

interface UnassignedListProps {
  visits: VisitRead[];
  onSelect?: (visit: VisitRead) => void;
}

export function UnassignedList({ visits, onSelect }: UnassignedListProps) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-serif text-base font-semibold text-text-primary">
          未割当
        </h2>
        <span className="text-xs text-text-muted">{visits.length} 件</span>
      </div>
      {visits.length === 0 ? (
        <p className="text-sm text-text-muted">未割当の訪問はありません。</p>
      ) : (
        <ul className="divide-y divide-border-default">
          {visits.map((v) => (
            <li key={v.id}>
              <button
                type="button"
                onClick={() => onSelect?.(v)}
                className="flex w-full items-center justify-between gap-3 px-1 py-2 text-left text-sm hover:bg-bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
              >
                <span className="tnum text-text-secondary">
                  {v.visit_date} {trimSeconds(v.start_time)}–
                  {trimSeconds(v.end_time)}
                </span>
                <span className="flex-1 truncate text-text-primary">
                  {v.patient_name ?? '(未設定)'}
                </span>
                <span className="text-xs text-text-muted">{v.status}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
