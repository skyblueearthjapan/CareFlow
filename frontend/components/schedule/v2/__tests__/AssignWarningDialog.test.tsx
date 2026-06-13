/**
 * AssignWarningDialog — Phase G-89 (自動割付の事後警告ダイアログ) テスト.
 *
 * カバーするシナリオ:
 *   1. ローテ衝突 (連続 / 2個前) を正しく描画する (患者名 / コース / 担当 / Badge).
 *   2. 未割当コースを正しく描画する (コース / 対象患者 / 「確保できませんでした」).
 *   3. 両方ある場合は両セクションが出る.
 *   4. open=false なら何も描画しない (= ダイアログを出さない).
 */
import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AssignWarningDialog } from '../AssignWarningDialog';
import type {
  RotationConflictWarning,
  UnassignedCourseWarning,
} from '@/lib/queries/assign_staff_only';

function makeRotation(over: Partial<RotationConflictWarning> = {}): RotationConflictWarning {
  return {
    patient_id: '11111111-1111-1111-1111-111111111111',
    patient_name: '田中太郎',
    staff_id: '22222222-2222-2222-2222-222222222222',
    staff_name: '山田花子',
    course_id: '33333333-3333-3333-3333-333333333333',
    course_code: 'A',
    weekday: 0,
    visit_start_time: '09:00:00',
    recent_index: 0,
    is_consecutive: true,
    ...over,
  };
}

function makeUnassigned(over: Partial<UnassignedCourseWarning> = {}): UnassignedCourseWarning {
  return {
    course_id: '44444444-4444-4444-4444-444444444444',
    course_code: 'C',
    weekday: 2,
    visit_start_time: '14:30:00',
    patient_ids: ['55555555-5555-5555-5555-555555555555'],
    patient_names: ['鈴木一郎'],
    ...over,
  };
}

describe('AssignWarningDialog', () => {
  it('ローテ衝突 (連続) を描画する', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        rotationWarnings={[makeRotation()]}
        unassignedWarnings={[]}
      />,
    );
    expect(screen.getByTestId('assign-warning-rotation-section')).toBeInTheDocument();
    const item = screen.getByTestId('assign-warning-rotation-item');
    expect(item).toHaveTextContent('田中太郎');
    expect(item).toHaveTextContent('山田花子');
    expect(item).toHaveTextContent('A');
    expect(item).toHaveTextContent('月');
    expect(item).toHaveTextContent('09:00');
    // 連続 Badge
    expect(item).toHaveTextContent('連続');
    // 未割当セクションは出ない
    expect(screen.queryByTestId('assign-warning-unassigned-section')).not.toBeInTheDocument();
  });

  it('ローテ衝突 (2個前) は連続でない Badge を出す', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        rotationWarnings={[makeRotation({ recent_index: 1, is_consecutive: false })]}
        unassignedWarnings={[]}
      />,
    );
    const item = screen.getByTestId('assign-warning-rotation-item');
    expect(item).toHaveTextContent('2個前と同じ');
    expect(item).not.toHaveTextContent('連続 (前回と同じ)');
  });

  it('未割当コースを描画する', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        rotationWarnings={[]}
        unassignedWarnings={[makeUnassigned()]}
      />,
    );
    expect(screen.getByTestId('assign-warning-unassigned-section')).toBeInTheDocument();
    const item = screen.getByTestId('assign-warning-unassigned-item');
    expect(item).toHaveTextContent('C');
    expect(item).toHaveTextContent('水');
    expect(item).toHaveTextContent('14:30');
    expect(item).toHaveTextContent('鈴木一郎');
    expect(item).toHaveTextContent('担当者が確保できませんでした');
    // ローテセクションは出ない
    expect(screen.queryByTestId('assign-warning-rotation-section')).not.toBeInTheDocument();
  });

  it('ローテ衝突 + 未割当の両方を描画する', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        rotationWarnings={[makeRotation()]}
        unassignedWarnings={[makeUnassigned()]}
      />,
    );
    expect(screen.getByTestId('assign-warning-rotation-section')).toBeInTheDocument();
    expect(screen.getByTestId('assign-warning-unassigned-section')).toBeInTheDocument();
  });

  it('open=false なら描画しない (= ダイアログを出さない)', () => {
    render(
      <AssignWarningDialog
        open={false}
        onClose={() => {}}
        rotationWarnings={[makeRotation()]}
        unassignedWarnings={[makeUnassigned()]}
      />,
    );
    expect(screen.queryByTestId('assign-warning-dialog')).not.toBeInTheDocument();
  });
});
