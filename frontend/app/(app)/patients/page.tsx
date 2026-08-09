/**
 * Patient master list (Phase 3-1).
 *
 * Filters: ステータスタブ (稼働中/開始前/一時休止/入院中/解約済み/すべて・PO決定 2026-08-09),
 *          検索 (name/kana/code, 300ms debounce), 保険区分.
 * Pagination: limit=20, prev/next + page numbers.
 * Roles: 「+ 新規登録」 is admin/manager only.
 */
'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';
import { cn } from '@/lib/utils';
import { Plus } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { RakusukeNote, RakusukeTitle } from '@/components/brand/Rakusuke';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  INSURANCE_LABEL,
  INSURANCE_OPTIONS,
  SEX_LABEL,
  STATUS_LABEL,
  normalizePatientInsurance,
  normalizePatientSex,
  normalizePatientStatus,
} from '@/lib/schemas/patient';
import { usePatients } from '@/lib/queries/patients';
import { useOffices } from '@/lib/queries/offices';
import { PatientsExcelButtons } from '@/components/patients/PatientsExcelButtons';
import { PatientKarteButtons } from '@/components/patients/PatientKarteButtons';
import { PatientsReplaceAllButton } from '@/components/patients/PatientsReplaceAllButton';

const PAGE_SIZE = 20;

/**
 * ステータスタブ (PO 決定 2026-08-09)。並びはライフサイクル順で「稼働中」先頭・既定。
 * ラベルは一覧の「状態」列 (STATUS_LABEL) と同一 — 表記の揺れを作らない。
 */
const STATUS_TABS = [
  { value: 'active', label: STATUS_LABEL.active },
  { value: 'pending', label: STATUS_LABEL.pending },
  { value: 'suspended', label: STATUS_LABEL.suspended },
  { value: 'admitted', label: STATUS_LABEL.admitted },
  { value: 'cancelled', label: STATUS_LABEL.cancelled },
  { value: 'all', label: 'すべて' },
] as const;
type StatusTabValue = (typeof STATUS_TABS)[number]['value'];

function initialStatusTab(): StatusTabValue {
  if (typeof window === 'undefined') return 'active';
  const v = new URLSearchParams(window.location.search).get('status');
  return STATUS_TABS.some((t) => t.value === v) ? (v as StatusTabValue) : 'active';
}

export default function PatientsPage() {
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canCreate = role === 'admin' || role === 'manager';
  const isAdmin = role === 'admin';

  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [insurance, setInsurance] = useState<string>('');
  const [statusTab, setStatusTab] = useState<StatusTabValue>(initialStatusTab);
  const [page, setPage] = useState(1);

  // タブを URL (?status=) に同期 — ブックマーク・戻る・他画面からのリンクに対応。
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (statusTab === 'active') params.delete('status');
    else params.set('status', statusTab);
    const qs = params.toString();
    window.history.replaceState(null, '', window.location.pathname + (qs ? `?${qs}` : ''));
  }, [statusTab]);

  // 300ms debounce for the search box.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  const { data, isLoading, isError, error } = usePatients({
    page,
    limit: PAGE_SIZE,
    search,
    insurance: insurance || undefined,
    status: statusTab,
  });

  // 拠点 id → 名前マップ (一覧で primary_office_id を拠点名表示するため)
  const { offices } = useOffices({ limit: 500 });
  const officeNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const o of offices ?? []) {
      m.set(o.id, o.name);
    }
    return m;
  }, [offices]);

  // 削除済みは BE が返さない (deleted_at IS NULL 固定) ため、旧「有効のみ」
  // チェックは実質無効だった — ステータスタブ導入に合わせて撤去 (2026-08-09)。
  const items = data?.items ?? [];
  const statusCounts = data?.statusCounts;

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <RakusukeTitle
          pose="heart"
          title="患者マスタ"
          subtitle={<>{total > 0 ? `全 ${total} 件` : '登録済み患者を一覧表示します'}</>}
        />
        <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
          {/* RB (PO決定 2026-07-08): 全ロール同一表示。staff には無効化して見せる
              (全患者置換のみ admin 限定のセンシティブ機能として非表示を維持)。 */}
          <div
            className={cn('flex items-center gap-2', !canCreate && 'opacity-50')}
            aria-disabled={!canCreate}
            inert={!canCreate || undefined}
          >
            <span className="text-xs font-medium text-text-muted">一括 (全患者)</span>
            <PatientsExcelButtons />
            {isAdmin ? <PatientsReplaceAllButton /> : null}
          </div>
          <div
            className={cn(
              'flex items-center gap-2 border-l border-border-default pl-2',
              !canCreate && 'opacity-50',
            )}
            aria-disabled={!canCreate}
            inert={!canCreate || undefined}
          >
            <span className="text-xs font-medium text-text-muted">個別カルテ (1 患者)</span>
            <PatientKarteButtons />
          </div>
          {canCreate ? (
            <Button asChild variant="outline">
              <Link href="/patients/qr-print?mode=bulk">QR一括印刷</Link>
            </Button>
          ) : (
            <Button variant="outline" disabled>
              QR一括印刷
            </Button>
          )}
          {canCreate ? (
            <Button asChild>
              <Link href="/patients/new">
                <Plus className="h-4 w-4" />
                新規登録
              </Link>
            </Button>
          ) : (
            <Button disabled>
              <Plus className="h-4 w-4" />
              新規登録
            </Button>
          )}
        </div>
      </header>

      {/* ステータスタブ (PO 決定 2026-08-09): 稼働中を先頭・既定。件数バッジ付き。 */}
      <div
        role="tablist"
        aria-label="ステータスで絞り込み"
        className="flex flex-wrap gap-1.5"
        data-testid="patient-status-tabs"
      >
        {STATUS_TABS.map((t) => {
          const selected = statusTab === t.value;
          const count = statusCounts?.[t.value];
          return (
            <button
              key={t.value}
              type="button"
              role="tab"
              aria-selected={selected}
              data-testid={`patient-status-tab-${t.value}`}
              onClick={() => {
                setStatusTab(t.value);
                setPage(1);
              }}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition-colors',
                selected
                  ? 'border-brand-primary bg-brand-primary text-white shadow-sm'
                  : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted',
              )}
            >
              {t.label}
              {count != null && (
                <span
                  className={cn(
                    'tnum rounded-full px-1.5 text-xs',
                    selected ? 'bg-white/25 text-white' : 'bg-bg-muted text-text-muted',
                  )}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <Card className="p-4">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_220px]">
          <Input
            type="search"
            placeholder="氏名・カナ・コードで検索"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
          <select
            value={insurance}
            onChange={(e) => {
              setInsurance(e.target.value);
              setPage(1);
            }}
            className="flex h-10 w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light"
          >
            <option value="">保険区分: すべて</option>
            {INSURANCE_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {INSURANCE_LABEL[v]}
              </option>
            ))}
          </select>
        </div>
      </Card>

      {data?.truncated ? (
        <Alert variant="warning">
          <AlertTitle>結果が打ち切られました</AlertTitle>
          <AlertDescription>
            患者数が表示上限 (500 件) を超えています。検索条件で絞ってください。
          </AlertDescription>
        </Alert>
      ) : null}

      <Card className="p-5">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-3/4" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : items.length === 0 ? (
          statusTab === 'all' ? (
            <RakusukeNote
              pose="heart"
              title="患者が登録されていません"
              comment="「新規登録」から最初の患者さんを迎えましょう"
            />
          ) : (
            <RakusukeNote
              pose="heart"
              title={`${STATUS_TABS.find((t) => t.value === statusTab)?.label ?? ''}の患者様はいません`}
              comment="他のタブや「すべて」で確認できます"
            />
          )
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">コード</th>
                  <th className="px-3 py-2 font-medium">氏名</th>
                  <th className="px-3 py-2 font-medium">カナ</th>
                  <th className="px-3 py-2 font-medium">性別</th>
                  <th className="px-3 py-2 font-medium">保険</th>
                  <th className="px-3 py-2 font-medium">主担当拠点</th>
                  <th className="px-3 py-2 font-medium">状態</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => {
                  const sexNorm = normalizePatientSex(p.sex as string | null | undefined);
                  const insuranceNorm = normalizePatientInsurance(
                    p.insurance as string | null | undefined,
                  );
                  const statusNorm = normalizePatientStatus(p.status as string | null | undefined);
                  return (
                    <tr
                      key={p.id}
                      className="border-b border-border-default last:border-0 hover:bg-bg-muted/30"
                    >
                      <td className="px-3 py-2 tnum">{p.code}</td>
                      <td className="px-3 py-2 font-medium text-text-primary">{p.name}</td>
                      <td className="px-3 py-2 text-text-secondary">{p.kana ?? '--'}</td>
                      <td className="px-3 py-2 text-text-secondary">
                        {sexNorm ? SEX_LABEL[sexNorm] : '--'}
                      </td>
                      <td className="px-3 py-2 text-text-secondary">
                        {insuranceNorm ? INSURANCE_LABEL[insuranceNorm] : '--'}
                      </td>
                      <td className="px-3 py-2 text-text-secondary">
                        {p.primary_office_id
                          ? (officeNameMap.get(p.primary_office_id) ?? '--')
                          : '--'}
                      </td>
                      <td className="px-3 py-2 text-text-secondary">
                        {p.deleted_at ? '削除済' : STATUS_LABEL[statusNorm]}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex gap-2">
                          <Link
                            href={`/patients/${p.id}`}
                            className="text-brand-primary hover:underline"
                          >
                            詳細
                          </Link>
                          {canCreate ? (
                            <Link
                              href={`/patients/${p.id}/edit`}
                              className="text-brand-primary hover:underline"
                            >
                              編集
                            </Link>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {totalPages > 1 ? (
        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      ) : null}
    </section>
  );
}

interface PaginationProps {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}

function Pagination({ page, totalPages, onChange }: PaginationProps) {
  // Compact page-number window: max 7 buttons.
  const pages = buildPageWindow(page, totalPages);
  return (
    <nav className="flex items-center justify-center gap-1" aria-label="ページネーション">
      <Button
        variant="outline"
        size="sm"
        onClick={() => onChange(Math.max(1, page - 1))}
        disabled={page <= 1}
      >
        前へ
      </Button>
      {pages.map((p, idx) =>
        p === '…' ? (
          <span key={`gap-${idx}`} className="px-2 text-text-muted">
            …
          </span>
        ) : (
          <Button
            key={p}
            variant={p === page ? 'default' : 'outline'}
            size="sm"
            onClick={() => onChange(p)}
          >
            {p}
          </Button>
        ),
      )}
      <Button
        variant="outline"
        size="sm"
        onClick={() => onChange(Math.min(totalPages, page + 1))}
        disabled={page >= totalPages}
      >
        次へ
      </Button>
    </nav>
  );
}

function buildPageWindow(page: number, totalPages: number): Array<number | '…'> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const out: Array<number | '…'> = [1];
  const start = Math.max(2, page - 1);
  const end = Math.min(totalPages - 1, page + 1);
  if (start > 2) out.push('…');
  for (let p = start; p <= end; p++) out.push(p);
  if (end < totalPages - 1) out.push('…');
  out.push(totalPages);
  return out;
}
