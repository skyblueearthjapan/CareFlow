/**
 * /settings/scheduling ページテスト — Phase G-88 Step4。
 *
 * カバー:
 *   1. 表示: 3 グループ + 各項目 + ライブ表示 (1kmあたり◯分 / 短縮注記)
 *   2. スライダー変更で保存ボタンが活性化し、変更フィールドのみ payload に載る
 *   3. 「すべて既定に戻す」で全項目 null の payload を PUT する
 *   4. staff ロールは /dashboard へリダイレクト (編集不可)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));

const replaceMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

vi.mock('@/lib/queries/schedulingSettings', () => ({
  useSchedulingSettings: vi.fn(),
  useUpdateSchedulingSettings: vi.fn(),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { useSession } from 'next-auth/react';
import {
  useSchedulingSettings,
  useUpdateSchedulingSettings,
} from '@/lib/queries/schedulingSettings';
import { toast } from '@/components/ui/sonner';

import SchedulingSettingsPage from '../page';

const VALUES = {
  visit_buffer_min: 8,
  travel_speed_kmh: 20,
  lunch_duration_min: 60,
  lunch_window_start: '11:30:00',
  lunch_window_end: '13:30:00',
  business_start: '09:30:00',
  business_end: '18:00:00',
  max_patients_per_course: 6,
};

const IS_DEFAULT = {
  visit_buffer_min: true,
  travel_speed_kmh: true,
  lunch_duration_min: true,
  lunch_window_start: true,
  lunch_window_end: true,
  business_start: true,
  business_end: true,
  max_patients_per_course: true,
};

/** 全項目が「ユーザー設定済 (= 既定でない)」のフラグ群。reset が実 PUT になる。 */
const NONE_DEFAULT = {
  visit_buffer_min: false,
  travel_speed_kmh: false,
  lunch_duration_min: false,
  lunch_window_start: false,
  lunch_window_end: false,
  business_start: false,
  business_end: false,
  max_patients_per_course: false,
};

interface SetupOpts {
  role?: 'admin' | 'manager' | 'staff';
  mutateAsync?: Mock;
  isPending?: boolean;
  isDefault?: typeof IS_DEFAULT;
}

function setup(opts: SetupOpts = {}) {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: opts.role ?? 'admin' } },
    status: 'authenticated',
  });
  (useSchedulingSettings as Mock).mockReturnValue({
    data: { values: VALUES, is_default: opts.isDefault ?? IS_DEFAULT },
    isLoading: false,
    isError: false,
  });
  (useUpdateSchedulingSettings as Mock).mockReturnValue({
    mutateAsync: opts.mutateAsync ?? vi.fn().mockResolvedValue(undefined),
    isPending: opts.isPending ?? false,
  });
}

describe('SchedulingSettingsPage (Phase G-88 Step4)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('1. 3 グループとライブ表示が描画される', () => {
    setup();
    render(<SchedulingSettingsPage />);

    expect(screen.getByText('時間のルール')).toBeInTheDocument();
    expect(screen.getByText('移動の見積もり')).toBeInTheDocument();
    expect(screen.getByText('営業・コース')).toBeInTheDocument();
    // 速度 20km/h → 1kmあたり約3分
    expect(screen.getByText(/1kmあたり約3分/)).toBeInTheDocument();
    // 昼休み短縮の注記
    expect(screen.getByText(/最低30分まで短縮/)).toBeInTheDocument();
    // 直線距離の注記
    expect(screen.getByText(/直線距離/)).toBeInTheDocument();
  });

  it('2. バッファー数値変更 → 保存活性 + 変更フィールドのみ PUT', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined);
    setup({ mutateAsync });
    render(<SchedulingSettingsPage />);

    // 初期は dirty でないため保存は disabled。
    const saveBtn = screen.getByTestId('save');
    expect(saveBtn).toBeDisabled();

    // バッファーの数値入力を 8 → 15 に変更。
    const bufferNumber = screen.getByTestId('visit-buffer-number');
    fireEvent.change(bufferNumber, { target: { value: '15' } });

    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    // 変更したフィールドのみが payload に載る。
    expect(mutateAsync).toHaveBeenCalledWith({ visit_buffer_min: 15 });
  });

  it('2b. 営業時間変更は "HH:MM:SS" で送信される', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined);
    setup({ mutateAsync });
    render(<SchedulingSettingsPage />);

    fireEvent.change(screen.getByTestId('business-start'), { target: { value: '08:00' } });
    fireEvent.click(screen.getByTestId('save'));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({ business_start: '08:00:00' });
  });

  it('3. 「すべて既定に戻す」で全項目 null を PUT (一部ユーザー設定済のとき)', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined);
    setup({ mutateAsync, isDefault: NONE_DEFAULT });
    render(<SchedulingSettingsPage />);

    fireEvent.click(screen.getByTestId('reset-all'));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const payload = mutateAsync.mock.calls[0][0];
    expect(Object.values(payload).every((x) => x === null)).toBe(true);
    expect(Object.keys(payload)).toHaveLength(8);
  });

  it('3b. 既に全項目が既定なら reset は PUT せずスキップする', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined);
    // IS_DEFAULT は全項目 true (= 全既定)。
    setup({ mutateAsync, isDefault: IS_DEFAULT });
    render(<SchedulingSettingsPage />);

    fireEvent.click(screen.getByTestId('reset-all'));

    // 余計な PUT (audit_log) を発生させない。
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(toast.info).toHaveBeenCalled();
  });

  it('4. staff ロールは /dashboard へリダイレクトされる', () => {
    setup({ role: 'staff' });
    render(<SchedulingSettingsPage />);
    expect(replaceMock).toHaveBeenCalledWith('/dashboard');
  });

  it('5. 背景リフェッチ (data 参照変化) で未保存の draft 編集が消えない', () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined);
    setup({ mutateAsync });
    const { rerender } = render(<SchedulingSettingsPage />);

    // バッファーを 8 → 15 に編集 (未保存)。
    const bufferNumber = screen.getByTestId('visit-buffer-number');
    fireEvent.change(bufferNumber, { target: { value: '15' } });
    expect((bufferNumber as HTMLInputElement).value).toBe('15');

    // 背景リフェッチ相当: 同一値だが新しい data オブジェクト参照を返す。
    (useSchedulingSettings as Mock).mockReturnValue({
      data: { values: { ...VALUES }, is_default: { ...IS_DEFAULT } },
      isLoading: false,
      isError: false,
    });
    rerender(<SchedulingSettingsPage />);

    // 未保存編集 (15) がサーバ値 (8) で上書きされない。
    expect((screen.getByTestId('visit-buffer-number') as HTMLInputElement).value).toBe('15');
    // 保存ボタンも活性のまま (dirty 維持)。
    expect(screen.getByTestId('save')).not.toBeDisabled();
  });
});
