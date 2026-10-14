/**
 * 「QRの管理」ダイアログ (設計 docs/plans/qr-manage-reissue-guard-2026-10-14.md §3-2)。
 *
 * 患者画面の右上の「QRの管理 ›」から開く。今の版・最後に読めた日・猶予中の古い QR を
 * 見せ、[QRを印刷する] を主役に置く。再発行は一番下にたたみ、理由を選んでチェックを
 * 入れたときだけ赤いボタンが押せる (PO 2026-10-14)。
 *
 * 理由で古いシールの扱いが決まる (BE が valid_until を決める):
 *   汚れた・読めない → 7 日間使える / なくした・他人に使われた恐れ → すぐ使えなくなる。
 * 実行後は今までと同じく印刷画面へ移る。
 */
'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { jstHm } from '@/lib/format/actualTime';
import {
  usePatientQrStatus,
  useRegeneratePatientQr,
  type QrRegenerateReason,
} from '@/lib/queries/patientQr';

const REASONS: { value: QrRegenerateReason; label: string; note: string }[] = [
  { value: 'damaged', label: '汚れた・読めない', note: '古いシールは 7 日間使えます' },
  { value: 'lost', label: 'なくした', note: '古いシールはすぐ使えなくなります' },
  { value: 'misuse', label: '他人に使われた恐れ', note: '古いシールはすぐ使えなくなります' },
];

/** 「最後に読めた日」がこの期間内なら、作り直す前に黄色の注意を出す。 */
const RECENT_SCAN_MS = 7 * 24 * 60 * 60 * 1000;

const JST_MD = new Intl.DateTimeFormat('ja-JP', {
  month: 'numeric',
  day: 'numeric',
  timeZone: 'Asia/Tokyo',
});

/** ISO 8601 → JST の "M/D"。読めない値は null。 */
function jstMd(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return JST_MD.format(d);
}

interface PatientQrManageDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  patient: { id: string; name: string; code: string };
}

export function PatientQrManageDialog({ open, onOpenChange, patient }: PatientQrManageDialogProps) {
  const router = useRouter();
  const statusQuery = usePatientQrStatus(patient.id, { enabled: open });
  const regenerateQr = useRegeneratePatientQr(patient.id);
  const [reissueOpen, setReissueOpen] = useState(false);
  const [reason, setReason] = useState<QrRegenerateReason | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  const resetReissue = () => {
    setReissueOpen(false);
    setReason(null);
    setConfirmed(false);
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) resetReissue();
    onOpenChange(next);
  };

  const qr = statusQuery.data;
  const lastScannedMs = qr?.last_scanned_at ? new Date(qr.last_scanned_at).getTime() : NaN;
  const scannedRecently =
    !Number.isNaN(lastScannedMs) && Date.now() - lastScannedMs <= RECENT_SCAN_MS;
  const canSubmit = reason !== null && confirmed && !regenerateQr.isPending;
  const printHref = `/patients/qr-print?mode=single&patient=${patient.id}`;

  // 同じ瞬間の連打で 2 回送らない (isPending は再描画の後にしか効かないため)。
  const submittingRef = useRef(false);
  const handleRegenerate = async () => {
    if (reason === null || submittingRef.current) return;
    submittingRef.current = true;
    try {
      await regenerateQr.mutateAsync(reason);
      toast.success('QRを作り直しました。新しいQRを印刷して貼り替えてください');
      handleOpenChange(false);
      router.push(printHref);
    } catch (e) {
      toast.error(`QRを作り直せませんでした: ${e instanceof Error ? e.message : '不明なエラー'}`);
    } finally {
      submittingRef.current = false;
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent data-testid="qr-manage-dialog">
        <DialogHeader>
          <DialogTitle>QRの管理</DialogTitle>
          <DialogDescription>
            {patient.name} 様<span className="ml-1 tnum">（{patient.code}）</span>
          </DialogDescription>
        </DialogHeader>

        {statusQuery.isLoading ? (
          <p className="text-sm text-text-muted">読み込み中…</p>
        ) : statusQuery.isError || !qr ? (
          <p className="text-sm text-error">QRの状態を読み込めませんでした</p>
        ) : (
          <dl
            className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm"
            data-testid="qr-manage-status"
          >
            <dt className="text-text-muted">今の QR</dt>
            <dd data-testid="qr-manage-version">
              第{qr.version}版{qr.issued_at ? `（${jstMd(qr.issued_at)} 発行）` : ''}
            </dd>
            <dt className="text-text-muted">最後に読めた日</dt>
            <dd data-testid="qr-manage-last-scan">
              {qr.last_scanned_at
                ? `${jstMd(qr.last_scanned_at)} ${jstHm(qr.last_scanned_at)}${
                    qr.last_scanned_staff_name ? ` ${qr.last_scanned_staff_name}さん` : ''
                  }`
                : 'まだ読まれていません'}
            </dd>
            {qr.grace.map((g) => (
              <div key={g.version} className="contents" data-testid="qr-manage-grace">
                <dt className="text-text-muted">古い QR</dt>
                <dd>
                  第{g.version}版は {jstMd(g.valid_until)} まで使えます
                </dd>
              </div>
            ))}
          </dl>
        )}

        <div className="flex justify-center py-2">
          <Button asChild>
            <Link href={printHref}>QRを印刷する</Link>
          </Button>
        </div>

        <div className="border-t border-border-default pt-3">
          <button
            type="button"
            className="text-sm text-text-secondary hover:underline"
            aria-expanded={reissueOpen}
            onClick={() => (reissueOpen ? resetReissue() : setReissueOpen(true))}
            data-testid="qr-manage-reissue-toggle"
          >
            {reissueOpen ? '▾' : '▸'} シールをなくした・汚れた時だけ
          </button>

          {reissueOpen ? (
            <div className="mt-3 space-y-3 text-sm" data-testid="qr-manage-reissue">
              {scannedRecently && qr?.last_scanned_at ? (
                <p
                  className="rounded-md border border-warning bg-warning-bg px-3 py-2 text-warning-strong"
                  data-testid="qr-manage-recent-warning"
                >
                  {jstMd(qr.last_scanned_at)} に QR が読めています。本当に作り直しますか？
                </p>
              ) : null}
              <fieldset className="space-y-1.5">
                <legend className="mb-1 font-semibold text-text-secondary">理由</legend>
                {REASONS.map((r) => (
                  <label key={r.value} className="flex cursor-pointer items-start gap-2">
                    <input
                      type="radio"
                      name="qr-regenerate-reason"
                      className="mt-0.5 h-4 w-4"
                      checked={reason === r.value}
                      onChange={() => setReason(r.value)}
                      data-testid={`qr-manage-reason-${r.value}`}
                    />
                    <span>
                      {r.label}
                      <span className="ml-1 text-text-muted">→ {r.note}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                  data-testid="qr-manage-confirm"
                />
                <span>新しいシールを印刷して貼り替えます</span>
              </label>
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={resetReissue} disabled={regenerateQr.isPending}>
                  やめる
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => void handleRegenerate()}
                  disabled={!canSubmit}
                  data-testid="qr-manage-regenerate"
                >
                  {regenerateQr.isPending ? '作り直し中…' : '作り直して印刷へ'}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
