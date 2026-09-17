'use client';

/**
 * 訪問記録カード（モバイル・設計 §2-3／モック ③）。
 *
 * **要約が主役**。状態バッジ → 要約（見出し付き箇条書き・バイタルはグリッド）→
 * 「確認済みにする」→ 折りたたみで音声・全文、の順に置く。
 * 文字起こし中はスケルトンとらく助の一言、失敗は理由を出す。
 */

import { useState } from 'react';
import { useSession } from 'next-auth/react';
import { AlertTriangle, Check, ChevronDown, FileText, Volume2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { AuthedAudio } from '@/components/mobile/AuthedAudio';
import { RakusukeWorking } from '@/components/brand/Rakusuke';
import { summaryDisplayMode } from '@/components/records/recordFormat';
import { formatElapsed } from '@/lib/voice/recorder';
import { useUpdateRecording, type VisitRecordingRead } from '@/lib/queries/visit-recordings';

/** 状態 → バッジの見た目と文言（未知の状態は「受付済み」に落とす）。 */
function statusMeta(status: string): {
  label: string;
  variant: 'default' | 'secondary' | 'success' | 'warning' | 'info' | 'destructive';
} {
  switch (status) {
    case 'transcribing':
      return { label: '文字起こし中', variant: 'info' };
    case 'summarized':
      return { label: '要約済み', variant: 'success' };
    case 'failed':
      return { label: '失敗', variant: 'destructive' };
    case 'unlinked':
      return { label: '要紐付け', variant: 'warning' };
    default:
      return { label: '受付済み', variant: 'secondary' };
  }
}

/** ISO 日時 → `M/D HH:MM`（端末ローカル）。 */
function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`;
}

/** 要約 JSON の見出し（`free` だけ日本語に読み替える）。 */
function sectionLabel(key: string): string {
  return key === 'free' ? 'その他' : key;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

/** 要約 1 セクション。配列=箇条書き / 連想=グリッド（バイタル）/ 文字列=本文。 */
function SummarySection({ label, value }: { label: string; value: unknown }) {
  if (Array.isArray(value)) {
    const items = value.map((v) => String(v)).filter((v) => v.trim() !== '');
    if (items.length === 0) return null;
    return (
      <section>
        <h4 className="text-xs font-bold tracking-wide text-text-muted">{label}</h4>
        <ul className="mt-1 space-y-0.5">
          {items.map((item, i) => (
            <li key={i} className="flex gap-1.5 text-sm text-text-primary">
              <span aria-hidden>・</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  if (isPlainObject(value)) {
    const pairs = Object.entries(value).filter(([, v]) => v !== null && String(v).trim() !== '');
    if (pairs.length === 0) return null;
    return (
      <section>
        <h4 className="text-xs font-bold tracking-wide text-text-muted">{label}</h4>
        <dl className="mt-1 grid grid-cols-2 gap-1.5">
          {pairs.map(([k, v]) => (
            <div key={k} className="rounded-md bg-bg-muted px-2 py-1.5">
              <dt className="text-xs text-text-muted">{k}</dt>
              <dd className="text-sm font-semibold tnum text-text-primary">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </section>
    );
  }
  const text = value == null ? '' : String(value).trim();
  if (text === '') return null;
  return (
    <section>
      <h4 className="text-xs font-bold tracking-wide text-text-muted">{label}</h4>
      <p className="mt-1 whitespace-pre-wrap text-sm text-text-primary">{text}</p>
    </section>
  );
}

interface VisitRecordCardProps {
  recording: VisitRecordingRead;
}

export function VisitRecordCard({ recording }: VisitRecordCardProps) {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const [showAudio, setShowAudio] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  const update = useUpdateRecording(recording.id);

  const meta = statusMeta(recording.status);
  const summaryEntries = recording.summary ? Object.entries(recording.summary) : [];
  const summaryMode = summaryDisplayMode(recording);
  const reviewed = !!recording.reviewed_at;

  async function markReviewed() {
    try {
      await update.mutateAsync({ reviewed: true });
      toast.success('確認済みにしました');
    } catch (err) {
      toast.error('確認済みにできませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return (
    <Card className="space-y-3 p-4" data-testid={`visit-record-${recording.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={meta.variant}>{meta.label}</Badge>
        <span className="text-sm text-text-secondary">
          {recording.patient_name ?? '(患者未紐付け)'}
        </span>
        <span className="text-sm tnum text-text-muted">{fmtDateTime(recording.recorded_at)}</span>
        {typeof recording.duration_sec === 'number' && recording.duration_sec > 0 && (
          <span className="text-sm tnum text-text-muted">
            {formatElapsed(recording.duration_sec)}
          </span>
        )}
      </div>

      {recording.status === 'failed' && (
        <div className="flex items-start gap-2 rounded-md bg-error-bg px-3 py-2 text-sm text-error">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{recording.error_message ?? '文字起こしに失敗しました'}</span>
        </div>
      )}

      {(recording.status === 'transcribing' || recording.status === 'uploaded') && (
        <div className="space-y-2" data-testid="visit-record-working">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-4 w-2/3" />
          <RakusukeWorking
            pose="think"
            message="らく助が文字起こし中です"
            sub="数分でお知らせします"
          />
        </div>
      )}

      {/* 描き方の規則は PC の詳細ダイアログと 1 本化（`summaryDisplayMode`）。
          手修正済み（`summary_edited_at`）なら本文、未編集なら JSON の構造表示。
          片方だけ規則を変えると「PC では直した要約・モバイルでは古い要約」になる。 */}
      {summaryMode === 'text' ? (
        <div className="space-y-1">
          <p className="whitespace-pre-wrap text-sm text-text-primary">{recording.summary_text}</p>
          {recording.summary_edited_at && (
            <p className="text-xs text-text-muted" data-testid="visit-record-summary-edited">
              手修正あり（{fmtDateTime(recording.summary_edited_at)}）
            </p>
          )}
        </div>
      ) : summaryMode === 'json' ? (
        <div className="space-y-3">
          {summaryEntries.map(([key, value]) => (
            <SummarySection key={key} label={sectionLabel(key)} value={value} />
          ))}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setShowAudio((v) => !v)}
          disabled={recording.has_audio === false}
        >
          <Volume2 className="h-4 w-4" />
          音声を聞く
          <ChevronDown className="h-4 w-4" />
        </Button>
        {recording.transcript && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowTranscript((v) => !v)}
          >
            <FileText className="h-4 w-4" />
            全文を見る
            <ChevronDown className="h-4 w-4" />
          </Button>
        )}
      </div>

      {showAudio && <AuthedAudio recordingId={recording.id} accessToken={accessToken} />}

      {showTranscript && recording.transcript && (
        <p
          className="whitespace-pre-wrap rounded-md bg-bg-muted p-3 text-sm text-text-primary"
          data-testid="visit-record-transcript"
        >
          {recording.transcript}
        </p>
      )}

      {reviewed ? (
        <p className="flex items-center gap-1.5 text-sm text-success">
          <Check className="h-4 w-4" />
          確認済み（{fmtDateTime(recording.reviewed_at)}）
        </p>
      ) : (
        // 「確認済み」= 要約の内容に責任を持つ署名（レビュー L-1）。文字起こし中 /
        // 失敗 / 要紐付けでは確認する中身がまだ無いので、押させない。
        recording.status === 'summarized' && (
          <Button
            type="button"
            className="w-full"
            disabled={update.isPending}
            onClick={() => void markReviewed()}
          >
            <Check className="h-4 w-4" />
            確認済みにする
          </Button>
        )
      )}
    </Card>
  );
}
