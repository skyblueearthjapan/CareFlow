'use client';

/**
 * ImprovementSuggestionCard — 改善提案 1 件のカード (P2-C).
 *
 * 見せ方 (設計 §3):
 *   - 効果を主役に: 「移動 −18分/週（−2.1km/週）」を text-success で大きく.
 *   - 変わるもの / 変わらないもの (BE 生成の日本語差分).
 *   - 要確認ラベル: requires_patient_confirmation=true のとき warning-bg バッジ
 *     「可動域未設定・患者様への確認推奨」(movability=unknown の時刻提案).
 *   - staff_warnings: P0-1 の proposeWarningLabel 辞書を流用して日本語化.
 *   - [採用][見送り]: canEdit=false のときは非表示 (RBAC は BE でも担保).
 *
 * 採用/見送りの実処理は親 (ImprovementSuggestionsSection) が持ち、本カードは表示と
 * ボタン発火のみに徹する (副作用なしのプレゼンテーション).
 *
 * デザイン: Warm & Human トークンのみ (bg/border/text/success/warning/brand-primary, tnum).
 */
import * as React from 'react';
import { ArrowLeftRight, ArrowRight, Loader2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { proposeWarningLabel } from '@/lib/queries/fieldBoard';
import type { ImprovementSuggestion } from '@/lib/schemas/v2/improvementSuggestion';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

/** 効果の主役テキスト. 正 = 削減なので「−N分/週」で見せる (text-success). */
function formatSaved(minutes: number, km: number): string {
  const min = `−${Math.abs(minutes)}分/週`;
  const dist = `−${Math.abs(km).toFixed(1)}km/週`;
  return `${min}（${dist}）`;
}

/**
 * 移動元 / 移動先の枠チップ (現場フィードバック: 「どのコースからどこへ動くのか」が
 * 一目で飛び込んでくるように、コースを色付きバッジ・曜日時刻を太字で見せる).
 *   - from: 控えめ (muted 背景 + 枠線バッジ)
 *   - to:   強調 (ブランド色の太枠 + 塗りつぶしバッジ + 太字)
 */
function SlotChip({
  tone,
  weekday,
  time,
  courseLabel,
  staffName,
}: {
  tone: 'from' | 'to';
  weekday: number;
  time: string;
  courseLabel?: string | null;
  /** 指定時のみ「担当: ◯◯」を添える (swap カードで移動先の担当を明示するため). */
  staffName?: string | null;
}) {
  const isTo = tone === 'to';
  return (
    <span
      className={
        isTo
          ? 'inline-flex items-center gap-1.5 rounded-md border-2 border-brand-primary bg-bg-base px-2 py-1'
          : 'inline-flex items-center gap-1.5 rounded-md border border-border-default bg-bg-muted px-2 py-1'
      }
      data-testid={`improvement-slot-${tone}`}
    >
      {courseLabel ? (
        <span
          className={
            isTo
              ? 'rounded bg-brand-primary px-1.5 py-0.5 text-[11px] font-bold text-white'
              : 'rounded border border-border-default bg-bg-base px-1.5 py-0.5 text-[11px] font-bold text-text-secondary'
          }
        >
          {courseLabel}
        </span>
      ) : null}
      <span
        className={
          isTo
            ? 'tabular-nums text-sm font-bold text-brand-primary'
            : 'tabular-nums text-sm font-bold text-text-secondary'
        }
      >
        {WEEKDAY_LABELS[weekday] ?? '?'} {time}
      </span>
      {staffName ? (
        <span
          className="text-[11px] text-text-secondary"
          data-testid={`improvement-slot-${tone}-staff`}
        >
          担当: {staffName}
        </span>
      ) : null}
    </span>
  );
}

/** from → to のチップ列 (move 1 行ぶん). label は swap 時の患者名など任意の接頭辞. */
function MoveVisual({
  label,
  fromWeekday,
  fromTime,
  fromCourse,
  fromStaff,
  toWeekday,
  toTime,
  toCourse,
  toStaff,
  testId,
}: {
  label?: string;
  fromWeekday: number;
  fromTime: string;
  fromCourse?: string | null;
  fromStaff?: string | null;
  toWeekday: number;
  toTime: string;
  toCourse?: string | null;
  toStaff?: string | null;
  testId?: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid={testId}>
      {label ? (
        <span className="w-full text-xs font-bold text-text-primary sm:w-auto">{label}</span>
      ) : null}
      <SlotChip
        tone="from"
        weekday={fromWeekday}
        time={fromTime}
        courseLabel={fromCourse}
        staffName={fromStaff}
      />
      <ArrowRight className="h-5 w-5 shrink-0 text-brand-primary" strokeWidth={2.5} aria-hidden />
      <SlotChip
        tone="to"
        weekday={toWeekday}
        time={toTime}
        courseLabel={toCourse}
        staffName={toStaff}
      />
    </div>
  );
}

export interface ImprovementSuggestionCardProps {
  suggestion: ImprovementSuggestion;
  canEdit: boolean;
  /** 採用処理中 (親の confirm mutation). ボタンを disable する. */
  adopting?: boolean;
  /** 表示中患者 (X) の氏名. スワップカードの双方向表示に使う (move カードでは未使用). */
  patientName?: string;
  onAdopt: (suggestion: ImprovementSuggestion) => void;
  onDismiss: (suggestion: ImprovementSuggestion) => void;
}

/** 採用 / 見送りボタン列 (move / swap で共通). */
function CardActions({
  suggestion,
  adopting,
  onAdopt,
  onDismiss,
}: {
  suggestion: ImprovementSuggestion;
  adopting: boolean;
  onAdopt: (s: ImprovementSuggestion) => void;
  onDismiss: (s: ImprovementSuggestion) => void;
}) {
  return (
    <div className="mt-2 flex items-center justify-end gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onDismiss(suggestion)}
        disabled={adopting}
        className="h-7 px-3 text-[11px]"
        data-testid="improvement-dismiss-button"
      >
        見送り
      </Button>
      <Button
        type="button"
        size="sm"
        onClick={() => onAdopt(suggestion)}
        disabled={adopting}
        className="h-7 px-3 text-[11px]"
        data-testid="improvement-adopt-button"
      >
        {adopting ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
        採用
      </Button>
    </div>
  );
}

/**
 * スワップ提案 (kind='swap') のカード. 効果 delta は move と同じ見せ方を流用し、
 * ヘッダ (◯◯様と入れ替え) と双方向の移動表示・双方の要確認バッジを追加する.
 */
function SwapCard({
  suggestion,
  canEdit,
  adopting,
  patientName,
  onAdopt,
  onDismiss,
}: {
  suggestion: ImprovementSuggestion;
  canEdit: boolean;
  adopting: boolean;
  patientName?: string;
  onAdopt: (s: ImprovementSuggestion) => void;
  onDismiss: (s: ImprovementSuggestion) => void;
}) {
  const {
    current,
    candidate,
    delta,
    staff_warnings,
    requires_patient_confirmation,
    within_preference,
  } = suggestion;
  const cp = suggestion.swap_counterpart!;
  const xName = patientName ? `${patientName} 様` : '対象の患者様';
  const yName = `${cp.patient_name} 様`;

  return (
    <div
      className="rounded border border-border-default bg-bg-base p-3 text-xs"
      data-testid={`improvement-card-${suggestion.kind}-${suggestion.target_weekday}`}
    >
      {/* ヘッダ: ◯◯様と入れ替え */}
      <div
        className="flex items-center gap-1.5 font-medium text-brand-primary"
        data-testid="improvement-swap-header"
      >
        <ArrowLeftRight className="h-3.5 w-3.5" aria-hidden />
        <span>{yName}と入れ替え</span>
      </div>

      {/* 効果 (主役) — move カードと同一の見せ方. */}
      <div className="mt-1.5 flex flex-wrap items-baseline gap-2">
        <span className="text-text-muted">移動</span>
        <span className="tnum text-base font-bold text-success" data-testid="improvement-effect">
          {formatSaved(delta.travel_minutes_saved, delta.travel_km_saved)}
        </span>
      </div>

      {/* 双方向の移動表示 (チップ型: 誰が・どのコースの何時から・どこへ、が一目で分かる).
          2026-08-11: 移動先の担当名が一切出ていなかった欠陥を修正。NG スタッフ /
          性別制限の判断材料になるため、コースの担当 (candidate/current.staff_name) を添える
          (BE のスキーマに既にある値のみ。相手 Y は X の旧枠へ入るので current 側が担当). */}
      <div className="mt-2 space-y-2" data-testid="improvement-swap-moves">
        {/* X: current → candidate (candidate = Y の旧枠). */}
        <MoveVisual
          label={xName}
          fromWeekday={current.weekday}
          fromTime={trimSeconds(current.start_time)}
          fromCourse={current.course_label}
          fromStaff={current.staff_name}
          toWeekday={candidate.weekday}
          toTime={trimSeconds(candidate.start_time)}
          toCourse={candidate.course_label}
          toStaff={candidate.staff_name}
          testId="improvement-swap-move-x"
        />
        {/* Y: 現在枠 (= candidate のコース) → X の旧枠 (= current のコース). */}
        <MoveVisual
          label={yName}
          fromWeekday={cp.current_weekday}
          fromTime={trimSeconds(cp.current_start_time)}
          fromCourse={candidate.course_label}
          fromStaff={candidate.staff_name}
          toWeekday={cp.new_weekday}
          toTime={trimSeconds(cp.new_start_time)}
          toCourse={current.course_label}
          toStaff={current.staff_name}
          testId="improvement-swap-move-y"
        />
      </div>

      {/* H2: 理由文 (swap は簡易文). */}
      {suggestion.reason ? (
        <div
          className="mt-1.5 text-[11px] leading-relaxed text-text-secondary"
          data-testid="improvement-reason"
        >
          {suggestion.reason}
        </div>
      ) : null}

      {/* 双方の希望内 / 要確認バッジ (#P4-B).
          - 希望内 (within_preference=true) は success トーンで「ご希望の範囲内」= 確認不要の安心感.
          - 希望外は従来どおり要確認 (warning) バッジ. */}
      {within_preference ||
      requires_patient_confirmation ||
      cp.within_preference ||
      cp.requires_patient_confirmation ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {within_preference ? (
            <span
              className="rounded border border-success/40 bg-success-bg px-1.5 py-0.5 text-[10px] font-medium text-success"
              data-testid="improvement-within-preference"
            >
              ✓ ご希望の範囲内
            </span>
          ) : null}
          {requires_patient_confirmation ? (
            <span
              className="rounded border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning"
              data-testid="improvement-requires-confirmation"
            >
              可動域未設定・患者様への確認推奨
            </span>
          ) : null}
          {cp.within_preference ? (
            <span
              className="rounded border border-success/40 bg-success-bg px-1.5 py-0.5 text-[10px] font-medium text-success"
              data-testid="improvement-swap-counterpart-within-preference"
            >
              {cp.patient_name} 様もご希望の範囲内
            </span>
          ) : cp.requires_patient_confirmation ? (
            <span
              className="rounded border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning"
              data-testid="improvement-swap-counterpart-confirmation"
            >
              {cp.patient_name} 様の可動域未設定
            </span>
          ) : null}
        </div>
      ) : null}

      {/* スタッフ警告 (P0-1 辞書を流用). */}
      {staff_warnings.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1" data-testid="improvement-staff-warnings">
          {staff_warnings.map((w, i) => (
            <Badge key={`warn-${i}`} variant="warning" className="text-[10px]">
              {proposeWarningLabel(w)}
            </Badge>
          ))}
        </div>
      ) : null}

      {canEdit ? (
        <CardActions
          suggestion={suggestion}
          adopting={adopting}
          onAdopt={onAdopt}
          onDismiss={onDismiss}
        />
      ) : null}
    </div>
  );
}

export function ImprovementSuggestionCard({
  suggestion,
  canEdit,
  adopting = false,
  patientName,
  onAdopt,
  onDismiss,
}: ImprovementSuggestionCardProps) {
  // スワップは専用レイアウト (move カードの表示・挙動は完全不変).
  if (suggestion.kind === 'swap' && suggestion.swap_counterpart) {
    return (
      <SwapCard
        suggestion={suggestion}
        canEdit={canEdit}
        adopting={adopting}
        patientName={patientName}
        onAdopt={onAdopt}
        onDismiss={onDismiss}
      />
    );
  }

  const {
    current,
    candidate,
    delta,
    changes,
    staff_warnings,
    requires_patient_confirmation,
    within_preference,
  } = suggestion;

  return (
    <div
      className="rounded border border-border-default bg-bg-base p-3 text-xs"
      data-testid={`improvement-card-${suggestion.kind}-${suggestion.target_weekday}`}
    >
      {/* 効果 (主役) */}
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-text-muted">移動</span>
        <span className="tnum text-base font-bold text-success" data-testid="improvement-effect">
          {formatSaved(delta.travel_minutes_saved, delta.travel_km_saved)}
        </span>
        {/* #P4-B: 希望内バッジ (確認不要の安心感). BE が within_preference=true のとき表示. */}
        {within_preference ? (
          <span
            className="rounded border border-success/40 bg-success-bg px-1.5 py-0.5 text-[10px] font-medium text-success"
            data-testid="improvement-within-preference"
          >
            ✓ ご希望の範囲内
          </span>
        ) : null}
        {requires_patient_confirmation ? (
          <span
            className="rounded border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning"
            data-testid="improvement-requires-confirmation"
          >
            可動域未設定・患者様への確認推奨
          </span>
        ) : null}
      </div>

      {/* 現在枠 → 候補枠 (チップ型: どのコースからどこへ動くのかが一目で分かる). */}
      <div className="mt-2" data-testid="improvement-move-visual">
        <MoveVisual
          fromWeekday={current.weekday}
          fromTime={`${trimSeconds(current.start_time)}–${trimSeconds(current.end_time)}`}
          fromCourse={current.course_label}
          toWeekday={candidate.weekday}
          toTime={`${trimSeconds(candidate.start_time)}–${trimSeconds(candidate.end_time)}`}
          toCourse={candidate.course_label}
        />
      </div>

      {/* H2: 理由文 (原因→対策→効果の 1 文). */}
      {suggestion.reason ? (
        <div
          className="mt-1.5 text-[11px] leading-relaxed text-text-secondary"
          data-testid="improvement-reason"
        >
          {suggestion.reason}
        </div>
      ) : null}

      {/* 変わるもの / 変わらないもの */}
      {changes.changes.length > 0 ? (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-text-primary">
          {changes.changes.map((c, i) => (
            <li key={`chg-${i}`}>{c}</li>
          ))}
        </ul>
      ) : null}
      {changes.unchanged.length > 0 ? (
        <div className="mt-1 text-text-muted">変わらないもの: {changes.unchanged.join(' / ')}</div>
      ) : null}

      {/* スタッフ警告 (P0-1 辞書を流用) */}
      {staff_warnings.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1" data-testid="improvement-staff-warnings">
          {staff_warnings.map((w, i) => (
            <Badge key={`warn-${i}`} variant="warning" className="text-[10px]">
              {proposeWarningLabel(w)}
            </Badge>
          ))}
        </div>
      ) : null}

      {/* イベント考慮2段階提案: 移動先スタッフのイベントと衝突する枠の詳細.
          (zod を通らない経路もあるため ?? [] で防御) */}
      {(suggestion.event_conflicts ?? []).length > 0 ? (
        <div
          className="mt-1.5 rounded border border-amber-400/60 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-900 dark:border-amber-600 dark:bg-amber-900/20 dark:text-amber-200"
          data-testid="improvement-event-conflict"
        >
          ⚠ 移動先に空き枠がなかったため、イベントを無視して算出した枠です。
          {(suggestion.event_conflicts ?? [])
            .map((c) => `「${c.title}（${c.start}〜${c.end}）」`)
            .join('・')}
          とぶつかります。採用した場合は、イベントの方を手動で調整してください。
        </div>
      ) : null}

      {/* 採用 / 見送り (canEdit ガード) */}
      {canEdit ? (
        <div className="mt-2 flex items-center justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onDismiss(suggestion)}
            disabled={adopting}
            className="h-7 px-3 text-[11px]"
            data-testid="improvement-dismiss-button"
          >
            見送り
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => onAdopt(suggestion)}
            disabled={adopting}
            className="h-7 px-3 text-[11px]"
            data-testid="improvement-adopt-button"
          >
            {adopting ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            採用
          </Button>
        </div>
      ) : null}
    </div>
  );
}
