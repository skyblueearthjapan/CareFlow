'use client';

/**
 * 「🎙 記録を見る」— 訪問モニターの詳細パネルから音声記録へ渡す 1 本（設計 §11-2）。
 *
 * モニターは当日限りの画面なので記録の置き場は `/records` だが、`/records` には
 * 訪問 1 件を名指しする URL が無い。ここでは `useVisitRecordings({visitId})` で
 * その訪問の記録を引き、**あるときだけ**ボタンを出して同じ詳細ダイアログを開く
 * （画面を移さずに要約を読める）。記録が無ければ何も描かない。
 */

import { useState } from 'react';
import { Mic } from 'lucide-react';

import { RecordDetailDialog } from '@/components/records/RecordDetailDialog';
import { useVisitRecordings } from '@/lib/queries/visit-recordings';

export function VisitRecordingLink({ visitId }: { visitId: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const query = useVisitRecordings({ visitId, order: 'recorded_at_desc', limit: 5 });
  const items = query.data?.items ?? [];

  if (items.length === 0) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setSelectedId(items[0]?.id ?? null)}
        className="inline-flex items-center gap-1.5 text-sm text-brand-primary underline hover:text-brand-primary-hover"
        data-testid="monitor-recording-link"
      >
        <Mic className="h-4 w-4" strokeWidth={1.75} />
        記録を見る{items.length > 1 ? `（${items.length}件）` : ''}
      </button>
      <RecordDetailDialog
        recordingId={selectedId}
        open={selectedId !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
        onDeleted={() => setSelectedId(null)}
      />
    </>
  );
}
