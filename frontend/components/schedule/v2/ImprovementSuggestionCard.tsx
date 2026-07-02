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
import { ArrowLeftRight, Loader2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { proposeWarningLabel } from '@/lib/queries/fieldBoard';
import type { ImprovementSuggestion } from '@/lib/schemas/v2/improvementSuggestion';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

/** 曜日 + 時刻の短縮表記 (例: 月10:00). スワップの移動表示に使う. */
function fmtWeekdayTime(weekday: number, start: string | null | undefined): string {
  const wd = WEEKDAY_LABELS[weekday] ?? '?';
  return `${wd}${trimSeconds(start)}`;
}

/** 効果の主役テキスト. 正 = 削減なので「−N分/週」で見せる (text-success). */
function formatSaved(minutes: number, km: number): string {
  const min = `−${Math.abs(minutes)}分/週`;
  const dist = `−${Math.abs(km).toFixed(1)}km/週`;
  return `${min}（${dist}）`;
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
  // X: current → candidate (candidate = Y の旧枠). Y: 現在枠 → X の旧枠.
  const xMove = `${fmtWeekdayTime(current.weekday, current.start_time)}→${fmtWeekdayTime(candidate.weekday, candidate.start_time)}`;
  const yMove = `${fmtWeekdayTime(cp.current_weekday, cp.current_start_time)}→${fmtWeekdayTime(cp.new_weekday, cp.new_start_time)}`;

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

      {/* 双方向の移動表示. */}
      <ul
        className="mt-1.5 list-disc space-y-0.5 pl-4 text-text-primary"
        data-testid="improvement-swap-moves"
      >
        <li className="tnum">
          {xName}: {xMove}
        </li>
        <li className="tnum">
          {yName}: {yMove}
        </li>
      </ul>

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
  const curWd = WEEKDAY_LABELS[current.weekday] ?? '?';
  const candWd = WEEKDAY_LABELS[candidate.weekday] ?? '?';

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

      {/* 現在枠 → 候補枠 の見出し */}
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-text-secondary">
        <span className="tnum">
          {curWd} {trimSeconds(current.start_time)}–{trimSeconds(current.end_time)}
          {current.course_label ? `（${current.course_label}）` : ''}
        </span>
        <span aria-hidden="true">→</span>
        <span className="tnum font-medium text-brand-primary">
          {candWd} {trimSeconds(candidate.start_time)}–{trimSeconds(candidate.end_time)}
          {candidate.course_label ? `（${candidate.course_label}）` : ''}
        </span>
      </div>

      {/* 変わるもの / 変わらないもの */}
      {changes.changes.length > 0 ? (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-text-primary">
          {changes.changes.map((c, i) => (
            <li key={`chg-${i}`}>{c}</li>
          ))}
        </ul>
      ) : null}
      {changes.unchanged.length > 0 ? (
        <div className="mt-1 text-text-muted">
          変わらないもの: {changes.unchanged.join(' / ')}
        </div>
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
