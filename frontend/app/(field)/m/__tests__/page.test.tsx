/**
 * /m 現場ボード (Phase2-3c 実データ接続) — レンダーテスト.
 *
 * 実 API 接続後の描画検証。board / propose-slots / pending / patient の各フックを
 * モックし、ボード本体・カルテ・提案シート・承認モードの描画を確認する。
 *
 * カバー:
 *   1. manager セッションでボードが描画される (ヘッダー + コース)
 *   2. DEMO チップは撤去済み (実データのため非表示)
 *   3. 実訪問が実時刻 (start_time〜end_time) + filled/6 + 空き枠で表示される
 *   4. 患者カード click → カルテシートが開く (患者詳細の実 API 値)
 *   5. 「提案」ボタン click → 提案シートが開く
 *   6. ローディング中はスピナー文言を出す
 *   7. staff セッションでもボードを閲覧できる (編集系 UI は非表示)
 *   8. レイアウト切替 UI (radio) は無い
 *   9. 全拠点を 1 ボードに 稲毛→都賀 / code 順で結合表示する (拠点プルダウン廃止)
 *  10. 曜日ヘッダーの訪問/空き集計が全拠点合算になる
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import type { BoardResponse } from '@/lib/schemas/v2/board';
import type { PatientRead } from '@/lib/schemas/patient';

const mockReplace = vi.fn();

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
  // R-8: MobileSurfaceSwitcher (共通切替ピルバー) が usePathname を使う。
  usePathname: () => '/m',
}));

vi.mock('@/lib/queries/fieldBoard', async () => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type FieldBoardModule = typeof import('@/lib/queries/fieldBoard');
  const actual = await vi.importActual<FieldBoardModule>('@/lib/queries/fieldBoard');
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
  useCreatePendingRequest: vi.fn(),
}));

vi.mock('@/lib/queries/patients', () => ({
  usePatient: vi.fn(),
  usePatients: vi.fn(),
  useUpdatePatient: vi.fn(),
  useCreatePatient: vi.fn(),
}));

// KarteSheet の「NGスタッフ」行 (patient-ng-staff-design.md §8-2 Phase 2)。
// 未モックだと QueryClientProvider 無しの本テスト環境で例外になる。
vi.mock('@/lib/queries/patient_ng_staff', () => ({
  useNgStaffList: () => ({ data: [], isLoading: false, isError: false, error: null }),
}));

vi.mock('@/lib/queries/geocoding', () => ({
  useGeocode: vi.fn(),
}));

vi.mock('@/lib/queries/offices', () => ({
  useResolveOffice: vi.fn(),
  // 0059: FieldBoard は sort_order / short_label をマスタから引く (旧直書き定数を撤去)。
  // backfill 相当の値 (稲毛=1/稲, 都賀=2/津) を返し、順序・トークンの現挙動を保つ。
  useOffices: () => ({
    allOffices: [
      { id: 'o-inage', name: '稲毛', sort_order: 1, short_label: '稲' },
      { id: 'o-tsuga', name: '都賀', sort_order: 2, short_label: '津' },
    ],
    isLoading: false,
  }),
}));

// Phase G-88 で FieldBoard に追加された営業時間設定フック。未モックだと
// QueryClientProvider 無しの本テスト環境で例外になる (フォールバック枠で十分)。
vi.mock('@/lib/queries/schedulingSettings', () => ({
  useSchedulingSettings: () => ({ data: undefined, isLoading: false, isError: false }),
}));

import { useSession } from 'next-auth/react';
import { useFieldBoard, useProposeSlots } from '@/lib/queries/fieldBoard';
import {
  usePendingRequests,
  useApproveRequest,
  useRejectRequest,
  useCreatePendingRequest,
} from '@/lib/queries/pending_requests';
import {
  usePatient,
  usePatients,
  useUpdatePatient,
  useCreatePatient,
} from '@/lib/queries/patients';
import { useGeocode } from '@/lib/queries/geocoding';
import { useResolveOffice } from '@/lib/queries/offices';
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

const OFFICE2_ID = '11111111-1111-1111-1111-111111111112';
const COURSE2_ID = '44444444-4444-4444-4444-444444444445';
const COURSE_INM_ID = '44444444-4444-4444-4444-44444444444a';

/**
 * 2 拠点ボード (全拠点結合表示の検証用)。
 *   - 稲毛 (OFFICE_ID): 月曜に「稲M」「稲A」(あえてコード逆順で投入 → ソート検証)。
 *   - 都賀 (OFFICE2_ID): 月曜に「津A」。
 * 結合 + ソート後の期待並び: 稲A → 稲M → 津A (拠点順 稲毛→都賀 → code 順 A..M)。
 * 各コース 1 訪問 (filled=1) なので、月曜ヘッダー集計は全拠点合算で訪問 3 件になる。
 */
function makeTwoOfficeBoard(): BoardResponse {
  const base = makeBoard();
  base.offices = [
    { office_id: OFFICE_ID, office_name: '稲毛' },
    { office_id: OFFICE2_ID, office_name: '都賀' },
  ];
  // 稲毛の月曜に「稲M」を追加 (既存「稲A」より前に投入してソート安定性を見る)。
  base.board[0]!.courses = [
    {
      course_id: COURSE_INM_ID,
      course_code: 'M',
      course_label: '稲M',
      staff_name: '田中 Ns',
      visits: [
        {
          visit_id: '88888888-8888-8888-8888-888888888888',
          patient_id: PATIENT_ID,
          patient_name: '移動 三郎',
          patient_kana: null,
          insurance: 'care' as const,
          service_minutes: 60,
          start_time: '13:00',
          end_time: '14:00',
          address: '千葉市稲毛区小仲台6-2-9',
          lat: null,
          lng: null,
          same_address_group_id: null,
          mode: 'normal' as const,
          slot_index: 0,
        },
      ],
      capacity: { filled: 1, max: 6, total_minutes: 60, remaining: 5 },
    },
    ...base.board[0]!.courses, // 既存「稲A」
  ];
  base.board[0]!.patient_count = 2;
  // 都賀ぶんの 7 曜日セルを追加 (月曜のみコース「津A」あり)。
  for (let weekday = 0; weekday < 7; weekday++) {
    base.board.push({
      office_id: OFFICE2_ID,
      weekday,
      weekday_code: base.weekdays[weekday]!.weekday_code,
      closed: false,
      staff_count: 1,
      manager_count: 0,
      patient_count: weekday === 0 ? 1 : 0,
      courses:
        weekday === 0
          ? [
              {
                course_id: COURSE2_ID,
                course_code: 'A',
                course_label: '津A',
                staff_name: '鈴木 Ns',
                visits: [
                  {
                    visit_id: '99999999-9999-9999-9999-999999999999',
                    patient_id: PATIENT_ID,
                    patient_name: '都賀 患者',
                    patient_kana: null,
                    insurance: 'med' as const,
                    service_minutes: 60,
                    start_time: '09:30',
                    end_time: '10:30',
                    address: '千葉市若葉区都賀1-1-1',
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
  }
  return base;
}

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
    primary_office_id: OFFICE_ID,
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
  (useCreatePendingRequest as unknown as ReturnType<typeof vi.fn>).mockReturnValue(noMutation());
  (usePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: makePatient(),
    isLoading: false,
    isError: false,
  });
  (usePatients as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items: [], total: 0, page: 1, limit: 8, truncated: false },
    isLoading: false,
    isFetching: false,
    isError: false,
  });
  (useUpdatePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue(noMutation());
  (useCreatePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue(noMutation());
  (useGeocode as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  });
  (useResolveOffice as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  });
});

describe('/m 現場ボード (実データ)', () => {
  it('manager セッションでボードを描画し、DEMO チップは出さない', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    // ブランドヘッダーは撤去済み — トップバー (戻る/受け入れ枠) がボードの目印。
    expect(screen.getByLabelText('画面切替')).toBeInTheDocument();
    // R-8: 受け入れ枠は共通切替バーのタブになった。
    expect(screen.getByText('受け入れ枠')).toBeInTheDocument();
    // 実データのため DEMO チップは撤去済み。
    expect(screen.queryByText('DEMO')).not.toBeInTheDocument();
    // 拠点プルダウンは廃止。拠点は薄い小見出しとして表示され (稲毛)、月曜のコースが並ぶ。
    expect(screen.getByText('稲毛')).toBeInTheDocument();
    expect(screen.getByText('稲A')).toBeInTheDocument();
    expect(screen.getByText('月曜')).toBeInTheDocument();
    // 拠点プルダウン (トリガ button) は存在しない。
    expect(screen.queryByRole('button', { name: /拠点を選択/ })).not.toBeInTheDocument();
  });

  it('実訪問が実時刻 + 容量 + 空き帯 (≥60分・開始時刻表示) で表示される', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    // 実時刻 (start_time〜end_time)。
    expect(screen.getByText('09:30〜10:30')).toBeInTheDocument();
    // 容量 filled/max。
    expect(screen.getByText(/1\/6/)).toBeInTheDocument();
    // 空き帯: 営業枠 09:30–12:00 / 13:00–18:00 から 09:30〜10:30 の visit を除いた
    // ≥60分 の 2 帯 (AM 10:30〜12:00=90分, PM 13:00〜18:00=300分) を各々のカードで表示。
    const empties = screen.getAllByText(/この時間空いてますよ：/);
    expect(empties).toHaveLength(2);
    expect(screen.getByText('10:30〜12:00')).toBeInTheDocument();
    expect(screen.getByText('13:00〜18:00')).toBeInTheDocument();
    // 旧仕様「空きN個」まとめ表示は廃止。
    expect(screen.queryByText(/空き\d+個/)).not.toBeInTheDocument();
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

  it('staff セッションでもボードを閲覧できる (リダイレクトしない・編集ボタンは出ない)', () => {
    setSession('staff');
    render(<FieldBoardPage />);
    expect(mockReplace).not.toHaveBeenCalledWith('/m/home');
    expect(screen.getByLabelText('画面切替')).toBeInTheDocument();
    // 編集系 (提案・承認・患者管理) は staff には出ない。
    expect(screen.queryByRole('button', { name: /提案/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /承認/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '患者の登録・編集' })).not.toBeInTheDocument();
  });

  it('全拠点が 1 ボードに 稲毛→都賀 / code 順 (稲A→稲M→津A) で結合表示される', () => {
    setSession('manager');
    setBoard({ data: makeTwoOfficeBoard() });
    render(<FieldBoardPage />);
    // 拠点プルダウンは無い (廃止)。
    expect(screen.queryByRole('button', { name: /拠点を選択/ })).not.toBeInTheDocument();
    // 全拠点のコースが同時に表示される (切替なし)。
    const inageA = screen.getByText('稲A');
    const inageM = screen.getByText('稲M');
    const tsugaA = screen.getByText('津A');
    expect(inageA).toBeInTheDocument();
    expect(inageM).toBeInTheDocument();
    expect(tsugaA).toBeInTheDocument();
    // 拠点小見出し (稲毛 / 都賀) が各拠点ブロック先頭に出る。
    expect(screen.getByText('稲毛')).toBeInTheDocument();
    expect(screen.getByText('都賀')).toBeInTheDocument();
    // DOM 文書順 = 描画順: 稲A → 稲M → 津A (拠点順 稲毛→都賀 → code 順 A..M)。
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(inageA.compareDocumentPosition(inageM) & FOLLOWING).toBeTruthy();
    expect(inageM.compareDocumentPosition(tsugaA) & FOLLOWING).toBeTruthy();
    // 稲毛小見出しは稲A より前、都賀小見出しは津A より前。
    const inageHead = screen.getByText('稲毛');
    const tsugaHead = screen.getByText('都賀');
    expect(inageHead.compareDocumentPosition(inageA) & FOLLOWING).toBeTruthy();
    expect(tsugaHead.compareDocumentPosition(tsugaA) & FOLLOWING).toBeTruthy();
  });

  it('曜日ヘッダーの訪問/空き集計が全拠点合算になる', () => {
    setSession('manager');
    setBoard({ data: makeTwoOfficeBoard() });
    render(<FieldBoardPage />);
    // 月曜: 稲A(filled1,remaining5) + 稲M(filled1,remaining5) + 津A(filled1,remaining5)。
    // 全拠点合算で 訪問 3 件・空き 15 つ。
    // 訪問件数は DayStepper 内で「訪問 <b>3</b> 件」と表示される。<b> 要素で件数を特定。
    const visitCountBold = screen
      .getAllByText('3')
      .find((el) => el.tagName === 'B' && el.parentElement?.textContent?.includes('訪問'));
    expect(visitCountBold).toBeDefined();
    expect(screen.getByText('空き 15つ')).toBeInTheDocument();
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

  it('60分未満の空き帯は表示せず、≥60分のみ「この時間空いてますよ：HH:MM〜HH:MM」で出す', () => {
    setSession('manager');
    setBoard({ data: makeSameAddressBoard() });
    render(<FieldBoardPage />);
    // 占有 09:30〜10:30 / 11:00〜12:00 を営業枠から除いた空き gap:
    //   AM 10:30〜11:00 (30分) → <60分 なので非表示。
    //   PM 13:00〜18:00 (300分) → ≥60分 なので 1 帯表示。
    const empties = screen.getAllByText(/この時間空いてますよ：/);
    expect(empties).toHaveLength(1);
    expect(screen.getByText('13:00〜18:00')).toBeInTheDocument();
    // 60分未満 (10:30〜11:00) の空き帯は出ない。
    expect(screen.queryByText('10:30〜11:00')).not.toBeInTheDocument();
    // 旧「空きN個」まとめ表示は廃止。
    expect(screen.queryByText(/空き\d+個/)).not.toBeInTheDocument();
  });
});

// ============================ 空き帯の精緻化 (≥60分・interleave) ============================

const COURSE_GAP_ID = '77777777-7777-7777-7777-777777777777';

/**
 * 月曜に 1 コース。訪問は 11:00〜12:00 のみ。
 *   AM: 09:30〜11:00 (90分) → ≥60分 → 訪問より前 (先頭) に空き帯。
 *   PM: 13:00〜18:00 (300分) → ≥60分 → 訪問より後に空き帯。
 * 空き帯が「開始時刻順」で訪問と正しく interleave されることを検証する。
 */
function makeInterleaveBoard(): BoardResponse {
  const base = makeBoard();
  const monCell = base.board[0]!;
  monCell.courses = [
    {
      course_id: COURSE_GAP_ID,
      course_code: 'A',
      course_label: '稲A',
      staff_name: '佐藤 Ns',
      visits: [v('bbbbbbbb-0000-0000-0000-000000000001', '昼前 太郎', '11:00', '12:00', null, 0)],
      capacity: { filled: 1, max: 6, total_minutes: 60, remaining: 5 },
    },
  ];
  return base;
}

/** 月曜に満杯コース (AM/PM を埋め切り、空き帯ゼロ)。 */
function makeFullBoard(): BoardResponse {
  const base = makeBoard();
  const monCell = base.board[0]!;
  monCell.courses = [
    {
      course_id: COURSE_GAP_ID,
      course_code: 'A',
      course_label: '稲A',
      staff_name: '佐藤 Ns',
      visits: [
        v('cccccccc-0000-0000-0000-000000000001', '満杯 一郎', '09:30', '12:00', null, 0),
        v('cccccccc-0000-0000-0000-000000000002', '満杯 二郎', '13:00', '18:00', null, 1),
      ],
      capacity: { filled: 2, max: 6, total_minutes: 450, remaining: 4 },
    },
  ];
  return base;
}

/** 月曜に空コース (訪問ゼロ)。AM/PM が各 ≥60分 → 2 帯。 */
function makeEmptyCourseBoard(): BoardResponse {
  const base = makeBoard();
  const monCell = base.board[0]!;
  monCell.courses = [
    {
      course_id: COURSE_GAP_ID,
      course_code: 'A',
      course_label: '稲A',
      staff_name: '佐藤 Ns',
      visits: [],
      capacity: { filled: 0, max: 6, total_minutes: 0, remaining: 6 },
    },
  ];
  return base;
}

describe('/m 現場ボード 空き帯の精緻化', () => {
  it('空き帯が開始時刻順で訪問と正しく interleave される (前: AM空き → 訪問 → 後: PM空き)', () => {
    setSession('manager');
    setBoard({ data: makeInterleaveBoard() });
    render(<FieldBoardPage />);
    // 3 要素: AM空き帯 (09:30〜11:00) / 訪問カード (昼前 太郎 11:00〜12:00) / PM空き帯 (13:00〜18:00)。
    const amGap = screen.getByText('09:30〜11:00');
    const visit = screen.getByText('11:00〜12:00');
    const pmGap = screen.getByText('13:00〜18:00');
    // DOM 文書順 = 描画順。空き帯はその開始時刻位置に挿入される。
    // a が b より前なら compareDocumentPosition に DOCUMENT_POSITION_FOLLOWING(=4) が立つ。
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(amGap.compareDocumentPosition(visit) & FOLLOWING).toBeTruthy();
    expect(visit.compareDocumentPosition(pmGap) & FOLLOWING).toBeTruthy();
    // 空き帯は 2 つ。
    expect(screen.getAllByText(/この時間空いてますよ：/)).toHaveLength(2);
  });

  it('満杯コースでは空き帯を表示しない', () => {
    setSession('manager');
    setBoard({ data: makeFullBoard() });
    render(<FieldBoardPage />);
    expect(screen.queryByText(/この時間空いてますよ：/)).not.toBeInTheDocument();
  });

  it('空コースでは AM / PM の 2 帯を表示する', () => {
    setSession('manager');
    setBoard({ data: makeEmptyCourseBoard() });
    render(<FieldBoardPage />);
    // AM 09:30〜12:00 (150分) / PM 13:00〜18:00 (300分)。
    expect(screen.getAllByText(/この時間空いてますよ：/)).toHaveLength(2);
    expect(screen.getByText('09:30〜12:00')).toBeInTheDocument();
    expect(screen.getByText('13:00〜18:00')).toBeInTheDocument();
  });
});

// ============================ 頭数(capacity)ゲート ============================
//
// 「この時間空いてますよ」カードは capacity.remaining(=6−filled, 最大6) でゲートする。
//   - filled>=6 (remaining<=0): 時間的に空き帯(≥60分)があっても一切出さない (空きなし)。
//   - filled<6 (remaining>0): 従来通り ≥60分 空き帯を開始時刻順に表示。件数はヘッダ「空きNつ」。

/**
 * 月曜に 1 コース。filled を引数で指定し、capacity.remaining = 6 − filled をセットする。
 * 訪問は 11:00〜12:00 の 1 件のみ (filled の数値とは独立。時間 gap は AM/PM に必ず残る)。
 * → 時間的には ≥60分 の空き帯 (09:30〜11:00 / 13:00〜18:00) が存在する状態。
 */
function makeCapacityBoard(filled: number): BoardResponse {
  const base = makeBoard();
  const monCell = base.board[0]!;
  monCell.courses = [
    {
      course_id: COURSE_GAP_ID,
      course_code: 'A',
      course_label: '稲A',
      staff_name: '佐藤 Ns',
      visits: [v('dddddddd-0000-0000-0000-000000000001', '昼前 太郎', '11:00', '12:00', null, 0)],
      capacity: { filled, max: 6, total_minutes: 60, remaining: 6 - filled },
    },
  ];
  return base;
}

/**
 * 月曜に 1 コース。remaining>0 だが ≥60分 の空き帯は存在しない (AM/PM を実時刻で埋め切る)。
 * → ゲートは通過するが空き帯カードは出ない。ヘッダのみ「空きNつ」。
 */
function makeNoGapButRoomBoard(): BoardResponse {
  const base = makeBoard();
  const monCell = base.board[0]!;
  monCell.courses = [
    {
      course_id: COURSE_GAP_ID,
      course_code: 'A',
      course_label: '稲A',
      staff_name: '佐藤 Ns',
      visits: [
        v('eeeeeeee-0000-0000-0000-000000000001', '埋め 一郎', '09:30', '12:00', null, 0),
        v('eeeeeeee-0000-0000-0000-000000000002', '埋め 二郎', '13:00', '18:00', null, 1),
      ],
      capacity: { filled: 4, max: 6, total_minutes: 450, remaining: 2 },
    },
  ];
  return base;
}

describe('/m 現場ボード 頭数ゲート', () => {
  it('filled>=6 (remaining=0): 時間gapがあっても空き帯カード非表示・ヘッダ「空きなし」', () => {
    setSession('manager');
    setBoard({ data: makeCapacityBoard(6) });
    render(<FieldBoardPage />);
    // 時間的には空き帯があるが、頭数ゲートで一切出さない。
    expect(screen.queryByText(/この時間空いてますよ：/)).not.toBeInTheDocument();
    // コースヘッダは満員表記。
    expect(screen.getByText(/6\/6 空きなし/)).toBeInTheDocument();
  });

  it('filled=4 (remaining=2): ヘッダ「空き2つ」・≥60分空き帯カードを表示', () => {
    setSession('manager');
    setBoard({ data: makeCapacityBoard(4) });
    render(<FieldBoardPage />);
    // ヘッダ件数。
    expect(screen.getByText(/4\/6 空き2つ/)).toBeInTheDocument();
    // 時間 gap (09:30〜11:00 / 13:00〜18:00) の 2 帯を表示。
    expect(screen.getAllByText(/この時間空いてますよ：/)).toHaveLength(2);
    expect(screen.getByText('09:30〜11:00')).toBeInTheDocument();
    expect(screen.getByText('13:00〜18:00')).toBeInTheDocument();
  });

  it('filled=5 (remaining=1): ヘッダ「空き1つ」', () => {
    setSession('manager');
    setBoard({ data: makeCapacityBoard(5) });
    render(<FieldBoardPage />);
    expect(screen.getByText(/5\/6 空き1つ/)).toBeInTheDocument();
    // remaining>0 なので空き帯カードは出る (時間 gap あり)。
    expect(screen.getAllByText(/この時間空いてますよ：/).length).toBeGreaterThanOrEqual(1);
  });

  it('remaining>0 だが ≥60分空き帯が無い: カード無しでもヘッダは「空きNつ」', () => {
    setSession('manager');
    setBoard({ data: makeNoGapButRoomBoard() });
    render(<FieldBoardPage />);
    // 時間 gap が無いので空き帯カードは出ない。
    expect(screen.queryByText(/この時間空いてますよ：/)).not.toBeInTheDocument();
    // だが頭数の空きはあるのでヘッダは「空き2つ」。
    expect(screen.getByText(/4\/6 空き2つ/)).toBeInTheDocument();
  });
});

// ============================ カルテ閲覧拡充 + 編集 (KarteSheet) ============================
//
// 実装1: 閲覧の情報拡充 (ステータス/性別制限/複数スタッフ/主担当拠点/週訪問回数/日曜)。
// 実装2: 編集機能 (manager/admin のみ編集ボタン・staff は閲覧専用・PATCH 呼び出し)。

/** 患者カードを開いてカルテシートを表示する (月曜「青柳 あい」)。 */
function openKarte() {
  fireEvent.click(screen.getByText('青柳 あい'));
}

describe('/m カルテ 閲覧の情報拡充', () => {
  it('新しい閲覧項目 (ステータス/性別制限/複数スタッフ/主担当拠点/週訪問回数/訪問頻度) を表示する', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    openKarte();
    // ステータス (admitted → 入院中)。
    expect(screen.getByText('ステータス')).toBeInTheDocument();
    expect(screen.getByText('入院中')).toBeInTheDocument();
    // 性別制限 (female_only → 女性のみ)。
    expect(screen.getByText('性別制限')).toBeInTheDocument();
    expect(screen.getByText('女性のみ')).toBeInTheDocument();
    // 複数スタッフ必須 (true → はい)。
    expect(screen.getByText('複数スタッフ必須')).toBeInTheDocument();
    expect(screen.getByText('はい')).toBeInTheDocument();
    // 主担当拠点 (primary_office_id → 稲毛)。基本情報内に拠点名。
    expect(screen.getByText('主担当拠点')).toBeInTheDocument();
    // 週訪問回数 / 訪問頻度。
    expect(screen.getByText('週訪問回数')).toBeInTheDocument();
    expect(screen.getByText('3回 / 週')).toBeInTheDocument();
    expect(screen.getByText('訪問頻度')).toBeInTheDocument();
    expect(screen.getByText('隔週')).toBeInTheDocument();
  });

  it('希望曜日に日曜 (日) を含む 月〜日 7 日を表示する', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    openKarte();
    // 希望曜日セクションに「日」ラベルが出る (7 日表示)。preferred_weekdays に Sun 含む。
    expect(screen.getByText('希望曜日')).toBeInTheDocument();
    expect(screen.getByText('日')).toBeInTheDocument();
  });
});

describe('/m カルテ 編集ボタンの認可', () => {
  it('manager では編集ボタンを表示する', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    openKarte();
    expect(screen.getByRole('button', { name: 'カルテを編集' })).toBeInTheDocument();
  });

  it('admin でも編集ボタンを表示する', () => {
    setSession('admin');
    render(<FieldBoardPage />);
    openKarte();
    expect(screen.getByRole('button', { name: 'カルテを編集' })).toBeInTheDocument();
  });

  // staff はボードを閲覧できるが、カルテは閲覧専用 (編集ボタンを出さない)。
  it('staff ではカルテを開けるが「カルテを編集」ボタンは出ない', () => {
    setSession('staff');
    render(<FieldBoardPage />);
    openKarte();
    // カルテシートが開いた (ボードカード + シート見出しで氏名が 2 箇所に出る)。
    expect(screen.getAllByText('青柳 あい').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole('button', { name: 'カルテを編集' })).not.toBeInTheDocument();
  });
});

describe('/m カルテ 編集 → 保存', () => {
  it('編集ボタン → 保存で useUpdatePatient(PATCH) が正しい値で呼ばれる', async () => {
    setSession('manager');
    const mutate = vi.fn();
    (useUpdatePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate,
      isPending: false,
      isError: false,
      data: undefined,
    });

    render(<FieldBoardPage />);
    openKarte();

    // 編集モードへ。
    fireEvent.click(screen.getByRole('button', { name: 'カルテを編集' }));

    // 編集フォームが出る (氏名フィールドに初期値)。
    const nameInput = screen.getByLabelText('氏名') as HTMLInputElement;
    expect(nameInput.value).toBe('青柳 あい');

    // 氏名を変更して保存。
    fireEvent.change(nameInput, { target: { value: '青柳 あい (更新)' } });
    fireEvent.click(screen.getByRole('button', { name: /カルテを保存/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    const submitted = mutate.mock.calls[0][0] as Record<string, unknown>;
    // 主要フィールドが期待値で渡る。
    expect(submitted.name).toBe('青柳 あい (更新)');
    expect(submitted.code).toBe('P-1042');
    expect(submitted.status).toBe('admitted');
    expect(submitted.sex_restriction).toBe('female_only');
    expect(submitted.requires_multiple_staff).toBe(true);
    // weekly_pattern (frequency/visit_frequency/preferred_weekdays) が保持される。
    const wp = submitted.weekly_pattern as Record<string, unknown>;
    expect(wp.frequency_per_week).toBe(3);
    expect(wp.visit_frequency).toBe('biweekly');
    expect(wp.preferred_weekdays).toEqual(['Mon', 'Fri', 'Sun']);
  });

  it('住所未変更なら保存時にジオコード・拠点解決を呼ばない (best-effort)', async () => {
    setSession('manager');
    const mutate = vi.fn();
    const geocodeAsync = vi.fn();
    const resolveAsync = vi.fn();
    (useUpdatePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate,
      isPending: false,
      isError: false,
      data: undefined,
    });
    (useGeocode as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      mutateAsync: geocodeAsync,
      isPending: false,
    });
    (useResolveOffice as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      mutateAsync: resolveAsync,
      isPending: false,
    });

    render(<FieldBoardPage />);
    openKarte();
    fireEvent.click(screen.getByRole('button', { name: 'カルテを編集' }));
    fireEvent.click(screen.getByRole('button', { name: /カルテを保存/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(geocodeAsync).not.toHaveBeenCalled();
    expect(resolveAsync).not.toHaveBeenCalled();
  });
});

// ============================ 患者管理ボタン (Phase G-87) ============================
//
// 提案を介さない新規登録 + 検索編集ハブ (PatientManageSheet) の入口。
// ヘッダ「患者」ボタンは manager/admin (canEditKarte) のみ表示し、押下で
// PatientManageSheet を開く。staff はボード閲覧のみ (ボタン非表示)。

describe('/m ヘッダ 患者ボタン (Phase G-87)', () => {
  it('manager では「患者」ボタンを表示する', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    expect(screen.getByRole('button', { name: '患者の登録・編集' })).toBeInTheDocument();
  });

  it('admin でも「患者」ボタンを表示する', () => {
    setSession('admin');
    render(<FieldBoardPage />);
    expect(screen.getByRole('button', { name: '患者の登録・編集' })).toBeInTheDocument();
  });

  it('staff ではボードは見えるが患者ボタンは出ない', () => {
    setSession('staff');
    render(<FieldBoardPage />);
    expect(screen.getByLabelText('画面切替')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '患者の登録・編集' })).not.toBeInTheDocument();
  });

  it('「患者」ボタン押下で患者管理シート (新規登録 + 検索) が開く', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    fireEvent.click(screen.getByRole('button', { name: '患者の登録・編集' }));
    // 患者管理シート: 新規登録ボタン + 検索ボックス。
    expect(screen.getByText('＋ 新規患者を登録')).toBeInTheDocument();
    expect(screen.getByLabelText('既存のお客様を検索')).toBeInTheDocument();
  });
});
