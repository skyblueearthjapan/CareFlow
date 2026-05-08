'use client';

/**
 * PartnerCourseDialog — Wave 37 Phase 3-C.
 *
 * 2 名体制 (patient.requires_multiple_staff=true) の患者を D&D 配置するときに
 * 「相方コース」(2 人目のスタッフが担当する course_template) をユーザに
 * 選んでもらうためのダイアログ。
 *
 * BE Phase 2-A の place-and-fix は staff_count=2 + course_template_ids: [a, b]
 * で 2 visit を visit_group_id 共有で作成する。FE 側では 1 つ目のコース
 * (= drop 先セル) は確定済なので、2 つ目を選ぶ UI が必要。
 *
 * Props:
 *   - open: ダイアログ開閉
 *   - primaryTemplate: 1 人目 (= drop 先セル) の course_template
 *   - candidateTemplates: 2 人目候補 (primaryTemplate を除外した同 office の
 *     表示中 template リスト)
 *   - onConfirm(secondaryTemplateId): ユーザ確定
 *   - onCancel: ダイアログを閉じるだけ (drop 取り消し)
 *
 * candidateTemplates が空の場合はエラーメッセージを表示し、確定ボタンを
 * 無効化する (= 相方コースが無いため staff_count=2 配置不可)。
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';

export interface PartnerCourseDialogProps {
  open: boolean;
  /** 1 人目のコース (drop 先セルから確定済み)。 */
  primaryTemplate: CourseTemplateRead | null;
  /** 2 人目のコース候補 (primaryTemplate を除外した同 office 同 weekday 表示対象 template). */
  candidateTemplates: CourseTemplateRead[];
  /** 表示用: 1 人目の office 名 (例: "本店"). */
  primaryOfficeName?: string;
  /** 表示用: drop 先 weekday + start_time (例: "月 09:30"). */
  contextLabel?: string;
  /** ユーザが 2 つ目を確定したときのコールバック. */
  onConfirm: (secondaryTemplateId: string) => void;
  /** キャンセル時 (open→false へ). drop 取り消し相当. */
  onCancel: () => void;
}

export function PartnerCourseDialog({
  open,
  primaryTemplate,
  candidateTemplates,
  primaryOfficeName,
  contextLabel,
  onConfirm,
  onCancel,
}: PartnerCourseDialogProps) {
  const [selectedId, setSelectedId] = React.useState<string>('');

  // open が変わるたびに選択状態をリセット
  React.useEffect(() => {
    if (open) {
      setSelectedId('');
    }
  }, [open]);

  const hasCandidates = candidateTemplates.length > 0;
  const canConfirm = hasCandidates && selectedId !== '';

  const primaryLabel = primaryTemplate
    ? `${primaryOfficeName ? primaryOfficeName + '-' : ''}${primaryTemplate.label}`
    : '';

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <DialogContent
        aria-describedby="partner-course-dialog-desc"
        data-testid="partner-course-dialog"
      >
        <DialogHeader>
          <DialogTitle>相方コースを選択</DialogTitle>
          <DialogDescription id="partner-course-dialog-desc">
            この患者は 2 名体制が必要です。1 人目のコースは
            <span className="mx-1 font-semibold text-text-primary">{primaryLabel}</span>
            {contextLabel ? ` (${contextLabel}) ` : ' '}
            で確定しました。 2 人目のスタッフが担当する「相方コース」を選んでください。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 py-2">
          {hasCandidates ? (
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-semibold text-text-primary">相方コース</span>
              <select
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
                className="rounded border border-border-default bg-bg-base px-2 py-1 text-sm"
                data-testid="partner-course-select"
                aria-label="相方コース"
              >
                <option value="">— 選択してください —</option>
                {candidateTemplates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <div
              className="rounded border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning"
              data-testid="partner-course-no-candidates"
              role="alert"
            >
              相方コースが選べません。同じ拠点・曜日に他のコーステンプレートが必要です。
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onCancel}
            data-testid="partner-course-cancel"
          >
            キャンセル
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!canConfirm}
            onClick={() => {
              if (canConfirm) onConfirm(selectedId);
            }}
            data-testid="partner-course-confirm"
          >
            確定
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
