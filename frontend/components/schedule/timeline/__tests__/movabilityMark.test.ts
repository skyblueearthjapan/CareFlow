/**
 * 可動域マーク (2026-08-07 / PO 要望「うっすらでもいいので表示」) の表示判定。
 *
 * 背景: ピン留め (📌) は盤面に出ていたが、その「さらに先」にある可動域は
 * どこにも表示されていなかった。一括ピン解除して提案を出させる運用では、
 * どの枠が完全固定として守られているのかが現場から見えない。
 */
import { describe, expect, it } from 'vitest';

import { movabilityMarkFor } from '../MovabilityMark';

describe('movabilityMarkFor', () => {
  it('完全固定 (locked) は「固」を出す', () => {
    expect(movabilityMarkFor({ is_pinned: false, movability: 'locked' })).toEqual({
      mark: '固',
      title: '可動域: 完全固定（提案も自動割当も動かしません）',
    });
  });

  it('time_flexible は「時」、day_flexible は「曜」', () => {
    expect(movabilityMarkFor({ is_pinned: false, movability: 'time_flexible' })?.mark).toBe('時');
    expect(movabilityMarkFor({ is_pinned: false, movability: 'day_flexible' })?.mark).toBe('曜');
  });

  it('未設定 (unknown) は出さない — 大多数がこれなのでノイズになる', () => {
    expect(movabilityMarkFor({ is_pinned: false, movability: 'unknown' })).toBeNull();
  });

  it('PFV 非紐付け (null / undefined) は出さない', () => {
    expect(movabilityMarkFor({ is_pinned: false, movability: null })).toBeNull();
    expect(movabilityMarkFor({ is_pinned: false })).toBeNull();
  });

  it('ピン留め済みは出さない — 📌 と二重表示になるため', () => {
    expect(movabilityMarkFor({ is_pinned: true, movability: 'locked' })).toBeNull();
    expect(movabilityMarkFor({ is_pinned: true, movability: 'day_flexible' })).toBeNull();
  });

  it('未知の値でも壊れない (BE が値を増やしても盤面を落とさない)', () => {
    expect(
      movabilityMarkFor({
        is_pinned: false,
        movability: 'something_new' as unknown as 'locked',
      }),
    ).toBeNull();
  });
});
