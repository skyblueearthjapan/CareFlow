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

// 登録の位置のずれ (location-drift-align-2026-10-17)。合わせる / 元に戻すの mutation。
vi.mock('@/lib/queries/patientLocation', () => ({
  useAlignPatientLocation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRevertPatientLocation: () => ({ mutateAsync: vi.fn(), isPending: false }),
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

  it('遅れて届いた到着・退出は「遅れて届いた（受信日時）」を並べる', () => {
    const v = makeVisit({
      phase: 'done',
      alert_level: 'none',
      arrival_at: '2026-09-29T09:45:00Z',
      departure_at: '2026-09-29T10:30:00Z',
      arrival_late_received_at: '2026-09-29T23:30:00Z',
      departure_late_received_at: '2026-09-29T23:31:00Z',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.getByText('到着の記録')).toBeInTheDocument();
    expect(screen.getByText('遅れて届いた（9/30 8:30 受信）')).toBeInTheDocument();
    expect(screen.getByText('退出の記録')).toBeInTheDocument();
    expect(screen.getByText('遅れて届いた（9/30 8:31 受信）')).toBeInTheDocument();
  });

  it('遅れていない訪問には出さない', () => {
    const v = makeVisit({ phase: 'done', alert_level: 'none' });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.queryByText('到着の記録')).toBeNull();
    expect(screen.queryByText(/遅れて届いた/)).toBeNull();
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
          by_name: '職員A 幸子',
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
    expect(box.textContent).toContain('職員A 幸子 9/18 13:10');
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

  // 地図 (PO 決定 2026-10-17): PC は場所を地図で見る・家から家への経路。
  describe('住所と経路', () => {
    const a = () =>
      makeVisit({
        patient_name: '患者A',
        start_time: '09:00',
        end_time: '09:30',
        patient_address: '千葉市稲毛区A町1-1',
      });
    const b = () =>
      makeVisit({
        patient_name: '患者B',
        start_time: '10:00',
        end_time: '10:30',
        patient_address: '千葉市稲毛区B町2-2',
      });
    const c = () =>
      makeVisit({
        patient_name: '患者C',
        start_time: '11:00',
        end_time: '11:30',
        patient_address: '千葉市稲毛区C町3-3',
      });

    it('住所と「地図を開く」を新しいタブで出す', () => {
      const v = b();
      render(
        <MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />,
      );
      expect(screen.getByTestId('monitor-detail-address')).toHaveTextContent('千葉市稲毛区B町2-2');
      const link = screen.getByTestId('monitor-detail-map-link');
      expect(link).toHaveAttribute('target', '_blank');
      expect(new URL(link.getAttribute('href')!).searchParams.get('query')).toBe(
        '千葉市稲毛区B町2-2',
      );
    });

    it('登録の位置のずれがあれば住所の下にお知らせを出す (BE が管理者にだけ入れる)', () => {
      const v = makeVisit({
        patient_address: '千葉市稲毛区B町2-2',
        patient_lat: 35.6,
        patient_lng: 140.1,
        location_drift: {
          distance_m: 441,
          suggested_lat: 35.604,
          suggested_lng: 140.1,
          scans: 6,
          days: 3,
        },
      });
      render(
        <MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />,
      );
      const notice = screen.getByTestId('location-drift-notice');
      expect(notice).toHaveTextContent('約 440m 離れています（打刻 6 回・3 日分）');
      // 住所の行のすぐ下。
      expect(screen.getByTestId('monitor-detail-address').nextElementSibling).toBe(notice);
      expect(screen.getByTestId('location-drift-compare')).toHaveAttribute(
        'href',
        'https://www.google.com/maps/dir/?api=1&origin=35.6%2C140.1&destination=35.604%2C140.1&travelmode=driving&hl=ja',
      );
    });

    it('ずれが無ければ (職員の画面も) お知らせを出さない', () => {
      const v = makeVisit({ patient_address: '千葉市稲毛区B町2-2', location_drift: null });
      render(
        <MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />,
      );
      expect(screen.queryByTestId('location-drift-notice')).toBeNull();
    });

    it('住所が無ければ出さない', () => {
      const v = makeVisit({ patient_address: null });
      render(
        <MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />,
      );
      expect(screen.queryByTestId('monitor-detail-address')).toBeNull();
    });

    it('前の訪問から・次の訪問への経路を時刻順で出す (並びが逆でも)', () => {
      const [va, vb, vc] = [a(), b(), c()];
      render(
        <MonitorDetailPanel
          visit={vb}
          row={makeRow({ visits: [vc, va, vb] })}
          onSelectVisit={vi.fn()}
        />,
      );
      const prev = new URL(screen.getByTestId('monitor-detail-route-prev').getAttribute('href')!);
      expect(prev.searchParams.get('origin')).toBe('千葉市稲毛区A町1-1');
      expect(prev.searchParams.get('destination')).toBe('千葉市稲毛区B町2-2');
      const next = new URL(screen.getByTestId('monitor-detail-route-next').getAttribute('href')!);
      expect(next.searchParams.get('origin')).toBe('千葉市稲毛区B町2-2');
      expect(next.searchParams.get('destination')).toBe('千葉市稲毛区C町3-3');
      expect(screen.getByTestId('monitor-detail-route-prev')).toHaveTextContent('患者A さんから');
      expect(screen.getByTestId('monitor-detail-route-next')).toHaveTextContent('患者C さんへ');
    });

    it('1 日の最初の訪問は「次」だけ', () => {
      const [va, vb] = [a(), b()];
      render(
        <MonitorDetailPanel
          visit={va}
          row={makeRow({ visits: [va, vb] })}
          onSelectVisit={vi.fn()}
        />,
      );
      expect(screen.queryByTestId('monitor-detail-route-prev')).toBeNull();
      expect(screen.getByTestId('monitor-detail-route-next')).toBeInTheDocument();
    });

    it('担当なしの行は 1 人の順路ではないので経路を出さない', () => {
      const [va, vb] = [a(), b()];
      render(
        <MonitorDetailPanel
          visit={vb}
          row={makeRow({ staff_id: null, visits: [va, vb] })}
          onSelectVisit={vi.fn()}
        />,
      );
      expect(screen.queryByTestId('monitor-detail-routes')).toBeNull();
    });
  });

  describe('1 日の経路を地図で', () => {
    const v = (name: string, t: string, addr: string | null) =>
      makeVisit({ patient_name: name, start_time: t, end_time: t, patient_address: addr });

    it('順路の見出しに 1 つだけ。住所の無い方は飛ばし、続けて同じ住所はまとめる', () => {
      const row = makeRow({
        visits: [
          v('A', '09:00', 'A町1'),
          v('B', '10:00', 'B町2'),
          v('B2', '10:00', 'B町2'),
          v('X', '11:00', null),
          v('C', '13:00', 'C町3'),
        ],
      });
      render(<MonitorDetailPanel visit={null} row={row} onSelectVisit={vi.fn()} />);
      expect(screen.getAllByTestId('monitor-route-day-map')).toHaveLength(1);
      const u = new URL(screen.getByTestId('monitor-route-day-map').getAttribute('href')!);
      expect(u.searchParams.get('origin')).toBe('A町1');
      expect(u.searchParams.get('waypoints')).toBe('B町2');
      expect(u.searchParams.get('destination')).toBe('C町3');
    });

    it('担当なしの行・地点が 1 か所だけの行には出さない', () => {
      const { unmount } = render(
        <MonitorDetailPanel
          visit={null}
          row={makeRow({
            staff_id: null,
            visits: [v('A', '09:00', 'A町1'), v('B', '10:00', 'B町2')],
          })}
          onSelectVisit={vi.fn()}
        />,
      );
      expect(screen.queryByTestId('monitor-route-day-map')).toBeNull();
      unmount();
      render(
        <MonitorDetailPanel
          visit={null}
          row={makeRow({ visits: [v('A', '09:00', 'A町1')] })}
          onSelectVisit={vi.fn()}
        />,
      );
      expect(screen.queryByTestId('monitor-route-day-map')).toBeNull();
    });
  });

  // レビュー 2026-10-17: 絞り込み・住所なし・稼働中でない患者。
  describe('経路の前後 (レビュー対応)', () => {
    const v = (name: string, t: string, addr: string | null, extra: object = {}) =>
      makeVisit({
        patient_name: name,
        start_time: t,
        end_time: t,
        patient_address: addr,
        ...extra,
      });

    it('「異常のみ」で行が絞られていても、前後は 1 日ぜんぶの順番で出す', () => {
      const [a, b, c] = [v('A', '09:00', 'A町1'), v('B', '10:00', 'B町2'), v('C', '11:00', 'C町3')];
      render(
        <MonitorDetailPanel
          visit={c}
          row={makeRow({ visits: [a, c] })}
          routeVisits={[a, b, c]}
          onSelectVisit={vi.fn()}
        />,
      );
      expect(screen.getByTestId('monitor-detail-route-prev')).toHaveTextContent('B さんから');
    });

    it('1 日の経路も絞り込み前の訪問で作る', () => {
      const [a, b, c] = [v('A', '09:00', 'A町1'), v('B', '10:00', 'B町2'), v('C', '11:00', 'C町3')];
      render(
        <MonitorDetailPanel
          visit={null}
          row={makeRow({ visits: [a, c] })}
          routeVisits={[a, b, c]}
          onSelectVisit={vi.fn()}
        />,
      );
      const u = new URL(screen.getByTestId('monitor-route-day-map').getAttribute('href')!);
      expect(u.searchParams.get('waypoints')).toBe('B町2');
    });

    it('住所の無い前の訪問は飛ばして、その前の方から', () => {
      const [a, x, c] = [v('A', '09:00', 'A町1'), v('X', '10:00', null), v('C', '11:00', 'C町3')];
      render(
        <MonitorDetailPanel
          visit={c}
          row={makeRow({ visits: [a, x, c] })}
          onSelectVisit={vi.fn()}
        />,
      );
      expect(screen.getByTestId('monitor-detail-route-prev')).toHaveTextContent('A さんから');
    });

    it('稼働中でない患者 (入院中など) は経路に入れない', () => {
      const [a, h, c] = [
        v('A', '09:00', 'A町1'),
        v('H', '10:00', 'H町9', { patient_status: 'hospitalized' }),
        v('C', '11:00', 'C町3'),
      ];
      render(
        <MonitorDetailPanel
          visit={c}
          row={makeRow({ visits: [a, h, c] })}
          onSelectVisit={vi.fn()}
        />,
      );
      expect(screen.getByTestId('monitor-detail-route-prev')).toHaveTextContent('A さんから');
    });
  });
});
