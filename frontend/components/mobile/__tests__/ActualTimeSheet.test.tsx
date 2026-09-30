/**
 * 「実績の時刻を合わせる」シート (設計 2026-09-30 §7-4)。
 *
 * 文言は `docs/mockups/mobile-actual-time-fix-mock.html` が正。画面に
 * 「直す」「修正」「補正」を出さないこと (PO 決定 #7) もここで縛る。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import {
  ActualTimeSheet,
  actualTimeRange,
  type ActualTimeSheetProps,
  type ActualTimeTimes,
} from '../ActualTimeSheet';

const hm = (h: number, m: number) => h * 60 + m;

/** 予定 13:00–13:35・13:06 に読み取り・まだ退出していない訪問。 */
function renderSheet(over: Partial<ActualTimeSheetProps> = {}) {
  const onSave = vi.fn(async () => true);
  const onOpenChange = vi.fn();
  const props: ActualTimeSheetProps = {
    open: true,
    onOpenChange,
    initialKind: 'arrival',
    patientName: '坂本 トミ',
    planStart: hm(13, 0),
    planEnd: hm(13, 35),
    arrival: { at: hm(13, 6), readAt: hm(13, 6) },
    departure: { at: null, readAt: null },
    isToday: true,
    onSave,
    ...over,
  };
  render(<ActualTimeSheet {...props} />);
  return { onSave: props.onSave as typeof onSave, onOpenChange };
}

const draft = () => screen.getByTestId('actual-time-draft').textContent;
// 保存ボタン。未記録の退出を入れるときだけ「退出を HH:MM で記録する」になる。
const saveButton = () => screen.getByRole('button', { name: /に合わせる$|で記録する$/ });

afterEach(() => {
  vi.useRealTimers();
});

describe('actualTimeRange — 合わせられる範囲 (サーバの検証と同じ規則)', () => {
  const base: ActualTimeTimes = {
    arrival: { at: hm(13, 6), readAt: hm(13, 6) },
    departure: { at: null, readAt: null },
    isToday: true,
    nowMin: hm(14, 0),
  };

  it('到着: 読取時刻の 90 分前 〜 読取時刻', () => {
    expect(actualTimeRange('arrival', base)).toEqual([hm(11, 36), hm(13, 6)]);
  });

  it('到着: 退出の実績があれば、その 1 分前まで', () => {
    const t = { ...base, departure: { at: hm(13, 0), readAt: hm(13, 0) } };
    expect(actualTimeRange('arrival', t)).toEqual([hm(11, 36), hm(12, 59)]);
  });

  it('到着: 0 時より前にはさかのぼらない', () => {
    const t = { ...base, arrival: { at: 20, readAt: 20 } };
    expect(actualTimeRange('arrival', t)).toEqual([0, 20]);
  });

  it('退出 (読み取りなし・今日): 到着の 1 分後 〜 現在時刻', () => {
    expect(actualTimeRange('departure', base)).toEqual([hm(13, 7), hm(14, 0)]);
  });

  it('退出 (読み取りなし・過去の訪問): 23:59 まで', () => {
    expect(actualTimeRange('departure', { ...base, isToday: false })).toEqual([
      hm(13, 7),
      hm(23, 59),
    ]);
  });

  it('退出 (読み取りあり): 読取時刻の 30 分後まで。今日は現在時刻が先に来る', () => {
    const read = { at: hm(13, 41), readAt: hm(13, 41) };
    expect(actualTimeRange('departure', { ...base, isToday: false, departure: read })).toEqual([
      hm(13, 7),
      hm(14, 11),
    ]);
    expect(actualTimeRange('departure', { ...base, departure: read })).toEqual([
      hm(13, 7),
      hm(14, 0),
    ]);
  });

  it('退出は、合わせた後の到着より後', () => {
    const t = { ...base, arrival: { at: hm(12, 56), readAt: hm(13, 6) } };
    expect(actualTimeRange('departure', t)[0]).toBe(hm(12, 57));
  });
});

describe('ActualTimeSheet — 到着', () => {
  it('見出し・予定・読取時刻を出し、変えていなければ保存できない', () => {
    renderSheet();
    expect(screen.getByText('実績の時刻を合わせる')).toBeInTheDocument();
    expect(screen.getByText(/坂本 トミ.*予定 13:00–13:35/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '到着 13:06' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: '退出 （未記録）' })).toBeInTheDocument();
    expect(draft()).toBe('13:06');
    expect(screen.getByTestId('actual-time-sub')).toHaveTextContent('読取 13:06 のまま');
    expect(screen.getByTestId('actual-time-summary')).toHaveTextContent(
      '予定 13:00–13:35（35 分）',
    );
    expect(saveButton()).toHaveTextContent('13:06 に合わせる');
    expect(saveButton()).toBeDisabled();
  });

  it('ひと押しチップで飛び、読取時刻との差を出す', () => {
    renderSheet();
    for (const label of ['読取どおり', '5分前', '10分前', '15分前', '20分前', '30分前']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('button', { name: '10分前' }));
    expect(draft()).toBe('12:56');
    expect(screen.getByTestId('actual-time-sub')).toHaveTextContent('読取 13:06 ・ 10 分前');
    expect(screen.getByRole('button', { name: '10分前' })).toHaveAttribute('aria-pressed', 'true');
    // ホイールもその時刻へ。
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent('12:56');
    expect(saveButton()).toHaveTextContent('12:56 に合わせる');
    expect(saveButton()).toBeEnabled();
  });

  it('「1 分 まえ」「1 分 あと」で詰める。読取時刻より後にはできない', () => {
    renderSheet();
    expect(screen.getByRole('button', { name: '1 分 あと' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '1 分 まえ' }));
    fireEvent.click(screen.getByRole('button', { name: '1 分 まえ' }));
    expect(draft()).toBe('13:04');
    fireEvent.click(screen.getByRole('button', { name: '1 分 あと' }));
    expect(draft()).toBe('13:05');
  });

  it('さかのぼれるのは 90 分まで — ホイールに範囲の外の時刻は無く、端で「1 分 まえ」が止まる', () => {
    renderSheet({ arrival: { at: hm(11, 36), readAt: hm(13, 6) } });
    expect(draft()).toBe('11:36');
    expect(screen.getByRole('button', { name: '1 分 まえ' })).toBeDisabled();
    const options = screen.getAllByRole('option');
    expect(options[0]).toHaveTextContent('11:36');
    expect(options[options.length - 1]).toHaveTextContent('13:06');
    expect(screen.queryByText('11:35')).toBeNull();
    expect(screen.queryByText('13:07')).toBeNull();
  });

  it('理由は尋ねない。選んだ時刻だけで保存し、成功したら閉じる (PO 決定 2026-10-01)', async () => {
    const { onSave, onOpenChange } = renderSheet();
    for (const label of ['インターホン待ち', '読み取りが後になった', '読み取りなし', 'その他']) {
      expect(screen.queryByRole('button', { name: label })).toBeNull();
    }

    fireEvent.click(screen.getByRole('button', { name: '10分前' }));
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('arrival', '12:56'));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('保存できなかったら開いたまま', async () => {
    const onSave = vi.fn(async () => false);
    const { onOpenChange } = renderSheet({ onSave });
    fireEvent.click(screen.getByRole('button', { name: '10分前' }));
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByText('実績の時刻を合わせる')).toBeInTheDocument();
  });

  it('合わせてある到着を「読取どおり」に戻して保存 = 読取時刻に戻す (time = null)', async () => {
    const { onSave } = renderSheet({ arrival: { at: hm(12, 56), readAt: hm(13, 6) } });
    expect(draft()).toBe('12:56');
    expect(saveButton()).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '読取どおり' }));
    expect(draft()).toBe('13:06');
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('arrival', null));
  });

  it('退出の実績があるときは滞在を出す (予定と同じなら「予定どおり」)', () => {
    renderSheet({ departure: { at: hm(13, 31), readAt: hm(13, 31) } });
    expect(screen.getByTestId('actual-time-summary')).toHaveTextContent('滞在 25 分（予定 35 分）');
    expect(screen.getByTestId('actual-time-summary')).not.toHaveTextContent('予定どおり');
    fireEvent.click(screen.getByRole('button', { name: '10分前' }));
    expect(screen.getByTestId('actual-time-summary')).toHaveTextContent(
      '滞在 35 分（予定 35 分） 予定どおり',
    );
  });

  it('保存中は二度押しできない', () => {
    renderSheet({ saving: true, arrival: { at: hm(12, 56), readAt: hm(13, 6) } });
    fireEvent.click(screen.getByRole('button', { name: '読取どおり' }));
    expect(saveButton()).toBeDisabled();
  });
});

describe('ActualTimeSheet — 退出', () => {
  it('読み取りの無い退出: 「到着 + 予定の長さ」から始まり、時刻だけで記録する', async () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z')); // JST 14:00
    const { onSave } = renderSheet({ initialKind: 'departure' });
    expect(screen.getByRole('button', { name: '退出 （未記録）' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(draft()).toBe('13:41');
    expect(screen.getByTestId('actual-time-sub')).toHaveTextContent(
      '退出の読み取りがありません（手入力）',
    );
    expect(screen.getByTestId('actual-time-summary')).toHaveTextContent(
      '滞在 35 分（予定 35 分） 予定どおり',
    );
    expect(screen.queryByRole('button', { name: '読み取りなし' })).toBeNull();
    // まだ入れていないので、そのままでも保存できる。
    expect(saveButton()).toBeEnabled();
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('departure', '13:41'));
  });

  it('未記録の退出は「合わせる」ではなく「退出を HH:MM で記録する」— 新しく記録する操作だと分かる (M-2)', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet();
    // 到着側は今までどおり。
    expect(saveButton()).toHaveTextContent('13:06 に合わせる');
    expect(screen.queryByTestId('actual-time-manual-note')).toBeNull();

    // 到着を合わせに来て、退出側へ切り替えた。
    fireEvent.click(screen.getByRole('button', { name: '退出 （未記録）' }));
    expect(saveButton()).toHaveTextContent('退出を 13:41 で記録する');
    expect(screen.queryByRole('button', { name: /に合わせる$/ })).toBeNull();
    expect(screen.getByTestId('actual-time-manual-note')).toHaveTextContent(
      '退出を記録すると、この訪問は完了になります。',
    );
    fireEvent.click(screen.getByRole('button', { name: '滞在 30分' }));
    expect(saveButton()).toHaveTextContent('退出を 13:36 で記録する');
  });

  it('読み取りのある退出: すでにある退出を動かすのは「合わせる」— 時刻だけで保存する', async () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    const { onSave } = renderSheet({
      initialKind: 'departure',
      departure: { at: hm(13, 31), readAt: hm(13, 31) },
    });
    expect(screen.queryByRole('button', { name: '読み取りが後になった' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '1 分 あと' }));
    expect(saveButton()).toHaveTextContent('13:32 に合わせる');
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('departure', '13:32'));
  });

  it('ひと押しは滞在の長さで選ぶ', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet({ initialKind: 'departure' });
    expect(screen.getByRole('button', { name: '滞在 予定どおり 35分' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: '滞在 30分' }));
    expect(draft()).toBe('13:36');
    fireEvent.click(screen.getByRole('button', { name: '滞在 45分' }));
    expect(draft()).toBe('13:51');
  });

  it('今日の訪問は現在時刻まで — 先の時刻へは飛べず、端に寄る', () => {
    vi.setSystemTime(new Date('2026-09-30T04:30:00Z')); // JST 13:30
    renderSheet({ initialKind: 'departure' });
    // 到着 13:06 + 予定 35 分 = 13:41 はまだ来ていないので、いまの 13:30 から。
    expect(draft()).toBe('13:30');
    expect(screen.getByRole('button', { name: '1 分 あと' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '滞在 45分' }));
    expect(draft()).toBe('13:30');
    expect(screen.queryByText('13:31')).toBeNull();
  });

  it('過去の訪問は 23:59 まで入れられる', () => {
    vi.setSystemTime(new Date('2026-09-30T00:30:00Z')); // いまは翌日の朝
    renderSheet({ initialKind: 'departure', isToday: false });
    expect(draft()).toBe('13:41');
    const options = screen.getAllByRole('option');
    expect(options[options.length - 1]).toHaveTextContent('23:59');
  });

  it('到着の直後で入れられる時刻がまだ無いときは、保存できない', () => {
    vi.setSystemTime(new Date('2026-09-30T04:06:30Z')); // JST 13:06 (到着と同じ分)
    renderSheet({ initialKind: 'departure' });
    expect(screen.getByTestId('actual-time-no-range')).toBeInTheDocument();
    expect(screen.queryByTestId('time-wheel')).toBeNull();
    expect(saveButton()).toBeDisabled();
  });

  it('到着 ⇄ 退出を切り替えると、その側の時刻に変わる', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet({ departure: { at: hm(13, 31), readAt: hm(13, 31) } });
    expect(draft()).toBe('13:06');
    fireEvent.click(screen.getByRole('button', { name: '退出 13:31' }));
    expect(draft()).toBe('13:31');
    expect(screen.getByTestId('actual-time-sub')).toHaveTextContent('読取 13:31 のまま');
    fireEvent.click(screen.getByRole('button', { name: '1 分 あと' }));
    expect(screen.getByTestId('actual-time-sub')).toHaveTextContent('読取 13:31 ・ 1 分後');
    fireEvent.click(screen.getByRole('button', { name: '到着 13:06' }));
    expect(draft()).toBe('13:06');
  });

  it('圏外で退避した到着を合わせている間は、退出側を選べない', () => {
    renderSheet({ departureDisabled: true, initialKind: 'departure' });
    expect(screen.getByRole('button', { name: '退出 （未記録）' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '到着 13:06' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(draft()).toBe('13:06');
  });
});

describe('ActualTimeSheet — 手で入れた退出を取り消す (M-2)', () => {
  /** 13:41 に手で入れた退出 (読み取りなし)。 */
  const MANUAL = { at: hm(13, 41), readAt: null };

  it('退出側に「入れた退出時刻を取り消す」を出し、押すと取り消して閉じる', async () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    const onCancelManualDeparture = vi.fn(async () => true);
    const { onSave, onOpenChange } = renderSheet({
      departure: MANUAL,
      departureManual: true,
      onCancelManualDeparture,
    });
    // 到着側には出さない。
    expect(screen.queryByTestId('actual-time-cancel-manual')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '退出 13:41' }));
    const cancel = screen.getByTestId('actual-time-cancel-manual');
    expect(cancel).toHaveTextContent('入れた退出時刻を取り消す');
    expect(cancel.className).toContain('h-11');
    // すでに入っている退出を動かすのは「合わせる」。
    expect(saveButton()).toHaveTextContent('13:41 に合わせる');

    fireEvent.click(cancel);
    await waitFor(() => expect(onCancelManualDeparture).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(onSave).not.toHaveBeenCalled();
  });

  it('取り消せなかったら開いたまま', async () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    const onCancelManualDeparture = vi.fn(async () => false);
    const { onOpenChange } = renderSheet({
      initialKind: 'departure',
      departure: MANUAL,
      departureManual: true,
      onCancelManualDeparture,
    });
    fireEvent.click(screen.getByTestId('actual-time-cancel-manual'));
    await waitFor(() => expect(onCancelManualDeparture).toHaveBeenCalled());
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it('読み取りのある退出には出さない', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet({
      initialKind: 'departure',
      departure: { at: hm(13, 31), readAt: hm(13, 31) },
      onCancelManualDeparture: vi.fn(async () => true),
    });
    expect(screen.queryByTestId('actual-time-cancel-manual')).toBeNull();
  });

  it('未記録の退出には出さない', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet({
      initialKind: 'departure',
      onCancelManualDeparture: vi.fn(async () => true),
    });
    expect(screen.queryByTestId('actual-time-cancel-manual')).toBeNull();
  });

  it('保存中は押せない', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet({
      initialKind: 'departure',
      departure: MANUAL,
      departureManual: true,
      onCancelManualDeparture: vi.fn(async () => true),
      saving: true,
    });
    expect(screen.getByTestId('actual-time-cancel-manual')).toBeDisabled();
  });
});

describe('ActualTimeSheet — 読み上げ (L-4)', () => {
  it('説明文の id を上書きせず、Radix の警告が出ない', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderSheet();
    const dialogMessages = [...warn.mock.calls, ...error.mock.calls]
      .map((args) => String(args[0]))
      .filter((m) => /Description|aria-describedby|DialogTitle/.test(m));
    expect(dialogMessages).toEqual([]);
    // Radix が振った id のまま。
    expect(screen.getByText(/坂本 トミ.*予定 13:00–13:35/).id).not.toBe('actual-time-sheet-desc');
    warn.mockRestore();
    error.mockRestore();
  });
});

describe('ActualTimeSheet — 文言の決まり (PO 決定 #7)', () => {
  it('「直す」「修正」「補正」を画面に出さない', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet({ departure: { at: hm(13, 31), readAt: hm(13, 31) } });
    const sheet = screen.getByTestId('actual-time-sheet');
    expect(sheet.textContent).not.toMatch(/直す|直し|修正|補正/);
    fireEvent.click(screen.getByRole('button', { name: '退出 13:31' }));
    expect(sheet.textContent).not.toMatch(/直す|直し|修正|補正/);
  });

  it('未記録の退出を入れる表示でも出さない', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet({ initialKind: 'departure' });
    expect(screen.getByTestId('actual-time-sheet').textContent).not.toMatch(/直す|直し|修正|補正/);
  });

  it('手で入れた退出を取り消す表示でも出さない', () => {
    vi.setSystemTime(new Date('2026-09-30T05:00:00Z'));
    renderSheet({
      initialKind: 'departure',
      departure: { at: hm(13, 41), readAt: null },
      departureManual: true,
      onCancelManualDeparture: vi.fn(async () => true),
    });
    expect(screen.getByTestId('actual-time-sheet').textContent).not.toMatch(/直す|直し|修正|補正/);
  });

  it('閉じるボタンは 44px (親指で押せる大きさ)', () => {
    renderSheet();
    expect(screen.getByRole('button', { name: '閉じる' }).className).toContain('h-11 w-11');
  });
});
