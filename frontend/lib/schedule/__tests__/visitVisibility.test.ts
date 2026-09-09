/**
 * 訪問の「見せ方」判定 — 患者ステータス連動 Phase 3「表示の保険」
 * (design 2026-09-09 §3-4 / §6-C Q15)。
 *
 * カバーするシナリオ:
 *   - 連動取消 (source='status_cancel' + status='cancelled') は既定で hidden、
 *     トグル ON で 'status_cancel' (打ち消し線 + 「取消（連動）」)。
 *   - 非稼働患者の planned が残っている → 常に 'inactive' (バッジ + 薄色)。
 *   - 稼働中 / ステータス不明 / 完了・取消済みは 'normal' (従来どおり)。
 *   - トグル OFF のときの hidden 判定は旧 `isStatusCancelledVisit` と完全同値。
 */
import { describe, it, expect } from 'vitest';

import { isStatusCancelledVisit } from '@/lib/schemas/v2/visit';
import {
  classifyVisitDisplay,
  isInactivePatientVisit,
  isVisitVisible,
  visitDisplayBadgeLabel,
  STATUS_CANCEL_BADGE_LABEL,
  VISIT_DISPLAY_CLASS,
} from '../visitVisibility';

describe('isInactivePatientVisit', () => {
  it('非稼働 (入院中・一時休止・解約済み・開始前) は true', () => {
    for (const s of ['admitted', 'suspended', 'cancelled', 'pending']) {
      expect(isInactivePatientVisit({ patient_status: s })).toBe(true);
    }
  });

  it('稼働中 / 欠落 / 空文字 / 未知の値は false (従来表示に倒す)', () => {
    expect(isInactivePatientVisit({ patient_status: 'active' })).toBe(false);
    expect(isInactivePatientVisit({})).toBe(false);
    expect(isInactivePatientVisit({ patient_status: null })).toBe(false);
    expect(isInactivePatientVisit({ patient_status: '' })).toBe(false);
    expect(isInactivePatientVisit({ patient_status: 'unknown-future-value' })).toBe(false);
  });
});

describe('classifyVisitDisplay', () => {
  const statusCancelled = {
    source: 'status_cancel',
    status: 'cancelled',
    patient_status: 'admitted',
  };

  it('連動取消は既定で hidden・トグル ON で status_cancel', () => {
    expect(classifyVisitDisplay(statusCancelled)).toBe('hidden');
    expect(classifyVisitDisplay(statusCancelled, { showInactive: false })).toBe('hidden');
    expect(classifyVisitDisplay(statusCancelled, { showInactive: true })).toBe('status_cancel');
  });

  it('非稼働患者の planned は常に inactive (トグルに関係なく見せる)', () => {
    const residue = { source: 'auto', status: 'planned', patient_status: 'admitted' };
    expect(classifyVisitDisplay(residue)).toBe('inactive');
    expect(classifyVisitDisplay(residue, { showInactive: true })).toBe('inactive');
    // status 欠落も予定扱い (寛容)。
    expect(classifyVisitDisplay({ patient_status: 'suspended' })).toBe('inactive');
  });

  it('非稼働でも完了・不在・今週だけ取消は normal (過去の実績に印を付けない)', () => {
    expect(classifyVisitDisplay({ status: 'completed', patient_status: 'admitted' })).toBe(
      'normal',
    );
    expect(classifyVisitDisplay({ status: 'no_show', patient_status: 'admitted' })).toBe('normal');
    expect(
      classifyVisitDisplay({
        source: 'manual_cancel',
        status: 'cancelled',
        patient_status: 'admitted',
      }),
    ).toBe('normal');
  });

  it('稼働中 / ステータス不明は normal', () => {
    expect(classifyVisitDisplay({ source: 'auto', status: 'planned' })).toBe('normal');
    expect(
      classifyVisitDisplay({ source: 'auto', status: 'planned', patient_status: 'active' }),
    ).toBe('normal');
  });

  it('source=status_cancel でも status が cancelled でなければ描く (寛容判定)', () => {
    expect(classifyVisitDisplay({ source: 'status_cancel', status: 'planned' })).toBe('normal');
  });

  it('トグル OFF の hidden 判定は旧 isStatusCancelledVisit と同値', () => {
    const samples = [
      { source: 'auto', status: 'planned' },
      { source: 'manual_cancel', status: 'cancelled' },
      { source: 'status_cancel', status: 'cancelled' },
      { source: 'status_cancel', status: 'planned' },
      {},
    ];
    for (const v of samples) {
      expect(classifyVisitDisplay(v) === 'hidden').toBe(isStatusCancelledVisit(v));
      expect(isVisitVisible(v)).toBe(!isStatusCancelledVisit(v));
    }
  });
});

describe('現場ボード / モニターのバッジ条件 (§3-4 の回帰ガード)', () => {
  it('今週だけ取消・訪問済みの行には「入院中」を出さない', () => {
    // 現場ボード (FieldBoard) / モニターはこの判定でバッジを出すので、
    // ここが 'inactive' を返さないことがそのまま「バッジを出さない」になる。
    for (const status of ['completed', 'no_show', 'cancelled']) {
      expect(
        classifyVisitDisplay(
          { source: 'manual_cancel', status, patient_status: 'admitted' },
          { showInactive: true },
        ),
      ).toBe('normal');
    }
  });

  it('まだ予定として残っている行にだけ「入院中」を出す', () => {
    expect(
      classifyVisitDisplay(
        { source: 'auto', status: 'planned', patient_status: 'admitted' },
        { showInactive: true },
      ),
    ).toBe('inactive');
  });
});

describe('visitDisplayBadgeLabel / VISIT_DISPLAY_CLASS', () => {
  it('inactive は患者ステータスのラベル・status_cancel は「取消（連動）」', () => {
    expect(visitDisplayBadgeLabel({ patient_status: 'admitted' }, 'inactive')).toBe('入院中');
    expect(visitDisplayBadgeLabel({ patient_status: 'suspended' }, 'inactive')).toBe('一時休止');
    expect(visitDisplayBadgeLabel({ patient_status: 'admitted' }, 'status_cancel')).toBe(
      STATUS_CANCEL_BADGE_LABEL,
    );
    expect(visitDisplayBadgeLabel({ patient_status: 'active' }, 'normal')).toBeNull();
    expect(visitDisplayBadgeLabel({ patient_status: 'active' }, 'inactive')).toBeNull();
  });

  it('normal は追加クラス無し / inactive は薄く / status_cancel は打ち消し線', () => {
    expect(VISIT_DISPLAY_CLASS.normal).toBe('');
    expect(VISIT_DISPLAY_CLASS.inactive).toContain('opacity');
    expect(VISIT_DISPLAY_CLASS.status_cancel).toContain('line-through');
  });
});
