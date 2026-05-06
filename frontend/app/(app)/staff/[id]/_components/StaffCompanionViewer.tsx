'use client';

/**
 * StaffCompanionViewer — 同行スタッフ割付 閲覧専用セクション (W10-FE2 / §3.5.x / §4.2.x).
 *
 * - `is_trainee = true` のスタッフ詳細画面にのみ表示。
 * - GET /api/v1/staff/{trainee_staff_id}/companion-assignments から取得。
 * - W10-FE1 の useCompanionAssignments hook は並行作業のため import せず、
 *   独自の inline fetch で実装 (マージ時に統合)。
 *
 * 表示レイアウト:
 *   曜日 × 時間帯 (am=午前 / pm=午後 / full=終日) の表で companion 名 + シフト時刻を表示。
 *   companion_staff_name はエンドポイントが返すレスポンスに含まれる想定
 *   (W10-BE1 契約 §12.1)。
 */
import Link from 'next/link';
import { useEffect, useReducer } from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import { fetcher } from '@/lib/api/fetcher';
import { WEEKDAY_LABELS } from '@/lib/schemas/staff';

// ---------------------------------------------------------------------------
// Types (inline — W10-FE1 の統合 schema ができたら差し替え)
// ---------------------------------------------------------------------------

type CompanionPart = 'am' | 'pm' | 'full';

interface CompanionAssignmentRead {
  id: string;
  trainee_staff_id: string;
  weekday: number;
  part: CompanionPart;
  companion_staff_id: string;
  /** BE が結合して返す氏名 (nullable — 旧レコードの場合 null の可能性) */
  companion_staff_name?: string | null;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Fetch state machine (simple reducer — SWR / TanStack を使わず軽量実装)
// ---------------------------------------------------------------------------

type FetchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; data: CompanionAssignmentRead[] };

type FetchAction =
  | { type: 'load' }
  | { type: 'ok'; data: CompanionAssignmentRead[] }
  | { type: 'error'; message: string };

function fetchReducer(_state: FetchState, action: FetchAction): FetchState {
  switch (action.type) {
    case 'load':
      return { status: 'loading' };
    case 'ok':
      return { status: 'ok', data: action.data };
    case 'error':
      return { status: 'error', message: action.message };
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PART_LABEL: Record<CompanionPart, string> = {
  am: '午前',
  pm: '午後',
  full: '終日',
};

/** 曜日 × part の表示セルを構築する */
function buildGrid(assignments: CompanionAssignmentRead[]) {
  // weekday 0..6, part: am | pm | full の辞書
  const map = new Map<string, CompanionAssignmentRead>();
  for (const a of assignments) {
    map.set(`${a.weekday}-${a.part}`, a);
  }
  return map;
}

/** 集計: companion ごとの出現回数 (曜日×part 単位) */
function summarize(assignments: CompanionAssignmentRead[]): Array<{ name: string; count: number }> {
  const freq = new Map<string, number>();
  for (const a of assignments) {
    const name = a.companion_staff_name ?? a.companion_staff_id;
    freq.set(name, (freq.get(name) ?? 0) + 1);
  }
  return [...freq.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface StaffCompanionViewerProps {
  staffId: string;
  canEdit: boolean;
}

export function StaffCompanionViewer({ staffId, canEdit }: StaffCompanionViewerProps) {
  const { data: session } = useSession();
  const [state, dispatch] = useReducer(fetchReducer, { status: 'idle' });

  useEffect(() => {
    if (!staffId) return;
    let cancelled = false;
    dispatch({ type: 'load' });
    fetcher<CompanionAssignmentRead[]>(`/api/v1/staff/${staffId}/companion-assignments`, {
      accessToken: session?.accessToken ?? null,
      refreshToken: session?.refreshToken ?? null,
    })
      .then((data) => {
        if (!cancelled) dispatch({ type: 'ok', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          dispatch({
            type: 'error',
            message: err instanceof Error ? err.message : '取得に失敗しました',
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [staffId, session?.accessToken, session?.refreshToken]);

  if (state.status === 'idle' || state.status === 'loading') {
    return (
      <div className="space-y-2">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <Alert variant="destructive">
        <AlertTitle>取得に失敗しました</AlertTitle>
        <AlertDescription>{state.message}</AlertDescription>
      </Alert>
    );
  }

  const assignments = state.data;
  const grid = buildGrid(assignments);
  const summary = summarize(assignments);

  // 使用している part の種別を判定して表示列を決定
  const hasAm = assignments.some((a) => a.part === 'am');
  const hasPm = assignments.some((a) => a.part === 'pm');
  const hasFull = assignments.some((a) => a.part === 'full');
  // 少なくとも 1 つは存在するが空の場合は am / pm の 2 列を標準表示
  const parts: CompanionPart[] =
    hasAm || hasPm || !hasFull
      ? ['am', 'pm', ...(hasFull ? (['full'] as CompanionPart[]) : [])]
      : ['full'];

  return (
    <div className="space-y-3">
      {/* 曜日 × 時間帯テーブル */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-border-default text-left text-text-secondary">
            <tr>
              <th className="px-3 py-2 font-medium">曜日</th>
              {parts.map((part) => (
                <th key={part} className="px-3 py-2 font-medium">
                  {PART_LABEL[part]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {WEEKDAY_LABELS.map((label, weekday) => (
              <tr key={weekday} className="border-b border-border-default last:border-0">
                <td className="px-3 py-2 font-medium">{label}</td>
                {parts.map((part) => {
                  const a = grid.get(`${weekday}-${part}`);
                  return (
                    <td key={part} className="px-3 py-2 text-text-secondary">
                      {a ? (
                        <span className="text-text-primary">
                          {a.companion_staff_name ?? a.companion_staff_id}
                        </span>
                      ) : (
                        <span className="text-text-muted">—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 集計行 */}
      {summary.length > 0 && (
        <p className="text-xs text-text-muted">
          主な同行スタッフ:{' '}
          {summary
            .slice(0, 5)
            .map((s) => `${s.name} (${s.count})`)
            .join(' / ')}
        </p>
      )}

      {assignments.length === 0 && (
        <p className="text-sm text-text-muted">同行スタッフはまだ登録されていません</p>
      )}

      {/* 編集リンク (admin/manager のみ) */}
      {canEdit && (
        <div>
          <Link
            href={`/staff/${staffId}/edit#companion`}
            className="text-xs font-medium text-brand-primary underline-offset-2 hover:underline"
          >
            編集 →
          </Link>
        </div>
      )}
    </div>
  );
}
