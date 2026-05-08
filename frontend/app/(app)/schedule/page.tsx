'use client';

/**
 * /schedule — Wave 16 Phase B スタッフ別週次スケジュール画面.
 *
 * Wave 15 の「コース×曜日マトリクス」(ScheduleUnifiedView) を完全に置換し、
 * 「スタッフ別テーブル N 個縦並び」構造に刷新した。
 *
 * レイアウト:
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ ヘッダー                                                       │
 *   │  週切替 | 拠点フィルタ | 受入目安レイヤー | 一括固定化         │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │ StaffWeekTablePanel                                          │
 *   │  - 「週を生成」ボタン (admin/manager only)                     │
 *   │  - スタッフ別テーブル N 個 (時刻×曜日 9:00-19:00 / 15min)      │
 *   │  - 保留プール (DnD ドロップで place-and-fix)                  │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * RBAC:
 *   - admin / manager: 全操作可
 *   - staff: 閲覧のみ
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { BulkFixToPatternButton } from '@/components/schedule/v2/BulkFixToPatternButton';
import { StaffWeekTablePanel } from '@/components/schedule/v2/StaffWeekTablePanel';
import { WeekSelector, toWeekStart } from '@/components/schedule/WeekSelector';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { useOffices } from '@/lib/queries/offices';

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

function toIsoYearWeek(d: Date): { isoYear: number; isoWeek: number } {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const year = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(year, 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return { isoYear: year, isoWeek: week };
}

// ─────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────

export default function SchedulePage() {
  const { data: session } = useSession();
  const role = session?.user?.role ?? 'staff';
  const canEdit = role === 'admin' || role === 'manager';

  // 週 state.
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  // 拠点フィルタ. null = 全拠点.
  const [officeId, setOfficeId] = useState<string | null>(null);
  const officesQuery = useOffices({ limit: 50 });

  // 受入目安レイヤー (フッター凡例 ON/OFF; 表示のみ.)
  const [showAcceptance, setShowAcceptance] = useState(false);

  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;

  return (
    <section className="space-y-3" data-testid="schedule-page-staff-week">
      <header className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-text-primary">スケジュール</h1>
        <p className="text-sm text-text-secondary">
          スタッフ別の週次タイムテーブルで配置と固定枠化を 1 画面で操作できます。
        </p>
      </header>

      {/* ヘッダーバー: 週切替 + 拠点フィルタ + 受入目安レイヤー + 一括固定化 */}
      <Card className="flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="flex flex-wrap items-center gap-3">
          <WeekSelector weekStart={weekStart} onChange={setWeekStart} />
          <span className="tnum text-xs text-text-muted">{isoWeekLabel}</span>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* 拠点フィルタ */}
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

          {/* 受入目安レイヤー (凡例フッター) */}
          <label className="flex items-center gap-1.5 rounded border border-border-default bg-bg-muted px-2 py-1 text-xs text-text-secondary">
            <Checkbox
              checked={showAcceptance}
              onCheckedChange={(v) => setShowAcceptance(v === true)}
              aria-label="受入目安凡例"
            />
            <span>受入目安</span>
          </label>

          <BulkFixToPatternButton canEdit={canEdit} isoYear={isoYear} isoWeek={isoWeek} />
        </div>
      </Card>

      {/* メイン: StaffWeekTablePanel */}
      <StaffWeekTablePanel
        weekStart={weekStart}
        officeId={officeId}
        canEdit={canEdit}
        showAcceptanceLayer={showAcceptance}
      />
    </section>
  );
}
