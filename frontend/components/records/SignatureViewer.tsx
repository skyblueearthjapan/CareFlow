'use client';

/**
 * 「サインを見る」— サインの画像を大きく出す (スマホの訪問詳細・PC の打刻履歴・
 * 訪問モニターで共通。設計 `signature-checkin-design-2026-10-06.md` §5-1 Q6・Q7)。
 *
 * - 画像は `GET /api/v1/visit-signatures/{id}/image` (Bearer 必須)。素の `<img src>` では
 *   認証が付かないので、fetch で blob を取り objectURL にする (`AuthedPhoto` と同じ)。
 * - 管理者も職員も、過去の日も見られる。**見るたびにサーバの監査ログに残る** ので、
 *   開いたときだけ取りに行き (一覧で先読みしない)、「見た記録が残ります」と添える。
 * - 保存期間 (5 年) を過ぎて消した画像は 410。サーバの文言をそのまま出す。
 */

import { useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { FileClock } from 'lucide-react';

import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { signatureImagePath } from '@/lib/signature-checkout';

export interface SignatureViewerProps {
  /** 表示するサインの ID。null で閉じる。 */
  signatureId: string | null;
  onClose: () => void;
  /** 見出し (例: 「サイン — 山田 花子 様」)。 */
  title: string;
  /** 補足 (例: 「2026/10/6（火） 退出 13:38 にサイン」)。 */
  meta?: string;
}

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; url: string }
  | { kind: 'error'; message: string };

const MESSAGE_FAILED = 'サインを読み込めませんでした。電波の良い場所で、もう一度お試しください';

async function detailOf(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === 'string' ? body.detail : null;
  } catch {
    return null;
  }
}

export function SignatureViewer({ signatureId, onClose, title, meta }: SignatureViewerProps) {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const [state, setState] = useState<LoadState>({ kind: 'loading' });

  useEffect(() => {
    if (!signatureId) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ kind: 'loading' });
    async function load(id: string) {
      try {
        const res = await fetch(signatureImagePath(id), {
          headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
          cache: 'no-store',
        });
        if (!res.ok) {
          const detail = res.status === 410 || res.status === 404 ? await detailOf(res) : null;
          if (!cancelled) setState({ kind: 'error', message: detail ?? MESSAGE_FAILED });
          return;
        }
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ kind: 'ready', url: objectUrl });
      } catch {
        if (!cancelled) setState({ kind: 'error', message: MESSAGE_FAILED });
      }
    }
    void load(signatureId);
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [signatureId, accessToken]);

  return (
    <Dialog
      open={signatureId != null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {signatureId != null && (
        <DialogContent className="max-w-xl gap-3" data-testid="signature-viewer">
          <DialogTitle className="pr-8 text-[16px]">{title}</DialogTitle>
          <DialogDescription className="sr-only">サインの画像</DialogDescription>
          <div className="flex aspect-[2/1] w-full items-center justify-center overflow-hidden rounded-lg border border-border-default bg-white">
            {state.kind === 'ready' ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={state.url}
                alt="サインの画像"
                className="h-full w-full object-contain"
                data-testid="signature-viewer-image"
              />
            ) : state.kind === 'error' ? (
              <p className="px-4 text-center text-[15px] text-text-secondary" role="alert">
                {state.message}
              </p>
            ) : (
              <div className="h-full w-full animate-pulse bg-bg-muted" aria-label="読み込み中" />
            )}
          </div>
          {meta && <p className="tnum text-[15px] text-text-secondary">{meta}</p>}
          <p className="inline-flex items-center gap-1.5 text-[13px] text-text-secondary">
            <FileClock className="h-4 w-4 shrink-0" aria-hidden="true" />
            見た記録が残ります（見た人・日時）
          </p>
        </DialogContent>
      )}
    </Dialog>
  );
}
