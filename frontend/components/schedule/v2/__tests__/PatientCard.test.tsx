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
