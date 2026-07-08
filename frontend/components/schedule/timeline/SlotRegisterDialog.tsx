'use client';

/**
 * SlotRegisterDialog — T-2 ②-a: 日タイムラインの空き枠クリック → 登録モーダル。
 *
 * docs/plans/session-2026-07-07-HANDOFF.md §4 A-2。訪問の配置は既存 place-and-fix を
 * 叩くだけ (新規API・ソルバなし)。会議・イベントは TimelineEventAddDialog へ切り替える
 * (onSwitchToEvent — カイポケ反映外である旨を本ダイアログで明示する)。
 *
 * 表示専用コンポーネントの原則に従い、API/mutation は持たない:
 *   - onRegisterVisit({patientId, startHM, durationMin}) — Panel が place-and-fix
 *     (fix_pattern=false = この週だけ) を呼ぶ。
 *   - onSwitchToEvent() — Panel が TimelineEventAddDialog (D-1: 全スタッフから複数選択) を開く。
 *
 * 配置候補 = プール患者 (不足あり・active)。2名体制患者はプールからの D&D 経路
 * (相方コース選択ダイアログ) に委ねるため、ここでは対象外 (注記を出す)。
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
import { fmtHM } from '@/lib/scheduling/freeGaps';

/** 空き枠の文脈 (Panel が列 + gap から組む)。 */
export interface SlotRegisterContext {
  /** 例: "稲毛A"。 */
  courseLabel: string;
  /** 担当スタッフ名 (未割当は null)。 */
  staffName: string | null;
  /** 例: "月"。 */
  weekdayLabel: string;
  gapStartMin: number;
  gapEndMin: number;
  /** 担当スタッフがいるときだけ会議・イベント登録へ切替可。 */
  canRegisterEvent: boolean;
}

/** 配置候補 1 名 (プール患者)。 */
export interface SlotPatientOption {
  id: string;
  name: string;
  /** weekly_pattern.service_minutes ?? 60。患者選択時に時間の初期値になる。 */
  defaultDurationMin: number;
  /** 今週の不足数 (希望 - 配置済)。 */
  shortage: number;
}

export interface SlotRegisterDialogProps {
  open: boolean;
  context: SlotRegisterContext | null;
  patients: SlotPatientOption[];
  /** place-and-fix 実行中はボタンを無効化。 */
  busy?: boolean;
  onRegisterVisit: (args: { patientId: string; startHM: string; durationMin: number }) => void;
  onSwitchToEvent: () => void;
  onClose: () => void;
}

const SNAP_MIN = 15;

/** gap 内で duration が収まる 15 分刻みの開始時刻候補 (分)。 */
export function slotStartOptions(
  gapStartMin: number,
  gapEndMin: number,
  durationMin: number,
): number[] {
  const out: number[] = [];
  for (let m = gapStartMin; m + durationMin <= gapEndMin; m += SNAP_MIN) out.push(m);
  return out;
}

export function SlotRegisterDialog({
  open,
  context,
  patients,
  busy = false,
  onRegisterVisit,
  onSwitchToEvent,
  onClose,
}: SlotRegisterDialogProps) {
  const [patientId, setPatientId] = React.useState<string>('');
  const [durationMin, setDurationMin] = React.useState<number>(60);
  const [startMin, setStartMin] = React.useState<number | null>(null);

  // open のたびに選択をリセット (gap 開始 = 既定の開始時刻)。
  React.useEffect(() => {
    if (open) {
      setPatientId('');
      setDurationMin(60);
      setStartMin(context?.gapStartMin ?? null);
    }
  }, [open, context]);

  const gapLen = context ? context.gapEndMin - context.gapStartMin : 0;

  // 時間候補: よく使う長さ + 患者既定値を gap に収まる範囲で。
  const durationOptions = React.useMemo(() => {
    const base = [30, 45, 60, 90, 120];
    const sel = patients.find((p) => p.id === patientId);
    if (sel && !base.includes(sel.defaultDurationMin)) base.push(sel.defaultDurationMin);
    return base.filter((d) => d <= gapLen).sort((a, b) => a - b);
  }, [patients, patientId, gapLen]);

  const startOptions = React.useMemo(
    () => (context ? slotStartOptions(context.gapStartMin, context.gapEndMin, durationMin) : []),
    [context, durationMin],
  );

  // duration 変更で現在の開始時刻が収まらなくなったら先頭候補へクランプ。
  React.useEffect(() => {
    if (startMin !== null && startOptions.length > 0 && !startOptions.includes(startMin)) {
      setStartMin(startOptions[0]!);
    }
  }, [startOptions, startMin]);

  const handleSelectPatient = (id: string) => {
    setPatientId(id);
    const sel = patients.find((p) => p.id === id);
    if (sel) {
      // 患者の型の実動時間を初期値に (gap に収まらないときは gap 長へ丸めず選択不可のまま)。
      setDurationMin(sel.defaultDurationMin);
    }
  };

  const canConfirm =
    !busy &&
    patientId !== '' &&
    startMin !== null &&
    startMin + durationMin <= (context?.gapEndMin ?? 0);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !busy) onClose();
      }}
    >
      <DialogContent aria-describedby="slot-register-desc" data-testid="slot-register-dialog">
        <DialogHeader>
          <DialogTitle>空き時間に登録</DialogTitle>
          <DialogDescription id="slot-register-desc">
            {context ? (
              <>
                <span className="font-semibold text-text-primary">{context.courseLabel}</span>
                （担当: {context.staffName ?? '未割当'}） {context.weekdayLabel}曜{' '}
                <span className="tnum">
                  {fmtHM(context.gapStartMin)}〜{fmtHM(context.gapEndMin)}
                </span>{' '}
                の空き時間（{gapLen}分）に訪問を配置します。今週のみの配置で、毎週の型は
                変更しません（トーストから昇格できます）。
              </>
            ) : null}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          {patients.length > 0 ? (
            <>
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-semibold text-text-primary">利用者（プールの不足患者）</span>
                <select
                  value={patientId}
                  onChange={(e) => handleSelectPatient(e.target.value)}
                  className="rounded border border-border-default bg-bg-base px-2 py-1 text-sm"
                  data-testid="slot-register-patient-select"
                  aria-label="利用者"
                >
                  <option value="">— 選択してください —</option>
                  {patients.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.shortage > 0 ? `（不足${p.shortage}件・${p.defaultDurationMin}分）` : ''}
                    </option>
                  ))}
                </select>
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-semibold text-text-primary">開始時刻</span>
                  <select
                    value={startMin ?? ''}
                    onChange={(e) => setStartMin(Number(e.target.value))}
                    className="tnum rounded border border-border-default bg-bg-base px-2 py-1 text-sm"
                    data-testid="slot-register-start-select"
                    aria-label="開始時刻"
                    disabled={startOptions.length === 0}
                  >
                    {startOptions.map((m) => (
                      <option key={m} value={m}>
                        {fmtHM(m)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-semibold text-text-primary">時間（分）</span>
                  <select
                    value={durationMin}
                    onChange={(e) => setDurationMin(Number(e.target.value))}
                    className="tnum rounded border border-border-default bg-bg-base px-2 py-1 text-sm"
                    data-testid="slot-register-duration-select"
                    aria-label="時間（分）"
                  >
                    {durationOptions.map((d) => (
                      <option key={d} value={d}>
                        {d}分
                      </option>
                    ))}
                    {!durationOptions.includes(durationMin) ? (
                      <option value={durationMin}>{durationMin}分（空きを超過）</option>
                    ) : null}
                  </select>
                </label>
              </div>

              {patientId !== '' && startOptions.length === 0 ? (
                <div
                  className="rounded border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning"
                  data-testid="slot-register-no-fit"
                  role="alert"
                >
                  選択した時間（{durationMin}分）はこの空き（{gapLen}分）に収まりません。
                  時間を短くするか、別の空き枠を選んでください。
                </div>
              ) : null}
            </>
          ) : (
            <div
              className="rounded border border-border-default bg-bg-muted px-3 py-2 text-xs text-text-muted"
              data-testid="slot-register-no-patients"
            >
              配置候補（今週の訪問が不足しているプール患者）がいません。
            </div>
          )}

          <p className="text-[11px] text-text-muted">
            ※ 2名体制の患者はプールからのドラッグ（相方コース選択）で配置してください。
          </p>

          {/* 会議・イベント登録への切替 (カイポケ反映外を明示)。 */}
          <div className="border-t border-border-subtle pt-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="w-full justify-center gap-1.5"
              disabled={busy || !context?.canRegisterEvent}
              title={
                context?.canRegisterEvent
                  ? undefined
                  : '担当スタッフが未割当のコースにはイベントを登録できません'
              }
              onClick={onSwitchToEvent}
              data-testid="slot-register-switch-event"
            >
              会議・イベントを登録
              <span className="rounded-full border border-border-default bg-bg-muted px-1.5 py-px text-[9px] font-bold text-text-muted">
                カイポケ反映外
              </span>
            </Button>
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={busy}
            data-testid="slot-register-cancel"
          >
            キャンセル
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!canConfirm}
            onClick={() => {
              if (canConfirm && startMin !== null) {
                onRegisterVisit({ patientId, startHM: fmtHM(startMin), durationMin });
              }
            }}
            data-testid="slot-register-confirm"
          >
            {busy ? '配置中…' : 'この週に配置'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
