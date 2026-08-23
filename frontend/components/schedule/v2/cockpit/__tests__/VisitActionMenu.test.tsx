/**
 * VisitActionMenu — 訪問クリックのポップオーバー (週空間 Phase E)。
 *
 * ① メニュー項目 (今週だけ取消 / 担当変更 / 時刻変更 / 曜日移動 / 型も変える…) が出る
 * ② 過去日 (JST) は「送信対象外」の警告が出る / 未来日は出ない
 * ③ 各操作が props のコールバックを正しい引数で呼ぶ (API は呼ばない)
 * ④ cancelled は「取消をやめる」に切り替わる
 * ⑤ 青ピン (week_pinned) は操作不可
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { VisitActionMenu, type VisitActionMenuVisit } from '../VisitActionMenu';

// 過去日判定は実時計基準のため、対象週は常に「来週」を使う。
const _today = new Date();
const _nextMonday = new Date(_today);
_nextMonday.setDate(_today.getDate() + ((8 - _today.getDay()) % 7 || 7));
const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const NEXT_TUE = new Date(_nextMonday);
NEXT_TUE.setDate(_nextMonday.getDate() + 1);

const STAFF_A = '00000000-0000-4000-8000-0000000000a1';
const STAFF_B = '00000000-0000-4000-8000-0000000000b2';

const BASE_VISIT: VisitActionMenuVisit = {
  id: '00000000-0000-4000-8000-000000000001',
  patient_name: '佐々木 様',
  date: iso(NEXT_TUE),
  start_time: '09:00',
  end_time: '10:00',
  course_label: '稲毛A',
  status: 'planned',
  source: 'manual_week',
  staff_id: STAFF_A,
  week_pinned: false,
  unsent: true,
};

function renderMenu(overrides: Partial<React.ComponentProps<typeof VisitActionMenu>> = {}) {
  const handlers = {
    onCancelToggle: vi.fn(),
    onChangeStaff: vi.fn(),
    onChangeTime: vi.fn(),
    onMoveWeekday: vi.fn(),
    onChangeMaster: vi.fn(),
    onChangeServiceContent: vi.fn(),
  };
  render(
    <VisitActionMenu
      visit={BASE_VISIT}
      staffOptions={[
        { id: STAFF_A, name: '川名' },
        { id: STAFF_B, name: '髙梨' },
      ]}
      isPast={false}
      defaultOpen
      {...handlers}
      {...overrides}
    >
      <button type="button">訪問カード</button>
    </VisitActionMenu>,
  );
  return handlers;
}

describe('VisitActionMenu', () => {
  it('メニュー項目が揃う', () => {
    renderMenu();
    expect(screen.getByTestId('visit-action-menu')).toBeInTheDocument();
    expect(screen.getByTestId('visit-action-cancel')).toHaveTextContent('今週だけ取消');
    expect(screen.getByLabelText('担当変更')).toBeInTheDocument();
    expect(screen.getByLabelText('時刻変更')).toBeInTheDocument();
    expect(screen.getByLabelText('曜日移動')).toBeInTheDocument();
    expect(screen.getByTestId('visit-action-master')).toHaveTextContent('型も変える');
    // 出所・同期の表示 (モックの sub 行)
    expect(screen.getByTestId('visit-action-footer')).toHaveTextContent('今週だけ');
    expect(screen.getByTestId('visit-action-footer')).toHaveTextContent('●未送信');
  });

  it('未来日は過去日警告を出さない', () => {
    renderMenu({ isPast: false });
    expect(screen.queryByTestId('visit-action-past-warn')).toBeNull();
  });

  it('過去日(JST)は送信対象外の警告を出し、取消もできない', () => {
    renderMenu({ isPast: true });
    expect(screen.getByTestId('visit-action-past-warn')).toHaveTextContent(
      '当日以前は実績の可能性があるため送信対象外',
    );
    const cancel = screen.getByTestId('visit-action-cancel');
    expect(cancel).toBeDisabled();
    expect(cancel).toHaveTextContent('当日以前は取消できません（明日以降の予定のみ）');
  });

  it('過去日でも「取消をやめる」は押せる (取消の巻き戻しは実績を壊さない)', () => {
    const h = renderMenu({ isPast: true, visit: { ...BASE_VISIT, status: 'cancelled' } });
    fireEvent.click(screen.getByTestId('visit-action-uncancel'));
    expect(h.onCancelToggle).toHaveBeenCalledWith(false);
  });

  it('現在値がプリセットされ、範囲外の時刻も選択肢に含まれる', () => {
    renderMenu({ visit: { ...BASE_VISIT, start_time: '07:20' } });
    const time = screen.getByLabelText('時刻変更') as HTMLSelectElement;
    expect(time.value).toBe('07:20');
    expect([...time.options].some((o) => o.value === '07:20')).toBe(true);
    expect((screen.getByLabelText('担当変更') as HTMLSelectElement).value).toBe(STAFF_A);
    // 火曜の訪問なので曜日は 1 が既定
    expect((screen.getByLabelText('曜日移動') as HTMLSelectElement).value).toBe('1');
  });

  // 各操作はメニューを閉じるため 1 テスト 1 操作で確認する。
  it('今週だけ取消が親へ通知される', () => {
    const h = renderMenu();
    fireEvent.click(screen.getByTestId('visit-action-cancel'));
    expect(h.onCancelToggle).toHaveBeenCalledWith(true);
  });

  it('担当変更が親へ通知される', () => {
    const h = renderMenu();
    fireEvent.change(screen.getByLabelText('担当変更'), { target: { value: STAFF_B } });
    expect(h.onChangeStaff).toHaveBeenCalledWith(STAFF_B);
  });

  it('時刻変更が親へ通知される', () => {
    const h = renderMenu();
    fireEvent.change(screen.getByLabelText('時刻変更'), { target: { value: '10:30' } });
    expect(h.onChangeTime).toHaveBeenCalledWith('10:30');
  });

  it('曜日移動が親へ通知される', () => {
    const h = renderMenu();
    fireEvent.change(screen.getByLabelText('曜日移動'), { target: { value: '3' } });
    expect(h.onMoveWeekday).toHaveBeenCalledWith(3);
  });

  it('型も変える… が親へ通知される', () => {
    const h = renderMenu();
    fireEvent.click(screen.getByTestId('visit-action-master'));
    expect(h.onChangeMaster).toHaveBeenCalled();
  });

  it('（担当なし）を選ぶと null が渡る', () => {
    const h = renderMenu();
    fireEvent.change(screen.getByLabelText('担当変更'), { target: { value: '__none__' } });
    expect(h.onChangeStaff).toHaveBeenCalledWith(null);
  });

  it('取消済みは「取消をやめる」に切り替わる', () => {
    const h = renderMenu({ visit: { ...BASE_VISIT, status: 'cancelled' } });
    expect(screen.queryByTestId('visit-action-cancel')).toBeNull();
    fireEvent.click(screen.getByTestId('visit-action-uncancel'));
    expect(h.onCancelToggle).toHaveBeenCalledWith(false);
  });

  it('青ピンは操作できない', () => {
    renderMenu({ visit: { ...BASE_VISIT, week_pinned: true } });
    expect(screen.getByTestId('visit-action-week-pinned')).toBeInTheDocument();
    expect(screen.getByTestId('visit-action-cancel')).toBeDisabled();
    expect(screen.getByLabelText('担当変更')).toBeDisabled();
  });

  // ── 🧾 カイポケのサービス内容に合わせる (mig 0078 / 設計 §2) ──

  it('サービス内容の項目は現在値を出し、押すとコールバックを呼ぶ', () => {
    const h = renderMenu({
      visit: { ...BASE_VISIT, kaipoke_service_override: '基本療養費Ⅰ・准看' },
    });
    const btn = screen.getByTestId('visit-action-service-content');
    expect(btn).toHaveTextContent('カイポケのサービス内容に合わせる');
    expect(btn).toHaveTextContent('現在: 基本療養費Ⅰ・准看');
    fireEvent.click(btn);
    expect(h.onChangeServiceContent).toHaveBeenCalled();
  });

  it('上書きが無いときは「この訪問だけ（マスタは変えない）」と出す', () => {
    renderMenu();
    expect(screen.getByTestId('visit-action-service-content')).toHaveTextContent(
      'この訪問だけ（マスタは変えない）',
    );
  });

  it('位置を変えないので青ピンでも押せる（取消と違って蓋の対象外）', () => {
    renderMenu({ visit: { ...BASE_VISIT, week_pinned: true } });
    expect(screen.getByTestId('visit-action-service-content')).toBeEnabled();
  });

  it('コールバック未指定なら項目を出さない', () => {
    renderMenu({ onChangeServiceContent: undefined });
    expect(screen.queryByTestId('visit-action-service-content')).toBeNull();
  });
});
