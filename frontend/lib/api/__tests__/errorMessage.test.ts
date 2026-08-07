import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';

function apiError(body: unknown, status = 422): ApiError {
  return new ApiError(`API ${status}  (/api/v1/test)`, status, body);
}

describe('apiErrorMessage', () => {
  it('素の文字列 detail をそのまま返す', () => {
    const e = apiError({ detail: 'sub_office_id が存在しません: abc' });
    expect(apiErrorMessage(e)).toBe('sub_office_id が存在しません: abc');
  });

  it('構造化 detail の message + violations を改行区切りで返す', () => {
    const e = apiError({
      detail: {
        message: '完全固定 (is_pinned) の枠を変更・削除しようとしています',
        violations: [
          { code: 'pinned_protection', message: '月曜 枠0 のピン留めされた枠を…', weekday: 0 },
          { code: 'pinned_protection', message: '火曜 枠0 のピン留めされた枠を…', weekday: 1 },
        ],
      },
    });
    expect(apiErrorMessage(e)).toBe(
      '完全固定 (is_pinned) の枠を変更・削除しようとしています\n' +
        '月曜 枠0 のピン留めされた枠を…\n' +
        '火曜 枠0 のピン留めされた枠を…',
    );
  });

  it('violations が無い構造化 detail は message のみ返す', () => {
    const e = apiError({ detail: { message: 'だめです' } });
    expect(apiErrorMessage(e)).toBe('だめです');
  });

  it('message が無ければ code にフォールバックする', () => {
    const e = apiError({ detail: { code: 'pinned_pfv_cannot_be_applied' } });
    expect(apiErrorMessage(e)).toBe('pinned_pfv_cannot_be_applied');
  });

  it('pydantic 検証エラー (配列 detail) の msg を集める', () => {
    const e = apiError({
      detail: [
        { loc: ['body', 'items'], msg: 'List should have at most 14 items', type: 'too_long' },
        { loc: ['body', 'mode'], msg: 'Input should be normal or special', type: 'enum' },
      ],
    });
    expect(apiErrorMessage(e)).toBe(
      'List should have at most 14 items\nInput should be normal or special',
    );
  });

  it('解釈できない body は元の Error.message にフォールバックする', () => {
    const e = apiError({ unexpected: true });
    expect(apiErrorMessage(e)).toBe('API 422  (/api/v1/test)');
  });

  it('body が null でもフォールバックする', () => {
    const e = apiError(null);
    expect(apiErrorMessage(e)).toBe('API 422  (/api/v1/test)');
  });

  it('ApiError 以外の Error は message をそのまま返す', () => {
    expect(apiErrorMessage(new Error('ネットワークエラー'))).toBe('ネットワークエラー');
  });

  it('Error ですらない値は fallback を返す', () => {
    expect(apiErrorMessage('なにか', 'デフォルト')).toBe('デフォルト');
  });
});
