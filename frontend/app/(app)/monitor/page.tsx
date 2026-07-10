'use client';

/**
 * /monitor — PC 訪問モニター (QR チェックイン Phase 3)。
 *
 * 当日の予定 vs 実績をリアルタイム (60s ポーリング) に把握する。タイムライン (ガント)
 * + 要対応アラートトレイ + 詳細パネル + 地図 (Leaflet)。
 * RB (PO決定 2026-07-08): 閲覧は全ロール。確認済み等の書込みは admin/manager (canReview)。
 *
 * しきい値設定 (⚙) は専用ページ /settings/checkin へ遷移する (Phase 4)。
 */
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { Settings, TriangleAlert } from 'lucide-react';

import { FilterChip } from '@/components/ui/filter-chip';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import {
  useMonitor,
  useNearbyPatients,
  useReviewVisit,
  useUnreviewVisit,
} from '@/lib/queries/monitor';
import type { MonitorStaffRow, MonitorVisit, NearbyPatient } from '@/lib/schemas/monitor';
import type { EventRead } from '@/lib/schemas/staff-events';

import {
  MonitorTimeline,
  monitorRowKey,
  type MonitorPatientMeta,
} from '@/components/monitor/MonitorTimeline';
import { MonitorAlertTray } from '@/components/monitor/MonitorAlertTray';
import { MonitorDetailPanel } from '@/components/monitor/MonitorDetailPanel';
import { MonitorMap } from '@/components/monitor/MonitorMap';
import {
  MISSING_BAR_BG,
  displayStatus,
  groupVisits,
  hmToMinutes,
  isoToHm,
} from '@/components/monitor/constants';
// M-4a/b: カード視覚言語 (性別ウォッシュ・イベント帯) 用の FE join。
import { usePatients } from '@/lib/queries/patients';
import { useStaffList } from '@/lib/queries/staff';
import { buildStaffEventsMap, useWeekStaffEvents } from '@/lib/queries/staff-events';
import { genderPalette } from '@/lib/scheduling/timeline';

type OnlyFilter = null | 'anomaly' | 'missing';

/** 近隣候補が無いときの安定参照 (毎レンダの新規 [] を避ける)。 */
const EMPTY_NEARBY: NearbyPatient[] = [];

/** 今日 (JST) の YYYY-MM-DD。 */
function todayJst(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

/** YYYY-MM-DD → [year, month, day] (数値)。 */
function parseYmd(ymd: string): [number, number, number] {
  const parts = ymd.split('-');
  return [Number(parts[0]), Number(parts[1]), Number(parts[2])];
}

/** YYYY-MM-DD に日数を加算。 */
function addDays(ymd: string, days: number): string {
  const [y, m, d] = parseYmd(ymd);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

function formatHeaderDate(ymd: string): string {
  const [y, m, d] = parseYmd(ymd);
  const dt = new Date(Date.UTC(y, m - 1, d));
  const wd = ['日', '月', '火', '水', '木', '金', '土'][dt.getUTCDay()] ?? '';
  return `${y}/${String(m).padStart(2, '0')}/${String(d).padStart(2, '0')} (${wd})`;
}

export default function MonitorPage() {
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  // RB (PO決定 2026-07-08): 閲覧は全ロール (BE GET も staff 許可済み)。
  // 確認済みトグル等の書込み操作だけ admin/manager (canReview)。
  const canReview = role === 'admin' || role === 'manager';

  const [date, setDate] = useState<string>(todayJst);
  const [officeId, setOfficeId] = useState<string | null>(null);
  const [only, setOnly] = useState<OnlyFilter>(null);
  // 行キー選択 (monitorRowKey)。担当未設定の行も選択してマップ表示できる (PO 報告 2026-07-03)。
  const [selectedRowKey, setSelectedRowKey] = useState<string | null>(null);
  const [selectedVisitId, setSelectedVisitId] = useState<string | null>(null);

  const monitorQuery = useMonitor({ date });
  const data = monitorQuery.data ?? null;

  const nowMinutes = useMemo(() => {
    if (!data?.now) return -1;
    return hmToMinutes(isoToHm(data.now));
  }, [data?.now]);

  // ─── M-4a: カード視覚言語用の FE join (スケジュール側と同一キー = キャッシュ共有) ───
  // 予定カードの性別ウォッシュ・📍住所 (患者マスタ) と行番号バッジのスタッフ性別。
  const patientsQuery = usePatients({ limit: 500 });
  const patientMetaById = useMemo(() => {
    const m = new Map<string, MonitorPatientMeta>();
    for (const p of patientsQuery.data?.items ?? []) {
      m.set(p.id, { sex: p.sex ?? null, address: p.address ?? null });
    }
    return m;
  }, [patientsQuery.data]);
  const staffListQuery = useStaffList({ limit: 200 });
  const staffSexById = useMemo(() => {
    const m = new Map<string, string | null | undefined>();
    for (const s of staffListQuery.data ?? []) m.set(s.id, s.sex ?? null);
    return m;
  }, [staffListQuery.data]);

  // ─── M-4b: 当日の会議・イベント帯 (藤色・カイポケ反映外・表示専用) ───
  // 行=コース単位 (2026-07-10): 行内の全担当 (staff_ids) を集めて重複排除する。
  const monitorStaffIds = useMemo(() => {
    const ids = new Set<string>();
    for (const r of data?.staff ?? []) {
      for (const sid of r.staff_ids ?? []) ids.add(sid);
      if (r.staff_id) ids.add(r.staff_id);
    }
    return Array.from(ids);
  }, [data?.staff]);
  // Date.UTC で構築する: useWeekStaffEvents は内部で toISOString() (UTC) から
  // from/to を作るため、ローカルTZ (JST) 解釈の Date だと前日にズレて
  // イベントが 1 件も取れなくなる (レビューHIGH対応・addDays と同方針)。
  const dateObj = useMemo(() => {
    const [y, m, d] = parseYmd(date);
    return new Date(Date.UTC(y, m - 1, d));
  }, [date]);
  const { data: staffEventsData } = useWeekStaffEvents(monitorStaffIds, dateObj, dateObj);
  const eventsByStaffId = useMemo(() => {
    const all = buildStaffEventsMap(monitorStaffIds, staffEventsData);
    // 当日分だけに絞る (フックは日単位でも配列を返すため防御的にフィルタ)。
    const m = new Map<string, EventRead[]>();
    for (const [sid, events] of all.entries()) {
      const todays = events.filter((ev) => ev.date === date);
      if (todays.length > 0) m.set(sid, todays);
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monitorStaffIds.join(','), staffEventsData, date]);

  // クライアント側フィルタ (拠点チップ / 異常のみ・未訪問のみ)。offices チップを
  // 常に全件出すため拠点フィルタもクライアントで行う (サーバ refetch なし)。
  const filteredRows = useMemo<MonitorStaffRow[]>(() => {
    if (!data) return [];
    const rows = officeId ? data.staff.filter((r) => r.office_id === officeId) : data.staff;
    if (!only) return rows;
    return rows
      .map((r) => ({
        ...r,
        visits: r.visits.filter((v) =>
          only === 'missing'
            ? v.alert_level === 'missing'
            : v.alert_level === 'review' ||
              v.alert_level === 'mismatch' ||
              v.alert_level === 'missing',
        ),
      }))
      .filter((r) => r.visits.length > 0);
  }, [data, officeId, only]);

  const selectedRow = useMemo(
    () => filteredRows.find((r) => monitorRowKey(r) === selectedRowKey) ?? null,
    [filteredRows, selectedRowKey],
  );
  const selectedVisit = useMemo<MonitorVisit | null>(() => {
    if (!selectedVisitId) return null;
    for (const r of filteredRows) {
      const v = r.visits.find((x) => x.visit_id === selectedVisitId);
      if (v) return v;
    }
    return null;
  }, [filteredRows, selectedVisitId]);

  // 場所違いで実 GPS 座標があれば近隣候補を取得。
  const isMismatch = selectedVisit != null && displayStatus(selectedVisit) === 'mismatch';
  const nearbyQuery = useNearbyPatients({
    lat: isMismatch ? selectedVisit?.arrival?.lat : null,
    lng: isMismatch ? selectedVisit?.arrival?.lng : null,
    enabled: isMismatch,
  });

  const reviewVisit = useReviewVisit();
  const unreviewVisit = useUnreviewVisit();
  const reviewPending = reviewVisit.isPending || unreviewVisit.isPending;
  const onReview = (visitId: string, comment: string | null) =>
    reviewVisit.mutate({ visitId, comment });
  const onUnreview = (visitId: string) => unreviewVisit.mutate(visitId);

  const onSelectVisit = (visitId: string) => {
    for (const r of data?.staff ?? []) {
      const v = r.visits.find((x) => x.visit_id === visitId);
      if (v) {
        setSelectedRowKey(monitorRowKey(r));
        setSelectedVisitId(visitId);
        return;
      }
    }
  };
  const onSelectRow = (rowKey: string) => {
    setSelectedRowKey(rowKey);
    setSelectedVisitId(null);
  };

  // KPI (表示中の論理訪問から算出)。2 名体制 (visit_group_id) は 1 件に重複排除し、
  // 各論理訪問を最重要バケットに 1 回だけ計上する (missing > review/mismatch >
  // 記録済(done/inprogress) > 予定/到着待ち)。合計 = 論理訪問数。
  const kpi = useMemo(() => {
    const groups = groupVisits(filteredRows);
    let recorded = 0;
    let review = 0;
    let missing = 0;
    const delays: number[] = [];
    for (const g of groups) {
      const rep = g.representative;
      if (g.worstAlertLevel === 'missing') {
        missing += 1;
      } else if (g.worstAlertLevel === 'review' || g.worstAlertLevel === 'mismatch') {
        review += 1;
      } else if (rep.phase === 'done' || rep.phase === 'inprogress') {
        recorded += 1;
      }
      if (typeof rep.arrival_delay_min === 'number') delays.push(rep.arrival_delay_min);
    }
    const avg = delays.length ? Math.round(delays.reduce((a, b) => a + b, 0) / delays.length) : 0;
    return { total: groups.length, recorded, review, missing, avg };
  }, [filteredRows]);

  const isToday = date === todayJst();

  if (status === 'loading') {
    return null;
  }

  return (
    <div className="flex h-[calc(100vh-100px)] flex-col overflow-hidden rounded-lg border border-border-default bg-bg-base shadow-outer-card">
      {/* ヘッダ */}
      <div className="flex items-center gap-3 border-b border-border-default px-5 py-3">
        <h1 className="font-serif text-xl font-bold text-text-primary">訪問モニター</h1>
        <div className="flex-1" />
        {isToday ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-text-secondary">
            <span className="h-2 w-2 animate-pulse rounded-full bg-success" />
            リアルタイム更新中
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-warning">
            <span className="h-2 w-2 rounded-full bg-warning" />
            過去日表示（リアルタイム更新なし）
          </span>
        )}
        <Link
          href="/settings/checkin"
          data-testid="monitor-threshold-settings-link"
          className="inline-flex items-center gap-1.5 rounded-md border border-border-default bg-bg-base px-2.5 py-1 text-xs text-text-secondary hover:bg-bg-muted"
        >
          <Settings className="h-3.5 w-3.5" strokeWidth={1.75} />
          しきい値設定
        </Link>
      </div>

      {/* フィルタ */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border-default px-5 py-2.5">
        <span className="inline-flex items-center gap-2 text-sm font-semibold">
          <button
            type="button"
            aria-label="前日"
            onClick={() => setDate((d) => addDays(d, -1))}
            className="h-6 w-6 rounded-md border border-border-default"
          >
            ‹
          </button>
          {formatHeaderDate(date)}
          <button
            type="button"
            aria-label="翌日"
            onClick={() => setDate((d) => addDays(d, 1))}
            className="h-6 w-6 rounded-md border border-border-default"
          >
            ›
          </button>
        </span>
        <span className="h-5 w-px bg-border-default" />
        <FilterChip active={officeId === null} onClick={() => setOfficeId(null)}>
          全拠点
        </FilterChip>
        {(data?.offices ?? []).map((o) => (
          <FilterChip key={o.id} active={officeId === o.id} onClick={() => setOfficeId(o.id)}>
            {o.name}
          </FilterChip>
        ))}
        <span className="h-5 w-px bg-border-default" />
        <FilterChip
          active={only === 'anomaly'}
          onClick={() => setOnly((o) => (o === 'anomaly' ? null : 'anomaly'))}
        >
          <TriangleAlert className="h-3 w-3" />
          異常のみ
        </FilterChip>
        <FilterChip
          active={only === 'missing'}
          onClick={() => setOnly((o) => (o === 'missing' ? null : 'missing'))}
        >
          未訪問のみ
        </FilterChip>
      </div>

      {/* KPI */}
      <div className="flex flex-wrap items-center gap-2 px-5 pb-1 pt-2">
        <Kpi label="本日の訪問" value={kpi.total} />
        <Kpi label="記録済" value={kpi.recorded} tone="ok" />
        <Kpi label="要確認" value={kpi.review} tone="warn" />
        <Kpi label="未訪問" value={kpi.missing} tone={kpi.missing ? 'bad' : undefined} />
        <Kpi label="平均到着ズレ" value={`${kpi.avg >= 0 ? '+' : ''}${kpi.avg}分`} />
      </div>

      {/* アラートトレイ */}
      {data && (
        <MonitorAlertTray
          rows={filteredRows}
          selectedVisitId={selectedVisitId}
          onSelectVisit={onSelectVisit}
          maxInprogressMin={data.thresholds.max_inprogress_min}
        />
      )}

      {/* 凡例 (M-4a: 予定=性別ウォッシュのカード・実績=状態色レール) */}
      <div className="flex flex-wrap gap-3.5 px-5 pb-2 text-xs text-text-secondary">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-flex gap-0.5">
            {(['male', 'female', 'unknown'] as const).map((sx) => {
              const pal = genderPalette(sx);
              return (
                <span
                  key={sx}
                  // rounded-[3px]: 10x14px の極小スウォッチのためトークン(sm=8px)未満の例外
                  className="inline-block h-2.5 w-3.5 rounded-[3px] border border-l-[2px]"
                  style={{ background: pal.bg, borderColor: pal.ln, borderLeftColor: pal.bar }}
                />
              );
            })}
          </span>
          予定カード（地色＝患者性別）
        </span>
        <Legend swatch="var(--status-match)" label="一致" />
        <Legend swatch="var(--status-review)" label="要確認" />
        <Legend swatch="var(--status-mismatch)" label="不一致" />
        <Legend swatch={MISSING_BAR_BG} label="未訪問" />
        <Legend swatch="var(--sched-event-bg)" label="会議・イベント" border />
        <span className="text-text-muted">→1.2km 次までの距離</span>
      </div>

      {/* 本体: タイムライン + 詳細/地図 */}
      <div className="flex min-h-0 flex-1">
        {/* M-4c改: self-start + max-h-full で高さを内容にフィットさせ、横スクロールバーが
            「最後のスタッフ行の直下」に来るようにする (旧: flex stretch で常に画面下端に
            張り付き、行から遠かった — PO指摘 2026-07-08)。行が多い日は max-h-full で
            従来どおり画面内に収まり、バーは可視行のすぐ下になる。 */}
        <div className="max-h-full min-w-0 flex-1 self-start overflow-auto">
          {monitorQuery.isLoading ? (
            <div className="space-y-2 p-5">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : monitorQuery.isError ? (
            <div className="p-6 text-sm text-error">
              モニターの取得に失敗しました。再読み込みしてください。
            </div>
          ) : (
            <MonitorTimeline
              rows={filteredRows}
              selectedRowKey={selectedRowKey}
              selectedVisitId={selectedVisitId}
              nowMinutes={nowMinutes}
              onSelectRow={onSelectRow}
              onSelectVisit={onSelectVisit}
              patientMetaById={patientMetaById}
              staffSexById={staffSexById}
              eventsByStaffId={eventsByStaffId}
            />
          )}
        </div>

        {/* 詳細パネル (地図も一緒にスクロール) */}
        <aside className="w-[348px] shrink-0 overflow-y-auto border-l border-border-default bg-bg-muted">
          <MonitorMap
            row={selectedRow}
            selectedVisitId={selectedVisitId}
            matchM={data?.thresholds.match_m ?? 100}
            nearby={nearbyQuery.data?.items ?? EMPTY_NEARBY}
          />
          <MonitorDetailPanel
            visit={selectedVisit}
            row={selectedRow}
            onSelectVisit={onSelectVisit}
            maxInprogressMin={data?.thresholds.max_inprogress_min}
            onReview={canReview ? onReview : undefined}
            onUnreview={canReview ? onUnreview : undefined}
            reviewPending={reviewPending}
          />
        </aside>
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: 'ok' | 'warn' | 'bad';
}) {
  const numColor =
    tone === 'ok'
      ? 'text-brand-primary-hover'
      : tone === 'warn'
        ? 'text-warning'
        : tone === 'bad'
          ? 'text-error'
          : 'text-text-primary';
  return (
    <div
      className={cn(
        'inline-flex items-baseline gap-1.5 rounded-full border px-3 py-1',
        tone === 'bad' ? 'border-border-error bg-error-bg' : 'border-border-default bg-bg-base',
      )}
    >
      <span className="text-[11px] text-text-muted">{label}</span>
      <span className={cn('text-[15px] font-bold tabular-nums', numColor)}>{value}</span>
    </div>
  );
}

function Legend({ swatch, label, border }: { swatch: string; label: string; border?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        // rounded-[3px]: 10x20px の極小スウォッチのためトークン(sm=8px)未満の例外
        className="inline-block h-2.5 w-5 rounded-[3px]"
        style={{
          background: swatch,
          border: border ? '1px solid var(--sched-event-ln)' : undefined,
        }}
      />
      {label}
    </span>
  );
}
