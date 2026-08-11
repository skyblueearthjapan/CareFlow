'use client';

/**
 * PatientEditDialog — Phase G-20.
 *
 * スケジュール画面の `PatientScheduleDetailDialog` から「編集」ボタンで開く、
 * 患者情報を **その場で編集できるダイアログ**。
 *
 * 構成:
 *   - PatientForm (基本情報 + 連絡先 + 保険・拠点 + 訪問条件 + 希望訪問パターン + 備考)
 *   - PatientFixedVisitsPanel (固定訪問パターン PFV / 各曜日)
 *
 * 仕様:
 *   - usePatient(patientId) で読み込み → patientReadToFormValues で defaultValues
 *   - useUpdatePatient(patientId, initial) で PATCH (PatientForm の onSubmit)
 *   - PFV は PatientFixedVisitsPanel 内で個別に CRUD 完結している (そのまま動作)
 *   - 保存成功時: invalidate patients / visits / courses / patient-fixed-visits
 *     → スケジュール画面に即時反映
 *   - キャンセル: isDirty なら confirm 表示
 *   - RBAC: admin / manager のみ。canEdit=false なら「閲覧専用」アラート
 *
 * 制約 (= /patients/[id]/edit page との分担):
 *   - 既存 edit page (`/patients/[id]/edit`) は大画面用に retain
 *   - PatientForm / PatientFixedVisitsPanel の内部実装は触らない (ラッパーのみ)
 *   - EditPageStickyBar は不要 (Dialog の Cancel で confirm)
 */
import * as React from 'react';
import { Loader2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { useQueryClient } from '@tanstack/react-query';

import { PatientForm } from '@/app/(app)/patients/_components/PatientForm';
import { PatientFixedVisitsPanel } from '@/app/(app)/patients/_components/PatientFixedVisitsPanel';
import { usePatient, useUpdatePatient } from '@/lib/queries/patients';
import {
  patientReadToFormValues,
  type PatientFormValues,
  type WeeklyPattern,
} from '@/lib/schemas/patient';

export interface PatientEditDialogProps {
  patientId: string | null;
  open: boolean;
  onClose: () => void;
  /** admin / manager 以外は閲覧専用 (= 編集フォームを描画しない). */
  canEdit?: boolean;
}

export function PatientEditDialog({
  patientId,
  open,
  onClose,
  canEdit = true,
}: PatientEditDialogProps) {
  const qc = useQueryClient();

  // open=false / patientId=null では fetch しない.
  const patientQuery = usePatient(open && patientId ? patientId : null);
  const patient = patientQuery.data ?? null;

  const initialFormValues = React.useMemo<PatientFormValues | undefined>(
    () => (patient ? patientReadToFormValues(patient) : undefined),
    [patient],
  );

  const updateMutation = useUpdatePatient(patientId ?? '', initialFormValues);

  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);
  const [isFormDirty, setIsFormDirty] = React.useState(false);

  // open 状態のリセット.
  React.useEffect(() => {
    if (!open) {
      setErrorMessage(null);
      setIsFormDirty(false);
    }
  }, [open]);

  const handleSubmit = React.useCallback(
    async (values: PatientFormValues) => {
      if (!patientId) return;
      setErrorMessage(null);
      try {
        await updateMutation.mutateAsync(values);
        // スケジュール画面に即時反映するため、関連 query を一括 invalidate.
        // - patients/fixed-visits は ['patients'] prefix で useUpdatePatient 内で
        //   既に invalidate 済 (FIXED_VISITS_KEY = ['patients', id, 'fixed-visits', ...])
        // - visits / courses は別 key のためここで明示する.
        void qc.invalidateQueries({ queryKey: ['visits'] });
        void qc.invalidateQueries({ queryKey: ['courses'] });
        toast.success('患者情報を更新しました');
        onClose();
      } catch (e) {
        const msg = e instanceof Error ? e.message : '不明なエラー';
        setErrorMessage(msg);
        toast.error(`更新に失敗しました: ${msg}`);
      }
    },
    [patientId, updateMutation, qc, onClose],
  );

  const handleDialogOpenChange = React.useCallback(
    (next: boolean) => {
      if (next) return;
      // isDirty 時は confirm. キャンセルしたら開いたままにする.
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

  const isLoading = patientQuery.isLoading;
  const isError = patientQuery.isError;

  return (
    <Dialog open={open} onOpenChange={handleDialogOpenChange}>
      <DialogContent
        className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col"
        aria-describedby="patient-edit-dialog-description"
        data-testid="patient-edit-dialog"
      >
        <DialogHeader>
          <DialogTitle>
            患者情報を編集
            {patient ? (
              <span className="ml-2 text-sm font-normal text-text-secondary">
                {patient.name}
                {patient.code ? ` (${patient.code})` : null}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription id="patient-edit-dialog-description">
            基本情報・訪問条件 (NGスタッフ /
            同住所紐付けを含む)・希望訪問パターン・固定訪問パターンを編集できます。保存後すぐにスケジュールに反映されます。
          </DialogDescription>
        </DialogHeader>

        {/* 中央の scroll 可能エリア */}
        <div className="flex-1 overflow-y-auto pr-1" data-testid="patient-edit-dialog-body">
          {!canEdit ? (
            <Alert variant="destructive">
              <AlertTitle>権限がありません</AlertTitle>
              <AlertDescription>
                患者の編集は管理者またはマネージャーのみ実行できます。
              </AlertDescription>
            </Alert>
          ) : !patientId ? null : isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-8 w-1/3" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : isError || !patient || !initialFormValues ? (
            <Alert variant="destructive">
              <AlertTitle>取得に失敗しました</AlertTitle>
              <AlertDescription>
                {patientQuery.error instanceof Error
                  ? patientQuery.error.message
                  : '患者情報を読み込めませんでした'}
              </AlertDescription>
            </Alert>
          ) : (
            <div className="space-y-6">
              <PatientForm
                // patientId を渡さないと PatientForm 内の「訪問条件」セクション
                // (NGスタッフ / 同住所紐付け / 特別訪問週間) が一切描画されない。
                patientId={patientId}
                defaultValues={initialFormValues}
                onSubmit={handleSubmit}
                onCancel={handleCancel}
                submitting={updateMutation.isPending}
                errorMessage={errorMessage}
                submitLabel="更新"
                onDirtyChange={setIsFormDirty}
              />

              <PatientFixedVisitsPanel
                patientId={patientId}
                weeklyPattern={patient.weekly_pattern as WeeklyPattern | null | undefined}
                primaryOfficeId={patient.primary_office_id}
                requiresMultipleStaff={patient.requires_multiple_staff === true}
              />

              {updateMutation.isPending ? (
                <div
                  className="flex items-center gap-2 py-2 text-sm text-text-secondary"
                  data-testid="patient-edit-dialog-saving"
                >
                  <Loader2 className="h-4 w-4 animate-spin" />
                  保存中…
                </div>
              ) : null}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
