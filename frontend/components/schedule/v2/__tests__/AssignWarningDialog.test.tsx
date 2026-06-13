/**
 * AssignWarningDialog — Phase G-91 (確認レビューフロー / コースカード型) テスト.
 *
 * カバーするシナリオ:
 *   1. 性別カード (赤) を描画する (コード / 曜日 / 候補スタッフ / 原因患者マーク).
 *   2. 連続カード (黄) を描画する (チェックボックス).
 *   3. 性別 = 「割り付ける」 → 確認モーダル 1 回 → apply に渡る.
 *   4. 連続 = チェックで apply に渡る (追加モーダル無し).
 *   5. review_items 空なら open=false でダイアログ非表示.
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { AssignWarningDialog, type ApprovedReviewItem } from '../AssignWarningDialog';
import type { ReviewItem } from '@/lib/queries/assign_staff_only';

function makeGender(over: Partial<ReviewItem> = {}): ReviewItem {
  return {
    course_id: '33333333-3333-3333-3333-333333333333',
    office_name: '都賀拠点',
    course_code: 'A',
    weekday: 0,
    reason: 'gender',
    candidate_staff_id: '22222222-2222-2222-2222-222222222222',
    candidate_staff_name: '山田太郎',
    candidate_staff_sex: 'male',
    visits: [
      {
        patient_id: '11111111-1111-1111-1111-111111111111',
        patient_name: '女性のみ患者',
        start_time: '09:00:00',
        sex_restriction: 'female_only',
        is_cause: true,
      },
    ],
    ...over,
  };
}

function makeConsecutive(over: Partial<ReviewItem> = {}): ReviewItem {
  return {
    course_id: '44444444-4444-4444-4444-444444444444',
    office_name: '中央拠点',
    course_code: 'C',
    weekday: 2,
    reason: 'consecutive',
    candidate_staff_id: '66666666-6666-6666-6666-666666666666',
    candidate_staff_name: '鈴木花子',
    candidate_staff_sex: 'female',
    visits: [
      {
        patient_id: '55555555-5555-5555-5555-555555555555',
        patient_name: '連続患者',
        start_time: '14:30:00',
        sex_restriction: null,
        is_cause: true,
      },
    ],
    ...over,
  };
}

describe('AssignWarningDialog (Phase G-91 review flow)', () => {
  it('性別カード (赤) を描画する', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeGender()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-review-gender-section')).toBeInTheDocument();
    const card = screen.getByTestId('assign-review-card');
    expect(card).toHaveAttribute('data-reason', 'gender');
    expect(card).toHaveTextContent('A');
    expect(card).toHaveTextContent('月');
    expect(card).toHaveTextContent('山田太郎');
    expect(card).toHaveTextContent('女性のみ患者');
    // 原因 visit がマークされる.
    const visit = screen.getByTestId('assign-review-visit');
    expect(visit).toHaveAttribute('data-cause', 'true');
    // 連続セクションは出ない.
    expect(screen.queryByTestId('assign-review-consecutive-section')).not.toBeInTheDocument();
  });

  it('連続カード (黄) を描画する', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-review-consecutive-section')).toBeInTheDocument();
    const card = screen.getByTestId('assign-review-card');
    expect(card).toHaveAttribute('data-reason', 'consecutive');
    expect(card).toHaveTextContent('C');
    expect(card).toHaveTextContent('水');
    expect(card).toHaveTextContent('鈴木花子');
    expect(screen.getByTestId('assign-review-consecutive-checkbox')).toBeInTheDocument();
  });

  it('性別 = 「割り付ける」→ 確認モーダル 1 回 → apply に渡る', async () => {
    const onApply = vi.fn<(a: ApprovedReviewItem[]) => void>();
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeGender()]}
        onApply={onApply}
      />,
    );
    // 1 回目の判断: 「割り付ける」 → 確認モーダルが開く.
    fireEvent.click(screen.getByTestId('assign-review-gender-approve'));
    expect(screen.getByTestId('assign-review-gender-confirm')).toBeInTheDocument();
    // 2 回目の判断: 確認モーダルで OK.
    fireEvent.click(screen.getByTestId('assign-review-gender-confirm-ok'));
    // 「選んだ内容で割り付け」 → apply に承認カードが渡る.
    fireEvent.click(screen.getByTestId('assign-review-apply'));
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenCalledWith([
      {
        course_id: '33333333-3333-3333-3333-333333333333',
        candidate_staff_id: '22222222-2222-2222-2222-222222222222',
      },
    ]);
  });

  it('連続 = チェックで apply に渡る (追加モーダル無し)', async () => {
    const onApply = vi.fn<(a: ApprovedReviewItem[]) => void>();
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        onApply={onApply}
      />,
    );
    // チェックを入れる.
    fireEvent.click(screen.getByTestId('assign-review-consecutive-checkbox'));
    // 確認モーダルは出ない (連続は追加モーダル無し).
    expect(screen.queryByTestId('assign-review-gender-confirm')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('assign-review-apply'));
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenCalledWith([
      {
        course_id: '44444444-4444-4444-4444-444444444444',
        candidate_staff_id: '66666666-6666-6666-6666-666666666666',
      },
    ]);
  });

  it('未承認のまま apply ボタンは無効 (= 押せない)', () => {
    const onApply = vi.fn();
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeGender(), makeConsecutive()]}
        onApply={onApply}
      />,
    );
    expect(screen.getByTestId('assign-review-apply')).toBeDisabled();
  });

  it('修正B: 連続カード承認で linked partner も candidate 付きで co-select される', async () => {
    // X = 連続コース (linked に Y を持つ), Y = clean partner (review_item として出る).
    // X を承認すると、 apply リストに X と Y の両方が candidate 付きで入る
    // (= 片承認で half-assigned になるのを防ぐ).
    const xId = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
    const yId = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
    const xStaff = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
    const yStaff = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
    const x = makeConsecutive({
      course_id: xId,
      candidate_staff_id: xStaff,
      linked_course_ids: [yId],
    });
    const y = makeConsecutive({
      course_id: yId,
      candidate_staff_id: yStaff,
      candidate_staff_name: 'パートナー花子',
      linked_course_ids: [xId],
    });
    const onApply = vi.fn<(a: ApprovedReviewItem[]) => void>();
    render(<AssignWarningDialog open onClose={() => {}} reviewItems={[x, y]} onApply={onApply} />);
    // X のチェックだけ入れる (Y は承認し忘れた想定).
    const checkboxes = screen.getAllByTestId('assign-review-consecutive-checkbox');
    fireEvent.click(checkboxes[0]);
    fireEvent.click(screen.getByTestId('assign-review-apply'));
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    const applied = onApply.mock.calls[0][0];
    // X と Y の両方が candidate 付きで apply 対象に入る (順不同).
    expect(applied).toHaveLength(2);
    expect(applied).toContainEqual({ course_id: xId, candidate_staff_id: xStaff });
    expect(applied).toContainEqual({ course_id: yId, candidate_staff_id: yStaff });
  });

  it('open=false なら描画しない (= ダイアログを出さない)', () => {
    render(
      <AssignWarningDialog
        open={false}
        onClose={() => {}}
        reviewItems={[makeGender()]}
        onApply={() => {}}
      />,
    );
    expect(screen.queryByTestId('assign-warning-dialog')).not.toBeInTheDocument();
  });
});
