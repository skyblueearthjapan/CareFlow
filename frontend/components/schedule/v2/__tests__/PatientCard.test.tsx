/**
 * PatientCard — Wave 20 テスト.
 *
 * - 名前表示 (太字)
 * - 希望時間表示 (preferredTimeLabel + serviceMinutes)
 * - 条件バッジ表示: sex_restriction (女性のみ / 男性のみ) / requires_multiple_staff / status=before_start
 * - 条件なし患者ではバッジ表示なし
 * - DnD draggable 機能維持 (useDraggable が呼ばれること)
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// ── dnd-kit mock ─────────────────────────────────────────────────────────────
const mockSetNodeRef = vi.fn();
vi.mock('@dnd-kit/core', () => ({
  useDraggable: vi.fn(({ id }: { id: string }) => ({
    attributes: { 'data-draggable-id': id },
    listeners: {},
    setNodeRef: mockSetNodeRef,
    transform: null,
    isDragging: false,
  })),
}));

vi.mock('@dnd-kit/utilities', () => ({
  CSS: {
    Translate: {
      toString: () => '',
    },
  },
}));

// ── lucide-react mock ─────────────────────────────────────────────────────────
vi.mock('lucide-react', () => ({
  User: () => <span data-testid="icon-user" aria-hidden />,
  Users: () => <span data-testid="icon-users" aria-hidden />,
  Plus: () => <span data-testid="icon-plus" aria-hidden />,
  X: () => <span data-testid="icon-x" aria-hidden />,
}));

// ── Badge mock (最小限) ───────────────────────────────────────────────────────
vi.mock('@/components/ui/badge', () => ({
  Badge: ({
    children,
    className,
    variant,
    ...props
  }: {
    children: React.ReactNode;
    className?: string;
    variant?: string;
    [key: string]: unknown;
  }) => (
    <span data-badge-variant={variant} className={className} {...props}>
      {children}
    </span>
  ),
}));

// ── utils mock ────────────────────────────────────────────────────────────────
vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import { PatientCard, type PatientCardData } from '../PatientCard';
import { useDraggable } from '@dnd-kit/core';

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function makePatient(overrides: Partial<PatientCardData> = {}): PatientCardData {
  return {
    id: 'test-patient-1',
    name: '田中 太郎',
    ...overrides,
  };
}

function renderCard(overrides: Partial<PatientCardData> = {}, draggableId = 'pool:test-patient-1') {
  return render(<PatientCard draggableId={draggableId} patient={makePatient(overrides)} />);
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

describe('PatientCard — Wave 20 (名前・希望時間・条件バッジ)', () => {
  it('患者名が太字で表示される', () => {
    renderCard();
    expect(screen.getByText('田中 太郎')).toBeInTheDocument();
  });

  it('preferredTimeLabel のみ → ラベルがそのまま表示される', () => {
    renderCard({ preferredTimeLabel: '10:00 (固定)' });
    expect(screen.getByTestId('patient-card-preferred-time-test-patient-1')).toHaveTextContent(
      '10:00 (固定)',
    );
  });

  it('preferredTimeLabel + serviceMinutes → "10:00 (固定) / 30分" 形式で表示', () => {
    renderCard({ preferredTimeLabel: '10:00 (固定)', serviceMinutes: 30 });
    expect(screen.getByTestId('patient-card-preferred-time-test-patient-1')).toHaveTextContent(
      '10:00 (固定) / 30分',
    );
  });

  it('preferredTimeLabel が null → 時間テキスト非表示', () => {
    renderCard({ preferredTimeLabel: null });
    expect(
      screen.queryByTestId('patient-card-preferred-time-test-patient-1'),
    ).not.toBeInTheDocument();
  });

  it('sexRestriction=female_only → 「女性のみ」バッジ表示', () => {
    renderCard({ sexRestriction: 'female_only' });
    expect(screen.getByTestId('patient-card-badge-female-only-test-patient-1')).toHaveTextContent(
      '女性のみ',
    );
  });

  it('sexRestriction=male_only → 「男性のみ」バッジ表示', () => {
    renderCard({ sexRestriction: 'male_only' });
    expect(screen.getByTestId('patient-card-badge-male-only-test-patient-1')).toHaveTextContent(
      '男性のみ',
    );
  });

  it('requiresMultipleStaff=true → 「複数」バッジ表示', () => {
    renderCard({ requiresMultipleStaff: true });
    expect(screen.getByTestId('patient-card-badge-multi-test-patient-1')).toHaveTextContent('複数');
  });

  it('patientStatus=before_start → 「新規」バッジ表示', () => {
    renderCard({ patientStatus: 'before_start' });
    expect(screen.getByTestId('patient-card-badge-new-test-patient-1')).toHaveTextContent('新規');
  });

  it('patientStatus=active → 「新規」バッジ非表示', () => {
    renderCard({ patientStatus: 'active' });
    expect(screen.queryByTestId('patient-card-badge-new-test-patient-1')).not.toBeInTheDocument();
  });

  it('条件なし患者 → バッジコンテナ非表示', () => {
    renderCard({ sexRestriction: null, requiresMultipleStaff: false, patientStatus: 'active' });
    expect(screen.queryByTestId('patient-card-badges-test-patient-1')).not.toBeInTheDocument();
  });

  it('複数バッジが同時に表示される (女性のみ + 複数 + 新規)', () => {
    renderCard({
      sexRestriction: 'female_only',
      requiresMultipleStaff: true,
      patientStatus: 'before_start',
    });
    expect(screen.getByTestId('patient-card-badge-female-only-test-patient-1')).toBeInTheDocument();
    expect(screen.getByTestId('patient-card-badge-multi-test-patient-1')).toBeInTheDocument();
    expect(screen.getByTestId('patient-card-badge-new-test-patient-1')).toBeInTheDocument();
  });

  it('compact=true のときバッジ・時間テキストは非表示', () => {
    const { container } = render(
      <PatientCard
        draggableId="pool:test-patient-1"
        patient={makePatient({
          preferredTimeLabel: '10:00 (固定)',
          serviceMinutes: 30,
          sexRestriction: 'female_only',
          requiresMultipleStaff: true,
          patientStatus: 'before_start',
        })}
        compact
      />,
    );
    expect(
      screen.queryByTestId('patient-card-preferred-time-test-patient-1'),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('patient-card-badges-test-patient-1')).not.toBeInTheDocument();
    // 名前は compact でも表示
    expect(screen.getByText('田中 太郎')).toBeInTheDocument();
    void container;
  });

  it('DnD: useDraggable が draggableId と patientId で呼ばれる', () => {
    renderCard({}, 'pool:test-patient-1');
    expect(useDraggable).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'pool:test-patient-1',
        data: expect.objectContaining({ patientId: 'test-patient-1' }),
      }),
    );
  });

  it('DnD: setNodeRef が実行される (DOM に ref が設定される)', () => {
    renderCard();
    expect(mockSetNodeRef).toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Wave 38: 相方の現在地ラベル併記 (PatientCard プール側)
// ─────────────────────────────────────────────────────────────────────────

describe('PatientCard — Wave 38 (相方の現在地併記)', () => {
  it('partnerAssigned=true + partnerLocationLabel あり → "本店-A 10:00" がバッジ横に表示される', () => {
    renderCard({
      slotIndex: 1,
      partnerAssigned: true,
      partnerLocationLabel: '本店-A 10:00',
    });
    // バッジ "① 配置済み" が描画される
    const row = screen.getByTestId('patient-card-partner-assigned-test-patient-1');
    expect(row).toBeInTheDocument();
    // 位置ラベルも併記される
    const loc = screen.getByTestId('patient-card-partner-location-test-patient-1');
    expect(loc).toBeInTheDocument();
    expect(loc.textContent).toBe('本店-A 10:00');
    expect(loc.className).toContain('text-text-muted');
  });

  it('partnerAssigned=true + partnerLocationLabel=null → バッジのみ (位置ラベルは出ない)', () => {
    renderCard({
      slotIndex: 0,
      partnerAssigned: true,
      partnerLocationLabel: null,
    });
    // バッジは存在
    expect(screen.getByTestId('patient-card-partner-assigned-test-patient-1')).toBeInTheDocument();
    // 位置ラベルは出ない
    expect(
      screen.queryByTestId('patient-card-partner-location-test-patient-1'),
    ).not.toBeInTheDocument();
  });

  it('partnerAssigned=false → バッジも位置ラベルも出ない (両 slot 未配置)', () => {
    renderCard({
      slotIndex: 0,
      partnerAssigned: false,
      partnerLocationLabel: '本店-A 10:00', // 渡しても無視される
    });
    expect(
      screen.queryByTestId('patient-card-partner-assigned-test-patient-1'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('patient-card-partner-location-test-patient-1'),
    ).not.toBeInTheDocument();
  });

  it('通常患者 (slotIndex 未指定) → partnerLocationLabel は無視 (regression)', () => {
    renderCard({
      partnerAssigned: true,
      partnerLocationLabel: 'leak-canary',
    });
    expect(
      screen.queryByTestId('patient-card-partner-location-test-patient-1'),
    ).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 新人同行モード: selected プロップによる選択ハイライト
// ─────────────────────────────────────────────────────────────────────────────

describe('PatientCard — selected (同行モード選択ハイライト)', () => {
  it('selected=true → data-accompaniment-selected="true" + ring クラス', () => {
    render(
      <PatientCard
        draggableId="pool-patient:test-patient-1:slot:1"
        patient={makePatient({ slotIndex: 1, partnerAssigned: true })}
        selected
      />,
    );
    const card = screen.getByTestId('patient-card-slot-1-test-patient-1');
    expect(card.getAttribute('data-accompaniment-selected')).toBe('true');
    expect(card.className).toContain('ring-2');
  });

  it('selected 未指定 → data-accompaniment-selected は付かない', () => {
    render(
      <PatientCard
        draggableId="pool-patient:test-patient-1:slot:1"
        patient={makePatient({ slotIndex: 1, partnerAssigned: true })}
      />,
    );
    const card = screen.getByTestId('patient-card-slot-1-test-patient-1');
    expect(card.getAttribute('data-accompaniment-selected')).toBeNull();
    expect(card.className).not.toContain('ring-2');
  });
});
