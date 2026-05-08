/**
 * PatientForm vitest unit tests — W12-FE (住所から主担当拠点を自動判定).
 *
 * テストケース:
 * 1. 初期 (空住所) → resolve 呼ばれない
 * 2. 住所入力 → debounce 600ms 後に resolve が呼ばれる
 * 3. confidence='exact' → primary_office_id が自動セットされる (自動判定バッジ表示)
 * 4. confidence='none'  → primary_office_id 不変、警告表示
 * 5. ユーザー手動選択後は住所変更で自動セットされない (officeMode='manual' 保護)
 *
 * NOTE: fake timers + async mutation のテストは、`act` で timer を進め
 * その後に Promise マイクロタスクを flush してから DOM を検査する。
 * `screen.findByText` (waitFor 内部) は real timer ポーリングに依存するため
 * fake timer 環境では使用せず `getByText` を使う。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

// ─── Mock next-auth ───────────────────────────────────────────────────────────
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

// ─── Mock office query hooks ──────────────────────────────────────────────────
vi.mock('@/lib/queries/offices', () => ({
  useOffices: vi.fn(),
  useResolveOffice: vi.fn(),
}));

// ─── Mock AddressGeocodeField (住所入力 input を直接レンダー) ────────────────
vi.mock('@/components/AddressGeocodeField', () => ({
  AddressGeocodeField: ({
    formMethods,
    addressFieldName,
    disabled,
  }: {
    formMethods: { register: (name: string) => object };
    addressFieldName: string;
    disabled?: boolean;
  }) => (
    <input
      data-testid="address-input"
      disabled={disabled}
      {...formMethods.register(addressFieldName)}
    />
  ),
}));

// ─── Mock OfficeCombobox ──────────────────────────────────────────────────────
vi.mock('@/components/master/OfficeCombobox', () => ({
  OfficeCombobox: ({
    value,
    onChange,
    disabled,
  }: {
    value: string;
    onChange: (v: string) => void;
    disabled?: boolean;
  }) => (
    <select
      data-testid="office-combobox"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">-- 選択 --</option>
      <option value="office-uuid-1">稲毛拠点</option>
      <option value="office-uuid-2">都賀拠点</option>
    </select>
  ),
}));

// ─── Mock WeeklyPatternEditor (重くなるため stub) ────────────────────────────
vi.mock('../WeeklyPatternEditor', () => ({
  WeeklyPatternEditor: () => <div data-testid="weekly-pattern-editor" />,
}));

// ─── Mock UI components ──────────────────────────────────────────────────────
vi.mock('@/components/ui/card', () => ({
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));
vi.mock('@/components/ui/alert', () => ({
  Alert: ({ children }: { children: React.ReactNode }) => <div role="alert">{children}</div>,
  AlertTitle: ({ children }: { children: React.ReactNode }) => <h4>{children}</h4>,
  AlertDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}));
vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({
    checked,
    onCheckedChange,
    ...rest
  }: {
    checked?: boolean;
    onCheckedChange?: (v: boolean) => void;
    [key: string]: unknown;
  }) => (
    <input
      type="checkbox"
      checked={checked}
      onChange={(e) => onCheckedChange?.(e.target.checked)}
      // W18 Codex-fix 軽-1: data-testid / aria-label など、追加 props を
      // 透過させて requires-multiple-staff-checkbox を testing-library から
      // 拾えるようにする。
      {...rest}
    />
  ),
}));

// ─── Imports after mocks ──────────────────────────────────────────────────────
import { useSession } from 'next-auth/react';
import { useOffices, useResolveOffice } from '@/lib/queries/offices';
import { PatientForm } from '../PatientForm';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeSession() {
  return {
    data: { accessToken: 'tok', refreshToken: 'ref', user: { role: 'admin' } },
    status: 'authenticated',
  };
}

function setupMocks(mutateAsync: Mock = vi.fn().mockResolvedValue(null)) {
  (useSession as Mock).mockReturnValue(makeSession());
  (useOffices as Mock).mockReturnValue({ offices: [], isLoading: false });
  (useResolveOffice as Mock).mockReturnValue({
    mutateAsync,
    isPending: false,
  });
}

/** fake timer 環境で debounce + async mutation を flush するユーティリティ */
async function flushDebounceAndMutation(ms = 700) {
  // 1. タイマーを進めて setTimeout callback を実行キューに積む
  act(() => {
    vi.advanceTimersByTime(ms);
  });
  // 2. Promise マイクロタスクを複数ラウンド flush
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    await Promise.resolve();
  });
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('PatientForm — W12-FE 住所→拠点自動判定', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('1. 初期 (空住所) → resolve が呼ばれない', async () => {
    const mutateAsync = vi.fn();
    setupMocks(mutateAsync);

    render(<PatientForm onSubmit={vi.fn()} />);

    await flushDebounceAndMutation();

    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('2. 住所入力 → debounce 600ms 後に resolve が呼ばれる', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      office_id: null,
      office_name: null,
      matched_city_id: null,
      confidence: 'none',
    });
    setupMocks(mutateAsync);

    render(<PatientForm onSubmit={vi.fn()} />);

    const addressInput = screen.getByTestId('address-input');

    act(() => {
      fireEvent.change(addressInput, { target: { value: '千葉県千葉市稲毛区' } });
    });

    // 400ms 未満ではまだ呼ばれない
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(mutateAsync).not.toHaveBeenCalled();

    // 残り分進めて flush
    await flushDebounceAndMutation(300);

    expect(mutateAsync).toHaveBeenCalledWith('千葉県千葉市稲毛区');
  });

  it('3. confidence="exact" → 自動判定バッジが表示される', async () => {
    const officeId = 'aaaaaaaa-0000-0000-0000-000000000001';
    const mutateAsync = vi.fn().mockResolvedValue({
      office_id: officeId,
      office_name: '稲毛拠点',
      matched_city_id: 'cccccccc-0000-0000-0000-000000000001',
      confidence: 'exact',
    });
    setupMocks(mutateAsync);

    render(<PatientForm onSubmit={vi.fn()} />);

    const addressInput = screen.getByTestId('address-input');

    act(() => {
      fireEvent.change(addressInput, { target: { value: '千葉県千葉市稲毛区穴川' } });
    });

    await flushDebounceAndMutation();

    // 自動判定バッジが表示される
    const badge = screen.getByText(
      (text) => text.includes('稲毛拠点') && text.includes('自動判定'),
    );
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toContain('exact');
  });

  it('4. confidence="none" → 警告が表示され primary_office_id は空のまま', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      office_id: null,
      office_name: null,
      matched_city_id: null,
      confidence: 'none',
    });
    setupMocks(mutateAsync);

    render(<PatientForm onSubmit={vi.fn()} />);

    const addressInput = screen.getByTestId('address-input');

    act(() => {
      fireEvent.change(addressInput, { target: { value: '北海道札幌市' } });
    });

    await flushDebounceAndMutation();

    // エリア外警告が表示される
    const warning = screen.getByText((text) => text.includes('拠点エリア外'));
    expect(warning).toBeInTheDocument();

    // OfficeCombobox の値は空のまま
    const combobox = screen.getByTestId('office-combobox') as HTMLSelectElement;
    expect(combobox.value).toBe('');
  });

  // ─── Wave 18 Codex-fix 軽-1: requires_multiple_staff checkbox round-trip ───
  // BE migration 0024 で追加された患者単位の「2 名同行必須」フラグが
  // PatientForm から正しく送られているかを担保する。

  it('6. requires_multiple_staff: 既定 false でレンダーされる', async () => {
    setupMocks();

    render(<PatientForm onSubmit={vi.fn()} />);

    const checkbox = screen.getByTestId('requires-multiple-staff-checkbox') as HTMLInputElement;
    expect(checkbox).toBeInTheDocument();
    expect(checkbox.checked).toBe(false);
  });

  it('7. requires_multiple_staff: toggle で onChange が反応する', async () => {
    setupMocks();

    render(<PatientForm onSubmit={vi.fn()} />);

    const checkbox = screen.getByTestId('requires-multiple-staff-checkbox') as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    act(() => {
      fireEvent.click(checkbox);
    });

    expect(checkbox.checked).toBe(true);

    // 再 toggle で false に戻る
    act(() => {
      fireEvent.click(checkbox);
    });
    expect(checkbox.checked).toBe(false);
  });

  it('8. requires_multiple_staff: form submit ペイロードに含まれる', async () => {
    setupMocks();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    render(<PatientForm onSubmit={onSubmit} />);

    // 必須フィールド (code / name) を入力する
    // Field コンポーネントは label の中に span (* マーカー含む) を入れているため、
    // テキスト一致は完全一致ではなく部分一致 (regex) で安全に取る。
    const codeInput = screen.getByLabelText(/患者コード/) as HTMLInputElement;
    const nameInput = screen.getByLabelText(/氏名/) as HTMLInputElement;
    act(() => {
      fireEvent.change(codeInput, { target: { value: 'P-9999' } });
      fireEvent.change(nameInput, { target: { value: 'テスト患者' } });
    });

    // checkbox をチェック
    const checkbox = screen.getByTestId('requires-multiple-staff-checkbox') as HTMLInputElement;
    act(() => {
      fireEvent.click(checkbox);
    });
    expect(checkbox.checked).toBe(true);

    // submit
    const submitBtn = screen.getByRole('button', { name: '保存' });
    await act(async () => {
      fireEvent.click(submitBtn);
    });
    // RHF の async validation を flush
    await flushDebounceAndMutation(0);
    await act(async () => {
      await Promise.resolve();
    });

    expect(onSubmit).toHaveBeenCalled();
    const submittedValues = onSubmit.mock.calls[0]?.[0] as
      | { requires_multiple_staff?: boolean }
      | undefined;
    expect(submittedValues?.requires_multiple_staff).toBe(true);
  });

  it('5. ユーザー手動選択後は住所変更で auto-set されない', async () => {
    const officeId = 'bbbbbbbb-0000-0000-0000-000000000002';
    const mutateAsync = vi.fn().mockResolvedValue({
      office_id: officeId,
      office_name: '都賀拠点',
      matched_city_id: 'dddddddd-0000-0000-0000-000000000002',
      confidence: 'fuzzy',
    });
    setupMocks(mutateAsync);

    render(<PatientForm onSubmit={vi.fn()} />);

    // ユーザーが手動で選択
    const combobox = screen.getByTestId('office-combobox') as HTMLSelectElement;
    act(() => {
      fireEvent.change(combobox, { target: { value: 'office-uuid-1' } });
    });

    // 「自動判定に戻す」リンクが表示されることを確認
    expect(screen.getByText('自動判定に戻す')).toBeInTheDocument();

    // 住所を変更して debounce 経過
    const addressInput = screen.getByTestId('address-input');
    act(() => {
      fireEvent.change(addressInput, { target: { value: '千葉県千葉市若葉区' } });
    });

    await flushDebounceAndMutation();

    // resolve は呼ばれているが、手動選択値が上書きされない
    expect(mutateAsync).toHaveBeenCalledWith('千葉県千葉市若葉区');
    // combobox はユーザー手動選択の値のまま
    expect(combobox.value).toBe('office-uuid-1');
  });
});
