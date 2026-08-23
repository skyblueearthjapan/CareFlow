'use client';

/**
 * ServiceContentDialog — 「この訪問だけカイポケのサービス内容に合わせる」。
 *
 * 正典 = `docs/plans/kaipoke-service-content-design.md` §2 (mig 0078)。
 *
 * サービス内容は本来 **患者の区分 × 職員1の資格** から自動で決まる。ここは
 * 「カイポケが正でらく助のマスタが追いついていない」1 件だけを合わせるための
 * 逃げ道で、患者の区分やスタッフの資格 (= その人の全訪問に効く) は動かさない。
 *
 * API は呼ばない: `onSubmit(serviceContent | null)` を親へ渡すだけ
 * (VisitActionMenu / AddVisitDialog と同じ作法)。null = 解除 (自動判定へ戻す)。
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
import { KAIPOKE_SERVICE_CONTENT_PRESETS } from '@/lib/schemas/v2/cockpit';

/** 自由入力に切り替える擬似オプション値 (プリセットに無い例外用)。 */
const CUSTOM = '__custom__';

export interface ServiceContentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 見出しに出す患者名。 */
  patientName: string;
  /** 現在の上書き値 (null = 自動判定のまま)。 */
  current: string | null;
  submitting?: boolean;
  /** null = 解除。 */
  onSubmit: (serviceContent: string | null) => void;
}

export function ServiceContentDialog({
  open,
  onOpenChange,
  patientName,
  current,
  submitting = false,
  onSubmit,
}: ServiceContentDialogProps) {
  const presetOf = (v: string | null): string => {
    if (!v) return '';
    return (KAIPOKE_SERVICE_CONTENT_PRESETS as readonly string[]).includes(v) ? v : CUSTOM;
  };
  const [choice, setChoice] = React.useState<string>(() => presetOf(current));
  const [custom, setCustom] = React.useState<string>(() =>
    presetOf(current) === CUSTOM ? (current ?? '') : '',
  );

  // ダイアログを開き直すたびに現在値へ戻す (前回の入力が残ると誤操作の元)。
  React.useEffect(() => {
    if (!open) return;
    setChoice(presetOf(current));
    setCustom(presetOf(current) === CUSTOM ? (current ?? '') : '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, current]);

  // 自由入力はプリセットの 4 通りから外れる = カイポケ側の表記と 1 文字でも
  // 違えば突合で永久に差分が残る。押した瞬間に確定させず、値を読み返させる。
  const [confirmingCustom, setConfirmingCustom] = React.useState(false);
  React.useEffect(() => {
    if (!open) setConfirmingCustom(false);
  }, [open]);

  const value = choice === CUSTOM ? custom.trim() : choice;
  const isCustom = choice === CUSTOM;
  const canSubmit = !submitting && value.length > 0 && value !== (current ?? '');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm" data-testid="cockpit-service-content-dialog">
        <DialogHeader>
          <DialogTitle className="text-sm">カイポケのサービス内容に合わせる</DialogTitle>
          <DialogDescription className="text-[11px]">
            {patientName}様の<b>この訪問だけ</b>
            、カイポケに登録されているサービス内容へ合わせます。
            患者の訪問看護区分やスタッフの資格（＝その人の全訪問に効くマスタ）は変わりません。
          </DialogDescription>
        </DialogHeader>

        <p className="text-[11px] text-text-muted" data-testid="cockpit-service-content-current">
          現在: <b>{current ?? '自動判定（区分 × 職員1の資格）'}</b>
        </p>

        <div className="space-y-1.5">
          <Label className="text-[11px]" htmlFor="service-content-choice">
            サービス内容
          </Label>
          <select
            id="service-content-choice"
            className="w-full rounded border border-border-default bg-bg-base px-2 py-1.5 text-[12px]"
            value={choice}
            disabled={submitting}
            data-testid="cockpit-service-content-select"
            onChange={(e) => setChoice(e.target.value)}
          >
            <option value="">選んでください</option>
            {KAIPOKE_SERVICE_CONTENT_PRESETS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
            <option value={CUSTOM}>その他（自由入力）</option>
          </select>
          {isCustom ? (
            <Input
              className="text-[12px]"
              placeholder="例: 精神基本療養費Ⅲ・正看"
              maxLength={64}
              value={custom}
              disabled={submitting}
              data-testid="cockpit-service-content-custom"
              onChange={(e) => {
                setCustom(e.target.value);
                setConfirmingCustom(false);
              }}
            />
          ) : null}
        </div>

        {confirmingCustom ? (
          <p
            className="rounded border border-warning-strong/40 bg-warning-subtle/40 px-2 py-1.5 text-[11px] text-warning-strong"
            data-testid="cockpit-service-content-custom-confirm"
          >
            ⚠ 「{value}」は選択肢にない値です。カイポケに登録されている表記と
            <b>1 文字でも違う</b>と、突合でずっと差分として残ります。
            この表記で間違いなければ、もう一度「これに合わせる」を押してください。
          </p>
        ) : null}

        <DialogFooter className="gap-1">
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={() => onOpenChange(false)}
          >
            やめる
          </Button>
          {/* 解除 = 自動判定へ戻す。上書きが無いときは押せない (何も起きないため)。 */}
          <Button
            type="button"
            variant="outline"
            disabled={submitting || current == null}
            data-testid="cockpit-service-content-clear"
            onClick={() => onSubmit(null)}
          >
            解除
          </Button>
          <Button
            type="button"
            disabled={!canSubmit}
            data-testid="cockpit-service-content-confirm"
            onClick={() => {
              // 自由入力は 2 段クリック (1 回目は注意書きを出すだけ)。
              if (isCustom && !confirmingCustom) {
                setConfirmingCustom(true);
                return;
              }
              onSubmit(value);
            }}
          >
            {isCustom && confirmingCustom ? 'この表記で確定する' : 'これに合わせる'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
