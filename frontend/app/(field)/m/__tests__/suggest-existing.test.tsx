/**
 * SuggestSheet 既存患者の検索・紐付け (StageA) — 振る舞いテスト.
 *
 * 「既存のお客様」トグル → 患者検索 → 候補選択 → 提案フォームのプリフィル →
 * 「自動提案する」で `existing_patient_id` 付きの propose リクエストが送られる、
 * という一連の流れを usePatients / useProposeSlots をモックして検証する。
 *
 * カバー:
 *   1. 既存モードで検索ボックスが出る / 新規モードでは出ない
 *   2. 検索語入力 → 候補リスト表示 → 選択で「紐付け中」表示
 *   3. 選択で住所がプリフィルされる
 *   4. 自動提案で existing_patient_id = 選択患者 id + lat/lng が送られる
 *   5. 新規モードでは existing_patient_id を送らない (null)
 *   6. [変更] で紐付け解除できる
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { PatientRead } from '@/lib/schemas/patient';

vi.mock('@/lib/queries/fieldBoard', async () => {
  const actual = await vi.importActual<typeof import('@/lib/queries/fieldBoard')>(
    '@/lib/queries/fieldBoard',
  );
  return {
    ...actual,
    useProposeSlots: vi.fn(),
  };
});

vi.mock('@/lib/queries/patients', () => ({
  usePatients: vi.fn(),
}));

import { useProposeSlots } from '@/lib/queries/fieldBoard';
import { usePatients } from '@/lib/queries/patients';
import { SuggestSheet } from '@/components/field/FieldSheets';

const PATIENT_ID = '33333333-3333-3333-3333-333333333333';

function makePatient(): PatientRead {
  return {
    id: PATIENT_ID,
    code: 'P-1042',
    name: '青柳 あい',
    kana: 'アオヤギ アイ',
    sex: 'female',
    status: 'active',
    insurance: 'medical',
    address: '千葉市稲毛区小仲台6-2-1',
    lat: 35.63,
    lng: 140.11,
    sex_restriction: 'female_only',
    requires_multiple_staff: true,
    note: null,
    weekly_pattern: {
      frequency_per_week: 2,
      visit_frequency: 'every',
      preferred_weekdays: ['Mon', 'Fri'],
      service_minutes: 60,
      time_type: '時間帯',
      preferred_start: '10:00',
      preferred_end: '11:00',
    },
    special_week_active: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  } as unknown as PatientRead;
}

const mutate = vi.fn();

function setPatients(items: PatientRead[], over: Record<string, unknown> = {}) {
  (usePatients as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items, total: items.length, page: 1, limit: 8, truncated: false },
    isLoading: false,
    isFetching: false,
    isError: false,
    ...over,
  });
}

function renderSheet() {
  return render(<SuggestSheet isoYear={2026} isoWeek={23} onClose={vi.fn()} onToast={vi.fn()} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  mutate.mockReset();
  (useProposeSlots as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate,
    isPending: false,
    isError: false,
    data: undefined,
  });
  setPatients([makePatient()]);
});

describe('SuggestSheet 既存患者の紐付け', () => {
  it('新規モードでは検索ボックスを出さず、既存モードで出す', () => {
    renderSheet();
    // 既定 (seg=0=新規) では検索ボックスは無い。
    expect(screen.queryByLabelText('既存のお客様を検索')).not.toBeInTheDocument();
    // 既存のお客様トグル。
    fireEvent.click(screen.getByText('既存のお客様'));
    expect(screen.getByLabelText('既存のお客様を検索')).toBeInTheDocument();
  });

  it('検索 → 候補選択でプリフィル + 紐付け表示', () => {
    renderSheet();
    fireEvent.click(screen.getByText('既存のお客様'));
    // 検索語を入れると候補が出る (usePatients がモックで青柳を返す)。
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '青柳' },
    });
    // 候補リストの患者コード。
    expect(screen.getByText('P-1042')).toBeInTheDocument();
    // 候補をクリックして紐付け。
    fireEvent.click(screen.getByText('青柳 あい'));
    // 紐付けバナー。
    expect(screen.getByText('青柳 あい 様 を紐付け中')).toBeInTheDocument();
    // 住所がプリフィルされる (input の value)。
    expect(screen.getByDisplayValue('千葉市稲毛区小仲台6-2-1')).toBeInTheDocument();
  });

  it('自動提案で existing_patient_id + lat/lng が送られる', () => {
    renderSheet();
    fireEvent.click(screen.getByText('既存のお客様'));
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '青柳' },
    });
    fireEvent.click(screen.getByText('青柳 あい'));
    fireEvent.click(screen.getByText('自動提案する'));

    expect(mutate).toHaveBeenCalledTimes(1);
    const payload = mutate.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.existing_patient_id).toBe(PATIENT_ID);
    expect(payload.lat).toBe(35.63);
    expect(payload.lng).toBe(140.11);
    expect(payload.address).toBe('千葉市稲毛区小仲台6-2-1');
    // プリフィルされた希望が送られる。
    expect(payload.service_minutes).toBe(60);
    expect(payload.time_type).toBe('時間帯');
    expect(payload.preferred_start).toBe('10:00');
    expect(payload.preferred_end).toBe('11:00');
    expect(payload.preferred_weekdays).toEqual(['Mon', 'Fri']);
    expect(payload.requires_multiple_staff).toBe(true);
    expect(payload.sex_restriction).toBe('female_only');
  });

  it('新規モードでは existing_patient_id を送らない (null)', () => {
    renderSheet();
    // 新規モード (既定) で住所だけ入れて提案。
    fireEvent.change(screen.getByPlaceholderText(/千葉市稲毛区小仲台/), {
      target: { value: '千葉市中央区1-1-1' },
    });
    fireEvent.click(screen.getByText('自動提案する'));

    expect(mutate).toHaveBeenCalledTimes(1);
    const payload = mutate.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.existing_patient_id).toBeNull();
    expect(payload.lat).toBeNull();
    expect(payload.lng).toBeNull();
    expect(payload.address).toBe('千葉市中央区1-1-1');
  });

  it('[変更] で紐付けを解除できる', () => {
    renderSheet();
    fireEvent.click(screen.getByText('既存のお客様'));
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '青柳' },
    });
    fireEvent.click(screen.getByText('青柳 あい'));
    expect(screen.getByText('青柳 あい 様 を紐付け中')).toBeInTheDocument();
    // [変更] で解除 → 検索ボックスに戻る。
    fireEvent.click(screen.getByText('変更'));
    expect(screen.queryByText('青柳 あい 様 を紐付け中')).not.toBeInTheDocument();
    expect(screen.getByLabelText('既存のお客様を検索')).toBeInTheDocument();
  });
});
