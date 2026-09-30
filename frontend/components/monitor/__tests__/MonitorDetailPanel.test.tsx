/** 詳細パネル: 空 / コース一覧 / visit 詳細 の切替。 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// 「🎙 記録を見る」は音声記録の一覧を引く (visit-voice-record-design §11-2)。
// このパネルの単体テストでは記録なし = リンクを出さない状態に固定する。
vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: () => ({ data: { items: [], total: 0 }, isLoading: false }),
  useVisitRecording: () => ({ data: null, isLoading: false }),
  useUpdateRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRetryRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  recordingAudioUrl: (id: string) => `/api/v1/visit-recordings/${id}/audio`,
}));

import { MonitorDetailPanel } from '../MonitorDetailPanel';
import { makeRow, makeVisit } from './fixtures';

describe('MonitorDetailPanel', () => {
  it('未選択は空状態を表示', () => {
    render(<MonitorDetailPanel visit={null} row={null} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-detail-empty')).toBeInTheDocument();
  });

  // 行 = 職員 (2026-10-01): 行のみ選択時は「コースの訪問一覧」ではなく、その人の 1 日の順路。
  it('行のみ選択時はその人の 1 日の順路、訪問クリックで onSelectVisit', () => {
    const v = makeVisit({ patient_name: '本田 武' });
    const row = makeRow({ visits: [v] });
    const onSelectVisit = vi.fn();
    render(<MonitorDetailPanel visit={null} row={row} onSelectVisit={onSelectVisit} />);
    expect(screen.getByTestId('monitor-detail-route')).toBeInTheDocument();
    fireEvent.click(screen.getByText(/本田 武/));
    expect(onSelectVisit).toHaveBeenCalledWith(v.visit_id);
  });

  it('順路は札つき・拠点をまたぐ所に「稲毛から都賀へ移動」を挟む', () => {
    const INAGE = '00000000-0000-0000-0000-00000000aaaa';
    const TSUGA = '00000000-0000-0000-0000-00000000bbbb';
    const v1 = makeVisit({
      course_tag: '稲D',
      course_office_id: INAGE,
      course_office_name: '稲毛',
    });
    const v2 = makeVisit({
      course_tag: '都臨2',
      course_office_id: TSUGA,
      course_office_name: '都賀',
      start_time: '17:00',
      end_time: '17:35',
    });
    const v3 = makeVisit({ is_unplanned: true, start_time: '18:00', end_time: '18:30' });
    render(
      <MonitorDetailPanel
        visit={null}
        row={makeRow({ visits: [v1, v2, v3] })}
        onSelectVisit={vi.fn()}
        officeIds={[INAGE, TSUGA]}
      />,
    );
    const route = screen.getByTestId('monitor-detail-route');
    expect(route.textContent).toContain('稲D');
    expect(route.textContent).toContain('都臨2');
    expect(route.textContent).toContain('予定外');
    expect(screen.getByTestId(`monitor-route-hop-${v2.visit_id}`).textContent).toBe(
      '↓ 稲毛から都賀へ移動',
    );
    expect(screen.queryByTestId(`monitor-route-hop-${v1.visit_id}`)).toBeNull();
  });

  it('訪問の詳細にはコースの札と拠点を出す (行ではなく訪問ごと)', () => {
    const v = makeVisit({
      staff_name: '担当 A',
      course_tag: '都臨2',
      course_office_name: '都賀',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-detail-visit').textContent).toContain(
      '担当: 担当 A ／ コース 都臨2 ／ 都賀',
    );
  });

  it('visit 選択時は予定/到着/滞在を表示', () => {
    const v = makeVisit({
      patient_name: '山田 花子',
      phase: 'done',
      alert_level: 'none',
      stay_minutes: 50,
      arrival_delay_min: 5,
      arrival: {
        kind: 'arrival',
        scanned_at: '2026-06-30T00:05:00Z',
        match_status: 'match',
        distance_m: 12,
        accuracy_m: 8,
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: '2026-06-30T00:55:00Z',
        match_status: 'match',
        is_override: false,
      },
    });
    const row = makeRow({ visits: [v] });
    render(<MonitorDetailPanel visit={v} row={row} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-detail-visit')).toBeInTheDocument();
    expect(screen.getByText('滞在時間')).toBeInTheDocument();
    expect(screen.getByText('50分')).toBeInTheDocument();
  });

  it('未訪問は即連絡ボックスを表示', () => {
    const v = makeVisit({ phase: 'missing', alert_level: 'missing', arrival: null });
    const row = makeRow({ visits: [v] });
    render(<MonitorDetailPanel visit={v} row={row} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-callbox')).toBeInTheDocument();
  });

  it('未確認は「確認済みにする」→ 理由入力 → 確定で onReview を呼ぶ', () => {
    const v = makeVisit({ phase: 'missing', alert_level: 'missing', arrival: null });
    const row = makeRow({ visits: [v] });
    const onReview = vi.fn();
    render(<MonitorDetailPanel visit={v} row={row} onSelectVisit={vi.fn()} onReview={onReview} />);
    fireEvent.click(screen.getByTestId('monitor-review-button'));
    fireEvent.change(screen.getByTestId('monitor-review-comment'), {
      target: { value: '電話で確認済み' },
    });
    fireEvent.click(screen.getByTestId('monitor-review-submit'));
    expect(onReview).toHaveBeenCalledWith(v.visit_id, '電話で確認済み');
  });

  it('確認済みは確認者/理由を表示し、取り消しで onUnreview を呼ぶ', () => {
    const v = makeVisit({
      phase: 'missing',
      alert_level: 'none',
      arrival: null,
      reviewed: true,
      reviewed_by_name: '管理 太郎',
      reviewed_at: '2026-06-30T01:00:00Z',
      review_comment: '在宅を電話確認',
    });
    const row = makeRow({ visits: [v] });
    const onUnreview = vi.fn();
    render(
      <MonitorDetailPanel
        visit={v}
        row={row}
        onSelectVisit={vi.fn()}
        onReview={vi.fn()}
        onUnreview={onUnreview}
      />,
    );
    expect(screen.getByTestId('monitor-review-done')).toBeInTheDocument();
    expect(screen.getByText('管理 太郎', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('在宅を電話確認')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('monitor-review-undo'));
    expect(onUnreview).toHaveBeenCalledWith(v.visit_id);
  });

  // --- 代行 / 予定外 (qr-open-checkin-design.md §6) ---

  it('代行 visit は「予定の担当 / 実際の訪問」を並記する (実際=代行者)', () => {
    // 代行 B のあとに担当 A が打ち直しても、「実際の訪問」は代行者 B を出す。
    const v = makeVisit({
      staff_name: '担当 A',
      actual_staff_name: '担当 A',
      substitute_staff_name: '代行 B',
      is_substitute: true,
      alert_level: 'review',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    const box = screen.getByTestId('monitor-detail-substitute');
    expect(box.textContent).toContain('予定の担当: 担当 A');
    expect(box.textContent).toContain('実際の訪問: 代行 B');
    expect(screen.queryByTestId('monitor-detail-unplanned')).toBeNull();
  });

  it('代行者名が無い応答では名前を併記しない', () => {
    const v = makeVisit({
      staff_name: '担当 A',
      actual_staff_name: '担当 A',
      substitute_staff_name: null,
      is_substitute: true,
      alert_level: 'review',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    const box = screen.getByTestId('monitor-detail-substitute');
    expect(box.textContent).toContain('予定の担当: 担当 A');
    expect(box.textContent).not.toContain('実際の訪問:');
  });

  it('予定外かつ代行なら両方のブロックを出す', () => {
    const v = makeVisit({
      staff_name: '担当 A',
      actual_staff_name: '代行 B',
      substitute_staff_name: '代行 B',
      is_substitute: true,
      is_unplanned: true,
      alert_level: 'review',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-detail-unplanned')).toBeInTheDocument();
    expect(screen.getByTestId('monitor-detail-substitute').textContent).toContain(
      '実際の訪問: 代行 B',
    );
  });

  it('予定外 visit は「予定外訪問」を明示する', () => {
    const v = makeVisit({
      patient_name: '飛込 花子',
      actual_staff_name: '実績 次郎',
      is_unplanned: true,
      alert_level: 'review',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    const box = screen.getByTestId('monitor-detail-unplanned');
    expect(box.textContent).toContain('予定外訪問');
    expect(box.textContent).toContain('実際の訪問: 実績 次郎');
  });

  it('通常 visit は代行/予定外の枠を出さない', () => {
    const v = makeVisit();
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.queryByTestId('monitor-detail-substitute')).toBeNull();
    expect(screen.queryByTestId('monitor-detail-unplanned')).toBeNull();
  });

  // --- 実績の時刻を合わせる (actual-time-adjust-design-2026-09-30 §8-1) ---

  /** 値の行 (Kv) を「見出し → 値」で引く。 */
  const kv = (label: string) => screen.getByText(label).parentElement?.textContent ?? '';

  it('到着・退出は実績時刻 (arrival_at / departure_at) で出し、読取時刻・理由・誰がいつを添える', () => {
    // 読取 13:06 (JST) → 12:56 に合わせた到着。退出は 13:31。
    const v = makeVisit({
      phase: 'done',
      stay_minutes: 35,
      arrival_delay_min: -4,
      arrival: {
        kind: 'arrival',
        scanned_at: '2026-09-18T04:06:00Z',
        match_status: 'match',
        distance_m: 12,
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: '2026-09-18T04:31:00Z',
        match_status: 'match',
        is_override: false,
      },
      arrival_at: '2026-09-18T03:56:00Z',
      arrival_read_at: '2026-09-18T04:06:00Z',
      arrival_adjusted: true,
      departure_at: '2026-09-18T04:31:00Z',
      departure_read_at: '2026-09-18T04:31:00Z',
      departure_adjusted: false,
      departure_manual: false,
      adjustments: [
        {
          kind: 'arrival',
          reason_label: 'インターホン待ち',
          reason_text: null,
          by_name: '川名 幸子',
          created_at: '2026-09-18T04:10:00Z',
        },
      ],
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(kv('到着（調整後）')).toContain('12:56');
    expect(kv('到着の読取時刻')).toContain('13:06');
    expect(kv('退出（QR/GPS）')).toContain('13:31');
    expect(screen.queryByText('退出の読取時刻')).toBeNull();

    const box = screen.getByTestId('monitor-detail-adjusted');
    expect(box.textContent).toContain('時刻の調整');
    expect(box.textContent).toContain('到着 12:56（読取 13:06）');
    expect(box.textContent).toContain('理由: インターホン待ち');
    expect(box.textContent).toContain('川名 幸子 9/18 13:10');
    // 文言ルール (PO): 「直す」「修正」「補正」は使わない。モニターは閲覧のみ。
    expect(screen.getByTestId('monitor-detail-visit').textContent).not.toMatch(/直す|修正|補正/);
    expect(screen.queryByRole('button', { name: /保存|合わせる/ })).toBeNull();
  });

  it('読み取りの無い退出 (手入力) は「手入力」と分かり、滞在中とは出さない', () => {
    const v = makeVisit({
      phase: 'done',
      arrival: {
        kind: 'arrival',
        scanned_at: '2026-09-18T04:06:00Z',
        match_status: 'match',
        is_override: false,
      },
      departure: null,
      arrival_at: '2026-09-18T04:06:00Z',
      departure_at: '2026-09-18T04:31:00Z',
      departure_read_at: null,
      departure_manual: true,
      adjustments: [{ kind: 'departure', reason_label: '読み取りなし' }],
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(kv('退出（手入力）')).toContain('13:31');
    expect(kv('到着（QR/GPS）')).toContain('13:06');
    expect(screen.getByTestId('monitor-detail-adjusted').textContent).toContain(
      '退出 13:31（手入力・読み取りなし）',
    );
  });

  it('新項目の無い応答 (古いバックエンド) は打刻の scanned_at で出し、調整の枠は出さない', () => {
    const v = makeVisit({
      phase: 'done',
      arrival: {
        kind: 'arrival',
        scanned_at: '2026-06-30T00:05:00Z',
        match_status: 'match',
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: '2026-06-30T00:55:00Z',
        match_status: 'match',
        is_override: false,
      },
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(kv('到着（QR/GPS）')).toContain('09:05');
    expect(kv('退出（QR/GPS）')).toContain('09:55');
    expect(screen.queryByTestId('monitor-detail-adjusted')).toBeNull();
  });
});
