'use client';

/**
 * SpecialVisitPoolSection — 特別訪問週間のプール統合.
 *
 * 正典: `docs/plans/special-visit-week-design.md` §6-2 (Wave2) / PO 指示 2026-07-29。
 *
 * 保留プールの**最上段**に「⭐特別訪問週間」専用セクションを出し、表示中の週の
 * チケット (GET /special-visit-marks/pool) を並べる。
 *
 * PO 指示 2026-07-29 で **既存 UI へ統一**した:
 *   - カードは通常のプール患者カード (`PatientCard`) と同じ視覚言語
 *     (性別ウォッシュ地 + 左色帯 + 太字の氏名)。⭐ / 種別 / 曜日のバッジで区別する。
 *   - クリックすると通常のプール患者と同じ **ポップアップ**
 *     (`PatientScheduleDetailDialog` → `PoolCandidateList`) が開き、ミニスケジュールの
 *     中で「ここに入れますか」を確認して採用する。旧インライン配置パネルは廃止。
 *
 * 設計上のルール (据え置き):
 *   - 追加分は**固定化しない**。place は PFV を作らず、その週の visit 行だけを増やす
 *     (トーストにも「この週のみ・固定化しません」と明記する)。実際の propose / place は
 *     `PoolCandidateList` の特別モード (`specialTicket` prop) 側が担当する。
 *   - チケット 0 件のときはセクション自体を描画しない (通常のプールを邪魔しない)。
 *   - `last_placement` は**参考ヒント**。候補リストの先頭に出すだけで強制はしない。
 *
 * カードに `PatientCard` を直接使わないのは、`PatientCard` が dnd-kit の draggable
 * である一方、チケットはドラッグ配置に対応していないため (見た目だけを合わせる)。
 */
import * as React from 'react';
import { CalendarDays, Star } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

import { useSpecialVisitPool } from '@/lib/queries/specialVisitWeek';
import { genderPalette } from '@/lib/scheduling/timeline';
import type { SpecialPoolTicket } from '@/lib/schemas/specialVisitWeek';

import { PatientScheduleDetailDialog } from './PatientScheduleDetailDialog';
import type { PoolCandidateSpecialTicket } from './PoolCandidateList';
import { SpecialVisitWeekDialog } from './SpecialVisitWeekDialog';

/** 0=月..5=土 (日曜は対象外だが 7 要素持つ)。 */
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function weekdayLabel(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? '?';
}

/** チケット → PoolCandidateList の特別モード指定. */
function toSpecialTicketMode(t: SpecialPoolTicket): PoolCandidateSpecialTicket {
  return {
    markId: t.mark.id,
    weekday: t.mark.weekday,
    isoYear: t.mark.iso_year,
    isoWeek: t.mark.iso_week,
    serviceMinutes: t.service_minutes,
    lastPlacement: t.last_placement,
  };
}

// ---------------------------------------------------------------------------
// SpecialVisitPoolSection — 保留プール最上段の専用セクション
// ---------------------------------------------------------------------------

export interface SpecialVisitPoolSectionProps {
  isoYear: number;
  isoWeek: number;
  /** プールと同じ拠点スコープ. null = 全拠点。 */
  officeId: string | null;
  /** 配置ボタンを出すか (RBAC; admin/manager のみ)。 */
  canEdit: boolean;
}

export function SpecialVisitPoolSection({
  isoYear,
  isoWeek,
  officeId,
  canEdit,
}: SpecialVisitPoolSectionProps) {
  const poolQuery = useSpecialVisitPool(isoYear, isoWeek, officeId);
  const tickets = poolQuery.data ?? [];
  // 配置ポップアップの対象チケット (通常プール患者と同じ導線)。
  const [detailTicket, setDetailTicket] = React.useState<SpecialPoolTicket | null>(null);
  // 「カレンダー」で表示する設定モーダルの対象患者。
  const [calendarPatient, setCalendarPatient] = React.useState<{
    id: string;
    name: string;
  } | null>(null);

  // チケット 0 件ならセクションごと出さない (§6-2)。
  if (tickets.length === 0) return null;

  return (
    <div
      className="mb-2 rounded-md border-2 border-brand-primary bg-brand-primary/5 p-2"
      data-testid="special-visit-pool-section"
    >
      <div className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-brand-primary">
        <Star className="h-3.5 w-3.5" aria-hidden />⭐ 特別訪問週間
        <span className="tnum text-[10px] font-normal text-text-muted">({tickets.length}件)</span>
      </div>
      <ul className="space-y-1">
        {tickets.map((t) => {
          // 通常プールカードと同じ性別ウォッシュ (PatientCard の pal 分岐と同一トークン)。
          const pal = genderPalette(t.patient.sex);
          return (
            <li
              key={t.mark.id}
              className="flex items-start gap-1"
              data-testid={`special-visit-ticket-${t.mark.id}`}
            >
              <button
                type="button"
                onClick={() => setDetailTicket(t)}
                style={{
                  background: pal.bg,
                  borderColor: pal.ln,
                  borderLeftColor: pal.bar,
                  color: pal.ink,
                }}
                className="group flex min-w-0 flex-1 flex-col gap-0.5 rounded-lg border border-l-[3px] px-2 py-1 text-left text-xs shadow-[var(--shadow-xs)] transition-shadow hover:shadow-[var(--shadow-md)]"
                title={`${t.patient.name} 様の配置先を探す`}
                data-testid={`special-visit-ticket-card-${t.mark.id}`}
              >
                <div className="flex items-center gap-1">
                  <span className="shrink-0 text-[10px]" aria-hidden>
                    ⭐
                  </span>
                  <span className="truncate font-bold" title={t.patient.name}>
                    {t.patient.name}
                  </span>
                  {t.patient.code ? (
                    <span className="truncate text-[10px] opacity-70">({t.patient.code})</span>
                  ) : null}
                </div>
                <div className="flex flex-wrap items-center gap-1 pl-4">
                  <Badge
                    variant={t.mark.kind === 'displaced' ? 'warning' : 'info'}
                    className="h-4 px-1 text-[10px]"
                    data-testid={`special-visit-ticket-kind-${t.mark.id}`}
                  >
                    {t.mark.kind === 'displaced' ? '固定退避' : '追加枠'}
                  </Badge>
                  <Badge
                    variant="secondary"
                    className="h-4 px-1 text-[10px]"
                    data-testid={`special-visit-ticket-weekday-${t.mark.id}`}
                  >
                    {weekdayLabel(t.mark.weekday)}曜
                  </Badge>
                  <Badge variant="secondary" className="h-4 px-1 text-[10px]">
                    週{t.period.weekly_target}回以上
                  </Badge>
                </div>
              </button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setCalendarPatient({ id: t.patient.id, name: t.patient.name })}
                className="h-6 shrink-0 px-1.5 text-[10px]"
                title="特別訪問週間のカレンダーを開く"
                data-testid={`special-visit-ticket-calendar-${t.mark.id}`}
              >
                <CalendarDays className="mr-0.5 h-3 w-3" aria-hidden />
                カレンダー
              </Button>
            </li>
          );
        })}
      </ul>

      {/* 通常プール患者と同じポップアップ (ミニスケジュールで「ここに入れますか」)。 */}
      {detailTicket ? (
        <PatientScheduleDetailDialog
          patientId={detailTicket.patient.id}
          open
          onClose={() => setDetailTicket(null)}
          isoYear={isoYear}
          isoWeek={isoWeek}
          canEdit={canEdit}
          enablePoolProposal
          officeId={officeId}
          specialTicket={toSpecialTicketMode(detailTicket)}
        />
      ) : null}

      {calendarPatient ? (
        <SpecialVisitWeekDialog
          patientId={calendarPatient.id}
          patientName={calendarPatient.name}
          open
          onOpenChange={(v) => {
            if (!v) setCalendarPatient(null);
          }}
        />
      ) : null}
    </div>
  );
}
