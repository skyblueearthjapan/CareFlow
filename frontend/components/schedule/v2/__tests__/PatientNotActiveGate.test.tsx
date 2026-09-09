/**
 * PatientNotActiveGate — 非稼働患者の入口ガード (Phase 2 / 設計 §3-3・§6-C Q16)。
 *
 * 固定したいこと:
 *   1. 422 `patient_not_active` → 案内ダイアログが出る
 *   2. 「稼働中にして続ける」→ PatientStatusChangeDialog → onDone で **元の操作を 1 回だけ**再実行
 *      (+ Phase 1 と同じ文言の成功トースト)
 *   3. 「やめる」→ **元のエラー**で reject (既存のトースト経路は今までどおり 1 回)
 *   4. 別のエラー (500 / 別コード / can_override=false) はダイアログを出さず素通し
 *   5. `useGuardedMutation` の `mutate` は、ダイアログを出す試行の onError を
 *      握りつぶし「やめる」で初めて呼ぶ (トーストとダイアログの二重表示を防ぐ)。
 *      **再実行が別のエラー (409 など) で落ちたときは古い 422 を流さない**。
 *   6. 同じ患者様の同時発火 (Promise.all) は 1 枚のダイアログに相乗りし、承諾で全部再実行
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// ── PatientStatusChangeDialog は Phase 1 の実物 (react-query / next-auth 依存) なので
//    「稼働中にする」ボタンだけの最小スタブへ差し替える。
const statusDialogSpy = vi.fn();
/** 復帰完了の戻り値 (formatStatusChangeMessage が見る形)。 */
const STATUS_RESULT = {
  direction: 'reactivate' as const,
  patient: { status: 'active' },
  cancelled_count: 0,
  regenerated: { created: 3, weeks: [] },
};

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/components/patients/PatientStatusChangeDialog', () => ({
  PatientStatusChangeDialog: (props: {
    open: boolean;
    patientId: string;
    patientName: string;
    fromStatus: string;
    toStatus: string;
    onCancel: () => void;
    onDone: (result: unknown) => void;
  }) => {
    statusDialogSpy(props);
    if (!props.open) return null;
    return (
      <div data-testid="stub-status-dialog">
        <span data-testid="stub-status-name">{props.patientName}</span>
        <span data-testid="stub-status-from">{props.fromStatus}</span>
        <span data-testid="stub-status-to">{props.toStatus}</span>
        <button type="button" onClick={() => props.onDone(STATUS_RESULT)}>
          スタブ: 稼働中にした
        </button>
        <button type="button" onClick={props.onCancel}>
          スタブ: やめる
        </button>
      </div>
    );
  },
}));

// ── 氏名取得 (usePatient) はネットワークに出ないようスタブ。
vi.mock('@/lib/queries/patients', () => ({
  usePatient: () => ({ data: { id: 'p1', name: '小湊 花子' } }),
}));

import { toast } from '@/components/ui/sonner';
import { ApiError } from '@/lib/api-client';
import {
  PatientNotActiveGateProvider,
  extractPatientNotActiveDetail,
  patientNameFromMessage,
  useGuardedMutation as useGuardedMutationForTest,
  usePatientNotActiveGate,
} from '../PatientNotActiveGate';

// ─── Helpers ─────────────────────────────────────────────────────────────────

const PATIENT_ID = '11111111-2222-3333-4444-555555555555';

const OTHER_PATIENT_ID = '99999999-8888-7777-6666-555555555555';

function notActiveError(overrides: Record<string, unknown> = {}): ApiError {
  return new ApiError('API 422 (/x)', 422, {
    detail: {
      code: 'patient_not_active',
      patient_id: PATIENT_ID,
      status: 'admitted',
      status_label: '入院中',
      can_override: true,
      message: '小湊 花子様は入院中のため予定に入れられません',
      ...overrides,
    },
  });
}

/** runGuarded を 1 回叩き、結果 / エラーを画面に出すだけの被験体。 */
function Subject({ fn }: { fn: () => Promise<string> }) {
  const { runGuarded } = usePatientNotActiveGate();
  const [ok, setOk] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  return (
    <div>
      <button
        type="button"
        onClick={() => {
          void runGuarded(fn).then(
            (v) => setOk(v),
            (e: unknown) => setErr(e instanceof Error ? e.message : String(e)),
          );
        }}
      >
        実行
      </button>
      {ok ? <span data-testid="subject-ok">{ok}</span> : null}
      {err ? <span data-testid="subject-err">{err}</span> : null}
    </div>
  );
}

function renderSubject(fn: () => Promise<string>) {
  return render(
    <PatientNotActiveGateProvider>
      <Subject fn={fn} />
    </PatientNotActiveGateProvider>,
  );
}

/**
 * 1 クリックで複数の操作を並列に投げる被験体（`AddVisitAnywhereDialog` が複数週へ
 * `Promise.all` する実経路の再現）。ダイアログが開くと body が inert になるため、
 * 「2 回クリックする」形では並列を再現できない。
 */
function MultiSubject({ fns }: { fns: (() => Promise<string>)[] }) {
  const { runGuarded } = usePatientNotActiveGate();
  const [oks, setOks] = React.useState<string[]>([]);
  const [errs, setErrs] = React.useState<string[]>([]);
  return (
    <div>
      <button
        type="button"
        onClick={() => {
          for (const fn of fns) {
            void runGuarded(fn).then(
              (v) => setOks((prev) => [...prev, v]),
              (e: unknown) => setErrs((prev) => [...prev, e instanceof Error ? e.message : 'x']),
            );
          }
        }}
      >
        まとめて実行
      </button>
      {oks.map((v) => (
        <span key={v} data-testid="multi-ok">
          {v}
        </span>
      ))}
      {errs.map((v, i) => (
        <span key={`${v}-${i}`} data-testid="multi-err">
          {v}
        </span>
      ))}
    </div>
  );
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('extractPatientNotActiveDetail', () => {
  it('422 + code=patient_not_active を取り出す', () => {
    const detail = extractPatientNotActiveDetail(notActiveError());
    expect(detail?.status_label).toBe('入院中');
    expect(detail?.can_override).toBe(true);
  });

  it('別コード / 別ステータス / 非 ApiError は null', () => {
    expect(
      extractPatientNotActiveDetail(new ApiError('x', 422, { detail: { code: 'other' } })),
    ).toBeNull();
    expect(extractPatientNotActiveDetail(new ApiError('x', 500, null))).toBeNull();
    expect(extractPatientNotActiveDetail(new Error('boom'))).toBeNull();
  });
});

describe('patientNameFromMessage', () => {
  it('「◯◯様は…」から氏名を拾う', () => {
    expect(patientNameFromMessage('小湊 花子様は入院中のため予定に入れられません')).toBe(
      '小湊 花子',
    );
  });
  it('様が無ければ null', () => {
    expect(patientNameFromMessage('入れられません')).toBeNull();
    expect(patientNameFromMessage(null)).toBeNull();
  });
});

describe('PatientNotActiveGate — runGuarded', () => {
  beforeEach(() => {
    statusDialogSpy.mockClear();
    vi.mocked(toast.success).mockClear();
  });

  it('成功する操作はそのまま resolve する (ダイアログは出ない)', async () => {
    const user = userEvent.setup();
    const fn = vi.fn().mockResolvedValue('done');
    renderSubject(fn);

    await user.click(screen.getByRole('button', { name: '実行' }));

    expect(await screen.findByTestId('subject-ok')).toHaveTextContent('done');
    expect(screen.queryByTestId('patient-not-active-gate')).not.toBeInTheDocument();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('422 patient_not_active → 案内 → 稼働中にして続ける → 元の操作を 1 回だけ再実行', async () => {
    const user = userEvent.setup();
    const fn = vi.fn().mockRejectedValueOnce(notActiveError()).mockResolvedValueOnce('placed');
    renderSubject(fn);

    await user.click(screen.getByRole('button', { name: '実行' }));

    // ❶ 案内ダイアログ
    expect(await screen.findByTestId('patient-not-active-gate')).toBeInTheDocument();
    expect(screen.getByTestId('patient-not-active-message')).toHaveTextContent(
      '小湊 花子様は入院中のため予定に入れられません',
    );

    // ❷ 「稼働中にして続ける」→ ステータス変更ダイアログ (toStatus=active)
    await user.click(screen.getByTestId('patient-not-active-confirm'));
    expect(await screen.findByTestId('stub-status-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('stub-status-to')).toHaveTextContent('active');
    expect(screen.getByTestId('stub-status-from')).toHaveTextContent('admitted');
    expect(screen.getByTestId('stub-status-name')).toHaveTextContent('小湊 花子');

    // ❸ onDone → 再実行して resolve
    await user.click(screen.getByRole('button', { name: 'スタブ: 稼働中にした' }));
    expect(await screen.findByTestId('subject-ok')).toHaveTextContent('placed');
    expect(fn).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('stub-status-dialog')).not.toBeInTheDocument();
  });

  it('「やめる」→ 元のエラーで reject し、再実行しない', async () => {
    const user = userEvent.setup();
    const err = notActiveError();
    const fn = vi.fn().mockRejectedValue(err);
    renderSubject(fn);

    await user.click(screen.getByRole('button', { name: '実行' }));
    await user.click(await screen.findByTestId('patient-not-active-cancel'));

    expect(await screen.findByTestId('subject-err')).toHaveTextContent('API 422 (/x)');
    expect(fn).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('patient-not-active-gate')).not.toBeInTheDocument();
  });

  it('ステータス変更ダイアログで閉じても元のエラーで reject する', async () => {
    const user = userEvent.setup();
    const fn = vi.fn().mockRejectedValue(notActiveError());
    renderSubject(fn);

    await user.click(screen.getByRole('button', { name: '実行' }));
    await user.click(await screen.findByTestId('patient-not-active-confirm'));
    await user.click(await screen.findByRole('button', { name: 'スタブ: やめる' }));

    expect(await screen.findByTestId('subject-err')).toHaveTextContent('API 422 (/x)');
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('別のエラーは素通し (ダイアログを出さない)', async () => {
    const user = userEvent.setup();
    const fn = vi.fn().mockRejectedValue(new ApiError('API 500 (/x)', 500, null));
    renderSubject(fn);

    await user.click(screen.getByRole('button', { name: '実行' }));

    expect(await screen.findByTestId('subject-err')).toHaveTextContent('API 500 (/x)');
    expect(screen.queryByTestId('patient-not-active-gate')).not.toBeInTheDocument();
  });

  it('can_override=false はダイアログを出さず素通し', async () => {
    const user = userEvent.setup();
    const fn = vi.fn().mockRejectedValue(notActiveError({ can_override: false }));
    renderSubject(fn);

    await user.click(screen.getByRole('button', { name: '実行' }));

    expect(await screen.findByTestId('subject-err')).toHaveTextContent('API 422 (/x)');
    expect(screen.queryByTestId('patient-not-active-gate')).not.toBeInTheDocument();
  });

  it('Provider が無ければ素通し (既存コンポーネントの単体テストが壊れない)', async () => {
    const user = userEvent.setup();
    const fn = vi.fn().mockResolvedValue('bare');
    render(<Subject fn={fn} />);

    await user.click(screen.getByRole('button', { name: '実行' }));

    expect(await screen.findByTestId('subject-ok')).toHaveTextContent('bare');
  });

  it('復帰完了時に Phase 1 と同じ文言の成功トーストを 1 本出す', async () => {
    const user = userEvent.setup();
    const fn = vi.fn().mockRejectedValueOnce(notActiveError()).mockResolvedValueOnce('placed');
    renderSubject(fn);

    await user.click(screen.getByRole('button', { name: '実行' }));
    await user.click(await screen.findByTestId('patient-not-active-confirm'));
    await user.click(await screen.findByRole('button', { name: 'スタブ: 稼働中にした' }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(toast.success).toHaveBeenCalledWith('稼働中に戻しました（3 件を作成）');
  });

  // ── 同時発火 (AddVisitAnywhereDialog の Promise.all が実例) ───────────

  it('同じ患者様の同時発火は 1 枚のダイアログにまとめ、承諾で **全部** 再実行する', async () => {
    const user = userEvent.setup();
    const a = vi.fn().mockRejectedValueOnce(notActiveError()).mockResolvedValueOnce('A');
    const b = vi.fn().mockRejectedValueOnce(notActiveError()).mockResolvedValueOnce('B');
    render(
      <PatientNotActiveGateProvider>
        <MultiSubject fns={[a, b]} />
      </PatientNotActiveGateProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'まとめて実行' }));

    // ダイアログは 1 枚だけ。
    expect(await screen.findByTestId('patient-not-active-gate')).toBeInTheDocument();
    expect(screen.getAllByTestId('patient-not-active-gate')).toHaveLength(1);

    await user.click(screen.getByTestId('patient-not-active-confirm'));
    await user.click(await screen.findByRole('button', { name: 'スタブ: 稼働中にした' }));

    await waitFor(() => expect(screen.getAllByTestId('multi-ok')).toHaveLength(2));
    expect(
      screen
        .getAllByTestId('multi-ok')
        .map((n) => n.textContent)
        .sort(),
    ).toEqual(['A', 'B']);
    expect(a).toHaveBeenCalledTimes(2);
    expect(b).toHaveBeenCalledTimes(2);
    // ステータス変更は 1 回 = トーストも 1 本。
    expect(toast.success).toHaveBeenCalledTimes(1);
  });

  it('同じ患者様の同時発火を「やめる」と、それぞれの元のエラーで reject される', async () => {
    const user = userEvent.setup();
    const a = vi.fn().mockRejectedValue(notActiveError());
    const b = vi.fn().mockRejectedValue(notActiveError());
    render(
      <PatientNotActiveGateProvider>
        <MultiSubject fns={[a, b]} />
      </PatientNotActiveGateProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'まとめて実行' }));
    await user.click(await screen.findByTestId('patient-not-active-cancel'));

    await waitFor(() => expect(screen.getAllByTestId('multi-err')).toHaveLength(2));
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  it('別の患者様の 422 は **あとから来たほう** を畳み、先のダイアログを残す', async () => {
    const user = userEvent.setup();
    const first = vi.fn().mockRejectedValueOnce(notActiveError()).mockResolvedValueOnce('FIRST');
    const other = vi
      .fn()
      .mockRejectedValue(
        notActiveError({ patient_id: OTHER_PATIENT_ID, status_label: '一時休止' }),
      );
    render(
      <PatientNotActiveGateProvider>
        <MultiSubject fns={[first, other]} />
      </PatientNotActiveGateProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'まとめて実行' }));

    // 後からの方は即座に reject、ダイアログは最初の患者のまま。
    await waitFor(() => expect(screen.getAllByTestId('multi-err')).toHaveLength(1));
    expect(screen.getByTestId('patient-not-active-message')).toHaveTextContent(
      '小湊 花子様は入院中のため予定に入れられません',
    );
    expect(other).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId('patient-not-active-confirm'));
    await user.click(await screen.findByRole('button', { name: 'スタブ: 稼働中にした' }));
    await waitFor(() => expect(screen.getAllByTestId('multi-ok')).toHaveLength(1));
    expect(screen.getByTestId('multi-ok')).toHaveTextContent('FIRST');
  });
});

// ─── useGuardedMutation ──────────────────────────────────────────────────────

describe('useGuardedMutation', () => {
  /**
   * react-query を張らずに済むよう、`mutate` / `mutateAsync` だけ持つ最小の mutation。
   * `mutate` は本物と同じく「per-call コールバックを呼んで reject は飲み込む」形にする。
   */
  function fakeMutation(impl: (vars: string) => Promise<string>) {
    type Cb = {
      onSuccess?: (d: string, v: string, r: unknown, c: unknown) => void;
      onError?: (e: unknown, v: string, r: unknown, c: unknown) => void;
      onSettled?: (d: string | undefined, e: unknown, v: string, r: unknown, c: unknown) => void;
    };
    const mutateAsync = vi.fn(async (vars: string, options?: Cb) => {
      try {
        const data = await impl(vars);
        options?.onSuccess?.(data, vars, undefined, undefined);
        options?.onSettled?.(data, null, vars, undefined, undefined);
        return data;
      } catch (e) {
        options?.onError?.(e, vars, undefined, undefined);
        options?.onSettled?.(undefined, e, vars, undefined, undefined);
        throw e;
      }
    });
    return {
      mutateAsync,
      mutate: vi.fn((vars: string, options?: Cb) => {
        void mutateAsync(vars, options).catch(() => undefined);
      }),
    };
  }

  it('mutate の onError は「やめる」で初めて 1 回だけ呼ばれる', async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    const onSuccess = vi.fn();
    const impl = vi.fn().mockRejectedValue(notActiveError());
    const mutation = fakeMutation(impl);

    function MutateSubject() {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const guarded = useGuardedMutationForTest(mutation as any);
      return (
        <button type="button" onClick={() => guarded.mutate('v', { onSuccess, onError })}>
          送る
        </button>
      );
    }

    render(
      <PatientNotActiveGateProvider>
        <MutateSubject />
      </PatientNotActiveGateProvider>,
    );

    await user.click(screen.getByRole('button', { name: '送る' }));
    // ダイアログが出ている間は onError を呼ばない (トーストの二重表示を防ぐ)。
    expect(await screen.findByTestId('patient-not-active-gate')).toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('patient-not-active-cancel'));
    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('mutate は復帰後の再実行で成功し onSuccess が 1 回だけ呼ばれる', async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    const onSuccess = vi.fn();
    const impl = vi.fn().mockRejectedValueOnce(notActiveError()).mockResolvedValueOnce('ok');
    const mutation = fakeMutation(impl);

    function MutateSubject() {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const guarded = useGuardedMutationForTest(mutation as any);
      return (
        <button type="button" onClick={() => guarded.mutate('v', { onSuccess, onError })}>
          送る
        </button>
      );
    }

    render(
      <PatientNotActiveGateProvider>
        <MutateSubject />
      </PatientNotActiveGateProvider>,
    );

    await user.click(screen.getByRole('button', { name: '送る' }));
    await user.click(await screen.findByTestId('patient-not-active-confirm'));
    await user.click(await screen.findByRole('button', { name: 'スタブ: 稼働中にした' }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(onError).not.toHaveBeenCalled();
    expect(impl).toHaveBeenCalledTimes(2);
  });
  // ── 回帰防止: 再実行が **別のエラー** で落ちたときに古い 422 を流さない ───
  // Phase 1 の復帰が型から予定を作り直すため、再実行が 409 (同じ枠が埋まった)
  // になり得る。ここで握りつぶしを流すと「入院中のため…」の古い文言が重ねて出る。
  it('mutate: 再実行が 409 で落ちても onError / onSettled は新しいエラーで 1 回だけ', async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    const onSettled = vi.fn();
    const conflict = new ApiError('API 409 (/x)', 409, { detail: '既に配置済みです' });
    const impl = vi.fn().mockRejectedValueOnce(notActiveError()).mockRejectedValueOnce(conflict);
    const mutation = fakeMutation(impl);

    function MutateSubject() {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const guarded = useGuardedMutationForTest(mutation as any);
      return (
        <button type="button" onClick={() => guarded.mutate('v', { onError, onSettled })}>
          送る
        </button>
      );
    }

    render(
      <PatientNotActiveGateProvider>
        <MutateSubject />
      </PatientNotActiveGateProvider>,
    );

    await user.click(screen.getByRole('button', { name: '送る' }));
    await user.click(await screen.findByTestId('patient-not-active-confirm'));
    await user.click(await screen.findByRole('button', { name: 'スタブ: 稼働中にした' }));

    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onError.mock.calls[0]![0]).toBe(conflict);
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(onSettled.mock.calls[0]![1]).toBe(conflict);
    expect(impl).toHaveBeenCalledTimes(2);
  });

  it('mutateAsync: 再実行が 409 で落ちても onError / onSettled は新しいエラーで 1 回だけ', async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    const onSettled = vi.fn();
    const caught = vi.fn();
    const conflict = new ApiError('API 409 (/x)', 409, { detail: '既に配置済みです' });
    const impl = vi.fn().mockRejectedValueOnce(notActiveError()).mockRejectedValueOnce(conflict);
    const mutation = fakeMutation(impl);

    function AsyncSubject() {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const guarded = useGuardedMutationForTest(mutation as any);
      return (
        <button
          type="button"
          onClick={() => {
            void guarded.mutateAsync('v', { onError, onSettled }).catch(caught);
          }}
        >
          送る
        </button>
      );
    }

    render(
      <PatientNotActiveGateProvider>
        <AsyncSubject />
      </PatientNotActiveGateProvider>,
    );

    await user.click(screen.getByRole('button', { name: '送る' }));
    await user.click(await screen.findByTestId('patient-not-active-confirm'));
    await user.click(await screen.findByRole('button', { name: 'スタブ: 稼働中にした' }));

    await waitFor(() => expect(caught).toHaveBeenCalledTimes(1));
    expect(caught.mock.calls[0]![0]).toBe(conflict);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]![0]).toBe(conflict);
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it('mutateAsync(vars, options): 握りつぶした onError は「やめる」で 1 回だけ呼ばれる', async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    const onSettled = vi.fn();
    const caught = vi.fn();
    const err = notActiveError();
    const impl = vi.fn().mockRejectedValue(err);
    const mutation = fakeMutation(impl);

    function AsyncSubject() {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const guarded = useGuardedMutationForTest(mutation as any);
      return (
        <button
          type="button"
          onClick={() => {
            void guarded.mutateAsync('v', { onError, onSettled }).catch(caught);
          }}
        >
          送る
        </button>
      );
    }

    render(
      <PatientNotActiveGateProvider>
        <AsyncSubject />
      </PatientNotActiveGateProvider>,
    );

    await user.click(screen.getByRole('button', { name: '送る' }));
    expect(await screen.findByTestId('patient-not-active-gate')).toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('patient-not-active-cancel'));
    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onError.mock.calls[0]![0]).toBe(err);
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(caught).toHaveBeenCalledTimes(1);
    expect(impl).toHaveBeenCalledTimes(1);
  });
});
