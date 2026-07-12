/**
 * AccompanimentBar (§7.1 下部固定バー) の表示・操作テスト。
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AccompanimentBar } from '../AccompanimentBar';
import type { AccompanimentBarProps } from '../useAccompanimentController';
import type { StaffRead } from '@/lib/schemas/staff';

function staff(id: string, name: string): StaffRead {
  return {
    id,
    name,
    status: 'active',
    role: 'staff',
    is_trainee: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  } as StaffRead;
}

function baseProps(over: Partial<AccompanimentBarProps> = {}): AccompanimentBarProps {
  return {
    trainees: [staff('s1', '髙梨'), staff('s2', '川名')],
    selectedTraineeId: 's1',
    onSelectTrainee: vi.fn(),
    courseCount: 1,
    visitCount: 2,
    overlapMessages: [],
    serverOverlapMessages: [],
    canConfirm: true,
    isSaving: false,
    setDefaultChecked: false,
    onToggleSetDefault: vi.fn(),
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
    isLoadingLinks: false,
    ...over,
  };
}

describe('AccompanimentBar', () => {
  it('選択数を「◯コース＋◯件選択中」で出す', () => {
    render(<AccompanimentBar {...baseProps()} />);
    expect(screen.getByTestId('accompaniment-count').textContent).toBe('1コース＋2件選択中');
  });

  it('確定ボタンは canConfirm=false のとき無効 (重複ブロック)', () => {
    render(
      <AccompanimentBar
        {...baseProps({ canConfirm: false, overlapMessages: ['⚠ 時間が重複しています: …'] })}
      />,
    );
    expect(screen.getByTestId('accompaniment-confirm')).toBeDisabled();
    expect(screen.getByTestId('accompaniment-warnings').textContent).toContain('時間が重複');
  });

  it('確定/キャンセルで各ハンドラを呼ぶ', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<AccompanimentBar {...baseProps({ onConfirm, onCancel })} />);
    fireEvent.click(screen.getByTestId('accompaniment-confirm'));
    fireEvent.click(screen.getByTestId('accompaniment-cancel'));
    expect(onConfirm).toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
  });

  it('新人セレクタの変更で onSelectTrainee を呼ぶ', () => {
    const onSelectTrainee = vi.fn();
    render(<AccompanimentBar {...baseProps({ onSelectTrainee })} />);
    fireEvent.change(screen.getByTestId('accompaniment-trainee-select'), {
      target: { value: 's2' },
    });
    expect(onSelectTrainee).toHaveBeenCalledWith('s2');
  });

  it('サーバ 422 メッセージも警告領域に統合表示 (二重防御)', () => {
    render(
      <AccompanimentBar
        {...baseProps({
          canConfirm: false,
          serverOverlapMessages: ['⚠ 時間が重複しています: 2026-07-14 …'],
        })}
      />,
    );
    expect(screen.getByTestId('accompaniment-warnings').textContent).toContain('2026-07-14');
  });
});
