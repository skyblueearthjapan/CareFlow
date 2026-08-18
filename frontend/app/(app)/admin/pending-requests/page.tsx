'use client';

/**
 * /admin/pending-requests — W3-FE6 申請履歴ビュー (PC).
 *
 * 設計仕様書 v0.9 §3.5.5「履歴 UI」/ 実装手順書 v0.2 §4 W3-FE6 に準拠。
 *
 * 主要要件:
 *   1. admin / manager 限定 (UI ガード + Backend RBAC で二重防御)
 *   2. **スタッフ予定 vs 患者関連** をタブで明確に分離 (§3.5.5)
 *   3. ステータス / request_type / 対象スタッフ / 対象患者 / 対象日のフィルタ
 *   4. 各申請に「承認 / 却下」ボタン
 *   5. 却下時は理由入力ダイアログで必須化 (§3.5.4)
 *
 * 「編集承認」(JSON テキストエリア) は 2026-08-18 に UI から撤去 —
 * 人が読めない生 payload 編集は運用に不適で、休みの調整は右パネル
 * (StaffLeavePanel) の直接追加/取消で代替できる。BE エンドポイント
 * (approve-with-edit) と FE フックは互換のため存続。
 */

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Filter, RefreshCw } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { RakusukeNote, RakusukeTitle } from '@/components/brand/Rakusuke';
import { Badge } from '@/components/ui/badge';
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
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { isAdminRole } from '@/lib/rbac';
import { usePatients } from '@/lib/queries/patients';
import { useStaffList } from '@/lib/queries/staff';
import { StaffLeavePanel } from './_components/StaffLeavePanel';
import {
  useApproveRequest,
  usePendingRequests,
  useRejectRequest,
  type UsePendingRequestsParams,
} from '@/lib/queries/pending_requests';
import {
  REQUEST_STATUS_VALUES,
  REQUEST_TYPE_VALUES,
  type PendingRequestV2Read,
  type RequestStatus,
  type RequestType,
} from '@/lib/schemas/pending_request';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

const PAGE_SIZE = 100;

/**
 * `request_type` を「スタッフ予定」「患者関連」のどちらに属するかに分類する
 * 対応表 (§3.5.5)。`staff_create` / `patient_create` などの「マスタ新規登録系」
 * もそれぞれの軸に紐づけて表示する。
 */
const STAFF_REQUEST_TYPES = new Set<RequestType>([
  'staff_off',
  'staff_event',
  'staff_mentor',
  'staff_create',
  'staff_status_update',
]);

const PATIENT_REQUEST_TYPES = new Set<RequestType>([
  'patient_create',
  'patient_cancel',
  'patient_reschedule',
  'patient_special_week_on',
  'patient_special_week_off',
  'patient_status_update',
]);

const REQUEST_TYPE_LABEL: Record<RequestType, string> = {
  staff_off: 'スタッフ休み',
  staff_event: 'スタッフイベント',
  staff_mentor: '同行スタッフ',
  staff_create: 'スタッフ新規',
  staff_status_update: 'スタッフ状態変更',
  patient_create: '患者新規',
  patient_cancel: '患者キャンセル',
  patient_reschedule: '日時変更',
  patient_special_week_on: '特別週ON',
  patient_special_week_off: '特別週OFF',
  patient_status_update: '患者状態変更',
  patient_visit_add: '訪問追加',
};

const STATUS_LABEL: Record<RequestStatus, string> = {
  pending: '保留中',
  approved: '承認済み',
  rejected: '却下',
};

/**
 * 種別チップの配色 (2026-08-18 可読性改善)。同系統の申請は近い色相に寄せる:
 * 休み系=ブランドピンク / 予定・時間系=青紫 / 新規登録系=緑 / 状態変更系=琥珀 /
 * キャンセル=ローズ。
 */
const REQUEST_TYPE_BADGE_CLASS: Record<RequestType, string> = {
  staff_off: 'bg-brand-primary-light text-brand-primary',
  staff_event: 'bg-sky-100 text-sky-700',
  staff_mentor: 'bg-bg-muted text-text-muted',
  staff_create: 'bg-emerald-100 text-emerald-700',
  staff_status_update: 'bg-amber-100 text-amber-700',
  patient_create: 'bg-emerald-100 text-emerald-700',
  patient_cancel: 'bg-rose-100 text-rose-700',
  patient_reschedule: 'bg-violet-100 text-violet-700',
  patient_special_week_on: 'bg-indigo-100 text-indigo-700',
  patient_special_week_off: 'bg-indigo-50 text-indigo-500',
  patient_status_update: 'bg-amber-100 text-amber-700',
  patient_visit_add: 'bg-teal-100 text-teal-700',
};

/** scope は patient_reschedule のみ意味を持つ → 独立列ではなく種別の注記に格上げ。 */
const SCOPE_LABEL: Record<string, string> = {
  one_time: 'この1回のみ',
  permanent: '恒久変更',
};

const WEEKDAYS_JP = ['日', '月', '火', '水', '木', '金', '土'] as const;

/** ISO 日時 → '2026/8/18 13:48' (端末ローカル = JST)。 */
function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(
    2,
    '0',
  )}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** 'YYYY-MM-DD' → '9/4（金）' (年が違うときだけ '2027年' を前置)。 */
function fmtTargetDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  if (Number.isNaN(d.getTime())) return iso;
  const yearPrefix = d.getFullYear() !== new Date().getFullYear() ? `${d.getFullYear()}年` : '';
  return `${yearPrefix}${d.getMonth() + 1}/${d.getDate()}（${WEEKDAYS_JP[d.getDay()]}）`;
}

/**
 * payload を人が読める1行要約へ (生 JSON の表示は撤去・2026-08-18 ユーザー要望)。
 * 生 payload は DB (payload/edited_payload 列) に残り、必要なら監査で参照する。
 */
function summarizeRequest(row: PendingRequestV2Read): string {
  const p = (row.edited_payload ?? row.payload ?? {}) as Record<string, unknown>;
  const str = (k: string): string => (typeof p[k] === 'string' ? (p[k] as string) : '');
  const parts: string[] = [];

  switch (row.request_type) {
    case 'staff_event':
      if (str('title')) parts.push(str('title'));
      if (str('start_time') && str('end_time'))
        parts.push(`${str('start_time')}〜${str('end_time')}`);
      break;
    case 'patient_reschedule':
    case 'patient_visit_add':
      if (str('start_time') && str('end_time'))
        parts.push(`${str('start_time')}〜${str('end_time')}`);
      break;
    case 'staff_create':
    case 'patient_create':
      if (str('name')) parts.push(`氏名: ${str('name')}`);
      break;
    case 'staff_status_update':
    case 'patient_status_update':
      if (str('status')) parts.push(`→ ${str('status')}`);
      break;
    default:
      break;
  }

  const note = str('note') || str('reason');
  if (note) parts.push(note);
  return parts.join(' ・ ');
}

function statusBadgeVariant(s: RequestStatus): 'warning' | 'success' | 'destructive' {
  switch (s) {
    case 'pending':
      return 'warning';
    case 'approved':
      return 'success';
    case 'rejected':
      return 'destructive';
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Page component
// ─────────────────────────────────────────────────────────────────────────

type CategoryTab = 'staff' | 'patient';

export default function AdminPendingRequestsPage() {
  const { data: session, status: sessionStatus } = useSession();
  const router = useRouter();
  const role = session?.user?.role;
  const isAuthorized = isAdminRole(role);

  // Soft client-side guard. The API enforces RBAC server-side as well.
  useEffect(() => {
    if (sessionStatus === 'authenticated' && !isAuthorized) {
      router.replace('/dashboard');
    }
  }, [sessionStatus, isAuthorized, router]);

  // Filters
  const [tab, setTab] = useState<CategoryTab>('staff');
  const [statusFilter, setStatusFilter] = useState<RequestStatus | ''>('pending');
  const [typeFilter, setTypeFilter] = useState<RequestType | ''>('');
  const [staffIdFilter, setStaffIdFilter] = useState('');
  const [patientIdFilter, setPatientIdFilter] = useState('');
  const [dateFilter, setDateFilter] = useState('');

  // Action dialog state
  const [rejectTarget, setRejectTarget] = useState<PendingRequestV2Read | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  const queryParams = useMemo<UsePendingRequestsParams>(() => {
    const p: UsePendingRequestsParams = {
      limit: PAGE_SIZE,
      offset: 0,
    };
    if (statusFilter) p.status = statusFilter;
    if (typeFilter) p.request_type = typeFilter;
    if (staffIdFilter.trim()) p.target_staff_id = staffIdFilter.trim();
    if (patientIdFilter.trim()) p.target_patient_id = patientIdFilter.trim();
    if (dateFilter) {
      p.target_date_from = dateFilter;
      p.target_date_to = dateFilter;
    }
    return p;
  }, [statusFilter, typeFilter, staffIdFilter, patientIdFilter, dateFilter]);

  const { data, isLoading, isError, error, refetch, isFetching } = usePendingRequests(queryParams);

  // UUID → 実名の解決 (2026-08-18 可読性改善)。500件キャップは既存リストと同じ規約。
  const staffList = useStaffList({ limit: 500 });
  const patientsList = usePatients({ limit: 500 });
  const staffNameById = useMemo(
    () => new Map((staffList.data ?? []).map((s) => [s.id, s.name])),
    [staffList.data],
  );
  const patientNameById = useMemo(
    () => new Map((patientsList.data?.items ?? []).map((p) => [p.id, p.name])),
    [patientsList.data],
  );

  const approve = useApproveRequest();
  const reject = useRejectRequest();

  // Split items into staff vs patient buckets.
  const { staffItems, patientItems } = useMemo(() => {
    const items = data?.items ?? [];
    const staff: PendingRequestV2Read[] = [];
    const patient: PendingRequestV2Read[] = [];
    for (const it of items) {
      if (STAFF_REQUEST_TYPES.has(it.request_type)) {
        staff.push(it);
      } else if (PATIENT_REQUEST_TYPES.has(it.request_type)) {
        patient.push(it);
      }
    }
    return { staffItems: staff, patientItems: patient };
  }, [data]);

  if (sessionStatus === 'loading' || (sessionStatus === 'authenticated' && !isAuthorized)) {
    return null;
  }

  // ── Action handlers ────────────────────────────────────────────────────
  const handleApprove = (row: PendingRequestV2Read) => {
    setActionError(null);
    approve.mutate(row.id, {
      onError: (err) => setActionError(err.message),
    });
  };

  const openRejectDialog = (row: PendingRequestV2Read) => {
    setActionError(null);
    setRejectTarget(row);
    setRejectReason('');
  };

  const handleReject = () => {
    if (!rejectTarget) return;
    const reason = rejectReason.trim();
    if (reason.length === 0) {
      // UI 側で空チェック (Backend / zod でも min(1) で守られている)
      setActionError('却下理由は必須です');
      return;
    }
    reject.mutate(
      { id: rejectTarget.id, rejection_reason: reason },
      {
        onSuccess: () => {
          setRejectTarget(null);
          setRejectReason('');
        },
        onError: (err) => setActionError(err.message),
      },
    );
  };

  const total = data?.total ?? 0;

  return (
    <section className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <RakusukeTitle
          pose="clap"
          title="モバイル申請履歴"
          subtitle={<>全 {total} 件 — モバイル経由の申請を管理者が承認・却下します</>}
        />
        <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} />
          再読み込み
        </Button>
      </header>

      {actionError && (
        <Alert variant="destructive">
          <AlertTitle>操作に失敗しました</AlertTitle>
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}

      {/* 2カラム: 左=申請リスト (主) / 右=休み・月確定パネル (副)。
          両カラムをストレッチして枠の下端を揃える (2026-08-18 ユーザー要望)。
          パネルでスタッフを選ぶと左のリストも同じスタッフで絞り込まれる。 */}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="flex min-w-0 flex-col gap-4">
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-text-muted" />
            <span className="text-sm text-text-secondary">フィルタ</span>
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as RequestStatus | '')}
            className="h-10 rounded-md border border-border-default bg-bg-base px-3 text-sm"
            aria-label="ステータスフィルタ"
          >
            <option value="">ステータス (すべて)</option>
            {REQUEST_STATUS_VALUES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as RequestType | '')}
            className="h-10 rounded-md border border-border-default bg-bg-base px-3 text-sm"
            aria-label="種別フィルタ"
          >
            <option value="">種別 (すべて)</option>
            {REQUEST_TYPE_VALUES.map((t) => (
              <option key={t} value={t}>
                {REQUEST_TYPE_LABEL[t]}
              </option>
            ))}
          </select>
          <Input
            value={staffIdFilter}
            onChange={(e) => setStaffIdFilter(e.target.value)}
            placeholder="対象スタッフ ID (UUID)"
            className="max-w-[280px] font-mono text-xs"
            aria-label="対象スタッフ ID"
          />
          <Input
            value={patientIdFilter}
            onChange={(e) => setPatientIdFilter(e.target.value)}
            placeholder="対象患者 ID (UUID)"
            className="max-w-[280px] font-mono text-xs"
            aria-label="対象患者 ID"
          />
          <Input
            type="date"
            value={dateFilter}
            onChange={(e) => setDateFilter(e.target.value)}
            className="max-w-[160px]"
            aria-label="対象日"
          />
        </div>
      </Card>

      <Tabs
        value={tab}
        onValueChange={(v) => setTab(v as CategoryTab)}
        className="flex flex-1 flex-col"
      >
        <TabsList>
          <TabsTrigger value="staff">スタッフ予定 ({staffItems.length})</TabsTrigger>
          <TabsTrigger value="patient">患者関連 ({patientItems.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="staff" className="flex-1">
          <RequestList
            items={staffItems}
            isLoading={isLoading}
            isError={isError}
            error={error}
            emptyMessage="スタッフ予定の申請はありません"
            onApprove={handleApprove}
            onReject={openRejectDialog}
            actionPending={approve.isPending || reject.isPending}
            staffNameById={staffNameById}
            patientNameById={patientNameById}
          />
        </TabsContent>

        <TabsContent value="patient" className="flex-1">
          <RequestList
            items={patientItems}
            isLoading={isLoading}
            isError={isError}
            error={error}
            emptyMessage="患者関連の申請はありません"
            onApprove={handleApprove}
            onReject={openRejectDialog}
            actionPending={approve.isPending || reject.isPending}
            staffNameById={staffNameById}
            patientNameById={patientNameById}
          />
        </TabsContent>
      </Tabs>
        </div>

        <StaffLeavePanel
          onStaffChange={(id) => {
            setStaffIdFilter(id);
            if (id) setTab('staff');
          }}
        />
      </div>

      {/* Reject dialog — rejection_reason は必須 */}
      <Dialog
        open={rejectTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRejectTarget(null);
            setRejectReason('');
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>申請を却下</DialogTitle>
            <DialogDescription>
              却下理由を入力してください。理由は申請者にも表示されます。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <label htmlFor="reject-reason" className="text-sm font-medium text-text-primary">
              却下理由 <span className="text-error">*</span>
            </label>
            <Textarea
              id="reject-reason"
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="例: 同日に既に別予定があるため"
              rows={4}
              required
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setRejectTarget(null);
                setRejectReason('');
              }}
              disabled={reject.isPending}
            >
              キャンセル
            </Button>
            <Button
              variant="destructive"
              onClick={handleReject}
              disabled={reject.isPending || rejectReason.trim().length === 0}
            >
              {reject.isPending ? '却下中...' : '却下する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Request list table — shared between the two tabs
// ─────────────────────────────────────────────────────────────────────────

interface RequestListProps {
  items: PendingRequestV2Read[];
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  emptyMessage: string;
  onApprove: (row: PendingRequestV2Read) => void;
  onReject: (row: PendingRequestV2Read) => void;
  actionPending: boolean;
  staffNameById: Map<string, string>;
  patientNameById: Map<string, string>;
}

function RequestList({
  items,
  isLoading,
  isError,
  error,
  emptyMessage,
  onApprove,
  onReject,
  actionPending,
  staffNameById,
  patientNameById,
}: RequestListProps) {
  if (isLoading) {
    return (
      <Card className="h-full p-5">
        <div className="space-y-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-2/3" />
        </div>
      </Card>
    );
  }

  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>取得に失敗しました</AlertTitle>
        <AlertDescription>
          {error instanceof Error ? error.message : '不明なエラー'}
        </AlertDescription>
      </Alert>
    );
  }

  if (items.length === 0) {
    // h-full + 縦中央: 右パネルとの下端揃えでカードが伸びても空状態が浮かないように
    return (
      <Card className="flex h-full items-center justify-center p-5">
        <RakusukeNote pose="clap" size="sm" title={emptyMessage} comment="すべて対応済みです" />
      </Card>
    );
  }

  return (
    <Card className="h-full p-0">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-border-default text-left text-text-secondary">
            <tr>
              <th className="px-3 py-2 font-medium">申請日時</th>
              <th className="px-3 py-2 font-medium">種別</th>
              <th className="px-3 py-2 font-medium">対象スタッフ</th>
              <th className="px-3 py-2 font-medium">対象患者</th>
              <th className="px-3 py-2 font-medium">対象日</th>
              <th className="px-3 py-2 font-medium">ステータス</th>
              <th className="px-3 py-2 font-medium">内容</th>
              <th className="px-3 py-2 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <RequestRow
                key={row.id}
                row={row}
                onApprove={onApprove}
                onReject={onReject}
                actionPending={actionPending}
                staffNameById={staffNameById}
                patientNameById={patientNameById}
              />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Single request row
// ─────────────────────────────────────────────────────────────────────────

interface RequestRowProps {
  row: PendingRequestV2Read;
  onApprove: (row: PendingRequestV2Read) => void;
  onReject: (row: PendingRequestV2Read) => void;
  actionPending: boolean;
  staffNameById: Map<string, string>;
  patientNameById: Map<string, string>;
}

function RequestRow({
  row,
  onApprove,
  onReject,
  actionPending,
  staffNameById,
  patientNameById,
}: RequestRowProps) {
  // 「承認 / 却下」は pending のときだけ可能。approved / rejected
  // に対して再度 approve するとサーバが 409 を返す (冪等性ガード)。
  const isActionable = row.status === 'pending';
  const summary = summarizeRequest(row);
  const staffName = row.target_staff_id ? staffNameById.get(row.target_staff_id) : null;
  const patientName = row.target_patient_id ? patientNameById.get(row.target_patient_id) : null;

  return (
    <tr className="border-b border-border-default last:border-0 align-top hover:bg-bg-muted">
      <td className="px-3 py-2 tnum text-text-secondary whitespace-nowrap">
        {fmtDateTime(row.created_at)}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${REQUEST_TYPE_BADGE_CLASS[row.request_type]}`}
        >
          {REQUEST_TYPE_LABEL[row.request_type]}
        </span>
        {row.scope && (
          <p className="mt-0.5 text-[11px] text-text-muted">
            {SCOPE_LABEL[row.scope] ?? row.scope}
          </p>
        )}
      </td>
      <td className="px-3 py-2 whitespace-nowrap text-text-primary">
        {staffName ?? (
          <span className="font-mono text-xs text-text-muted">
            {row.target_staff_id ? row.target_staff_id.slice(0, 8) : '--'}
          </span>
        )}
      </td>
      <td className="px-3 py-2 whitespace-nowrap text-text-primary">
        {patientName ?? (
          <span className="font-mono text-xs text-text-muted">
            {row.target_patient_id ? row.target_patient_id.slice(0, 8) : '--'}
          </span>
        )}
      </td>
      <td className="px-3 py-2 tnum whitespace-nowrap font-medium text-text-primary">
        {row.target_date ? fmtTargetDate(row.target_date) : '--'}
      </td>
      <td className="px-3 py-2">
        <Badge variant={statusBadgeVariant(row.status)}>{STATUS_LABEL[row.status]}</Badge>
        {row.status === 'rejected' && row.rejection_reason && (
          <p className="mt-1 max-w-[220px] text-xs text-text-muted" title={row.rejection_reason}>
            理由: {row.rejection_reason}
          </p>
        )}
      </td>
      <td className="px-3 py-2 max-w-[260px]">
        {summary ? (
          <p className="truncate text-xs text-text-secondary" title={summary}>
            {summary}
          </p>
        ) : (
          <span className="text-xs text-text-muted">--</span>
        )}
      </td>
      <td className="px-3 py-2">
        {isActionable ? (
          <div className="flex flex-col gap-1">
            <Button size="sm" onClick={() => onApprove(row)} disabled={actionPending}>
              承認
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => onReject(row)}
              disabled={actionPending}
            >
              却下
            </Button>
          </div>
        ) : (
          <span className="text-xs text-text-muted">--</span>
        )}
      </td>
    </tr>
  );
}
