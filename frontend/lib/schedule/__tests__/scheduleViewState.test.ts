import { describe, expect, it } from 'vitest';

import {
  DEFAULT_SCHEDULE_VIEW_STATE,
  parseScheduleViewParams,
  writeScheduleViewParams,
} from '../scheduleViewState';

describe('parseScheduleViewParams', () => {
  it.each([
    ['', DEFAULT_SCHEDULE_VIEW_STATE],
    ['unit=course&day=wed&view=timeline', { unit: 'course', day: 2, view: 'timeline' }],
    ['unit=staff&day=sat&view=list', { unit: 'staff', day: 5, view: 'list' }],
    ['unit=course', { unit: 'course', day: 'week', view: 'list' }],
    ['unit=xx&day=sun&view=grid', DEFAULT_SCHEDULE_VIEW_STATE],
  ])('?%s', (q, want) => {
    expect(parseScheduleViewParams(new URLSearchParams(q))).toEqual(want);
  });
});

describe('writeScheduleViewParams', () => {
  it('他のクエリを残して 3 つを書く', () => {
    const p = new URLSearchParams('visit=abc');
    writeScheduleViewParams(p, { unit: 'course', day: 0, view: 'timeline' });
    expect(p.toString()).toBe('visit=abc&unit=course&day=mon&view=timeline');
    writeScheduleViewParams(p, { unit: 'staff', day: 'week', view: 'list' });
    expect(parseScheduleViewParams(p)).toEqual({ unit: 'staff', day: 'week', view: 'list' });
  });
});
