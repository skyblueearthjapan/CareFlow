/** 受け入れ枠「月で見る」の小さな計算と文言。 */
import { describe, expect, it } from 'vitest';

import {
  currentMonthJst,
  monthLabel,
  monthPayloadSchema,
  monthRulesText,
  pct,
  shiftMonth,
  utilBand,
} from '@/lib/acceptanceMonth';
import { monthPath } from '@/lib/queries/acceptance_month';

import { monthPayload } from './fixtures/acceptanceMonthPayload';

describe('月の計算', () => {
  it('月をずらす (年をまたぐ)', () => {
    expect(shiftMonth('2026-11', 1)).toBe('2026-12');
    expect(shiftMonth('2026-12', 1)).toBe('2027-01');
    expect(shiftMonth('2026-01', -1)).toBe('2025-12');
    expect(monthLabel('2026-03')).toBe('2026 年 3 月');
  });

  it('今の月は JST で決める', () => {
    // UTC 10/31 15:30 = JST 11/1 0:30
    expect(currentMonthJst(new Date('2026-10-31T15:30:00Z'))).toBe('2026-11');
  });

  it('稼働率の段と表記', () => {
    expect(utilBand(null, 0.7)).toBe('none');
    expect(utilBand(0, 0.7)).toBe('u0');
    expect(utilBand(0.2, 0.7)).toBe('u1');
    expect(utilBand(0.35, 0.7)).toBe('u2');
    expect(utilBand(0.6, 0.7)).toBe('u3');
    expect(utilBand(0.7, 0.7)).toBe('u4');
    expect(pct(0.456)).toBe('46%');
    expect(pct(null)).toBe('－');
  });

  it('決まりの文章は昼休みの切り替えで変わる', () => {
    const rules = monthPayload().rules;
    expect(monthRulesText(rules)).toContain('昼休み 60 分を 11:30〜13:30');
    expect(monthRulesText({ ...rules, lunch_considered: false })).toContain('昼休みは考えない');
  });

  it('応答の形を読める', () => {
    expect(() => monthPayloadSchema.parse(monthPayload())).not.toThrow();
  });

  it('URL: 拠点・担い手・昼休み・HTML', () => {
    const base = {
      month: '2026-11',
      officeId: null,
      staffIds: null,
      mode: 'real' as const,
      lunch: true,
    };
    expect(monthPath(base)).toBe(
      '/api/v1/acceptance-matrix/month?month=2026-11&mode=real&lunch=true',
    );
    const full = monthPath({ ...base, officeId: 'o1', staffIds: [], lunch: false }, true);
    expect(full).toContain('office_id=o1');
    expect(full).toContain('staff=&');
    expect(full).toContain('lunch=false');
    expect(full).toContain('include_html=true');
  });
});
