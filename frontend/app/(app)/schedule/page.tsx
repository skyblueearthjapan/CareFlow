'use client';

/**
 * /schedule — Wave 17 Phase B: (曜日 × コース) テーブル N 個構造.
 *
 * Wave 16 の「スタッフ × 時刻 × 曜日」(StaffWeekTablePanel) を完全に置換し、
 * Excel スケジュール枠組みに完全準拠した「曜日タブ + コーステーブル N 個」
 * 構造に刷新した。
 *
 * レイアウト (Phase G-41 で Card 1 は表示制御 only に再整理):
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ Card 1 (表示制御 only)                                        │
 *   │  < 今週 > YYYY-Www      拠点 ▼                                │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │ CourseDayTablePanel (Card 2: 操作 + 曜日 + テーブル/リスト)    │
 *   │  Row 1: 主要 4 ボタン (右寄せ)                                 │
 *   │   [週を生成][自動割付 🟢][全面最適化 🟢][プール投入]            │
 *   │  Row 2: 曜日タブ + テーブル/リスト + 二次操作                  │
 *   │  (コーステーブル本体)                                         │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * RBAC:
 *   - admin / manager: 全操作可
 *   - staff: 閲覧のみ
 */
import { useMemo, useState } from 'react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { CourseDayTablePanel } from '@/components/schedule/v2/CourseDayTablePanel';
import { WeekSelector, toWeekStart } from '@/components/schedule/WeekSelector';
import { Card } from '@/components/ui/card';
import { useOffices } from '@/lib/queries/offices';
import { isoWeekFromLocalDate } from '@/lib/format/isoWeek';
import { useSession } from 'next-auth/react';
import { isAdminRole } from '@/lib/rbac';
import { useUIStore } from '@/lib/stores/ui';

// ─────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────

export default function SchedulePage() {
  const { data: session } = useSession();
  const role = session?.user?.role ?? 'staff';
  const canEdit = isAdminRole(role);

  // 週 state.
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const { isoYear, isoWeek } = useMemo(() => isoWeekFromLocalDate(weekStart), [weekStart]);

  // 拠点フィルタ. null = 全拠点.
  const [officeId, setOfficeId] = useState<string | null>(null);
  const officesQuery = useOffices({ limit: 50 });

  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;

  // 上部折りたたみ (PO 要望 2026-08-23: 盤面を広く見せたい)。
  // 畳むと見出し + 週セレクタ Card を隠し、週切替 / 拠点フィルタは
  // CourseDayTablePanel のコンパクト行へ移す (props で委譲)。
  const headerCollapsed = useUIStore((s) => s.scheduleHeaderCollapsed);

  return (
    // lg 以上ではページ自体をスクロールさせず (h-full 固定)、Panel 内の
    // タイムライン領域とプールだけを内部スクロールにする (sticky のような
    // 「張り付くまで一瞬上がる」挙動を根絶するため・PO指摘 2026-07-08)。
    <section
      className="flex flex-col gap-3 lg:h-full lg:min-h-0"
      data-testid="schedule-page-course-day"
    >
      {headerCollapsed ? null : (
        <header className="space-y-1 lg:shrink-0" data-testid="schedule-page-header">
          <RakusukeTitle
            pose="calendar"
            title="スケジュール"
            subtitle="(曜日 × コース) テーブルで週次スケジュールを管理します。"
          />
        </header>
      )}

      {/* Phase G-41: Card 1 は表示制御のみ.
          左: WeekSelector + iso week label.
          右 (ml-auto): 拠点フィルタ.
          主要 4 ボタン (週を生成 / 自動割付 / 全面最適化 / プール投入) は CourseDayTablePanel Row 1 へ移設. */}
      {headerCollapsed ? null : (
        <Card
          className="flex flex-wrap items-center gap-3 p-3 lg:shrink-0"
          data-testid="schedule-week-selector-card"
        >
          <div className="flex flex-wrap items-center gap-3">
            <WeekSelector weekStart={weekStart} onChange={setWeekStart} />
            <span className="tnum text-xs text-text-muted">{isoWeekLabel}</span>
          </div>

          {/* ml-auto で右端に拠点フィルタを寄せる. */}
          <div className="ml-auto flex flex-wrap items-center gap-3">
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
          </div>
        </Card>
      )}

      {/* メイン: CourseDayTablePanel (Card 2).
          Phase G-41: 主要 4 ボタン + 曜日タブ + テーブル/リスト + 二次操作を 1 つの Card にまとめる.
          2026-08-23: 上部を畳んだときは週切替 / 拠点フィルタも Panel のコンパクト行が担うため
          onWeekChange / onOfficeChange を渡す. */}
      <CourseDayTablePanel
        weekStart={weekStart}
        officeId={officeId}
        canEdit={canEdit}
        onWeekChange={setWeekStart}
        onOfficeChange={setOfficeId}
      />
    </section>
  );
}
