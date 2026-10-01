/**
 * /settings/business (事業所の情報・mig 0089) の vitest テスト.
 *
 * 1. 保存値を入力欄に出し、ロゴの見え方を出す
 * 2. 変えた項目だけ送る (空欄は null = 未設定に戻す)
 * 3. ロゴが「/」でも https でもなければ保存できない
 * 4. スタッフは見られるが保存できない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/brand/Rakusuke', () => ({
  RakusukeTitle: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock('@/lib/queries/businessProfile', () => ({
  useBusinessProfile: vi.fn(),
  useUpdateBusinessProfile: vi.fn(),
}));

import { useSession } from 'next-auth/react';
import { useBusinessProfile, useUpdateBusinessProfile } from '@/lib/queries/businessProfile';

import BusinessSettingsPage from '../page';

const SEEDED = {
  station_name: '訪問看護ステーション よりより',
  contact_tel: '043-215-8991',
  contact_hours: '9:00〜18:00',
  contact_days: '日曜・年末年始休暇を除く',
  logo_url: '/brand/yoriyori-logo-h.svg',
};

const mutateAsync = vi.fn();

function setup(role: 'admin' | 'staff' = 'admin') {
  (useSession as Mock).mockReturnValue({
    data: { user: { role }, accessToken: 't' },
    status: 'authenticated',
  });
  (useBusinessProfile as Mock).mockReturnValue({
    data: SEEDED,
    isLoading: false,
    isError: false,
  });
  mutateAsync.mockResolvedValue(SEEDED);
  (useUpdateBusinessProfile as Mock).mockReturnValue({ mutateAsync, isPending: false });
}

describe('BusinessSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 保存値を入力欄に出し、ロゴの見え方を出す', () => {
    setup();
    render(<BusinessSettingsPage />);
    expect(screen.getByLabelText('電話番号')).toHaveValue('043-215-8991');
    expect(screen.getByLabelText('事業所名')).toHaveValue('訪問看護ステーション よりより');
    expect(screen.getByTestId('logo-preview')).toHaveAttribute('src', '/brand/yoriyori-logo-h.svg');
    // 変えていなければ保存できない。
    expect(screen.getByTestId('save')).toBeDisabled();
  });

  it('2. 変えた項目だけ送る (空欄は null)', async () => {
    setup();
    render(<BusinessSettingsPage />);
    fireEvent.change(screen.getByLabelText('電話番号'), { target: { value: ' 03-0000-0000 ' } });
    fireEvent.change(screen.getByLabelText('ロゴ画像'), { target: { value: '' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('save'));
    });
    expect(mutateAsync).toHaveBeenCalledWith({ contact_tel: '03-0000-0000', logo_url: null });
  });

  it('3. ロゴが「/」でも https でもなければ保存できない', () => {
    setup();
    render(<BusinessSettingsPage />);
    fireEvent.change(screen.getByLabelText('ロゴ画像'), {
      target: { value: 'http://example.com/a.svg' },
    });
    expect(screen.getByTestId('logo-url-warning')).toBeInTheDocument();
    expect(screen.getByTestId('save')).toBeDisabled();
  });

  it('4. スタッフは見られるが保存できない', () => {
    setup('staff');
    render(<BusinessSettingsPage />);
    expect(screen.getByLabelText('電話番号')).toBeDisabled();
    expect(screen.getByTestId('save')).toBeDisabled();
  });
});
