/** アラートトレイ: 優先順 / 全件ドロップダウン / 連絡ボタン。 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

import { MonitorAlertTray } from '../MonitorAlertTray';
import { makeRow, makeVisit } from './fixtures';

function buildRows() {
  const review = makeVisit({ patient_name: 'R子', alert_level: 'review', start_time: '08:00' });
  const missing = makeVisit({ patient_name: 'M男', alert_level: 'missing', start_time: '09:00' });
  const mismatch = makeVisit({
    patient_name: 'X代',
    alert_level: 'mismatch',
    start_time: '10:00',
    arrival: {
      kind: 'arrival',
      scanned_at: '2026-06-30T01:00:00Z',
      match_status: 'mismatch',
      distance_m: 360,
      is_override: false,
    },
  });
  const none = makeVisit({ patient_name: '正常', alert_level: 'none' });
  return {
    rows: [makeRow({ visits: [review, missing, mismatch, none] })],
    review,
    missing,
    mismatch,
  };
}

describe('MonitorAlertTray', () => {
  it('未訪問→場所違い→要確認 の優先順で並ぶ (正常は除外)', () => {
    const { rows } = buildRows();
    const { container } = render(
      <MonitorAlertTray rows={rows} selectedVisitId={null} onSelectVisit={vi.fn()} />,
    );
    const cards = container.querySelectorAll('[role="button"][data-testid^="monitor-alert-"]');
    const names = Array.from(cards).map(
      (c) => within(c as HTMLElement).getByText(/男|代|子/).textContent,
    );
    expect(names).toEqual(['M男', 'X代', 'R子']);
  });

  it('4 件以上で「全件」ボタンが出て、クリックでドロップダウンが開く', () => {
    const { rows } = buildRows(); // alerts = 3 件 → ボタン無し
    const extra = makeVisit({ patient_name: '追加', alert_level: 'review' });
    rows[0].visits.push(extra);
    render(<MonitorAlertTray rows={rows} selectedVisitId={null} onSelectVisit={vi.fn()} />);
    const more = screen.getByTestId('monitor-alert-more');
    expect(screen.queryByTestId('monitor-alert-popover')).toBeNull();
    fireEvent.click(more);
    expect(screen.getByTestId('monitor-alert-popover')).toBeInTheDocument();
  });

  it('未訪問カードの連絡ボタンで onSelectVisit が呼ばれる', () => {
    const { rows, missing } = buildRows();
    const onSelectVisit = vi.fn();
    render(<MonitorAlertTray rows={rows} selectedVisitId={null} onSelectVisit={onSelectVisit} />);
    fireEvent.click(screen.getByTestId(`monitor-alert-contact-${missing.visit_id}`));
    expect(onSelectVisit).toHaveBeenCalledWith(missing.visit_id);
  });

  it('異常が無ければ「要対応の異常はありません」', () => {
    const row = makeRow({ visits: [makeVisit({ alert_level: 'none' })] });
    render(<MonitorAlertTray rows={[row]} selectedVisitId={null} onSelectVisit={vi.fn()} />);
    expect(screen.getByText(/要対応の異常はありません/)).toBeInTheDocument();
  });

  it('同一 visit_group_id の 2 行は 1 枚に集約し worst を表示・2名バッジ・両スタッフ名', () => {
    const gid = '22222222-2222-2222-2222-222222222222';
    const reviewMember = makeVisit({
      visit_group_id: gid,
      patient_name: '合算 様',
      alert_level: 'review',
      phase: 'inprogress',
    });
    const missingMember = makeVisit({
      visit_group_id: gid,
      patient_name: '合算 様',
      alert_level: 'missing',
      phase: 'missing',
    });
    const rows = [
      makeRow({ staff_name: 'A介護', visits: [reviewMember] }),
      makeRow({ staff_name: 'B介護', visits: [missingMember] }),
    ];
    render(<MonitorAlertTray rows={rows} selectedVisitId={null} onSelectVisit={vi.fn()} />);

    // 集約されて 1 件のみ。
    const cards = document.querySelectorAll('[role="button"][data-testid^="monitor-alert-"]');
    expect(cards).toHaveLength(1);
    // worst = 未訪問。
    expect(screen.getByText('要対応 1件')).toBeInTheDocument();
    expect(screen.getByText('未訪問')).toBeInTheDocument();
    // 2 名バッジと両スタッフ名。
    expect(screen.getByText('2名')).toBeInTheDocument();
    expect(screen.getByText('A介護・B介護')).toBeInTheDocument();
  });

  // --- 代行 / 予定外 (qr-open-checkin-design.md §6 #9) ---

  it('代行・予定外は理由ラベルのチップを出す (優先順は不変)', () => {
    const sub = makeVisit({
      patient_name: '代行 対象',
      staff_name: '予定 太郎',
      actual_staff_name: '実績 次郎',
      is_substitute: true,
      alert_level: 'review',
      start_time: '09:00',
    });
    const unplanned = makeVisit({
      patient_name: '飛込 花子',
      actual_staff_name: '実績 次郎',
      is_unplanned: true,
      alert_level: 'review',
      start_time: '10:00',
    });
    const missing = makeVisit({
      patient_name: 'M男',
      alert_level: 'missing',
      phase: 'missing',
      start_time: '11:00',
    });
    render(
      <MonitorAlertTray
        rows={[makeRow({ visits: [sub, unplanned, missing] })]}
        selectedVisitId={null}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getByTestId(`monitor-alert-chip-substitute-${sub.visit_id}`).textContent).toBe(
      '代行',
    );
    expect(
      screen.getByTestId(`monitor-alert-chip-unplanned-${unplanned.visit_id}`).textContent,
    ).toBe('予定外');
    // 理由未入力でも代行/予定外は 1 行で状況を示す。
    expect(screen.getAllByText('予定: 予定 太郎 / 実績: 実績 次郎').length).toBeGreaterThan(0);
    expect(screen.getAllByText('予定に無い訪問（QR打刻）').length).toBeGreaterThan(0);
    // 優先順 (未訪問 → 場所違い → 要確認) は不変: 未訪問が先頭。
    const cards = document.querySelectorAll('[role="button"][data-testid^="monitor-alert-"]');
    expect(cards[0].getAttribute('data-testid')).toBe(`monitor-alert-${missing.visit_id}`);
    // 通常の要対応にはチップを出さない。
    expect(screen.queryByTestId(`monitor-alert-chip-substitute-${missing.visit_id}`)).toBeNull();
    expect(screen.queryByTestId(`monitor-alert-chip-unplanned-${missing.visit_id}`)).toBeNull();
  });
});
