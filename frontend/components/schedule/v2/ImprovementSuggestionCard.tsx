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
import { Loader2 } from 'lucide-react';

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

export interface ImprovementSuggestionCardProps {
  suggestion: ImprovementSuggestion;
  canEdit: boolean;
  /** 採用処理中 (親の confirm mutation). ボタンを disable する. */
  adopting?: boolean;
  onAdopt: (suggestion: ImprovementSuggestion) => void;
  onDismiss: (suggestion: ImprovementSuggestion) => void;
}

export function ImprovementSuggestionCard({
  suggestion,
  canEdit,
  adopting = false,
  onAdopt,
  onDismiss,
}: ImprovementSuggestionCardProps) {
  const { current, candidate, delta, changes, staff_warnings, requires_patient_confirmation } =
    suggestion;
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
