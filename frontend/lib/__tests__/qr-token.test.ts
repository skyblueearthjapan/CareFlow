import { describe, it, expect } from 'vitest';

import { extractQrToken } from '@/lib/qr-token';

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
