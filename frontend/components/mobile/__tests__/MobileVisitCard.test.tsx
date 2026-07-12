/** 新人同行 (§7.4) の訪問カード表示テスト。 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

import { useSession } from 'next-auth/react';
import { MobileVisitCard } from '../MobileVisitCard';
import type { MyVisit } from '@/lib/queries/me';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function makeVisit(overrides: Partial<MyVisit> = {}): MyVisit {
  return {
    id: 'visit-1',
    patient_id: 'pat-1',
    primary_staff_id: 'staff-senior-1',
    secondary_staff_id: null,
    mentor_staff_id: null,
    visit_date: '2026-07-11',
    start_time: '09:30:00',
    end_time: '10:30:00',
    type: 'normal',
    status: 'planned',
    source: 'manual',
    note: null,
    patient_name: '山田 花子',
    staff_name: '佐藤 先輩',
    ...overrides,
  };
}

function setSession(staffId: string | null) {
  asMock(useSession).mockReturnValue({
    data: staffId ? { user: { staffId, role: 'staff' } } : null,
    status: staffId ? 'authenticated' : 'unauthenticated',
  });
}

describe('MobileVisitCard 新人同行表示', () => {
  it('先輩が閲覧: 「同行: ◯◯」を表示する', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({
      accompaniment: { staff_id: 'staff-trainee-1', staff_name: '新人 一郎' },
    });
    render(<MobileVisitCard visit={visit} />);
    const el = screen.getByTestId('mobile-visit-accompaniment');
    expect(el.textContent).toContain('同行: 新人 一郎');
  });

  it('新人本人が閲覧: 「同行」バッジを表示する', () => {
    setSession('staff-trainee-1');
    const visit = makeVisit({
      accompaniment: { staff_id: 'staff-trainee-1', staff_name: '新人 一郎' },
    });
    render(<MobileVisitCard visit={visit} />);
    const el = screen.getByTestId('mobile-visit-accompaniment');
    expect(el.textContent).toContain('同行');
    expect(el.textContent).not.toContain('新人 一郎');
  });

  it('同行が無い訪問は何も表示しない', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({ accompaniment: null });
    render(<MobileVisitCard visit={visit} />);
    expect(screen.queryByTestId('mobile-visit-accompaniment')).toBeNull();
  });
});
