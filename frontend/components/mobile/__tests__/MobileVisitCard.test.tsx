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

  it('複数名の同行は accompaniments[] を「・」連結で全員出す (確定#5)', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({
      accompaniment: { staff_id: 'staff-trainee-1', staff_name: '新人 一郎' },
      accompaniments: [
        { staff_id: 'staff-trainee-1', staff_name: '新人 一郎', kind: 'trainee' },
        { staff_id: 'staff-support-1', staff_name: '熊澤 二郎', kind: 'support' },
      ],
    });
    render(<MobileVisitCard visit={visit} />);
    expect(screen.getByTestId('mobile-visit-accompaniment').textContent).toContain(
      '同行: 新人 一郎・熊澤 二郎',
    );
  });

  it('accompaniments[] が無い旧レスポンスは単数 accompaniment にフォールバックする', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({
      accompaniment: { staff_id: 'staff-support-1', staff_name: '熊澤 二郎' },
      accompaniments: undefined,
    });
    render(<MobileVisitCard visit={visit} />);
    expect(screen.getByTestId('mobile-visit-accompaniment').textContent).toContain(
      '同行: 熊澤 二郎',
    );
  });
});

describe('MobileVisitCard 音声記録マーク', () => {
  it('hasRecording のとき 🎙 マークを出す', () => {
    setSession('staff-senior-1');
    render(<MobileVisitCard visit={makeVisit()} hasRecording />);
    expect(screen.getByTestId('mobile-visit-recording-mark')).toBeInTheDocument();
    expect(screen.getByLabelText('音声記録あり')).toBeInTheDocument();
  });

  it('録音が無ければ出さない', () => {
    setSession('staff-senior-1');
    render(<MobileVisitCard visit={makeVisit()} />);
    expect(screen.queryByTestId('mobile-visit-recording-mark')).toBeNull();
  });
});

describe('MobileVisitCard 打刻の実績時刻', () => {
  it('打刻なし: 実績行を出さない (従来の見え方のまま)', () => {
    setSession('staff-senior-1');
    render(<MobileVisitCard visit={makeVisit()} />);
    expect(screen.queryByTestId('mobile-visit-actual')).toBeNull();
  });

  it('到着のみ: 「到着 12:56 〜（訪問中）」', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({
      status: 'in_progress',
      actual_arrival_at: '2026-09-18T03:56:00Z',
      actual_departure_at: null,
    });
    render(<MobileVisitCard visit={visit} />);
    expect(screen.getByTestId('mobile-visit-actual').textContent).toContain(
      '到着 12:56 〜（訪問中）',
    );
  });

  it('到着+退出: 「実績 12:56 – 13:40」', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({
      status: 'completed',
      actual_arrival_at: '2026-09-18T03:56:00Z',
      actual_departure_at: '2026-09-18T04:40:00Z',
    });
    render(<MobileVisitCard visit={visit} />);
    expect(screen.getByTestId('mobile-visit-actual').textContent).toContain('実績 12:56 – 13:40');
  });

  it('実績が並んでも左の時刻カラムは予定のまま', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({
      status: 'completed',
      actual_arrival_at: '2026-09-18T03:56:00Z',
      actual_departure_at: '2026-09-18T04:40:00Z',
    });
    render(<MobileVisitCard visit={visit} />);
    expect(screen.getByText('09:30')).toBeInTheDocument();
    expect(screen.getByText('10:30')).toBeInTheDocument();
  });

  it('訪問中でない状態で到着だけ届いているときは「（訪問中）」と書かない', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({
      status: 'completed',
      actual_arrival_at: '2026-09-18T03:56:00Z',
      actual_departure_at: null,
    });
    render(<MobileVisitCard visit={visit} />);
    const el = screen.getByTestId('mobile-visit-actual');
    expect(el.textContent).toContain('到着 12:56 〜');
    expect(el.textContent).not.toContain('訪問中');
  });

  it('未訪問 (no_show) は到着打刻があっても実績行を出さない', () => {
    setSession('staff-senior-1');
    const visit = makeVisit({
      status: 'no_show',
      actual_arrival_at: '2026-09-18T03:56:00Z',
      actual_departure_at: null,
    });
    render(<MobileVisitCard visit={visit} />);
    expect(screen.queryByTestId('mobile-visit-actual')).toBeNull();
  });
});
