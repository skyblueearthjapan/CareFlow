'use client';

/**
 * /schedule — Wave 17 Phase B: (曜日 × コース) テーブル N 個構造.
 *
 * Wave 16 の「スタッフ × 時刻 × 曜日」(StaffWeekTablePanel) を完全に置換し、
 * Excel スケジュール枠組みに完全準拠した「曜日タブ + コーステーブル N 個」
 * 構造に刷新した。
 *
 * レイアウト (Phase G-40 で Card 1 に主要 4 ボタンを統合):
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ Card 1 (週ナビ + 主要操作 + 拠点 + 受入目安、1 段統合)         │
 *   │  < 今週 > YYYY-Www                                            │
 *   │   [週を生成][自動割付 🟢][全面最適化 🟢][プール投入]           │
 *   │   │ 拠点 ▼ │ ☐ 受入目安                                      │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │ CourseDayTablePanel (Card 2: 2 row に圧縮)                    │
 *   │  Row 1: 曜日タブ + 二次操作 (固定枠戻 / 全件保存)              │
 *   │  Row 2: テーブル/リスト切替 + 一斉未割当 + 🔒/🔓               │
 *   │  (コーステーブル本体)                                         │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * RBAC:
 *   - admin / manager: 全操作可
 *   - staff: 閲覧のみ
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';
import { Loader2, Plus, RefreshCw, UserCheck } from 'lucide-react';
import { toast } from 'sonner';

import { CourseDayTablePanel } from '@/components/schedule/v2/CourseDayTablePanel';
import { DiffAddDialog } from '@/components/schedule/v2/DiffAddDialog';
import { FullOptimizeDialog } from '@/components/schedule/v2/FullOptimizeDialog';
import { WeekSelector, toWeekStart } from '@/components/schedule/WeekSelector';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { ApiError } from '@/lib/api-client';
import { useAssignStaffOnly } from '@/lib/queries/assign_staff_only';
import { useGenerateWeekOnly } from '@/lib/queries/generate_week';
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

  // ─── Phase G-40: 主要 4 ボタン (週生成 / 自動割付 / 全面最適化 / プール投入) を
  //     CourseDayTablePanel から Card 1 へ移設. 関連 hooks と state も page 側に lift. ───
  const generateWeekMut = useGenerateWeekOnly();
  const assignStaffOnlyMut = useAssignStaffOnly();
  const [diffAddOpen, setDiffAddOpen] = useState(false);
  const [fullOptimizeOpen, setFullOptimizeOpen] = useState(false);

  const isProcessing = generateWeekMut.isPending || assignStaffOnlyMut.isPending;

  const handleGenerateWeek = async () => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    try {
      const res = await generateWeekMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_id: officeId,
      });
      toast.success(`週次 visits を生成しました (visits=${res.visits_created})`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`週の生成に失敗しました: ${msg}`);
    }
  };

  const handleAssignStaff = async () => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    try {
      const res = await assignStaffOnlyMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_id: officeId,
      });
      toast.success(`スタッフを自動割付しました (courses=${res.courses_assigned})`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`自動割付に失敗しました: ${msg}`);
    }
  };

  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;

  return (
    <section className="space-y-3" data-testid="schedule-page-course-day">
      <header className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-text-primary">スケジュール</h1>
        <p className="text-sm text-text-secondary">
          (曜日 × コース) テーブルで週次スケジュールを管理します。
        </p>
      </header>

      {/* Phase G-40: Card 1 を「週ナビ + 主要操作 + 拠点 + 受入目安」 1 段に統合.
          左: WeekSelector + iso week label.
          中央: 主要 4 ボタン (canEdit only) — 週を生成 / 自動割付 🟢 / 全面最適化 🟢 / プール投入.
          右: 拠点フィルタ + 受入目安 (中央との間に縦区切り線で視覚的にグルーピング). */}
      <Card className="flex flex-wrap items-center gap-3 p-3">
        <div className="flex flex-wrap items-center gap-3">
          <WeekSelector weekStart={weekStart} onChange={setWeekStart} />
          <span className="tnum text-xs text-text-muted">{isoWeekLabel}</span>
        </div>

        {/* 主要 4 ボタン (admin/manager のみ).
            毎週必ず押す主要 2 ボタン (自動割付 / 全面最適化) のみ variant="default" (= brand-primary 緑) で目立たせる. */}
        {canEdit ? (
          <div
            className="flex flex-wrap items-center gap-1.5"
            data-testid="schedule-main-action-toolbar"
          >
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={handleGenerateWeek}
              disabled={generateWeekMut.isPending}
              data-testid="generate-week-button"
            >
              {generateWeekMut.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
              )}
              週を生成
            </Button>
            <Button
              type="button"
              size="sm"
              variant="default"
              onClick={handleAssignStaff}
              disabled={assignStaffOnlyMut.isPending}
              data-testid="assign-staff-only-button"
            >
              {assignStaffOnlyMut.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <UserCheck className="mr-1 h-4 w-4" aria-hidden />
              )}
              自動割付
            </Button>
            <Button
              type="button"
              size="sm"
              variant="default"
              onClick={() => setFullOptimizeOpen(true)}
              disabled={isProcessing}
              data-testid="full-optimize-button"
            >
              <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
              全面最適化
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setDiffAddOpen(true)}
              disabled={isProcessing}
              data-testid="diff-add-button"
            >
              <Plus className="mr-1 h-4 w-4" aria-hidden />
              プール投入
            </Button>
          </div>
        ) : null}

        {/* 主要操作 と 拠点/受入目安 の間に視覚的セパレーター (縦区切り線) を入れて bunching を回避.
            canEdit のときのみ表示 (= staff 閲覧時は中央に主要 4 ボタンが無いため区切り線も不要). */}
        {canEdit ? (
          <span
            aria-hidden
            className="h-5 w-px bg-border-default"
            data-testid="schedule-card1-divider"
          />
        ) : null}

        {/* ml-auto で右端に拠点/受入目安を寄せる. */}
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

      {/* メイン: CourseDayTablePanel.
          Phase G-40: 主要 4 ボタンの mutation pending 状態を isProcessing で渡し、
          panel 内の二次操作 (固定枠戻 / 一斉未割当 等) を多重実行から保護する. */}
      <CourseDayTablePanel
        weekStart={weekStart}
        officeId={officeId}
        canEdit={canEdit}
        showAcceptanceLayer={showAcceptance}
        isProcessing={isProcessing}
      />

      {/* Phase G-40: 主要 4 ボタンの dialog を panel から page 側へ移設. */}
      <DiffAddDialog
        open={diffAddOpen}
        onClose={() => setDiffAddOpen(false)}
        isoYear={isoYear}
        isoWeek={isoWeek}
        officeId={officeId}
      />
      <FullOptimizeDialog
        open={fullOptimizeOpen}
        onClose={() => setFullOptimizeOpen(false)}
        isoYear={isoYear}
        isoWeek={isoWeek}
        officeId={officeId}
      />
    </section>
  );
}
