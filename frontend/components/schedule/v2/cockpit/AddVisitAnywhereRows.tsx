'use client';

/**
 * AddVisitAnywhereRows — `AddVisitAnywhereDialog` の「日付ごとの 1 行」たち。
 *
 * 本体を読める大きさに保つための分割 (状態は持たない・すべて props)。
 *   - `DateProposalRow` … ④ 提案 1 日付ぶん（候補ラジオ・他拠点・M・理由欄）
 *   - `WeekSourceRow`   … ⑤ その週を変える の「動かす元」1 日付ぶん
 *   - `CandidateOption` … 候補 1 件のラジオ
 */
import * as React from 'react';

import { Input } from '@/components/ui/input';
import { proposeWarningLabel } from '@/lib/queries/fieldBoard';
import {
  excludedReasonLabel,
  formatDateLabel,
  type VisitLite,
} from '@/lib/scheduling/addVisitPlan';
import type { ProposeSlotItem } from '@/lib/schemas/v2/propose_slots';

/** 候補 1 件。`key` は並べ替えで変わらない安定キー (選択の保持に使う)。 */
export interface CandidateEntry {
  key: string;
  slot: ProposeSlotItem;
}

/** 1 日付ぶんの提案結果。 */
export interface DateProposal {
  /** 主担当拠点の候補 (BE のランキング順・定員超は後ろ)。 */
  primary: CandidateEntry[];
  /** 他拠点（要確認）の候補 (PO 決定 11)。 */
  other: CandidateEntry[];
  /** 主担当拠点で 0 件だった理由コード。 */
  excludedReason: string | null;
  /** 0 件だが理由も取れなかった (M1)。 */
  reasonUnavailable: boolean;
  /** その週の候補が `limit` に達した = 打ち切られている (M2)。 */
  truncated: boolean;
}

/** M（担当なし）を選んだことを表す選択キー。 */
export const M_KEY = '__M__';
/** その拠点に M テンプレートが無いときの表示。 */
export const M_FALLBACK_LABEL = '臨（コースなし）';

/**
 * ダイアログ内の `<select>` 共通クラス (設計 §3-5: 高さ 36px・文字 14px)。
 * `Input` の既定サイズ (h-10 text-sm) と同じ読み味に揃えるための単一ソース。
 */
export const selectCls =
  'block h-9 w-full rounded border border-border-default bg-bg-base px-3 text-sm disabled:opacity-50';

/** 候補・M の 1 行。ラベル全体がクリック領域 (設計 §3-5「ラジオ・チェック」)。 */
const optionRowCls =
  'flex cursor-pointer flex-wrap items-center gap-2 rounded px-2 py-1.5 hover:bg-bg-muted';

export function CandidateOption({
  date,
  entry,
  checked,
  disabled,
  onSelect,
}: {
  date: string;
  entry: CandidateEntry;
  checked: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  const { slot } = entry;
  return (
    <label className={optionRowCls}>
      <input
        type="radio"
        className="h-4 w-4"
        name={`ava-cand-${date}`}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        data-testid={`ava-cand-${date}-${entry.key}`}
      />
      <span className="text-sm font-semibold">
        {slot.course_label}
        {slot.staff_name ? `（${slot.staff_name}）` : '（担当なし）'}
      </span>
      {slot.partner_course_label ? (
        <span className="rounded bg-bg-muted px-1.5 py-0.5 text-xs">
          相方 {slot.partner_course_label}
          {slot.partner_staff_name ? `（${slot.partner_staff_name}）` : ''}
        </span>
      ) : null}
      {slot.overcapacity ? (
        <span className="rounded bg-warning-bg px-1.5 py-0.5 text-xs text-warning-strong">
          定員超
        </span>
      ) : null}
      {slot.warnings.map((w) => (
        <span key={w} className="rounded bg-bg-muted px-1.5 py-0.5 text-xs text-text-muted">
          ⚠ {proposeWarningLabel(w)}
        </span>
      ))}
    </label>
  );
}

export function DateProposalRow({
  date,
  startHM,
  proposal,
  selectedKey,
  mLabel,
  isMSelected,
  otherDisabledNote = null,
  otherOk,
  reason,
  error,
  disabled,
  orderCandidates,
  onSelect,
  onToggleOther,
  onReasonChange,
}: {
  date: string;
  startHM: string;
  /** 提案前 / 座標なしの患者では null（M だけを出す）。 */
  proposal: DateProposal | null;
  selectedKey: string;
  mLabel: string;
  isMSelected: boolean;
  /**
   * 他拠点（要確認）の候補を選べない理由 (H3)。反映先が「新しく 1 件追加」の
   * ときは `place-and-fix` が拠点跨ぎのテンプレートを 422 で拒むため塞ぐ。
   * null = 選べる。
   */
  otherDisabledNote?: string | null;
  otherOk: boolean;
  reason: string;
  /** この日の選択が登録できない理由 (コース未解決・相方未解決)。 */
  error: string | null;
  disabled: boolean;
  /** 希望担当のコースを先頭へ並べ替える (判定は変えない)。 */
  orderCandidates: (list: CandidateEntry[]) => CandidateEntry[];
  onSelect: (key: string) => void;
  onToggleOther: (checked: boolean) => void;
  onReasonChange: (value: string) => void;
}) {
  return (
    <div
      className="space-y-2 rounded-lg border border-border-default p-3 text-sm"
      data-testid={`ava-row-${date}`}
    >
      <div className="text-base font-semibold">
        {formatDateLabel(date)} {startHM}
      </div>

      {proposal && proposal.truncated ? (
        <p className="text-sm text-warning-strong" data-testid={`ava-truncated-${date}`}>
          候補が上限に達したため、この週は一部しか見えていません（日付を減らして探し直してください）
        </p>
      ) : null}

      {proposal && proposal.primary.length === 0 && !proposal.truncated ? (
        <p className="text-sm text-text-muted">
          主担当拠点に空きがありません
          {proposal.reasonUnavailable
            ? '（理由は取得できませんでした）'
            : proposal.excludedReason
              ? `（理由: ${excludedReasonLabel(proposal.excludedReason)}）`
              : ''}
        </p>
      ) : null}

      {proposal ? (
        <div className="space-y-0.5">
          {orderCandidates(proposal.primary).map((e) => (
            <CandidateOption
              key={e.key}
              date={date}
              entry={e}
              checked={selectedKey === e.key}
              disabled={disabled}
              onSelect={() => onSelect(e.key)}
            />
          ))}
        </div>
      ) : null}

      {proposal && proposal.other.length > 0 ? (
        <div className="space-y-1 rounded-md bg-bg-muted p-2">
          <p className="text-sm font-semibold">他拠点（要確認）</p>
          {otherDisabledNote ? (
            <p className="text-sm text-warning-strong" data-testid={`ava-other-blocked-${date}`}>
              {otherDisabledNote}
            </p>
          ) : (
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="h-4 w-4"
                checked={otherOk}
                onChange={(ev) => onToggleOther(ev.target.checked)}
                data-testid={`ava-other-office-${date}`}
              />
              拠点跨ぎを承知で入れる
            </label>
          )}
          <div className="space-y-0.5">
            {orderCandidates(proposal.other).map((e) => (
              <CandidateOption
                key={e.key}
                date={date}
                entry={e}
                checked={selectedKey === e.key}
                disabled={disabled || otherDisabledNote != null || !otherOk}
                onSelect={() => onSelect(e.key)}
              />
            ))}
          </div>
        </div>
      ) : null}

      <label className={optionRowCls}>
        <input
          type="radio"
          className="h-4 w-4"
          name={`ava-cand-${date}`}
          checked={selectedKey === M_KEY}
          onChange={() => onSelect(M_KEY)}
          disabled={disabled}
          data-testid={`ava-cand-${date}-${M_KEY}`}
        />
        <span className="text-sm font-semibold">{mLabel}</span>
      </label>

      {isMSelected ? (
        <Input
          value={reason}
          onChange={(ev) => onReasonChange(ev.target.value)}
          placeholder="理由（任意）"
          className="h-9 text-sm"
          data-testid={`ava-reason-${date}`}
          aria-label={`${formatDateLabel(date)} の M 配置理由`}
        />
      ) : null}

      {error ? (
        <p className="text-sm text-error" data-testid={`ava-row-error-${date}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function WeekSourceRow({
  date,
  candidates,
  selectedId,
  usedByOtherDates,
  disabled,
  onChange,
}: {
  date: string;
  /** その週の「動かせる」訪問すべて (planned・青ピンでない・当日以前でない)。 */
  candidates: VisitLite[];
  /** 割り当てられた元。'' = この日には残っていない → 新規追加になる。 */
  selectedId: string;
  /** 他の日付が既に使っている訪問 id (1 件を 2 日に使えない)。 */
  usedByOtherDates: ReadonlySet<string>;
  disabled: boolean;
  onChange: (visitId: string) => void;
}) {
  return (
    <div className="space-y-1 text-sm" data-testid={`ava-source-row-${date}`}>
      <span>{formatDateLabel(date)} に動かす元:</span>
      {candidates.length === 0 ? (
        <span className="text-warning-strong">
          この週に動かせる予定が無いため、新規追加として登録します
        </span>
      ) : selectedId === '' ? (
        <span className="text-warning-strong" data-testid={`ava-source-exhausted-${date}`}>
          この日は新規追加になります（動かせる予定が残っていません）
        </span>
      ) : (
        <select
          className={selectCls}
          value={selectedId}
          onChange={(ev) => onChange(ev.target.value)}
          disabled={disabled}
          data-testid={`ava-source-${date}`}
          aria-label={`${formatDateLabel(date)} に動かす元`}
        >
          {candidates.map((v) => (
            <option key={v.id} value={v.id} disabled={usedByOtherDates.has(v.id)}>
              {formatDateLabel(v.visit_date)} {v.start_time} {v.staff_name ?? '（担当なし）'}
              {v.course_label ? `・${v.course_label}` : ''}
              {usedByOtherDates.has(v.id) ? '（他の日で使用中）' : ''}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
