/** タイムライン描画テスト (患者名 / 実績バーの状態色 / 行クリック)。 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { EventRead } from '@/lib/schemas/staff-events';

import { MonitorTimeline, monitorRowKey } from '../MonitorTimeline';
import { UNPLANNED_ROW_LABEL } from '../constants';
import { makeRow, makeVisit } from './fixtures';

describe('MonitorTimeline', () => {
  it('予定の患者名を描画する', () => {
    const v = makeVisit({ patient_name: '佐藤 一郎' });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getAllByText('佐藤 一郎').length).toBeGreaterThan(0);
  });

  it('到着済 mismatch の実績バーは data-status=mismatch', () => {
    const v = makeVisit({
      phase: 'done',
      alert_level: 'mismatch',
      arrival: {
        kind: 'arrival',
        scanned_at: '2026-06-30T00:05:00Z',
        match_status: 'mismatch',
        distance_m: 360,
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: '2026-06-30T00:55:00Z',
        match_status: 'mismatch',
        is_override: false,
      },
    });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const bar = screen.getByTestId(`monitor-bar-actual-${v.visit_id}`);
    expect(bar.getAttribute('data-status')).toBe('mismatch');
  });

  it('未訪問は実績バーに「未訪問」を表示する', () => {
    const v = makeVisit({ phase: 'missing', alert_level: 'missing', arrival: null });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const bar = screen.getByTestId(`monitor-bar-actual-${v.visit_id}`);
    expect(bar.getAttribute('data-status')).toBe('missing');
    expect(bar.textContent).toContain('未訪問');
  });

  it('行クリックで onSelectRow、バークリックで onSelectVisit', () => {
    const v = makeVisit();
    const row = makeRow({ staff_id: 'staff-x', visits: [v] });
    const onSelectRow = vi.fn();
    const onSelectVisit = vi.fn();
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={onSelectRow}
        onSelectVisit={onSelectVisit}
      />,
    );
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    expect(onSelectRow).toHaveBeenCalledWith('staff-x');
    fireEvent.click(screen.getByTestId(`monitor-bar-plan-${v.visit_id}`));
    expect(onSelectVisit).toHaveBeenCalledWith(v.visit_id);
  });

  it('同時刻 2 件で両方の患者名が可視・data-lane が 0/1 に振り分けられる', () => {
    const v1 = makeVisit({ patient_name: '田中 一郎', start_time: '09:00', end_time: '10:00' });
    const v2 = makeVisit({ patient_name: '鈴木 花子', start_time: '09:00', end_time: '10:00' });
    const row = makeRow({ visits: [v1, v2] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    // 両方の患者名が描画されていること。
    expect(screen.getAllByText('田中 一郎').length).toBeGreaterThan(0);
    expect(screen.getAllByText('鈴木 花子').length).toBeGreaterThan(0);
    // 予定バーの data-lane が 0/1 に分かれていること。
    const bar1 = screen.getByTestId(`monitor-bar-plan-${v1.visit_id}`);
    const bar2 = screen.getByTestId(`monitor-bar-plan-${v2.visit_id}`);
    const lanes = new Set([bar1.getAttribute('data-lane'), bar2.getAttribute('data-lane')]);
    expect(lanes).toEqual(new Set(['0', '1']));
  });

  it('重なりなし (連続) は 1 レーン → data-lane=0', () => {
    const v1 = makeVisit({ patient_name: '田中 一郎', start_time: '09:00', end_time: '10:00' });
    const v2 = makeVisit({ patient_name: '鈴木 花子', start_time: '10:00', end_time: '11:00' });
    const row = makeRow({ visits: [v1, v2] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const bar1 = screen.getByTestId(`monitor-bar-plan-${v1.visit_id}`);
    const bar2 = screen.getByTestId(`monitor-bar-plan-${v2.visit_id}`);
    expect(bar1.getAttribute('data-lane')).toBe('0');
    expect(bar2.getAttribute('data-lane')).toBe('0');
  });

  it('pair_waiting の visit は「ペア待ち」バッジを表示し、未訪問バーは出さない', () => {
    const v = makeVisit({
      phase: 'awaiting',
      alert_level: 'none',
      pair_waiting: true,
      arrival: null,
    });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const badge = screen.getByTestId(`monitor-pair-waiting-${v.visit_id}`);
    expect(badge.textContent).toContain('ペア待ち');
    // 未訪問バーは出ない (誤警告にしない)。
    expect(screen.queryByTestId(`monitor-bar-actual-${v.visit_id}`)).toBeNull();
  });

  it('pair_waiting バッジのクリックで onSelectVisit', () => {
    const v = makeVisit({ phase: 'awaiting', alert_level: 'none', pair_waiting: true });
    const row = makeRow({ visits: [v] });
    const onSelectVisit = vi.fn();
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={onSelectVisit}
      />,
    );
    fireEvent.click(screen.getByTestId(`monitor-pair-waiting-${v.visit_id}`));
    expect(onSelectVisit).toHaveBeenCalledWith(v.visit_id);
  });

  it('担当未設定の行もクリックで選択できる (rowKey=unassigned-コース)', () => {
    const v = makeVisit();
    const row = makeRow({ staff_id: null, staff_name: null, course_label: 'Aコース', visits: [v] });
    const onSelectRow = vi.fn();
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={onSelectRow}
        onSelectVisit={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    expect(onSelectRow).toHaveBeenCalledWith('unassigned-Aコース');
  });

  it('新人同行がある行は「＋◯◯（同行）」を表示する', () => {
    const v = makeVisit({ accompaniment_staff_name: '新人 一郎' });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getByText('＋新人 一郎（同行）')).toBeTruthy();
  });

  it('複数名の同行は accompaniment_staff_names を「・」連結で出す (確定#5)', () => {
    const v = makeVisit({
      accompaniment_staff_name: '新人 一郎',
      accompaniment_staff_names: ['新人 一郎', '熊澤 二郎'],
    });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getByText('＋新人 一郎・熊澤 二郎（同行）')).toBeTruthy();
  });

  it('新人同行が無い行はラベルを表示しない', () => {
    const v = makeVisit({ accompaniment_staff_name: null });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.queryByText(/（同行）/)).toBeNull();
  });

  it('担当乖離⚠と新人同行ラベルは別物として共存する', () => {
    const staffId = 'staff-course-1';
    const v = makeVisit({ accompaniment_staff_name: '新人 花子' });
    const row = makeRow({
      staff_id: 'staff-actual-2',
      staff_name: '実担当 太郎',
      staff_ids: ['staff-actual-2'],
      course_staff_id: staffId,
      course_staff_name: 'コース担当 次郎',
      course_label: 'Aコース',
      visits: [v],
    });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    // ⚠ 乖離警告
    expect(screen.getByText('⚠')).toBeTruthy();
    // ＋◯◯（同行）ラベル (別要素として共存)
    const accompanimentLabel = screen.getByTestId(`monitor-row-accompaniment-${monitorRowKey(row)}`);
    expect(accompanimentLabel.textContent).toContain('＋新人 花子（同行）');
  });

  // --- 代行 / 予定外 (qr-open-checkin-design.md §6) ---

  it('代行 visit のバーに「代行」バッジと代行者名が出る (代行B→担当A打ち直し)', () => {
    // 代行 B が打刻したあとに担当 A が打ち直したケース。actual (最新打刻者) は担当 A に
    // なるが、バッジの根拠は substitute_staff_name (= 代行した B)。
    const v = makeVisit({
      patient_name: '代行 対象',
      staff_name: '担当 A',
      actual_staff_name: '担当 A',
      substitute_staff_name: '代行 B',
      is_substitute: true,
      alert_level: 'review',
    });
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [v] })]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const badge = screen.getByTestId(`monitor-bar-substitute-${v.visit_id}`);
    expect(badge.textContent).toBe('代行');
    // 行レベル ⚠ (担当乖離) とは別物なので、⚠ は出ない。
    expect(screen.queryByText('⚠')).toBeNull();
    // ツールチップは「予定: ○○ / 代行: △△」。
    expect(badge.getAttribute('title')).toBe('予定: 担当 A / 代行: 代行 B');
    // 併記名は最新打刻者 (担当 A) ではなく代行者 (代行 B)。
    expect(screen.getByTestId(`monitor-bar-actual-staff-${v.visit_id}`).textContent).toBe(
      '→代行 B',
    );
  });

  it('代行だが代行者名が無い応答ではバッジのみ出す (名前は併記しない)', () => {
    const v = makeVisit({
      staff_name: '担当 A',
      actual_staff_name: '担当 A',
      substitute_staff_name: null,
      is_substitute: true,
      alert_level: 'review',
    });
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [v] })]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getByTestId(`monitor-bar-substitute-${v.visit_id}`)).toBeTruthy();
    expect(screen.queryByTestId(`monitor-bar-actual-staff-${v.visit_id}`)).toBeNull();
  });

  it('通常 visit には代行バッジを出さない', () => {
    const v = makeVisit({ actual_staff_name: '実績 次郎', is_substitute: false });
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [v] })]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.queryByTestId(`monitor-bar-substitute-${v.visit_id}`)).toBeNull();
    expect(screen.queryByTestId(`monitor-bar-actual-staff-${v.visit_id}`)).toBeNull();
  });

  it('予定外訪問の専用行は独自スタイルで描画され、バーに患者名+実績名が出る', () => {
    const v = makeVisit({
      patient_name: '飛込 花子',
      actual_staff_name: '実績 次郎',
      is_unplanned: true,
      alert_level: 'review',
    });
    const row = makeRow({
      course_id: null,
      course_label: UNPLANNED_ROW_LABEL,
      visits: [v],
    });
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [makeVisit()] }), row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    // 予定外行だけに印が付く (通常コース行は素のまま)。
    expect(screen.getByTestId('monitor-row-0').getAttribute('data-unplanned')).toBeNull();
    expect(screen.getByTestId('monitor-row-1').getAttribute('data-unplanned')).toBe('true');
    const label = screen.getByTestId(`monitor-row-unplanned-${monitorRowKey(row)}`);
    expect(label.textContent).toBe(UNPLANNED_ROW_LABEL);
    expect(screen.getAllByText('飛込 花子').length).toBeGreaterThan(0);
    expect(screen.getByTestId(`monitor-bar-actual-staff-${v.visit_id}`).textContent).toBe(
      '→実績 次郎',
    );
  });

  it('拠点別の予定外行は行キーが衝突しない (2 行同時選択にならない)', () => {
    // BE は予定外行を拠点ごとに 1 本作る (course_id=null・ラベル共通・掛け持ちなら
    // staff_id も null)。拠点でキーを分けないと 2 行が同じキーになる。
    const rowA = makeRow({
      course_id: null,
      staff_id: null,
      course_label: UNPLANNED_ROW_LABEL,
      office_id: '00000000-0000-0000-0000-0000000000aa',
      office_name: '稲毛',
      visits: [makeVisit({ patient_name: '稲毛 太郎', is_unplanned: true })],
    });
    const rowB = makeRow({
      course_id: null,
      staff_id: null,
      course_label: UNPLANNED_ROW_LABEL,
      office_id: '00000000-0000-0000-0000-0000000000bb',
      office_name: '花見川',
      visits: [makeVisit({ patient_name: '花見川 花子', is_unplanned: true })],
    });
    expect(monitorRowKey(rowA)).not.toBe(monitorRowKey(rowB));

    render(
      <MonitorTimeline
        rows={[rowA, rowB]}
        selectedRowKey={monitorRowKey(rowA)}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    // 選択されるのは片方だけ。
    expect(screen.getAllByText('● 選択中')).toHaveLength(1);
    expect(screen.getByTestId('monitor-row-0').getAttribute('data-unplanned')).toBe('true');
    expect(screen.getByTestId('monitor-row-1').getAttribute('data-unplanned')).toBe('true');
  });

  it('予定外行と同じスタッフのコース無し行が併存しても行キーが衝突しない', () => {
    const staffId = '00000000-0000-0000-0000-0000000000cc';
    const officeId = '00000000-0000-0000-0000-0000000000aa';
    const unplanned = makeRow({
      course_id: null,
      staff_id: staffId,
      staff_name: '田中 太郎',
      course_label: UNPLANNED_ROW_LABEL,
      office_id: officeId,
      visits: [makeVisit({ is_unplanned: true })],
    });
    const noCourse = makeRow({
      course_id: null,
      staff_id: staffId,
      staff_name: '田中 太郎',
      course_label: null,
      office_id: officeId,
      visits: [makeVisit()],
    });
    expect(monitorRowKey(unplanned)).toBe(`unplanned-${officeId}`);
    expect(monitorRowKey(noCourse)).toBe(staffId);

    render(
      <MonitorTimeline
        rows={[noCourse, unplanned]}
        selectedRowKey={monitorRowKey(noCourse)}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getAllByText('● 選択中')).toHaveLength(1);
    // 選択されたのはコース無し行のほう (予定外行は非選択のまま独自表示)。
    expect(screen.getByTestId('monitor-row-1').getAttribute('data-unplanned')).toBe('true');
    expect(
      screen.getByTestId(`monitor-row-unplanned-${monitorRowKey(unplanned)}`).textContent,
    ).toBe(UNPLANNED_ROW_LABEL);
  });

  it('行キーはラベル文字列ではなく is_unplanned フラグで決まる (m-4)', () => {
    // ラベルの文言を変えても行キーは `unplanned-{office_id}` のまま = 配色/印の
    // 判定 (isUnplannedRow) と同じ系統で動く。
    const officeId = '00000000-0000-0000-0000-0000000000aa';
    const renamed = makeRow({
      course_id: null,
      staff_id: null,
      course_label: '📌飛び込み訪問', // UNPLANNED_ROW_LABEL とは別の文言
      office_id: officeId,
      visits: [makeVisit({ is_unplanned: true })],
    });
    expect(renamed.course_label).not.toBe(UNPLANNED_ROW_LABEL);
    expect(monitorRowKey(renamed)).toBe(`unplanned-${officeId}`);

    // 逆に、ラベルだけ予定外でも中身が通常訪問なら予定外キーにはしない。
    const labelOnly = makeRow({
      course_id: null,
      staff_id: '00000000-0000-0000-0000-0000000000cc',
      course_label: UNPLANNED_ROW_LABEL,
      office_id: officeId,
      visits: [makeVisit()],
    });
    expect(monitorRowKey(labelOnly)).toBe('00000000-0000-0000-0000-0000000000cc');
  });

  it('予定外行にはイベント帯を描画しない', () => {
    const staffId = '00000000-0000-0000-0000-0000000000dd';
    const event: EventRead = {
      id: '00000000-0000-0000-0000-0000000000e1',
      staff_id: staffId,
      date: '2026-06-30',
      type: 'イベント',
      title: '朝礼',
      start_time: '09:00',
      end_time: '10:00',
      note: null,
      blocking: false,
    };
    const events = new Map<string, EventRead[]>([[staffId, [event]]]);
    const unplanned = makeRow({
      course_id: null,
      staff_id: staffId,
      course_label: UNPLANNED_ROW_LABEL,
      visits: [makeVisit({ is_unplanned: true })],
    });
    const normal = makeRow({ staff_id: staffId, visits: [makeVisit()] });

    const { rerender } = render(
      <MonitorTimeline
        rows={[unplanned]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
        eventsByStaffId={events}
      />,
    );
    expect(screen.queryByTestId('monitor-event-00000000-0000-0000-0000-0000000000e1')).toBeNull();

    // 通常行では従来どおり描画される (回帰確認)。
    rerender(
      <MonitorTimeline
        rows={[normal]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
        eventsByStaffId={events}
      />,
    );
    expect(screen.getByTestId('monitor-event-00000000-0000-0000-0000-0000000000e1')).toBeTruthy();
  });
});
