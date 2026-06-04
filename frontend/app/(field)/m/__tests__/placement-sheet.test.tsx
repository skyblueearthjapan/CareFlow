/**
 * PlacementSheet (Phase G-84 空き枠への直接配置) — 振る舞いテスト.
 *
 * カバー:
 *   1. 枠ヘッダ (拠点/コース/曜日/gap) を出す
 *   2. 開始時刻ステッパーが gap 範囲にクランプされる
 *   3. 既存患者を選んで「配置を申請」で patient_visit_add を payload 付きで作成
 *   4. 赤警告 (容量超過) では強行理由が無いと申請されない、入れれば申請できる
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import type { SlotPlacementContext } from '@/lib/field/patientCreate';

vi.mock('@/lib/queries/fieldBoard', async () => {
  const actual = await vi.importActual<typeof import('@/lib/queries/fieldBoard')>(
    '@/lib/queries/fieldBoard',
  );
  return { ...actual, useTravelEstimate: vi.fn() };
});

vi.mock('@/lib/queries/patients', () => ({
  usePatients: vi.fn(),
  usePatient: vi.fn(),
  useUpdatePatient: vi.fn(),
}));

vi.mock('@/lib/queries/pending_requests', () => ({
  useCreatePendingRequest: vi.fn(),
}));

import { useTravelEstimate } from '@/lib/queries/fieldBoard';
import { usePatients } from '@/lib/queries/patients';
import { useCreatePendingRequest } from '@/lib/queries/pending_requests';
import { PlacementSheet } from '@/components/field/FieldSheets';

function ctx(over: Partial<SlotPlacementContext> = {}): SlotPlacementContext {
  return {
    officeId: 'office-1',
    officeName: '稲毛',
    courseId: 'course-1',
    courseCode: '稲A',
    courseLabel: '稲A',
    weekday: 0,
    gapStartMin: 10 * 60, // 10:00
    gapEndMin: 11 * 60, // 11:00
    prevVisit: null,
    nextVisit: null,
    capacityRemaining: 3,
    existingVisits: [],
    ...over,
  };
}

const createMutate = vi.fn();
const travelMutate = vi.fn();

function renderSheet(over: Partial<SlotPlacementContext> = {}) {
  return render(
    <PlacementSheet
      ctx={ctx(over)}
      isoYear={2026}
      isoWeek={23}
      onClose={vi.fn()}
      onToast={vi.fn()}
    />,
  );
}

/** usePatients に候補1件を仕込んで既存モードで選択するヘルパ。 */
function mockOnePatient() {
  (usePatients as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: {
      items: [
        {
          id: 'pid-1',
          code: 'P-1',
          name: '田中 太郎',
          kana: 'タナカ タロウ',
          address: '千葉市稲毛区1-1',
          lat: 35.6,
          lng: 140.1,
          weekly_pattern: {},
        },
      ],
      total: 1,
      page: 1,
      limit: 8,
      truncated: false,
    },
    isLoading: false,
    isFetching: false,
    isError: false,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  createMutate.mockReset();
  (useTravelEstimate as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: travelMutate,
    isPending: false,
    isError: false,
    data: undefined,
  });
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

describe('PlacementSheet ヘッダ / 開始時刻', () => {
  it('枠ヘッダ (拠点 / コース / 曜日 / gap) を表示する', () => {
    renderSheet();
    // gap ラベルは独立テキストで一意。
    expect(screen.getByText('空き 10:00〜11:00')).toBeInTheDocument();
    // 拠点 / コース / 曜日 はヘッダ行に含まれる (テキスト断片が混在するため部分一致)。
    expect(screen.getByText(/稲毛/)).toBeInTheDocument();
    expect(screen.getAllByText(/稲A/).length).toBeGreaterThan(0);
  });

  it('初期開始時刻は枠頭 (10:00)。＋で5分進み、範囲端で止まる', () => {
    renderSheet();
    // 初期表示 10:00。
    expect(screen.getByText('10:00')).toBeInTheDocument();
    // 既定の所要 35分なので上限は 11:00 - 35 = 10:25。
    const up = screen.getByLabelText('開始を遅らせる');
    fireEvent.click(up); // 10:05
    fireEvent.click(up); // 10:10
    fireEvent.click(up); // 10:15
    fireEvent.click(up); // 10:20
    fireEvent.click(up); // 10:25 (上限)
    expect(screen.getByText('10:25')).toBeInTheDocument();
    // さらに押しても 10:25 のまま (クランプ)。
    fireEvent.click(up);
    expect(screen.getByText('10:25')).toBeInTheDocument();
  });
});

describe('PlacementSheet travel-estimate 由来の初期開始時刻 (案2: ceil+clamp)', () => {
  it('prevVisit あり + travel total_minutes 解決で開始時刻が prevEnd+total を5分切上げ', () => {
    vi.useFakeTimers();
    try {
      // travelMut.mutate(req, {onSuccess}) が total_minutes=22 で成功する mock。
      travelMutate.mockImplementation(
        (_req: unknown, opts?: { onSuccess?: (r: { total_minutes: number | null }) => void }) => {
          opts?.onSuccess?.({ total_minutes: 22 });
        },
      );
      // 直前訪問 09:50 終了、gap 10:00〜11:00。新規モードで住所を入れると to_address で見積もる。
      renderSheet({
        prevVisit: { patientId: 'p-prev', patientName: '前 太郎', endTime: '09:50' },
      });
      // 初期は枠頭 10:00。
      expect(screen.getByText('10:00')).toBeInTheDocument();
      // 住所を入力 (最小5文字ゲートを満たす) → デバウンス後に travel-estimate 発火。
      fireEvent.change(screen.getByLabelText('ご住所'), {
        target: { value: '千葉市稲毛区小仲台6-2-1' },
      });
      // 500ms デバウンスを進めて mutate を発火させる (state 更新を act で包む)。
      act(() => {
        vi.advanceTimersByTime(600);
      });
      // prevEnd 09:50(590) + 22 = 612 → 5分切上げ 615 = 10:15。gap 内なのでそのまま採用。
      expect(screen.getByText('10:15')).toBeInTheDocument();
      expect(travelMutate).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('mutation in-flight 中に手動でステッパー操作すると onSuccess が開始時刻を上書きしない', () => {
    vi.useFakeTimers();
    try {
      // onSuccess を即時呼ばず捕捉しておき、手動操作後に遅延解決させる。
      let resolveTravel: ((r: { total_minutes: number | null }) => void) | undefined;
      travelMutate.mockImplementation(
        (_req: unknown, opts?: { onSuccess?: (r: { total_minutes: number | null }) => void }) => {
          resolveTravel = opts?.onSuccess;
        },
      );
      renderSheet({
        prevVisit: { patientId: 'p-prev', patientName: '前 太郎', endTime: '09:50' },
      });
      expect(screen.getByText('10:00')).toBeInTheDocument();
      // 住所入力 → デバウンス経過で mutate 発火 (onSuccess は捕捉のみ、未解決)。
      fireEvent.change(screen.getByLabelText('ご住所'), {
        target: { value: '千葉市稲毛区小仲台6-2-1' },
      });
      act(() => {
        vi.advanceTimersByTime(600);
      });
      expect(travelMutate).toHaveBeenCalledTimes(1);
      expect(resolveTravel).toBeTypeOf('function');
      // in-flight 中にユーザーが手動で開始時刻を 10:05 に調整 (startTouched=true)。
      fireEvent.click(screen.getByLabelText('開始を遅らせる'));
      expect(screen.getByText('10:05')).toBeInTheDocument();
      // ここで遅延していた onSuccess を解決 (total=22 → 本来なら 10:15 に上書き)。
      act(() => {
        resolveTravel?.({ total_minutes: 22 });
      });
      // 手動操作が尊重され、上書きされず 10:05 のまま。
      expect(screen.getByText('10:05')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('住所が短い (5文字未満) 間は travel-estimate を発火しない', () => {
    vi.useFakeTimers();
    try {
      renderSheet({
        prevVisit: { patientId: 'p-prev', patientName: '前 太郎', endTime: '09:50' },
      });
      fireEvent.change(screen.getByLabelText('ご住所'), { target: { value: '千葉市' } });
      act(() => {
        vi.advanceTimersByTime(600);
      });
      expect(travelMutate).not.toHaveBeenCalled();
      // 開始時刻は枠頭のまま。
      expect(screen.getByText('10:00')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('PlacementSheet 既存患者の訪問追加', () => {
  it('既存患者を選んで配置を申請すると patient_visit_add を payload 付きで作成', () => {
    (usePatients as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          {
            id: 'pid-1',
            code: 'P-1',
            name: '田中 太郎',
            kana: 'タナカ タロウ',
            address: '千葉市稲毛区1-1',
            lat: 35.6,
            lng: 140.1,
            weekly_pattern: {},
          },
        ],
        total: 1,
        page: 1,
        limit: 8,
        truncated: false,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
    });
    renderSheet();
    // 既存モードへ。
    fireEvent.click(screen.getByText('既存のお客様'));
    // 検索 → 候補選択。
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), {
      target: { value: '田中' },
    });
    fireEvent.click(screen.getByText('田中 太郎'));
    // 申請。
    fireEvent.click(screen.getByText('この枠に配置を申請（承認へ）'));

    expect(createMutate).toHaveBeenCalledTimes(1);
    const arg = createMutate.mock.calls[0]![0] as {
      request_type: string;
      payload: Record<string, unknown>;
    };
    expect(arg.request_type).toBe('patient_visit_add');
    expect(arg.payload.patient_id).toBe('pid-1');
    expect(arg.payload.patient_name).toBe('田中 太郎');
    const pv = arg.payload.proposed_visits as Array<Record<string, unknown>>;
    expect(pv).toHaveLength(1);
    expect(pv[0]!.weekday).toBe(0);
    expect(pv[0]!.start_time).toBe('10:00');
    expect(pv[0]!.course_code).toBe('稲A');
    expect(arg.payload.placement).toBeTruthy();
    expect(arg.payload.override_reason).toBeNull();
    // Phase G-85: 既定は恒常 (毎週固定)。course_id/iso は載せない。
    expect(arg.payload.scope).toBe('permanent');
    expect('course_id' in arg.payload).toBe(false);
    expect('iso_year' in arg.payload).toBe(false);
  });
});

describe('PlacementSheet 配置方法トグル (Phase G-85)', () => {
  it('新規モードでは「配置方法」トグルを出さない (恒常のみ)', () => {
    renderSheet();
    expect(screen.queryByText('配置方法')).not.toBeInTheDocument();
  });

  it('既存モードでは「配置方法」トグルを出す', () => {
    mockOnePatient();
    renderSheet();
    fireEvent.click(screen.getByText('既存のお客様'));
    expect(screen.getByText('配置方法')).toBeInTheDocument();
    expect(screen.getByText('毎週 固定')).toBeInTheDocument();
    expect(screen.getByText('この週だけ')).toBeInTheDocument();
  });

  it('「この週だけ」選択で one_time payload に course_id/iso を同梱して申請', () => {
    mockOnePatient();
    renderSheet();
    fireEvent.click(screen.getByText('既存のお客様'));
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), { target: { value: '田中' } });
    fireEvent.click(screen.getByText('田中 太郎'));
    // 単発トグルへ切替。
    fireEvent.click(screen.getByText('この週だけ'));
    fireEvent.click(screen.getByText('この枠に配置を申請（承認へ）'));

    expect(createMutate).toHaveBeenCalledTimes(1);
    const arg = createMutate.mock.calls[0]![0] as {
      request_type: string;
      payload: Record<string, unknown>;
    };
    expect(arg.request_type).toBe('patient_visit_add');
    expect(arg.payload.scope).toBe('one_time');
    expect(arg.payload.course_id).toBe('course-1');
    expect(arg.payload.iso_year).toBe(2026);
    expect(arg.payload.iso_week).toBe(23);
  });

  it('ctx.courseId == null の枠では単発を無効化 (恒常で申請)', () => {
    mockOnePatient();
    renderSheet({ courseId: null });
    fireEvent.click(screen.getByText('既存のお客様'));
    fireEvent.change(screen.getByLabelText('既存のお客様を検索'), { target: { value: '田中' } });
    fireEvent.click(screen.getByText('田中 太郎'));
    // 単発ボタンは disabled。クリックしても scope は変わらない。
    const oneTimeBtn = screen.getByText('この週だけ').closest('button');
    expect(oneTimeBtn).toBeDisabled();
    fireEvent.click(screen.getByText('この週だけ'));
    fireEvent.click(screen.getByText('この枠に配置を申請（承認へ）'));

    const arg = createMutate.mock.calls[0]![0] as {
      request_type: string;
      payload: Record<string, unknown>;
    };
    expect(arg.payload.scope).toBe('permanent');
    expect('course_id' in arg.payload).toBe(false);
  });
});

describe('PlacementSheet 赤警告 (容量超過) の強行', () => {
  it('赤警告では理由が無いと申請されず、理由を入れると申請できる', () => {
    renderSheet({ capacityRemaining: 0 });
    // 新規カルテ入力。
    fireEvent.change(screen.getByLabelText('氏名'), { target: { value: '新患 一郎' } });
    fireEvent.change(screen.getByLabelText('患者コード'), { target: { value: 'P-NEW' } });

    // 理由なしで強行ボタンを押しても作成されない (disabled)。
    fireEvent.click(screen.getByText('警告を承知して配置を申請'));
    expect(createMutate).not.toHaveBeenCalled();

    // 強行理由を入力 → 申請できる。
    fireEvent.change(screen.getByLabelText('強行理由'), {
      target: { value: '管理者判断で増枠' },
    });
    fireEvent.click(screen.getByText('警告を承知して配置を申請'));
    expect(createMutate).toHaveBeenCalledTimes(1);
    const arg = createMutate.mock.calls[0]![0] as {
      request_type: string;
      payload: Record<string, unknown>;
    };
    expect(arg.request_type).toBe('patient_create');
    expect(arg.payload.override_reason).toBe('管理者判断で増枠');
    const warnings = arg.payload.warnings as Array<Record<string, unknown>>;
    expect(warnings.some((w) => w.code === 'capacity_full')).toBe(true);
  });
});
