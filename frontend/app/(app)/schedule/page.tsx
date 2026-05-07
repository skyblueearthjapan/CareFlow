'use client';

/**
 * /schedule — Wave 15 統合スケジュール画面 (W15-FE D-1).
 *
 * 旧 3 タブ構成 (Pool 配置 / コース提案 / スタッフ割付) を **完全に置換** し、
 * 「コース × 曜日マトリクス」の 1 画面で全フローを操作できる統合 UI に変更。
 *
 * レイアウト:
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ ヘッダー                                                       │
 *   │  週切替 | 拠点フィルタ | レイヤースイッチ | 一括固定化       │
 *   ├──────────┬───────────────────────────────────────────────────┤
 *   │          │ ScheduleUnifiedView (コース × 曜日)                │
 *   │ プール    │  - 受入目安レイヤー (背景色 + バッジ)              │
 *   │ (左)     │  - 満枠超過警告 (バッジ)                          │
 *   │          │  - スタッフ差替 dropdown                          │
 *   └──────────┴───────────────────────────────────────────────────┘
 *
 * 要件 (Wave 15 設計サマリ):
 *   - メイン軸: コーステンプレート × 曜日 (大改修案)
 *   - 受入目安: ヘッダーチェックで ON/OFF (デフォルト OFF)
 *   - 満枠超過: バッジのみ・モーダル無し (admin が自由に増やせる)
 *   - ドロップ即固定枠化: place-and-fix 1 トランザクション
 *   - 旧タブ構成は完全削除
 *
 * RBAC:
 *   - admin / manager: 全操作可
 *   - staff: 閲覧のみ
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { BulkFixToPatternButton } from '@/components/schedule/v2/BulkFixToPatternButton';
import { ScheduleUnifiedView } from '@/components/schedule/v2/ScheduleUnifiedView';
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

  // 週 state (controlled).
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  // 拠点フィルタ. null = 全拠点.
  const [officeId, setOfficeId] = useState<string | null>(null);
  const officesQuery = useOffices({ limit: 50 });

  // レイヤー切替 (デフォルト: 受入目安 OFF, 警告 ON).
  const [showAcceptance, setShowAcceptance] = useState(false);
  const [showWarning, setShowWarning] = useState(true);

  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;

  return (
    <section className="space-y-3" data-testid="schedule-page-unified">
      <header className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-text-primary">スケジュール</h1>
        <p className="text-sm text-text-secondary">
          コース × 曜日マトリクスで週次スケジュールを 1 画面操作できます。
        </p>
      </header>

      {/* ヘッダーバー: 週切替 + 拠点フィルタ + レイヤースイッチ + 一括固定化 */}
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

          {/* レイヤースイッチ */}
          <div className="flex items-center gap-3 rounded border border-border-default bg-bg-muted px-2 py-1">
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              <Checkbox
                checked={showAcceptance}
                onCheckedChange={(v) => setShowAcceptance(v === true)}
                aria-label="受入目安レイヤー"
              />
              <span>受入目安</span>
            </label>
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              <Checkbox
                checked={showWarning}
                onCheckedChange={(v) => setShowWarning(v === true)}
                aria-label="満枠超過警告レイヤー"
              />
              <span>警告</span>
            </label>
          </div>

          <BulkFixToPatternButton canEdit={canEdit} isoYear={isoYear} isoWeek={isoWeek} />
        </div>
      </Card>

      {/* メイン: ScheduleUnifiedView */}
      <ScheduleUnifiedView
        weekStart={weekStart}
        onWeekChange={setWeekStart}
        officeId={officeId}
        canEdit={canEdit}
        showAcceptanceLayer={showAcceptance}
        showWarningLayer={showWarning}
      />
    </section>
  );
}
