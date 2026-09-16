/**
 * スマホの自分専用フックが使う寛容パース (2026-09-16 レビュー MEDIUM-7)。
 *
 * `useMyStaffEvents` / `useMyOverrides` は応答をそのまま画面へ流していたため、
 * BE の項目欠落 1 行で当日画面が丸ごと落ちる形になっていた。行単位で検証し、
 * 読めない行だけ warn して捨てることをここで担保する。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { parseWeekEvents } from '@/lib/queries/staff-events';
import { parseMyOverrides } from '@/lib/queries/me';

const EVENT_ID = '11111111-1111-4111-8111-111111111111';
const STAFF_ID = '22222222-2222-4222-8222-222222222222';
const OVERRIDE_ID = '33333333-3333-4333-8333-333333333333';

function rawEvent(over: Record<string, unknown> = {}) {
  return {
    id: EVENT_ID,
    staff_id: STAFF_ID,
    date: '2026-09-16',
    title: '朝会',
    start_time: '08:30',
    end_time: '09:00',
    type: 'イベント',
    source: 'manual',
    cancelled_at: null,
    ...over,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useMyStaffEvents の寛容パース (parseWeekEvents)', () => {
  it('start_time が欠けた行だけ捨てて warn し、正常な行は残す', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const out = parseWeekEvents([{ ...rawEvent(), start_time: undefined }, rawEvent()], STAFF_ID);
    expect(out).toHaveLength(1);
    expect(out[0]!.id).toBe(EVENT_ID);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('配列でない応答は空配列', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(parseWeekEvents({ detail: 'boom' }, STAFF_ID)).toEqual([]);
  });
});

describe('useMyOverrides の寛容パース (parseMyOverrides)', () => {
  it('type が未知の行だけ捨てて warn し、正常な行は残す', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const out = parseMyOverrides(
      [
        { id: OVERRIDE_ID, date: '2026-09-16', type: '休み' },
        { id: OVERRIDE_ID, date: '2026-09-16', type: '謎の区分' },
      ],
      STAFF_ID,
    );
    expect(out).toHaveLength(1);
    expect(out[0]!.type).toBe('休み');
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('配列でない応答は空配列', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(parseMyOverrides(null, STAFF_ID)).toEqual([]);
  });
});
