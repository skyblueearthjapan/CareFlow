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
 *   4. 各申請に「承認 / 編集承認 / 却下」ボタン
 *   5. 却下時は理由入力ダイアログで必須化 (§3.5.4)
 *
 * 「編集承認」の編集 UI 自体は本チケットのスコープ外で、JSON テキストエリア
 * での簡易編集にとどめる。リッチな payload エディタは後続 W3-FE7 / W5-FE11
 * で各 request_type 別に拡張する想定。
 */

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Filter, RefreshCw } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
import {
  useApproveRequest,
  useApproveWithEdit,
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
]);

const PATIENT_REQUEST_TYPES = new Set<RequestType>([
  'patient_create',
  'patient_cancel',
  'patient_reschedule',
  'patient_special_week_on',
  'patient_special_week_off',
]);

const REQUEST_TYPE_LABEL: Record<RequestType, string> = {
  staff_off: 'スタッフ休み',
  staff_event: 'スタッフイベント',
  staff_mentor: 'メンター',
  staff_create: 'スタッフ新規',
  patient_create: '患者新規',
  patient_cancel: '患者キャンセル',
  patient_reschedule: '日時変更',
  patient_special_week_on: '特別週ON',
  patient_special_week_off: '特別週OFF',
};

const STATUS_LABEL: Record<RequestStatus, string> = {
  pending: '保留中',
  approved: '承認済み',
  rejected: '却下',
};

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
  const isAuthorized = role === 'admin' || role === 'manager';

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
  const [editTarget, setEditTarget] = useState<PendingRequestV2Read | null>(null);
  const [editJson, setEditJson] = useState('');
  const [editError, setEditError] = useState<string | null>(null);
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

  const approve = useApproveRequest();
  const approveWithEdit = useApproveWithEdit();
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

  const openEditDialog = (row: PendingRequestV2Read) => {
    setActionError(null);
    setEditError(null);
    setEditTarget(row);
    // 編集承認は payload を起点に編集する。既に edited_payload が
    // 入っていればそちらを優先する。
    const seed = row.edited_payload ?? row.payload ?? {};
    setEditJson(JSON.stringify(seed, null, 2));
  };

  const handleEditApprove = () => {
    if (!editTarget) return;
    let parsed: Record<string, unknown>;
    try {
      const obj: unknown = JSON.parse(editJson);
      if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
        throw new Error('payload は JSON オブジェクトである必要があります');
      }
      parsed = obj as Record<string, unknown>;
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'JSON の解析に失敗しました');
      return;
    }
    approveWithEdit.mutate(
      { id: editTarget.id, edited_payload: parsed },
      {
        onSuccess: () => {
          setEditTarget(null);
          setEditJson('');
          setEditError(null);
        },
        onError: (err) => setEditError(err.message),
      },
    );
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
        <div>
          <h1 className="font-serif text-2xl font-bold text-text-primary">申請履歴</h1>
          <p className="text-sm text-text-secondary">
            全 {total} 件 — モバイル / AI 経由の申請を管理者が承認・却下します
          </p>
        </div>
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

      <Tabs value={tab} onValueChange={(v) => setTab(v as CategoryTab)}>
        <TabsList>
          <TabsTrigger value="staff">スタッフ予定 ({staffItems.length})</TabsTrigger>
          <TabsTrigger value="patient">患者関連 ({patientItems.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="staff">
          <RequestList
            items={staffItems}
            isLoading={isLoading}
            isError={isError}
            error={error}
            emptyMessage="スタッフ予定の申請はありません"
            onApprove={handleApprove}
            onEdit={openEditDialog}
            onReject={openRejectDialog}
            actionPending={approve.isPending || approveWithEdit.isPending || reject.isPending}
          />
        </TabsContent>

        <TabsContent value="patient">
          <RequestList
            items={patientItems}
            isLoading={isLoading}
            isError={isError}
            error={error}
            emptyMessage="患者関連の申請はありません"
            onApprove={handleApprove}
            onEdit={openEditDialog}
            onReject={openRejectDialog}
            actionPending={approve.isPending || approveWithEdit.isPending || reject.isPending}
          />
        </TabsContent>
      </Tabs>

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

      {/* Edit-and-approve dialog — payload を JSON で編集 */}
      <Dialog
        open={editTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setEditTarget(null);
            setEditJson('');
            setEditError(null);
          }
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>編集して承認</DialogTitle>
            <DialogDescription>
              申請内容 (payload) を編集して承認します。JSON オブジェクト形式で入力してください。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <label htmlFor="edit-payload" className="text-sm font-medium text-text-primary">
              編集後 payload (JSON)
            </label>
            <Textarea
              id="edit-payload"
              value={editJson}
              onChange={(e) => setEditJson(e.target.value)}
              rows={10}
              className="font-mono text-xs"
            />
            {editError && (
              <p className="text-sm text-error" role="alert">
                {editError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setEditTarget(null);
                setEditJson('');
                setEditError(null);
              }}
              disabled={approveWithEdit.isPending}
            >
              キャンセル
            </Button>
            <Button onClick={handleEditApprove} disabled={approveWithEdit.isPending}>
              {approveWithEdit.isPending ? '承認中...' : '編集して承認'}
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
  onEdit: (row: PendingRequestV2Read) => void;
  onReject: (row: PendingRequestV2Read) => void;
  actionPending: boolean;
}

function RequestList({
  items,
  isLoading,
  isError,
  error,
  emptyMessage,
  onApprove,
  onEdit,
  onReject,
  actionPending,
}: RequestListProps) {
  if (isLoading) {
    return (
      <Card className="p-5">
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
    return (
      <Card className="p-5">
        <p className="text-sm text-text-muted">{emptyMessage}</p>
      </Card>
    );
  }

  return (
    <Card className="p-0">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-border-default text-left text-text-secondary">
            <tr>
              <th className="px-3 py-2 font-medium">申請日時</th>
              <th className="px-3 py-2 font-medium">種別</th>
              <th className="px-3 py-2 font-medium">対象スタッフ</th>
              <th className="px-3 py-2 font-medium">対象患者</th>
              <th className="px-3 py-2 font-medium">対象日</th>
              <th className="px-3 py-2 font-medium">スコープ</th>
              <th className="px-3 py-2 font-medium">ステータス</th>
              <th className="px-3 py-2 font-medium">payload</th>
              <th className="px-3 py-2 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <RequestRow
                key={row.id}
                row={row}
                onApprove={onApprove}
                onEdit={onEdit}
                onReject={onReject}
                actionPending={actionPending}
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
  onEdit: (row: PendingRequestV2Read) => void;
  onReject: (row: PendingRequestV2Read) => void;
  actionPending: boolean;
}

function RequestRow({ row, onApprove, onEdit, onReject, actionPending }: RequestRowProps) {
  // 「承認 / 編集承認 / 却下」は pending のときだけ可能。approved / rejected
  // に対して再度 approve するとサーバが 409 を返す (冪等性ガード)。
  const isActionable = row.status === 'pending';
  const payloadPreview = JSON.stringify(row.edited_payload ?? row.payload ?? {});

  return (
    <tr className="border-b border-border-default last:border-0 align-top hover:bg-bg-muted">
      <td className="px-3 py-2 tnum text-text-secondary whitespace-nowrap">{row.created_at}</td>
      <td className="px-3 py-2">
        <span className="inline-flex items-center rounded-full bg-bg-muted px-2 py-0.5 text-xs">
          {REQUEST_TYPE_LABEL[row.request_type]}
        </span>
      </td>
      <td className="px-3 py-2 font-mono text-xs text-text-secondary">
        {row.target_staff_id ? row.target_staff_id.slice(0, 8) : '--'}
      </td>
      <td className="px-3 py-2 font-mono text-xs text-text-secondary">
        {row.target_patient_id ? row.target_patient_id.slice(0, 8) : '--'}
      </td>
      <td className="px-3 py-2 tnum text-text-secondary whitespace-nowrap">
        {row.target_date ?? '--'}
      </td>
      <td className="px-3 py-2 text-text-secondary">{row.scope ?? '--'}</td>
      <td className="px-3 py-2">
        <Badge variant={statusBadgeVariant(row.status)}>{STATUS_LABEL[row.status]}</Badge>
        {row.status === 'rejected' && row.rejection_reason && (
          <p className="mt-1 max-w-[220px] text-xs text-text-muted" title={row.rejection_reason}>
            理由: {row.rejection_reason}
          </p>
        )}
      </td>
      <td className="px-3 py-2 max-w-[280px]">
        <pre className="whitespace-pre-wrap break-words rounded bg-bg-muted px-2 py-1 text-xs">
          {payloadPreview}
        </pre>
      </td>
      <td className="px-3 py-2">
        {isActionable ? (
          <div className="flex flex-col gap-1">
            <Button size="sm" onClick={() => onApprove(row)} disabled={actionPending}>
              承認
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onEdit(row)}
              disabled={actionPending}
            >
              編集承認
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
