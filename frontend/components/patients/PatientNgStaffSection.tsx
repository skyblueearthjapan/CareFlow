/**
 * PatientNgStaffSection — NG スタッフ (患者 × スタッフ割当禁止).
 *
 * 設計書 `docs/plans/patient-ng-staff-design.md` §8-1 (B案: 独立セクション・即時 CRUD)。
 * 患者編集画面 (PatientForm) の同住所セクションの隣に描画する
 * (`SameAddressLinksSection.tsx` をテンプレに揃える)。
 *
 * 仕様:
 *   1. GET /api/v1/patients/{patient_id}/ng-staff で一覧取得 (閲覧は全ロール).
 *   2. StaffCombobox (検索付き) + 理由メモ input + 「追加」で
 *      PUT /api/v1/patients/{patient_id}/ng-staff/{staff_id} (upsert).
 *   3. 行の「削除」で DELETE /api/v1/patients/{patient_id}/ng-staff/{staff_id}.
 *   4. 既に追加済みのスタッフは Combobox の候補から除外する.
 *   5. 編集は admin のみ (isAdminRole)。それ以外は「閲覧のみ」バッジ + 追加/削除 UI 非表示.
 *   6. 退職済み (staff_is_deleted) の行は残し「(退職)」注記を添える.
 */
'use client';

import * as React from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { StaffCombobox } from '@/components/master/StaffCombobox';

import { useDeleteNgStaff, useNgStaffList, useUpsertNgStaff } from '@/lib/queries/patient_ng_staff';
import type { PatientNgStaffRead } from '@/lib/schemas/patient_ng_staff';
import { isAdminRole } from '@/lib/rbac';

export interface PatientNgStaffSectionProps {
  /** 対象患者の ID (新規作成画面では未描画 = 呼び出し側で patientId を条件にする). */
  patientId: string;
  /** true で強制的に読み取り専用 (= staff role 等). */
  readOnly?: boolean;
}

export function PatientNgStaffSection({ patientId, readOnly }: PatientNgStaffSectionProps) {
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canEdit = !readOnly && isAdminRole(role);

  const { data: rows = [], isLoading, isError, error } = useNgStaffList(patientId);
  const upsert = useUpsertNgStaff();
  const remove = useDeleteNgStaff();

  const [staffId, setStaffId] = React.useState('');
  const [note, setNote] = React.useState('');

  const isBusy = upsert.isPending || remove.isPending;
  const excludeIds = React.useMemo(() => rows.map((r) => r.staff_id), [rows]);

  const handleAdd = async () => {
    if (!staffId) return;
    try {
      await upsert.mutateAsync({ patientId, staffId, note: note || null });
      setStaffId('');
      setNote('');
      toast.success('NG スタッフを追加しました');
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存に失敗しました';
      toast.error(`NG スタッフの追加に失敗: ${msg}`);
    }
  };

  const handleDelete = async (row: PatientNgStaffRead) => {
    try {
      await remove.mutateAsync({ patientId, staffId: row.staff_id });
      toast.success('NG スタッフを解除しました');
    } catch (e) {
      const msg = e instanceof Error ? e.message : '削除に失敗しました';
      toast.error(`NG スタッフの解除に失敗: ${msg}`);
    }
  };

  return (
    <Card className="p-5 space-y-3" data-testid="patient-ng-staff-section">
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-lg font-bold text-text-primary">NGスタッフ</h2>
        {!canEdit ? (
          <span className="text-xs text-text-muted bg-bg-muted rounded px-2 py-0.5">閲覧のみ</span>
        ) : null}
      </div>
      <p className="text-xs text-text-muted">
        この患者様に割り当てないスタッフを指定します。自動割当では除外され、提案には警告が付きます。
      </p>

      {canEdit ? (
        <div className="flex flex-wrap items-end gap-2" data-testid="patient-ng-staff-add-row">
          <label className="flex min-w-[14rem] flex-1 flex-col gap-1 text-xs text-text-secondary">
            <span className="font-medium">スタッフ</span>
            <StaffCombobox
              value={staffId}
              onChange={setStaffId}
              excludeIds={excludeIds}
              disabled={isBusy}
              placeholder="スタッフを選択"
            />
          </label>
          <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-xs text-text-secondary">
            <span className="font-medium">理由メモ (任意)</span>
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={isBusy}
              placeholder="なぜ NG にしたか (内部管理用)"
              className="h-9 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-primary disabled:opacity-60"
              data-testid="patient-ng-staff-note-input"
              maxLength={500}
            />
          </label>
          <Button
            type="button"
            onClick={() => void handleAdd()}
            disabled={!staffId || isBusy}
            data-testid="patient-ng-staff-add-button"
          >
            追加
          </Button>
        </div>
      ) : null}

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : isError ? (
        <Alert variant="destructive">
          <AlertTitle>NGスタッフの取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      ) : rows.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid="patient-ng-staff-empty">
          NGスタッフの指定はありません
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li
              key={r.staff_id}
              className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border-default p-3"
              data-testid={`patient-ng-staff-row-${r.staff_id}`}
            >
              <span className="font-medium text-text-primary">
                {r.staff_name ?? '(氏名未登録)'}
              </span>
              {r.staff_is_deleted ? <span className="text-xs text-text-muted">(退職)</span> : null}
              {r.note ? (
                <span className="text-xs text-text-secondary truncate">📝 {r.note}</span>
              ) : null}
              {canEdit ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="ml-auto"
                  onClick={() => void handleDelete(r)}
                  disabled={isBusy}
                  data-testid={`patient-ng-staff-delete-${r.staff_id}`}
                >
                  削除
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
