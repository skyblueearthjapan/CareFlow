/**
 * P0-2 Commit 3: apply-individual H10 (昼休み) 422 → 確認 → force_lunch 再送の
 * オーケストレーション (`applyIndividualWithLunchConfirm` / `isLunchBreak422`) 単体テスト.
 *
 * モーダル配線 (DiffAddDialog / FullOptimizeDialog) は window.confirm ベースのため、
 * ここでは helper 層で「force_lunch がペイロードに載って再送されること」を施錠する。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { ApiError } from '@/lib/api-client';
import type {
  ApplyIndividualRequest,
  ApplyIndividualResponse,
} from '@/lib/schemas/v2/autoScheduleV2';

vi.mock('sonner', () => ({ toast: { warning: vi.fn() } }));

import {
  applyIndividualWithLunchConfirm,
  isLunchBreak422,
  toastApplyWarnings,
} from '../_autoScheduleUtils';
import { toast } from 'sonner';

const BASE_PAYLOAD: ApplyIndividualRequest = {
  proposal_id: '00000000-0000-4000-8000-000000000010',
  patient_id: '00000000-0000-4000-8000-000000000002',
  confirm: true,
  iso_year: 2026,
  iso_week: 24,
  visit_plans: [],
};

function okResponse(): ApplyIndividualResponse {
  return {
    patient_id: BASE_PAYLOAD.patient_id!,
    applied: true,
    fixed_visit_ids: [],
    idempotent: false,
    warnings: [],
  };
}

const lunch422 = () =>
  new ApiError('API 422 Unprocessable Entity (/apply-individual)', 422, {
    detail: '昼休みと重複するため配置できません',
  });

describe('isLunchBreak422', () => {
  it('detail に「昼休み」を含む 422 は true', () => {
    expect(isLunchBreak422(lunch422())).toBe(true);
  });
  it('昼休みを含まない 422 は false', () => {
    expect(isLunchBreak422(new ApiError('x', 422, { detail: '定員超過' }))).toBe(false);
  });
  it('422 以外は false', () => {
    expect(isLunchBreak422(new ApiError('x', 409, { detail: '昼休み' }))).toBe(false);
    expect(isLunchBreak422(new Error('boom'))).toBe(false);
  });
});

describe('toastApplyWarnings', () => {
  beforeEach(() => {
    vi.mocked(toast.warning).mockClear();
  });
  it('空/undefined ではトーストを出さない (成功パス不変)', () => {
    toastApplyWarnings(undefined);
    toastApplyWarnings([]);
    expect(toast.warning).not.toHaveBeenCalled();
  });
  it('5件は先頭3件＋「他 2 件」の計4トーストに丸める', () => {
    toastApplyWarnings(['a', 'b', 'c', 'd', 'e']);
    expect(toast.warning).toHaveBeenCalledTimes(4);
    expect(toast.warning).toHaveBeenLastCalledWith('他 2 件の警告があります');
  });
  it('3件以下はそのまま件数分', () => {
    toastApplyWarnings(['a', 'b']);
    expect(toast.warning).toHaveBeenCalledTimes(2);
  });
});

describe('applyIndividualWithLunchConfirm', () => {
  let confirmSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
  });
  afterEach(() => confirmSpy.mockRestore());

  it('成功時はそのままレスポンスを返し、確認は出さない', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(okResponse());
    const res = await applyIndividualWithLunchConfirm(mutateAsync, BASE_PAYLOAD);
    expect(res).not.toBeNull();
    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(confirmSpy).not.toHaveBeenCalled();
    // 初回は force_lunch を付けない.
    expect(mutateAsync.mock.calls[0][0].force_lunch).toBeUndefined();
  });

  it('昼休み 422 → 承認 で force_lunch=true 再送し成功レスポンスを返す', async () => {
    confirmSpy.mockReturnValue(true);
    const mutateAsync = vi
      .fn()
      .mockRejectedValueOnce(lunch422())
      .mockResolvedValueOnce(okResponse());
    const res = await applyIndividualWithLunchConfirm(mutateAsync, BASE_PAYLOAD);
    expect(res).not.toBeNull();
    expect(mutateAsync).toHaveBeenCalledTimes(2);
    // 2 回目に force_lunch=true が乗る.
    expect(mutateAsync.mock.calls[1][0].force_lunch).toBe(true);
  });

  it('昼休み 422 → 非承認 で null を返し再送しない', async () => {
    confirmSpy.mockReturnValue(false);
    const mutateAsync = vi.fn().mockRejectedValueOnce(lunch422());
    const res = await applyIndividualWithLunchConfirm(mutateAsync, BASE_PAYLOAD);
    expect(res).toBeNull();
    expect(mutateAsync).toHaveBeenCalledTimes(1);
  });

  it('昼休み以外のエラーは再送せず throw する', async () => {
    const err = new ApiError('x', 422, { detail: '定員超過' });
    const mutateAsync = vi.fn().mockRejectedValueOnce(err);
    await expect(applyIndividualWithLunchConfirm(mutateAsync, BASE_PAYLOAD)).rejects.toBe(err);
    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});
