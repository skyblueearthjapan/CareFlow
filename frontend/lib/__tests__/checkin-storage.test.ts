import { describe, it, expect, beforeEach } from 'vitest';

import { clearAllCheckins, clearAllSessionData } from '@/lib/checkin-storage';

describe('checkin-storage purge helpers', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('clearAllCheckins は checkin: のみ消す', () => {
    window.localStorage.setItem('checkin:staff-1:visit-1', '{}');
    window.localStorage.setItem('checkin-pending:staff-1', '[]');
    window.localStorage.setItem('visit-memo:staff-1:visit-1', 'memo');
    window.localStorage.setItem('unrelated', 'keep');

    clearAllCheckins();

    expect(window.localStorage.getItem('checkin:staff-1:visit-1')).toBeNull();
    // checkin-pending / visit-memo は対象外 (後方互換)。
    expect(window.localStorage.getItem('checkin-pending:staff-1')).toBe('[]');
    expect(window.localStorage.getItem('visit-memo:staff-1:visit-1')).toBe('memo');
    expect(window.localStorage.getItem('unrelated')).toBe('keep');
  });

  it('clearAllSessionData は 3 プレフィックスを全消去し他は残す (PHI 残留対策)', () => {
    window.localStorage.setItem('checkin:staff-1:visit-1', '{}');
    window.localStorage.setItem('checkin-pending:staff-1', '[]');
    window.localStorage.setItem('visit-memo:staff-1:visit-1', 'memo');
    window.localStorage.setItem('unrelated', 'keep');

    clearAllSessionData();

    expect(window.localStorage.getItem('checkin:staff-1:visit-1')).toBeNull();
    expect(window.localStorage.getItem('checkin-pending:staff-1')).toBeNull();
    expect(window.localStorage.getItem('visit-memo:staff-1:visit-1')).toBeNull();
    // セッション無関係のキーは保持する。
    expect(window.localStorage.getItem('unrelated')).toBe('keep');
  });
});
