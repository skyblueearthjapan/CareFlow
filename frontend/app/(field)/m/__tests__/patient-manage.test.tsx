/**
 * PatientManageSheet (Phase G-87) — 提案を介さない患者 新規登録 / 検索編集の振る舞いテスト.
 *
 * 提案 (SuggestSheet) / 枠採用 / 承認を介さずに、患者の新規登録と既存患者の
 * 検索 → 編集を行うハブ (PatientManageSheet) と、それが内包する create/edit 両対応の
 * KarteEditSheet を、usePatients / usePatient / useCreatePatient / useUpdatePatient を
 * モックして検証する。
 *
 * カバー:
 *   list ビュー:
 *     1. 「＋新規患者を登録」ボタン + 検索ボックスが出る
 *     2. 検索語入力 → 候補リスト表示
 *   create フロー:
 *     3. 新規ボタン → create モード (ヘッダ「新規患者を登録」/ボタン「登録」/コード任意)
 *     4. 氏名のみで「登録」 → useCreatePatient が呼ばれる (code 空文字)
 *     5. 氏名空欄では登録しない (mutate されない)
 *   edit フロー:
 *     6. 候補タップ → usePatient(id) 取得後に edit モード (PATCH 維持・ボタン「カルテを保存」)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { patientUpdateSchema } from '@/lib/schemas/patient';
import type { PatientFormValues, PatientRead } from '@/lib/schemas/patient';

vi.mock('@/lib/queries/patients', async () => ({
  // 送る中身のきまり (住所 2 つ) は本物で確かめる。
  prepareFormPayload: (await vi.importActual<Record<string, unknown>>('@/lib/queries/patients'))
    .prepareFormPayload,
  usePatients: vi.fn(),
  usePatient: vi.fn(),
  useCreatePatient: vi.fn(),
  useUpdatePatient: vi.fn(),
  // ステータス変更ゲート (usePatientStatusGate → PatientStatusChangeDialog) が
  // カルテ編集シートに常駐する。open=false でも hook は呼ばれるので実体が要る。
  usePatientStatusImpact: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useChangePatientStatus: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/lib/queries/geocoding', () => ({
  useGeocode: vi.fn(),
}));

vi.mock('@/lib/queries/offices', () => ({
  useResolveOffice: vi.fn(),
}));

import {
  prepareFormPayload,
  usePatients,
  usePatient,
  useCreatePatient,
  useUpdatePatient,
} from '@/lib/queries/patients';
import { useGeocode } from '@/lib/queries/geocoding';
import { useResolveOffice } from '@/lib/queries/offices';
import { PatientManageSheet } from '@/components/field/FieldSheets';

const PATIENT_ID = '33333333-3333-3333-3333-333333333333';

function makePatient(): PatientRead {
  return {
    id: PATIENT_ID,
    code: 'P-1042',
    name: '利用者A あい',
    kana: 'アオヤギ アイ',
    sex: 'female',
    status: 'admitted',
    insurance: 'medical',
    address: '千葉市稲毛区小仲台6-2-1',
    primary_office_id: '11111111-1111-1111-1111-111111111111',
    sex_restriction: 'female_only',
    requires_multiple_staff: true,
    note: '玄関の鍵は植木鉢の下。',
    weekly_pattern: {
      frequency_per_week: 3,
      visit_frequency: 'biweekly',
      preferred_weekdays: ['Mon', 'Fri', 'Sun'],
      service_minutes: 60,
      time_type: '終日',
    },
    special_week_active: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  } as unknown as PatientRead;
}

const createMutate = vi.fn();
const updateMutate = vi.fn();

function setPatients(items: PatientRead[], over: Record<string, unknown> = {}) {
  (usePatients as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items, total: items.length, page: 1, limit: 8, truncated: false },
    isLoading: false,
    isFetching: false,
    isError: false,
    ...over,
  });
}

function setPatient(data: PatientRead | undefined, over: Record<string, unknown> = {}) {
  (usePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data,
    isLoading: !data,
    isError: false,
    ...over,
  });
}

function renderSheet(onToast = vi.fn()) {
  return render(<PatientManageSheet onClose={vi.fn()} onToast={onToast} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  createMutate.mockReset();
  updateMutate.mockReset();
  setPatients([makePatient()]);
  // 既定では詳細未取得 (edit ビューに入って初めて usePatient が data を返す想定だが、
  // テストごとに setPatient で上書きする)。
  setPatient(undefined);
  (useCreatePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: createMutate,
    isPending: false,
    isError: false,
    data: undefined,
  });
  (useUpdatePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: updateMutate,
    isPending: false,
    isError: false,
    data: undefined,
  });
  (useGeocode as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  });
  (useResolveOffice as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  });
});

describe('PatientManageSheet list ビュー', () => {
  it('新規登録ボタンと検索ボックスを表示する', () => {
    renderSheet();
    expect(screen.getByText('＋ 新規患者を登録')).toBeInTheDocument();
    expect(screen.getByLabelText('既存のお客様を検索')).toBeInTheDocument();
  });

  it('検索語を入力すると候補リストが表示される', () => {
    renderSheet();
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '利用者A' },
    });
    // 候補に患者名 + コードが出る。
    expect(screen.getByText('利用者A あい')).toBeInTheDocument();
    expect(screen.getByText('P-1042')).toBeInTheDocument();
  });
});

describe('PatientManageSheet 新規登録フロー (create)', () => {
  it('新規ボタンで create モードへ (ヘッダ/ボタン/コード任意)', () => {
    renderSheet();
    fireEvent.click(screen.getByText('＋ 新規患者を登録'));
    // ヘッダは「新規患者を登録」、コード欄は任意ラベル、保存ボタンは「登録」。
    expect(screen.getByText('新規患者を登録')).toBeInTheDocument();
    expect(screen.getByText('患者コード（任意）')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登録' })).toBeInTheDocument();
    // 氏名は空 (新規)。
    expect((screen.getByLabelText('氏名') as HTMLInputElement).value).toBe('');
  });

  it('氏名のみ入力 → 登録で useCreatePatient が code 空文字で呼ばれる', () => {
    renderSheet();
    fireEvent.click(screen.getByText('＋ 新規患者を登録'));
    // 氏名のみ入力 (コードは空のまま = 自動採番)。
    fireEvent.change(screen.getByLabelText('氏名'), { target: { value: '新患 太郎' } });
    fireEvent.click(screen.getByRole('button', { name: '登録' }));

    expect(createMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate).not.toHaveBeenCalled();
    const values = createMutate.mock.calls[0]![0] as Record<string, unknown>;
    expect(values.name).toBe('新患 太郎');
    // コードは空文字で送る (useCreatePatient 内の schema が空 → undefined に畳んで自動採番)。
    expect(values.code).toBe('');
    expect(values.status).toBe('active');
  });

  it('氏名空欄では登録せず、トーストで弾く', () => {
    const onToast = vi.fn();
    renderSheet(onToast);
    fireEvent.click(screen.getByText('＋ 新規患者を登録'));
    fireEvent.click(screen.getByRole('button', { name: '登録' }));
    expect(createMutate).not.toHaveBeenCalled();
    expect(onToast).toHaveBeenCalledWith('氏名を入力してください');
  });
});

describe('PatientManageSheet 検索編集フロー (edit)', () => {
  it('候補タップ → 詳細取得後に edit モード (PATCH 維持・コード必須)', () => {
    // edit ビューに入ると usePatient が詳細を返す状態にする。
    setPatient(makePatient());
    renderSheet();
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '利用者A' },
    });
    // 候補 (氏名) をタップ → edit モードへ。
    fireEvent.click(screen.getByText('利用者A あい'));

    // edit モードのフォーム: 氏名/コードに初期値、保存ボタンは「カルテを保存」。
    const nameInput = screen.getByLabelText('氏名') as HTMLInputElement;
    expect(nameInput.value).toBe('利用者A あい');
    expect((screen.getByLabelText('患者コード') as HTMLInputElement).value).toBe('P-1042');
    expect(screen.getByText('患者コード（必須）')).toBeInTheDocument();

    // 氏名を変更して保存 → useUpdatePatient (PATCH) が呼ばれる。
    fireEvent.change(nameInput, { target: { value: '利用者A あい (更新)' } });
    fireEvent.click(screen.getByRole('button', { name: /カルテを保存/ }));

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(createMutate).not.toHaveBeenCalled();
    const values = updateMutate.mock.calls[0]![0] as Record<string, unknown>;
    expect(values.name).toBe('利用者A あい (更新)');
    expect(values.code).toBe('P-1042');
  });

  // 住所 2 つ (設計 2026-10-22)
  async function editAddress(patient: PatientRead, next: string) {
    setPatient(patient);
    renderSheet();
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '利用者A' },
    });
    fireEvent.click(screen.getByText('利用者A あい'));
    fireEvent.change(screen.getByLabelText('ご住所'), { target: { value: next } });
    fireEvent.click(screen.getByRole('button', { name: /カルテを保存/ }));
    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    return updateMutate.mock.calls[0]![0] as PatientFormValues;
  }

  it('訪問先が「保険証の住所と同じ」の方: 直した住所は保険証の住所にも入り、送る中身で戻らない', async () => {
    const NEXT = '千葉市稲毛区園生町1-2-3';
    const values = await editAddress(
      {
        ...makePatient(),
        insurance_address: '千葉市稲毛区小仲台6-2-1',
        visit_same_as_insurance: true,
      } as PatientRead,
      NEXT,
    );
    expect(values.insurance_address).toBe(NEXT);
    // 位置が求められなかったので、前の位置は送らない (サーバーが求め直す)。
    expect(values.lat).toBe('');
    expect(patientUpdateSchema.parse(prepareFormPayload(values, values)).lat).toBeUndefined();
    expect(prepareFormPayload(values)).toMatchObject({
      insurance_address: NEXT,
      address: NEXT,
      visit_same_as_insurance: true,
    });
  });

  it('訪問先が「別の場所」の方: 直した住所は訪問先だけ (保険証の住所は変えない)', async () => {
    const NEXT = '千葉市稲毛区園生町1-2-3';
    const values = await editAddress(
      {
        ...makePatient(),
        insurance_address: '千葉市花見川区こてはし台9-9-9',
        visit_same_as_insurance: false,
      } as PatientRead,
      NEXT,
    );
    expect(prepareFormPayload(values)).toMatchObject({
      insurance_address: '千葉市花見川区こてはし台9-9-9',
      address: NEXT,
      visit_same_as_insurance: false,
    });
  });
});

// 訪問頻度 (biweekly-monthly-design-2026-10-30.md §2-4・段 2 のレビュー):
// 開いて保存しただけで weekly_pattern の entries・知らないキーを消さず、
// 空の希望時刻に 09:00 / 12:00 を書かない。
describe('KarteEditSheet weekly_pattern の保存 (読んだ dict を保つ)', () => {
  const SOURCE_WP = {
    frequency_per_week: 2,
    visit_frequency: 'biweekly',
    biweekly_anchor: '2026-11-02',
    preferred_weekdays: ['Tue', 'Fri'],
    service_minutes: 35,
    time_type: '時間帯',
    entries: [
      { weekday: 'Tue', start: '10:00' },
      { weekday: 'Fri', start: '14:00' },
    ],
    custom_note_key: { keep: true },
  };

  async function openAndSave(change?: () => void) {
    setPatient({ ...makePatient(), weekly_pattern: SOURCE_WP } as unknown as PatientRead);
    renderSheet();
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '利用者A' },
    });
    fireEvent.click(screen.getByText('利用者A あい'));
    change?.();
    fireEvent.click(screen.getByRole('button', { name: /カルテを保存/ }));
    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    const values = updateMutate.mock.calls[0]![0] as PatientFormValues;
    return prepareFormPayload(values).weekly_pattern as Record<string, unknown>;
  }

  it('何も変えずに保存 → 読んだ dict のまま (entries・知らないキーが残り、時刻を足さない)', async () => {
    const wp = await openAndSave();
    expect(wp).toEqual(SOURCE_WP);
    expect(wp).not.toHaveProperty('preferred_start');
    expect(wp).not.toHaveProperty('preferred_end');
  });

  it('空の希望時刻は「未設定」と出し、選んだ欄だけを書く', async () => {
    const wp = await openAndSave(() => {
      const start = screen.getByLabelText('希望開始時刻') as HTMLSelectElement;
      const end = screen.getByLabelText('希望終了時刻') as HTMLSelectElement;
      expect(start.value).toBe('');
      expect(end.value).toBe('');
      expect(start.options[0]!.textContent).toBe('未設定');
      fireEvent.change(start, { target: { value: start.options[1]!.value } });
    });
    expect(typeof wp.preferred_start).toBe('string');
    expect(wp.preferred_start).not.toBe('');
    expect(wp).not.toHaveProperty('preferred_end');
    expect(wp.entries).toEqual(SOURCE_WP.entries);
    expect(wp.custom_note_key).toEqual({ keep: true });
  });

  it('NG 曜日: 選んだ曜日を月→日の順で書き、ほかのキー (entries・知らないキー) を残す', async () => {
    const wp = await openAndSave(() => {
      fireEvent.click(screen.getByRole('button', { name: 'NG 曜日 金曜' }));
      fireEvent.click(screen.getByRole('button', { name: 'NG 曜日 月曜' }));
      // 希望曜日 (火・金) と重なった金はやわらかく知らせる.
      expect(screen.getByTestId('ng-weekday-conflict').textContent).toContain('金');
    });
    expect(wp.ng_weekdays).toEqual(['Mon', 'Fri']);
    expect(wp.entries).toEqual(SOURCE_WP.entries);
    expect(wp.custom_note_key).toEqual({ keep: true });
    expect(wp.biweekly_anchor).toBe('2026-11-02');
  });

  it('NG 曜日を持つ方: 開いて保存しただけなら NG 曜日はそのまま', async () => {
    setPatient({
      ...makePatient(),
      weekly_pattern: { ...SOURCE_WP, ng_weekdays: ['Sun'] },
    } as unknown as PatientRead);
    renderSheet();
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '利用者A' },
    });
    fireEvent.click(screen.getByText('利用者A あい'));
    expect(screen.getByRole('button', { name: 'NG 曜日 日曜' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: /カルテを保存/ }));
    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    const values = updateMutate.mock.calls[0]![0] as PatientFormValues;
    const wp = prepareFormPayload(values).weekly_pattern as Record<string, unknown>;
    expect(wp).toEqual({ ...SOURCE_WP, ng_weekdays: ['Sun'] });
  });
});
