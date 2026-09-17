/**
 * `buildListQuery`（GET /visit-recordings のクエリ組み立て）の vitest。
 *
 * 縛る挙動:
 *   1. 検索語は **2 文字以上のときだけ** 送る（レビュー N-4）。UI 側でも抑止して
 *      いるが、フックを直接叩く呼び出しがあるのでここでも守る。
 *   2. `reviewed` は false も送る（「未確認だけ」の絞り込みが効かなくなるため）。
 *   3. `unscoped` は FE 専用フラグなので BE へ漏らさない。
 */
import { describe, it, expect } from 'vitest';

import { buildListQuery } from '@/lib/queries/visit-recordings';

function params(qs: string): URLSearchParams {
  return new URLSearchParams(qs);
}

describe('buildListQuery', () => {
  it('1 文字の検索語は送らない (N-4)', () => {
    expect(params(buildListQuery({ q: '足' })).has('q')).toBe(false);
    expect(params(buildListQuery({ q: ' 　' })).has('q')).toBe(false);
  });

  it('2 文字以上なら trim して送る (N-4)', () => {
    expect(params(buildListQuery({ q: '  足が  ' })).get('q')).toBe('足が');
  });

  it('reviewed は false も送る', () => {
    expect(params(buildListQuery({ reviewed: false })).get('reviewed')).toBe('false');
    expect(params(buildListQuery({ reviewed: true })).get('reviewed')).toBe('true');
    expect(params(buildListQuery({ reviewed: null })).has('reviewed')).toBe(false);
  });

  it('unscoped は FE 専用なので BE へ渡さない', () => {
    const qs = params(buildListQuery({ unscoped: true, patientId: 'p-1' }));
    expect(qs.has('unscoped')).toBe(false);
    expect(qs.get('patient_id')).toBe('p-1');
  });

  it('既定の窓は limit=50 / offset=0', () => {
    const qs = params(buildListQuery({}));
    expect(qs.get('limit')).toBe('50');
    expect(qs.get('offset')).toBe('0');
  });

  it('office_id / order / from / to / status を渡す', () => {
    const qs = params(
      buildListQuery({
        officeId: 'of-1',
        order: 'recorded_at_desc',
        from: '2026-09-14',
        to: '2026-09-20',
        status: 'failed',
      }),
    );
    expect(qs.get('office_id')).toBe('of-1');
    expect(qs.get('order')).toBe('recorded_at_desc');
    expect(qs.get('from')).toBe('2026-09-14');
    expect(qs.get('to')).toBe('2026-09-20');
    expect(qs.get('status')).toBe('failed');
  });
});
