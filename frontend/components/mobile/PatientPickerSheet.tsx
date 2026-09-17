'use client';

/**
 * 患者を選ぶパネル（設計 §2-1 導線 C ②③・モック ④）。
 *
 * 「この記録はどなたの訪問ですか？」の中身。上から
 *   ① 今日/今週の担当患者チップ（`recentPatientIds` の順を保つ）
 *   ② 氏名 / カナ / 患者コードの検索（300ms デバウンス）
 *   ③ あいうえお順リスト（`compareByKana` ＋ 行見出し）
 * の順に置く。**選ぶだけ**で、選んだ後に何をするか（PATCH・遷移）は呼び出し側。
 *
 * ダイアログを内蔵しない素のパネルにしてある。モバイルはページ内のステップとして、
 * `/m/today` の「要紐付け」はダイアログの中身として、PC は将来の訪問記録ページで、
 * それぞれ違う器に載るため。
 *
 * **非稼働（入院中等）の患者も選べる**（設計 §10-3・2026-09-18 決定）。録音は予定
 * ではなく事実の記録なので、入院中の患者宅で録った音声を紐付けられないほうが困る。
 * ただし取り違えを避けるため既定では隠し、「非稼働も表示」で出す。
 */

import { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';

import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { compareByKana } from '@/lib/kana-sort';
import { usePatients } from '@/lib/queries/patients';
import {
  isSchedulableStatus,
  normalizePatientStatus,
  STATUS_LABEL,
  type PatientRead,
} from '@/lib/schemas/patient';
import { cn } from '@/lib/utils';

/** 検索の反応待ち（打っている最中に一覧を作り直さない）。 */
const SEARCH_DEBOUNCE_MS = 300;

/** あいうえお順の行見出し（カタカナの先頭文字 → 行）。 */
const KANA_ROWS: ReadonlyArray<{ label: string; chars: string }> = [
  { label: 'あ', chars: 'アイウエオァィゥェォヴ' },
  { label: 'か', chars: 'カキクケコガギグゲゴヵヶ' },
  { label: 'さ', chars: 'サシスセソザジズゼゾ' },
  { label: 'た', chars: 'タチツテトダヂヅデドッ' },
  { label: 'な', chars: 'ナニヌネノ' },
  { label: 'は', chars: 'ハヒフヘホバビブベボパピプペポ' },
  { label: 'ま', chars: 'マミムメモ' },
  { label: 'や', chars: 'ヤユヨャュョ' },
  { label: 'ら', chars: 'ラリルレロ' },
  { label: 'わ', chars: 'ワヲンヮ' },
];

const OTHER_ROW = 'その他';

/** ひらがな 1 文字をカタカナへ（かなの表記ゆれで行が割れないように）。 */
function toKatakana(ch: string): string {
  const code = ch.codePointAt(0) ?? 0;
  if (code >= 0x3041 && code <= 0x3096) return String.fromCodePoint(code + 0x60);
  return ch;
}

/** かな → 行見出し（かな未設定・英数字は「その他」）。 */
export function kanaRowLabel(kana: string | null | undefined): string {
  const head = toKatakana((kana ?? '').trim().charAt(0));
  if (!head) return OTHER_ROW;
  const row = KANA_ROWS.find((r) => r.chars.includes(head));
  return row ? row.label : OTHER_ROW;
}

/** `usePatients` と同じ突合（氏名 / カナ / 患者コードの部分一致）。 */
function matches(p: PatientRead, needle: string): boolean {
  if (!needle) return true;
  return `${p.name ?? ''} ${p.kana ?? ''} ${p.code ?? ''}`.toLowerCase().includes(needle);
}

export interface PatientPickerSheetProps {
  /** 患者を選んだ。PATCH・遷移・トーストは呼び出し側の責務。 */
  onPick: (patient: PatientRead) => void;
  /**
   * 上部にチップで出す患者（今日/今週の自分の担当など）。**渡された順**に出す
   * ので、並び（名前順）は呼び出し側が決める。非稼働でもチップには出す
   * — 自分の予定に居る患者を隠す理由は無い。
   */
  recentPatientIds?: string[];
  /** 既定で非稼働（入院中等）も出すか。トグルの初期値。 */
  includeInactive?: boolean;
  /** 保存中など、選択を受け付けたくないとき（二重 PATCH 防止）。 */
  disabled?: boolean;
}

export function PatientPickerSheet({
  onPick,
  recentPatientIds,
  includeInactive = false,
  disabled = false,
}: PatientPickerSheetProps) {
  const [query, setQuery] = useState('');
  const [needle, setNeedle] = useState('');
  const [showInactive, setShowInactive] = useState(includeInactive);

  // 300ms 打ち終わってから絞り込む。
  useEffect(() => {
    const id = window.setTimeout(() => setNeedle(query.trim().toLowerCase()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [query]);

  /**
   * 患者マスタは**1 回だけ**引いて、検索・非稼働トグルは手元で掛ける。
   *
   * `usePatients` は `search` / `status` を queryKey に含むため、渡すと 1 打鍵
   * ごとに 500 件の取得が走る（現場の電波では待ち時間になる）。取得したい集合は
   * 常に同じなので、絞り込みだけこちらで持つ。
   */
  const { data, isLoading, isError } = usePatients({ page: 1, limit: 500, sort: 'kana' });
  const all = useMemo(() => data?.items ?? [], [data]);

  const chips = useMemo(() => {
    const ids = recentPatientIds ?? [];
    if (ids.length === 0) return [];
    const byId = new Map(all.map((p) => [p.id, p]));
    return ids.map((id) => byId.get(id)).filter((p): p is PatientRead => !!p);
  }, [all, recentPatientIds]);

  const rows = useMemo(() => {
    const filtered = all.filter((p) => {
      if (!showInactive && !isSchedulableStatus(p.status)) return false;
      return matches(p, needle);
    });
    return [...filtered].sort(compareByKana);
  }, [all, needle, showInactive]);

  return (
    <div className="space-y-3" data-testid="patient-picker">
      {chips.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-bold text-text-secondary">今日の担当</p>
          <div className="flex flex-wrap gap-1.5">
            {chips.map((p) => (
              <button
                key={p.id}
                type="button"
                disabled={disabled}
                onClick={() => onPick(p)}
                data-testid={`patient-chip-${p.id}`}
                className="min-h-9 rounded-full border border-brand-primary bg-bg-base px-3.5 py-1.5 text-sm font-bold text-brand-primary disabled:opacity-50"
              >
                {p.name}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-1.5">
        <label
          className="block text-xs font-bold text-text-secondary"
          htmlFor="patient-picker-search"
        >
          氏名 / カナ / 患者コード で検索
        </label>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <Input
            id="patient-picker-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="例: やまだ / P-0042"
            aria-label="氏名 / カナ / 患者コード で検索"
            className="pl-9"
            disabled={disabled}
          />
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm text-text-secondary">
        <Checkbox
          checked={showInactive}
          onCheckedChange={(v) => setShowInactive(v === true)}
          aria-label="非稼働も表示"
        />
        非稼働（入院中など）も表示
      </label>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      )}

      {isError && (
        <p className="text-sm text-error">患者の取得に失敗しました。通信状況をご確認ください。</p>
      )}

      {!isLoading && !isError && rows.length === 0 && (
        <p className="text-sm text-text-secondary">
          該当するお客様が見つかりませんでした。
          {!showInactive && '「非稼働も表示」もお試しください。'}
        </p>
      )}

      {rows.length > 0 && (
        <div className="max-h-[50vh] overflow-y-auto rounded-md border border-border-default">
          {rows.map((p, i) => {
            const label = kanaRowLabel(p.kana);
            const head = i === 0 || kanaRowLabel(rows[i - 1]?.kana) !== label;
            const active = isSchedulableStatus(p.status);
            return (
              <div key={p.id}>
                {head && (
                  <div className="bg-bg-muted px-3 py-1 text-xs font-bold text-text-secondary">
                    {label}
                  </div>
                )}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onPick(p)}
                  data-testid={`patient-row-${p.id}`}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 border-t border-border-default px-3 py-2.5 text-left first:border-t-0 disabled:opacity-50',
                    !active && 'bg-bg-muted',
                  )}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-bold text-text-primary">
                      {p.name}
                      {!active && (
                        <span className="ml-1.5 text-xs font-normal text-warning">
                          {STATUS_LABEL[normalizePatientStatus(p.status)]}
                        </span>
                      )}
                    </span>
                    <span className="block truncate text-xs text-text-secondary">{p.kana}</span>
                  </span>
                  <span className="shrink-0 text-xs text-text-muted">{p.code}</span>
                </button>
              </div>
            );
          })}
        </div>
      )}

      {data?.truncated && (
        <p className="text-xs text-text-muted">
          500 件までを表示しています（それ以降は PC の患者一覧から）。
        </p>
      )}
    </div>
  );
}
