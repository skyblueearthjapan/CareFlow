'use client';

/**
 * 訪問記録の詳細ダイアログ（設計 §11-2 / モック ⑥）。
 *
 * `max-w-5xl max-h-[90vh]`・2 列・本文 14px（add-visit-anywhere-design.md §3-5 の
 * ダイアログ基準。11px 以下は使わない）。
 *
 *   左 = 要約（`summary` JSON を見出し付きで整形・admin / 本人は本文を直せる）＋ メタ
 *   右 = 音声プレーヤー ＋ 文字起こし全文（話者ラベル色分け・タイムスタンプ）
 *   フッタ = 紐付けを変更 / 再処理（admin）/ 削除（admin）… 右に「確認済み」
 *
 * RBAC は PO 決定どおり「全ロール同一表示・権限外は disabled」。隠さずに理由を
 * `title` で出す（何ができないのかが分からない画面にしない）。
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useSession } from 'next-auth/react';
import { AlertTriangle, Check, Link2, Pencil, RotateCcw, Trash2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/sonner';
import { PatientCombobox } from '@/components/master/PatientCombobox';
import { AuthedAudioPlayer } from '@/components/records/AuthedAudioPlayer';
import {
  formatDurationSec,
  formatOffset,
  formatRecordDateTime,
  isPlainObject,
  parseTranscriptSegments,
  recordStatusMeta,
  speakerTone,
  summaryDisplayMode,
  summarySectionLabel,
  summaryToText,
} from '@/components/records/recordFormat';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { isAdminRole } from '@/lib/rbac';
import {
  useDeleteRecording,
  useRetryRecording,
  useUpdateRecording,
  useVisitRecording,
  type VisitRecordingRead,
} from '@/lib/queries/visit-recordings';

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
        <dl className="mt-1 grid grid-cols-3 gap-1.5">
          {pairs.map(([k, v]) => (
            <div key={k} className="rounded-md bg-bg-muted px-2 py-1.5">
              <dt className="text-xs text-text-muted">{k}</dt>
              <dd className="tnum text-sm font-semibold text-text-primary">{String(v)}</dd>
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

function MetaRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <dt className="w-24 shrink-0 text-text-muted">{label}</dt>
      <dd className="min-w-0 flex-1 text-text-primary">{value}</dd>
    </div>
  );
}

/** 費用（BE は Decimal を文字列で返すことがある）。 */
function formatCost(cost: number | string | null | undefined): string {
  if (cost == null) return '--';
  const n = typeof cost === 'number' ? cost : Number(cost);
  if (!Number.isFinite(n)) return String(cost);
  return `$${n.toFixed(4)}`;
}

/** 音声ファイルの補足（`AAC · 8.2 MB`）。 */
function audioCaption(rec: VisitRecordingRead): string | null {
  const parts: string[] = [];
  if (rec.audio_mime) parts.push(rec.audio_mime.replace(/^audio\//, '').toUpperCase());
  if (typeof rec.audio_bytes === 'number' && rec.audio_bytes > 0) {
    parts.push(`${(rec.audio_bytes / (1024 * 1024)).toFixed(1)} MB`);
  }
  return parts.length > 0 ? parts.join(' · ') : null;
}

export interface RecordDetailDialogProps {
  /** 表示する記録。null なら閉じた状態。 */
  recordingId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 削除が完了したとき（呼び出し側が選択を解除する）。 */
  onDeleted?: () => void;
}

export function RecordDetailDialog({
  recordingId,
  open,
  onOpenChange,
  onDeleted,
}: RecordDetailDialogProps) {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const isAdmin = isAdminRole(session?.user?.role);
  const myStaffId = session?.user?.staffId ?? null;

  const detail = useVisitRecording(open ? recordingId : null);
  const rec = detail.data ?? null;

  const update = useUpdateRecording(recordingId ?? '');
  const retry = useRetryRecording(recordingId ?? '');
  const del = useDeleteRecording(recordingId ?? '');

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [relinking, setRelinking] = useState(false);
  const [nextPatientId, setNextPatientId] = useState('');
  const [confirming, setConfirming] = useState<null | 'retry' | 'delete'>(null);

  // 記録が変わったら編集状態を畳む（別の記録の下書きが残らないように）。
  useEffect(() => {
    setEditing(false);
    setRelinking(false);
    setConfirming(null);
    setNextPatientId('');
  }, [recordingId]);

  const isOwner = !!rec?.staff_id && !!myStaffId && rec.staff_id === myStaffId;
  const canEditSummary = isAdmin || isOwner;
  const reviewed = !!rec?.reviewed_at;

  const summaryEntries = useMemo(
    () => (rec?.summary ? Object.entries(rec.summary) : []),
    [rec?.summary],
  );
  const segments = useMemo(
    () => (rec ? parseTranscriptSegments(rec.transcript_json) : null),
    [rec],
  );
  const summaryMode = rec ? summaryDisplayMode(rec) : 'empty';

  async function saveSummary() {
    if (!rec) return;
    try {
      const wasReviewed = reviewed;
      await update.mutateAsync({ summary_text: draft });
      toast.success(
        wasReviewed ? '要約を保存しました（「確認済み」は外れました）' : '要約を保存しました',
        wasReviewed
          ? { description: '内容を見直したら、もう一度確認済みにしてください。' }
          : undefined,
      );
      setEditing(false);
    } catch (err) {
      toast.error('要約を保存できませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function toggleReviewed() {
    if (!rec) return;
    try {
      await update.mutateAsync({ reviewed: !reviewed });
      toast.success(reviewed ? '確認を取り消しました' : '確認済みにしました');
    } catch (err) {
      toast.error('確認済みにできませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function saveRelink() {
    if (!nextPatientId) return;
    try {
      await update.mutateAsync({ patient_id: nextPatientId });
      toast.success('紐付けを変更しました');
      setRelinking(false);
      setNextPatientId('');
    } catch (err) {
      toast.error('紐付けを変更できませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function runRetry() {
    try {
      await retry.mutateAsync();
      toast.success('再処理を開始しました');
      setConfirming(null);
    } catch (err) {
      toast.error('再処理を開始できませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function runDelete() {
    try {
      await del.mutateAsync();
      toast.success('記録を削除しました');
      setConfirming(null);
      onOpenChange(false);
      onDeleted?.();
    } catch (err) {
      toast.error('記録を削除できませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  const status = rec ? recordStatusMeta(rec.status) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex max-h-[90vh] w-[min(1024px,95vw)] max-w-5xl flex-col gap-3 overflow-hidden p-0 text-sm"
        aria-describedby={undefined}
        data-testid="record-detail-dialog"
      >
        {/* ヘッダ */}
        <div className="flex items-start justify-between gap-3 border-b border-border-default px-5 py-3 pr-12">
          <div className="min-w-0">
            <DialogTitle className="truncate">
              {rec?.patient_name ? `${rec.patient_name} 様の訪問記録` : '訪問記録'}
            </DialogTitle>
            <p className="mt-1 text-[13px] text-text-secondary">
              {rec ? formatRecordDateTime(rec.recorded_at) : '読み込み中…'}
              {rec && formatDurationSec(rec.duration_sec) !== ''
                ? `（${formatDurationSec(rec.duration_sec)}）`
                : ''}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {status && <Badge variant={status.variant}>{status.label}</Badge>}
            {reviewed && (
              <Badge variant="success" className="gap-1">
                <Check className="h-3 w-3" />
                確認済み
              </Badge>
            )}
          </div>
        </div>

        {/* 本体: 2 列 */}
        <div className="grid min-h-0 flex-1 gap-5 overflow-y-auto px-5 pb-1 lg:grid-cols-2">
          {/* 左: 要約 + メタ */}
          <div className="min-w-0 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-serif text-base font-bold text-text-primary">要約</h3>
              {!editing && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!rec || !canEditSummary}
                  title={
                    canEditSummary ? undefined : '要約を直せるのは管理者と録音した本人だけです'
                  }
                  data-testid="record-summary-edit"
                  onClick={() => {
                    if (!rec) return;
                    setDraft(summaryToText(rec));
                    setEditing(true);
                  }}
                >
                  <Pencil className="h-4 w-4" />
                  編集
                </Button>
              )}
            </div>

            {detail.isLoading && (
              <div className="space-y-2">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-4/5" />
                <Skeleton className="h-4 w-2/3" />
              </div>
            )}

            {/* 読めなかったときは黙って空にしない — BE の detail をそのまま見せる。 */}
            {detail.isError && (
              <Alert variant="destructive" data-testid="record-detail-error">
                <AlertTitle>記録を読み込めませんでした</AlertTitle>
                <AlertDescription className="whitespace-pre-wrap">
                  {apiErrorMessage(detail.error)}
                </AlertDescription>
              </Alert>
            )}

            {rec?.status === 'failed' && (
              <div className="flex items-start gap-2 rounded-md bg-error-bg px-3 py-2 text-sm text-error">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{rec.error_message ?? '文字起こしに失敗しました'}</span>
              </div>
            )}

            {editing ? (
              <div className="space-y-2" data-testid="record-summary-editor">
                <Textarea
                  aria-label="要約"
                  className="min-h-[220px] text-sm"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
                {/* 要約に手を入れたら「確認済み」の署名は無効になる（BE が外す）。
                    後から気づくのでは遅いので、保存する前に言う（レビュー M-3）。 */}
                <p className="text-xs text-warning-strong" data-testid="record-summary-edit-note">
                  保存すると「確認済み」が外れます。直したあとにもう一度確認してください。
                </p>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    disabled={update.isPending}
                    onClick={() => void saveSummary()}
                  >
                    保存
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setEditing(false)}
                  >
                    やめる
                  </Button>
                </div>
              </div>
            ) : (
              rec && (
                <div className="space-y-3" data-testid="record-summary-view">
                  {/* 描き方の規則は `summaryDisplayMode` に 1 本化（モバイルの
                      `VisitRecordCard` と同じ）。手修正済み = 本文、未編集 = JSON 構造。 */}
                  {summaryMode === 'text' ? (
                    <p
                      className="whitespace-pre-wrap text-sm text-text-primary"
                      data-testid="record-summary-text"
                    >
                      {rec.summary_text}
                    </p>
                  ) : summaryMode === 'json' ? (
                    summaryEntries.map(([key, value]) => (
                      <SummarySection key={key} label={summarySectionLabel(key)} value={value} />
                    ))
                  ) : (
                    <p className="text-sm text-text-muted">まだ要約はありません。</p>
                  )}
                  {/* 誰が直したかは UUID でしか来ないので出さない（レビュー L-1）。 */}
                  {rec.summary_edited_at && (
                    <p className="text-xs text-text-muted" data-testid="record-summary-edited">
                      手修正あり（{formatRecordDateTime(rec.summary_edited_at)}）
                    </p>
                  )}
                </div>
              )
            )}

            {rec && (
              <div className="space-y-2 rounded-lg border border-border-default p-3">
                <h3 className="font-serif text-base font-bold text-text-primary">記録の情報</h3>
                <dl className="space-y-1">
                  <MetaRow label="患者" value={rec.patient_name ?? '（未紐付け）'} />
                  <MetaRow label="スタッフ" value={rec.staff_name ?? '--'} />
                  <MetaRow label="拠点" value={rec.office_name ?? '--'} />
                  <MetaRow label="録音日時" value={formatRecordDateTime(rec.recorded_at)} />
                  <MetaRow label="長さ" value={formatDurationSec(rec.duration_sec) || '--'} />
                  <MetaRow label="状態" value={status?.label ?? rec.status} />
                  <MetaRow label="費用" value={formatCost(rec.cost_usd)} />
                  <MetaRow
                    label="モデル"
                    value={[rec.provider, rec.model, rec.prompt_version]
                      .filter((v) => !!v)
                      .join(' / ')}
                  />
                </dl>
                {rec.visit_id && (
                  <a
                    href="/schedule"
                    className="inline-flex items-center gap-1.5 text-sm text-brand-primary underline hover:text-brand-primary-hover"
                    data-testid="record-visit-link"
                  >
                    <Link2 className="h-4 w-4" />
                    スケジュールで訪問を見る
                  </a>
                )}
              </div>
            )}
          </div>

          {/* 右: 音声 + 文字起こし */}
          <div className="flex min-w-0 flex-col gap-3">
            <div>
              <h3 className="mb-2 font-serif text-base font-bold text-text-primary">音声</h3>
              {rec && rec.has_audio !== false ? (
                <AuthedAudioPlayer
                  recordingId={rec.id}
                  accessToken={accessToken}
                  caption={audioCaption(rec)}
                  durationSec={rec.duration_sec ?? null}
                />
              ) : (
                <p className="text-sm text-text-muted">音声はありません。</p>
              )}
            </div>

            <div className="flex min-h-0 flex-1 flex-col">
              <h3 className="mb-2 font-serif text-base font-bold text-text-primary">
                文字起こし全文
              </h3>
              {segments ? (
                <div
                  className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1"
                  data-testid="record-transcript-segments"
                >
                  {segments.map((seg, i) => (
                    <div key={i} className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        <span
                          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${speakerTone(
                            seg.speaker,
                          )}`}
                        >
                          {seg.speaker ?? '不明'}
                        </span>
                        {formatOffset(seg.offsetSec) && (
                          <span className="tnum text-xs text-text-muted">
                            {formatOffset(seg.offsetSec)}
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-text-primary">{seg.text}</p>
                    </div>
                  ))}
                </div>
              ) : rec?.transcript ? (
                <p
                  className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap rounded-md bg-bg-muted p-3 text-sm text-text-primary"
                  data-testid="record-transcript-plain"
                >
                  {rec.transcript}
                </p>
              ) : (
                <p className="text-sm text-text-muted">まだ文字起こしはありません。</p>
              )}
            </div>
          </div>
        </div>

        {/* 紐付け変更（開いたときだけ出す行） */}
        {relinking && (
          <div
            className="flex flex-wrap items-center gap-2 border-t border-border-default px-5 py-2"
            data-testid="record-relink-row"
          >
            <span className="text-sm text-text-secondary">紐付ける患者</span>
            <PatientCombobox
              value={nextPatientId}
              onChange={setNextPatientId}
              includeInactive
              className="w-72"
            />
            <Button
              type="button"
              size="sm"
              disabled={!nextPatientId || update.isPending}
              onClick={() => void saveRelink()}
            >
              変更する
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => setRelinking(false)}>
              やめる
            </Button>
          </div>
        )}

        {/* フッタ */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-default px-5 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!rec || !canEditSummary}
              title={canEditSummary ? undefined : '紐付けを変えられるのは管理者と本人だけです'}
              data-testid="record-relink"
              onClick={() => setRelinking((v) => !v)}
            >
              <Link2 className="h-4 w-4" />
              紐付けを変更
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!rec || !isAdmin || retry.isPending}
              title={isAdmin ? undefined : '再処理は管理者のみです'}
              data-testid="record-retry"
              onClick={() => setConfirming('retry')}
            >
              <RotateCcw className="h-4 w-4" />
              再処理
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!rec || !isAdmin || del.isPending}
              title={isAdmin ? undefined : '削除は管理者のみです'}
              data-testid="record-delete"
              className="text-error"
              onClick={() => setConfirming('delete')}
            >
              <Trash2 className="h-4 w-4" />
              削除
            </Button>
          </div>
          <Button
            type="button"
            size="sm"
            variant={reviewed ? 'outline' : 'default'}
            // 「確認済み」= 要約の内容に責任を持つ署名（モバイル VisitRecordCard と同じ規則）。
            // 要約がまだ無い状態では確認する中身が無いので押させない。
            disabled={!rec || update.isPending || (!reviewed && rec?.status !== 'summarized')}
            data-testid="record-reviewed"
            onClick={() => void toggleReviewed()}
          >
            <Check className="h-4 w-4" />
            {reviewed ? '確認を取り消す' : '確認済みにする'}
          </Button>
        </div>

        {/* 確認（再処理 / 削除） */}
        {confirming && (
          <div
            className="flex flex-wrap items-center justify-end gap-2 border-t border-border-default bg-bg-muted px-5 py-2.5"
            data-testid="record-confirm"
          >
            <span className="mr-auto text-sm text-text-primary">
              {confirming === 'delete'
                ? 'この記録を削除します（音声も消えます）。よろしいですか？'
                : 'この記録を文字起こしからやり直します。よろしいですか？'}
            </span>
            <Button type="button" variant="outline" size="sm" onClick={() => setConfirming(null)}>
              やめる
            </Button>
            <Button
              type="button"
              size="sm"
              variant={confirming === 'delete' ? 'destructive' : 'default'}
              disabled={retry.isPending || del.isPending}
              data-testid="record-confirm-ok"
              onClick={() => void (confirming === 'delete' ? runDelete() : runRetry())}
            >
              {confirming === 'delete' ? '削除する' : '再処理する'}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
