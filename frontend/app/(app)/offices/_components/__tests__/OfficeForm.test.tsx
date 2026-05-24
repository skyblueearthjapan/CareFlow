/**
 * OfficeForm vitest unit tests — Phase G-45 regression + new field 反映.
 *
 * 既存 OfficeForm 挙動 (= 拠点名必須 / onSubmit payload 構造) を維持しつつ、
 * 新規 `operating_weekdays` フィールドの挙動を検証:
 *
 *   1. 初期値 `operating_weekdays` が undefined のとき default [0..5] が payload に乗る.
 *   2. `initial.operating_weekdays = [0,1,2,3,4]` を渡すと反映され、 onSubmit にもそのまま乗る.
 *   3. OperatingWeekdaysField の toggle クリックで payload が変化する.
 *   4. 0 個チェック状態で submit すると validation error (= onSubmit 呼ばれない).
 *
 * 重い子コンポーネント (`useCities` / `AddressGeocodeField`) は本テストの責務外なので
 * 浅く mock する.
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

// ─── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock('@/lib/queries/cities', () => ({
  useCities: () => ({
    cities: [],
    allCities: [],
    isLoading: false,
  }),
}));

vi.mock('@/components/AddressGeocodeField', () => ({
  AddressGeocodeField: () => <div data-testid="address-geocode-stub" />,
}));

// 後 import (mock 前に import すると hoist 順で fail する).
import { OfficeForm } from '../OfficeForm';

describe('OfficeForm (Phase G-45)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses default operating_weekdays = [0..5] when initial is not provided', async () => {
    const onSubmit = vi.fn();
    render(<OfficeForm onSubmit={onSubmit} />);

    // 拠点名を入れて submit.
    fireEvent.change(screen.getByRole('textbox', { name: /拠点名/ }) as HTMLInputElement, {
      target: { value: 'g45-test-office' },
    });
    await act(async () => {
      fireEvent.submit(screen.getByRole('button', { name: '作成' }).closest('form')!);
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const arg = onSubmit.mock.calls[0]![0];
    expect(arg.name).toBe('g45-test-office');
    expect(arg.operating_weekdays).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it('respects initial.operating_weekdays and passes it through onSubmit', async () => {
    const onSubmit = vi.fn();
    render(
      <OfficeForm
        initial={{ name: 'tsuga', operating_weekdays: [0, 1, 2, 3, 4] }}
        onSubmit={onSubmit}
      />,
    );

    // 初期値 [0..4] に応じた toggle 状態を検証 (Sat=5 / Sun=6 が unchecked).
    expect(screen.getByTestId('operating-weekdays-toggle-4').getAttribute('aria-checked')).toBe(
      'true',
    );
    expect(screen.getByTestId('operating-weekdays-toggle-5').getAttribute('aria-checked')).toBe(
      'false',
    );

    await act(async () => {
      fireEvent.submit(screen.getByRole('button', { name: '作成' }).closest('form')!);
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]![0].operating_weekdays).toEqual([0, 1, 2, 3, 4]);
  });

  it('toggling a weekday updates the payload before submit', async () => {
    const onSubmit = vi.fn();
    render(
      <OfficeForm
        initial={{ name: 'mut', operating_weekdays: [0, 1, 2, 3, 4, 5] }}
        onSubmit={onSubmit}
      />,
    );

    // 土曜 (=5) を外す.
    fireEvent.click(screen.getByTestId('operating-weekdays-toggle-5'));

    await act(async () => {
      fireEvent.submit(screen.getByRole('button', { name: '作成' }).closest('form')!);
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]![0].operating_weekdays).toEqual([0, 1, 2, 3, 4]);
  });

  it('blocks submit when operating_weekdays becomes empty (= 0 days)', async () => {
    const onSubmit = vi.fn();
    render(<OfficeForm initial={{ name: 'empty', operating_weekdays: [0] }} onSubmit={onSubmit} />);

    // 唯一の 月 toggle を外して 0 個状態にする.
    fireEvent.click(screen.getByTestId('operating-weekdays-toggle-0'));

    await act(async () => {
      fireEvent.submit(screen.getByRole('button', { name: '作成' }).closest('form')!);
    });

    // validation error が表示されて onSubmit は呼ばれない.
    expect(onSubmit).not.toHaveBeenCalled();
    // 入力エラー Alert タイトルが見える.
    expect(screen.getByText('入力エラー')).toBeInTheDocument();
  });
});
