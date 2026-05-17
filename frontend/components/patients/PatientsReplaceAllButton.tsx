'use client';

import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { replaceAllPatientsExcel } from '@/lib/api/patientsExcel';
import type { PatientExcelReplaceAllResponse } from '@/lib/schemas/patientExcel';

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB

type Stage = 'idle' | 'select' | 'preview';

/**
 * 完全置換 (バックアップ復元) ボタン.
 *
 * 通常の Excel インポートと違い、Excel に無い患者は soft delete されるため
 * 二段階確認 (ファイル選択 + プレビュー + 最終確認) を経て実行する.
 * RBAC: admin のみ表示する (parent 側でガード推奨).
 */
export function PatientsReplaceAllButton() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const queryClient = useQueryClient();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [stage, setStage] = useState<Stage>('idle');
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isApplying, setIsApplying] = useState(false);
  const applyingRef = useRef(false);
  const pendingFileRef = useRef<File | null>(null);
  const [preview, setPreview] = useState<PatientExcelReplaceAllResponse | null>(null);

  function openSelectStage() {
    setStage('select');
  }

  function close() {
    if (isApplying) return;
    setStage('idle');
    setPreview(null);
    pendingFileRef.current = null;
  }

  function handlePickFile() {
    fileInputRef.current?.click();
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      toast.error('Excel ファイル (.xlsx) を選択してください');
      return;
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      toast.error('ファイルサイズが大きすぎます (最大 10MB)');
      return;
    }
    if (!accessToken) return;

    pendingFileRef.current = file;
    setIsPreviewing(true);
    try {
      const result = await replaceAllPatientsExcel({
        file,
        dryRun: true,
        accessToken,
        refreshToken,
      });
      setPreview(result);
      setStage('preview');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '完全置換のプレビューに失敗しました');
      pendingFileRef.current = null;
    } finally {
      setIsPreviewing(false);
    }
  }

  async function handleConfirmApply() {
    const file = pendingFileRef.current;
    if (!file || !accessToken) return;
    if (applyingRef.current) return;

    applyingRef.current = true;
    setIsApplying(true);
    const summary = preview?.summary;
    try {
      await replaceAllPatientsExcel({
        file,
        dryRun: false,
        accessToken,
        refreshToken,
      });
      close();
      toast.success(
        `患者マスタを完全置換しました (` +
          `更新 ${summary?.patients_to_update ?? 0} / ` +
          `新規 ${summary?.patients_to_create ?? 0} / ` +
          `削除 ${summary?.patients_to_soft_delete ?? 0})`,
      );
      void queryClient.invalidateQueries({ queryKey: ['patients'] });
    } catch (err) {
      toast.error(
        err instanceof Error
          ? `${err.message} (変更はすべてロールバックされています)`
          : '完全置換に失敗しました (変更はすべてロールバックされています)',
      );
    } finally {
      applyingRef.current = false;
      setIsApplying(false);
    }
  }

  const summary = preview?.summary;
  const errorCount = summary ? summary.patients_error + summary.pfv_error : 0;
  const hasErrors = errorCount > 0;

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        onChange={handleFileChange}
      />

      <Button
        variant="destructive"
        size="sm"
        onClick={openSelectStage}
        disabled={!accessToken}
        title="バックアップ復元目的の完全置換 (admin のみ)"
      >
        ⚠️ 完全置換
      </Button>

      {/* Stage 1: file select + warning */}
      <Dialog
        open={stage === 'select'}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>⚠️ 完全置換インポート</DialogTitle>
            <DialogDescription>この操作はバックアップからの完全復元です。</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-sm text-text-secondary">
            <ul className="list-disc space-y-1 pl-5">
              <li>
                現状の全患者を Excel の内容で<strong>完全に置き換え</strong>ます
              </li>
              <li>
                Excel に無い患者は<strong>削除</strong>されます (soft delete)
              </li>
              <li>
                Excel の空セルは <strong>NULL</strong> で上書きされます
              </li>
              <li>固定訪問スケジュール (PFV) は全件物理削除され、Excel から再投入されます</li>
              <li>
                通常編集ではなく <strong>バックアップ復元目的</strong> で使ってください
              </li>
            </ul>
            <p className="pt-2">ファイルを選択してください:</p>
            <Button variant="outline" size="sm" onClick={handlePickFile} disabled={isPreviewing}>
              {isPreviewing ? 'プレビュー中...' : '📁 ファイル選択'}
            </Button>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={close} disabled={isPreviewing}>
              キャンセル
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Stage 2: preview + final confirm */}
      <Dialog
        open={stage === 'preview'}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>完全置換プレビュー</DialogTitle>
            <DialogDescription>
              内容を確認のうえ「完全置換を実行する」を押してください。
            </DialogDescription>
          </DialogHeader>
          {preview && summary ? (
            <div className="space-y-3 text-sm">
              <ul className="space-y-1">
                <li>
                  更新: <strong>{summary.patients_to_update}</strong> 件
                </li>
                <li>
                  新規: <strong>{summary.patients_to_create}</strong> 件
                </li>
                <li>
                  削除: <strong>{summary.patients_to_soft_delete}</strong> 件 (Excel に無い既存患者)
                </li>
                <li>
                  PFV: 既存 {summary.pfv_to_replace} 件を物理削除 → Excel から{' '}
                  {summary.pfv_to_create} 件を再投入
                </li>
                {hasErrors ? (
                  <li className="text-red-600 dark:text-red-400">
                    エラー: <strong>{errorCount}</strong> 件 (このまま実行すると 422 で失敗します)
                  </li>
                ) : null}
              </ul>

              {preview.patients_to_soft_delete_preview.length > 0 ? (
                <div className="space-y-1 border-t border-border-default pt-3">
                  <p className="font-medium text-text-primary">
                    削除される患者の一覧 ({preview.patients_to_soft_delete_preview.length} 件):
                  </p>
                  <div className="max-h-48 overflow-auto rounded border border-border-default bg-bg-muted/30 p-2">
                    <ul className="space-y-0.5 text-xs">
                      {preview.patients_to_soft_delete_preview.map((p) => (
                        <li key={p.patient_id} className="tnum">
                          {p.patient_code ?? '(no code)'} {p.name}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : null}

              <p className="text-xs text-text-muted">
                ⚠️ 確認: 上記が正しい場合のみ実行してください。この操作は取り消せません。
              </p>
            </div>
          ) : (
            <p className="text-sm text-text-muted">プレビューを読み込めませんでした。</p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={close} disabled={isApplying}>
              キャンセル
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmApply}
              disabled={isApplying || hasErrors || !preview}
            >
              {isApplying ? '実行中...' : '⚠️ 完全置換を実行する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
