'use client';

/**
 * 「訪問記録」カード（患者詳細・スタッフ詳細の両方が使う 1 本・設計 §11-2）。
 *
 * 直近 5 件だけ出し、続きは `/records?patient=` / `?staff=` へ送る。意匠は
 * `EventsCard`（Card + CardHeader + CardTitle）にそろえる。行クリックで
 * `/records` と同じ詳細ダイアログを開く（画面を移さずに要約を読めるように）。
 */

import { useState } from 'react';
import Link from 'next/link';
import { Mic } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { RecordDetailDialog } from '@/components/records/RecordDetailDialog';
import {
  formatRecordDate,
  formatRecordTime,
  recordStatusMeta,
  summaryFirstLine,
} from '@/components/records/recordFormat';
import { useVisitRecordings } from '@/lib/queries/visit-recordings';

/** カードに出す件数（設計 §11-2「直近 5 件」）。 */
const PREVIEW_LIMIT = 5;

export interface VisitRecordsCardProps {
  /** 患者詳細から使うとき。 */
  patientId?: string;
  /** スタッフ詳細から使うとき。 */
  staffId?: string;
}

export function VisitRecordsCard({ patientId, staffId }: VisitRecordsCardProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const query = useVisitRecordings({
    patientId: patientId ?? null,
    staffId: staffId ?? null,
    order: 'recorded_at_desc',
    limit: PREVIEW_LIMIT,
  });
  const items = query.data?.items ?? [];
  const total = query.data?.total ?? items.length;

  const allHref = patientId ? `/records?patient=${patientId}` : `/records?staff=${staffId ?? ''}`;

  return (
    <Card data-testid="visit-records-card">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Mic className="h-4 w-4 text-brand-primary" strokeWidth={1.75} />
          訪問記録
        </CardTitle>
        <Link
          href={allHref}
          className="text-sm text-brand-primary underline hover:text-brand-primary-hover"
          data-testid="visit-records-see-all"
        >
          すべて見る{total > 0 ? `（${total}件）` : ''}
        </Link>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : items.length === 0 ? (
          <RakusukeNote
            pose="think"
            title="まだ訪問記録はありません"
            comment="モバイルの録音ボタンから記録を作成できます。"
            size="sm"
          />
        ) : (
          <ul className="divide-y divide-border-default">
            {items.map((rec) => {
              const meta = recordStatusMeta(rec.status);
              return (
                <li key={rec.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(rec.id)}
                    className="flex w-full items-center gap-3 px-1 py-2 text-left hover:bg-bg-muted"
                    data-testid={`visit-records-row-${rec.id}`}
                  >
                    <span className="shrink-0 tnum text-sm text-text-secondary">
                      {formatRecordDate(rec.recorded_at)} {formatRecordTime(rec.recorded_at)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-text-primary">
                      {patientId ? summaryFirstLine(rec) : (rec.patient_name ?? '（未紐付け）')}
                    </span>
                    <Badge variant={meta.variant} className="shrink-0">
                      {meta.label}
                    </Badge>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>

      <RecordDetailDialog
        recordingId={selectedId}
        open={selectedId !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
        onDeleted={() => setSelectedId(null)}
      />
    </Card>
  );
}
