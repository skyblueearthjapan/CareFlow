'use client';

/**
 * /acceptance — 受け入れ枠マトリックス (PC版, P2).
 *
 * 拠点ごとに、配下の全コースを統合した「曜日 × 時間帯」の受け入れ可否 (○△×) を
 * 一覧表示する read-only ページ。事務所・現場での空き状況把握と、印刷して持ち運ぶ
 * 用途を想定 (印刷ボタン → ブラウザ印刷、マトリックスのみ印字)。
 *
 * 値は自動算出 (当週の実 Visit) に既存の手動「受入目安」を重ねた effective を表示。
 * 拠点・コースはアプリ登録データに完全追従する (ハードコードなし)。
 */
import { useMemo, useState } from 'react';

import { format } from 'date-fns';
import { ja } from 'date-fns/locale';
import { Printer } from 'lucide-react';

import {
  AcceptanceMatrixBoard,
  AcceptanceMatrixLegend,
} from '@/components/acceptance/AcceptanceMatrixBoard';
import { WeekSelector, toWeekStart, addDays } from '@/components/schedule/WeekSelector';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useAcceptanceMatrix } from '@/lib/queries/acceptance_matrix';
import { toIsoYearWeek } from '@/lib/queries/fieldBoard';
import { useOffices } from '@/lib/queries/offices';

export default function AcceptanceMatrixPage() {
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);
  const [officeId, setOfficeId] = useState<string | null>(null);

  const officesQuery = useOffices({ limit: 50 });
  const matrixQuery = useAcceptanceMatrix({ isoYear, isoWeek, officeId });

  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;
  const rangeLabel = `${format(weekStart, 'yyyy/M/d', { locale: ja })} 〜 ${format(
    addDays(weekStart, 6),
    'M/d',
    { locale: ja },
  )}`;

  return (
    <section className="space-y-3">
      <header className="space-y-1 no-print">
        <h1 className="font-serif text-2xl font-bold text-text-primary">受け入れ枠</h1>
        <p className="text-sm text-text-secondary">
          拠点ごとに全コースを統合した「曜日 × 時間帯」の受け入れ可能枠（おおよその目安）。
        </p>
      </header>

      {/* 操作バー (印刷対象外) */}
      <Card className="flex flex-wrap items-center gap-3 p-3 no-print">
        <div className="flex flex-wrap items-center gap-3">
          <WeekSelector weekStart={weekStart} onChange={setWeekStart} />
          <span className="tnum text-xs text-text-muted">{isoWeekLabel}</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1 text-xs text-text-secondary">
            拠点
            <select
              value={officeId ?? ''}
              onChange={(e) => setOfficeId(e.target.value === '' ? null : e.target.value)}
              className="rounded border border-border-default bg-bg-base px-1.5 py-1 text-xs"
              aria-label="拠点フィルタ"
            >
              <option value="">全拠点</option>
              {officesQuery.allOffices.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => window.print()}
            className="gap-1.5"
          >
            <Printer className="h-4 w-4" />
            印刷
          </Button>
        </div>
      </Card>

      {/* 印刷対象コンテナ */}
      <div id="acceptance-print" className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border-default pb-3">
          <div>
            <h2 className="font-serif text-xl font-bold text-text-primary">
              受け入れ枠マトリックス
            </h2>
            <p className="text-xs text-text-secondary">
              {isoYear}年 第{isoWeek}週（{rangeLabel}）現在 ・ 全コース統合の目安
            </p>
          </div>
          <AcceptanceMatrixLegend />
        </div>

        {matrixQuery.isLoading && <p className="text-sm text-text-secondary">読み込み中…</p>}
        {matrixQuery.isError && (
          <p className="text-sm text-rose-600">
            読み込みに失敗しました: {matrixQuery.error.message}
          </p>
        )}
        {matrixQuery.data && (
          <>
            {!matrixQuery.data.week_generated_any && (
              <p className="rounded bg-bg-muted px-3 py-2 text-xs text-text-secondary no-print">
                ※ この週はまだコースが生成されていないため、空き枠を算出できません（×表示）。
              </p>
            )}
            <AcceptanceMatrixBoard data={matrixQuery.data} />
          </>
        )}
      </div>
    </section>
  );
}
