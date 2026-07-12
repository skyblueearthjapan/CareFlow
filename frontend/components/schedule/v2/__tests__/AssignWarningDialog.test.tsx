/**
 * AssignWarningDialog — Phase G-91 (確認レビューフロー / コースカード型) テスト.
 *
 * カバーするシナリオ:
 *   1. 性別カード (赤) を描画する (コード / 曜日 / 候補スタッフ / 原因患者マーク).
 *   2. 連続カード (黄) を描画する (チェックボックス).
 *   3. 性別 = 「割り当てる」 → 確認モーダル 1 回 → apply に渡る.
 *   4. 連続 = チェックで apply に渡る (追加モーダル無し).
 *   5. review_items 空なら open=false でダイアログ非表示.
 *   6. Wave N-2: notices を渡すと折りたたみセクションが出る.
 *   7. Wave N-2: notices のみ (reviewItems=[]) でも open 時に描画され、情報トーンの説明が出る.
 *   8. Wave N-2: notices があっても apply ボタンの disabled 判定に影響しない.
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { AssignWarningDialog, type ApprovedReviewItem } from '../AssignWarningDialog';
import type {
  AutoCommittedNotice,
  ReviewItem,
  StageAssignmentNotice,
  UnresolvedGenderWarning,
} from '@/lib/queries/assign_staff_only';

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

  it('性別 = 「割り当てる」→ 確認モーダル 1 回 → apply に渡る', async () => {
    const onApply = vi.fn<(a: ApprovedReviewItem[]) => void>();
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeGender()]}
        onApply={onApply}
      />,
    );
    // 1 回目の判断: 「割り当てる」 → 確認モーダルが開く.
    fireEvent.click(screen.getByTestId('assign-review-gender-approve'));
    expect(screen.getByTestId('assign-review-gender-confirm')).toBeInTheDocument();
    // 2 回目の判断: 確認モーダルで OK.
    fireEvent.click(screen.getByTestId('assign-review-gender-confirm-ok'));
    // 「選んだ内容で割り当て」 → apply に承認カードが渡る.
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

  it('一斉承認 = 連続カードを全件まとめて承認して apply に渡る', async () => {
    const c1 = makeConsecutive();
    const c2 = makeConsecutive({
      course_id: '77777777-7777-7777-7777-777777777777',
      candidate_staff_id: '88888888-8888-8888-8888-888888888888',
      candidate_staff_name: '佐藤一郎',
    });
    const onApply = vi.fn<(a: ApprovedReviewItem[]) => void>();
    render(
      <AssignWarningDialog open onClose={() => {}} reviewItems={[c1, c2]} onApply={onApply} />,
    );
    fireEvent.click(screen.getByTestId('assign-review-consecutive-approve-all'));
    fireEvent.click(screen.getByTestId('assign-review-apply'));
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    const applied = onApply.mock.calls[0][0];
    expect(applied).toHaveLength(2);
    expect(applied).toContainEqual({
      course_id: c1.course_id,
      candidate_staff_id: c1.candidate_staff_id,
    });
    expect(applied).toContainEqual({
      course_id: c2.course_id,
      candidate_staff_id: c2.candidate_staff_id,
    });
  });

  it('一斉承認は 🔴 性別カードを対象にしない (連続のみ)', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeGender(), makeConsecutive()]}
        onApply={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId('assign-review-consecutive-approve-all'));
    // 連続カードのみ承認され、性別カードは未承認のまま.
    const cards = screen.getAllByTestId('assign-review-card');
    const gender = cards.find((c) => c.getAttribute('data-reason') === 'gender');
    const consecutive = cards.find((c) => c.getAttribute('data-reason') === 'consecutive');
    expect(gender).toHaveAttribute('data-approved', 'false');
    expect(consecutive).toHaveAttribute('data-approved', 'true');
  });

  it('全件承認済みのとき一斉承認ボタンはトグルで全件解除する', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        onApply={() => {}}
      />,
    );
    const btn = screen.getByTestId('assign-review-consecutive-approve-all');
    expect(btn).toHaveTextContent('一斉承認');
    fireEvent.click(btn);
    expect(btn).toHaveTextContent('一斉承認を解除');
    fireEvent.click(btn);
    expect(screen.getByTestId('assign-review-card')).toHaveAttribute('data-approved', 'false');
    // 全解除で apply は再び無効化される.
    expect(screen.getByTestId('assign-review-apply')).toBeDisabled();
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

// ─────────────────────────────────────────────────────────────────────────
// Wave N-2: auto_committed_notices セクションのテスト
// ─────────────────────────────────────────────────────────────────────────

function makeNotice(over: Partial<AutoCommittedNotice> = {}): AutoCommittedNotice {
  return {
    course_id: 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
    course_code: 'A',
    weekday: 0,
    office_name: '都賀拠点',
    staff_name: '田中スタッフ',
    cause_patient_names: ['患者A', '患者B'],
    reason_kind: 'single_staff',
    reason_text: 'この曜日に都賀拠点で勤務できるスタッフが田中スタッフ 1 名のため、連続担当は避けられません',
    ...over,
  };
}

describe('AssignWarningDialog — Wave N-2 notices セクション', () => {
  it('notices を渡すとお知らせセクションが描画される (既定は折りたたみ = 行非表示)', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        notices={[makeNotice()]}
        onApply={() => {}}
      />,
    );
    // セクション自体は表示される
    expect(screen.getByTestId('assign-notice-section')).toBeInTheDocument();
    // 既定は折りたたみ状態 = 行は見えない
    expect(screen.queryByTestId('assign-notice-row')).not.toBeInTheDocument();
  });

  it('「理由を見る ▼」を押すと行が表示される', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        notices={[makeNotice()]}
        onApply={() => {}}
      />,
    );
    // トグルボタンをクリックして展開
    fireEvent.click(screen.getByText('理由を見る ▼'));
    // 行が表示される
    const row = screen.getByTestId('assign-notice-row');
    expect(row).toBeInTheDocument();
    expect(row).toHaveTextContent('都賀拠点');
    expect(row).toHaveTextContent('A');
    expect(row).toHaveTextContent('月');
    expect(row).toHaveTextContent('田中スタッフ');
    expect(row).toHaveTextContent('患者A・患者B');
  });

  it('notices のみ (reviewItems=[]) でも open 時に描画され、実態を反映した説明が出る', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        notices={[makeNotice()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-warning-dialog')).toBeInTheDocument();
    // W-11: 「管理者の判断が必要なコースはありません」で誤誘導しない.
    expect(screen.queryByText(/管理者の判断が必要なコースはありません/)).not.toBeInTheDocument();
    // 確定済みお知らせがある実態を反映する説明文.
    expect(screen.getByText(/体制上避けられない連続が.*件あり.*確定済み/)).toBeInTheDocument();
  });

  it('notices があっても apply ボタンは reviewItems 承認数のみで disabled 判定される', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        notices={[makeNotice()]}
        onApply={() => {}}
      />,
    );
    // reviewItems=[] なので approvedCount=0 → apply は disabled のまま
    expect(screen.getByTestId('assign-review-apply')).toBeDisabled();
  });

  it('notices + reviewItems 両方ある場合は両セクションが並存する', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        notices={[makeNotice()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-review-consecutive-section')).toBeInTheDocument();
    expect(screen.getByTestId('assign-notice-section')).toBeInTheDocument();
  });

  it('notices なし (デフォルト) ではお知らせセクションが出ない', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        onApply={() => {}}
      />,
    );
    expect(screen.queryByTestId('assign-notice-section')).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// W-11: unresolved_warnings (性別候補ゼロの残留違反) セクションのテスト
// ─────────────────────────────────────────────────────────────────────────

function makeUnresolved(
  over: Partial<UnresolvedGenderWarning> = {},
): UnresolvedGenderWarning {
  return {
    course_id: 'ffffffff-ffff-ffff-ffff-ffffffffffff',
    course_code: 'B',
    weekday: 3,
    office_name: '稲毛拠点',
    current_staff_name: '違反 太郎',
    reason_text:
      '性別制約を満たす候補が見つかりません。現在の担当（違反 太郎）は性別制約を満たしていません — 手動で調整してください',
    ...over,
  };
}

describe('AssignWarningDialog — W-11 unresolved_warnings セクション', () => {
  it('unresolvedWarnings を渡すと残留違反セクションが常時表示される (折りたたみ無し)', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        unresolvedWarnings={[makeUnresolved()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-unresolved-section')).toBeInTheDocument();
    const row = screen.getByTestId('assign-unresolved-row');
    expect(row).toBeInTheDocument();
    expect(row).toHaveTextContent('稲毛拠点');
    expect(row).toHaveTextContent('B');
    expect(row).toHaveTextContent('木');
    expect(row).toHaveTextContent('違反 太郎');
    expect(row).toHaveTextContent('手動で調整してください');
  });

  it('残留違反は承認対象外 (apply ボタンは reviewItems=0 で disabled のまま)', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        unresolvedWarnings={[makeUnresolved()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-review-apply')).toBeDisabled();
  });

  it('残留違反のみ (reviewItems=[]・notices=[]) でも説明文が実態を反映し誤誘導しない', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        unresolvedWarnings={[makeUnresolved()]}
        onApply={() => {}}
      />,
    );
    expect(screen.queryByText(/管理者の判断が必要なコースはありません/)).not.toBeInTheDocument();
    expect(screen.queryByText(/レビュー対象はありません/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/性別制約を満たすスタッフが見つからない残留が.*件あります/),
    ).toBeInTheDocument();
  });

  it('残留違反なし (デフォルト) では残留違反セクションが出ない', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        onApply={() => {}}
      />,
    );
    expect(screen.queryByTestId('assign-unresolved-section')).not.toBeInTheDocument();
  });

  it('notices + 残留違反 + reviewItems が並存できる', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        notices={[makeNotice()]}
        unresolvedWarnings={[makeUnresolved()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-review-consecutive-section')).toBeInTheDocument();
    expect(screen.getByTestId('assign-notice-section')).toBeInTheDocument();
    expect(screen.getByTestId('assign-unresolved-section')).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 4段ソルバ Stage 2/3: manager_mobilized_notices / rotation_relaxed_notices セクションのテスト
// ─────────────────────────────────────────────────────────────────────────

function makeMobilized(over: Partial<StageAssignmentNotice> = {}): StageAssignmentNotice {
  return {
    course_id: 'a1111111-1111-1111-1111-111111111111',
    weekday: 0,
    course_code: 'B',
    staff_id: 'a2222222-2222-2222-2222-222222222222',
    staff_name: '熊澤妙子',
    ...over,
  };
}

function makeRelaxed(over: Partial<StageAssignmentNotice> = {}): StageAssignmentNotice {
  return {
    course_id: 'b1111111-1111-1111-1111-111111111111',
    weekday: 0,
    course_code: 'A',
    staff_id: 'b2222222-2222-2222-2222-222222222222',
    staff_name: '宇田川優莉',
    ...over,
  };
}

describe('AssignWarningDialog — 4段ソルバ Stage 2/3 notices セクション', () => {
  it('managerMobilizedNotices を渡すとセクションが描画される (既定は折りたたみ = 行非表示)', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        managerMobilizedNotices={[makeMobilized()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-manager-mobilized-section')).toBeInTheDocument();
    // 既定は折りたたみ = 行は非表示
    expect(screen.queryByTestId('assign-manager-mobilized-row')).not.toBeInTheDocument();
    // 説明文が出る
    expect(screen.getByText(/スタッフ不足のため、以下のコースにマネージャーを割り当てました/)).toBeInTheDocument();
  });

  it('「詳細を見る ▼」を押すとマネージャー動員の行が表示される', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        managerMobilizedNotices={[makeMobilized()]}
        onApply={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('詳細を見る ▼'));
    const row = screen.getByTestId('assign-manager-mobilized-row');
    expect(row).toBeInTheDocument();
    expect(row).toHaveTextContent('月');
    expect(row).toHaveTextContent('B');
    expect(row).toHaveTextContent('熊澤妙子');
  });

  it('rotationRelaxedNotices を渡すとセクションが描画される (既定は折りたたみ = 行非表示)', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        rotationRelaxedNotices={[makeRelaxed()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-rotation-relaxed-section')).toBeInTheDocument();
    expect(screen.queryByTestId('assign-rotation-relaxed-row')).not.toBeInTheDocument();
    expect(screen.getByText(/候補がいないため、前週と同じコースを許容して割り当てました/)).toBeInTheDocument();
  });

  it('「詳細を見る ▼」を押すと前週同コースの行が表示される', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        rotationRelaxedNotices={[makeRelaxed()]}
        onApply={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('詳細を見る ▼'));
    const row = screen.getByTestId('assign-rotation-relaxed-row');
    expect(row).toBeInTheDocument();
    expect(row).toHaveTextContent('月');
    expect(row).toHaveTextContent('A');
    expect(row).toHaveTextContent('宇田川優莉');
  });

  it('0件のセクションは描画しない', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        onApply={() => {}}
      />,
    );
    expect(screen.queryByTestId('assign-manager-mobilized-section')).not.toBeInTheDocument();
    expect(screen.queryByTestId('assign-rotation-relaxed-section')).not.toBeInTheDocument();
  });

  it('managerMobilizedNotices のみ (reviewItems=[]) でも open 時に描画され説明文が出る', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        managerMobilizedNotices={[makeMobilized()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-warning-dialog')).toBeInTheDocument();
    expect(screen.getByText(/マネージャー動員が.*件あり、確定済みです/)).toBeInTheDocument();
  });

  it('rotationRelaxedNotices のみ (reviewItems=[]) でも open 時に描画され説明文が出る', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        rotationRelaxedNotices={[makeRelaxed()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-warning-dialog')).toBeInTheDocument();
    expect(screen.getByText(/前週と同じコースへの割り当てが.*件あり、確定済みです/)).toBeInTheDocument();
  });

  it('新 Stage 通知があっても apply ボタンは reviewItems 承認数のみで disabled 判定される', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        managerMobilizedNotices={[makeMobilized()]}
        rotationRelaxedNotices={[makeRelaxed()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-review-apply')).toBeDisabled();
  });

  it('§4.1 チップ: auto_committed_notices と manager_mobilized が同一 course_id のとき「👔マネージャー動員」チップが出る', () => {
    // 同一 course_id で notice + mobilized が重複するシナリオ
    const sharedCourseId = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
    const notice = makeNotice({ course_id: sharedCourseId });
    const mobilized = makeMobilized({ course_id: sharedCourseId });
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        notices={[notice]}
        managerMobilizedNotices={[mobilized]}
        onApply={() => {}}
      />,
    );
    // noticesセクションを展開
    fireEvent.click(screen.getByText('理由を見る ▼'));
    expect(screen.getByTestId('chip-manager-mobilized')).toBeInTheDocument();
    expect(screen.getByTestId('chip-manager-mobilized')).toHaveTextContent('👔マネージャー動員');
  });

  it('§4.1 チップ: auto_committed_notices と rotation_relaxed が同一 course_id のとき「🔁前週同コース」チップが出る', () => {
    const sharedCourseId = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
    const notice = makeNotice({ course_id: sharedCourseId });
    const relaxed = makeRelaxed({ course_id: sharedCourseId });
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        notices={[notice]}
        rotationRelaxedNotices={[relaxed]}
        onApply={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('理由を見る ▼'));
    expect(screen.getByTestId('chip-rotation-relaxed')).toBeInTheDocument();
    expect(screen.getByTestId('chip-rotation-relaxed')).toHaveTextContent('🔁前週同コース');
  });

  it('§4.1 チップ: course_id が異なる場合はチップが出ない', () => {
    const notice = makeNotice({ course_id: 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee' });
    const mobilized = makeMobilized({ course_id: 'a1111111-1111-1111-1111-111111111111' });
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[]}
        notices={[notice]}
        managerMobilizedNotices={[mobilized]}
        onApply={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('理由を見る ▼'));
    expect(screen.queryByTestId('chip-manager-mobilized')).not.toBeInTheDocument();
  });

  it('全セクション (review + notices + unresolved + mobilized + relaxed) が並存できる', () => {
    render(
      <AssignWarningDialog
        open
        onClose={() => {}}
        reviewItems={[makeConsecutive()]}
        notices={[makeNotice()]}
        unresolvedWarnings={[makeUnresolved()]}
        managerMobilizedNotices={[makeMobilized()]}
        rotationRelaxedNotices={[makeRelaxed()]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByTestId('assign-review-consecutive-section')).toBeInTheDocument();
    expect(screen.getByTestId('assign-notice-section')).toBeInTheDocument();
    expect(screen.getByTestId('assign-unresolved-section')).toBeInTheDocument();
    expect(screen.getByTestId('assign-manager-mobilized-section')).toBeInTheDocument();
    expect(screen.getByTestId('assign-rotation-relaxed-section')).toBeInTheDocument();
  });
});
