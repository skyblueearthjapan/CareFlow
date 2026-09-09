'use client';

/**
 * usePatientStatusGate — 患者フォームの保存を「ステータス変更の確認」で包む共通フック。
 *
 * 正典 = `docs/plans/patient-status-schedule-design-2026-09-09.md` §3-6 / §7-4。
 *
 * なぜフックか:
 *   ステータスを変える入口は 3 つ（PC 編集ページ / 盤面の患者編集ダイアログ / 現場カルテ）
 *   あり、どれも同じ `PatientForm` 系の保存に落ちる。確認と API 呼び分けを各画面に
 *   書くと必ずズレるので 1 箇所に集約する。
 *
 * 流れ:
 *   1. `wrapSubmit(submit)` が返す関数をフォームの onSubmit に渡す。
 *   2. `values.status` が変わっていない、または向きが `none`（例: 入院中 → 一時休止＝
 *      どちらも非稼働）なら **そのまま** `submit(values, { omitStatus: false })`。
 *      PATCH が status を運び、BE 側の安全網が面倒を見る。
 *   3. 稼働中 ⇄ 非稼働（direction が deactivate / reactivate）なら
 *      `PatientStatusChangeDialog` を開いて保存を保留する。
 *   4. ダイアログが `POST /patients/{id}/status-change` を成功させたら
 *      `submit(values, { omitStatus: true })` を実行し、**status 以外**のフォーム項目を
 *      PATCH で保存する（status を再送すると連動処理を巻き戻してしまう）。
 *   5. キャンセルは静かに握りつぶす（フォームは開いたまま・API は 1 本も飛ばない）。
 */
import * as React from 'react';

import { toast } from '@/components/ui/sonner';
import {
  STATUS_LABEL,
  normalizePatientStatus,
  statusChangeDirection,
  type PatientFormValues,
  type PatientStatus,
} from '@/lib/schemas/patient';
import type { StatusChangeResult } from '@/lib/schemas/patientStatus';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface PatientStatusGateSubmitOptions {
  /** true のとき PATCH から `status` を落とす（status-change API が変更済み）。 */
  omitStatus: boolean;
}

/** ゲートが包む「本来の保存処理」。 */
export type PatientStatusGateSubmit = (
  values: PatientFormValues,
  options: PatientStatusGateSubmitOptions,
) => Promise<void>;

export interface UsePatientStatusGateOptions {
  patientId: string;
  patientName: string;
  /** 変更前のステータス（サーバー値・未ロード時は 'active' 相当で構わない）。 */
  initialStatus: string | null | undefined;
  /**
   * status-change 成功時の通知。省略時は sonner トースト。
   * 現場ボード `/m` のように独自トーストを持つ画面はここで差し替える。
   */
  onStatusChanged?: (message: string, result: StatusChangeResult) => void;
}

export interface PatientStatusGateDialogProps {
  open: boolean;
  patientId: string;
  patientName: string;
  fromStatus: PatientStatus;
  toStatus: PatientStatus;
  onCancel: () => void;
  onDone: (result: StatusChangeResult) => void;
}

export interface PatientStatusGate {
  /** フォームの onSubmit をこれで包む。 */
  wrapSubmit: (submit: PatientStatusGateSubmit) => (values: PatientFormValues) => Promise<void>;
  /** `<PatientStatusChangeDialog {...gate.dialogProps} />` にそのまま展開する。 */
  dialogProps: PatientStatusGateDialogProps;
  /** 確認待ちかどうか（保存ボタンの見た目を変えたい画面向け）。 */
  isAwaitingConfirm: boolean;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * 変更完了トーストの文言。3 入口で同じ文にするためここに置く。
 * 例:「入院中にしました（予定 9 件を取消）」「稼働中に戻しました（8 件を作成）」
 */
export function formatStatusChangeMessage(result: StatusChangeResult): string {
  const to = normalizePatientStatus(result.patient.status as string | null | undefined);
  if (result.direction === 'reactivate') {
    const created = result.regenerated?.created ?? 0;
    return created > 0 ? `稼働中に戻しました（${created} 件を作成）` : '稼働中に戻しました';
  }
  if (result.direction === 'deactivate') {
    const base =
      result.cancelled_count > 0
        ? `${STATUS_LABEL[to]}にしました（予定 ${result.cancelled_count} 件を取消）`
        : `${STATUS_LABEL[to]}にしました`;
    return result.special_period?.action === 'end' ? `${base}・特別訪問週間を終了` : base;
  }
  return `${STATUS_LABEL[to]}にしました`;
}

// ─── Hook ────────────────────────────────────────────────────────────────────

interface PendingSave {
  values: PatientFormValues;
  submit: PatientStatusGateSubmit;
  toStatus: PatientStatus;
  resolve: () => void;
  reject: (e: unknown) => void;
}

export function usePatientStatusGate({
  patientId,
  patientName,
  initialStatus,
  onStatusChanged,
}: UsePatientStatusGateOptions): PatientStatusGate {
  const fromStatus = normalizePatientStatus(initialStatus);
  const [pending, setPending] = React.useState<PendingSave | null>(null);
  // 再入ガード用のミラー。state は次のレンダーまで反映されないので、
  // 「確認中にもう一度保存を押す」を止めるには ref で見る必要がある。
  const pendingRef = React.useRef<PendingSave | null>(null);

  const wrapSubmit = React.useCallback(
    (submit: PatientStatusGateSubmit) => async (values: PatientFormValues) => {
      // 確認ダイアログを出している間の 2 度押しは黙って捨てる
      // (2 本目の status-change / PATCH が走るのを防ぐ)。
      if (pendingRef.current) return;
      const next = normalizePatientStatus(values.status);
      const direction = statusChangeDirection(fromStatus, next);
      // 同じ値、または非稼働どうしの移動（入院中 → 一時休止 など）は素通し。
      // PATCH が status を運び、BE の安全網が必要なら連動を走らせる。
      if (next === fromStatus || direction === 'none') {
        await submit(values, { omitStatus: false });
        return;
      }
      await new Promise<void>((resolve, reject) => {
        const entry: PendingSave = { values, submit, toStatus: next, resolve, reject };
        pendingRef.current = entry;
        setPending(entry);
      });
    },
    [fromStatus],
  );

  const handleCancel = React.useCallback(() => {
    const current = pendingRef.current;
    pendingRef.current = null;
    setPending(null);
    // 静かに終了する（reject すると呼び出し側の catch が「更新に失敗」を出す）。
    current?.resolve();
  }, []);

  const handleDone = React.useCallback(
    (result: StatusChangeResult) => {
      const current = pendingRef.current;
      pendingRef.current = null;
      setPending(null);
      if (!current) return;
      const message = formatStatusChangeMessage(result);
      if (onStatusChanged) onStatusChanged(message, result);
      else toast.success(message);
      // status は status-change が済ませたので PATCH からは落とす。
      void current
        .submit(current.values, { omitStatus: true })
        .then(() => current.resolve())
        .catch((e: unknown) => current.reject(e));
    },
    [onStatusChanged],
  );

  return {
    wrapSubmit,
    isAwaitingConfirm: pending !== null,
    dialogProps: {
      open: pending !== null,
      patientId,
      patientName,
      fromStatus,
      // 閉じている間は fromStatus と同値にしておく（影響 GET は open=false で走らない）。
      toStatus: pending?.toStatus ?? fromStatus,
      onCancel: handleCancel,
      onDone: handleDone,
    },
  };
}
