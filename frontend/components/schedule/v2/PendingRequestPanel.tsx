'use client';

/**
 * PendingRequestPanel — スケジュール画面の保留申請パネル (W3-FE7).
 *
 * 設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.5.5「履歴 UI」/
 * 実装手順書 v0.2 §4 W3-FE7 に準拠。
 *
 * 役割:
 *   スケジュール画面 (`/schedule`) の上端に常駐し、未処理の申請
 *   (status = 'pending') を上位 N 件だけサマリ表示する。管理者が
 *   `/admin/pending-requests` まで遷移しなくても、その場で承認できる
 *   「クイック承認」を提供する (§3.5.5)。
 *
 * 詳細編集 (編集承認 / 却下理由付き却下) や絞り込みは本パネルでは行わない。
 * 「すべて見る」「詳細」リンクから `/admin/pending-requests` へ送る。
 *
 * RBAC:
 *   admin / manager のみ表示。staff には何も render しない (Backend も
 *   approve エンドポイントを 403 で守るが、UI 側でも完全に出さない)。
 *   pending_requests の GET 自体は staff も叩けるが、本パネルは「クイック
 *   承認」が主目的なので staff には不要 (§3.5.3)。
 *
 * ファイル所有: 本ファイルは W3-FE7 所有。所有外 (ScheduleGridV2.tsx など)
 * のファイルは編集しない。親 (page.tsx) が `pendingPanelSlot` 経由で
 * ScheduleGridV2 にこのコンポーネントを差し込む。
 */
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { Bell, Check, ExternalLink, Loader2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useApproveRequest, usePendingRequests } from '@/lib/queries/pending_requests';
import { usePatients } from '@/lib/queries/patients';
import { useStaffList } from '@/lib/queries/staff';
import { cn } from '@/lib/utils';
import type { PendingRequestV2Read, RequestType } from '@/lib/schemas/pending_request';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

/** 既定の最大表示件数. props で上書き可能 (default 5). */
const DEFAULT_MAX_VISIBLE = 5;

/**
 * RequestType の日本語ラベル. /admin/pending-requests と揃えてあるが、
 * 本パネル独自にコンパクトな文言 (例: 「日時変更」を「予定変更」など) に
 * 変える可能性があるため、共有はせず複製する。
 */
const REQUEST_TYPE_LABEL: Record<RequestType, string> = {
  staff_off: 'スタッフ休み',
  staff_event: 'スタッフ予定',
  staff_mentor: '同行スタッフ',
  staff_create: 'スタッフ新規',
  staff_status_update: 'スタッフ状態変更',
  patient_create: '患者新規',
  patient_cancel: '患者キャンセル',
  patient_reschedule: '日時変更',
  patient_special_week_on: '特別週ON',
  patient_special_week_off: '特別週OFF',
  patient_status_update: '患者状態変更',
};

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

/** ISO 文字列を「たった今 / N 分前 / N 時間前 / N 日前 / YYYY-MM-DD」に整形. */
function formatRelative(iso: string): string {
  const now = Date.now();
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso.slice(0, 16);
  const diffSec = Math.max(0, Math.floor((now - t) / 1000));
  if (diffSec < 60) return 'たった今';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}分前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}時間前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 30) return `${diffDay}日前`;
  return iso.slice(0, 10);
}

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface PendingRequestPanelProps {
  className?: string;
  /** 一覧に出す最大件数. 既定 5. */
  maxVisible?: number;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function PendingRequestPanel({
  className,
  maxVisible = DEFAULT_MAX_VISIBLE,
}: PendingRequestPanelProps) {
  const { data: session, status: sessionStatus } = useSession();
  const role = session?.user?.role;
  const isAuthorized = role === 'admin' || role === 'manager';

  // RBAC: admin/manager 以外には何も描画しない。
  // 認証中もしくは staff の場合は null を返す (= layout 側で空白)。
  const enabled = sessionStatus === 'authenticated' && isAuthorized;

  // pending 状態だけを引く. limit は表示数より少し多めに取って、
  // バッジ件数の表示に耐えうるサイズにする (本パネルは概要表示なので
  // 厳密な件数が必要な場合は /admin/pending-requests に飛ばす想定).
  // Hooks は条件付き呼び出しできないため `enabled` で抑制する.
  const requestsQuery = usePendingRequests({
    status: 'pending',
    limit: 100,
    offset: 0,
  });

  const approve = useApproveRequest();

  // 承認失敗時の最後のエラーメッセージ (1 つだけ表示).
  const [actionError, setActionError] = useState<string | null>(null);

  // 名前解決用の補助クエリ. 一覧自体は 100 件想定で軽いが、
  // 患者 / スタッフ ID → 名前のマップを作るには別クエリが必要.
  // すでに他画面で同じキーで cache 済みのはずなので追加コストは小さい.
  const patientsQuery = usePatients({ limit: 500 });
  const staffQuery = useStaffList({ limit: 500 });

  const patientNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of patientsQuery.data?.items ?? []) {
      m.set(p.id, p.name ?? p.id);
    }
    return m;
  }, [patientsQuery.data]);

  const staffNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of staffQuery.data ?? []) {
      m.set(s.id, s.name ?? s.id);
    }
    return m;
  }, [staffQuery.data]);

  // RBAC で弾く場合は早期 return (Hooks の順序は維持済み).
  if (!enabled) {
    return null;
  }

  const items = requestsQuery.data?.items ?? [];
  const total = items.length;
  const visible = items.slice(0, Math.max(0, maxVisible));
  const hasMore = total > visible.length;

  // ── 承認ハンドラ ────────────────────────────────────────────────────────
  const handleApprove = (row: PendingRequestV2Read) => {
    setActionError(null);
    approve.mutate(row.id, {
      onError: (err) => setActionError(err.message),
    });
  };

  // ── レンダリング ────────────────────────────────────────────────────────
  if (requestsQuery.isLoading) {
    return (
      <Card className={cn('p-3', className)}>
        <div className="mb-2 flex items-center gap-2">
          <Bell className="h-4 w-4 text-text-muted" aria-hidden />
          <span className="text-sm font-semibold text-text-primary">保留中の申請</span>
        </div>
        <div className="space-y-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-2/3" />
        </div>
      </Card>
    );
  }

  if (requestsQuery.isError) {
    return (
      <Alert variant="destructive" className={className}>
        <AlertTitle>保留申請の取得に失敗しました</AlertTitle>
        <AlertDescription>
          {requestsQuery.error instanceof Error ? requestsQuery.error.message : '不明なエラー'}
        </AlertDescription>
      </Alert>
    );
  }

  if (total === 0) {
    return (
      <Card className={cn('p-3', className)}>
        <div className="flex items-center gap-2">
          <Bell className="h-4 w-4 text-text-muted" aria-hidden />
          <span className="text-sm text-text-muted">保留中の申請はありません</span>
        </div>
      </Card>
    );
  }

  return (
    <Card className={cn('p-3', className)} aria-label="保留中の申請">
      <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Bell className="h-4 w-4 text-warning" aria-hidden />
          <span className="text-sm font-semibold text-text-primary">保留中の申請</span>
          <Badge variant="warning" className="tnum">
            {total}件
          </Badge>
        </div>
        <Link
          href="/admin/pending-requests"
          className="inline-flex items-center gap-1 text-xs font-medium text-brand-primary hover:underline"
        >
          すべて見る
          <ExternalLink className="h-3 w-3" aria-hidden />
        </Link>
      </header>

      {actionError && (
        <Alert variant="destructive" className="mb-2">
          <AlertTitle>承認に失敗しました</AlertTitle>
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}

      <ul className="divide-y divide-border-default/60">
        {visible.map((row) => (
          <PendingRequestRow
            key={row.id}
            row={row}
            patientNameMap={patientNameMap}
            staffNameMap={staffNameMap}
            onApprove={handleApprove}
            approving={approve.isPending}
          />
        ))}
      </ul>

      {hasMore && (
        <div className="mt-2 text-right">
          <Link
            href="/admin/pending-requests"
            className="text-xs font-medium text-brand-primary hover:underline"
          >
            残り {total - visible.length} 件をもっと見る →
          </Link>
        </div>
      )}
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Row component
// ─────────────────────────────────────────────────────────────────────────

interface PendingRequestRowProps {
  row: PendingRequestV2Read;
  patientNameMap: Map<string, string>;
  staffNameMap: Map<string, string>;
  onApprove: (row: PendingRequestV2Read) => void;
  approving: boolean;
}

function PendingRequestRow({
  row,
  patientNameMap,
  staffNameMap,
  onApprove,
  approving,
}: PendingRequestRowProps) {
  // 対象表示: 患者優先、無ければスタッフ. どちらも無ければ「--」.
  const patientName = row.target_patient_id
    ? (patientNameMap.get(row.target_patient_id) ?? row.target_patient_id.slice(0, 8))
    : null;
  const staffName = row.target_staff_id
    ? (staffNameMap.get(row.target_staff_id) ?? row.target_staff_id.slice(0, 8))
    : null;

  const targetLabel = patientName ?? staffName ?? '--';
  const targetKind = patientName ? '患者' : staffName ? 'スタッフ' : null;

  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center rounded-full bg-bg-muted px-2 py-0.5 text-[11px] font-medium text-text-secondary">
            {REQUEST_TYPE_LABEL[row.request_type]}
          </span>
          <span className="truncate text-sm font-medium text-text-primary">
            {targetKind ? `${targetKind}: ${targetLabel}` : targetLabel}
          </span>
          {row.target_date && (
            <span className="tnum text-xs text-text-muted">{row.target_date}</span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-text-muted">申請: {formatRelative(row.created_at)}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          size="sm"
          onClick={() => onApprove(row)}
          disabled={approving}
          aria-label={`${REQUEST_TYPE_LABEL[row.request_type]} の申請を承認`}
        >
          {approving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Check className="h-3.5 w-3.5" aria-hidden />
          )}
          承認
        </Button>
        <Button asChild size="sm" variant="outline">
          <Link href="/admin/pending-requests">詳細</Link>
        </Button>
      </div>
    </li>
  );
}
