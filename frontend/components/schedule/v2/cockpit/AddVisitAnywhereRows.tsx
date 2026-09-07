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

export const selectCls =
  'block w-full rounded border border-border-default bg-bg-base px-2 py-1 text-xs disabled:opacity-50';

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
    <label className="flex flex-wrap items-center gap-1">
      <input
        type="radio"
        name={`ava-cand-${date}`}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        data-testid={`ava-cand-${date}-${entry.key}`}
      />
      <span>
        {slot.course_label}
        {slot.staff_name ? `（${slot.staff_name}）` : '（担当なし）'}
      </span>
      {slot.partner_course_label ? (
        <span className="rounded bg-bg-muted px-1 text-[10px]">
          相方 {slot.partner_course_label}
          {slot.partner_staff_name ? `（${slot.partner_staff_name}）` : ''}
        </span>
      ) : null}
      {slot.overcapacity ? (
        <span className="rounded bg-warning-bg px-1 text-[10px] text-warning-strong">定員超</span>
      ) : null}
      {slot.warnings.map((w) => (
        <span key={w} className="rounded bg-bg-muted px-1 text-[10px] text-text-muted">
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
      className="rounded border border-border-default p-2 text-[11px]"
      data-testid={`ava-row-${date}`}
    >
      <div className="font-semibold">
        {formatDateLabel(date)} {startHM}
      </div>

      {proposal && proposal.truncated ? (
        <p className="text-warning-strong" data-testid={`ava-truncated-${date}`}>
          候補が上限に達したため、この週は一部しか見えていません（日付を減らして探し直してください）
        </p>
      ) : null}

      {proposal && proposal.primary.length === 0 && !proposal.truncated ? (
        <p className="text-text-muted">
          主担当拠点に空きがありません
          {proposal.reasonUnavailable
            ? '（理由は取得できませんでした）'
            : proposal.excludedReason
              ? `（理由: ${excludedReasonLabel(proposal.excludedReason)}）`
              : ''}
        </p>
      ) : null}

      {proposal
        ? orderCandidates(proposal.primary).map((e) => (
            <CandidateOption
              key={e.key}
              date={date}
              entry={e}
              checked={selectedKey === e.key}
              disabled={disabled}
              onSelect={() => onSelect(e.key)}
            />
          ))
        : null}

      {proposal && proposal.other.length > 0 ? (
        <div className="mt-1 rounded bg-bg-muted p-1">
          <p className="font-semibold">他拠点（要確認）</p>
          {otherDisabledNote ? (
            <p className="text-warning-strong" data-testid={`ava-other-blocked-${date}`}>
              {otherDisabledNote}
            </p>
          ) : (
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={otherOk}
                onChange={(ev) => onToggleOther(ev.target.checked)}
                data-testid={`ava-other-office-${date}`}
              />
              拠点跨ぎを承知で入れる
            </label>
          )}
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
      ) : null}

      <label className="flex items-center gap-1">
        <input
          type="radio"
          name={`ava-cand-${date}`}
          checked={selectedKey === M_KEY}
          onChange={() => onSelect(M_KEY)}
          disabled={disabled}
          data-testid={`ava-cand-${date}-${M_KEY}`}
        />
        {mLabel}
      </label>

      {isMSelected ? (
        <Input
          value={reason}
          onChange={(ev) => onReasonChange(ev.target.value)}
          placeholder="理由（任意）"
          className="mt-1 h-6 text-[11px]"
          data-testid={`ava-reason-${date}`}
          aria-label={`${formatDateLabel(date)} の M 配置理由`}
        />
      ) : null}

      {error ? (
        <p className="mt-1 text-error" data-testid={`ava-row-error-${date}`}>
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
    <div className="text-[11px]" data-testid={`ava-source-row-${date}`}>
      <span className="mr-1">{formatDateLabel(date)} に動かす元:</span>
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
