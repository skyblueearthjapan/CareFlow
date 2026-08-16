/**
 * AccompanimentBar (§7.1 下部固定バー) の表示・操作テスト。
 * 一般化 (general-accompaniment-design.md §4): 一般スタッフも候補に出る・新人は
 * 先頭グループ+バッジ・対象の二択が切り替わる。
 */
import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AccompanimentBar } from '../AccompanimentBar';
import type { AccompanimentBarProps } from '../useAccompanimentController';
import type { StaffRead } from '@/lib/schemas/staff';

function staff(id: string, name: string, isTrainee = true): StaffRead {
  return {
    id,
    name,
    status: 'active',
    role: 'staff',
    is_trainee: isTrainee,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  } as StaffRead;
}

function baseProps(over: Partial<AccompanimentBarProps> = {}): AccompanimentBarProps {
  return {
    staffOptions: [staff('s1', '髙梨'), staff('s2', '川名')],
    selectedStaffId: 's1',
    onSelectStaff: vi.fn(),
    targetMode: 'course',
    onChangeTargetMode: vi.fn(),
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

  it('セレクタの変更で onSelectStaff を呼ぶ', () => {
    const onSelectStaff = vi.fn();
    render(<AccompanimentBar {...baseProps({ onSelectStaff })} />);
    fireEvent.change(screen.getByTestId('accompaniment-staff-select'), {
      target: { value: 's2' },
    });
    expect(onSelectStaff).toHaveBeenCalledWith('s2');
  });

  it('一般スタッフもセレクタに出る。新人は先頭グループで「新人」バッジ付き', () => {
    render(
      <AccompanimentBar
        {...baseProps({
          staffOptions: [staff('s1', '髙梨'), staff('g1', '熊澤', false)],
        })}
      />,
    );
    const select = screen.getByTestId('accompaniment-staff-select');
    // 一般スタッフが候補に出る (旧実装は is_trainee のみだった)。
    expect(within(select).getByRole('option', { name: '熊澤' })).toBeInTheDocument();
    // 新人はバッジ付き + 先頭グループ。
    expect(within(select).getByRole('option', { name: '髙梨（新人）' })).toBeInTheDocument();
    const groups = select.querySelectorAll('optgroup');
    expect(Array.from(groups).map((g) => g.getAttribute('label'))).toEqual(['新人', 'スタッフ']);
  });

  it('対象の二択を切り替えると onChangeTargetMode を呼び、押下状態が反映される', () => {
    const onChangeTargetMode = vi.fn();
    render(<AccompanimentBar {...baseProps({ onChangeTargetMode })} />);
    expect(screen.getByTestId('accompaniment-target-course')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByTestId('accompaniment-target-visit')).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    fireEvent.click(screen.getByTestId('accompaniment-target-visit'));
    expect(onChangeTargetMode).toHaveBeenCalledWith('visit');
  });

  it('ガイド文は対象の二択に追随する', () => {
    const { rerender } = render(<AccompanimentBar {...baseProps()} />);
    expect(screen.getByTestId('accompaniment-guide').textContent).toContain('コースの曜日ヘッダー');
    rerender(<AccompanimentBar {...baseProps({ targetMode: 'visit' })} />);
    expect(screen.getByTestId('accompaniment-guide').textContent).toContain('患者カード');
  });

  it('サーバ 422 メッセージも警告領域に統合表示 (二重防御)', () => {
    render(
      <AccompanimentBar
        {...baseProps({
          canConfirm: false,
          serverOverlapMessages: [
            '⚠ 8月18日(火) 10:00〜10:35 は 山田 太郎様（稲毛A・ご自身の担当）と重なるため登録できません',
          ],
        })}
      />,
    );
    const warnings = screen.getByTestId('accompaniment-warnings').textContent ?? '';
    expect(warnings).toContain('ご自身の担当');
    expect(warnings).toContain('重なるため登録できません');
  });
});
