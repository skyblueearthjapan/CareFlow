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
import { render, screen, fireEvent } from '@testing-library/react';

import type { PatientRead } from '@/lib/schemas/patient';

vi.mock('@/lib/queries/patients', () => ({
  usePatients: vi.fn(),
  usePatient: vi.fn(),
  useCreatePatient: vi.fn(),
  useUpdatePatient: vi.fn(),
}));

vi.mock('@/lib/queries/geocoding', () => ({
  useGeocode: vi.fn(),
}));

vi.mock('@/lib/queries/offices', () => ({
  useResolveOffice: vi.fn(),
}));

import {
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
    name: '青柳 あい',
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
      target: { value: '青柳' },
    });
    // 候補に患者名 + コードが出る。
    expect(screen.getByText('青柳 あい')).toBeInTheDocument();
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
      target: { value: '青柳' },
    });
    // 候補 (氏名) をタップ → edit モードへ。
    fireEvent.click(screen.getByText('青柳 あい'));

    // edit モードのフォーム: 氏名/コードに初期値、保存ボタンは「カルテを保存」。
    const nameInput = screen.getByLabelText('氏名') as HTMLInputElement;
    expect(nameInput.value).toBe('青柳 あい');
    expect((screen.getByLabelText('患者コード') as HTMLInputElement).value).toBe('P-1042');
    expect(screen.getByText('患者コード（必須）')).toBeInTheDocument();

    // 氏名を変更して保存 → useUpdatePatient (PATCH) が呼ばれる。
    fireEvent.change(nameInput, { target: { value: '青柳 あい (更新)' } });
    fireEvent.click(screen.getByRole('button', { name: /カルテを保存/ }));

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(createMutate).not.toHaveBeenCalled();
    const values = updateMutate.mock.calls[0]![0] as Record<string, unknown>;
    expect(values.name).toBe('青柳 あい (更新)');
    expect(values.code).toBe('P-1042');
  });
});
