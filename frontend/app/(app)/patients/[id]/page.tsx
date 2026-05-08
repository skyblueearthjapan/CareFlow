/**
 * Patient detail (Phase 3-2 + 3-7 delete confirm).
 *
 * Sections: 基本情報 / 連絡先 / 保険 / 訪問条件 / 備考.
 * Edit/Delete buttons gated by role (admin/manager edit, admin delete).
 */
'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useState } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { useDeletePatient, usePatient } from '@/lib/queries/patients';
import {
  INSURANCE_LABEL,
  SEX_LABEL,
  SEX_RESTRICTION_LABEL,
  STATUS_LABEL,
  VISIT_FREQUENCY_LABELS,
  WEEKDAY_LABELS_JA,
  coerceWeeklyPattern,
  normalizePatientInsurance,
  normalizePatientSex,
  normalizePatientSexRestriction,
  normalizePatientStatus,
  type PatientRead,
  type WeekdayKey,
} from '@/lib/schemas/patient';
import { PatientFixedVisitsPanel } from '../_components/PatientFixedVisitsPanel';

export default function PatientDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canEdit = role === 'admin' || role === 'manager';
  const canDelete = role === 'admin';

  const { data, isLoading, isError, error } = usePatient(id);
  const deleteMutation = useDeletePatient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  if (isLoading) {
    return (
      <section className="space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </section>
    );
  }

  if (isError || !data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>取得に失敗しました</AlertTitle>
        <AlertDescription>
          {error instanceof Error ? error.message : '患者情報を読み込めませんでした'}
        </AlertDescription>
      </Alert>
    );
  }

  const handleDelete = async () => {
    try {
      await deleteMutation.mutateAsync(id);
      toast.success('患者を削除しました');
      router.push('/patients');
    } catch (e) {
      toast.error(`削除に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`);
    }
  };

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-text-muted">
            <Link href="/patients" className="hover:underline">
              ← 患者一覧へ
            </Link>
          </p>
          <h1 className="font-serif text-2xl font-bold text-text-primary">
            {data.name}
            <span className="ml-2 text-sm text-text-secondary tnum">({data.code})</span>
          </h1>
          {data.deleted_at ? (
            <span className="inline-block rounded bg-error/10 px-2 py-0.5 text-xs text-error">
              削除済 ({data.deleted_at})
            </span>
          ) : null}
        </div>
        <div className="flex gap-2">
          {canEdit && !data.deleted_at ? (
            <Button asChild variant="outline">
              <Link href={`/patients/${id}/edit`}>編集</Link>
            </Button>
          ) : null}
          {canDelete && !data.deleted_at ? (
            <Button
              variant="destructive"
              onClick={() => setConfirmOpen(true)}
              disabled={deleteMutation.isPending}
            >
              削除
            </Button>
          ) : null}
        </div>
      </header>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">基本情報</h2>
        <DetailGrid
          rows={(() => {
            const sexNorm = normalizePatientSex(data.sex as string | null | undefined);
            const statusNorm = normalizePatientStatus(data.status as string | null | undefined);
            return [
              ['コード', data.code],
              ['氏名', data.name],
              ['カナ', data.kana ?? '--'],
              ['性別', sexNorm ? SEX_LABEL[sexNorm] : '--'],
              ['状態', STATUS_LABEL[statusNorm]],
            ];
          })()}
        />
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">連絡先</h2>
        <DetailGrid
          rows={[
            ['住所', data.address ?? '--'],
            ['緯度', data.lat != null ? String(data.lat) : '--'],
            ['経度', data.lng != null ? String(data.lng) : '--'],
          ]}
        />
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">保険・拠点</h2>
        <DetailGrid
          rows={(() => {
            const insuranceNorm = normalizePatientInsurance(
              data.insurance as string | null | undefined,
            );
            return [
              ['保険区分', insuranceNorm ? INSURANCE_LABEL[insuranceNorm] : '--'],
              ['主担当拠点 ID', data.primary_office_id ?? '--'],
            ];
          })()}
        />
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">訪問条件</h2>
        <DetailGrid
          rows={(() => {
            const sexResNorm = normalizePatientSexRestriction(
              data.sex_restriction as string | null | undefined,
            );
            return [
              ['性別制限', sexResNorm ? SEX_RESTRICTION_LABEL[sexResNorm] : 'なし'],
              ['特別週パターン', data.special_weekly_pattern ? '有効' : '--'],
              ['2 名体制必須', data.requires_multiple_staff ? 'はい' : 'いいえ'],
            ];
          })()}
        />
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">週間訪問パターン</h2>
        <WeeklyPatternView raw={data.weekly_pattern} />
      </Card>

      <PatientFixedVisitsPanel
        patientId={data.id}
        primaryOfficeId={data.primary_office_id ?? null}
        readOnly={true}
      />

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">備考</h2>
        <p className="whitespace-pre-wrap text-sm text-text-primary">{data.note ?? '--'}</p>
      </Card>

      <Card className="p-5 space-y-1 text-xs text-text-muted">
        <div>作成: {data.created_at}</div>
        <div>更新: {data.updated_at}</div>
      </Card>

      <DeleteConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        patient={data}
        submitting={deleteMutation.isPending}
        onConfirm={() => {
          setConfirmOpen(false);
          void handleDelete();
        }}
      />
    </section>
  );
}

interface WeeklyPatternViewProps {
  raw: unknown;
}

function WeeklyPatternView({ raw }: WeeklyPatternViewProps) {
  if (!raw || typeof raw !== 'object') {
    return <p className="text-sm text-text-muted">--</p>;
  }
  const wp = coerceWeeklyPattern(raw);
  const ngWeekdays = wp.ng_weekdays ?? [];
  const labelDays = (days: WeekdayKey[]) =>
    days.length === 0 ? '--' : days.map((d) => WEEKDAY_LABELS_JA[d]).join('・');
  const visitFreq = wp.visit_frequency ? VISIT_FREQUENCY_LABELS[wp.visit_frequency] : '--';
  const timeRange =
    wp.preferred_start || wp.preferred_end
      ? `${wp.preferred_start ?? '--'} 〜 ${wp.preferred_end ?? '--'}`
      : '--';
  return (
    <DetailGrid
      rows={[
        ['週あたり訪問回数', String(wp.frequency_per_week)],
        ['訪問頻度', visitFreq],
        ['訪問週', wp.visit_weeks ?? '--'],
        ['希望曜日', labelDays(wp.preferred_weekdays)],
        ['曜日優先度', wp.weekday_priority],
        ['サービス時間', `${wp.service_minutes} 分`],
        ['時間タイプ', wp.time_type],
        ['希望時間', timeRange],
        ['NG曜日', labelDays(ngWeekdays)],
      ]}
    />
  );
}

interface DetailGridProps {
  rows: ReadonlyArray<readonly [string, string]>;
}

function DetailGrid({ rows }: DetailGridProps) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm md:grid-cols-2">
      {rows.map(([k, v]) => (
        <div key={k} className="flex gap-3 border-b border-border-default/50 pb-2">
          <dt className="w-32 shrink-0 font-medium text-text-secondary">{k}</dt>
          <dd className="text-text-primary">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

interface DeleteConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  patient: PatientRead;
  submitting: boolean;
  onConfirm: () => void;
}

function DeleteConfirmDialog({
  open,
  onOpenChange,
  patient,
  submitting,
  onConfirm,
}: DeleteConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>患者を削除しますか？</DialogTitle>
          <DialogDescription>
            以下の患者を削除します（ソフト削除）。この操作は取り消せます（管理者復旧）。
          </DialogDescription>
        </DialogHeader>
        <p className="rounded-md border border-border-default bg-bg-muted/50 px-3 py-2 text-sm">
          <span className="font-medium">{patient.name}</span>
          <span className="ml-2 text-text-muted tnum">({patient.code})</span>
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            キャンセル
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={submitting}>
            {submitting ? '削除中…' : '削除'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
