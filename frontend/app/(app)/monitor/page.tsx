'use client';

/**
 * /monitor — PC 訪問モニター (QR チェックイン Phase 3)。
 *
 * 管理者 (admin/manager) が当日の予定 vs 実績をリアルタイム (60s ポーリング) に把握
 * する。タイムライン (ガント) + 要対応アラートトレイ + 詳細パネル + 地図 (Leaflet)。
 * 非該当ロールは /dashboard へリダイレクト (settings/scheduling の作法に倣う)。
 *
 * しきい値設定 (⚙ モーダル) は Phase 4 のため本ページには載せない。
 */
import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';

import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { useMonitor, useNearbyPatients } from '@/lib/queries/monitor';
import type { MonitorStaffRow, MonitorVisit, NearbyPatient } from '@/lib/schemas/monitor';

import { MonitorTimeline } from '@/components/monitor/MonitorTimeline';
import { MonitorAlertTray } from '@/components/monitor/MonitorAlertTray';
import { MonitorDetailPanel } from '@/components/monitor/MonitorDetailPanel';
import { MonitorMap } from '@/components/monitor/MonitorMap';
import { displayStatus, groupVisits, hmToMinutes, isoToHm } from '@/components/monitor/constants';

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
  const router = useRouter();
  const role = session?.user?.role;
  const canView = role === 'admin' || role === 'manager';

  useEffect(() => {
    if (status === 'authenticated' && !canView) {
      router.replace('/dashboard');
    }
  }, [status, canView, router]);

  const [date, setDate] = useState<string>(todayJst);
  const [officeId, setOfficeId] = useState<string | null>(null);
  const [only, setOnly] = useState<OnlyFilter>(null);
  const [selectedStaffId, setSelectedStaffId] = useState<string | null>(null);
  const [selectedVisitId, setSelectedVisitId] = useState<string | null>(null);

  const monitorQuery = useMonitor({ date });
  const data = monitorQuery.data ?? null;

  const nowMinutes = useMemo(() => {
    if (!data?.now) return -1;
    return hmToMinutes(isoToHm(data.now));
  }, [data?.now]);

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
    () => filteredRows.find((r) => r.staff_id != null && r.staff_id === selectedStaffId) ?? null,
    [filteredRows, selectedStaffId],
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

  const onSelectVisit = (visitId: string) => {
    for (const r of data?.staff ?? []) {
      const v = r.visits.find((x) => x.visit_id === visitId);
      if (v) {
        setSelectedStaffId(r.staff_id ?? null);
        setSelectedVisitId(visitId);
        return;
      }
    }
  };
  const onSelectStaff = (staffId: string | null) => {
    setSelectedStaffId(staffId);
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

  if (status === 'loading' || (status === 'authenticated' && !canView)) {
    return null;
  }

  return (
    <div className="flex h-[calc(100vh-100px)] flex-col">
      {/* ヘッダ */}
      <div className="flex items-center gap-3 border-b border-border-default px-5 py-3">
        <h1 className="font-serif text-xl font-bold text-text-primary">訪問モニター</h1>
        <div className="flex-1" />
        {isToday ? (
          <span className="inline-flex items-center gap-1.5 text-[11.5px] text-text-secondary">
            <span className="h-2 w-2 animate-pulse rounded-full bg-green-600" />
            リアルタイム更新中
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[11.5px] font-semibold text-amber-600">
            <span className="h-2 w-2 rounded-full bg-amber-500" />
            過去日表示（リアルタイム更新なし）
          </span>
        )}
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
        <Chip active={officeId === null} onClick={() => setOfficeId(null)}>
          全拠点
        </Chip>
        {(data?.offices ?? []).map((o) => (
          <Chip key={o.id} active={officeId === o.id} onClick={() => setOfficeId(o.id)}>
            {o.name}
          </Chip>
        ))}
        <span className="h-5 w-px bg-border-default" />
        <Chip
          active={only === 'anomaly'}
          onClick={() => setOnly((o) => (o === 'anomaly' ? null : 'anomaly'))}
        >
          ⚠ 異常のみ
        </Chip>
        <Chip
          active={only === 'missing'}
          onClick={() => setOnly((o) => (o === 'missing' ? null : 'missing'))}
        >
          未訪問のみ
        </Chip>
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
        />
      )}

      {/* 凡例 */}
      <div className="flex flex-wrap gap-3.5 px-5 pb-2 text-xs text-text-secondary">
        <Legend
          swatch="repeating-linear-gradient(90deg,#cfd8d6,#cfd8d6 6px,#dde4e2 6px,#dde4e2 12px)"
          label="予定（患者名）"
          border
        />
        <Legend swatch="#0d9488" label="一致" />
        <Legend swatch="#d97706" label="要確認" />
        <Legend swatch="#dc2626" label="不一致" />
        <Legend
          swatch="repeating-linear-gradient(45deg,#dc2626,#dc2626 5px,#ef4444 5px,#ef4444 10px)"
          label="未訪問"
        />
        <span className="text-text-muted">→1.2km 次までの距離</span>
      </div>

      {/* 本体: タイムライン + 詳細/地図 */}
      <div className="flex min-h-0 flex-1">
        <div className="flex-1 overflow-auto">
          {monitorQuery.isLoading ? (
            <div className="space-y-2 p-5">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : monitorQuery.isError ? (
            <div className="p-6 text-sm text-red-600">
              モニターの取得に失敗しました。再読み込みしてください。
            </div>
          ) : (
            <MonitorTimeline
              rows={filteredRows}
              selectedStaffId={selectedStaffId}
              selectedVisitId={selectedVisitId}
              nowMinutes={nowMinutes}
              onSelectStaff={onSelectStaff}
              onSelectVisit={onSelectVisit}
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
          />
        </aside>
      </div>
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1.5 text-[12.5px]',
        active
          ? 'border-transparent bg-brand-primary-light font-semibold text-brand-primary-hover'
          : 'border-border-default bg-bg-base text-text-secondary',
      )}
    >
      {children}
    </button>
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
        ? 'text-amber-600'
        : tone === 'bad'
          ? 'text-red-600'
          : 'text-text-primary';
  return (
    <div
      className={cn(
        'inline-flex items-baseline gap-1.5 rounded-full border px-3 py-1',
        tone === 'bad' ? 'border-red-300 bg-red-50' : 'border-border-default bg-bg-base',
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
        className="inline-block h-2.5 w-5 rounded-[3px]"
        style={{ background: swatch, border: border ? '1px solid #c2ccc9' : undefined }}
      />
      {label}
    </span>
  );
}
