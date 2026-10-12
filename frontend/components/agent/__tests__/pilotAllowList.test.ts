import { describe, expect, it } from 'vitest';
import { isInPilot, parseAllowList } from '../useAgentEnabled';

describe('試験の許可リスト', () => {
  it('空なら誰にも出さない', () => {
    expect(isInPilot({ id: 'u1', email: 'a@example.com' }, parseAllowList(''))).toBe(false);
  });
  it('ID かメールが入っていれば出す', () => {
    expect(isInPilot({ id: 'u1' }, parseAllowList('u1,u2'))).toBe(true);
    expect(isInPilot({ email: 'A@example.com' }, parseAllowList('a@example.com'))).toBe(true);
    expect(isInPilot({ id: 'u3' }, parseAllowList('u1,u2'))).toBe(false);
  });
  it("'*' なら全員（管理者かどうかは別に確かめる）", () => {
    expect(isInPilot({ id: 'u9' }, parseAllowList('*'))).toBe(true);
    expect(isInPilot(undefined, parseAllowList('*'))).toBe(false);
  });
});
