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
 * カードに `PatientCard` を直接使わないのは、患者プールカードとは載せる情報
 * (⭐ / 種別 / 曜日 / 週N回以上) が違うため (見た目だけを合わせる)。
 *
 * 2026-09-08 (PO 指摘「⭐ だけドラッグできない」・
 * `docs/plans/special-ticket-dnd-design-2026-09-08.md`):
 *   カードを `SpecialTicketCard` に切り出して dnd-kit の `useDraggable` を付けた。
 *   クリック (= 従来のポップアップ) との判別は `PatientCard` と同じ 6px 判定。
 *
 * 2026-09-08 後段 (`docs/plans/dnd-all-views-design-2026-09-08.md` §2-4): PO 指示
 *   「どこでも掴める / 置く瞬間に案内」に従い、初版の曜日ゲート (表示中の曜日タブと
 *   同じチケットしか掴めない) を撤去した。掴めないのは閲覧専用のときだけで、
 *   曜日が違う場所へ落としたときは盤面の「配置の確認」モーダルが問い直す。
 */
import * as React from 'react';
import { useDraggable } from '@dnd-kit/core';
import { CSS } from '@dnd-kit/utilities';
import { CalendarDays, Star } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

import { useSpecialVisitPool } from '@/lib/queries/specialVisitWeek';
import { genderPalette } from '@/lib/scheduling/timeline';
import { inactiveStatusLabel } from '@/lib/schemas/patient';
import type { SpecialPoolTicket } from '@/lib/schemas/specialVisitWeek';
import { cn } from '@/lib/utils';

import {
  buildSpecialTicketDraggableId,
  parseSpecialTicketDraggableId,
  specialTicketWeekdayLabel,
} from './courseDnd';
import { PatientScheduleDetailDialog } from './PatientScheduleDetailDialog';
import type { PoolCandidateSpecialTicket } from './PoolCandidateList';
import { SpecialVisitWeekDialog } from './SpecialVisitWeekDialog';

// id helper / 曜日ラベルは courseDnd.ts が単一ソース。⭐ を扱う呼び出し元 (盤面) が
// 「チケットのことは SpecialTicketPlacePanel から」で済むよう re-export する。
export { buildSpecialTicketDraggableId, parseSpecialTicketDraggableId, specialTicketWeekdayLabel };

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
// SpecialTicketCard — ⭐チケット 1 枚 (ドラッグ可能 + クリックでポップアップ)
// ---------------------------------------------------------------------------

export interface SpecialTicketCardProps {
  ticket: SpecialPoolTicket;
  /**
   * カード本体クリック (= ドラッグではない単純クリック) のハンドラ。
   * 従来の配置ポップアップ導線。ドラッグ不可のチケットでも押せる (唯一の配置手段)。
   */
  onCardClick?: () => void;
  /**
   * ドラッグ禁止 (閲覧専用)。クリック導線は残す。
   */
  dragDisabled?: boolean;
  /** `dragDisabled` のときに出す説明 (title 属性)。 */
  disabledTitle?: string;
  /**
   * DragOverlay 用ゴーストモード。draggable として登録せず (ghost- 接頭辞 + disabled)、
   * 掴んだカードと同じ内容を描く。
   */
  ghost?: boolean;
}

/**
 * ⭐チケットカード本体。`PatientCard` と同じ視覚言語 (性別ウォッシュ地 + 左色帯) に、
 * ⭐ / 種別 / 曜日 / 週N回以上 のバッジを載せる。
 */
export function SpecialTicketCard({
  ticket,
  onCardClick,
  dragDisabled = false,
  disabledTitle,
  ghost = false,
}: SpecialTicketCardProps) {
  const markId = ticket.mark.id;
  const draggableId = buildSpecialTicketDraggableId(markId);
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    // ghost は DragOverlay 内の描画専用 (実 draggable と id が衝突しないよう接頭辞)。
    id: ghost ? `ghost-overlay:${draggableId}` : draggableId,
    disabled: dragDisabled || ghost,
    data: { kind: 'special-ticket', markId },
  });

  // クリック / ドラッグ判別は PatientCard と同じ規則 (移植):
  //  (1) pointerdown からの移動量が PointerSensor の activationConstraint (6px) を
  //      超えていたらドラッグ扱いで click を無視する。
  //  (2) TouchSensor は長押し (250ms) 起点なので移動量が小さくてもドラッグが成立する。
  //      isDragging を ref に記憶して直後の click を握りつぶす。
  const pointerDownPos = React.useRef<{ x: number; y: number } | null>(null);
  const draggedRef = React.useRef(false);
  React.useEffect(() => {
    if (isDragging) draggedRef.current = true;
  }, [isDragging]);
  const handleClick = React.useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!onCardClick) return;
      if (draggedRef.current) {
        draggedRef.current = false;
        pointerDownPos.current = null;
        return;
      }
      const start = pointerDownPos.current;
      pointerDownPos.current = null;
      if (start && (Math.abs(e.clientX - start.x) > 6 || Math.abs(e.clientY - start.y) > 6)) {
        return; // ポインタ移動がしきい値超 = ドラッグ
      }
      onCardClick();
    },
    [onCardClick],
  );

  // 通常プールカードと同じ性別ウォッシュ (PatientCard の pal 分岐と同一トークン)。
  const pal = genderPalette(ticket.patient.sex);

  // 非稼働バッジ (Phase 2 / PO 決定 Q⭐)。プールからは除外せず、入院中などは
  // バッジ + 薄色で「このままでは置けない」ことを見せる (置こうとすれば入口ガードが問い直す)。
  const inactiveLabel = inactiveStatusLabel(ticket.patient.patient_status);

  return (
    <div
      ref={setNodeRef}
      style={{
        transform: ghost ? undefined : CSS.Translate.toString(transform),
        opacity: !ghost && isDragging ? 0.4 : inactiveLabel ? 0.65 : 1,
        background: pal.bg,
        borderColor: pal.ln,
        borderLeftColor: pal.bar,
        color: pal.ink,
      }}
      className={cn(
        'group flex min-w-0 flex-1 select-none touch-none flex-col gap-0.5 rounded-lg border border-l-[3px] px-2 py-1 text-left text-xs shadow-[var(--shadow-xs)] transition-shadow hover:shadow-[var(--shadow-md)]',
        ghost
          ? 'h-full w-full cursor-grabbing shadow-[var(--shadow-md)]'
          : dragDisabled
            ? // 閲覧専用: 掴めないことを控えめに示す (クリックは効く)。
              'cursor-pointer opacity-70'
            : 'cursor-grab active:cursor-grabbing',
      )}
      title={
        dragDisabled && disabledTitle ? disabledTitle : `${ticket.patient.name} 様の配置先を探す`
      }
      data-testid={`special-visit-ticket-card-${markId}`}
      /** ドラッグ可否はテスト / スタイル用の明示フラグ (a11y の aria-disabled とは別物)。 */
      data-drag-disabled={dragDisabled || ghost ? 'true' : 'false'}
      {...listeners}
      {...attributes}
      /*
       * dnd-kit の attributes は「掴めない = aria-disabled: true」を付けるが、
       * このカードはドラッグできなくてもクリック / Enter で配置ポップアップを
       * 開ける (他曜日タブでの唯一の導線)。支援技術に「使えない」と読ませないよう
       * spread の後で打ち消す。
       */
      aria-disabled={onCardClick ? undefined : (attributes['aria-disabled'] ?? undefined)}
      onPointerDownCapture={(e) => {
        pointerDownPos.current = { x: e.clientX, y: e.clientY };
      }}
      onClick={onCardClick ? handleClick : undefined}
      /*
       * キーボード操作: dnd-kit の attributes が role="button" / tabIndex=0 を付けるので、
       * Enter / Space でクリックと同じ導線を開けるようにする (盤面は KeyboardSensor を
       * 使っていないため listeners の onKeyDown とは衝突しない)。
       */
      onKeyDown={
        onCardClick
          ? (e) => {
              if (e.key !== 'Enter' && e.key !== ' ') return;
              e.preventDefault();
              onCardClick();
            }
          : undefined
      }
    >
      <div className="flex items-center gap-1">
        <span className="shrink-0 text-[10px]" aria-hidden>
          ⭐
        </span>
        <span className="truncate font-bold" title={ticket.patient.name}>
          {ticket.patient.name}
        </span>
        {ticket.patient.code ? (
          <span className="truncate text-[10px] opacity-70">({ticket.patient.code})</span>
        ) : null}
      </div>
      {/* PO 指示 2026-09-08: ⭐ が何のチケットで、時間が未定であることを
          カードの言葉で明示する (○ の意味をプール側でも揃える)。 */}
      <div className="pl-4 text-xs" data-testid={`special-visit-ticket-note-${markId}`}>
        {ticket.mark.kind === 'displaced' ? '固定退避・時間未定' : '特別訪問週間の追加枠・時間未定'}
      </div>
      <div className="flex flex-wrap items-center gap-1 pl-4">
        <Badge
          variant={ticket.mark.kind === 'displaced' ? 'warning' : 'info'}
          className="h-4 px-1 text-[10px]"
          data-testid={`special-visit-ticket-kind-${markId}`}
        >
          {ticket.mark.kind === 'displaced' ? '固定退避' : '追加枠'}
        </Badge>
        <Badge
          variant="secondary"
          className="h-4 px-1 text-[10px]"
          data-testid={`special-visit-ticket-weekday-${markId}`}
        >
          {specialTicketWeekdayLabel(ticket.mark.weekday)}曜
        </Badge>
        <Badge variant="secondary" className="h-4 px-1 text-[10px]">
          週{ticket.period.weekly_target}回以上
        </Badge>
        {inactiveLabel ? (
          <Badge
            variant="warning"
            className="h-4 px-1 text-[10px]"
            data-testid={`special-visit-ticket-inactive-${markId}`}
            title={`${ticket.patient.name} 様は${inactiveLabel}のため予定に入れられません`}
          >
            {inactiveLabel}
          </Badge>
        ) : null}
      </div>
    </div>
  );
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
          // 2026-09-08 (`dnd-all-views-design-2026-09-08.md` §2-4): 曜日ゲートを撤去。
          // どの曜日のチケットでもどのビューへでも掴んで運べる。曜日が違う場所へ
          // 落としたときは盤面が「配置の確認」モーダルで問い直す (唯一の砦)。
          const dragDisabled = !canEdit;
          const disabledTitle = dragDisabled ? '編集権限がありません' : undefined;
          return (
            <li
              key={t.mark.id}
              className="flex items-start gap-1"
              data-testid={`special-visit-ticket-${t.mark.id}`}
            >
              <SpecialTicketCard
                ticket={t}
                onCardClick={() => setDetailTicket(t)}
                dragDisabled={dragDisabled}
                disabledTitle={disabledTitle}
              />
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
