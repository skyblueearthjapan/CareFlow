/**
 * 「実績の時刻を合わせる」共通の枠と純関数（設計 `pc-actual-time-edit-design-2026-10-06.md`）。
 *
 * * 退出のひと押しの候補は 2 つだけ（予定の終わり／到着＋予定の長さ = 最初に選ぶ・Q2）。
 * * 打刻なしの訪問は「到着・退出を手で入れる」（D2）: 到着 → 退出の順に PUT。
 * * まとめて入れるときの決め方（Q1）と、1 件ずつ順番に記録して失敗を残すこと（D3）。
 * * 文言に「直す」「修正」「補正」を使わない（PO 決定）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { mockAdjust, mockReset, mockToast } = vi.hoisted(() => ({
  mockAdjust: vi.fn(),
  mockReset: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/components/ui/sonner', () => ({ toast: mockToast }));
vi.mock('@/lib/queries/visit-history', () => ({
  useAdjustVisitActualTime: () => ({ mutateAsync: mockAdjust, isPending: false }),
  useResetVisitActualTime: () => ({ mutateAsync: mockReset, isPending: false }),
}));

import { ApiError } from '@/lib/api-client';

import { ActualTimeAdjustBox } from '../ActualTimeAdjustBox';
import {
  bulkDepartureTime,
  departureCandidates,
  runBulkDepartures,
  type ActualTimeTarget,
} from '../actualTimeAdjust';

const FORBIDDEN = /直す|直し|修正|補正/;

function target(over: Partial<ActualTimeTarget> = {}): ActualTimeTarget {
  return {
    visitId: 'v-1',
    plannedStart: '13:00',
    plannedEnd: '13:35',
    // 04:13Z = JST 13:13
    arrivalAt: '2026-10-06T04:13:00Z',
    departureAt: null,
    arrivalReadAt: '2026-10-06T04:13:00Z',
    departureReadAt: null,
    arrivalAdjusted: false,
    departureAdjusted: false,
    arrivalManual: false,
    departureManual: false,
    adjustAllowed: true,
    manualArrivalAllowed: false,
    hasNoShow: false,
    noShowReason: null,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockAdjust.mockResolvedValue({});
  mockReset.mockResolvedValue({});
});

describe('departureCandidates（ひと押しの候補）', () => {
  it('予定の終わり／到着＋予定の長さ の 2 つだけ', () => {
    expect(departureCandidates('13:13', '13:00', '13:35')).toEqual([
      { key: 'planned_end', label: '予定の終わり', time: '13:35' },
      { key: 'arrival_plus_len', label: '到着＋予定の長さ', time: '13:48' },
    ]);
  });

  it('予定が無い（予定外の訪問）なら候補は出さない', () => {
    expect(departureCandidates('13:13', null, null)).toEqual([]);
  });
});

describe('ActualTimeAdjustBox — 退出の読み取りが無い訪問', () => {
  it('候補は 2 つ・最初は到着＋予定の長さ。押すと時刻が入り、記録する', async () => {
    render(<ActualTimeAdjustBox target={target()} naReason="—" testIdPrefix="t" />);
    const chips = screen.getAllByTestId(/^t-cand-/);
    expect(chips).toHaveLength(2);
    expect(screen.getByTestId('t-cand-arrival_plus_len')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('退出の時刻')).toHaveValue('13:48');
    expect(screen.getByTestId('t-save-departure')).toHaveTextContent('退出を 13:48 で記録する');

    fireEvent.click(screen.getByTestId('t-cand-planned_end'));
    expect(screen.getByLabelText('退出の時刻')).toHaveValue('13:35');
    expect(screen.getByTestId('t-cand-planned_end')).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByTestId('t-save-departure'));
    await waitFor(() =>
      expect(mockAdjust).toHaveBeenCalledWith({ visitId: 'v-1', kind: 'departure', time: '13:35' }),
    );
    expect(mockToast.success).toHaveBeenCalledWith('退出を 13:35 で記録しました');
  });

  it('手で入れた到着は「手入力の時刻を消す」で消せる', async () => {
    render(
      <ActualTimeAdjustBox
        target={target({ arrivalReadAt: null, arrivalAdjusted: true, arrivalManual: true })}
        naReason="—"
        testIdPrefix="t"
      />,
    );
    expect(screen.getByTestId('t-note-arrival')).toHaveTextContent('読み取りなし ・ 手入力の時刻');
    fireEvent.click(screen.getByTestId('t-reset-arrival'));
    await waitFor(() =>
      expect(mockReset).toHaveBeenCalledWith({ visitId: 'v-1', kind: 'arrival' }),
    );
    expect(mockToast.success).toHaveBeenCalledWith('手入力の到着時刻を消しました');
  });

  it('画面の文言に「直す」「修正」「補正」を使わない', () => {
    const { container } = render(
      <ActualTimeAdjustBox target={target()} naReason="—" testIdPrefix="t" />,
    );
    expect(container.textContent ?? '').not.toMatch(FORBIDDEN);
  });
});

describe('ActualTimeAdjustBox — 打刻なしの訪問（到着・退出を手で入れる）', () => {
  const none = () =>
    target({
      arrivalAt: null,
      arrivalReadAt: null,
      adjustAllowed: false,
      manualArrivalAllowed: true,
      plannedStart: '10:00',
      plannedEnd: '10:45',
    });

  it('手で入れられない訪問（スタッフ）には何も出さない', () => {
    const { container } = render(
      <ActualTimeAdjustBox target={{ ...none(), manualArrivalAllowed: false }} naReason="—" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('入力は空から始まり、入れるまで保存できない。「予定の時刻を入れる」で両方入る', () => {
    render(<ActualTimeAdjustBox target={none()} naReason="—" testIdPrefix="t" />);
    expect(screen.getByLabelText('到着の時刻')).toHaveValue('');
    expect(screen.getByLabelText('退出の時刻')).toHaveValue('');
    // 予定の時刻は横に出す。
    expect(screen.getByTestId('t-manual-box')).toHaveTextContent('予定 10:00');
    expect(screen.getByTestId('t-manual-box')).toHaveTextContent('予定 10:45');
    expect(screen.getByTestId('t-manual-save')).toBeDisabled();

    fireEvent.click(screen.getByTestId('t-manual-fill-planned'));
    expect(screen.getByLabelText('到着の時刻')).toHaveValue('10:00');
    expect(screen.getByLabelText('退出の時刻')).toHaveValue('10:45');
    expect(screen.getByTestId('t-manual-save')).not.toBeDisabled();
  });

  it('到着 → 退出の順に記録する', async () => {
    const { container } = render(
      <ActualTimeAdjustBox target={none()} naReason="—" testIdPrefix="t" />,
    );
    expect(screen.getByTestId('t-manual-box')).toHaveTextContent('到着・退出を手で入れる');
    expect(container.textContent ?? '').not.toMatch(FORBIDDEN);
    fireEvent.change(screen.getByLabelText('到着の時刻'), { target: { value: '10:02' } });
    fireEvent.change(screen.getByLabelText('退出の時刻'), { target: { value: '10:47' } });
    fireEvent.click(screen.getByTestId('t-manual-save'));
    await waitFor(() => expect(mockAdjust).toHaveBeenCalledTimes(2));
    expect(mockAdjust.mock.calls.map((c) => c[0])).toEqual([
      { visitId: 'v-1', kind: 'arrival', time: '10:02' },
      { visitId: 'v-1', kind: 'departure', time: '10:47' },
    ]);
    expect(mockToast.success).toHaveBeenCalledWith('到着 10:02・退出 10:47 を手入力で記録しました');
  });

  it('退出が到着より前なら送らずに知らせる', () => {
    render(<ActualTimeAdjustBox target={none()} naReason="—" testIdPrefix="t" />);
    fireEvent.change(screen.getByLabelText('到着の時刻'), { target: { value: '10:00' } });
    fireEvent.change(screen.getByLabelText('退出の時刻'), { target: { value: '09:50' } });
    fireEvent.click(screen.getByTestId('t-manual-save'));
    expect(screen.getByTestId('t-error')).toHaveTextContent('退出は到着より後の時刻にしてください');
    expect(mockAdjust).not.toHaveBeenCalled();
  });

  it('到着だけでも記録できる（退出は空のまま）', async () => {
    render(<ActualTimeAdjustBox target={none()} naReason="—" testIdPrefix="t" />);
    fireEvent.change(screen.getByLabelText('到着の時刻'), { target: { value: '10:00' } });
    fireEvent.click(screen.getByTestId('t-manual-save'));
    await waitFor(() => expect(mockAdjust).toHaveBeenCalledTimes(1));
    expect(mockAdjust).toHaveBeenCalledWith({ visitId: 'v-1', kind: 'arrival', time: '10:00' });
  });

  it('未訪問の記録がある訪問には注意書きを出す（入れることはできる）', () => {
    render(
      <ActualTimeAdjustBox
        target={{ ...none(), hasNoShow: true, noShowReason: '不在でした' }}
        naReason="—"
        testIdPrefix="t"
      />,
    );
    expect(screen.getByTestId('t-no-show')).toHaveTextContent(
      'この訪問には未訪問の記録があります（理由: 不在でした）。到着を入れると訪問した扱いになります',
    );
    expect(screen.getByLabelText('到着の時刻')).not.toBeDisabled();
  });

  it('到着が入った後は、未訪問の記録を履歴として出す', () => {
    render(
      <ActualTimeAdjustBox
        target={target({ hasNoShow: true, noShowReason: '不在でした', arrivalManual: true })}
        naReason="—"
        testIdPrefix="t"
      />,
    );
    expect(screen.getByTestId('t-no-show')).toHaveTextContent(
      '到着が入っているため、訪問した扱いです',
    );
  });
});

describe('まとめて退出を入れる（決め方と順番の記録）', () => {
  const row = { arrival: '13:13', plannedStart: '13:00', plannedEnd: '13:35' };

  it('到着＋予定の長さ / 到着＋○分 / 予定の終わり', () => {
    expect(bulkDepartureTime('len', row)).toEqual({ time: '13:48', why: '到着＋35分' });
    expect(bulkDepartureTime('min', row, 50)).toEqual({ time: '14:03', why: '到着＋50分' });
    expect(bulkDepartureTime('end', row)).toEqual({ time: '13:35', why: '予定の終わり' });
    // 予定が無い・日をまたぐ・分が不正は決められない。
    expect(bulkDepartureTime('len', { ...row, plannedStart: null, plannedEnd: null }).time).toBe(
      null,
    );
    expect(bulkDepartureTime('min', { ...row, arrival: '23:50' }, 35).time).toBe(null);
    expect(bulkDepartureTime('min', row, 0)).toEqual({
      time: null,
      why: '分は 1〜600 で入れてください',
    });
    expect(bulkDepartureTime('min', row, 601).time).toBe(null);
    expect(bulkDepartureTime('min', row, Number.NaN).time).toBe(null);
    expect(bulkDepartureTime('min', row, 600).time).toBe('23:13');
  });

  it('1 件ずつ順番に呼び、失敗は理由つきで残して続ける', async () => {
    const order: string[] = [];
    let inFlight = 0;
    const put = vi.fn(async (visitId: string) => {
      inFlight += 1;
      expect(inFlight).toBe(1); // 並べて投げない
      order.push(visitId);
      await new Promise((r) => setTimeout(r, 1));
      inFlight -= 1;
      if (visitId === 'b') throw new ApiError('API 422', 422, { detail: '到着より前です' });
    });
    const results = await runBulkDepartures(
      [
        { visitId: 'a', time: '13:48', why: '到着＋35分' },
        { visitId: 'b', time: '10:00', why: '到着＋35分' },
        { visitId: 'c', time: null, why: '予定が無いため決められません' },
        { visitId: 'd', time: '15:00', why: '到着＋35分' },
      ],
      put,
      (e) => (e instanceof ApiError ? String((e.body as { detail: string }).detail) : 'error'),
    );
    expect(order).toEqual(['a', 'b', 'd']);
    expect(results.map((r) => [r.visitId, r.ok, r.error])).toEqual([
      ['a', true, null],
      ['b', false, '到着より前です'],
      ['c', false, '予定が無いため決められません'],
      ['d', true, null],
    ]);
  });
});
