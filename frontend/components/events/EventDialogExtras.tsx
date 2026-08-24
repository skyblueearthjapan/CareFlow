'use client';

/**
 * イベント追加ダイアログの共通パーツ (Wave 2-D)。
 *
 * 正典 = docs/plans/staff-event-history-design.md §2 Phase 2/3 /
 *        docs/mockups/event-add-dialog-mock.html (ピクセルの正典)。
 *
 * スケジュール画面の `TimelineEventAddDialog` (複数スタッフ一括) と
 * スタッフマスタの `EventAddDialog` (1名宛) の両方に同じ 3 点を足すため、
 * 見た目と状態の形をここに 1 本化する:
 *
 *   1. `EventTemplateBar`     — 📋 ひな形から選ぶ (共通 / 個人)
 *   2. `EventDialogOptions`   — ☆ ひな形に保存 / 📌 毎週の固定イベントにする
 *   3. 曜日ユーティリティ (0=月 … 5=土 — BE `staff_event_defaults.weekday` と同じ)
 *
 * ひな形は「入力の型」であって FK ではない。反映後の手直しは自由で、
 * 作成されるイベントとひな形の間に関連は残らない。
 */
import * as React from 'react';

import { useEventTemplates, type EventTemplateRead } from '@/lib/queries/event-templates';
import type { EventType } from '@/lib/schemas/staff-events';

/** BE `staff_event_defaults.weekday` と同じ並び (0=月 … 5=土)。 */
export const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

export const ALL_WEEKDAYS = [0, 1, 2, 3, 4, 5];

/**
 * `yyyy-MM-dd` → 0=月 … 5=土。日曜と不正値は月曜 (0) にフォールバックする
 * (固定イベントは月〜土のみ。既定値なので利用者はチップで直せる)。
 */
/**
 * ISO 日付 → 固定イベント曜日 (0=月…5=土)。日曜・不正値は **null**
 * (固定イベントは日曜を定義できないため既定選択なし。レビュー指摘:
 * ⋯メニューの toEventDefaultWeekday と同じ規約に統一)。
 */
export function weekdayFromIsoDate(iso: string | undefined | null): number | null {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return null;
  const [y, m, d] = iso.split('-').map(Number);
  const day = new Date(y!, m! - 1, d!).getDay(); // 0=日 … 6=土
  return day === 0 ? null : day - 1;
}

/** weekdayFromIsoDate の結果を既定選択配列へ (null=選択なし)。 */
export function defaultWeekdaysFor(iso: string | undefined | null): number[] {
  const w = weekdayFromIsoDate(iso);
  return w === null ? [] : [w];
}

/** ひな形の `event_type` → ダイアログの種別 (研修 / イベント)。 */
export function templateTypeToEventType(t: EventTemplateRead): EventType {
  return t.event_type === 'training' ? '研修' : 'イベント';
}

/** ダイアログの種別 → ひな形の `event_type`。 */
export function eventTypeToTemplateType(type: EventType): 'event' | 'training' {
  return type === '研修' ? 'training' : 'event';
}

// ---------------------------------------------------------------------------
// 1. ひな形バー
// ---------------------------------------------------------------------------

export interface EventTemplateBarProps {
  /**
   * 個人ひな形を出す対象。null なら共通ひな形だけを出す。
   * (タイムライン側は「ちょうど 1 名選択中」のときだけ ID が入る)
   */
  staffId: string | null;
  /**
   * 個人グループの見出しに使う名前 (`{name}さんの個人ひな形`)。
   * 未指定なら「このスタッフの個人ひな形」(スタッフマスタ側は文脈で自明)。
   */
  staffName?: string | null;
  /** 選択されたひな形をフォームへ反映する。 */
  onApply: (template: EventTemplateRead) => void;
  /** data-testid の接頭辞 (ダイアログごとに衝突させない)。 */
  testIdPrefix: string;
}

export function EventTemplateBar({
  staffId,
  staffName,
  onApply,
  testIdPrefix,
}: EventTemplateBarProps) {
  const { data: templates = [] } = useEventTemplates({ staffId });
  const [selectedId, setSelectedId] = React.useState('');
  const [applied, setApplied] = React.useState<EventTemplateRead | null>(null);

  const shared = templates.filter((t) => t.is_shared);
  const personal = staffId ? templates.filter((t) => !t.is_shared && t.staff_id === staffId) : [];

  // 対象スタッフが変わったら個人ひな形の選択は無効になり得る → 選択を戻す。
  React.useEffect(() => {
    setSelectedId('');
    setApplied(null);
  }, [staffId]);

  const handleChange = (value: string) => {
    setSelectedId(value);
    const tpl = templates.find((t) => t.id === value) ?? null;
    setApplied(tpl);
    if (tpl) onApply(tpl);
  };

  if (shared.length === 0 && personal.length === 0) return null;

  return (
    <div
      className="rounded-lg border border-brand-primary-light bg-brand-primary-50 px-3 py-2"
      data-testid={`${testIdPrefix}-template-bar`}
    >
      <label
        className="mb-1 block text-xs font-bold text-brand-primary"
        htmlFor={`${testIdPrefix}-template-select`}
      >
        📋 ひな形から選ぶ（任意）
      </label>
      <select
        id={`${testIdPrefix}-template-select`}
        value={selectedId}
        onChange={(e) => handleChange(e.target.value)}
        data-testid={`${testIdPrefix}-template-select`}
        className="w-full rounded border border-brand-primary-light bg-bg-base px-2 py-1 text-sm text-text-primary"
      >
        <option value="">— ひな形を使わず手入力 —</option>
        {shared.length > 0 && (
          <optgroup label="共通">
            {shared.map((t) => (
              <option key={t.id} value={t.id}>
                {templateOptionLabel(t)}
              </option>
            ))}
          </optgroup>
        )}
        {personal.length > 0 && (
          <optgroup label={staffName ? `${staffName}さんの個人ひな形` : 'このスタッフの個人ひな形'}>
            {personal.map((t) => (
              <option key={t.id} value={t.id}>
                {templateOptionLabel(t)}
              </option>
            ))}
          </optgroup>
        )}
      </select>
      {applied ? (
        <p
          className="mt-1 text-[11px] font-bold text-success"
          data-testid={`${testIdPrefix}-template-applied`}
        >
          ✓ ひな形の内容を反映しました（手直しできます）
          {applied.blocking ? '／🔒付き（絶対に潰せないイベント）' : ''}
        </p>
      ) : null}
    </div>
  );
}

function templateOptionLabel(t: EventTemplateRead): string {
  const time = t.start_time && t.end_time ? `${t.start_time}〜${t.end_time}` : '時間はその場で';
  const type = t.event_type === 'training' ? '研修・' : '';
  return `${t.title}${t.blocking ? ' 🔒' : ''}（${type}${time}）`;
}

// ---------------------------------------------------------------------------
// 2. ☆ひな形に保存 / 📌毎週固定
// ---------------------------------------------------------------------------

export interface EventDialogOptionsValue {
  saveTemplate: boolean;
  /** 'shared' = 事業所共通 / 'personal' = 対象スタッフの個人ひな形。 */
  templateScope: 'shared' | 'personal';
  fixWeekly: boolean;
  /** 0=月 … 5=土。 */
  weekdays: number[];
}

export function initialOptionsValue(date: string | undefined): EventDialogOptionsValue {
  return {
    saveTemplate: false,
    templateScope: 'shared',
    fixWeekly: false,
    weekdays: defaultWeekdaysFor(date),
  };
}

export interface EventDialogOptionsProps {
  value: EventDialogOptionsValue;
  onChange: (next: EventDialogOptionsValue) => void;
  /** admin 以外は両方無効化 (BE が 403 を返すため事前に止める)。 */
  canEdit: boolean;
  /**
   * 個人ひな形の保存先ラベル (`{name}さんの個人ひな形`)。
   * null なら「共通」しか選べない (複数スタッフ選択中など)。
   */
  personalScopeLabel: string | null;
  /** 📌 の対象説明 (例:「選択スタッフ全員」/「このスタッフ」)。 */
  fixWeeklyTargetLabel: string;
  testIdPrefix: string;
}

export function EventDialogOptions({
  value,
  onChange,
  canEdit,
  personalScopeLabel,
  fixWeeklyTargetLabel,
  testIdPrefix,
}: EventDialogOptionsProps) {
  const disabledTitle = canEdit ? undefined : '管理者のみ設定できます';

  const toggleWeekday = (w: number) => {
    const next = value.weekdays.includes(w)
      ? value.weekdays.filter((x) => x !== w)
      : [...value.weekdays, w].sort((a, b) => a - b);
    onChange({ ...value, weekdays: next });
  };

  return (
    <div
      className="grid gap-2 border-t border-dashed border-border-default pt-3"
      data-testid={`${testIdPrefix}-options`}
    >
      {/* ☆ ひな形に保存 */}
      <div className="text-sm">
        <label className="flex cursor-pointer items-start gap-2" title={disabledTitle}>
          <input
            type="checkbox"
            className="mt-0.5"
            checked={value.saveTemplate}
            disabled={!canEdit}
            onChange={(e) => onChange({ ...value, saveTemplate: e.target.checked })}
            data-testid={`${testIdPrefix}-save-template`}
          />
          <span>☆ この内容をひな形に保存する</span>
        </label>
        {value.saveTemplate ? (
          <div className="ml-6 mt-1 flex items-center gap-2 text-xs text-text-muted">
            <span>保存先:</span>
            <select
              value={value.templateScope}
              onChange={(e) =>
                onChange({ ...value, templateScope: e.target.value as 'shared' | 'personal' })
              }
              aria-label="ひな形の保存先"
              data-testid={`${testIdPrefix}-template-scope`}
              className="rounded border border-border-default bg-bg-base px-1.5 py-0.5 text-xs text-text-primary"
            >
              <option value="shared">共通（全員のプルダウンに出す）</option>
              {personalScopeLabel ? <option value="personal">{personalScopeLabel}</option> : null}
            </select>
          </div>
        ) : null}
      </div>

      {/* 📌 毎週の固定イベント */}
      <div className="text-sm">
        <label className="flex cursor-pointer items-start gap-2" title={disabledTitle}>
          <input
            type="checkbox"
            className="mt-0.5"
            checked={value.fixWeekly}
            disabled={!canEdit}
            onChange={(e) => onChange({ ...value, fixWeekly: e.target.checked })}
            data-testid={`${testIdPrefix}-fix-weekly`}
          />
          <span>
            📌 毎週の固定イベントにする（{fixWeeklyTargetLabel}）
            <span className="block text-[11px] text-text-muted">
              — 週生成のたびに自動でこの予定が入ります
            </span>
          </span>
        </label>
        {value.fixWeekly ? (
          <div className="ml-6 mt-1 flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
            <span>曜日:</span>
            {WEEKDAY_LABELS.map((label, w) => {
              const on = value.weekdays.includes(w);
              return (
                <button
                  key={label}
                  type="button"
                  onClick={() => toggleWeekday(w)}
                  aria-pressed={on}
                  data-testid={`${testIdPrefix}-weekday-${w}`}
                  className={`rounded-full border px-2 py-0.5 text-xs ${
                    on
                      ? 'border-brand-primary bg-brand-primary font-bold text-white'
                      : 'border-border-default bg-bg-base text-text-primary'
                  }`}
                >
                  {label}
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => onChange({ ...value, weekdays: [...ALL_WEEKDAYS] })}
              data-testid={`${testIdPrefix}-weekday-all`}
              className="rounded border border-border-default bg-bg-base px-2 py-0.5 text-xs text-text-primary"
            >
              毎日(月〜土)
            </button>
          </div>
        ) : null}
        {value.fixWeekly && value.weekdays.length === 0 ? (
          <p
            className="text-xs font-medium text-warning-strong"
            data-testid={`${testIdPrefix}-weekday-warning`}
          >
            曜日を1つ以上選んでください（未選択のままだと固定イベントは登録されません）
          </p>
        ) : null}
      </div>
    </div>
  );
}
