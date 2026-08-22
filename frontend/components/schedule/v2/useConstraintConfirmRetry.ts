'use client';

/**
 * useConstraintConfirmRetry — NG スタッフ / 性別制限の 422 を「確認して通す」ための共有フック.
 *
 * 設計書 `docs/plans/patient-ng-staff-design.md` §7-2 (acknowledge 方式・BE が正)。
 * 人手操作系の 5 エンドポイント (visit-move-week-only / place-and-fix /
 * special-visit-marks/{id}/place / PUT fixed-visits / apply-individual) は、
 * 担当が対象患者の NG スタッフ・性別制限に抵触すると 422 を返す:
 *
 *   { detail: { code: 'constraint_confirmation_required', warnings: [...] } }
 *
 * 呼び出し側は「失敗したときの catch / onError」で `capture()` に渡すだけでよい。
 * 確認ダイアログの open / warnings / applying と OK 時の再実行は本フックが握る。
 *
 * 使い方 (mutateAsync 系):
 *
 *   const cc = useConstraintConfirmRetry();
 *   const doMove = async (acknowledge = false) => {
 *     try {
 *       await mut.mutateAsync({ ...req, ...ackFlag(acknowledge) });
 *     } catch (err) {
 *       if (!acknowledge && cc.capture(err, () => doMove(true), MOVE_TEXT)) return;
 *       toast.error(...);
 *     }
 *   };
 *   ...
 *   <ConstraintOverrideConfirmDialog {...cc.dialogProps} />
 *
 * 使い方 (mutate + onError コールバック系) も同じ: onError の中で capture するだけ。
 *
 * 注意: retry は **自前でエラー処理を完結させる** こと (本フックは retry の例外を
 * 握りつぶしてダイアログを閉じるだけ)。無限ループを防ぐため、呼び出し側は
 * acknowledge=true の再送では capture を呼ばない。
 */
import * as React from 'react';

import { ApiError } from '@/lib/api-client';
import {
  parseConstraintConfirmationDetail,
  type ConstraintWarning,
} from '@/lib/schemas/patient_ng_staff';

import type { ConstraintOverrideConfirmDialogProps } from './ConstraintOverrideConfirmDialog';

/** 経路ごとの文言 (省略時はダイアログ既定 = コース担当変更の文言)。 */
export interface ConstraintConfirmText {
  title?: string;
  description?: string;
  confirmLabel?: string;
}

interface PendingConstraintConfirm extends ConstraintConfirmText {
  warnings: ConstraintWarning[];
  retry: () => void | Promise<void>;
  /** 「やめる」/ reset で確認を捨てたときに呼ぶ後始末 (キューの中止など)。 */
  onAbort?: () => void;
}

export interface UseConstraintConfirmRetryResult {
  /**
   * 422 `constraint_confirmation_required` なら確認ダイアログを開いて true を返す。
   * それ以外のエラーでは何もせず false (= 呼び出し側が通常のエラー処理をする)。
   */
  capture: (
    err: unknown,
    retry: () => void | Promise<void>,
    text?: ConstraintConfirmText,
    /**
     * 確認を捨てたとき (「やめる」/ `reset()`) に呼ばれる。複数件を順に流す
     * 呼び出し側が「中止した」と分かる唯一の合図なので、後始末はここでする。
     */
    onAbort?: () => void,
  ) => boolean;
  /** ConstraintOverrideConfirmDialog にそのまま spread する。 */
  dialogProps: ConstraintOverrideConfirmDialogProps;
  /** 週切替・患者切替などで確認待ちを捨てる (待機中の onAbort を呼ぶ)。 */
  reset: () => void;
}

/** リクエストへ足す acknowledge フラグ (true のときだけキーを生やす)。 */
export function ackFlag(acknowledge: boolean): { acknowledge_constraint_warnings?: true } {
  return acknowledge ? { acknowledge_constraint_warnings: true } : {};
}

export function useConstraintConfirmRetry(): UseConstraintConfirmRetryResult {
  const [pending, setPending] = React.useState<PendingConstraintConfirm | null>(null);
  const [applying, setApplying] = React.useState(false);
  /**
   * 「今開いている確認」の実体。`reset()` は useCallback([]) で state を読めないため、
   * onAbort を取り出す口として ref で持つ (state と同じものを指す)。
   */
  const pendingRef = React.useRef<PendingConstraintConfirm | null>(null);

  const capture = React.useCallback(
    (
      err: unknown,
      retry: () => void | Promise<void>,
      text?: ConstraintConfirmText,
      onAbort?: () => void,
    ): boolean => {
      const detail =
        err instanceof ApiError && err.status === 422
          ? parseConstraintConfirmationDetail(err.body)
          : null;
      if (!detail) return false;
      const next: PendingConstraintConfirm = { warnings: detail.warnings, retry, onAbort, ...text };
      pendingRef.current = next;
      setPending(next);
      return true;
    },
    [],
  );

  const reset = React.useCallback(() => {
    const cur = pendingRef.current;
    pendingRef.current = null;
    setPending(null);
    setApplying(false);
    // 「やめる」も週切替も、待っている側から見れば同じ「中止」。
    cur?.onAbort?.();
  }, []);

  const onConfirm = React.useCallback(() => {
    if (!pending) return;
    const current = pending;
    setApplying(true);
    void (async () => {
      try {
        // retry 側が自前で成功/失敗のトーストを出す契約 (例外は握りつぶす)。
        await current.retry();
      } finally {
        setApplying(false);
        // retry の途中で**次の**確認が積まれることがある (複数件を順に流す経路)。
        // 無条件に null にするとその 2 件目が消えてキューが止まるので、
        // 自分が出したものだけを畳む。
        if (pendingRef.current === current) pendingRef.current = null;
        setPending((cur) => (cur === current ? null : cur));
      }
    })();
  }, [pending]);

  const dialogProps = React.useMemo<ConstraintOverrideConfirmDialogProps>(
    () => ({
      open: pending !== null,
      warnings: pending?.warnings ?? [],
      applying,
      title: pending?.title,
      description: pending?.description,
      confirmLabel: pending?.confirmLabel,
      onCancel: reset,
      onConfirm,
    }),
    [pending, applying, reset, onConfirm],
  );

  return { capture, dialogProps, reset };
}
