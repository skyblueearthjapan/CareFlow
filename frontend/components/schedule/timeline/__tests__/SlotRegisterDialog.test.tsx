import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  SlotRegisterDialog,
  slotStartOptions,
  type SlotPatientOption,
  type SlotRegisterContext,
} from '@/components/schedule/timeline/SlotRegisterDialog';

const ctx = (over: Partial<SlotRegisterContext> = {}): SlotRegisterContext => ({
  courseLabel: '稲毛A',
  staffName: '田中 一郎',
  weekdayLabel: '月',
  gapStartMin: 13 * 60,
  gapEndMin: 15 * 60,
  canRegisterEvent: true,
  ...over,
});

const patients: SlotPatientOption[] = [
  { id: 'p1', name: '青柳 あい', defaultDurationMin: 45, shortage: 2 },
  { id: 'p2', name: '佐藤 二郎', defaultDurationMin: 60, shortage: 1 },
];

function renderDialog(over: Partial<Parameters<typeof SlotRegisterDialog>[0]> = {}) {
  const onRegisterVisit = vi.fn();
  const onSwitchToEvent = vi.fn();
  const onClose = vi.fn();
  render(
    <SlotRegisterDialog
      open
      context={ctx()}
      patients={patients}
      onRegisterVisit={onRegisterVisit}
      onSwitchToEvent={onSwitchToEvent}
      onClose={onClose}
      {...over}
    />,
  );
  return { onRegisterVisit, onSwitchToEvent, onClose };
}

describe('slotStartOptions', () => {
  it('gap 内で duration が収まる 15分刻みの開始候補を返す', () => {
    // 13:00〜15:00 に 60分 → 13:00..14:00 の 5 候補。
    expect(slotStartOptions(780, 900, 60)).toEqual([780, 795, 810, 825, 840]);
  });

  it('duration が gap を超えるときは空', () => {
    expect(slotStartOptions(780, 840, 90)).toEqual([]);
  });
});

describe('SlotRegisterDialog', () => {
  it('文脈 (コース/担当/曜日/空き時間帯) を表示する', () => {
    renderDialog();
    expect(screen.getByText('稲毛A')).toBeInTheDocument();
    expect(screen.getByText(/田中 一郎/)).toBeInTheDocument();
    expect(screen.getByText(/13:00〜15:00/)).toBeInTheDocument();
  });

  it('患者選択で時間が患者既定値になり、確定で onRegisterVisit を呼ぶ', () => {
    const { onRegisterVisit } = renderDialog();
    fireEvent.change(screen.getByTestId('slot-register-patient-select'), {
      target: { value: 'p1' },
    });
    // p1 の既定 45 分が選ばれている。
    expect((screen.getByTestId('slot-register-duration-select') as HTMLSelectElement).value).toBe(
      '45',
    );
    fireEvent.click(screen.getByTestId('slot-register-confirm'));
    expect(onRegisterVisit).toHaveBeenCalledWith({
      patientId: 'p1',
      startHM: '13:00',
      durationMin: 45,
    });
  });

  it('開始時刻は 15分刻みで選べる (gap 起点)', () => {
    renderDialog();
    fireEvent.change(screen.getByTestId('slot-register-patient-select'), {
      target: { value: 'p2' },
    });
    const start = screen.getByTestId('slot-register-start-select') as HTMLSelectElement;
    const values = Array.from(start.options).map((o) => o.textContent);
    // 60分 in 13:00〜15:00 → 13:00..14:00。
    expect(values).toEqual(['13:00', '13:15', '13:30', '13:45', '14:00']);
    fireEvent.change(start, { target: { value: String(13 * 60 + 30) } });
    expect(start.value).toBe(String(13 * 60 + 30));
  });

  it('患者未選択のとき確定は無効', () => {
    renderDialog();
    expect(screen.getByTestId('slot-register-confirm')).toBeDisabled();
  });

  it('候補患者ゼロのときは案内を出す', () => {
    renderDialog({ patients: [] });
    expect(screen.getByTestId('slot-register-no-patients')).toBeInTheDocument();
    expect(screen.getByTestId('slot-register-confirm')).toBeDisabled();
  });

  it('会議・イベント切替ボタンは onSwitchToEvent を呼び、カイポケ反映外を明示する', () => {
    const { onSwitchToEvent } = renderDialog();
    expect(screen.getByText('カイポケ反映外')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('slot-register-switch-event'));
    expect(onSwitchToEvent).toHaveBeenCalledTimes(1);
  });

  it('担当未割当 (canRegisterEvent=false) のときイベント切替は無効', () => {
    renderDialog({ context: ctx({ staffName: null, canRegisterEvent: false }) });
    expect(screen.getByTestId('slot-register-switch-event')).toBeDisabled();
  });

  it('busy 中は確定/キャンセルとも無効 (患者選択済みでも busy だけで塞がる)', () => {
    // 患者選択済みで確定が有効 → busy=true に切り替わると無効化される
    // (= canConfirm の !busy ガード自体の検証)。
    const props = {
      open: true,
      context: ctx(),
      patients,
      onRegisterVisit: vi.fn(),
      onSwitchToEvent: vi.fn(),
      onClose: vi.fn(),
    };
    const { rerender } = render(<SlotRegisterDialog {...props} busy={false} />);
    fireEvent.change(screen.getByTestId('slot-register-patient-select'), {
      target: { value: 'p1' },
    });
    expect(screen.getByTestId('slot-register-confirm')).toBeEnabled();
    rerender(<SlotRegisterDialog {...props} busy />);
    expect(screen.getByTestId('slot-register-confirm')).toBeDisabled();
    expect(screen.getByTestId('slot-register-cancel')).toBeDisabled();
  });
});
