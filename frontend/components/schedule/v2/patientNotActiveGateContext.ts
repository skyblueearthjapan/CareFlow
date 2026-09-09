'use client';

/**
 * 非稼働患者の入口ガード — **軽い側**（context / hook / 422 の判定）。
 *
 * 設計 = `docs/plans/patient-status-schedule-design-2026-09-09.md` §3-3・§6-C Q16・§7-4。
 * ダイアログ本体と Provider は `PatientNotActiveGate.tsx`（app root に 1 回だけ置く）。
 *
 * **なぜファイルを分けるか**: `useGuardedMutation` は盤面・現場シートなど多数の
 * コンポーネントが import する。Provider 側は `usePatient`（=`lib/queries/patients`）と
 * `PatientStatusChangeDialog` に依存し、そこから `lib/schemas/patient` まで芋づるに
 * 読み込まれるため、既存テストの部分モック（`vi.mock('@/lib/schemas/patient')`）が
 * 壊れる。呼び出し側が触るのはこの軽いファイルだけにして依存を持ち込まない
 * （zod ではなく手書きの型ガードで 422 を判定しているのも同じ理由）。
 */
import * as React from 'react';
import type { MutateOptions, UseMutationResult } from '@tanstack/react-query';

import { ApiError } from '@/lib/api-client';
import type { PatientNotActiveDetail } from '@/lib/schemas/patientStatus';

// ─── 422 detail の取り出し ───────────────────────────────────────────────────

/**
 * 422 `{"detail": {"code": "patient_not_active", …}}` を取り出す。
 * それ以外（別コード・別ステータス・非 ApiError）は `null`。
 *
 * 寛容パース: 欠けたフィールドは空文字 / false に倒す（`can_override` は
 * **明示的に true のときだけ** true = 上書き導線を出す条件を厳しめに取る）。
 */
export function extractPatientNotActiveDetail(err: unknown): PatientNotActiveDetail | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const body: unknown = err.body;
  if (typeof body !== 'object' || body === null) return null;
  const detail: unknown = (body as { detail?: unknown }).detail;
  if (typeof detail !== 'object' || detail === null) return null;
  const rec = detail as Record<string, unknown>;
  if (rec.code !== 'patient_not_active') return null;
  if (typeof rec.patient_id !== 'string' || rec.patient_id.length === 0) return null;
  return {
    code: 'patient_not_active',
    patient_id: rec.patient_id,
    status: typeof rec.status === 'string' ? rec.status : '',
    status_label: typeof rec.status_label === 'string' ? rec.status_label : '',
    can_override: rec.can_override === true,
    message: typeof rec.message === 'string' ? rec.message : '',
  };
}

/** 「◯◯様は入院中のため…」から氏名を拾う（患者取得が間に合わないときの保険）。 */
export function patientNameFromMessage(message: string | null | undefined): string | null {
  if (!message) return null;
  const m = /^\s*(.+?)様/.exec(message);
  const name = m?.[1] ?? '';
  return name.length > 0 ? name : null;
}

/** ゲートが引き取る（＝ダイアログを出す）エラーか。 */
export function isGateHandledError(err: unknown): boolean {
  const detail = extractPatientNotActiveDetail(err);
  return detail !== null && detail.can_override;
}

// ─── Context ─────────────────────────────────────────────────────────────────

export interface PatientNotActiveGateApi {
  /**
   * `fn` を実行し、422 `patient_not_active` なら復帰導線を挟んで **1 回だけ**再実行する。
   * 承諾されなければ元のエラーで reject する（呼び出し側の catch は不変）。
   */
  runGuarded: <T>(fn: () => Promise<T>) => Promise<T>;
}

/** Provider が無い文脈（テスト・非対象 UI）では素通し。 */
const PASSTHROUGH: PatientNotActiveGateApi = {
  runGuarded: <T>(fn: () => Promise<T>) => fn(),
};

export const PatientNotActiveGateContext =
  React.createContext<PatientNotActiveGateApi>(PASSTHROUGH);

export function usePatientNotActiveGate(): PatientNotActiveGateApi {
  return React.useContext(PatientNotActiveGateContext);
}

// ─── Mutation ラッパ ─────────────────────────────────────────────────────────

/**
 * TanStack の mutation 結果を包み、`mutate` / `mutateAsync` をゲート経由にする。
 *
 * - `mutate` は **元の `mutation.mutate` をそのまま呼ぶ**（挙動もスパイも従来どおり）。
 *   結果は per-call コールバックを包んで拾い、422 `patient_not_active` のときだけ
 *   ゲートへ流す。
 * - 呼び出し単位の `onError` / `onSettled` は、ダイアログを出した試行では
 *   **握りつぶし**、「やめる」で確定した時点で同じ引数のまま呼び直す。
 *   → 「入れられません」トーストとダイアログが同時に出ない／断ったときの
 *     見え方は改修前と同じ（トースト 1 回）。
 * - `onSuccess` は react-query にそのまま渡すので `context`（`onMutate` の戻り値）は本物。
 * - `useMutation` 定義側のコールバック（invalidate など）は試行ごとに従来どおり動く。
 */
export function useGuardedMutation<TData, TError, TVariables, TContext>(
  mutation: UseMutationResult<TData, TError, TVariables, TContext>,
): UseMutationResult<TData, TError, TVariables, TContext> {
  const { runGuarded } = usePatientNotActiveGate();

  return React.useMemo(() => {
    type Options = MutateOptions<TData, TError, TVariables, TContext>;
    type Deferred = { run: (() => void) | null };

    /** ゲートが引き取った試行の per-call コールバックを「あとで呼ぶ」側へ積む。 */
    const defer = (deferred: Deferred, fn: () => void) => {
      const prev = deferred.run;
      deferred.run = () => {
        prev?.();
        fn();
      };
    };

    const guarded = (options: Options | undefined, deferred: Deferred): Options => ({
      onSuccess: options?.onSuccess,
      onError: (error, variables, onMutateResult, context) => {
        if (isGateHandledError(error)) {
          defer(deferred, () => options?.onError?.(error, variables, onMutateResult, context));
          return;
        }
        options?.onError?.(error, variables, onMutateResult, context);
      },
      onSettled: (data, error, variables, onMutateResult, context) => {
        if (error !== null && isGateHandledError(error)) {
          defer(deferred, () =>
            options?.onSettled?.(data, error, variables, onMutateResult, context),
          );
          return;
        }
        options?.onSettled?.(data, error, variables, onMutateResult, context);
      },
    });

    /**
     * ゲートを抜けたあとの後始末。**握りつぶしたコールバックを呼び直すのは
     * 「ユーザーが断った（= 最終エラーがゲート対象の 422）」ときだけ**。
     *
     * 復帰後の再実行が別の理由（例: Phase 1 の再生成で枠が埋まって 409）で落ちた場合、
     * その 409 は素通しで onError / onSettled が既に 1 回走っている。ここで積み残しを
     * 流すと **古い 422「入院中のため…」でもう 1 回**呼ばれてしまうため流さない。
     */
    const settle = (deferred: Deferred, error: unknown) => {
      if (isGateHandledError(error)) deferred.run?.();
      deferred.run = null;
    };

    const mutateAsync = (variables: TVariables, options?: Options): Promise<TData> => {
      const deferred: Deferred = { run: null };
      // options 未指定なら **何も渡さない**（`mutateAsync(vars)` の呼び出し形を変えない。
      // 握りつぶすコールバックも無いので包む必要が無い）。
      return runGuarded(() => {
        deferred.run = null; // 試行ごとにリセット（前の試行の積み残しを持ち越さない）
        return options === undefined
          ? mutation.mutateAsync(variables)
          : mutation.mutateAsync(variables, guarded(options, deferred));
      }).catch((error: unknown) => {
        settle(deferred, error);
        throw error;
      });
    };

    const mutate = (variables: TVariables, options?: Options): void => {
      const deferred: Deferred = { run: null };
      // `mutation.mutate` を経由するのが要点（fire-and-forget の作法・スパイ・
      // 定義側コールバックを一切変えない）。結果は onSettled で Promise に橋渡しする。
      const attempt = () =>
        new Promise<TData>((resolve, reject) => {
          deferred.run = null; // 試行ごとにリセット
          const base = guarded(options, deferred);
          mutation.mutate(variables, {
            onSuccess: base.onSuccess,
            onError: base.onError,
            onSettled: (data, error, vars, onMutateResult, context) => {
              base.onSettled?.(data, error, vars, onMutateResult, context);
              if (error !== null) reject(error);
              else resolve(data as TData);
            },
          });
        });
      void runGuarded(attempt).catch((error: unknown) => {
        // `mutate` は元から reject を飲み込む（呼び出し側は callbacks で受ける）。
        settle(deferred, error);
      });
    };

    return { ...mutation, mutate, mutateAsync } as UseMutationResult<
      TData,
      TError,
      TVariables,
      TContext
    >;
  }, [mutation, runGuarded]);
}
