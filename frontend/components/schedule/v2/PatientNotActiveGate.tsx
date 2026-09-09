'use client';

/**
 * PatientNotActiveGate — 非稼働患者の入口ガード（Phase 2 / 設計 §3-3・§6-C Q16・§7-4）。
 *
 * BE は「稼働中でない患者様を予定に入れる」API を 422 で止める:
 *
 * ```json
 * { "detail": { "code": "patient_not_active", "patient_id": "…", "status": "admitted",
 *               "status_label": "入院中", "can_override": true,
 *               "message": "◯◯様は入院中のため予定に入れられません" } }
 * ```
 *
 * PO 決定（Q16）は「基本は止める。それでも進めるなら **ステータスを稼働中に変えてから**
 * 進める。入院中のまま進む道は作らない」。本モジュールはその導線を 1 箇所に集約する:
 *
 *   1. 操作（mutate / mutateAsync）が 422 `patient_not_active` で落ちる
 *   2. 案内ダイアログ「予定に入れられません」→「稼働中にして続ける」/「やめる」
 *   3. 「続ける」→ `PatientStatusChangeDialog(toStatus='active')`（＝ Phase 1 の復帰フロー。
 *      型から予定を作り直す件数まで見せて確定する）
 *   4. 復帰が完了したら **元の操作を 1 回だけ**再実行して、その結果で resolve する
 *
 * トーストの扱い（重複防止の取り決め）:
 *   - 本ゲートが出すトーストは **ステータス変更の成功 1 本だけ**
 *     （`formatStatusChangeMessage` = Phase 1 の 3 入口と同じ文言）。
 *     操作そのものの成否は呼び出し側の既存 catch / callbacks に任せる。
 *   - 「やめる」で閉じたときは **元の ApiError をそのまま reject** する。つまり既存の
 *     `toast.error(apiErrorDetail(err) ?? …)` が今までどおり 1 回だけ出る（挙動不変）。
 *     `mutate(vars, { onError })` の呼び出し単位コールバックは、ダイアログを出す試行では
 *     いったん握りつぶし、「やめる」で確定した時点で同じ引数のまま呼び直す
 *     （＝ダイアログとトーストが同時に出ない・断ったときのトーストは 1 回）。
 *   - 再実行が失敗したら、その新しいエラーを reject する（同じく呼び出し側が 1 回出す）。
 *   - `can_override` が false の 422、および `patient_not_active` 以外のエラーは
 *     ダイアログを出さずに素通しする。
 *
 * 使い方:
 *   - `<PatientNotActiveGateProvider>` を app root（`app/providers.tsx`）に 1 回だけ置く。
 *     PC / 現場ボード / モバイルは同じ `Providers` を共有するので 1 箇所で足りる。
 *   - 呼び出し側は `useGuardedMutation(useXxxMutation())` で包むだけ。個別に包みたい
 *     ときは `usePatientNotActiveGate().runGuarded(fn)`。**import 元は軽い
 *     `patientNotActiveGateContext.ts`**（本ファイルは `usePatient` や Phase 1 の
 *     ダイアログまで芋づるに読み込むため、呼び出し側からは import しない）。
 *   - Provider が無い文脈（単体テストなど）では素通し実装が返るため、
 *     既存コンポーネントのテストは今までどおり動く。
 */
import * as React from 'react';

import { PatientStatusChangeDialog } from '@/components/patients/PatientStatusChangeDialog';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { formatStatusChangeMessage } from '@/lib/hooks/usePatientStatusGate';
import { usePatient } from '@/lib/queries/patients';
import { normalizePatientStatus } from '@/lib/schemas/patient';
import type { PatientNotActiveDetail, StatusChangeResult } from '@/lib/schemas/patientStatus';

import {
  PatientNotActiveGateContext,
  extractPatientNotActiveDetail,
  patientNameFromMessage,
  type PatientNotActiveGateApi,
} from './patientNotActiveGateContext';

// 呼び出し側が「ゲートのことは PatientNotActiveGate から」で済むよう re-export する
// （実体は軽い context 側。盤面などの import 元は context ファイルを直接使う）。
export {
  extractPatientNotActiveDetail,
  isGateHandledError,
  patientNameFromMessage,
  useGuardedMutation,
  usePatientNotActiveGate,
  type PatientNotActiveGateApi,
} from './patientNotActiveGateContext';

// ─── Provider ────────────────────────────────────────────────────────────────

/** ゲートで待たせている操作 1 件。 */
interface GateEntry {
  /** 元の操作。承諾されたら 1 回だけ再実行する。 */
  retry: () => Promise<unknown>;
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
  /** その操作が最初に受け取った ApiError（やめたときはこれで reject する）。 */
  originalError: unknown;
}

/**
 * 同じ患者様に対する同時発火は 1 枚のダイアログにまとめる。
 * 実例: `AddVisitAnywhereDialog` は複数週へ `Promise.all` で並列に投げるため、
 * 1 患者で 422 が同時に何本も返る。1 本だけ通して残りを捨てると、
 * 稼働中に戻したあとに一部の週だけ入らない（＝取りこぼし）。
 */
interface PendingGate {
  detail: PatientNotActiveDetail;
  entries: GateEntry[];
}

type Phase = 'confirm' | 'status';

export function PatientNotActiveGateProvider({ children }: { children: React.ReactNode }) {
  // 実体は ref（reject / 再実行という副作用を state updater の中で起こさないため。
  // StrictMode の二重呼び出しで元の操作が 2 回走るのを避ける）。state は描画用のミラー。
  const pendingRef = React.useRef<PendingGate | null>(null);
  const [pending, setPending] = React.useState<PendingGate | null>(null);
  const [phase, setPhase] = React.useState<Phase>('confirm');

  const runGuarded = React.useCallback(<T,>(fn: () => Promise<T>): Promise<T> => {
    return fn().catch((err: unknown) => {
      const detail = extractPatientNotActiveDetail(err);
      if (!detail || !detail.can_override) throw err;
      const current = pendingRef.current;
      // 別の患者様のゲートが既に開いているなら、ダイアログは 1 枚に保ちたいので
      // **あとから来たほう**を元のエラーで畳む（先に人が判断しているものを消さない）。
      if (current && current.detail.patient_id !== detail.patient_id) throw err;
      return new Promise<T>((resolve, reject) => {
        const entry: GateEntry = {
          retry: fn as () => Promise<unknown>,
          resolve: resolve as (value: unknown) => void,
          reject,
          originalError: err,
        };
        if (current) {
          // 同じ患者様 = 同じ判断でよいので、開いているダイアログに相乗りさせる。
          current.entries.push(entry);
          return;
        }
        const next: PendingGate = { detail, entries: [entry] };
        pendingRef.current = next;
        setPending(next);
        setPhase('confirm');
      });
    });
  }, []);

  const api = React.useMemo<PatientNotActiveGateApi>(() => ({ runGuarded }), [runGuarded]);

  const take = React.useCallback((): PendingGate | null => {
    const cur = pendingRef.current;
    pendingRef.current = null;
    setPending(null);
    setPhase('confirm');
    return cur;
  }, []);

  const abort = React.useCallback(() => {
    const cur = take();
    // 待たせていた全部を **それぞれの元のエラー** で畳む（呼び出し側の既存
    // トースト経路が今までどおり 1 回ずつ出る）。
    for (const entry of cur?.entries ?? []) entry.reject(entry.originalError);
  }, [take]);

  const accept = React.useCallback(() => setPhase('status'), []);

  const finish = React.useCallback(
    (result: StatusChangeResult) => {
      const cur = take();
      // Phase 1 の 3 入口と同じ文言でステータス変更の結果を知らせる
      // （「稼働中に戻しました（8 件を作成）」）。
      toast.success(formatStatusChangeMessage(result));
      // 復帰が済んだので、待たせていた操作をそれぞれ 1 回だけ再実行する。
      for (const entry of cur?.entries ?? []) entry.retry().then(entry.resolve, entry.reject);
    },
    [take],
  );

  return (
    <PatientNotActiveGateContext.Provider value={api}>
      {children}
      {pending ? (
        <PatientNotActiveGateDialogs
          key={pending.detail.patient_id}
          detail={pending.detail}
          phase={phase}
          onAbort={abort}
          onAccept={accept}
          onFinish={finish}
        />
      ) : null}
    </PatientNotActiveGateContext.Provider>
  );
}

// ─── ダイアログ ──────────────────────────────────────────────────────────────

function PatientNotActiveGateDialogs({
  detail,
  phase,
  onAbort,
  onAccept,
  onFinish,
}: {
  detail: PatientNotActiveDetail;
  phase: Phase;
  onAbort: () => void;
  onAccept: () => void;
  onFinish: (result: StatusChangeResult) => void;
}) {
  // 氏名は患者マスタから取る（キャッシュに載っていれば即時）。取れないうちは
  // メッセージ「◯◯様は…」から拾い、それも無ければ汎称にする。
  const patientQuery = usePatient(detail.patient_id);
  const patientName =
    // 後ろに「様」を付けて使うので、代替も敬称抜きにする（「この患者様様」にならないように）。
    patientQuery.data?.name ?? patientNameFromMessage(detail.message) ?? 'この患者';

  if (phase === 'status') {
    return (
      <PatientStatusChangeDialog
        open
        patientId={detail.patient_id}
        patientName={patientName}
        fromStatus={normalizePatientStatus(detail.status)}
        toStatus="active"
        onCancel={onAbort}
        onDone={onFinish}
      />
    );
  }

  const statusLabel = detail.status_label || '稼働中以外';
  const message = detail.message || `${patientName}様は${statusLabel}のため予定に入れられません`;

  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onAbort();
      }}
    >
      <DialogContent className="max-w-md" data-testid="patient-not-active-gate">
        <DialogHeader>
          <DialogTitle data-testid="patient-not-active-title">予定に入れられません</DialogTitle>
          <DialogDescription className="text-sm text-text-secondary">
            ステータスが「{statusLabel}」の患者様は予定に入れられません。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 text-sm text-text-primary" data-testid="patient-not-active-body">
          <p data-testid="patient-not-active-message">{message}</p>
          <p>ステータスを稼働中に変更しますか？</p>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onAbort}
            data-testid="patient-not-active-cancel"
          >
            やめる
          </Button>
          <Button type="button" onClick={onAccept} data-testid="patient-not-active-confirm">
            稼働中にして続ける
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
