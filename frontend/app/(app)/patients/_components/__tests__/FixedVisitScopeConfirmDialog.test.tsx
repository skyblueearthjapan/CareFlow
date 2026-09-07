/**
 * FixedVisitScopeConfirmDialog の純関数ユニットテスト (Phase E / 設計 §6).
 *
 * ダイアログの描画・保存フローは PatientFixedVisitsPanel.test.tsx の
 * 「Phase E: 反映先の事前確認」で検証する。ここでは件数と週範囲の計算という
 * 事故になりやすい部分 (ISO 週の日付展開・保護判定・差分) だけを固定する。
 */
import { describe, it, expect } from 'vitest';

import {
  diffFixedVisitSlots,
  formatDiffEntry,
  formatIsoWeekLabel,
  hasCheckin,
  isDeletedByReset,
  isoWeekDateStrings,
  suppressesRegen,
} from '../FixedVisitScopeConfirmDialog';
import type { VisitRead } from '@/lib/schemas/visit';

function visit(over: Partial<VisitRead>): VisitRead {
  return {
    id: '00000000-0000-0000-0000-0000000000a1',
    patient_id: '00000000-0000-0000-0000-000000000001',
    visit_date: '2026-09-14',
    start_time: '09:30:00',
    end_time: '10:05:00',
    type: 'regular',
    status: 'planned',
    source: 'allocate',
    created_at: '2026-09-01T00:00:00',
    updated_at: '2026-09-01T00:00:00',
    ...over,
  } as VisitRead;
}

describe('isoWeekDateStrings / formatIsoWeekLabel', () => {
  it('2026 年 ISO 第 37 週は 9/7(月)〜9/13(日)', () => {
    expect(isoWeekDateStrings(2026, 37)).toEqual([
      '2026-09-07',
      '2026-09-08',
      '2026-09-09',
      '2026-09-10',
      '2026-09-11',
      '2026-09-12',
      '2026-09-13',
    ]);
    expect(formatIsoWeekLabel(2026, 37)).toBe('9/7 週（9/7〜9/13）');
  });

  it('年跨ぎ (2026 W1) も月曜始まりで展開できる', () => {
    const dates = isoWeekDateStrings(2026, 1);
    expect(dates[0]).toBe('2025-12-29');
    expect(dates[6]).toBe('2026-01-04');
  });
});

// BE は許可リスト (_RESET_DELETABLE_SOURCES / _RESET_DELETABLE_STATUSES) で消す。
// 「manual_week 以外は全部消える」という否定形で数えると消える件数を過大に見せる。
describe('isDeletedByReset', () => {
  it('自動生成由来 × planned/proposed だけが消える', () => {
    for (const source of [
      'auto',
      'auto_alloc',
      'auto_alloc_v2',
      'auto_alloc_v2w',
      'pfv',
      'fixed',
      'reset_v2',
    ]) {
      expect(isDeletedByReset(visit({ source, status: 'planned' }))).toBe(true);
    }
    expect(isDeletedByReset(visit({ source: 'reset_v2', status: 'proposed' }))).toBe(true);
  });

  it('手動作成 (manual) は許可リスト外なので消えない', () => {
    expect(isDeletedByReset(visit({ source: 'manual', status: 'planned' }))).toBe(false);
    // source 欠落は zod default で 'manual' になる = 保護側に落ちるのが正しい
    expect(isDeletedByReset(visit({ status: 'planned' }))).toBe(false);
  });

  it('実施済み / 訪問中 / 確定 の status は消えない', () => {
    expect(isDeletedByReset(visit({ source: 'auto', status: 'completed' }))).toBe(false);
    expect(isDeletedByReset(visit({ source: 'auto', status: 'in_progress' }))).toBe(false);
    expect(isDeletedByReset(visit({ source: 'auto', status: 'confirmed' }))).toBe(false);
  });

  it('青ピン (week_pinned) は source/status に関わらず消えない', () => {
    expect(isDeletedByReset(visit({ source: 'auto', status: 'planned', week_pinned: true }))).toBe(
      false,
    );
  });
});

describe('suppressesRegen', () => {
  it('week_pinned / manual_week / import は同日の型スロット再生成を止める', () => {
    expect(suppressesRegen(visit({ week_pinned: true }))).toBe(true);
    expect(suppressesRegen(visit({ source: 'manual_week' }))).toBe(true);
    expect(suppressesRegen(visit({ source: 'import' }))).toBe(true);
  });

  it('manual / completed は「消えない」が日スキップはしない (削除可否とは別軸)', () => {
    expect(suppressesRegen(visit({ source: 'manual' }))).toBe(false);
    expect(suppressesRegen(visit({ source: 'auto', status: 'completed' }))).toBe(false);
  });
});

describe('hasCheckin', () => {
  it('latest_checkin か 打刻で進んだ status (in_progress/completed) を打刻済みとみなす', () => {
    expect(hasCheckin(visit({ status: 'planned' }))).toBe(false);
    expect(hasCheckin(visit({ status: 'in_progress' }))).toBe(true);
    expect(hasCheckin(visit({ status: 'completed' }))).toBe(true);
    expect(
      hasCheckin(visit({ status: 'planned', latest_checkin: { id: 'c1' } } as Partial<VisitRead>)),
    ).toBe(true);
  });
});

describe('diffFixedVisitSlots', () => {
  it('追加 / 変更 / 削除 を曜日順に返す', () => {
    const before = [
      { weekday: 0, start_time: '09:30', duration_min: 35 },
      { weekday: 1, start_time: '10:00', duration_min: 30 },
    ];
    const after = [
      { weekday: 0, start_time: '12:00', duration_min: 35 },
      { weekday: 3, start_time: '12:00', duration_min: 35 },
    ];
    const diff = diffFixedVisitSlots(before, after);
    expect(diff.map((e) => [e.weekday, e.kind])).toEqual([
      [0, 'changed'],
      [1, 'removed'],
      [3, 'added'],
    ]);
    expect(formatDiffEntry(diff[0]!)).toBe('月 09:30(35分) → 12:00(35分) に変更');
    expect(formatDiffEntry(diff[1]!)).toBe('火 10:00(30分) を削除');
    expect(formatDiffEntry(diff[2]!)).toBe('木 12:00(35分) を追加');
  });

  it('2 名体制 (同曜日 slot 0/1) は 1 件に畳んで比較する', () => {
    const before = [
      { weekday: 0, start_time: '09:00:00', duration_min: 30 },
      { weekday: 0, start_time: '09:00:00', duration_min: 30 },
    ];
    const after = [
      { weekday: 0, start_time: '09:00', duration_min: 30 },
      { weekday: 0, start_time: '09:00', duration_min: 30 },
    ];
    expect(diffFixedVisitSlots(before, after)).toEqual([]);
  });
});
