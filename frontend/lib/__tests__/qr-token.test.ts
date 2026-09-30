import { describe, it, expect } from 'vitest';

import {
  QR_READ_AT_PARAM,
  extractQrToken,
  parseHandoffReadAt,
  visitDeepLinkHref,
} from '@/lib/qr-token';

describe('extractQrToken', () => {
  it('full URL から token を取り出す', () => {
    expect(extractQrToken('https://carelink.example/q/abc123XYZ')).toBe('abc123XYZ');
  });

  it('末尾スラッシュ / クエリ / ハッシュ付きでも token のみ抽出', () => {
    expect(extractQrToken('https://app.example/q/tok_en-1/?v=2#frag')).toBe('tok_en-1');
  });

  it('パスのみ (/q/{token}) を受理', () => {
    expect(extractQrToken('/q/PathOnly_9')).toBe('PathOnly_9');
  });

  it('生のトークン文字列をそのまま返す (手入力/旧QR互換)', () => {
    expect(extractQrToken('rawToken-_9')).toBe('rawToken-_9');
  });

  it('前後の空白を無視する', () => {
    expect(extractQrToken('  https://x.example/q/spaced  ')).toBe('spaced');
  });

  it('/q/ の後ろが空なら null', () => {
    expect(extractQrToken('https://x.example/q/')).toBeNull();
  });

  it('空文字は null', () => {
    expect(extractQrToken('')).toBeNull();
    expect(extractQrToken('   ')).toBeNull();
  });

  it('/q/ を含まない別サイトの URL は null (誤読取を弾く)', () => {
    expect(extractQrToken('https://evil.example/login?x=1')).toBeNull();
  });

  it('スラッシュを含むがトークン形式でない素文字列は null', () => {
    expect(extractQrToken('foo/bar')).toBeNull();
  });
});

/**
 * 読取時刻の引き継ぎ (`/q/{token}` → 訪問詳細・レビュー L-7)。URL のクエリは外から
 * 来る値なので、採れるものだけを採る。
 */
describe('parseHandoffReadAt', () => {
  const NOW = Date.parse('2026-09-30T04:10:00.000Z');

  it('直前に読み取った時刻はそのまま採る', () => {
    expect(parseHandoffReadAt('2026-09-30T04:06:20.000Z', NOW)).toBe('2026-09-30T04:06:20.000Z');
    expect(parseHandoffReadAt('2026-09-30T04:10:00.000Z', NOW)).toBe('2026-09-30T04:10:00.000Z');
  });

  it('未来の時刻は採らない (1 秒先でも)', () => {
    expect(parseHandoffReadAt('2026-09-30T04:10:01.000Z', NOW)).toBeNull();
    expect(parseHandoffReadAt('2026-10-01T00:00:00.000Z', NOW)).toBeNull();
  });

  it('古すぎる時刻は採らない (10 分を超えたら、開きっぱなしの URL とみなす)', () => {
    expect(parseHandoffReadAt('2026-09-30T04:00:00.000Z', NOW)).toBe('2026-09-30T04:00:00.000Z');
    expect(parseHandoffReadAt('2026-09-30T03:59:59.000Z', NOW)).toBeNull();
    expect(parseHandoffReadAt('2026-09-29T04:06:20.000Z', NOW)).toBeNull();
  });

  it('形の違う値・日付として読めない値・空は採らない', () => {
    for (const raw of [
      '',
      null,
      undefined,
      'abc',
      '13:06',
      '1759205180000',
      '2026-09-30',
      '2026-09-30T13:06:20+09:00',
      '2026-09-30T04:06:20',
      '2026-13-45T99:99:99.000Z',
      '2026-09-30T04:06:20.000Z<script>',
    ]) {
      expect(parseHandoffReadAt(raw, NOW)).toBeNull();
    }
  });
});

describe('visitDeepLinkHref', () => {
  it('?qr= に読取時刻を添える (どちらも URL エンコードする)', () => {
    const href = visitDeepLinkHref('visit-1', 'TOK123', '2026-09-30T04:06:20.000Z');
    expect(href).toBe('/m/today/visit-1?qr=TOK123&read_at=2026-09-30T04%3A06%3A20.000Z');
    const qs = new URLSearchParams(href.split('?')[1]);
    expect(qs.get('qr')).toBe('TOK123');
    expect(qs.get(QR_READ_AT_PARAM)).toBe('2026-09-30T04:06:20.000Z');
  });
});
