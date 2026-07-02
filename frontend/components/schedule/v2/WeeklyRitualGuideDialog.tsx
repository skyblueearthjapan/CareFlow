'use client';

/**
 * WeeklyRitualGuideDialog — 週次スケジュール準備ガイド (P3-⑥ 導線).
 *
 * 週次5分の儀式 3ステップ（①枠生成 → ②自動スタッフ割付 → ③レビュー承認）を
 * 案内するだけのダイアログ。実行ボタンは置かない（誤操作防止）。
 *
 * 起動: CourseDayTablePanel ツールバーの「週次ガイド」ボタン (variant="ghost")。
 * RBAC: 呼出側 (CourseDayTablePanel) で admin/manager ガード済み。
 */
import * as React from 'react';
import { ListChecks } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface WeeklyRitualGuideDialogProps {
  open: boolean;
  onClose: () => void;
}

// ─────────────────────────────────────────────────────────────────────────
// Sub-component: ステップカード
// ─────────────────────────────────────────────────────────────────────────

interface StepCardProps {
  step: 1 | 2 | 3;
  title: string;
  description: React.ReactNode;
  faq: React.ReactNode;
}

function StepCard({ step, title, description, faq }: StepCardProps) {
  return (
    <li
      className="flex gap-3"
      data-testid={`weekly-ritual-step-${step}`}
    >
      {/* ステップ番号バッジ */}
      <div
        aria-hidden
        className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-brand-primary text-xs font-bold text-white"
      >
        {step}
      </div>

      {/* カード本体 */}
      <div className="flex-1 rounded-lg border border-border-default bg-bg-base p-3 text-sm">
        <p className="font-semibold text-text-primary">{title}</p>
        <div className="mt-1 text-text-secondary">{description}</div>
        {/* よくある質問 */}
        <div className="mt-2 rounded-md bg-bg-muted px-3 py-2 text-xs text-text-muted">
          <span className="font-semibold text-text-secondary">Q. </span>
          {faq}
        </div>
      </div>
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────

export function WeeklyRitualGuideDialog({ open, onClose }: WeeklyRitualGuideDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent
        className="max-w-lg"
        data-testid="weekly-ritual-guide-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ListChecks className="h-5 w-5 text-brand-primary" aria-hidden />
            週次スケジュール準備（5分の儀式）
          </DialogTitle>
          <DialogDescription>
            毎週はじめに 3ステップを順番に実行するだけで、スタッフ割付まで完了します。
          </DialogDescription>
        </DialogHeader>

        {/* 3ステップカード */}
        <ol
          className="space-y-3"
          data-testid="weekly-ritual-steps"
        >
          <StepCard
            step={1}
            title="今週の枠を生成する"
            description={
              <>
                ツールバーの{' '}
                <span className="font-medium text-text-primary">「週を生成」</span>（↻ アイコン）
                ボタンをクリックします。固定訪問パターン（毎週の型・PFV）をもとに、今週の
                実際の訪問予定が一括で作成されます。
              </>
            }
            faq="手動で動かした訪問は消える？ → 「ピン留め（完全固定）」した訪問は保護されます。ピン留めなしの訪問は型から再生成されます。"
          />

          <StepCard
            step={2}
            title="自動スタッフ割付を実行する"
            description={
              <>
                ツールバーの{' '}
                <span className="font-medium text-text-primary">「自動スタッフ割付」</span>（緑ボタン）
                をクリックします。コース別に最適なスタッフが自動で割り付けられ、問題のない
                コースはそのまま確定します。欠勤対応がある場合は先に「欠勤対応」を実施してください。
              </>
            }
            faq="欠勤者がいる場合は？ → 先に「欠勤対応」ボタンで代替スタッフを指定してから実行してください。"
          />

          <StepCard
            step={3}
            title="レビューカードを確認して承認する"
            description={
              <>
                割付後、要確認のコースは{' '}
                <span className="font-medium text-text-primary">レビューダイアログ</span>に表示されます。
                <br />
                <span className="mt-1 inline-flex items-center gap-1">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-yellow-400" aria-hidden />
                  <span className="font-medium">連続（黄）</span>
                </span>
                ：直近の担当が同一スタッフになるコース。チェックボックスで承認。
                <br />
                <span className="mt-0.5 inline-flex items-center gap-1">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-500" aria-hidden />
                  <span className="font-medium">性別（赤）</span>
                </span>
                ：性別制限に適合しないスタッフの候補。「割り付ける」→ 確認して承認。
                <br />
                確認後「選んだ内容で割り付け」で完了です。
              </>
            }
            faq="手動で差し替えた割付は消える？ → 欠勤対応で差し替えた分（代替スタッフ確定済み）は保護されます。"
          />
        </ol>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            data-testid="weekly-ritual-guide-close"
          >
            閉じる
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
