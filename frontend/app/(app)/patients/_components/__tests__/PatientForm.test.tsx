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
  useAddOfficeAreaCity: vi.fn(),
  useDismissAreaPrompt: vi.fn(),
}));

// ─── Mock toast (sonner) ──────────────────────────────────────────────────────
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
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
import {
  useOffices,
  useResolveOffice,
  useAddOfficeAreaCity,
  useDismissAreaPrompt,
} from '@/lib/queries/offices';
import { toast } from '@/components/ui/sonner';
import { emptyPatientFormValues } from '@/lib/schemas/patient';
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
  // W-7: 選択拠点名の解決に使うため combobox の option id と一致させて offices を返す。
  (useOffices as Mock).mockReturnValue({
    offices: [
      { id: 'office-uuid-1', name: '稲毛拠点' },
      { id: 'office-uuid-2', name: '都賀拠点' },
    ],
    isLoading: false,
  });
  (useResolveOffice as Mock).mockReturnValue({
    mutateAsync,
    isPending: false,
  });
  const addAreaCity = vi.fn().mockResolvedValue({
    office_id: 'office-uuid-1',
    city_id: 'city-1',
    city_name: '千葉市美浜区',
  });
  const dismissArea = vi.fn().mockResolvedValue(undefined);
  (useAddOfficeAreaCity as Mock).mockReturnValue({ mutateAsync: addAreaCity, isPending: false });
  (useDismissAreaPrompt as Mock).mockReturnValue({ mutateAsync: dismissArea, isPending: false });
  return { addAreaCity, dismissArea };
}

/** resolve 結果に W-7 の matched_city を含めたモック値を作る。 */
function noneWithCity(overrides: Record<string, unknown> = {}) {
  return {
    office_id: null,
    office_name: null,
    matched_city_id: 'city-1',
    confidence: 'none',
    matched_city: { id: 'city-1', name: '千葉市美浜区', prefecture: '千葉県' },
    prompt_dismissed: false,
    ...overrides,
  };
}

/** 発火4条件を揃える: 住所入力→resolve(none+city)→拠点を手動選択。callout を出す。 */
async function renderWithCalloutFired(mutateAsync: Mock) {
  render(<PatientForm onSubmit={vi.fn()} />);
  const addressInput = screen.getByTestId('address-input');
  act(() => {
    fireEvent.change(addressInput, { target: { value: '千葉県千葉市美浜区' } });
  });
  await flushDebounceAndMutation();
  // 拠点を手動選択 (officeMode='manual' かつ値あり)
  const combobox = screen.getByTestId('office-combobox') as HTMLSelectElement;
  act(() => {
    fireEvent.change(combobox, { target: { value: 'office-uuid-1' } });
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

  // ─── Phase G-86: 患者コードの自動採番 (必須→任意) ───────────────────────────
  // code 空 (未入力) でも氏名さえあれば form validation が通り submit できる。
  // backend は空 code を自動採番する契約。

  it('9. 患者コード空でも氏名があれば submit できる (自動採番)', async () => {
    setupMocks();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    render(<PatientForm onSubmit={onSubmit} />);

    // 氏名のみ入力 (患者コードは空のまま)。
    const nameInput = screen.getByLabelText(/氏名/) as HTMLInputElement;
    act(() => {
      fireEvent.change(nameInput, { target: { value: 'テスト患者' } });
    });

    // 患者コードが空であることを確認。
    const codeInput = screen.getByLabelText(/患者コード/) as HTMLInputElement;
    expect(codeInput.value).toBe('');

    const submitBtn = screen.getByRole('button', { name: '保存' });
    await act(async () => {
      fireEvent.click(submitBtn);
    });
    await flushDebounceAndMutation(0);
    await act(async () => {
      await Promise.resolve();
    });

    // validation を通過し onSubmit が呼ばれる (code 空でブロックされない)。
    expect(onSubmit).toHaveBeenCalled();
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

  // ─── W-6 項目4: 編集ページの拠点自動上書きバグ修正 ──────────────────────────

  it('項目4: 編集モード (primary_office_id 既設定) は manual 初期化で住所 watch が上書きしない', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      office_id: 'office-uuid-1',
      office_name: '稲毛拠点',
      matched_city_id: 'cccccccc-0000-0000-0000-000000000001',
      confidence: 'exact',
    });
    setupMocks(mutateAsync);

    render(
      <PatientForm
        onSubmit={vi.fn()}
        defaultValues={{
          ...emptyPatientFormValues,
          name: '既存 患者',
          address: '千葉県千葉市稲毛区',
          primary_office_id: 'office-uuid-2',
        }}
      />,
    );

    // 編集モードは manual 初期化 → 最初から「自動判定に戻す」リンクが出る。
    expect(screen.getByText('自動判定に戻す')).toBeInTheDocument();

    // 住所 watch の初回発火で resolve は走るが、手動設定済みの拠点は上書きされない。
    await flushDebounceAndMutation();
    expect(mutateAsync).toHaveBeenCalled();
    const combobox = screen.getByTestId('office-combobox') as HTMLSelectElement;
    expect(combobox.value).toBe('office-uuid-2');
  });

  it('項目4: 新規作成 (defaultValues 無し) は auto 初期化で住所から自動セットされる', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      office_id: 'office-uuid-1',
      office_name: '稲毛拠点',
      matched_city_id: 'cccccccc-0000-0000-0000-000000000001',
      confidence: 'exact',
    });
    setupMocks(mutateAsync);

    render(<PatientForm onSubmit={vi.fn()} />);

    // 手動選択していないので「自動判定に戻す」は出ない (= auto)。
    expect(screen.queryByText('自動判定に戻す')).not.toBeInTheDocument();

    const addressInput = screen.getByTestId('address-input');
    act(() => {
      fireEvent.change(addressInput, { target: { value: '千葉県千葉市稲毛区穴川' } });
    });
    await flushDebounceAndMutation();

    // auto なので resolve 結果で自動セットされる。
    const combobox = screen.getByTestId('office-combobox') as HTMLSelectElement;
    expect(combobox.value).toBe('office-uuid-1');
  });

  // ─── W-7: 地域ルールの学習 (未カバー地域の呼びかけ) ──────────────────────────

  describe('W-7 地域ルール呼びかけ', () => {
    it('発火4条件が揃うと callout が表示される', async () => {
      const mutateAsync = vi.fn().mockResolvedValue(noneWithCity());
      setupMocks(mutateAsync);

      await renderWithCalloutFired(mutateAsync);

      const callout = screen.getByTestId('region-rule-callout');
      expect(callout).toBeInTheDocument();
      expect(callout.textContent).toContain('千葉市美浜区');
      expect(callout.textContent).toContain('稲毛拠点');
    });

    it('confidence=exact なら callout は出ない', async () => {
      const mutateAsync = vi.fn().mockResolvedValue(
        noneWithCity({ confidence: 'exact', office_id: 'office-uuid-1', office_name: '稲毛拠点' }),
      );
      setupMocks(mutateAsync);

      render(<PatientForm onSubmit={vi.fn()} />);
      const addressInput = screen.getByTestId('address-input');
      act(() => {
        fireEvent.change(addressInput, { target: { value: '千葉県千葉市稲毛区' } });
      });
      await flushDebounceAndMutation();
      const combobox = screen.getByTestId('office-combobox') as HTMLSelectElement;
      act(() => {
        fireEvent.change(combobox, { target: { value: 'office-uuid-1' } });
      });

      expect(screen.queryByTestId('region-rule-callout')).not.toBeInTheDocument();
    });

    it('prompt_dismissed=true なら callout は出ない', async () => {
      const mutateAsync = vi.fn().mockResolvedValue(noneWithCity({ prompt_dismissed: true }));
      setupMocks(mutateAsync);

      await renderWithCalloutFired(mutateAsync);

      expect(screen.queryByTestId('region-rule-callout')).not.toBeInTheDocument();
    });

    it('matched_city=null なら callout は出ない', async () => {
      const mutateAsync = vi.fn().mockResolvedValue(noneWithCity({ matched_city: null }));
      setupMocks(mutateAsync);

      await renderWithCalloutFired(mutateAsync);

      expect(screen.queryByTestId('region-rule-callout')).not.toBeInTheDocument();
    });

    it('拠点未選択なら callout は出ない (手動選択していない)', async () => {
      const mutateAsync = vi.fn().mockResolvedValue(noneWithCity());
      setupMocks(mutateAsync);

      render(<PatientForm onSubmit={vi.fn()} />);
      const addressInput = screen.getByTestId('address-input');
      act(() => {
        fireEvent.change(addressInput, { target: { value: '千葉県千葉市美浜区' } });
      });
      await flushDebounceAndMutation();

      // 拠点を選んでいない → 発火しない
      expect(screen.queryByTestId('region-rule-callout')).not.toBeInTheDocument();
    });

    it('[担当地域に登録する] → area-cities mutation が呼ばれ callout が消える', async () => {
      const mutateAsync = vi.fn().mockResolvedValue(noneWithCity());
      const { addAreaCity } = setupMocks(mutateAsync);

      await renderWithCalloutFired(mutateAsync);
      expect(screen.getByTestId('region-rule-callout')).toBeInTheDocument();

      const registerBtn = screen.getByTestId('region-rule-register');
      await act(async () => {
        fireEvent.click(registerBtn);
      });
      await act(async () => {
        await Promise.resolve();
      });

      expect(addAreaCity).toHaveBeenCalledWith({ officeId: 'office-uuid-1', cityId: 'city-1' });
      expect(toast.success as Mock).toHaveBeenCalled();
      expect(screen.queryByTestId('region-rule-callout')).not.toBeInTheDocument();
    });

    it('[今回だけ] → dismissals mutation が呼ばれ callout が消える', async () => {
      const mutateAsync = vi.fn().mockResolvedValue(noneWithCity());
      const { dismissArea } = setupMocks(mutateAsync);

      await renderWithCalloutFired(mutateAsync);
      expect(screen.getByTestId('region-rule-callout')).toBeInTheDocument();

      const dismissBtn = screen.getByTestId('region-rule-dismiss');
      await act(async () => {
        fireEvent.click(dismissBtn);
      });
      await act(async () => {
        await Promise.resolve();
      });

      expect(dismissArea).toHaveBeenCalledWith('city-1');
      expect(screen.queryByTestId('region-rule-callout')).not.toBeInTheDocument();
    });
  });
});
