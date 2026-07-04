'use client';

/**
 * CreatePatientDialog — W-4 (pool-bulk-insert-design D-4).
 *
 * スケジュール画面のツールバー「＋新規患者登録」から開く、患者マスタと同等の
 * **新規登録ダイアログ**。旧「＋新規提案」(ProposeNewModal) の「新規の方」タブの後継。
 *
 * 構成・方針:
 *   - 患者マスタの `PatientForm` (基本情報 + 連絡先 + 保険・拠点 + 訪問条件 +
 *     希望訪問パターン + 備考) を **そのまま再利用**する (二重実装しない)。
 *     `/patients/new` ページ / `PatientEditDialog` と同一部品。
 *   - 登録は `useCreatePatient` (POST /api/v1/patients)。成功時に PATIENTS_KEY を
 *     invalidate するため、プール (usePatients 由来の poolPatients) に自動反映される。
 *   - PatientForm には希望訪問パターン (weekly_pattern) 編集が含まれるため、登録時に
 *     希望を入れれば frequency_per_week >= 1 で当該患者は保留プールに現れる。
 *   - 登録後: トーストで「希望訪問スケジュール登録 → プール表示」を案内し、患者詳細
 *     (希望訪問スケジュール編集) への導線を提供する。
 *
 * 制約 (= /patients/new page / PatientEditDialog との分担):
 *   - PatientForm の内部実装は触らない (ラッパーのみ)。
 *   - 新規患者にはまだ固定訪問枠 (PFV) が無いため PatientFixedVisitsPanel は出さない。
 */
import * as React from 'react';
import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';

import { PatientForm } from '@/app/(app)/patients/_components/PatientForm';
import { useCreatePatient } from '@/lib/queries/patients';
import type { PatientFormValues, PatientRead } from '@/lib/schemas/patient';

export interface CreatePatientDialogProps {
  open: boolean;
  onClose: () => void;
  /** 登録成功時に呼ばれる (作成された患者を受け取る)。 */
  onCreated?: (patient: PatientRead) => void;
  /** admin / manager 以外は閲覧専用 (= 登録フォームを描画しない)。 */
  canCreate?: boolean;
}

export function CreatePatientDialog({
  open,
  onClose,
  onCreated,
  canCreate = true,
}: CreatePatientDialogProps) {
  const router = useRouter();
  const createMutation = useCreatePatient();

  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);
  const [isFormDirty, setIsFormDirty] = React.useState(false);

  // open 状態のリセット。
  React.useEffect(() => {
    if (!open) {
      setErrorMessage(null);
      setIsFormDirty(false);
    }
  }, [open]);

  const handleSubmit = React.useCallback(
    async (values: PatientFormValues) => {
      setErrorMessage(null);
      try {
        // useCreatePatient は成功時に PATIENTS_KEY を invalidate するため、
        // usePatients 由来の保留プール (poolPatients) は自動で再計算される。
        const created = await createMutation.mutateAsync(values);
        toast.success('患者を登録しました', {
          description:
            '希望訪問スケジュール（週間パターン）を登録すると保留プールに表示されます。',
          action: {
            label: '患者詳細を開く',
            onClick: () => router.push(`/patients/${created.id}`),
          },
        });
        onCreated?.(created);
        onClose();
      } catch (e) {
        const msg = e instanceof Error ? e.message : '不明なエラー';
        setErrorMessage(msg);
        toast.error(`登録に失敗しました: ${msg}`);
      }
    },
    [createMutation, router, onCreated, onClose],
  );

  const handleDialogOpenChange = React.useCallback(
    (next: boolean) => {
      if (next) return;
      if (isFormDirty) {
        const ok = window.confirm(
          '未保存の変更があります。閉じるとこれらの変更は失われます。続行しますか？',
        );
        if (!ok) return;
      }
      onClose();
    },
    [isFormDirty, onClose],
  );

  const handleCancel = React.useCallback(() => {
    if (isFormDirty) {
      const ok = window.confirm(
        '未保存の変更があります。閉じるとこれらの変更は失われます。続行しますか？',
      );
      if (!ok) return;
    }
    onClose();
  }, [isFormDirty, onClose]);

  return (
    <Dialog open={open} onOpenChange={handleDialogOpenChange}>
      <DialogContent
        className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col"
        aria-describedby="create-patient-dialog-description"
        data-testid="create-patient-dialog"
      >
        <DialogHeader>
          <DialogTitle>患者 新規登録</DialogTitle>
          <DialogDescription id="create-patient-dialog-description">
            基本情報・希望訪問パターンを登録します。希望訪問スケジュールを入れると保留プールに表示され、個別／一括投入で予定化できます。
          </DialogDescription>
        </DialogHeader>

        {/* 中央の scroll 可能エリア */}
        <div className="flex-1 overflow-y-auto pr-1" data-testid="create-patient-dialog-body">
          {!canCreate ? (
            <Alert variant="destructive">
              <AlertTitle>権限がありません</AlertTitle>
              <AlertDescription>
                患者の新規登録は管理者またはマネージャーのみ実行できます。
              </AlertDescription>
            </Alert>
          ) : (
            <div className="space-y-6">
              <PatientForm
                onSubmit={handleSubmit}
                onCancel={handleCancel}
                submitting={createMutation.isPending}
                errorMessage={errorMessage}
                submitLabel="登録"
                onDirtyChange={setIsFormDirty}
              />

              {createMutation.isPending ? (
                <div
                  className="flex items-center gap-2 py-2 text-sm text-text-secondary"
                  data-testid="create-patient-dialog-saving"
                >
                  <Loader2 className="h-4 w-4 animate-spin" />
                  登録中…
                </div>
              ) : null}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
