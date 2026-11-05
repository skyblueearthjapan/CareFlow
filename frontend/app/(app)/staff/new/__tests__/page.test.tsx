/**
 * StaffNewPage vitest unit tests — S1 「資格」セレクトの追加.
 *
 * 設計 docs/plans/kaipoke-service-content-design.md §1-2:
 * カイポケ「職種」列 = スタッフの資格。准看護師のときだけサービス内容が
 * 「・准看」になるが、FE には入力手段が無かった (v2 schema / フォーム未対応)。
 *
 * 観点:
 *   1. 「資格」セレクトが描画され、既定は未設定 (空)
 *   2. 選択肢は 未設定 + 5 資格
 *   3. 選択して登録すると POST payload に qualification が載る
 *   4. 未設定のまま登録すると qualification: null で送る (空文字を送らない)
 * 連絡先 (contacts-phone-email-design-2026-11-05 §7): 電話・連絡用メール・お知らせ
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

// ─── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('@/lib/queries/staff', () => ({
  useCreateStaff: vi.fn(),
}));

vi.mock('@/components/master/OfficeCombobox', () => ({
  OfficeCombobox: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <select data-testid="office-combobox" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">-- 選択 --</option>
    </select>
  ),
}));

vi.mock('@/components/brand/Rakusuke', () => ({
  RakusukeTitle: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

// ─── Imports after mocks ──────────────────────────────────────────────────────
import { useSession } from 'next-auth/react';
import { useCreateStaff } from '@/lib/queries/staff';
import StaffNewPage from '../page';

function setupMocks() {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: 'admin' } },
    status: 'authenticated',
  });
  const mutate = vi.fn();
  (useCreateStaff as Mock).mockReturnValue({
    mutate,
    isPending: false,
    isError: false,
    error: null,
  });
  return { mutate };
}

/** 氏名だけ埋めて登録ボタンを押す (氏名以外は任意)。 */
async function submitWithName(name = 'テスト 花子') {
  fireEvent.change(screen.getByPlaceholderText('例: 山田 花子'), { target: { value: name } });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '登録する' }));
  });
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('StaffNewPage — 資格 (カイポケ職種)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 資格セレクトが描画され、既定は未設定', () => {
    setupMocks();
    render(<StaffNewPage />);

    const select = screen.getByTestId('staff-qualification-select') as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.value).toBe('');
  });

  it('2. 選択肢は 未設定 + 5 資格', () => {
    setupMocks();
    render(<StaffNewPage />);

    const select = screen.getByTestId('staff-qualification-select') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual([
      '',
      '看護師',
      '准看護師',
      '理学療法士',
      '作業療法士',
      '言語聴覚士',
    ]);
  });

  it('3. 准看護師を選ぶと POST payload に載る', async () => {
    const { mutate } = setupMocks();
    render(<StaffNewPage />);

    const select = screen.getByTestId('staff-qualification-select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: '准看護師' } });
    expect(select.value).toBe('准看護師');

    await submitWithName();

    expect(mutate).toHaveBeenCalledTimes(1);
    const payload = mutate.mock.calls[0]?.[0] as { qualification?: string | null };
    expect(payload.qualification).toBe('准看護師');
  });

  it('4. 未設定のままなら qualification: null で送る', async () => {
    const { mutate } = setupMocks();
    render(<StaffNewPage />);

    await submitWithName();

    expect(mutate).toHaveBeenCalledTimes(1);
    const payload = mutate.mock.calls[0]?.[0] as { qualification?: string | null };
    expect(payload.qualification).toBeNull();
  });
});

describe('StaffNewPage — 連絡先', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('既定はお知らせ「受け取らない」・ログイン用とは別の説明・今は使わない説明', async () => {
    const { mutate } = setupMocks();
    render(<StaffNewPage />);
    const section = screen.getByTestId('staff-contacts-section');
    expect(section).toHaveTextContent('ログインに使うメールアドレス（アカウントの欄）とは別です');
    expect(section).toHaveTextContent('今は使いません');
    expect(screen.getByTestId('staff-notify-none')).toHaveAttribute('aria-pressed', 'true');

    await submitWithName();
    const payload = mutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(payload).toMatchObject({ phone: null, contact_email: null, notify_channel: 'none' });
  });

  it('入れた電話・メール・手段を送り、見分けを出す', async () => {
    const { mutate } = setupMocks();
    render(<StaffNewPage />);
    fireEvent.change(screen.getByTestId('staff-phone-input'), {
      target: { value: '080-9876-5432' },
    });
    fireEvent.change(screen.getByTestId('staff-contact-email-input'), {
      target: { value: 'kan@example.jp' },
    });
    fireEvent.click(screen.getByTestId('staff-notify-sms'));
    expect(screen.getByTestId('phone-judge')).toHaveTextContent('携帯');

    await submitWithName();
    const payload = mutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(payload).toMatchObject({
      phone: '080-9876-5432',
      contact_email: 'kan@example.jp',
      notify_channel: 'sms',
    });
  });

  it('形の違う電話・メールは送らず、欄に誤りを出す', async () => {
    const { mutate } = setupMocks();
    render(<StaffNewPage />);
    fireEvent.change(screen.getByTestId('staff-phone-input'), { target: { value: '090-123-456' } });
    fireEvent.change(screen.getByTestId('staff-contact-email-input'), {
      // ブラウザの type=email は通すが、らく助の決まり (ドメインに「.」) では違う形
      target: { value: 'kan@example' },
    });
    await submitWithName();
    expect(mutate).not.toHaveBeenCalled();
    expect(screen.getByText('電話番号の形が違います')).toBeInTheDocument();
    expect(screen.getByText('メールアドレスの形が違います')).toBeInTheDocument();
  });
});
