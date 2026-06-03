/**
 * SuggestSheet 新規カルテ登録 (StageB) + カバレッジ表示 (StageC) — 振る舞いテスト.
 *
 * カバー:
 *   StageC カバレッジ:
 *     1. coverage サマリ「希望 週N日 → M/N日 提案可」を出す
 *     2. per_day を ○/× 曜日チップで出す (×=赤系)
 *     3. fully_covered で「全日OK」バッジ
 *   StageB 新規カルテ:
 *     4. 新規モードでカルテ項目入力欄 (氏名/コード/カナ/性別/保険) が出る
 *     5. 採用枠を選んで「提案を作成（承認へ）」で pending patient_create を作成し、
 *        payload に karte 項目 + weekly_pattern + proposed_visits が入る
 *     6. 氏名/コード未入力 or 採用枠ゼロでは作成ボタンが無効
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type {
  ProposeSlotsResponse,
  ProposeSlotItem,
  ProposeCoverage,
} from '@/lib/schemas/v2/propose_slots';

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

vi.mock('@/lib/queries/pending_requests', () => ({
  useCreatePendingRequest: vi.fn(),
}));

import { useProposeSlots } from '@/lib/queries/fieldBoard';
import { usePatients } from '@/lib/queries/patients';
import { useCreatePendingRequest } from '@/lib/queries/pending_requests';
import { SuggestSheet } from '@/components/field/FieldSheets';

const OFFICE_ID = '11111111-1111-1111-1111-111111111111';

function slot(over: Partial<ProposeSlotItem>): ProposeSlotItem {
  return {
    office_id: OFFICE_ID,
    office_name: '稲毛',
    weekday: 0,
    weekday_code: 'Mon',
    course_code: 'A',
    course_label: '稲A',
    staff_name: '佐藤 Ns',
    start_time: '10:00',
    end_time: '11:00',
    score: 90,
    reasons: [],
    warnings: [],
    is_pair: false,
    pair_partner: null,
    mini_schedule: [],
    ...over,
  } as ProposeSlotItem;
}

const monSlot = slot({ weekday: 0, weekday_code: 'Mon', start_time: '10:00', end_time: '11:00' });
const friSlot = slot({
  weekday: 4,
  weekday_code: 'Fri',
  course_code: 'B',
  course_label: '稲B',
  start_time: '14:00',
  end_time: '15:00',
});

function coverage(over: Partial<ProposeCoverage> = {}): ProposeCoverage {
  return {
    required_days: 2,
    requested_weekdays: [0, 4],
    per_day: [
      { weekday: 0, weekday_code: 'Mon', has_slot: true, best_slot: monSlot },
      { weekday: 4, weekday_code: 'Fri', has_slot: true, best_slot: friSlot },
    ],
    covered_days: 2,
    fully_covered: true,
    ...over,
  };
}

function response(over: Partial<ProposeSlotsResponse> = {}): ProposeSlotsResponse {
  return {
    iso_year: 2026,
    iso_week: 23,
    candidate_lat: null,
    candidate_lng: null,
    resolved_office_id: null,
    slots: [monSlot, friSlot],
    coverage: coverage(),
    message: null,
    ...over,
  };
}

const createMutate = vi.fn();

function setPropose(data: ProposeSlotsResponse | undefined) {
  (useProposeSlots as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    data,
  });
}

function renderSheet() {
  return render(<SuggestSheet isoYear={2026} isoWeek={23} onClose={vi.fn()} onToast={vi.fn()} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  createMutate.mockReset();
  setPropose(response());
  (usePatients as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items: [], total: 0, page: 1, limit: 8, truncated: false },
    isLoading: false,
    isFetching: false,
    isError: false,
  });
  (useCreatePendingRequest as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: createMutate,
    isPending: false,
    isError: false,
    data: undefined,
  });
});

describe('StageC カバレッジ表示', () => {
  it('カバレッジサマリ「希望 週N日 → M/N日 提案可」を表示する', () => {
    renderSheet();
    expect(screen.getByText('希望 週2日 → 2/2日 提案可')).toBeInTheDocument();
  });

  it('fully_covered なら「全日OK」バッジを出す', () => {
    renderSheet();
    expect(screen.getByText('全日OK')).toBeInTheDocument();
  });

  it('per_day を ○/× 曜日チップで表示する (未充足は ×)', () => {
    setPropose(
      response({
        coverage: coverage({
          per_day: [
            { weekday: 0, weekday_code: 'Mon', has_slot: true, best_slot: monSlot },
            { weekday: 4, weekday_code: 'Fri', has_slot: false, best_slot: null },
          ],
          covered_days: 1,
          fully_covered: false,
        }),
        slots: [monSlot],
      }),
    );
    renderSheet();
    expect(screen.getByText('希望 週2日 → 1/2日 提案可')).toBeInTheDocument();
    // ○ (Mon) と × (Fri) の両方が aria-label つきチップで出る。
    expect(screen.getByLabelText('月曜 提案あり')).toBeInTheDocument();
    expect(screen.getByLabelText('金曜 提案なし')).toBeInTheDocument();
    // 未充足なので全日OK バッジは出ない。
    expect(screen.queryByText('全日OK')).not.toBeInTheDocument();
  });
});

describe('StageB 新規カルテ登録', () => {
  it('新規モードでカルテ入力欄 (氏名/コード/カナ/性別/保険) が出る', () => {
    renderSheet();
    expect(screen.getByLabelText('氏名')).toBeInTheDocument();
    expect(screen.getByLabelText('患者コード')).toBeInTheDocument();
    expect(screen.getByLabelText('フリガナ')).toBeInTheDocument();
    expect(screen.getByLabelText('性別')).toBeInTheDocument();
    expect(screen.getByLabelText('保険')).toBeInTheDocument();
  });

  it('既存モードに切り替えるとカルテ入力欄は消える', () => {
    renderSheet();
    fireEvent.click(screen.getByText('既存のお客様'));
    expect(screen.queryByLabelText('氏名')).not.toBeInTheDocument();
  });

  it('カルテ入力 + 採用枠選択 → pending patient_create を payload 付きで作成', () => {
    renderSheet();
    // カルテ項目。
    fireEvent.change(screen.getByLabelText('氏名'), { target: { value: '青柳 あい' } });
    fireEvent.change(screen.getByLabelText('患者コード'), { target: { value: 'P-1042' } });
    fireEvent.change(screen.getByLabelText('フリガナ'), { target: { value: 'アオヤギ アイ' } });
    fireEvent.change(screen.getByLabelText('性別'), { target: { value: 'female' } });
    fireEvent.change(screen.getByLabelText('保険'), { target: { value: 'medical' } });
    // 住所 (既存欄)。
    fireEvent.change(screen.getByPlaceholderText(/千葉市稲毛区小仲台/), {
      target: { value: '千葉市稲毛区小仲台6-2-1' },
    });

    // 月曜の枠を採用 (RankCard の採用ボタン)。
    fireEvent.click(screen.getByText('月曜 この枠を採用'));
    // 金曜の枠も採用。
    fireEvent.click(screen.getByText('金曜 この枠を採用'));

    // 作成。
    fireEvent.click(screen.getByText('提案を作成（承認へ）'));

    expect(createMutate).toHaveBeenCalledTimes(1);
    const arg = createMutate.mock.calls[0]![0] as {
      request_type: string;
      payload: Record<string, unknown>;
    };
    expect(arg.request_type).toBe('patient_create');
    const p = arg.payload;
    expect(p.name).toBe('青柳 あい');
    expect(p.code).toBe('P-1042');
    expect(p.kana).toBe('アオヤギ アイ');
    expect(p.sex).toBe('female');
    expect(p.insurance).toBe('medical');
    expect(p.address).toBe('千葉市稲毛区小仲台6-2-1');
    expect(p.status).toBe('active');

    // weekly_pattern (希望) が入る。
    const wp = p.weekly_pattern as Record<string, unknown>;
    expect(wp).toBeTruthy();
    expect(wp.frequency_per_week).toBeDefined();

    // proposed_visits: 採用した 2 枠が weekday 昇順で積まれる。
    const pv = p.proposed_visits as Array<Record<string, unknown>>;
    expect(pv).toHaveLength(2);
    expect(pv[0]!.weekday).toBe(0);
    expect(pv[0]!.start_time).toBe('10:00');
    expect(pv[0]!.duration_min).toBe(60);
    expect(pv[0]!.course_code).toBe('A');
    expect(pv[1]!.weekday).toBe(4);
    expect(pv[1]!.start_time).toBe('14:00');
    expect(pv[1]!.course_code).toBe('B');
  });

  it('採用枠ゼロでは作成しない (ボタン無効でクリックしても mutate されない)', () => {
    renderSheet();
    fireEvent.change(screen.getByLabelText('氏名'), { target: { value: '青柳 あい' } });
    fireEvent.change(screen.getByLabelText('患者コード'), { target: { value: 'P-1042' } });
    // 採用せずに作成ボタン (無効) を押す。
    fireEvent.click(screen.getByText('提案を作成（承認へ）'));
    expect(createMutate).not.toHaveBeenCalled();
  });

  it('採用枠を再タップで解除できる (採用中表示 → 採用に戻る)', () => {
    renderSheet();
    fireEvent.click(screen.getByText('月曜 この枠を採用'));
    expect(screen.getByText('月曜 この枠を採用中')).toBeInTheDocument();
    fireEvent.click(screen.getByText('月曜 この枠を採用中'));
    expect(screen.getByText('月曜 この枠を採用')).toBeInTheDocument();
  });
});

describe('複数スタッフ注記 (Major#3)', () => {
  const NOTE = '※ 2名体制の空き判定は未対応です（1名前提の提案）';

  it('複数スタッフ OFF の初期状態では注記を出さない', () => {
    renderSheet();
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });

  it('複数スタッフ ON にすると結果付近に注記を出す', () => {
    renderSheet();
    // 「2名以上での訪問が必要」トグルを ON。
    fireEvent.click(screen.getByText('2名以上での訪問が必要'));
    expect(screen.getByText(NOTE)).toBeInTheDocument();
  });

  it('複数スタッフ ON でも propose 結果が無ければ注記は出さない (結果付近のみ)', () => {
    setPropose(undefined);
    renderSheet();
    fireEvent.click(screen.getByText('2名以上での訪問が必要'));
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });
});
