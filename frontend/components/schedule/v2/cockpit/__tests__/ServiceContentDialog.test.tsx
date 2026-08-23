/**
 * ServiceContentDialog — この訪問だけカイポケのサービス内容に合わせる (§2 / mig 0078)。
 *
 * ① 現在値を出す (未設定なら「自動判定」)
 * ② 4 択から選ぶと onSubmit(値)
 * ③ 「その他（自由入力）」で任意の文字列を送れる
 * ④ 「解除」は onSubmit(null) — 上書きが無いときは押せない
 * ⑤ 現在値と同じ選択では確定できない (無意味な API 呼び出しを作らない)
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ServiceContentDialog } from '../ServiceContentDialog';

function renderDialog(props: Partial<React.ComponentProps<typeof ServiceContentDialog>> = {}) {
  const onSubmit = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <ServiceContentDialog
      open
      onOpenChange={onOpenChange}
      patientName="佐々木"
      current={null}
      onSubmit={onSubmit}
      {...props}
    />,
  );
  return { onSubmit, onOpenChange };
}

describe('ServiceContentDialog', () => {
  it('未設定なら現在値を「自動判定」と出し、解除は押せない', () => {
    renderDialog();
    expect(screen.getByTestId('cockpit-service-content-current')).toHaveTextContent(
      '自動判定（区分 × 職員1の資格）',
    );
    expect(screen.getByTestId('cockpit-service-content-clear')).toBeDisabled();
    // 何も選んでいなければ確定もできない
    expect(screen.getByTestId('cockpit-service-content-confirm')).toBeDisabled();
  });

  it('4 択から選ぶと onSubmit にその値が渡る', () => {
    const { onSubmit } = renderDialog();
    fireEvent.change(screen.getByTestId('cockpit-service-content-select'), {
      target: { value: '基本療養費Ⅰ・准看' },
    });
    fireEvent.click(screen.getByTestId('cockpit-service-content-confirm'));
    expect(onSubmit).toHaveBeenCalledWith('基本療養費Ⅰ・准看');
  });

  it('その他（自由入力）は 2 段クリック（1 回目は注意書きだけ）', () => {
    const { onSubmit } = renderDialog();
    fireEvent.change(screen.getByTestId('cockpit-service-content-select'), {
      target: { value: '__custom__' },
    });
    fireEvent.change(screen.getByTestId('cockpit-service-content-custom'), {
      target: { value: '精神基本療養費Ⅲ・正看' },
    });

    // 1 回目: 確定させず、表記ズレの注意を出す。
    fireEvent.click(screen.getByTestId('cockpit-service-content-confirm'));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId('cockpit-service-content-custom-confirm')).toHaveTextContent(
      '選択肢にない値です',
    );
    expect(screen.getByTestId('cockpit-service-content-confirm')).toHaveTextContent(
      'この表記で確定する',
    );

    // 2 回目で確定。
    fireEvent.click(screen.getByTestId('cockpit-service-content-confirm'));
    expect(onSubmit).toHaveBeenCalledWith('精神基本療養費Ⅲ・正看');
  });

  it('自由入力を打ち直したら確認からやり直す', () => {
    const { onSubmit } = renderDialog();
    fireEvent.change(screen.getByTestId('cockpit-service-content-select'), {
      target: { value: '__custom__' },
    });
    fireEvent.change(screen.getByTestId('cockpit-service-content-custom'), {
      target: { value: '打ち間違い' },
    });
    fireEvent.click(screen.getByTestId('cockpit-service-content-confirm'));
    expect(screen.getByTestId('cockpit-service-content-custom-confirm')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('cockpit-service-content-custom'), {
      target: { value: '精神基本療養費Ⅲ・正看' },
    });
    expect(screen.queryByTestId('cockpit-service-content-custom-confirm')).toBeNull();
    fireEvent.click(screen.getByTestId('cockpit-service-content-confirm'));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('プリセットは 1 クリックで確定する（確認を挟まない）', () => {
    const { onSubmit } = renderDialog();
    fireEvent.change(screen.getByTestId('cockpit-service-content-select'), {
      target: { value: '精神基本療養費Ⅰ・准看' },
    });
    fireEvent.click(screen.getByTestId('cockpit-service-content-confirm'));
    expect(onSubmit).toHaveBeenCalledWith('精神基本療養費Ⅰ・准看');
    expect(screen.queryByTestId('cockpit-service-content-custom-confirm')).toBeNull();
  });

  it('上書き済みなら現在値を出し、解除で null を送る', () => {
    const { onSubmit } = renderDialog({ current: '基本療養費Ⅰ・准看' });
    expect(screen.getByTestId('cockpit-service-content-current')).toHaveTextContent(
      '基本療養費Ⅰ・准看',
    );
    // 現在値と同じ選択のままでは確定できない (何も変わらないため)
    expect(screen.getByTestId('cockpit-service-content-confirm')).toBeDisabled();
    fireEvent.click(screen.getByTestId('cockpit-service-content-clear'));
    expect(onSubmit).toHaveBeenCalledWith(null);
  });

  it('プリセットに無い現在値は自由入力として復元する', () => {
    renderDialog({ current: '精神基本療養費Ⅲ・正看' });
    expect(screen.getByTestId('cockpit-service-content-custom')).toHaveValue(
      '精神基本療養費Ⅲ・正看',
    );
  });
});
