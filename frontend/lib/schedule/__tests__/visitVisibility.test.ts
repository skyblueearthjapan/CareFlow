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
  todayJstIso,
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

/**
 * PO フィードバック 2026-09-10: 「入院中」バッジはステータスを変えた日以降の
 * 予定にだけ出す。それ以前 = 実際に訪問した日なので従来表示のまま。
 */
describe('バッジの日付条件 (patient_status_since)', () => {
  const TODAY = '2026-09-10';
  const admitted = {
    source: 'auto',
    status: 'planned',
    patient_status: 'admitted',
  } as const;

  it('起点日より前の予定にはバッジを出さない (normal)', () => {
    expect(
      classifyVisitDisplay(
        { ...admitted, visit_date: '2026-09-07', patient_status_since: '2026-09-08' },
        { today: TODAY },
      ),
    ).toBe('normal');
  });

  it('起点日当日・以降の予定にはバッジを出す (inactive)', () => {
    for (const day of ['2026-09-08', '2026-09-09', '2026-09-30']) {
      expect(
        classifyVisitDisplay(
          { ...admitted, visit_date: day, patient_status_since: '2026-09-08' },
          { today: TODAY },
        ),
      ).toBe('inactive');
    }
  });

  it('起点日が無い (mig 0082 以前) なら今日を起点にする', () => {
    expect(
      classifyVisitDisplay(
        { ...admitted, visit_date: '2026-09-09', patient_status_since: null },
        { today: TODAY },
      ),
    ).toBe('normal');
    expect(classifyVisitDisplay({ ...admitted, visit_date: TODAY }, { today: TODAY })).toBe(
      'inactive',
    );
    expect(classifyVisitDisplay({ ...admitted, visit_date: '2026-09-11' }, { today: TODAY })).toBe(
      'inactive',
    );
  });

  it('visit_date が無い入力は従来どおり (日付条件を課さない)', () => {
    expect(classifyVisitDisplay(admitted, { today: TODAY })).toBe('inactive');
    expect(classifyVisitDisplay({ ...admitted, visit_date: null }, { today: TODAY })).toBe(
      'inactive',
    );
    // 解釈できない値も「日付不明」= 従来どおり。
    expect(classifyVisitDisplay({ ...admitted, visit_date: 'unknown' }, { today: TODAY })).toBe(
      'inactive',
    );
  });

  it('ISO 日時が来ても先頭 10 文字 (日付) で比較する', () => {
    expect(
      classifyVisitDisplay(
        {
          ...admitted,
          visit_date: '2026-09-07T10:00:00',
          patient_status_since: '2026-09-08T00:00:00',
        },
        { today: TODAY },
      ),
    ).toBe('normal');
  });

  it('日付条件は連動取消 (hidden / status_cancel) の判定を変えない', () => {
    const cancelled = {
      source: 'status_cancel',
      status: 'cancelled',
      patient_status: 'admitted',
      visit_date: '2026-09-01',
      patient_status_since: '2026-09-08',
    };
    expect(classifyVisitDisplay(cancelled, { today: TODAY })).toBe('hidden');
    expect(classifyVisitDisplay(cancelled, { today: TODAY, showInactive: true })).toBe(
      'status_cancel',
    );
  });

  it('today 未指定なら JST の今日を使う', () => {
    expect(todayJstIso(new Date('2026-09-09T23:00:00Z'))).toBe('2026-09-10'); // JST +9h
    // 明後日の予定は起点 (= 今日) より後なので必ず inactive。
    const future = new Date(Date.now() + 2 * 86400000).toISOString().slice(0, 10);
    expect(classifyVisitDisplay({ ...admitted, visit_date: future })).toBe('inactive');
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
