'use client';

/**
 * PinScopeMenu — Phase G-47: 個別 🔒 toggle に「曜日のみ / 全曜日」 選択肢を提供する.
 *
 * 仕様 (CareFlow Phase G-47):
 *   Before: 🔒 button click 即時 toggle (= 当該 PFV のみ ON/OFF)
 *   After:  🔒 button click → Popover で 2 択メニュー
 *           ・「この曜日のみ {ロック/解除}」 (= 既存挙動, 当該 PFV だけ)
 *           ・「この患者の全曜日 {ロック/解除}」 (= 患者の全 PFV を bulk)
 *
 * 配置箇所 (3 箇所共通):
 *   - CourseDayTable.OccupantPatientInfo   (各曜日テーブルの visit 行)
 *   - CourseWeekOverview                   (週ビューの visit 行)
 *   - WeekdayScheduleCard.PinToggleButton  (リスト表示の visit 行)
 *
 * 反映先 (自動):
 *   - React Query invalidate (patients / courses / visits) で全 UI が再 fetch される.
 *   - 親 (CourseDayTablePanel.handleTogglePin) が scope に応じて単一 PATCH / bulk POST を発火.
 *
 * デザイン方針:
 *   - 「品格 + シンプル」: shadcn/ui の既存 Popover (@/components/ui/popover) を使用.
 *   - DropdownMenu ライブラリ追加なし (= bundle 増 / 依存追加なし).
 *   - trigger は children prop パターン — 各呼び出し側が既存のピン留めボタン見た目を
 *     完全維持できる (全 UI 共通の PushPin/PushPinOff アイコンを使用).
 */
import * as React from 'react';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { PushPin, PushPinOff } from '@/components/ui/push-pin';
import { cn } from '@/lib/utils';

// ─────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────

/**
 * 🔒 toggle のスコープ.
 *   - 'day'      : 当該 PFV (= 1 曜日 1 行) だけ更新.
 *   - 'all-days' : 当該患者の全 PFV (= 全曜日) を bulk 更新.
 */
export type PinScope = 'day' | 'all-days';

/**
 * PinScopeMenu の trigger を render するための callback 引数.
 *
 * 各呼び出し側 (CourseDayTable / CourseWeekOverview / WeekdayScheduleCard) は
 * 既存の Lock / Unlock ボタン JSX をそのまま `children` (render-prop) として渡し、
 * 外側 wrapper で props を spread することで Popover の anchor になる.
 *
 * - `triggerProps`: PopoverTrigger asChild 用の props (onClick / aria-* / data-* 等).
 *   ※ children 側で 別 onClick を持つ場合は内部で stopPropagation してから
 *     `triggerProps.onClick?.(e)` を呼ぶ必要がある (Radix の挙動).
 */
export interface PinScopeMenuRenderArgs {
  /** 現在 menu が開いているか. trigger 見た目の active 状態切替に使える. */
  isOpen: boolean;
}

export interface PinScopeMenuProps {
  /** 当該 PFV の id (= 'day' 選択時に渡る). */
  pfvId: string;
  /** 当該患者の id (= 'all-days' 選択時に bulk 対象患者として渡る). */
  patientId: string;
  /** 現在の is_pinned 状態. menu の表記 (ロック / 解除) を切り替える. */
  isPinned: boolean;
  /**
   * scope 選択時に呼ばれるハンドラ.
   * - scope='day'      → 当該 PFV だけ反転 (= 既存個別 toggle と同じ).
   * - scope='all-days' → 当該患者の全 PFV を bulk 反転.
   *
   * 親 (CourseDayTablePanel) が単一 PATCH / bulk POST を振り分ける.
   */
  onSelect: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
  /**
   * trigger ボタン本体. 既存 🔒/🔓 ボタン JSX を渡す (= 見た目の継続性確保).
   * クリック判定は Popover が制御するため、内部の `<button>` は装飾と
   * aria 属性のみ持てばよい (onClick 不要 — Popover 側で発火する).
   *
   * NOTE: トリガを `<button>` として描画したい場合は `<PopoverTrigger asChild>`
   * の制約により単一の React element を返すこと.
   */
  children: (args: PinScopeMenuRenderArgs) => React.ReactElement;
  /**
   * disabled (= fixed_visit_id が無い等). true のとき menu は開かず children の
   * disabled state を尊重する.
   */
  disabled?: boolean;
  /** testid 末尾識別子 (= visit id 等). data-testid: pin-scope-menu-{suffix}. */
  testIdSuffix?: string;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function PinScopeMenu({
  pfvId,
  patientId,
  isPinned,
  onSelect,
  children,
  disabled,
  testIdSuffix,
}: PinScopeMenuProps) {
  const [open, setOpen] = React.useState(false);

  // 統合 (PO 決定 2026-08-09): 赤ピン = 完全固定の表示。文言も「完全固定」へ統一。
  const dayLabel = isPinned ? 'この曜日のみ 完全固定を解除' : 'この曜日のみ 完全固定';
  const allLabel = isPinned ? 'この患者の全曜日 完全固定を解除' : 'この患者の全曜日 完全固定';
  // アイコンは「実行される操作」を表す: 未固定 → これから完全固定 (PushPin), 完全固定中 → 外す (PushPinOff).
  const actionIcon = isPinned ? (
    <PushPinOff className="h-3.5 w-3.5 text-text-muted" aria-hidden />
  ) : (
    <PushPin className="h-3.5 w-3.5 text-red-600" aria-hidden />
  );

  const handleSelect = (scope: PinScope) => {
    setOpen(false);
    if (disabled) return;
    onSelect(pfvId, !isPinned, scope, patientId);
  };

  // disabled の場合は menu を開かない (= 通常の disabled button 挙動).
  if (disabled) {
    return children({ isOpen: false });
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{children({ isOpen: open })}</PopoverTrigger>
      <PopoverContent
        align="end"
        side="bottom"
        sideOffset={4}
        className="w-auto min-w-[200px] p-1"
        // dnd-kit の pointerdown を内側で捕まえないよう event 伝搬を止める
        // (= popover 内 click が drag 起動扱いされる事故防止).
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
        data-testid={testIdSuffix ? `pin-scope-menu-${testIdSuffix}` : 'pin-scope-menu'}
      >
        <ul className="flex flex-col gap-0.5" role="menu">
          <li role="none">
            <button
              type="button"
              role="menuitem"
              onClick={() => handleSelect('day')}
              className={cn(
                'flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs',
                'hover:bg-bg-muted focus:bg-bg-muted focus:outline-none',
              )}
              data-testid={
                testIdSuffix ? `pin-scope-menu-day-${testIdSuffix}` : 'pin-scope-menu-day'
              }
            >
              {actionIcon}
              <span>{dayLabel}</span>
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              role="menuitem"
              onClick={() => handleSelect('all-days')}
              className={cn(
                'flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs',
                'hover:bg-bg-muted focus:bg-bg-muted focus:outline-none',
              )}
              data-testid={
                testIdSuffix ? `pin-scope-menu-all-days-${testIdSuffix}` : 'pin-scope-menu-all-days'
              }
            >
              {actionIcon}
              <span>{allLabel}</span>
            </button>
          </li>
        </ul>
      </PopoverContent>
    </Popover>
  );
}
