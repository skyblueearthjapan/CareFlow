/**
 * /m/shifts — 旧「出勤カレンダー」は「休み」ページ (/m/leave) へ転送する (leave-and-kinmu §6)。
 */
import { describe, expect, it, vi } from 'vitest';

const redirect = vi.fn();
vi.mock('next/navigation', () => ({
  redirect: (url: string) => redirect(url),
}));

import MobileShiftsRedirect from '../page';

describe('/m/shifts', () => {
  it('/m/leave へ転送する', () => {
    MobileShiftsRedirect();
    expect(redirect).toHaveBeenCalledWith('/m/leave');
  });
});
