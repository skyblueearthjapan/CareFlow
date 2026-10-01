/** ロゴのパス / URL の検証 (BE schemas/business_profile.is_allowed_logo_url と同じ規則)。 */
import { describe, expect, it } from 'vitest';

import { isAllowedLogoUrl } from '../businessProfile';

describe('isAllowedLogoUrl', () => {
  it.each([
    '',
    '/brand/yoriyori-logo-h.svg',
    '/brand/logo_v2.png',
    'https://cdn.example.com/logo.png',
    'https://cdn.example.com/a/b.svg?v=1',
  ])('受け付ける: %s', (v) => {
    expect(isAllowedLogoUrl(v)).toBe(true);
  });

  it.each([
    '//evil.example/x.svg',
    '/\\evil.example/x.svg',
    '/brand/logo .svg',
    '/brand/ロゴ.svg',
    '/brand/a\u0000.svg',
    'http://example.com/a.svg',
    'javascript:alert(1)',
    'https:///evil',
    'https://evil.example\\x.svg',
    'brand/logo.svg',
  ])('断る: %s', (v) => {
    expect(isAllowedLogoUrl(v)).toBe(false);
  });
});
