/**
 * `buildListUrl` — GET /api/v1/staff/{id}/events のクエリ組み立て
 * (staff-event-history-design.md §2 Phase 1)。
 *
 * ここが BE 契約の接点。パラメータ名 (`hide_regular` など) を間違えると
 * 「絞り込んだつもりで全件が返る」静かな不具合になるため直接検証する。
 */
import { describe, it, expect } from 'vitest';

import { buildListUrl } from '../staff-events';

const SID = '11111111-1111-1111-1111-111111111111';
const BASE = `/api/v1/staff/${SID}/events`;

function params(url: string): Record<string, string> {
  const qs = url.includes('?') ? url.slice(url.indexOf('?') + 1) : '';
  return Object.fromEntries(new URLSearchParams(qs).entries());
}

describe('buildListUrl', () => {
  it('引数なしは従来どおりの素の URL (後方互換)', () => {
    expect(buildListUrl(SID)).toBe(BASE);
  });

  it('range は from/to、片側だけの指定 (過去タブ) も通る', () => {
    expect(params(buildListUrl(SID, { from: '2026-08-24', to: '2027-02-20' }))).toEqual({
      from: '2026-08-24',
      to: '2027-02-20',
    });
    expect(params(buildListUrl(SID, { to: '2026-08-23' }))).toEqual({ to: '2026-08-23' });
  });

  it('絞り込みは BE パラメータに載る', () => {
    const url = buildListUrl(
      SID,
      { to: '2026-08-23' },
      { q: ' 鈴木 ', source: 'kaipoke', type: 'training', order: 'desc', hideRegular: true },
    );
    expect(params(url)).toEqual({
      to: '2026-08-23',
      q: '鈴木',
      source: 'kaipoke',
      type: 'training',
      order: 'desc',
      hide_regular: 'true',
    });
  });

  it('既定値・空値はクエリに載せない (未絞り込みは素の URL のまま)', () => {
    const url = buildListUrl(SID, undefined, {
      q: '   ',
      source: null,
      type: null,
      order: 'asc',
      hideRegular: false,
    });
    expect(url).toBe(BASE);
  });
});
