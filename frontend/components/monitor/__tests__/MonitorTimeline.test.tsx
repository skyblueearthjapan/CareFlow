/** タイムライン描画テスト (患者名 / 実績バーの状態色 / 行クリック)。 */
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';

import type { EventRead } from '@/lib/schemas/staff-events';

import { MonitorTimeline, monitorRowKey } from '../MonitorTimeline';
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

  it('「担当なし」行 (staff_id=null) もクリックで選べる (rowKey=unassigned)・⚠ を出す', () => {
    const v = makeVisit();
    const row = makeRow({ staff_id: null, staff_name: null, office_id: null, visits: [v] });
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
    expect(onSelectRow).toHaveBeenCalledWith('unassigned');
    expect(screen.getByTestId('monitor-row-0').textContent).toContain('担当なし');
    expect(screen.getByTestId('monitor-row-unassigned-warning')).toBeTruthy();
    // 区切り行も「担当なし」。
    expect(screen.getByTestId('monitor-office-divider-unassigned').textContent).toBe('担当なし');
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

  it('コース担当との食い違い (札の ⚠) と新人同行ラベルは別物として共存する', () => {
    const courseId = '00000000-0000-0000-0000-00000000c0c0';
    const officeId = '00000000-0000-0000-0000-00000000aaaa';
    const v = makeVisit({
      accompaniment_staff_name: '新人 花子',
      course_id: courseId,
      course_tag: '稲A',
      course_office_id: officeId,
      course_staff_mismatch: true,
    });
    const row = makeRow({
      course_tags: [
        { label: '稲A', course_id: courseId, office_id: officeId, office_name: '稲毛' },
      ],
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
        officeIds={[officeId]}
      />,
    );
    // ⚠ は行ヘッダの札に付く。カードには札を出さない (PO 2026-10-01) — 食い違いは title で読める。
    expect(screen.getByTestId(`monitor-row-tags-${monitorRowKey(row)}`).textContent).toBe('稲A⚠');
    expect(screen.queryByTestId(`monitor-bar-tag-${v.visit_id}`)).toBeNull();
    expect(screen.getByTestId(`monitor-bar-plan-${v.visit_id}`).getAttribute('title')).toContain(
      'コース: 稲A（コースの担当と違う）',
    );
    // ＋◯◯（同行）ラベル (別要素として共存)
    const accompanimentLabel = screen.getByTestId(
      `monitor-row-accompaniment-${monitorRowKey(row)}`,
    );
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

  it('予定外の訪問は本人の行に「予定外」の札つきで入り、行ヘッダにも「予定外」を出す', () => {
    const staffId = '00000000-0000-0000-0000-0000000000cc';
    const planned = makeVisit({
      patient_name: '予定 一郎',
      start_time: '09:00',
      end_time: '09:35',
    });
    const adhoc = makeVisit({
      patient_name: '飛込 花子',
      start_time: '11:00',
      end_time: '11:35',
      actual_staff_name: '実績 次郎',
      is_unplanned: true,
      alert_level: 'review',
    });
    const row = makeRow({ staff_id: staffId, staff_name: '実績 次郎', visits: [planned, adhoc] });
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
    // 行キーは職員 id (専用行のキー `unplanned-{office}` は廃止)。
    expect(monitorRowKey(row)).toBe(staffId);
    expect(screen.getAllByTestId(/^monitor-row-\d+$/)).toHaveLength(1);
    expect(screen.getByTestId(`monitor-bar-tag-${adhoc.visit_id}`).textContent).toBe('予定外');
    expect(screen.queryByTestId(`monitor-bar-tag-${planned.visit_id}`)).toBeNull();
    expect(screen.getByTestId(`monitor-row-tags-${staffId}`).textContent).toContain('予定外');
    expect(screen.getByTestId(`monitor-bar-actual-staff-${adhoc.visit_id}`).textContent).toBe(
      '→実績 次郎',
    );
    // 専用行の名残 (📌予定外訪問・data-unplanned) は出さない。
    expect(screen.queryByText(/📌/)).toBeNull();
    expect(screen.getByTestId('monitor-row-0').getAttribute('data-unplanned')).toBeNull();
  });

  it('予定外の訪問がある行にもイベント帯を描く (その人の 1 日の行なので)', () => {
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
    render(
      <MonitorTimeline
        rows={[makeRow({ staff_id: staffId, visits: [makeVisit({ is_unplanned: true })] })]}
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

// ─── 行 = 職員 (monitor-staff-rows-design-2026-09-30.md §4) ─────────────────

describe('MonitorTimeline — 行 = 職員', () => {
  const INAGE = '00000000-0000-0000-0000-00000000aaaa';
  const TSUGA = '00000000-0000-0000-0000-00000000bbbb';

  it('行ヘッダ: 職員名・所属・件数・コースの札 (初出順)', () => {
    const cD = '00000000-0000-0000-0000-0000000000d1';
    const cR = '00000000-0000-0000-0000-0000000000d2';
    const row = makeRow({
      staff_id: '00000000-0000-0000-0000-000000000501',
      staff_name: '佐々木 美咲',
      office_id: INAGE,
      office_name: '稲毛',
      course_tags: [
        { label: '稲D', course_id: cD, office_id: INAGE, office_name: '稲毛' },
        { label: '都臨2', course_id: cR, office_id: TSUGA, office_name: '都賀' },
      ],
      visits: [
        makeVisit({ course_id: cD, course_tag: '稲D', course_office_id: INAGE }),
        makeVisit({
          course_id: cR,
          course_tag: '都臨2',
          course_office_id: TSUGA,
          start_time: '17:00',
          end_time: '17:35',
        }),
      ],
    });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
        officeIds={[INAGE, TSUGA]}
      />,
    );
    const key = monitorRowKey(row);
    expect(screen.getByTestId('monitor-row-0').textContent).toContain('佐々木 美咲');
    expect(screen.getByTestId(`monitor-row-sub-${key}`).textContent).toBe('稲毛 ・ 2 件');
    const tags = screen.getByTestId(`monitor-row-tags-${key}`);
    expect(Array.from(tags.children).map((c) => c.textContent)).toEqual(['稲D', '都臨2']);
    // 札の色は拠点ごとに違う。
    const [t1, t2] = Array.from(tags.children) as HTMLElement[];
    expect(t1!.style.background).not.toBe(t2!.style.background);
  });

  it('所属の拠点が変わる所に「所属: ◯◯」の区切り行を出す', () => {
    const rows = [
      makeRow({ office_id: INAGE, office_name: '稲毛', visits: [makeVisit()] }),
      makeRow({ office_id: INAGE, office_name: '稲毛', visits: [makeVisit()] }),
      makeRow({ office_id: TSUGA, office_name: '都賀', visits: [makeVisit()] }),
    ];
    render(
      <MonitorTimeline
        rows={rows}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getByTestId(`monitor-office-divider-${INAGE}`).textContent).toBe('所属: 稲毛');
    expect(screen.getByTestId(`monitor-office-divider-${TSUGA}`).textContent).toBe('所属: 都賀');
    expect(screen.getAllByTestId(/^monitor-office-divider-/)).toHaveLength(2);
  });

  it('訪問が無くイベント・休みだけの職員の行: 「訪問なし」と休みの帯', () => {
    const row = makeRow({
      office_name: '稲毛',
      visits: [],
      day_override: { kind: 'off', start_time: null, end_time: null, reason: '有給' },
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
    const key = monitorRowKey(row);
    expect(screen.getByTestId(`monitor-row-sub-${key}`).textContent).toBe(
      '稲毛 ・ 訪問なし ・ 休み',
    );
    const band = screen.getByTestId(`monitor-offduty-${key}`);
    expect(band.textContent).toBe('休み（終日）');
    expect(band.getAttribute('title')).toBe('休み（有給）');
  });

  it('時間変更は勤務時間の外側だけをハッチ帯にする', () => {
    const row = makeRow({
      visits: [],
      day_override: { kind: 'custom_time', start_time: '10:00', end_time: '15:00', reason: null },
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
    const key = monitorRowKey(row);
    expect(screen.getAllByTestId(`monitor-offduty-${key}`)).toHaveLength(2);
    expect(screen.getByTestId(`monitor-row-sub-${key}`).textContent).toContain(
      '時間変更 10:00–15:00',
    );
  });

  it('拠点をまたぐ次の訪問の前に「→ 都賀へ」を出す', () => {
    const v1 = makeVisit({ course_office_id: INAGE, course_office_name: '稲毛' });
    const v2 = makeVisit({
      course_office_id: TSUGA,
      course_office_name: '都賀',
      start_time: '11:00',
      end_time: '11:35',
    });
    const v3 = makeVisit({
      course_office_id: TSUGA,
      course_office_name: '都賀',
      start_time: '13:00',
      end_time: '13:35',
      distance_to_next_m: null,
    });
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [v1, v2, v3] })]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getByTestId(`monitor-hop-${v1.visit_id}`).textContent).toBe('→ 都賀へ');
    expect(screen.queryByTestId(`monitor-hop-${v2.visit_id}`)).toBeNull();
  });

  it('同行・副担当の訪問は薄いカードで出し、押すと主担当側の訪問を選ぶ', () => {
    const shared = makeVisit({ patient_name: '同行 先', staff_name: '主担当 一郎' });
    const mainRow = makeRow({ staff_name: '主担当 一郎', visits: [shared] });
    const trainee = makeRow({
      staff_name: '新人 二郎',
      visits: [],
      companion_visit_ids: [shared.visit_id],
    });
    const onSelectVisit = vi.fn();
    const onSelectRow = vi.fn();
    render(
      <MonitorTimeline
        rows={[mainRow, trainee]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={onSelectRow}
        onSelectVisit={onSelectVisit}
        visitById={new Map([[shared.visit_id, shared]])}
      />,
    );
    const card = screen.getByTestId(`monitor-bar-companion-${shared.visit_id}`);
    expect(card.textContent).toContain('同行');
    expect(card.textContent).toContain('同行 先');
    expect(card.className).toContain('border-dashed');
    // 実績レールは主担当の行だけ (薄いカードには出さない)。
    expect(screen.getAllByTestId(`monitor-bar-plan-${shared.visit_id}`)).toHaveLength(1);
    fireEvent.click(card);
    expect(onSelectVisit).toHaveBeenCalledWith(shared.visit_id);
    expect(onSelectRow).not.toHaveBeenCalled();
  });

  it('上の行のパネルが閉じて下の行が開いても、押した行の画面上の位置を保つ (画面が飛ばない)', () => {
    const rows = [
      makeRow({ staff_name: '一番', visits: [makeVisit()] }),
      makeRow({ staff_name: '二番', visits: [makeVisit()] }),
      makeRow({ staff_name: '三番', visits: [makeVisit()] }),
    ];
    const keys = rows.map((r) => monitorRowKey(r));
    // 疑似レイアウト: 行の文書上の位置 (px) とパネルの高さ。パネルより後ろの行は下へずれる。
    const DOC_TOP: Record<string, number> = {
      [keys[0]!]: 1000,
      [keys[1]!]: 1066,
      [keys[2]!]: 1132,
    };
    const PANEL_H = 300;
    let scrollTop = 0;
    const rect = (top: number, h: number) =>
      ({ top, bottom: top + h, left: 0, right: 0, width: 0, height: h, x: 0, y: top }) as DOMRect;
    const spy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        if (this.dataset.testid === 'scroller') return rect(0, 2000);
        const key = this.getAttribute('data-row-key');
        if (!key) return rect(0, 0);
        let top = (DOC_TOP[key] ?? 0) - scrollTop;
        const panel = document.querySelector('[data-testid="monitor-row-panel"]');
        if (panel && panel.compareDocumentPosition(this) & Node.DOCUMENT_POSITION_FOLLOWING) {
          top += PANEL_H;
        }
        return rect(top, 66);
      });

    function Harness() {
      const [sel, setSel] = useState<string | null>(keys[0]!);
      return (
        <div
          data-testid="scroller"
          style={{ overflow: 'auto' }}
          ref={(el) => {
            if (el && !Object.getOwnPropertyDescriptor(el, 'scrollTop')) {
              Object.defineProperty(el, 'scrollTop', {
                get: () => scrollTop,
                set: (v: number) => {
                  scrollTop = v;
                },
              });
            }
          }}
        >
          <MonitorTimeline
            rows={rows}
            selectedRowKey={sel}
            selectedVisitId={null}
            nowMinutes={-1}
            onSelectRow={(k) => setSel((cur) => (cur === k ? null : k))}
            onSelectVisit={vi.fn()}
            renderRowPanel={() => <div>パネル</div>}
          />
        </div>
      );
    }
    render(<Harness />);
    const row2 = () => screen.getByTestId('monitor-row-2');
    const before = row2().getBoundingClientRect().top; // 1 行目のパネルの下 = 下へずれている
    fireEvent.click(row2());
    // 1 行目のパネルが閉じて 3 行目の下に開いた。3 行目は押した時と同じ位置に見えている。
    expect(row2().nextElementSibling).toBe(screen.getByTestId('monitor-row-panel'));
    expect(row2().getBoundingClientRect().top).toBe(before);
    spy.mockRestore();
  });

  it('選んだ行のすぐ下にだけパネルを開く (renderRowPanel)', () => {
    const rows = [
      makeRow({ staff_name: '一番', visits: [makeVisit()] }),
      makeRow({ staff_name: '二番', visits: [makeVisit()] }),
      makeRow({ staff_name: '三番', visits: [makeVisit()] }),
    ];
    const onSelectRow = vi.fn();
    render(
      <MonitorTimeline
        rows={rows}
        selectedRowKey={monitorRowKey(rows[1]!)}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={onSelectRow}
        onSelectVisit={vi.fn()}
        renderRowPanel={(r) => <div data-testid="panel-content">{r.staff_name}のパネル</div>}
      />,
    );
    const panels = screen.getAllByTestId('monitor-row-panel');
    expect(panels).toHaveLength(1);
    expect(panels[0]!.textContent).toBe('二番のパネル');
    // DOM 上で選んだ行 (2 行目) の直後にある。
    expect(screen.getByTestId('monitor-row-1').nextElementSibling).toBe(panels[0]);
    expect(screen.getByTestId('monitor-row-1').getAttribute('aria-expanded')).toBe('true');
    expect(panels[0]!.className).toContain('sticky');
    // パネルの中を押しても行の選択は動かない (行のクリック扱いにしない)。
    fireEvent.click(screen.getByTestId('panel-content'));
    expect(onSelectRow).not.toHaveBeenCalled();
    // タイムラインは select-none だが、パネルの文字は選べる。
    expect(panels[0]!.className).toContain('select-text');
    // 行とパネルを aria-controls で結ぶ (開いている行だけ)。
    expect(screen.getByTestId('monitor-row-1').getAttribute('aria-controls')).toBe(panels[0]!.id);
    expect(panels[0]!.id).not.toBe('');
    expect(screen.getByTestId('monitor-row-0').hasAttribute('aria-controls')).toBe(false);
  });

  it('訪問カードで Enter / Space を押すとカードが選ばれ、行のキー操作は動かない', () => {
    const v = makeVisit();
    const row = makeRow({ staff_id: 'staff-k', visits: [v] });
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
    const card = screen.getByTestId(`monitor-bar-plan-${v.visit_id}`);
    for (const key of ['Enter', ' ']) {
      // 既定の動作 (ボタンの click) を止めない = fireEvent が true を返す。
      expect(fireEvent.keyDown(card, { key })).toBe(true);
    }
    expect(onSelectRow).not.toHaveBeenCalled();
    // ボタンの既定の動作 (click) で訪問が選ばれる。
    fireEvent.click(card);
    expect(onSelectVisit).toHaveBeenCalledWith(v.visit_id);
    // 行そのものでの Enter / Space は行を開く。
    const rowEl = screen.getByTestId('monitor-row-0');
    expect(fireEvent.keyDown(rowEl, { key: 'Enter' })).toBe(false);
    fireEvent.keyDown(rowEl, { key: ' ' });
    expect(onSelectRow).toHaveBeenCalledTimes(2);
    expect(onSelectRow).toHaveBeenCalledWith('staff-k');
  });

  it('パネルを閉じるボタンで閉じると、開いていた行へフォーカスを戻す', () => {
    const rows = [
      makeRow({ staff_name: '一番', visits: [makeVisit()] }),
      makeRow({ staff_name: '二番', visits: [makeVisit()] }),
    ];
    const keys = rows.map((r) => monitorRowKey(r));
    function Harness() {
      const [sel, setSel] = useState<string | null>(keys[1]!);
      return (
        <MonitorTimeline
          rows={rows}
          selectedRowKey={sel}
          selectedVisitId={null}
          nowMinutes={-1}
          onSelectRow={setSel}
          onSelectVisit={vi.fn()}
          renderRowPanel={() => (
            <button type="button" data-testid="panel-close" onClick={() => setSel(null)}>
              閉じる
            </button>
          )}
        />
      );
    }
    render(<Harness />);
    const close = screen.getByTestId('panel-close');
    close.focus();
    fireEvent.click(close);
    expect(screen.queryByTestId('monitor-row-panel')).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId('monitor-row-1'));
  });
});

// ─── 非稼働患者 (患者ステータス連動 Phase 3・design 2026-09-09 §3-4) ─────────

describe('MonitorTimeline — 非稼働患者のバッジ', () => {
  function renderWith(visit: ReturnType<typeof makeVisit>, dateIso?: string | null) {
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [visit] })]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
        dateIso={dateIso}
      />,
    );
  }

  it('非稼働 (入院中) の予定にはバッジを出す', () => {
    const v = makeVisit({ phase: 'awaiting', patient_status: 'admitted' });
    renderWith(v);
    expect(screen.getByTestId(`monitor-bar-inactive-${v.visit_id}`)).toHaveTextContent('入院中');
  });

  it('稼働中 / ステータス不明にはバッジを出さない', () => {
    const active = makeVisit({ phase: 'awaiting', patient_status: 'active' });
    renderWith(active);
    expect(screen.queryByTestId(`monitor-bar-inactive-${active.visit_id}`)).not.toBeInTheDocument();
  });

  it('訪問前 / 訪問中 (future・awaiting・inprogress・missing) にはバッジを出す', () => {
    for (const phase of ['future', 'awaiting', 'inprogress', 'missing']) {
      const v = makeVisit({ phase, patient_status: 'admitted' });
      renderWith(v);
      expect(screen.getByTestId(`monitor-bar-inactive-${v.visit_id}`)).toHaveTextContent('入院中');
      cleanup();
    }
  });

  it('ステータス変更日より前の日を見ているときはバッジを出さない (PO 2026-09-10)', () => {
    const v = makeVisit({
      phase: 'awaiting',
      patient_status: 'admitted',
      patient_status_since: '2026-09-08',
    });
    // 9/7 のモニターを見ている = 実際に訪問した日なのでバッジは出さない。
    renderWith(v, '2026-09-07');
    expect(screen.queryByTestId(`monitor-bar-inactive-${v.visit_id}`)).not.toBeInTheDocument();
    cleanup();
    // 9/8 以降はバッジを出す。
    renderWith(v, '2026-09-08');
    expect(screen.getByTestId(`monitor-bar-inactive-${v.visit_id}`)).toHaveTextContent('入院中');
  });

  it('実績が確定した phase (done・no_show・cancelled) にはバッジを出さない', () => {
    for (const phase of ['done', 'no_show', 'cancelled']) {
      const v = makeVisit({ phase, patient_status: 'admitted' });
      renderWith(v);
      expect(screen.queryByTestId(`monitor-bar-inactive-${v.visit_id}`)).not.toBeInTheDocument();
      cleanup();
    }
  });
});

describe('MonitorTimeline — 打刻の実時刻の併記 (お客様要望 2026-09-18)', () => {
  function renderVisit(visit: ReturnType<typeof makeVisit>) {
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [visit] })]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
  }

  /** JST 12:56 到着 / 13:40 退出。 */
  const ARRIVAL = '2026-09-18T03:56:00Z';
  const DEPARTURE = '2026-09-18T04:40:00Z';

  it('打刻なし: 2 行目は予定だけ・ツールチップにも実績を足さない', () => {
    const v = makeVisit({ phase: 'awaiting', arrival: null, departure: null });
    renderVisit(v);
    expect(screen.queryByTestId(`monitor-bar-actual-time-${v.visit_id}`)).not.toBeInTheDocument();
    const card = screen.getByTestId(`monitor-bar-plan-${v.visit_id}`);
    expect(card.getAttribute('title')).not.toContain('打刻:');
  });

  it('到着+退出: 2 行目に「✓12:56–13:40」・ツールチップに「｜打刻: 12:56–13:40」', () => {
    const v = makeVisit({
      phase: 'done',
      arrival: {
        kind: 'arrival',
        scanned_at: ARRIVAL,
        match_status: 'match',
        distance_m: 10,
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: DEPARTURE,
        match_status: 'match',
        is_override: false,
      },
    });
    renderVisit(v);
    expect(screen.getByTestId(`monitor-bar-actual-time-${v.visit_id}`).textContent).toBe(
      '✓12:56–13:40',
    );
    // 予定はそのまま残る (バーの位置・幅と同じく書き換えない)。
    const card = screen.getByTestId(`monitor-bar-plan-${v.visit_id}`);
    expect(card.textContent).toContain('09:00–10:00');
    expect(card.getAttribute('title')).toContain('｜打刻: 12:56–13:40');
  });

  it('到着のみ: 2 行目に「▶12:56〜」・ツールチップに「｜打刻: 12:56〜」', () => {
    const v = makeVisit({
      phase: 'inprogress',
      arrival: {
        kind: 'arrival',
        scanned_at: ARRIVAL,
        match_status: 'match',
        distance_m: 10,
        is_override: false,
      },
      departure: null,
    });
    renderVisit(v);
    const timeEl = screen.getByTestId(`monitor-bar-actual-time-${v.visit_id}`);
    expect(timeEl.textContent).toBe('▶12:56〜');
    // 時刻は途中で切り詰めない (詰まるのは氏名・住所側だけ)。
    expect(timeEl.className).toContain('shrink-0');
    expect(timeEl.className).not.toContain('truncate');
    expect(screen.getByTestId(`monitor-bar-plan-${v.visit_id}`).getAttribute('title')).toContain(
      '｜打刻: 12:56〜',
    );
  });

  it('未訪問 (no_show) は到着打刻があっても併記しない', () => {
    const v = makeVisit({
      phase: 'no_show',
      arrival: {
        kind: 'arrival',
        scanned_at: ARRIVAL,
        match_status: 'match',
        distance_m: 10,
        is_override: false,
      },
      departure: null,
      no_show: {
        kind: 'no_show',
        scanned_at: DEPARTURE,
        match_status: 'match',
        is_override: false,
      },
    });
    renderVisit(v);
    expect(screen.queryByTestId(`monitor-bar-actual-time-${v.visit_id}`)).not.toBeInTheDocument();
    expect(
      screen.getByTestId(`monitor-bar-plan-${v.visit_id}`).getAttribute('title'),
    ).not.toContain('打刻:');
  });

  // ── 実績の時刻を合わせる (actual-time-adjust-design-2026-09-30 §8-1) ──
  // 画面の時刻は MonitorVisit.arrival_at / departure_at (実績時刻) で描く。
  // arrival / departure (生の打刻) の scanned_at は読まない。

  /** 読取 13:06 (JST) → 10 分さかのぼって 12:56 に合わせた到着。退出は 13:31。 */
  const READ_ARRIVAL = '2026-09-18T04:06:00Z';
  const ADJ_DEPARTURE = '2026-09-18T04:31:00Z';
  const adjustedVisit = (over: Parameters<typeof makeVisit>[0] = {}) =>
    makeVisit({
      phase: 'done',
      start_time: '13:00',
      end_time: '13:35',
      arrival: {
        kind: 'arrival',
        scanned_at: READ_ARRIVAL,
        match_status: 'match',
        distance_m: 10,
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: ADJ_DEPARTURE,
        match_status: 'match',
        is_override: false,
      },
      arrival_at: ARRIVAL,
      arrival_read_at: READ_ARRIVAL,
      arrival_adjusted: true,
      departure_at: ADJ_DEPARTURE,
      departure_read_at: ADJ_DEPARTURE,
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
      ...over,
    });

  /** タイムライン (8–19h) 上の左 % 座標。 */
  const pct = (hm: string) => {
    const [h, m] = hm.split(':').map(Number);
    return (((h ?? 0) * 60 + (m ?? 0) - 8 * 60) / (11 * 60)) * 100;
  };

  it('実績バーの位置と幅は実績時刻 (arrival_at) から描く — 読取時刻 (scanned_at) ではない', () => {
    const v = adjustedVisit();
    renderVisit(v);
    const bar = screen.getByTestId(`monitor-bar-actual-${v.visit_id}`);
    // 12:56 (合わせた後) 起点。読取の 13:06 起点ではない。
    expect(parseFloat(bar.style.left)).toBeCloseTo(pct('12:56'), 4);
    expect(parseFloat(bar.style.left)).not.toBeCloseTo(pct('13:06'), 1);
    expect(parseFloat(bar.style.width)).toBeCloseTo(pct('13:31') - pct('12:56'), 4);
  });

  it('併記とツールチップも実績時刻。「調整」の印と、読取時刻・理由・誰がいつを出す', () => {
    const v = adjustedVisit();
    renderVisit(v);
    expect(screen.getByTestId(`monitor-bar-actual-time-${v.visit_id}`).textContent).toBe(
      '✓12:56–13:31',
    );
    const badge = screen.getByTestId(`monitor-bar-adjusted-${v.visit_id}`);
    expect(badge.textContent).toBe('調整');
    const detail = '到着 12:56（読取 13:06）・インターホン待ち・川名 幸子 9/18 13:10';
    expect(badge.getAttribute('title')).toBe(detail);
    const title = screen.getByTestId(`monitor-bar-plan-${v.visit_id}`).getAttribute('title');
    expect(title).toContain('｜打刻: 12:56–13:31');
    expect(title).toContain(`｜調整: ${detail}`);
    expect(screen.getByTestId(`monitor-bar-actual-${v.visit_id}`).getAttribute('title')).toContain(
      detail,
    );
    // 文言ルール (PO): 「直す」「修正」「補正」は使わない。
    expect(title).not.toMatch(/直す|修正|補正/);
  });

  it('読み取りの無い退出 (手入力) は departure が null でも退出まで描き、「手入力」と分かる', () => {
    const v = adjustedVisit({
      arrival_at: READ_ARRIVAL,
      arrival_adjusted: false,
      departure: null,
      departure_at: ADJ_DEPARTURE,
      departure_read_at: null,
      departure_manual: true,
      adjustments: [{ kind: 'departure', reason_label: '読み取りなし', by_name: '管理 太郎' }],
    });
    renderVisit(v);
    // nowMinutes (13:30) ではなく、手で入れた退出 13:31 まで。
    const bar = screen.getByTestId(`monitor-bar-actual-${v.visit_id}`);
    expect(parseFloat(bar.style.width)).toBeCloseTo(pct('13:31') - pct('13:06'), 4);
    expect(screen.getByTestId(`monitor-bar-actual-time-${v.visit_id}`).textContent).toBe(
      '✓13:06–13:31',
    );
    const badge = screen.getByTestId(`monitor-bar-adjusted-${v.visit_id}`);
    expect(badge.textContent).toBe('手入力');
    expect(badge.getAttribute('title')).toBe(
      '退出 13:31（手入力・読み取りなし）・読み取りなし・管理 太郎',
    );
  });

  it('調整の無い訪問には「調整」の印もツールチップの調整も出さない', () => {
    const v = adjustedVisit({ arrival_at: READ_ARRIVAL, arrival_adjusted: false, adjustments: [] });
    renderVisit(v);
    expect(screen.queryByTestId(`monitor-bar-adjusted-${v.visit_id}`)).not.toBeInTheDocument();
    expect(
      screen.getByTestId(`monitor-bar-plan-${v.visit_id}`).getAttribute('title'),
    ).not.toContain('調整');
  });

  it('新項目の無い応答 (古いバックエンド) は打刻の scanned_at で描く (従来どおり)', () => {
    const v = makeVisit({
      phase: 'done',
      arrival: {
        kind: 'arrival',
        scanned_at: ARRIVAL,
        match_status: 'match',
        distance_m: 10,
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: DEPARTURE,
        match_status: 'match',
        is_override: false,
      },
    });
    expect(v.arrival_at).toBeUndefined();
    renderVisit(v);
    const bar = screen.getByTestId(`monitor-bar-actual-${v.visit_id}`);
    expect(parseFloat(bar.style.left)).toBeCloseTo(pct('12:56'), 4);
    expect(parseFloat(bar.style.width)).toBeCloseTo(pct('13:40') - pct('12:56'), 4);
    expect(screen.queryByTestId(`monitor-bar-adjusted-${v.visit_id}`)).not.toBeInTheDocument();
  });

  // サインで記録 (signature-checkin-design-2026-10-06 §5-1): 退出がサインなら「サイン」の印。
  it('退出がサインの訪問には「サイン」の印を付ける', () => {
    const signed = adjustedVisit({
      arrival_adjusted: false,
      adjustments: [],
      departure: {
        kind: 'departure',
        scanned_at: ADJ_DEPARTURE,
        match_status: 'match',
        is_override: false,
        checkin_source: 'signature',
        signature_id: 'sig-1',
      },
    });
    renderVisit(signed);
    expect(screen.getByTestId(`monitor-bar-signature-${signed.visit_id}`).textContent).toBe(
      'サイン',
    );
  });

  it('QR の退出には「サイン」の印を付けない', () => {
    const v = adjustedVisit();
    renderVisit(v);
    expect(screen.queryByTestId(`monitor-bar-signature-${v.visit_id}`)).not.toBeInTheDocument();
  });
});
