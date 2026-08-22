'use client';

/**
 * VisitActionMenu — 訪問クリックのポップオーバー (週空間 Phase E・運転席)。
 *
 * モック `docs/mockups/staff-schedule-week-cockpit-mock.html` の `openItemMenu()` を
 * 部品化したもの。**API は呼ばない**: すべて props のコールバックで親 (FE-C) へ渡す。
 *
 *   🗑 今週だけ取消 / ↶ 取消をやめる  … visits.status planned↔cancelled
 *   👤 担当変更                       … visit-assign-staff-week
 *   🕘 時刻変更 (15分)                … visit-move-week-only
 *   📅 曜日移動                       … visit-move-week-only (曜日跨ぎ)
 *   📐 型も変える…                    … ChangeScopeChoice へ委譲 (マスタ昇格)
 *
 * 憲法1: ここから出る操作は「今週だけ」。マスタ(PFV/テンプレ)に触るのは
 * 「📐 型も変える…」を選んだときだけ。
 */
import * as React from 'react';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { fmtMd, weekdayOfIso } from './reconcileMarkers';

/** 曜日ラベルは reconcileMarkers.fmtMd と同じ 7 要素 (日曜が空文字にならないように)。 */
const WD_JA = ['月', '火', '水', '木', '金', '土', '日'] as const;
/** 「曜日移動」の行き先は盤面と同じ 月〜土 のみ。 */
const MOVE_WEEKDAYS = WD_JA.slice(0, 6);

/** 8:00〜18:45 の 15 分刻み (モックと同じ 44 個)。 */
export const TIME_OPTIONS: string[] = Array.from({ length: 44 }, (_, i) => {
  const v = 8 * 60 + i * 15;
  return `${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`;
});

/** 出所チップの表記 (盤面上部の集計と同じ語彙)。 */
const SOURCE_JA: Record<string, string> = {
  manual_week: '今週だけ',
  // BE が「今週だけ取消」に付ける source。取消も「今週だけ」の操作。
  manual_cancel: '今週だけ',
  import: 'カイポケ由来',
  manual: '型どおり',
  auto: '型どおり',
  ai: '型どおり',
  fixed: '🔒固定',
};

export interface VisitActionMenuVisit {
  id: string;
  patient_name: string;
  /** YYYY-MM-DD。 */
  date: string;
  /** HH:MM。 */
  start_time: string;
  end_time: string;
  course_label: string | null;
  /** planned / cancelled / completed / no_show。 */
  status: string;
  /** manual / auto / import / ai / manual_week。 */
  source: string;
  staff_id: string | null;
  /** 青ピン = 今週の蓋。人手操作も BE が 422 で拒否する。 */
  week_pinned: boolean;
  /**
   * ●未送信 (カイポケへまだ送っていない)。
   * `null` = まだ分からない (同期バーが未送信を数えていない) — 「✓同期済」と
   * 言い切ると嘘になるので、そのときは「同期バーで確認」と出す。
   */
  unsent: boolean | null;
}

export interface VisitActionMenuProps {
  visit: VisitActionMenuVisit;
  /** 担当変更の候補 (「（担当なし）」は内部で足す)。 */
  staffOptions: { id: string; name: string }[];
  /** JST 基準で当日以前か。true = 送信対象外の警告を出す。 */
  isPast: boolean;
  /** false で全操作を無効化 (閲覧ロール)。既定 true。 */
  canEdit?: boolean;
  /** ポップオーバーのトリガー (盤面の訪問行をそのまま渡す)。 */
  children: React.ReactNode | ((args: { isOpen: boolean }) => React.ReactNode);
  /** テスト/ストーリー用の初期表示。 */
  defaultOpen?: boolean;
  /** 今週だけ取消 (cancel=true) / 取消をやめる (cancel=false)。 */
  onCancelToggle: (cancel: boolean) => void;
  /** null = （担当なし）へ戻す。 */
  onChangeStaff: (staffId: string | null) => void;
  /** 開始時刻 'HH:MM' (所要時間は据え置き)。 */
  onChangeTime: (start: string) => void;
  /** 0=月 .. 5=土。 */
  onMoveWeekday: (weekday: number) => void;
  /** 「📐 型も変える…」= ChangeScopeChoice ダイアログを親が開く。 */
  onChangeMaster: () => void;
}

function weekdayOf(dateIso: string): number {
  return weekdayOfIso(dateIso);
}

/** 見出しの日付表記は盤面/差分カードと同じ `fmtMd` に統一する。 */
const fmtHead = fmtMd;

const rowLabel = 'w-[74px] shrink-0 text-[11px] text-text-secondary';
const selectCls =
  'min-w-0 flex-1 rounded border border-border-default bg-bg-base px-1.5 py-1 text-[11px] disabled:opacity-50';

export function VisitActionMenu({
  visit,
  staffOptions,
  isPast,
  canEdit = true,
  children,
  defaultOpen = false,
  onCancelToggle,
  onChangeStaff,
  onChangeTime,
  onMoveWeekday,
  onChangeMaster,
}: VisitActionMenuProps) {
  const [open, setOpen] = React.useState(defaultOpen);
  const cancelled = visit.status === 'cancelled';
  const locked = visit.week_pinned;
  const disabled = !canEdit || locked;
  const curWeekday = weekdayOf(visit.date);
  // 当日以前は実績が付いている可能性があるため取消できない (BE も 422)。
  const cancelDisabled = disabled || (!cancelled && isPast);
  const curStaffValue = visit.staff_id ?? '__none__';
  /** 現在値が 8:00〜18:45 の外 (早朝/夜間) でも選択肢に含める。 */
  const timeOptions = React.useMemo(
    () =>
      TIME_OPTIONS.includes(visit.start_time)
        ? TIME_OPTIONS
        : [...TIME_OPTIONS, visit.start_time].sort(),
    [visit.start_time],
  );

  const close = () => setOpen(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {typeof children === 'function' ? children({ isOpen: open }) : children}
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-72 p-0"
        data-testid="visit-action-menu"
        aria-label={`${visit.patient_name} の訪問メニュー`}
      >
        <div className="border-b border-border-subtle px-3 py-2">
          <p className="text-xs font-bold text-text-primary">訪問｜{visit.patient_name}</p>
          <p className="tnum text-[11px] text-text-muted">
            {fmtHead(visit.date)} {visit.start_time}〜{visit.end_time}
            {visit.course_label ? ` ${visit.course_label}` : ''}
          </p>
        </div>

        <div className="space-y-1.5 px-3 py-2">
          {locked ? (
            <p
              className="rounded bg-warning-bg px-2 py-1 text-[11px] text-warning-strong"
              data-testid="visit-action-week-pinned"
            >
              📌 青ピン（今週の蓋）が付いています。解除するまで変更できません
            </p>
          ) : null}

          {cancelled ? (
            <button
              type="button"
              disabled={disabled}
              data-testid="visit-action-uncancel"
              onClick={() => {
                onCancelToggle(false);
                close();
              }}
              className="w-full rounded border border-border-default px-2 py-1.5 text-left text-[12px] hover:border-brand-primary/50 disabled:opacity-50"
            >
              ↶ 取消をやめる
            </button>
          ) : (
            <button
              type="button"
              disabled={cancelDisabled}
              data-testid="visit-action-cancel"
              title={isPast ? '当日以前は取消できません（明日以降の予定のみ）' : undefined}
              onClick={() => {
                onCancelToggle(true);
                close();
              }}
              className="w-full rounded border border-error/40 bg-error-bg px-2 py-1.5 text-left text-[12px] text-error hover:border-error disabled:opacity-50"
            >
              🗑 今週だけ取消
              <span className="ml-1 text-[10px] text-text-muted">
                {isPast
                  ? '当日以前は取消できません（明日以降の予定のみ）'
                  : 'カイポケでも削除（送信時）'}
              </span>
            </button>
          )}

          <div className="flex items-center gap-1.5">
            <span className={rowLabel}>👤 担当変更</span>
            <select
              className={selectCls}
              disabled={disabled}
              defaultValue={curStaffValue}
              data-testid="visit-action-staff"
              aria-label="担当変更"
              onChange={(e) => {
                const v = e.target.value;
                if (v === '' || v === curStaffValue) return;
                onChangeStaff(v === '__none__' ? null : v);
                close();
              }}
            >
              {staffOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.id === visit.staff_id ? '（現在）' : ''}
                </option>
              ))}
              <option value="__none__">
                （担当なし）{visit.staff_id == null ? '（現在）' : ''}
              </option>
            </select>
          </div>

          <div className="flex items-center gap-1.5">
            <span className={rowLabel}>🕘 時刻変更</span>
            <select
              className={cn(selectCls, 'tnum')}
              disabled={disabled}
              defaultValue={visit.start_time}
              data-testid="visit-action-time"
              aria-label="時刻変更"
              onChange={(e) => {
                const v = e.target.value;
                if (v === '' || v === visit.start_time) return;
                onChangeTime(v);
                close();
              }}
            >
              {timeOptions.map((t) => (
                <option key={t} value={t}>
                  {t}
                  {t === visit.start_time ? '（現在）' : ''}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-1.5">
            <span className={rowLabel}>📅 曜日移動</span>
            <select
              className={selectCls}
              disabled={disabled}
              defaultValue={String(curWeekday)}
              data-testid="visit-action-weekday"
              aria-label="曜日移動"
              onChange={(e) => {
                const v = e.target.value;
                if (v === '' || Number.parseInt(v, 10) === curWeekday) return;
                onMoveWeekday(Number.parseInt(v, 10));
                close();
              }}
            >
              {MOVE_WEEKDAYS.map((w, i) => (
                <option key={w} value={i}>
                  {w}
                  {i === curWeekday ? '（現在）' : ''}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            disabled={!canEdit}
            data-testid="visit-action-master"
            onClick={() => {
              onChangeMaster();
              close();
            }}
            className="w-full rounded border border-border-default px-2 py-1.5 text-left text-[12px] hover:border-brand-primary/50 disabled:opacity-50"
          >
            📐 型も変える…
            <span className="ml-1 text-[10px] text-text-muted">マスタへ昇格（2択ダイアログ）</span>
          </button>
        </div>

        <div
          className="border-t border-border-subtle px-3 py-1.5 text-[11px] text-text-muted"
          data-testid="visit-action-footer"
        >
          出所: <b>{SOURCE_JA[visit.source] ?? '型どおり'}</b> ／ 同期:{' '}
          <b>{visit.unsent == null ? '同期バーで確認' : visit.unsent ? '●未送信' : '✓同期済'}</b>
          {isPast ? (
            <span className="mt-0.5 block text-warning-strong" data-testid="visit-action-past-warn">
              ⚠ 当日以前は実績の可能性があるため送信対象外（表示のみ変更）
            </span>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
