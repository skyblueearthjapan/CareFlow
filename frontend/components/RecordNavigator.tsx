/**
 * RecordNavigator — 詳細/編集ページ間の前後ナビゲーション + 検索窓.
 *
 * 一覧画面に戻らずに別レコードへ切り替えられる UX を提供する。
 *   - 「← 前: {name}」「次: {name} →」: code 昇順で隣接レコードへ遷移
 *     (最端では disabled + tooltip)
 *   - 検索窓: 名前 / code / カナ を NFKC + lower で部分一致、最大 8 件 dropdown
 *     - Esc で閉じる / Enter で 1 件目に遷移 / 外側クリックで閉じる
 *
 * code 順序: code 文字列の自然順 (P001 < P002 ...) で昇順。`code === null` は末尾。
 * 患者は code 必須 (string)、スタッフは code nullable のため `NavigatorRecord.code`
 * は `string | null` 型を許容する。
 *
 * モバイル対応: ボタンのテキストは sm 以上で表示し、それ未満ではアイコンのみ。
 */
'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

export interface NavigatorRecord {
  id: string;
  code: string | null;
  name: string;
  kana?: string | null;
}

export interface RecordNavigatorProps {
  /** 現在表示中のレコード id */
  currentId: string;
  /** 全レコード (id, code, name, kana) */
  records: NavigatorRecord[];
  /** ナビゲート先 URL のテンプレート (例: '/patients/{id}' or '/patients/{id}/edit') */
  hrefTemplate: string;
  /** 表示用ラベル ('患者' or 'スタッフ') */
  entityLabel: '患者' | 'スタッフ';
  /** ナビゲート前に呼ばれる確認 callback. false を返したらキャンセル. */
  onBeforeNavigate?: () => boolean | Promise<boolean>;
  /** true のとき表示件数が上限を超えている旨の注意書きを表示する */
  truncated?: boolean;
}

/**
 * code 昇順比較。null は最後尾。
 * 自然順 (P001 < P002 < P010) を維持するため localeCompare の numeric オプションを使う。
 * code が両方 null の場合は name で二次ソートし安定性を保つ。
 */
function compareByCode(a: NavigatorRecord, b: NavigatorRecord): number {
  const ac = a.code;
  const bc = b.code;
  if (ac === null && bc === null) {
    return a.name.localeCompare(b.name, 'ja');
  }
  if (ac === null) return 1;
  if (bc === null) return -1;
  return ac.localeCompare(bc, 'ja', { numeric: true, sensitivity: 'base' });
}

function normalize(value: string | null | undefined): string {
  if (!value) return '';
  return value.normalize('NFKC').toLowerCase();
}

export function RecordNavigator({
  currentId,
  records,
  hrefTemplate,
  entityLabel,
  onBeforeNavigate,
  truncated,
}: RecordNavigatorProps) {
  const router = useRouter();

  const sorted = React.useMemo(() => [...records].sort(compareByCode), [records]);

  const currentIndex = React.useMemo(
    () => sorted.findIndex((r) => r.id === currentId),
    [sorted, currentId],
  );

  const prev = currentIndex > 0 ? sorted[currentIndex - 1] : null;
  const next =
    currentIndex >= 0 && currentIndex < sorted.length - 1 ? sorted[currentIndex + 1] : null;

  const navigate = React.useCallback(
    async (id: string) => {
      if (onBeforeNavigate) {
        const ok = await onBeforeNavigate();
        if (!ok) return;
      }
      router.push(hrefTemplate.replace('{id}', id));
    },
    [router, hrefTemplate, onBeforeNavigate],
  );

  // -------------------------------------------------------------------------
  // Search dropdown
  // -------------------------------------------------------------------------
  const [query, setQuery] = React.useState('');
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);

  const filtered = React.useMemo(() => {
    if (!query) return [];
    const q = normalize(query);
    return sorted
      .filter((r) => {
        if (r.name && normalize(r.name).includes(q)) return true;
        if (r.code && normalize(r.code).includes(q)) return true;
        if (r.kana && normalize(r.kana).includes(q)) return true;
        return false;
      })
      .slice(0, 8);
  }, [sorted, query]);

  // 外側クリックで閉じる
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const first = filtered[0];
      if (first) {
        setOpen(false);
        setQuery('');
        void navigate(first.id);
      }
    }
  };

  const handleSelect = (id: string) => {
    setOpen(false);
    setQuery('');
    void navigate(id);
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* 前へ */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!prev}
        onClick={() => {
          if (prev) void navigate(prev.id);
        }}
        title={prev ? `前へ: ${prev.name}` : `最初の${entityLabel}です`}
        aria-label={prev ? `前の${entityLabel}: ${prev.name}` : `最初の${entityLabel}です`}
        data-testid="record-nav-prev"
      >
        <ChevronLeft className="h-4 w-4" />
        <span className="hidden sm:inline">
          {prev ? `前: ${prev.name}` : `最初の${entityLabel}`}
        </span>
      </Button>

      {/* 検索窓 + dropdown */}
      <div ref={containerRef} className="relative">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
            aria-hidden="true"
          />
          <Input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => {
              if (query) setOpen(true);
            }}
            onKeyDown={handleKeyDown}
            placeholder={`${entityLabel}を検索`}
            className="h-9 w-44 pl-7 sm:w-56"
            aria-label={`${entityLabel}を検索`}
            data-testid="record-nav-search"
          />
        </div>
        {open && filtered.length > 0 && (
          <ul
            role="listbox"
            className={cn(
              'absolute left-0 right-0 top-full z-50 mt-1 max-h-80 overflow-y-auto rounded-md border border-border-default bg-bg-base shadow-md',
            )}
            data-testid="record-nav-search-dropdown"
          >
            {filtered.map((r) => (
              <li key={r.id} role="option" aria-selected={r.id === currentId}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-bg-muted focus:bg-bg-muted focus:outline-none"
                  onClick={() => handleSelect(r.id)}
                >
                  <span className="truncate text-text-primary">{r.name}</span>
                  <span className="shrink-0 text-xs text-text-muted tnum">{r.code ?? '--'}</span>
                </button>
              </li>
            ))}
            {truncated ? (
              <div className="px-2 py-1 text-[10px] text-amber-700 border-t border-amber-200 bg-amber-50">
                ⚠️ 表示件数が上限 (500) を超えています。一部の{entityLabel}が一覧に含まれません。
              </div>
            ) : null}
          </ul>
        )}
        {open && query && filtered.length === 0 && (
          <div
            className="absolute left-0 right-0 top-full z-50 mt-1 rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-muted shadow-md"
            data-testid="record-nav-search-empty"
          >
            該当する{entityLabel}はありません
          </div>
        )}
      </div>

      {/* 次へ */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!next}
        onClick={() => {
          if (next) void navigate(next.id);
        }}
        title={next ? `次へ: ${next.name}` : `最後の${entityLabel}です`}
        aria-label={next ? `次の${entityLabel}: ${next.name}` : `最後の${entityLabel}です`}
        data-testid="record-nav-next"
      >
        <span className="hidden sm:inline">
          {next ? `次: ${next.name}` : `最後の${entityLabel}`}
        </span>
        <ChevronRight className="h-4 w-4" />
      </Button>
    </div>
  );
}
