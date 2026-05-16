'use client';

/**
 * FixedTimeEditModal — W41 v2 拡張 (警告アクション).
 *
 * 同住所集約警告などの actionable 警告に対し、以下の 2 経路で時間を変更する:
 *   (a) 患者マスター更新 (永続): POST /v2/update-fixed-time-master
 *   (b) 今週限定変更:           POST /v2/update-fixed-time-week-only
 *
 * (b) は対象 visit_id が必要 (apply-week-only 適用後に DB に visit が入って
 * 初めて使える経路). 提案算出直後は (a) のみ選択可.
 */
import * as React from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  useUpdateFixedTimeMasterMutation,
  useUpdateFixedTimeWeekOnlyMutation,
} from '@/lib/queries/autoScheduleV2';
import type { V2Warning } from '@/lib/schemas/v2/autoScheduleV2';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

const TIME_TYPE_OPTIONS = ['固定', '時間帯', '午前', '午後', '終日'] as const;

function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.slice(0, 5);
}

function formatErr(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === 'string') return err;
  return String(err);
}

export interface FixedTimeEditModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  /** クリックされた警告 (patient_id / weekday / current_time 等を持つ). */
  warning: V2Warning;
}

type Mode = 'master' | 'week_only';

export function FixedTimeEditModal({ open, onClose, onSuccess, warning }: FixedTimeEditModalProps) {
  const masterMut = useUpdateFixedTimeMasterMutation();
  const weekOnlyMut = useUpdateFixedTimeWeekOnlyMutation();

  // 初期値: 集約したかった時刻があれば、そちらを優先 (UX: ユーザーは「集約したい」が動機).
  const initialStart = trimSeconds(warning.suggested_time ?? warning.current_time ?? '09:30');
  const computeDefaultEnd = (start: string): string => {
    // start + 60min (60 分は妥当な default. UI 側で編集可能).
    const m = start.match(/^(\d{1,2}):(\d{2})$/);
    if (!m) return start;
    const total = Number(m[1]) * 60 + Number(m[2]) + 60;
    const eh = Math.min(23, Math.floor(total / 60));
    const em = total % 60;
    return `${String(eh).padStart(2, '0')}:${String(em).padStart(2, '0')}`;
  };
  const initialEnd = trimSeconds(warning.preferred_end ?? '') || computeDefaultEnd(initialStart);
  const initialTimeType = (warning.time_type ?? '固定') as (typeof TIME_TYPE_OPTIONS)[number];

  // visit_id 不在のときは master のみ選択可.
  const canWeekOnly = !!warning.visit_id;
  const [mode, setMode] = React.useState<Mode>('master');
  const [newStart, setNewStart] = React.useState(initialStart);
  const [newEnd, setNewEnd] = React.useState(initialEnd);
  const [newTimeType, setNewTimeType] =
    React.useState<(typeof TIME_TYPE_OPTIONS)[number]>(initialTimeType);

  // open 時にフォームをリセット.
  React.useEffect(() => {
    if (!open) return;
    setMode(canWeekOnly ? 'master' : 'master');
    setNewStart(initialStart);
    setNewEnd(initialEnd);
    setNewTimeType(initialTimeType);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, warning.patient_id, warning.weekday, warning.visit_id]);

  const busy = masterMut.isPending || weekOnlyMut.isPending;

  const handleConfirm = async () => {
    if (busy) return;
    try {
      if (mode === 'master') {
        if (!warning.patient_id || warning.weekday === null) {
          toast.error('patient_id / weekday が不足しています');
          return;
        }
        await masterMut.mutateAsync({
          patient_id: warning.patient_id,
          weekday: warning.weekday,
          new_start: newStart,
          new_end: newEnd || null,
          new_time_type: newTimeType,
        });
        toast.success('固定時間マスターを更新しました');
      } else {
        if (!warning.visit_id) {
          toast.error('visit_id がないため週限定変更できません');
          return;
        }
        await weekOnlyMut.mutateAsync({
          visit_id: warning.visit_id,
          new_start: newStart,
          new_end: newEnd || null,
        });
        toast.success('今週限定で visit を更新しました');
      }
      onSuccess();
      onClose();
    } catch (err) {
      toast.error(`更新に失敗しました: ${formatErr(err)}`);
    }
  };

  const wdLabel =
    warning.weekday === null || warning.weekday === undefined
      ? '曜日不問'
      : `${WEEKDAY_LABELS[warning.weekday]}曜`;

  return (
    <Dialog open={open} onOpenChange={(o) => (!o && !busy ? onClose() : undefined)}>
      <DialogContent className="max-w-md" data-testid="fixed-time-edit-modal">
        <DialogHeader>
          <DialogTitle>固定時間の変更</DialogTitle>
          <DialogDescription>
            希望時刻 / 時刻種別を変更します。マスターを更新する場合は永続的に反映されます。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          <div className="rounded border border-border-default bg-bg-muted p-3 text-xs">
            <div>
              患者: <span className="font-semibold">{warning.patient_name ?? '—'} 様</span>
            </div>
            <div>曜日: {wdLabel}</div>
            <div>
              現在の時刻: <span className="tnum">{trimSeconds(warning.current_time) || '—'}</span>{' '}
              {warning.time_type ? `(${warning.time_type})` : ''}
            </div>
            {warning.suggested_time ? (
              <div>
                集約したかった時刻:{' '}
                <span className="tnum font-semibold text-brand-primary">
                  {trimSeconds(warning.suggested_time)}
                </span>
              </div>
            ) : null}
          </div>

          <div className="space-y-1">
            <Label className="text-xs font-semibold">変更モード</Label>
            <div className="flex flex-col gap-1">
              <label className="flex items-center gap-2 text-xs">
                <input
                  type="radio"
                  name="fixed-time-mode"
                  value="master"
                  checked={mode === 'master'}
                  onChange={() => setMode('master')}
                  data-testid="fixed-time-edit-mode-master"
                />
                <span>(a) 患者マスターを更新する (永続)</span>
              </label>
              <label
                className={`flex items-center gap-2 text-xs ${
                  canWeekOnly ? '' : 'text-text-muted opacity-60'
                }`}
              >
                <input
                  type="radio"
                  name="fixed-time-mode"
                  value="week_only"
                  checked={mode === 'week_only'}
                  onChange={() => setMode('week_only')}
                  disabled={!canWeekOnly}
                  data-testid="fixed-time-edit-mode-week-only"
                />
                <span>
                  (b) 今週限定で変更する
                  {canWeekOnly ? '' : ' (※提案中の visit にはこのオプション無効)'}
                </span>
              </label>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs">開始</Label>
              <Input
                type="time"
                value={newStart}
                onChange={(e) => setNewStart(e.target.value)}
                disabled={busy}
                data-testid="fixed-time-edit-start"
              />
            </div>
            <div>
              <Label className="text-xs">終了</Label>
              <Input
                type="time"
                value={newEnd}
                onChange={(e) => setNewEnd(e.target.value)}
                disabled={busy}
                data-testid="fixed-time-edit-end"
              />
            </div>
          </div>

          {mode === 'master' ? (
            <div>
              <Label className="text-xs">時刻種別</Label>
              <select
                value={newTimeType}
                onChange={(e) =>
                  setNewTimeType(e.target.value as (typeof TIME_TYPE_OPTIONS)[number])
                }
                disabled={busy}
                className="block w-full rounded border border-border-default bg-bg-default px-2 py-1 text-xs"
                data-testid="fixed-time-edit-time-type"
              >
                {TIME_TYPE_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={busy}
            data-testid="fixed-time-edit-cancel"
          >
            キャンセル
          </Button>
          <Button
            type="button"
            onClick={() => {
              void handleConfirm();
            }}
            disabled={busy || !newStart}
            data-testid="fixed-time-edit-confirm"
          >
            {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden /> : null}
            変更を確定
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
