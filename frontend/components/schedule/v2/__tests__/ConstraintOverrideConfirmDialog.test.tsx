/**
 * ConstraintOverrideConfirmDialog テスト (patient-ng-staff-design.md §7-2).
 *
 * カバーするシナリオ:
 *   1. NG スタッフ警告の文言 (メモ付き / メモ無し)
 *   2. 性別制限警告の文言
 *   3. 複数警告 (週一括変更の集約) を 1 ダイアログにまとめて出す
 *   4. 「割り当てる」/「やめる」のコールバック
 *   5. open=false では描画しない
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ConstraintOverrideConfirmDialog } from '../ConstraintOverrideConfirmDialog';
import type { ConstraintWarning } from '@/lib/schemas/patient_ng_staff';

const ngWarning: ConstraintWarning = {
  kind: 'ng_staff',
  patient_id: 'p1',
  patient_name: '山田',
  staff_id: 's1',
  staff_name: '田中 一郎',
  note: 'ご家族からの申し出',
};

const genderWarning: ConstraintWarning = {
  kind: 'gender',
  patient_id: 'p2',
  patient_name: '佐藤',
  staff_id: 's1',
  staff_name: '田中 一郎',
  note: null,
};

describe('ConstraintOverrideConfirmDialog', () => {
  it('1. NG スタッフ警告はメモ付きで表示される', () => {
    render(
      <ConstraintOverrideConfirmDialog
        open
        warnings={[ngWarning]}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    const row = screen.getByTestId('constraint-override-warning-row');
    expect(row).toHaveAttribute('data-kind', 'ng_staff');
    expect(row).toHaveTextContent('田中 一郎さんは山田様のNGスタッフです');
    expect(row).toHaveTextContent('メモ: ご家族からの申し出');
  });

  it('2. メモ無しの NG 警告は「メモ:」を出さない', () => {
    render(
      <ConstraintOverrideConfirmDialog
        open
        warnings={[{ ...ngWarning, note: null }]}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByTestId('constraint-override-warning-row')).not.toHaveTextContent('メモ:');
  });

  it('3. 性別制限警告の文言', () => {
    render(
      <ConstraintOverrideConfirmDialog
        open
        warnings={[genderWarning]}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    const row = screen.getByTestId('constraint-override-warning-row');
    expect(row).toHaveAttribute('data-kind', 'gender');
    expect(row).toHaveTextContent('田中 一郎さんは佐藤様の性別制限に適合しません');
  });

  it('4. 複数警告を 1 ダイアログにまとめて表示する (週一括変更の集約)', () => {
    render(
      <ConstraintOverrideConfirmDialog
        open
        warnings={[ngWarning, genderWarning]}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getAllByTestId('constraint-override-warning-row')).toHaveLength(2);
  });

  it('5. 「割り当てる」で onConfirm、「やめる」で onCancel が呼ばれる', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConstraintOverrideConfirmDialog
        open
        warnings={[ngWarning]}
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByTestId('constraint-override-ok'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('constraint-override-cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('6. applying=true では両ボタンが無効', () => {
    render(
      <ConstraintOverrideConfirmDialog
        open
        applying
        warnings={[ngWarning]}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByTestId('constraint-override-ok')).toBeDisabled();
    expect(screen.getByTestId('constraint-override-cancel')).toBeDisabled();
  });

  it('6b. 文言 props 省略時は既定 (コース担当変更) の文言 — 後方互換', () => {
    render(
      <ConstraintOverrideConfirmDialog
        open
        warnings={[ngWarning]}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    const dialog = screen.getByTestId('constraint-override-confirm');
    expect(dialog).toHaveTextContent('それでも割り当てますか？');
    expect(dialog).toHaveTextContent('この担当変更は、次の制約に抵触します');
    expect(screen.getByTestId('constraint-override-ok')).toHaveTextContent('割り当てる');
  });

  it('6c. title / description / confirmLabel を差し替えられる (移動・配置などの経路)', () => {
    render(
      <ConstraintOverrideConfirmDialog
        open
        warnings={[ngWarning]}
        title="それでも移動しますか？"
        description="この移動先の担当者は、次の制約に抵触します"
        confirmLabel="移動する"
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    const dialog = screen.getByTestId('constraint-override-confirm');
    expect(dialog).toHaveTextContent('それでも移動しますか？');
    expect(dialog).toHaveTextContent('この移動先の担当者は、次の制約に抵触します');
    // 説明文の定型後半にも実行ボタン名が入る。
    expect(dialog).toHaveTextContent('「移動する」を押してください');
    expect(screen.getByTestId('constraint-override-ok')).toHaveTextContent('移動する');
    expect(dialog).not.toHaveTextContent('それでも割り当てますか？');
  });

  it('7. open=false では描画しない', () => {
    render(
      <ConstraintOverrideConfirmDialog
        open={false}
        warnings={[ngWarning]}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId('constraint-override-confirm')).not.toBeInTheDocument();
  });
});
