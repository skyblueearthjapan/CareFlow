'use client';

/**
 * AddVisitDialog — 盤面セルの「＋訪問」(週空間 Phase E・運転席)。
 *
 * モックの `openAddPanel(kind='v')` を部品化。保留プールの候補から患者を選び、
 * 開始時刻・所要時間・コース(任意) を決めて **今週だけ** 追加する。
 *
 * 契約書 D6: 実行は既存 `POST /visits` に `source='manual_week'` (PFV 不変)。
 * コースは省略可 (臨時扱い)。API は呼ばず `onSubmit(payload)` を親へ渡す。
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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { parseIsoDate } from './reconcileMarkers';
import { TIME_OPTIONS } from './VisitActionMenu';

/** 所要時間の選択肢 (モックと同じ)。 */
export const DURATION_OPTIONS = [30, 45, 60, 90] as const;

const WD_JA = ['月', '火', '水', '木', '金', '土', '日'] as const;

export interface AddVisitCandidate {
  patient_id: string;
  patient_name: string;
  /** 「月曜希望 / 60分 / 稲毛」などの補足 (保留プールの見出し)。 */
  hint?: string | null;
}

export interface AddVisitPayload {
  patient_id: string;
  /** YYYY-MM-DD。 */
  date: string;
  start_time: string;
  /** start_time + 所要時間 (HH:MM)。 */
  end_time: string;
  service_minutes: number;
  /** null = 臨時 (コース未所属)。 */
  course_id: string | null;
  /** null = （担当なし）。 */
  staff_id: string | null;
}

export interface AddVisitDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 追加先のスタッフ。null = （担当なし）行。 */
  staff: { id: string; name: string } | null;
  /** 追加先の日付 (YYYY-MM-DD)。 */
  date: string;
  /** 保留プールの候補 (親が渡す)。検索欄で絞り込める。 */
  poolCandidates: AddVisitCandidate[];
  /** コースの選択肢 (空なら「臨（臨時）」のみ)。 */
  courseOptions?: { id: string; label: string }[];
  defaultStart?: string;
  defaultMinutes?: number;
  submitting?: boolean;
  onSubmit: (payload: AddVisitPayload) => void;
  /** 「型にも登録…」= マスタ(固定訪問)へ昇格する 2 択を親が開く。 */
  onRegisterToMaster?: (payload: AddVisitPayload) => void;
}

function addMinutes(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(':').map((s) => Number.parseInt(s, 10));
  const total = (h ?? 0) * 60 + (m ?? 0) + minutes;
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function fmtDate(dateIso: string): string {
  const d = parseIsoDate(dateIso);
  const wd = WD_JA[(d.getDay() + 6) % 7] ?? '';
  return `${d.getMonth() + 1}/${d.getDate()}(${wd})`;
}

const selectCls =
  'block w-full rounded border border-border-default bg-bg-base px-2 py-1 text-xs disabled:opacity-50';

export function AddVisitDialog({
  open,
  onOpenChange,
  staff,
  date,
  poolCandidates,
  courseOptions = [],
  defaultStart = '14:00',
  defaultMinutes = 45,
  submitting = false,
  onSubmit,
  onRegisterToMaster,
}: AddVisitDialogProps) {
  const [keyword, setKeyword] = React.useState('');
  const [patientId, setPatientId] = React.useState('');
  const [start, setStart] = React.useState(defaultStart);
  const [minutes, setMinutes] = React.useState<number>(defaultMinutes);
  const [courseId, setCourseId] = React.useState('');

  // ダイアログを開き直したら入力をリセットする。
  React.useEffect(() => {
    if (!open) return;
    setKeyword('');
    setPatientId('');
    setStart(defaultStart);
    setMinutes(defaultMinutes);
    setCourseId('');
  }, [open, defaultStart, defaultMinutes]);

  const filtered = React.useMemo(() => {
    const kw = keyword.trim();
    if (!kw) return poolCandidates;
    return poolCandidates.filter((c) => c.patient_name.includes(kw) || (c.hint ?? '').includes(kw));
  }, [poolCandidates, keyword]);

  const buildPayload = (): AddVisitPayload | null => {
    if (!patientId) return null;
    return {
      patient_id: patientId,
      date,
      start_time: start,
      end_time: addMinutes(start, minutes),
      service_minutes: minutes,
      course_id: courseId === '' ? null : courseId,
      staff_id: staff?.id ?? null,
    };
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm" data-testid="add-visit-dialog">
        <DialogHeader>
          <DialogTitle className="text-sm">＋ 訪問を追加</DialogTitle>
          <DialogDescription className="text-[11px]">
            {staff?.name ?? '（担当なし）'}・{fmtDate(date)} に今週だけ追加します（毎週の型は
            変わりません）
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <div>
            <Label className="text-xs">患者</Label>
            <Input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="名前で絞り込む"
              className="mb-1 h-7 text-xs"
              data-testid="add-visit-search"
              aria-label="患者を検索"
            />
            <select
              className={selectCls}
              value={patientId}
              onChange={(e) => setPatientId(e.target.value)}
              disabled={submitting}
              data-testid="add-visit-patient"
              aria-label="患者"
            >
              <option value="">選ぶ…</option>
              {filtered.map((c) => (
                <option key={c.patient_id} value={c.patient_id}>
                  {c.patient_name}
                  {c.hint ? `（${c.hint}）` : ''}
                </option>
              ))}
            </select>
            {filtered.length === 0 ? (
              <p className="mt-0.5 text-[11px] text-text-muted" data-testid="add-visit-no-hit">
                該当する患者がいません
              </p>
            ) : null}
          </div>

          <div className="flex gap-2">
            <div className="flex-1">
              <Label className="text-xs">開始</Label>
              <select
                className={selectCls}
                value={start}
                onChange={(e) => setStart(e.target.value)}
                disabled={submitting}
                data-testid="add-visit-start"
                aria-label="開始"
              >
                {TIME_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <Label className="text-xs">時間</Label>
              <select
                className={selectCls}
                value={String(minutes)}
                onChange={(e) => setMinutes(Number.parseInt(e.target.value, 10))}
                disabled={submitting}
                data-testid="add-visit-minutes"
                aria-label="時間"
              >
                {DURATION_OPTIONS.map((d) => (
                  <option key={d} value={d}>
                    {d}分
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <Label className="text-xs">コース（任意）</Label>
            <select
              className={selectCls}
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
              disabled={submitting}
              data-testid="add-visit-course"
              aria-label="コース"
            >
              <option value="">臨（臨時・コースなし）</option>
              {courseOptions.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <DialogFooter>
          {onRegisterToMaster ? (
            <Button
              type="button"
              variant="outline"
              disabled={submitting || !patientId}
              onClick={() => {
                const p = buildPayload();
                if (p) onRegisterToMaster(p);
              }}
              data-testid="add-visit-master"
            >
              📐 型にも登録…
            </Button>
          ) : null}
          <Button
            type="button"
            disabled={submitting || !patientId}
            onClick={() => {
              const p = buildPayload();
              if (p) onSubmit(p);
            }}
            data-testid="add-visit-submit"
          >
            今週に追加
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
