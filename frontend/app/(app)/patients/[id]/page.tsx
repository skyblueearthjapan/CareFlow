/**
 * Patient detail (Phase 3-2 + 3-7 delete confirm).
 *
 * Sections: 基本情報 / 連絡先 / 保険 / 訪問条件 / 備考.
 * Edit/Delete buttons gated by role (admin/manager edit, admin delete).
 */
'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
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
import { downloadPatientKarte, triggerBlobDownload } from '@/lib/api/patientsExcel';
import { RecordNavigator, type NavigatorRecord } from '@/components/RecordNavigator';
import { useDeletePatient, usePatient, usePatients } from '@/lib/queries/patients';
import { useNgStaffList } from '@/lib/queries/patient_ng_staff';
import { useRegeneratePatientQr } from '@/lib/queries/patientQr';
import { useOffices } from '@/lib/queries/offices';
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
import { formatNgStaffNames } from '@/lib/schemas/patient_ng_staff';
import { useSameAddressCandidates } from '@/lib/queries/g21';
import { formatSameAddressLinkNames } from '@/lib/schemas/g21';
import { PatientFixedVisitsPanel } from '../_components/PatientFixedVisitsPanel';
import { isAdminRole } from '@/lib/rbac';

export default function PatientDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canEdit = isAdminRole(role);
  const canDelete = role === 'admin';
  const canExportKarte = isAdminRole(role);
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const { data, isLoading, isError, error } = usePatient(id);
  const { data: patientsList } = usePatients({ limit: 500 });
  // NGスタッフ (patient-ng-staff-design.md §8-2): 「訪問条件」Card に名前を列挙する.
  const { data: ngStaff = [] } = useNgStaffList(id);
  // 同住所紐付け (Phase G-21): NGスタッフと並べて「訪問条件」Card に列挙する.
  const { data: sameAddressCandidates = [] } = useSameAddressCandidates(id);
  const { offices } = useOffices();
  const deleteMutation = useDeletePatient();
  const regenerateQr = useRegeneratePatientQr(id);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [qrRegenerateOpen, setQrRegenerateOpen] = useState(false);
  const [isExportingKarte, setIsExportingKarte] = useState(false);

  const handleRegenerateQr = async () => {
    try {
      await regenerateQr.mutateAsync();
      toast.success('QRを再発行しました。新しいQRを印刷してください');
      router.push(`/patients/qr-print?mode=single&patient=${id}`);
    } catch (e) {
      toast.error(`QR再発行に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`);
    }
  };

  const handleExportKarte = async () => {
    if (!accessToken || !id) return;
    setIsExportingKarte(true);
    try {
      const { blob, filename } = await downloadPatientKarte({
        patientId: id,
        accessToken,
        refreshToken,
      });
      // Content-Disposition のファイル名 (患者コード_氏名.xlsx) を尊重. なければ fallback.
      triggerBlobDownload(blob, filename ?? `karte_${id}.xlsx`);
    } catch (e) {
      toast.error(`カルテ出力に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`);
    } finally {
      setIsExportingKarte(false);
    }
  };

  // 主担当拠点 表示用: ID → 「拠点名 (住所)」 (UI 統一: 編集 form と同じ可読形式)
  const primaryOfficeLabel = useMemo<string>(() => {
    const oid = data?.primary_office_id;
    if (!oid) return '--';
    const office = offices.find((o) => o.id === oid);
    if (!office) return oid;
    const addr = office.address?.trim();
    return addr ? `${office.name} (${addr})` : office.name;
  }, [data?.primary_office_id, offices]);

  const navRecords = useMemo<NavigatorRecord[]>(
    () =>
      (patientsList?.items ?? []).map((p) => ({
        id: p.id,
        code: p.code,
        name: p.name,
        kana: p.kana,
      })),
    [patientsList],
  );

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
      {/* grid 3 列 (1fr - auto - 1fr) で RecordNavigator を viewport 中央に固定. */}
      <header className="grid grid-cols-1 items-end gap-3 lg:grid-cols-[1fr_auto_1fr]">
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
          <div className="flex flex-wrap items-center gap-2">
            {data.deleted_at ? (
              <span className="inline-block rounded bg-error/10 px-2 py-0.5 text-xs text-error">
                削除済 ({data.deleted_at})
              </span>
            ) : null}
            {/* W37 Phase 3-D: 複数スタッフ対応バッジ */}
            {data.requires_multiple_staff ? (
              <span
                className="inline-block rounded bg-brand-primary/10 px-2 py-0.5 text-xs font-medium text-brand-primary"
                data-testid="badge-multiple-staff"
              >
                2 名対応
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex justify-center">
          {navRecords.length > 0 && (
            <RecordNavigator
              currentId={id}
              records={navRecords}
              hrefTemplate="/patients/{id}"
              entityLabel="患者"
              truncated={patientsList?.truncated}
            />
          )}
        </div>
        <div className="flex gap-2 lg:justify-end">
          {/* RB (PO決定 2026-07-08): 全ロール同一表示。staff には disabled で見せる
              (削除のみ admin 限定のセンシティブ操作として非表示を維持)。 */}
          <Button
            variant="outline"
            onClick={() => void handleExportKarte()}
            disabled={!canExportKarte || isExportingKarte || !accessToken}
          >
            {isExportingKarte ? '出力中…' : '📄 カルテ出力'}
          </Button>
          {canEdit && !data.deleted_at ? (
            <Button asChild variant="outline">
              <Link href={`/patients/qr-print?mode=single&patient=${id}`}>QRを印刷</Link>
            </Button>
          ) : (
            <Button variant="outline" disabled>
              QRを印刷
            </Button>
          )}
          <Button
            variant="outline"
            onClick={() => setQrRegenerateOpen(true)}
            disabled={!canEdit || data.deleted_at != null || regenerateQr.isPending}
          >
            QR再発行
          </Button>
          {canEdit && !data.deleted_at ? (
            <Button asChild variant="outline">
              <Link href={`/patients/${id}/edit`}>編集</Link>
            </Button>
          ) : (
            <Button variant="outline" disabled>
              編集
            </Button>
          )}
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
              ['主担当拠点', primaryOfficeLabel],
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
              // NGスタッフ (patient-ng-staff-design.md §8-2): 性別制限の隣に列挙.
              ['NGスタッフ', formatNgStaffNames(ngStaff)],
              // 同住所紐付け (Phase G-21): 明示設定済みの相手のみ「氏名（モード）」で列挙.
              ['同住所紐付け', formatSameAddressLinkNames(sameAddressCandidates)],
              ['特別週パターン', data.special_weekly_pattern ? '有効' : '--'],
              ['2 名体制必須', data.requires_multiple_staff ? 'はい' : 'いいえ'],
            ];
          })()}
        />
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">希望訪問パターン</h2>
        <WeeklyPatternView raw={data.weekly_pattern} />
      </Card>

      <PatientFixedVisitsPanel
        patientId={data.id}
        primaryOfficeId={data.primary_office_id ?? null}
        readOnly={true}
        requiresMultipleStaff={data.requires_multiple_staff === true}
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

      <Dialog open={qrRegenerateOpen} onOpenChange={setQrRegenerateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>QRを再発行しますか？</DialogTitle>
            <DialogDescription>
              新しいQRコードを発行します。これまでに掲示・配布した古いQRは無効になり、
              読み取るとエラー（更新済み）になります。再発行後は新しいQRを印刷して貼り替えてください。
            </DialogDescription>
          </DialogHeader>
          <p className="rounded-md border border-border-default bg-bg-muted/50 px-3 py-2 text-sm">
            <span className="font-medium">{data.name}</span>
            <span className="ml-2 text-text-muted tnum">({data.code})</span>
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setQrRegenerateOpen(false)}
              disabled={regenerateQr.isPending}
            >
              キャンセル
            </Button>
            <Button
              onClick={() => {
                setQrRegenerateOpen(false);
                void handleRegenerateQr();
              }}
              disabled={regenerateQr.isPending}
            >
              {regenerateQr.isPending ? '再発行中…' : '再発行して印刷へ'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
