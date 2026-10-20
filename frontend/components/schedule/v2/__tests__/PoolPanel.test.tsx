/**
 * PoolPanel — Wave 18 Phase B-3 / B-4 + W37 Phase 3-B テスト.
 *
 * - poolGroupKey / poolGroupLabel pure-function unit tests (B-3)
 * - PoolGroupedByWeekday: 希望曜日別グループ化 + 希望なしは末尾 (B-3)
 * - formatPreferredTimeLabel: WeeklyPattern → ラベル変換 (B-4)
 * - parsePoolDraggableId / buildPoolDraggableId helper (W37 Phase 3-B)
 * - 複数スタッフ対応患者は slot 0/1 の 2 カードに分かれる (W37 Phase 3-B)
 * - 片 slot 配置済みなら未配置 slot のカードのみ残る (W37 Phase 3-B)
 * - グループヘッダに「内 2 名対応 N 名」サブカウントが出る (W37 Phase 3-B)
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@dnd-kit/core', () => ({
  useDroppable: () => ({ isOver: false, setNodeRef: vi.fn() }),
}));

vi.mock('lucide-react', () => ({
  Inbox: () => <span aria-hidden />,
}));

vi.mock('@/components/ui/card', () => ({
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import {
  PoolGroupedByWeekday,
  poolGroupKey,
  poolGroupLabel,
  buildPoolDraggableId,
  parsePoolDraggableId,
  type PoolCardSlotInfo,
} from '../PoolPanel';
import { formatPreferredTimeLabel, emptyWeeklyPattern } from '@/lib/schemas/patient';
import type { PatientRead } from '@/lib/schemas/patient';
import {
  augmentAssignedSlotsWithAccompaniment,
  buildAccompanimentLinkIndex,
  type FulfillmentVisit,
} from '@/lib/scheduling/accompanimentFulfillment';
import type { TraineeAccompanimentItem } from '@/lib/schemas/trainee_accompaniment';

describe('poolGroupKey / poolGroupLabel (B-3)', () => {
  it('preferred_weekdays が空 → __none__ / "希望なし"', () => {
    expect(poolGroupKey([])).toBe('__none__');
    expect(poolGroupKey(null)).toBe('__none__');
    expect(poolGroupKey(undefined)).toBe('__none__');
    expect(poolGroupLabel('__none__')).toBe('希望なし');
  });

  it('単曜日 [Mon] → "Mon" / "月希望"', () => {
    expect(poolGroupKey(['Mon'])).toBe('Mon');
    expect(poolGroupLabel('Mon')).toBe('月希望');
  });

  it('複数曜日 [Fri, Mon, Wed] → canonical 順で結合 / "月・水・金 希望"', () => {
    expect(poolGroupKey(['Fri', 'Mon', 'Wed'])).toBe('Mon,Wed,Fri');
    expect(poolGroupLabel('Mon,Wed,Fri')).toBe('月・水・金 希望');
  });
});

describe('formatPreferredTimeLabel (B-4)', () => {
  it('time_type=固定 + preferred_start → "09:30 (固定)"', () => {
    expect(
      formatPreferredTimeLabel({
        ...emptyWeeklyPattern,
        time_type: '固定',
        preferred_start: '09:30',
      }),
    ).toBe('09:30 (固定)');
  });

  it('time_type=時間帯 + start/end → "09:30〜10:00 (時間帯)"', () => {
    expect(
      formatPreferredTimeLabel({
        ...emptyWeeklyPattern,
        time_type: '時間帯',
        preferred_start: '09:30',
        preferred_end: '10:00',
      }),
    ).toBe('09:30〜10:00 (時間帯)');
  });

  it('time_type=午前 → "午前"', () => {
    expect(formatPreferredTimeLabel({ ...emptyWeeklyPattern, time_type: '午前' })).toBe('午前');
  });

  it('time_type=終日 で start/end なし → "終日"', () => {
    expect(formatPreferredTimeLabel({ ...emptyWeeklyPattern, time_type: '終日' })).toBe('終日');
  });

  it('null/undefined → null', () => {
    expect(formatPreferredTimeLabel(null)).toBeNull();
    expect(formatPreferredTimeLabel(undefined)).toBeNull();
  });
});

describe('PoolGroupedByWeekday (B-3)', () => {
  function makePatient(
    id: string,
    name: string,
    pattern: Partial<{ preferred_weekdays: string[] }> = {},
  ): PatientRead {
    return {
      id,
      code: id,
      name,
      kana: null,
      sex: null,
      status: 'active',
      insurance: null,
      address: null,
      lat: null,
      lng: null,
      primary_office_id: null,
      sex_restriction: null,
      note: null,
      weekly_pattern: { ...pattern },
      special_weekly_pattern: null,
      special_week_active: [],
      created_at: '',
      updated_at: '',
      deleted_at: null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;
  }

  it('希望曜日別にセクション化され、希望なしは末尾に表示', () => {
    const patients: PatientRead[] = [
      makePatient('p1', '田中', { preferred_weekdays: ['Mon'] }),
      makePatient('p2', '佐藤', { preferred_weekdays: ['Mon', 'Wed', 'Fri'] }),
      makePatient('p3', '鈴木', { preferred_weekdays: [] }),
      makePatient('p4', '山田', { preferred_weekdays: ['Tue'] }),
    ];
    render(
      <PoolGroupedByWeekday
        patients={patients}
        renderCard={(p) => <div data-testid={`card-${p.id}`}>{p.name}</div>}
      />,
    );

    // 4 グループ生成 (Mon, Tue, Mon-Wed-Fri, __none__)
    expect(screen.getByTestId('pool-group-Mon')).toBeInTheDocument();
    expect(screen.getByTestId('pool-group-Tue')).toBeInTheDocument();
    expect(screen.getByTestId('pool-group-Mon,Wed,Fri')).toBeInTheDocument();
    expect(screen.getByTestId('pool-group-__none__')).toBeInTheDocument();

    // 各グループ内に該当 patient が描画される
    expect(screen.getByTestId('card-p1')).toHaveTextContent('田中');
    expect(screen.getByTestId('card-p2')).toHaveTextContent('佐藤');
    expect(screen.getByTestId('card-p3')).toHaveTextContent('鈴木');
    expect(screen.getByTestId('card-p4')).toHaveTextContent('山田');

    // 「希望なし」は末尾 (DOM 順): __none__ > Mon,Wed,Fri > Tue > Mon
    const groups = screen.getAllByTestId(/^pool-group-/);
    const lastKey = (groups[groups.length - 1]!.getAttribute('data-testid') ?? '').replace(
      'pool-group-',
      '',
    );
    expect(lastKey).toBe('__none__');
  });

  it('プールが空ならプレースホルダー文を表示', () => {
    render(<PoolGroupedByWeekday patients={[]} renderCard={() => <span />} />);
    expect(screen.getByText(/プールは空です/)).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// W37 Phase 3-B: parsePoolDraggableId / buildPoolDraggableId
// ─────────────────────────────────────────────────────────────────────────

describe('buildPoolDraggableId / parsePoolDraggableId (W37 Phase 3-B)', () => {
  it('slotIndex 未指定 → 旧形式 "pool-patient:{id}"', () => {
    expect(buildPoolDraggableId('p1')).toBe('pool-patient:p1');
    expect(buildPoolDraggableId('p1', null)).toBe('pool-patient:p1');
    expect(buildPoolDraggableId('p1', undefined)).toBe('pool-patient:p1');
  });

  it('slotIndex=0/1 → 新形式 "pool-patient:{id}:slot:0|1"', () => {
    expect(buildPoolDraggableId('p1', 0)).toBe('pool-patient:p1:slot:0');
    expect(buildPoolDraggableId('p1', 1)).toBe('pool-patient:p1:slot:1');
  });

  it('旧形式 "pool-patient:p1" → slotIndex=null (後方互換)', () => {
    expect(parsePoolDraggableId('pool-patient:p1')).toEqual({
      patientId: 'p1',
      slotIndex: null,
    });
  });

  it('UUID も含む旧形式 → slotIndex=null', () => {
    expect(parsePoolDraggableId('pool-patient:99999999-9999-9999-9999-999999999999')).toEqual({
      patientId: '99999999-9999-9999-9999-999999999999',
      slotIndex: null,
    });
  });

  it('新形式 "pool-patient:p1:slot:0" → slotIndex=0', () => {
    expect(parsePoolDraggableId('pool-patient:p1:slot:0')).toEqual({
      patientId: 'p1',
      slotIndex: 0,
    });
  });

  it('新形式 "pool-patient:p1:slot:1" → slotIndex=1', () => {
    expect(parsePoolDraggableId('pool-patient:p1:slot:1')).toEqual({
      patientId: 'p1',
      slotIndex: 1,
    });
  });

  it('UUID + slot サフィックス → 正しく分解', () => {
    expect(
      parsePoolDraggableId('pool-patient:99999999-9999-9999-9999-999999999999:slot:1'),
    ).toEqual({
      patientId: '99999999-9999-9999-9999-999999999999',
      slotIndex: 1,
    });
  });

  it('プレフィックス不一致 (e.g. visit:xxx) → null', () => {
    expect(parsePoolDraggableId('visit:abc')).toBeNull();
    expect(parsePoolDraggableId('something-else')).toBeNull();
    expect(parsePoolDraggableId('')).toBeNull();
  });

  it('round-trip: build → parse で同じ patient/slot を得る', () => {
    for (const pid of ['p1', 'abc-def', '12345']) {
      for (const slot of [null, 0, 1] as const) {
        const built = buildPoolDraggableId(pid, slot);
        const parsed = parsePoolDraggableId(built);
        expect(parsed).toEqual({ patientId: pid, slotIndex: slot });
      }
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────
// W37 Phase 3-B: PoolGroupedByWeekday の 2 カード化挙動
// ─────────────────────────────────────────────────────────────────────────

describe('PoolGroupedByWeekday — multi-staff 2 cards (W37 Phase 3-B)', () => {
  function makeMultiPatient(
    id: string,
    name: string,
    requiresMulti: boolean,
    preferredWeekdays: string[] = ['Mon'],
  ): PatientRead {
    return {
      id,
      code: id,
      name,
      kana: null,
      sex: null,
      status: 'active',
      insurance: null,
      address: null,
      lat: null,
      lng: null,
      primary_office_id: null,
      sex_restriction: null,
      requires_multiple_staff: requiresMulti,
      note: null,
      weekly_pattern: { preferred_weekdays: preferredWeekdays },
      special_weekly_pattern: null,
      special_week_active: [],
      created_at: '',
      updated_at: '',
      deleted_at: null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;
  }

  /**
   * テスト用 renderCard.
   * draggableId と slotIndex を data-* で露出して assertion しやすくする。
   */
  function renderCardForTest(
    p: PatientRead,
    slotInfo: { slotIndex: 0 | 1 | null; partnerAssigned: boolean },
  ) {
    const id = buildPoolDraggableId(p.id, slotInfo.slotIndex);
    return (
      <div
        data-testid={`card-${id}`}
        data-patient-id={p.id}
        data-slot-index={slotInfo.slotIndex === null ? '' : String(slotInfo.slotIndex)}
        data-partner-assigned={slotInfo.partnerAssigned ? '1' : '0'}
      >
        {p.name}
        {slotInfo.slotIndex === 0 ? '①' : slotInfo.slotIndex === 1 ? '②' : ''}
      </div>
    );
  }

  it('通常患者 (requires_multiple_staff=false) → 1 カードのみ描画 (regression)', () => {
    const p = makeMultiPatient('p-norm', '田中', false);
    render(<PoolGroupedByWeekday patients={[p]} renderCard={renderCardForTest} />);

    // 旧形式 ID で 1 枚のみ
    expect(screen.getByTestId('card-pool-patient:p-norm')).toBeInTheDocument();
    expect(screen.queryByTestId('card-pool-patient:p-norm:slot:0')).toBeNull();
    expect(screen.queryByTestId('card-pool-patient:p-norm:slot:1')).toBeNull();
  });

  it('複数スタッフ対応患者 → ① と ② の 2 カードが描画される', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    render(<PoolGroupedByWeekday patients={[p]} renderCard={renderCardForTest} />);

    expect(screen.getByTestId('card-pool-patient:p-multi:slot:0')).toBeInTheDocument();
    expect(screen.getByTestId('card-pool-patient:p-multi:slot:1')).toBeInTheDocument();
    // 旧形式 ID は使われない
    expect(screen.queryByTestId('card-pool-patient:p-multi')).toBeNull();
  });

  it('slot 0 のみ配置済み → プールに ② のみ残り、partnerAssigned=true', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    const assigned = new Map<string, Set<0 | 1>>([['p-multi', new Set<0 | 1>([0])]]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardForTest}
        assignedSlotsByPatient={assigned}
      />,
    );

    expect(screen.queryByTestId('card-pool-patient:p-multi:slot:0')).toBeNull();
    const remaining = screen.getByTestId('card-pool-patient:p-multi:slot:1');
    expect(remaining).toBeInTheDocument();
    expect(remaining.getAttribute('data-partner-assigned')).toBe('1');
  });

  it('slot 1 のみ配置済み → プールに ① のみ残り、partnerAssigned=true', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    const assigned = new Map<string, Set<0 | 1>>([['p-multi', new Set<0 | 1>([1])]]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardForTest}
        assignedSlotsByPatient={assigned}
      />,
    );

    const remaining = screen.getByTestId('card-pool-patient:p-multi:slot:0');
    expect(remaining).toBeInTheDocument();
    expect(remaining.getAttribute('data-partner-assigned')).toBe('1');
    expect(screen.queryByTestId('card-pool-patient:p-multi:slot:1')).toBeNull();
  });

  it('両方配置済み → プールから完全に消える (0 カード)', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    const assigned = new Map<string, Set<0 | 1>>([['p-multi', new Set<0 | 1>([0, 1])]]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardForTest}
        assignedSlotsByPatient={assigned}
      />,
    );

    expect(screen.queryByTestId('card-pool-patient:p-multi:slot:0')).toBeNull();
    expect(screen.queryByTestId('card-pool-patient:p-multi:slot:1')).toBeNull();
    expect(screen.queryByTestId('card-pool-patient:p-multi')).toBeNull();
  });

  it('assignedSlotsByPatient 未指定 (undefined) → 全 slot 未配置として 2 カード描画', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    render(<PoolGroupedByWeekday patients={[p]} renderCard={renderCardForTest} />);
    expect(screen.getByTestId('card-pool-patient:p-multi:slot:0')).toBeInTheDocument();
    expect(screen.getByTestId('card-pool-patient:p-multi:slot:1')).toBeInTheDocument();
  });

  it('partnerAssigned は通常患者 (slotIndex=null) では常に false', () => {
    const p = makeMultiPatient('p-norm', '田中', false);
    // 仮にマップに何かあっても通常患者には影響しない
    const assigned = new Map<string, Set<0 | 1>>([['p-norm', new Set<0 | 1>([0])]]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardForTest}
        assignedSlotsByPatient={assigned}
      />,
    );
    const card = screen.getByTestId('card-pool-patient:p-norm');
    expect(card.getAttribute('data-partner-assigned')).toBe('0');
  });

  it('グループヘッダに "内 2 名対応 N 名" サブカウントが描画される', () => {
    const patients: PatientRead[] = [
      makeMultiPatient('p1', '田中', false, ['Mon']),
      makeMultiPatient('p2', '佐藤', true, ['Mon']), // multi
      makeMultiPatient('p3', '鈴木', true, ['Mon']), // multi
      makeMultiPatient('p4', '山田', false, ['Tue']),
    ];
    render(<PoolGroupedByWeekday patients={patients} renderCard={renderCardForTest} />);

    // Mon グループ: multi 2 名のサブカウント
    const monSub = screen.getByTestId('pool-group-multi-subcount-Mon');
    expect(monSub.textContent).toContain('内 2名対応 2 名');

    // Tue グループ: multi 0 名 → サブカウントなし
    expect(screen.queryByTestId('pool-group-multi-subcount-Tue')).toBeNull();
  });

  it('複数の multi 患者が同じグループにいても、それぞれ ①/② の 2 カードずつ描画される', () => {
    const patients: PatientRead[] = [
      makeMultiPatient('p2', '佐藤', true, ['Mon']),
      makeMultiPatient('p3', '鈴木', true, ['Mon']),
    ];
    render(<PoolGroupedByWeekday patients={patients} renderCard={renderCardForTest} />);

    expect(screen.getByTestId('card-pool-patient:p2:slot:0')).toBeInTheDocument();
    expect(screen.getByTestId('card-pool-patient:p2:slot:1')).toBeInTheDocument();
    expect(screen.getByTestId('card-pool-patient:p3:slot:0')).toBeInTheDocument();
    expect(screen.getByTestId('card-pool-patient:p3:slot:1')).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Wave 38: PoolGroupedByWeekday の partnerLocationByPatientSlot 注入
// ─────────────────────────────────────────────────────────────────────────

describe('PoolGroupedByWeekday — partnerLocationLabel 注入 (Wave 38)', () => {
  function makeMultiPatient(
    id: string,
    name: string,
    requiresMulti: boolean,
    preferredWeekdays: string[] = ['Mon'],
  ): PatientRead {
    return {
      id,
      code: id,
      name,
      kana: null,
      sex: null,
      status: 'active',
      insurance: null,
      address: null,
      lat: null,
      lng: null,
      primary_office_id: null,
      sex_restriction: null,
      requires_multiple_staff: requiresMulti,
      note: null,
      weekly_pattern: { preferred_weekdays: preferredWeekdays },
      special_weekly_pattern: null,
      special_week_active: [],
      created_at: '',
      updated_at: '',
      deleted_at: null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;
  }

  function renderCardCapture(p: PatientRead, info: PoolCardSlotInfo) {
    const id = buildPoolDraggableId(p.id, info.slotIndex);
    return (
      <div
        data-testid={`card-${id}`}
        data-patient-id={p.id}
        data-slot-index={info.slotIndex === null ? '' : String(info.slotIndex)}
        data-partner-assigned={info.partnerAssigned ? '1' : '0'}
        data-partner-location-label={info.partnerLocationLabel ?? ''}
      >
        {p.name}
      </div>
    );
  }

  it('slot 0 配置済み → 残カード (slot 1) に partnerLocationLabel="本店-A 10:00"', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    const assigned = new Map<string, Set<0 | 1>>([['p-multi', new Set<0 | 1>([0])]]);
    // 相方 (slot 0) の location を渡す
    const partnerLocations = new Map<string, string>([['p-multi:0', '本店-A 10:00']]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardCapture}
        assignedSlotsByPatient={assigned}
        partnerLocationByPatientSlot={partnerLocations}
      />,
    );
    const remaining = screen.getByTestId('card-pool-patient:p-multi:slot:1');
    expect(remaining.getAttribute('data-partner-location-label')).toBe('本店-A 10:00');
  });

  it('slot 1 配置済み → 残カード (slot 0) に partnerLocationLabel="都賀-B 14:00"', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    const assigned = new Map<string, Set<0 | 1>>([['p-multi', new Set<0 | 1>([1])]]);
    const partnerLocations = new Map<string, string>([['p-multi:1', '都賀-B 14:00']]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardCapture}
        assignedSlotsByPatient={assigned}
        partnerLocationByPatientSlot={partnerLocations}
      />,
    );
    const remaining = screen.getByTestId('card-pool-patient:p-multi:slot:0');
    expect(remaining.getAttribute('data-partner-location-label')).toBe('都賀-B 14:00');
  });

  it('partnerLocationByPatientSlot 未指定 → label は null/空', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    const assigned = new Map<string, Set<0 | 1>>([['p-multi', new Set<0 | 1>([0])]]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardCapture}
        assignedSlotsByPatient={assigned}
      />,
    );
    const remaining = screen.getByTestId('card-pool-patient:p-multi:slot:1');
    expect(remaining.getAttribute('data-partner-location-label')).toBe('');
  });

  it('partnerAssigned=false (両方未配置) → partnerLocationLabel は null/空', () => {
    const p = makeMultiPatient('p-multi', '鈴木', true);
    // assigned なし = 両方未配置
    const partnerLocations = new Map<string, string>([
      ['p-multi:0', 'should-not-leak'],
      ['p-multi:1', 'should-not-leak'],
    ]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardCapture}
        partnerLocationByPatientSlot={partnerLocations}
      />,
    );
    const slot0 = screen.getByTestId('card-pool-patient:p-multi:slot:0');
    const slot1 = screen.getByTestId('card-pool-patient:p-multi:slot:1');
    expect(slot0.getAttribute('data-partner-location-label')).toBe('');
    expect(slot1.getAttribute('data-partner-location-label')).toBe('');
  });

  it('通常患者 (multi=false) → partnerLocationLabel は常に null/空 (regression)', () => {
    const p = makeMultiPatient('p-norm', '田中', false);
    const partnerLocations = new Map<string, string>([['p-norm:0', 'leak-canary']]);
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardCapture}
        partnerLocationByPatientSlot={partnerLocations}
      />,
    );
    const card = screen.getByTestId('card-pool-patient:p-norm');
    expect(card.getAttribute('data-partner-location-label')).toBe('');
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 新人同行で 2 人目を充足 → ②カードがプールから消える (augment 経由の統合確認)
// ─────────────────────────────────────────────────────────────────────────

describe('PoolGroupedByWeekday — 新人同行による②カード消滅', () => {
  function makeMultiPatient(id: string, name: string): PatientRead {
    return {
      id,
      code: id,
      name,
      kana: null,
      sex: null,
      status: 'active',
      insurance: null,
      address: null,
      lat: null,
      lng: null,
      primary_office_id: null,
      sex_restriction: null,
      requires_multiple_staff: true,
      note: null,
      weekly_pattern: { preferred_weekdays: ['Mon'] },
      special_weekly_pattern: null,
      special_week_active: [],
      created_at: '',
      updated_at: '',
      deleted_at: null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;
  }

  function renderCardForTest(p: PatientRead, info: PoolCardSlotInfo) {
    const id = buildPoolDraggableId(p.id, info.slotIndex);
    return (
      <div data-testid={`card-${id}`} data-patient-id={p.id}>
        {p.name}
      </div>
    );
  }

  // slot 0 だけ配置済み (= ②カードが出る初期状態)。
  const baseAssigned = () =>
    new Map<string, Set<0 | 1>>([['p-multi', new Set<0 | 1>([0])]]);
  // 患者 p-multi は月曜コース c1 (template t1) に単独配置されている。
  const placed = new Map<string, FulfillmentVisit[]>([
    ['p-multi', [{ id: 'v1', courseId: 'c1', courseTemplateId: 't1', weekday: 0 }]],
  ]);

  it('同行リンクなし → ②カードは表示される (regression)', () => {
    const p = makeMultiPatient('p-multi', '鈴木');
    const assigned = augmentAssignedSlotsWithAccompaniment(
      baseAssigned(),
      ['p-multi'],
      placed,
      buildAccompanimentLinkIndex([]),
    );
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardForTest}
        assignedSlotsByPatient={assigned}
      />,
    );
    expect(screen.getByTestId('card-pool-patient:p-multi:slot:1')).toBeInTheDocument();
  });

  it('コースリンクで配置済み訪問が同行つき → ②カードが消える', () => {
    const p = makeMultiPatient('p-multi', '鈴木');
    const links: TraineeAccompanimentItem[] = [
      {
        id: '00000000-0000-0000-0000-000000000901',
        trainee_staff_id: '00000000-0000-0000-0000-000000000001',
        trainee_staff_name: '新人A',
        target_type: 'course',
        source: 'manual',
        course: {
          id: 'c1',
          code: 'INAGE-A',
          weekday: 0,
          template_id: 't1',
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any,
    ];
    const assigned = augmentAssignedSlotsWithAccompaniment(
      baseAssigned(),
      ['p-multi'],
      placed,
      buildAccompanimentLinkIndex(links),
    );
    render(
      <PoolGroupedByWeekday
        patients={[p]}
        renderCard={renderCardForTest}
        assignedSlotsByPatient={assigned}
      />,
    );
    // slot1 が上乗せされ、両 slot 埋まり扱い → カード 0 枚。
    expect(screen.queryByTestId('card-pool-patient:p-multi:slot:0')).toBeNull();
    expect(screen.queryByTestId('card-pool-patient:p-multi:slot:1')).toBeNull();
  });
});
