/**
 * 今週の予定 (モバイル) — 患者ステータス連動の取消は一覧に出さない。
 *
 * design 2026-09-09 §7-4 (PO 決定): source='status_cancel' + status='cancelled'
 * は消す。「今週だけ取消」= manual_cancel は打ち消し線つきで残す (現場が
 * 「今週は行かない」を知る必要があるため)。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const EMPTY_QUERY = { data: [], isLoading: false, isError: false, error: null };

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

// 音声記録 (設計 2026-09-17): 🎙 マーク用の一覧。既定は 0 件。
vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: vi.fn(() => ({ data: { items: [], total: 0 } })),
}));

vi.mock('@/lib/queries/me', () => ({
  useMyVisits: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  // 職員イベント / 休み・時間変更 (design 2026-09-16 §3 C-1)。既定は 0 件で、
  // 必要なテストだけ mockImplementation で差し替える。
  useMyStaffEvents: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  useMyOverrides: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  currentWeekStartIso: () => '2026-09-07',
  addDays: (iso: string, days: number) => {
    const [y, m, d] = iso.split('-').map(Number);
    const dt = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
    dt.setDate(dt.getDate() + days);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
  },
}));

import { useMyOverrides, useMyStaffEvents, useMyVisits, type MyVisit } from '@/lib/queries/me';
import MobileThisWeekPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function makeVisit(over: Partial<MyVisit> & { id: string }): MyVisit {
  return {
    visit_date: '2026-09-07',
    start_time: '09:00:00',
    end_time: '10:00:00',
    status: 'planned',
    source: 'auto',
    patient_name: `患者${over.id}`,
    ...over,
  } as unknown as MyVisit;
}

beforeEach(() => {
  vi.clearAllMocks();
  // clearAllMocks は implementation を消さないので毎回「0 件」へ戻す。
  asMock(useMyStaffEvents).mockImplementation(() => EMPTY_QUERY);
  asMock(useMyOverrides).mockImplementation(() => EMPTY_QUERY);
});

function renderWith(visits: MyVisit[], extra: { events?: unknown[]; overrides?: unknown[] } = {}) {
  asMock(useMyVisits).mockImplementation(() => ({
    data: visits,
    isLoading: false,
    isError: false,
    error: null,
  }));
  if (extra.events) {
    asMock(useMyStaffEvents).mockImplementation(() => ({ ...EMPTY_QUERY, data: extra.events }));
  }
  if (extra.overrides) {
    asMock(useMyOverrides).mockImplementation(() => ({ ...EMPTY_QUERY, data: extra.overrides }));
  }
  return render(<MobileThisWeekPage />);
}

describe('今週の予定 — status_cancel は非表示', () => {
  it('status_cancel は出さず、manual_cancel と通常訪問は出す', () => {
    renderWith([
      makeVisit({ id: 'plain' }),
      makeVisit({
        id: 'manual',
        start_time: '11:00:00',
        status: 'cancelled',
        source: 'manual_cancel',
      }),
      makeVisit({
        id: 'status',
        start_time: '13:00:00',
        status: 'cancelled',
        source: 'status_cancel',
      }),
    ]);
    expect(screen.getByText('患者plain')).toBeInTheDocument();
    expect(screen.getByText('患者manual')).toBeInTheDocument();
    expect(screen.queryByText('患者status')).not.toBeInTheDocument();
    // 日付見出しの件数からも外れる (2 件)。
    expect(screen.getByText('2件')).toBeInTheDocument();
  });

  it('非稼働患者の予定が残っていたらバッジ + 薄色で見せる (§3-4)', () => {
    // バッジはステータスを変えた日以降にだけ出す (PO 2026-09-10)。この訪問日 (9/7) と
    // 同日に入院中へ変えた前提。
    renderWith([
      makeVisit({ id: 'residue', patient_status: 'admitted', patient_status_since: '2026-09-07' }),
    ]);
    expect(screen.getByTestId('this-week-inactive-badge-residue')).toHaveTextContent('入院中');
  });

  it('ステータス変更日より前の予定にはバッジを出さない (PO 2026-09-10)', () => {
    // 9/8 に入院中へ変えたなら、9/7 は実際に訪問した日なので従来表示のまま。
    renderWith([
      makeVisit({ id: 'past', patient_status: 'admitted', patient_status_since: '2026-09-08' }),
    ]);
    expect(screen.getByText('患者past')).toBeInTheDocument();
    expect(screen.queryByTestId('this-week-inactive-badge-past')).not.toBeInTheDocument();
  });

  it('稼働中の予定にはバッジを出さない', () => {
    renderWith([makeVisit({ id: 'plain', patient_status: 'active' })]);
    expect(screen.queryByTestId('this-week-inactive-badge-plain')).not.toBeInTheDocument();
  });

  it('その日が status_cancel だけなら日付グループごと消える', () => {
    renderWith([
      makeVisit({
        id: 'status',
        visit_date: '2026-09-08',
        status: 'cancelled',
        source: 'status_cancel',
      }),
    ]);
    expect(screen.getByText('今週の訪問はありません')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 職員イベント / 休み・時間変更の混在表示 (design 2026-09-16 §3 C-3)
// ---------------------------------------------------------------------------

function makeEvent(over: Partial<Record<string, unknown>> & { id: string }) {
  return {
    staff_id: 'staff-1',
    date: '2026-09-07',
    title: '朝会',
    start_time: '08:30',
    end_time: '09:00',
    type: 'イベント',
    source: 'manual',
    blocking: false,
    cancelled_at: null,
    ...over,
  };
}

describe('今週の予定 — 職員イベントの混在', () => {
  it('訪問とイベントを開始時刻順に混ぜて描く', () => {
    renderWith([makeVisit({ id: 'plain', start_time: '10:00:00' })], {
      events: [makeEvent({ id: 'ev1' })],
    });
    const chip = screen.getByTestId('mobile-event-chip-ev1');
    expect(chip).toHaveTextContent('朝会');
    expect(chip).toHaveTextContent('08:30〜09:00');
    // 08:30 のイベントが 10:00 の訪問より前に並ぶ。
    expect(chip.compareDocumentPosition(screen.getByText('患者plain'))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    // 件数は訪問だけ数える (イベントは数えない)。
    expect(screen.getByText('1件')).toBeInTheDocument();
  });

  it('starts_at === ends_at は 📝 メモ扱いで時刻だけ出す', () => {
    renderWith([], { events: [makeEvent({ id: 'memo', start_time: '13:00', end_time: '13:00' })] });
    expect(screen.getByTestId('mobile-event-chip-memo')).toHaveTextContent('📝 13:00');
  });

  it('cancelled_at のイベントは打消線 + 「今週除外」バッジ', () => {
    renderWith([], {
      events: [makeEvent({ id: 'off', cancelled_at: '2026-09-06T00:00:00Z' })],
    });
    const chip = screen.getByTestId('mobile-event-chip-off');
    expect(chip).toHaveTextContent('今週除外');
    expect(chip.querySelector('.line-through')).not.toBeNull();
  });

  it('同一スタッフ・同一時刻・同一タイトルの二重は 1 件に畳む (カイポケを残す)', () => {
    renderWith([], {
      events: [
        makeEvent({ id: 'manual-row', source: 'manual' }),
        makeEvent({ id: 'kaipoke-row', source: 'kaipoke' }),
      ],
    });
    expect(screen.getByTestId('mobile-event-chip-kaipoke-row')).toBeInTheDocument();
    expect(screen.queryByTestId('mobile-event-chip-manual-row')).not.toBeInTheDocument();
    expect(screen.getAllByText('朝会')).toHaveLength(1);
  });

  it('休み / 時間変更は日付見出しの右にバッジで出す', () => {
    renderWith([makeVisit({ id: 'plain' })], {
      overrides: [{ id: 'o1', date: '2026-09-07', type: '休み' }],
    });
    expect(screen.getByTestId('this-week-override-2026-09-07')).toHaveTextContent('🛌休み');
  });

  it('時間変更は ⏱HH:MM〜HH:MM で出す', () => {
    renderWith([makeVisit({ id: 'plain' })], {
      overrides: [
        { id: 'o2', date: '2026-09-07', type: '時間変更', start_time: '10:00', end_time: '15:00' },
      ],
    });
    expect(screen.getByTestId('this-week-override-2026-09-07')).toHaveTextContent('⏱10:00〜15:00');
  });

  it('「今週だけ取消」には赤い取消バッジを出す', () => {
    renderWith([makeVisit({ id: 'manual', status: 'cancelled', source: 'manual_cancel' })]);
    expect(screen.getByTestId('this-week-cancelled-badge-manual')).toHaveTextContent('取消');
  });
});
