'use client';

/**
 * `/records` — PC 訪問記録一覧（設計 §11-2 / モック ⑤）。
 *
 * モバイルで録った音声の要約を、事務所側が読み・直し・確認する画面。
 * `RakusukeTitle` ＋ コンソール箱（`/monitor` と同じ型）でフィルタ行と表を包む。
 *
 * 絞り込みは**すべて BE パラメータ**（1 ページ 50 件の窓なので FE で削らない）。
 * `?patient=` / `?staff=` / `?visit=` で初期フィルタが決まる（患者詳細・スタッフ
 * 詳細の「すべて見る」、モニターの「🎙 記録を見る」からの導線）。
 *
 * RBAC は PO 決定どおり「全ロール同一表示」。staff が自分の分しか見えないのは
 * BE が `staff_id` を強制するため（FE では出し分けない）。
 */

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Check, Mic } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { RakusukeNote, RakusukeTitle } from '@/components/brand/Rakusuke';
import { RecordDetailDialog } from '@/components/records/RecordDetailDialog';
import {
  acceptUuid,
  formatRecordDate,
  formatRecordTime,
  recordStatusMeta,
  summaryFirstLine,
} from '@/components/records/recordFormat';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { isAdminRole } from '@/lib/rbac';
import { useVisitRecordings } from '@/lib/queries/visit-recordings';

import {
  DEFAULT_RECORDS_FILTER,
  RecordsFilterBar,
  recordPeriodRange,
  type RecordsFilterState,
} from './_components/RecordsFilterBar';

/** 1 ページの件数（設計 §11-2）。 */
const PAGE_SIZE = 50;

export default function RecordsPage() {
  // useSearchParams は Suspense 境界が必須 (Next 15 の CSR bailout 対策)。
  return (
    <Suspense
      fallback={
        <section className="space-y-4">
          <Skeleton className="h-12 w-1/2" />
          <Skeleton className="h-[400px] w-full" />
        </section>
      }
    >
      <RecordsPageInner />
    </Suspense>
  );
}

function RecordsPageInner() {
  const searchParams = useSearchParams();
  const { data: session, status: sessionStatus } = useSession();
  // staff は BE が `staff_id` を自分に固定する。UI は同一表示のまま、効かない
  // セレクトだけ無効化して理由を出す（PO 決定 + レビュー L-2）。
  //
  // セッション取得中は role が未定 = `isAdminRole` が false になるため、判定を
  // 保留する（レビュー N-3）。保留しないと管理者にも一瞬セレクトが無効化されて
  // 見え、開いた直後に触ると操作を取りこぼす。
  const staffScoped = sessionStatus === 'loading' ? false : !isAdminRole(session?.user?.role);
  // URL は人が書き換えられる。UUID でない値は「指定なし」として捨てる
  // （BE に 400 を撒かない・誤った絞り込みも起こさない・レビュー L-3）。
  const initialPatient = acceptUuid(searchParams.get('patient'));
  const initialStaff = acceptUuid(searchParams.get('staff'));
  const visitId = acceptUuid(searchParams.get('visit'));

  const [filter, setFilter] = useState<RecordsFilterState>(() => ({
    ...DEFAULT_RECORDS_FILTER,
    // 患者 / スタッフ / 訪問を名指しで開いたときは期間で切らない
    // （「すべて見る」で今週しか出ないのは目的と合わない）。
    tab: initialPatient || initialStaff || visitId ? 'all' : DEFAULT_RECORDS_FILTER.tab,
    patientId: initialPatient,
    staffId: initialStaff,
  }));
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // フィルタが変わったら 1 ページ目へ戻す（窓だけ残って空表示になるのを防ぐ）。
  useEffect(() => {
    setPage(0);
  }, [filter]);

  const range = useMemo(() => recordPeriodRange(filter.tab), [filter.tab]);

  const query = useVisitRecordings({
    unscoped: true,
    visitId: visitId || null,
    patientId: filter.patientId || null,
    staffId: filter.staffId || null,
    officeId: filter.officeId || null,
    from: range.from ?? null,
    to: range.to ?? null,
    status: filter.status || null,
    reviewed: filter.reviewed === '' ? null : filter.reviewed === 'yes',
    q: filter.q || null,
    order: 'recorded_at_desc',
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  // `useEffect` の依存に入れるので参照を安定させる（毎レンダの新規 [] を避ける）。
  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const total = query.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // `?visit=` で来たら 1 件目の詳細をそのまま開く（モニターからの導線）。
  // 一度だけ。閉じたあとに開き直さない。
  const autoOpened = useRef(false);
  useEffect(() => {
    if (!visitId || autoOpened.current || items.length === 0) return;
    autoOpened.current = true;
    setSelectedId(items[0]?.id ?? null);
  }, [visitId, items]);

  return (
    <section className="flex flex-col gap-3">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <RakusukeTitle
          pose="visit"
          title="訪問記録"
          subtitle="現場で録音した音声の要約・文字起こしを読み、確認します。"
        />
      </header>

      {/* コンソール箱（/monitor と同じ型）。 */}
      <div className="flex flex-col overflow-hidden rounded-lg border border-border-default bg-bg-base shadow-outer-card">
        <div className="border-b border-border-default px-5 py-2.5">
          <RecordsFilterBar
            value={filter}
            onChange={setFilter}
            count={total}
            staffScoped={staffScoped}
          />
        </div>

        {query.isError ? (
          <div className="p-5" data-testid="records-error">
            <Alert variant="destructive">
              <AlertTitle>訪問記録を読み込めませんでした</AlertTitle>
              <AlertDescription className="whitespace-pre-wrap">
                {apiErrorMessage(query.error)}
              </AlertDescription>
            </Alert>
          </div>
        ) : query.isLoading ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : items.length === 0 ? (
          <div className="p-6" data-testid="records-empty">
            <RakusukeNote
              pose="think"
              title="この期間に訪問記録はありません。"
              comment="モバイルの録音ボタンから記録を作成できます。"
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="records-table">
              <thead>
                <tr className="border-b border-border-default text-left text-xs text-text-muted">
                  <th className="px-4 py-2 font-medium">日付</th>
                  <th className="px-4 py-2 font-medium">時刻</th>
                  <th className="px-4 py-2 font-medium">患者</th>
                  <th className="px-4 py-2 font-medium">スタッフ</th>
                  <th className="px-4 py-2 font-medium">要約（1行目）</th>
                  <th className="px-4 py-2 font-medium">状態</th>
                  <th className="px-4 py-2 text-center font-medium">🎙</th>
                  <th className="px-4 py-2 text-center font-medium">確認済み</th>
                </tr>
              </thead>
              <tbody>
                {items.map((rec) => {
                  const meta = recordStatusMeta(rec.status);
                  return (
                    <tr
                      key={rec.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelectedId(rec.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          setSelectedId(rec.id);
                        }
                      }}
                      className="cursor-pointer border-b border-border-default hover:bg-bg-muted"
                      data-testid={`records-row-${rec.id}`}
                    >
                      <td className="whitespace-nowrap px-4 py-2 tnum text-text-secondary">
                        {formatRecordDate(rec.recorded_at)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2 tnum text-text-secondary">
                        {formatRecordTime(rec.recorded_at)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2 font-semibold text-text-primary">
                        {rec.patient_name ?? '—'}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2 text-text-secondary">
                        {rec.staff_name ?? '—'}
                      </td>
                      <td className="max-w-[420px] truncate px-4 py-2 text-text-primary">
                        {summaryFirstLine(rec)}
                      </td>
                      <td className="px-4 py-2">
                        <Badge variant={meta.variant}>{meta.label}</Badge>
                      </td>
                      <td className="px-4 py-2 text-center">
                        {rec.has_audio !== false && (
                          <Mic
                            className="mx-auto h-4 w-4 text-text-muted"
                            strokeWidth={1.75}
                            aria-label="音声あり"
                          />
                        )}
                      </td>
                      <td className="px-4 py-2 text-center">
                        {rec.reviewed_at && (
                          <Check
                            className="mx-auto h-4 w-4 text-success"
                            strokeWidth={2}
                            aria-label="確認済み"
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* ページング */}
        {total > PAGE_SIZE && (
          <div
            className="flex items-center justify-end gap-3 border-t border-border-default px-5 py-2.5"
            data-testid="records-pager"
          >
            <span className="text-xs text-text-muted">
              {page + 1} / {pageCount} ページ（全{total}件）
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              前へ
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={page + 1 >= pageCount}
              onClick={() => setPage((p) => p + 1)}
            >
              次へ
            </Button>
          </div>
        )}
      </div>

      <RecordDetailDialog
        recordingId={selectedId}
        open={selectedId !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
        onDeleted={() => setSelectedId(null)}
      />
    </section>
  );
}
