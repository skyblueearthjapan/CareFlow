'use client';

/**
 * 送れなかった録音の一覧（レビュー C-3）。
 *
 * 4xx は「再送しても直らない」が、**消していい**という意味ではない。訪問の会話は
 * 録り直せないので、キューから外した音声は `voice-failed` に残し、ここから
 *   - 再送（BE 側が直った / 紐付けが決まった後）
 *   - 端末に保存（ダウンロード＝最後の逃げ道）
 *   - 削除（確認つき・唯一の不可逆操作）
 * の 3 つを本人の判断でできるようにする。
 */

import { useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { AlertTriangle, Download, RefreshCw, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import {
  audioFileName,
  flushVoiceQueue,
  listFailedVoice,
  removeFailedVoice,
  requeueFailedVoice,
  type FailedVoice,
} from '@/lib/voice/queue';
import { formatBytes } from '@/lib/voice/constants';
import { formatElapsed } from '@/lib/voice/recorder';

/** ISO / epoch → `M/D HH:MM`（端末ローカル）。 */
function fmtWhen(ms: number): string {
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return '';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`;
}

function downloadBlob(blob: Blob, filename: string): boolean {
  try {
    if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return false;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
    return true;
  } catch {
    return false;
  }
}

interface VoiceFailedSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 一覧を操作した後（件数の数え直しに使う）。 */
  onChanged?: () => void;
}

export function VoiceFailedSheet({ open, onOpenChange, onChanged }: VoiceFailedSheetProps) {
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const [rows, setRows] = useState<FailedVoice[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  /** 削除の確認中（1 件だけ）。 */
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!staffId) return;
    setRows(await listFailedVoice(staffId));
  }, [staffId]);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  async function handleRetry(row: FailedVoice) {
    setBusyId(row.id);
    try {
      const moved = await requeueFailedVoice(row.id);
      if (!moved) {
        toast.error('再送できませんでした');
        return;
      }
      const res = await flushVoiceQueue(staffId, { accessToken, refreshToken });
      if (res.sent > 0) toast.success('送信しました');
      else if (res.dropped.length > 0) {
        toast.error('やはり送信できませんでした', { description: res.dropped[0]?.reason });
      } else {
        toast.warning('電波が戻ると自動で送信します');
      }
      await reload();
      onChanged?.();
    } finally {
      setBusyId(null);
    }
  }

  function handleDownload(row: FailedVoice) {
    const ok = downloadBlob(row.blob, audioFileName(row.mimeType));
    if (ok) toast.success('端末に保存しました');
    else toast.error('端末に保存できませんでした');
  }

  async function handleDelete(row: FailedVoice) {
    setBusyId(row.id);
    try {
      await removeFailedVoice(row.id);
      setConfirmId(null);
      await reload();
      onChanged?.();
      toast.success('削除しました');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="voice-failed-sheet">
        <DialogHeader>
          <DialogTitle>送れなかった録音</DialogTitle>
          <DialogDescription>
            音声は端末に残っています。原因が直れば再送できます（削除するまで消えません）。
          </DialogDescription>
        </DialogHeader>

        {rows.length === 0 ? (
          <p className="py-4 text-center text-sm text-text-muted">送れなかった録音はありません</p>
        ) : (
          <ul className="max-h-[60vh] space-y-2 overflow-y-auto">
            {rows.map((row) => (
              <li
                key={row.id}
                className="space-y-2 rounded-md border border-border-default p-3"
                data-testid={`voice-failed-${row.id}`}
              >
                <div className="flex flex-wrap items-center gap-2 text-sm text-text-secondary">
                  <span className="tnum">{fmtWhen(row.droppedAt)}</span>
                  {row.durationSec > 0 && (
                    <span className="tnum">{formatElapsed(row.durationSec)}</span>
                  )}
                  <span className="tnum text-text-muted">{formatBytes(row.blob?.size ?? 0)}</span>
                </div>
                <p className="flex items-start gap-1.5 text-sm text-error">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{row.reason}</span>
                </p>
                {confirmId === row.id ? (
                  <div className="space-y-1.5 rounded-md bg-error-bg px-3 py-2">
                    <p className="text-sm text-error">削除すると元に戻せません。よろしいですか？</p>
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        disabled={busyId === row.id}
                        onClick={() => void handleDelete(row)}
                      >
                        削除する
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setConfirmId(null)}
                      >
                        やめる
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busyId === row.id}
                      onClick={() => void handleRetry(row)}
                    >
                      <RefreshCw className="h-4 w-4" />
                      再送
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => handleDownload(row)}
                    >
                      <Download className="h-4 w-4" />
                      端末に保存
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-error"
                      onClick={() => setConfirmId(row.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                      削除
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}
