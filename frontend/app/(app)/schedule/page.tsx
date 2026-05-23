'use client';

/**
 * /schedule — Wave 17 Phase B: (曜日 × コース) テーブル N 個構造.
 *
 * Wave 16 の「スタッフ × 時刻 × 曜日」(StaffWeekTablePanel) を完全に置換し、
 * Excel スケジュール枠組みに完全準拠した「曜日タブ + コーステーブル N 個」
 * 構造に刷新した。
 *
 * レイアウト:
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ ヘッダー                                                       │
 *   │  週切替 | 拠点フィルタ | 受入目安レイヤー | 一括固定化         │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │ CourseDayTablePanel                                          │
 *   │  - 曜日タブ [月][火][水][木][金][土]                           │
 *   │  - 「週を生成」 + 「自動割付」 (admin/manager only)             │
 *   │  - 当該曜日のコーステーブル N 個 (5 列 × 35 行 / 9:30-18:00)    │
 *   │  - 保留プール (DnD ドロップで place-and-fix)                  │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * RBAC:
 *   - admin / manager: 全操作可
 *   - staff: 閲覧のみ
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { CourseDayTablePanel } from '@/components/schedule/v2/CourseDayTablePanel';
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
    <section className="space-y-3" data-testid="schedule-page-course-day">
      <header className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-text-primary">スケジュール</h1>
        <p className="text-sm text-text-secondary">
          (曜日 × コース) テーブルで週次スケジュールを管理します。
        </p>
      </header>

      {/* ヘッダーバー (表示制御のみ): 週切替 + 拠点フィルタ + 受入目安レイヤー.
          Phase G-35: 一括操作系ボタン (Bulk*) は CourseDayTablePanel ヘッダーへ集約済. */}
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
        </div>
      </Card>

      {/* メイン: CourseDayTablePanel */}
      <CourseDayTablePanel
        weekStart={weekStart}
        officeId={officeId}
        canEdit={canEdit}
        showAcceptanceLayer={showAcceptance}
      />
    </section>
  );
}
