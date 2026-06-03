/**
 * /m 現場ボード (Phase2-3c 実データ接続) — レンダーテスト.
 *
 * 実 API 接続後の描画検証。board / propose-slots / pending / patient の各フックを
 * モックし、ボード本体・カルテ・提案シート・承認モードの描画を確認する。
 *
 * カバー:
 *   1. manager セッションでボードが描画される (ヘッダー + 拠点タブ + コース)
 *   2. DEMO チップは撤去済み (実データのため非表示)
 *   3. 実訪問が実時刻 (start_time〜end_time) + filled/6 + 空き枠で表示される
 *   4. 患者カード click → カルテシートが開く (患者詳細の実 API 値)
 *   5. 「提案」ボタン click → 提案シートが開く
 *   6. ローディング中はスピナー文言を出す
 *   7. staff セッションでは /m/home へリダイレクトし、ボードを描画しない
 *   8. レイアウト切替 UI (radio) は無い
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { BoardResponse } from '@/lib/schemas/v2/board';
import type { PatientRead } from '@/lib/schemas/patient';

const mockReplace = vi.fn();

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
}));

vi.mock('@/lib/queries/fieldBoard', async () => {
  const actual = await vi.importActual<typeof import('@/lib/queries/fieldBoard')>(
    '@/lib/queries/fieldBoard',
  );
  return {
    ...actual,
    useFieldBoard: vi.fn(),
    useProposeSlots: vi.fn(),
  };
});

vi.mock('@/lib/queries/pending_requests', () => ({
  usePendingRequests: vi.fn(),
  useApproveRequest: vi.fn(),
  useRejectRequest: vi.fn(),
}));

vi.mock('@/lib/queries/patients', () => ({
  usePatient: vi.fn(),
}));

import { useSession } from 'next-auth/react';
import { useFieldBoard, useProposeSlots } from '@/lib/queries/fieldBoard';
import {
  usePendingRequests,
  useApproveRequest,
  useRejectRequest,
} from '@/lib/queries/pending_requests';
import { usePatient } from '@/lib/queries/patients';
import FieldBoardPage from '../page';

type Role = 'admin' | 'manager' | 'staff';

const OFFICE_ID = '11111111-1111-1111-1111-111111111111';
const VISIT_ID = '22222222-2222-2222-2222-222222222222';
const PATIENT_ID = '33333333-3333-3333-3333-333333333333';
const COURSE_ID = '44444444-4444-4444-4444-444444444444';

function makeBoard(): BoardResponse {
  const emptyCell = (weekday: number, code: BoardResponse['board'][number]['weekday_code']) => ({
    office_id: OFFICE_ID,
    weekday,
    weekday_code: code,
    closed: false,
    staff_count: 1,
    manager_count: 0,
    patient_count: weekday === 0 ? 1 : 0,
    courses:
      weekday === 0
        ? [
            {
              course_id: COURSE_ID,
              course_code: 'A',
              course_label: '稲A',
              staff_name: '佐藤 Ns',
              visits: [
                {
                  visit_id: VISIT_ID,
                  patient_id: PATIENT_ID,
                  patient_name: '青柳 あい',
                  patient_kana: 'アオヤギ アイ',
                  insurance: 'med' as const,
                  service_minutes: 60,
                  start_time: '09:30',
                  end_time: '10:30',
                  address: '千葉市稲毛区小仲台6-2-1',
                  lat: null,
                  lng: null,
                  same_address_group_id: null,
                  mode: 'normal' as const,
                  slot_index: 0,
                },
              ],
              capacity: { filled: 1, max: 6, total_minutes: 60, remaining: 5 },
            },
          ]
        : [],
  });
  return {
    iso_year: 2026,
    iso_week: 23,
    course_max: 6,
    offices: [{ office_id: OFFICE_ID, office_name: '稲毛' }],
    weekdays: [
      { weekday: 0, weekday_code: 'Mon', date: '2026-06-01', patient_count: 1 },
      { weekday: 1, weekday_code: 'Tue', date: '2026-06-02', patient_count: 0 },
      { weekday: 2, weekday_code: 'Wed', date: '2026-06-03', patient_count: 0 },
      { weekday: 3, weekday_code: 'Thu', date: '2026-06-04', patient_count: 0 },
      { weekday: 4, weekday_code: 'Fri', date: '2026-06-05', patient_count: 0 },
      { weekday: 5, weekday_code: 'Sat', date: '2026-06-06', patient_count: 0 },
      { weekday: 6, weekday_code: 'Sun', date: '2026-06-07', patient_count: 0 },
    ],
    board: [
      emptyCell(0, 'Mon'),
      emptyCell(1, 'Tue'),
      emptyCell(2, 'Wed'),
      emptyCell(3, 'Thu'),
      emptyCell(4, 'Fri'),
      emptyCell(5, 'Sat'),
      emptyCell(6, 'Sun'),
    ],
  };
}

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
    note: '玄関の鍵は植木鉢の下。',
    weekly_pattern: {
      frequency_per_week: 2,
      preferred_weekdays: ['Mon', 'Fri'],
      service_minutes: 60,
      time_type: '終日',
    },
    special_week_active: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  } as unknown as PatientRead;
}

function setSession(role: Role | null) {
  (useSession as unknown as ReturnType<typeof vi.fn>).mockReturnValue(
    role === null
      ? { data: null, status: 'unauthenticated' }
      : { data: { user: { role } }, status: 'authenticated' },
  );
}

function setBoard(
  state: Partial<{ data: BoardResponse; isLoading: boolean; isError: boolean }> = {},
) {
  (useFieldBoard as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: state.data,
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
    refetch: vi.fn(),
  });
}

function noMutation() {
  return { mutate: vi.fn(), isPending: false, isError: false, data: undefined };
}

beforeEach(() => {
  vi.clearAllMocks();
  setBoard({ data: makeBoard() });
  (useProposeSlots as unknown as ReturnType<typeof vi.fn>).mockReturnValue(noMutation());
  (usePendingRequests as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items: [], total: 0, limit: 100, offset: 0 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
  (useApproveRequest as unknown as ReturnType<typeof vi.fn>).mockReturnValue(noMutation());
  (useRejectRequest as unknown as ReturnType<typeof vi.fn>).mockReturnValue(noMutation());
  (usePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: makePatient(),
    isLoading: false,
    isError: false,
  });
});

describe('/m 現場ボード (実データ)', () => {
  it('manager セッションでボードを描画し、DEMO チップは出さない', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    expect(screen.getByText('CareFlow')).toBeInTheDocument();
    expect(screen.getByText('現場ボード')).toBeInTheDocument();
    // 実データのため DEMO チップは撤去済み。
    expect(screen.queryByText('DEMO')).not.toBeInTheDocument();
    // 拠点タブ (offices[] から構築) と月曜のコース。
    expect(screen.getByText('稲毛')).toBeInTheDocument();
    expect(screen.getByText('稲A')).toBeInTheDocument();
    expect(screen.getByText('月曜')).toBeInTheDocument();
  });

  it('実訪問が実時刻 + 容量 + 空き枠で表示される', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    // 実時刻 (start_time〜end_time)。
    expect(screen.getByText('09:30〜10:30')).toBeInTheDocument();
    // 容量 filled/max。
    expect(screen.getByText(/1\/6/)).toBeInTheDocument();
    // 空き枠 (remaining): 「この時間空いてますよ：空きN個」。
    expect(screen.getByText(/この時間空いてますよ：空き5個/)).toBeInTheDocument();
    // 空き時間帯 (営業枠 09:30–12:00 / 13:00–18:00 から 09:30〜10:30 の visit を除く)。
    expect(screen.getByText(/10:30〜12:00 \/ 13:00〜18:00/)).toBeInTheDocument();
  });

  it('患者カード click でカルテシートが開く (患者詳細の実 API 値)', () => {
    setSession('admin');
    render(<FieldBoardPage />);
    fireEvent.click(screen.getByText('青柳 あい'));
    expect(screen.getByText(/患者コード: P-1042/)).toBeInTheDocument();
  });

  it('「提案」ボタンで提案シートが開く', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    fireEvent.click(screen.getByText('提案'));
    expect(screen.getByText('自動提案する')).toBeInTheDocument();
    expect(screen.getByText('新規訪問の提案')).toBeInTheDocument();
  });

  it('ローディング中はスピナー文言を出す', () => {
    setSession('manager');
    setBoard({ data: undefined, isLoading: true });
    render(<FieldBoardPage />);
    expect(screen.getByText('ボードを読み込んでいます…')).toBeInTheDocument();
  });

  it('レイアウト切替 UI (radio) は無い', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(screen.queryByText('週全体')).not.toBeInTheDocument();
  });

  it('staff セッションでは /m/home へリダイレクトし、ボードを描画しない', () => {
    setSession('staff');
    render(<FieldBoardPage />);
    expect(mockReplace).toHaveBeenCalledWith('/m/home');
    expect(screen.queryByText('現場ボード')).not.toBeInTheDocument();
  });
});

// ============================ 同住所連続 (3点修正) ============================

const GROUP_ID = '55555555-5555-5555-5555-555555555555';
const COURSE_C_ID = '66666666-6666-6666-6666-666666666666';

function v(id: string, name: string, start: string, end: string, gid: string | null, slot: number) {
  return {
    visit_id: id,
    patient_id: PATIENT_ID,
    patient_name: name,
    patient_kana: null,
    insurance: 'med' as const,
    service_minutes: 60,
    start_time: start,
    end_time: end,
    address: '千葉市稲毛区小仲台6-2-1',
    lat: null,
    lng: null,
    same_address_group_id: gid,
    mode: 'normal' as const,
    slot_index: slot,
  };
}

/**
 * 月曜に 1 コース (Cコース)。同じ same_address_group_id を持つ 3 visit:
 *   - A/B は 09:30 開始 (同時刻 → 連結)
 *   - C は 11:00 開始 (別時刻 → 連結しない、通常カード)
 */
function makeSameAddressBoard(): BoardResponse {
  const base = makeBoard();
  const monCell = base.board[0]!;
  monCell.courses = [
    {
      course_id: COURSE_C_ID,
      course_code: 'C',
      course_label: '稲C',
      staff_name: '佐藤 Ns',
      visits: [
        v('aaaaaaaa-0000-0000-0000-000000000001', '同時 太郎', '09:30', '10:30', GROUP_ID, 0),
        v('aaaaaaaa-0000-0000-0000-000000000002', '同時 花子', '09:30', '10:30', GROUP_ID, 1),
        v('aaaaaaaa-0000-0000-0000-000000000003', '別時 次郎', '11:00', '12:00', GROUP_ID, 2),
      ],
      capacity: { filled: 3, max: 6, total_minutes: 180, remaining: 3 },
    },
  ];
  return base;
}

describe('/m 現場ボード 同住所連続', () => {
  it('同住所でも開始時刻が異なれば連結しない (同時刻の 2 名のみ連続バンド)', () => {
    setSession('manager');
    setBoard({ data: makeSameAddressBoard() });
    render(<FieldBoardPage />);
    // 同時刻 (09:30) の 2 名は「同住所・同時」連続バンドにまとまる。
    // 「同住所・同時」は連続バンド見出しと DayStepper 凡例の両方に出るため getAllByText で検証。
    expect(screen.getAllByText('同住所・同時').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/09:30〜10:30 同時刻・連続訪問（約60分）/)).toBeInTheDocument();
    // 全 3 名が描画される (別時刻の次郎は通常カードとして単独表示)。
    expect(screen.getByText('同時 太郎')).toBeInTheDocument();
    expect(screen.getByText('同時 花子')).toBeInTheDocument();
    expect(screen.getByText('別時 次郎')).toBeInTheDocument();
  });

  it('空き枠は「この時間空いてますよ：空きN個」+ 空き時間帯を表示する', () => {
    setSession('manager');
    setBoard({ data: makeSameAddressBoard() });
    render(<FieldBoardPage />);
    // remaining=3。
    expect(screen.getByText(/この時間空いてますよ：空き3個/)).toBeInTheDocument();
    // 占有 09:30〜10:30 / 11:00〜12:00 を営業枠から除いた空き gap。
    // AM: 10:30〜11:00 (30分) / PM: 13:00〜18:00。12:00 開始の空きは無し。
    expect(screen.getByText(/10:30〜11:00 \/ 13:00〜18:00/)).toBeInTheDocument();
  });
});
