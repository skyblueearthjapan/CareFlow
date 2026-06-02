'use client';

import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  downloadKarteTemplate,
  importPatientKarte,
  triggerBlobDownload,
} from '@/lib/api/patientsExcel';
import type { PatientExcelImportResponse } from '@/lib/schemas/patientExcel';
import { ImportPreviewModal } from './ImportPreviewModal';

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB

/**
 * 個別カルテ (1 患者 1 ファイル) 用ボタン群.
 *
 *   * 🗒 空白カルテ — 新規ヒアリング記入用テンプレを DL
 *   * 📄 カルテ取込 — 単一 xlsx を dry_run プレビュー → 反映 (既存 ImportPreviewModal 流用)
 *
 * 一括 Excel (PatientsExcelButtons) とは別ボタン. 取込レスポンスは一括 import と
 * 同一スキーマなので二段階確認 / プレビュー UI はそのまま流用している.
 */
export function PatientKarteButtons() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const queryClient = useQueryClient();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isTemplating, setIsTemplating] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isApplying, setIsApplying] = useState(false);
  // double-click 防止用. setState は async なので useRef で sync ガード.
  const applyingRef = useRef(false);

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState<PatientExcelImportResponse | null>(null);
  // Keep a ref to the file so we can re-submit it for the actual apply.
  const pendingFileRef = useRef<File | null>(null);
  // Latest preview data for the confirm message (state は async なため ref で保持).
  const previewRef = useRef<PatientExcelImportResponse | null>(null);

  // ─── 空白カルテ (template) ────────────────────────────────────────────────

  async function handleTemplate() {
    if (!accessToken) return;
    setIsTemplating(true);
    try {
      const blob = await downloadKarteTemplate({ accessToken, refreshToken });
      triggerBlobDownload(blob, 'patient_karte_template.xlsx');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '空白カルテのダウンロードに失敗しました');
    } finally {
      setIsTemplating(false);
    }
  }

  // ─── カルテ取込 (file select → preview) ───────────────────────────────────

  function handleImportClick() {
    fileInputRef.current?.click();
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset input so the same file can be re-selected.
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
      const result = await importPatientKarte({
        file,
        dryRun: true,
        accessToken,
        refreshToken,
      });
      setPreviewData(result);
      previewRef.current = result;
      setPreviewOpen(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'カルテ取込のプレビューに失敗しました');
      pendingFileRef.current = null;
      previewRef.current = null;
    } finally {
      setIsPreviewing(false);
    }
  }

  // ─── 反映 (apply) ──────────────────────────────────────────────────────────

  async function handleApply() {
    const file = pendingFileRef.current;
    if (!file || !accessToken) return;
    if (applyingRef.current) return; // double-click guard (sync)

    // 二段階確認: 件数を user に提示してから反映する.
    const preview = previewRef.current;
    const summary = preview?.summary;
    const totalCount = summary
      ? summary.patients_new +
        summary.patients_update +
        summary.patients_delete +
        summary.pfv_new +
        summary.pfv_update +
        summary.pfv_delete
      : 0;
    const errorCount = summary ? summary.patients_error + summary.pfv_error : 0;
    const confirmMsg =
      `カルテの内容を患者マスタに ${totalCount} 件反映します。\n` +
      `(新規: 患者 ${summary?.patients_new ?? 0} + PFV ${summary?.pfv_new ?? 0})\n` +
      `(更新: 患者 ${summary?.patients_update ?? 0} + PFV ${summary?.pfv_update ?? 0})\n` +
      `(削除: 患者 ${summary?.patients_delete ?? 0} + PFV ${summary?.pfv_delete ?? 0})\n` +
      (errorCount > 0 ? `(エラー ${errorCount} 件は skip します)\n` : '') +
      `\nこの操作は取り消せません。続行しますか？`;
    if (typeof window !== 'undefined' && !window.confirm(confirmMsg)) return;

    applyingRef.current = true;
    setIsApplying(true);
    try {
      await importPatientKarte({
        file,
        dryRun: false,
        accessToken,
        refreshToken,
      });
      toast.success(
        errorCount > 0
          ? `カルテを反映しました (${totalCount} 件反映、エラー ${errorCount} 件 skip)`
          : `カルテを反映しました (${totalCount} 件反映)`,
      );
      setPreviewOpen(false);
      setPreviewData(null);
      previewRef.current = null;
      pendingFileRef.current = null;
      // patients リスト + 配下の固定枠 (queryKey が ['patients', id, 'fixed-visits']) も
      // ['patients'] 接頭辞で一斉 invalidate される.
      void queryClient.invalidateQueries({ queryKey: ['patients'] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '反映に失敗しました');
    } finally {
      applyingRef.current = false;
      setIsApplying(false);
    }
  }

  function handleClose() {
    if (isApplying) return;
    setPreviewOpen(false);
    setPreviewData(null);
    previewRef.current = null;
    pendingFileRef.current = null;
  }

  return (
    <>
      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hidden"
        onChange={handleFileChange}
      />

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handleTemplate}
          disabled={isTemplating || !accessToken}
        >
          {isTemplating ? 'ダウンロード中...' : '🗒 空白カルテ'}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleImportClick}
          disabled={isPreviewing || !accessToken}
        >
          {isPreviewing ? 'プレビュー中...' : '📄 カルテ取込'}
        </Button>
      </div>

      <ImportPreviewModal
        open={previewOpen}
        preview={previewData}
        isApplying={isApplying}
        onApply={handleApply}
        onClose={handleClose}
      />
    </>
  );
}
