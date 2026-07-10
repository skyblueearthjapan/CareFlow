/**
 * CloudflareAccessBanner — Access 切れ再ログイン導線の単体テスト (handoff §6-3)。
 *
 * 1. 初期状態では何も表示しない
 * 2. CF_ACCESS_EXPIRED_EVENT で警告バナーが出る
 * 3. 「閉じる」で消える
 */
import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import { CloudflareAccessBanner } from '../CloudflareAccessBanner';
import { CF_ACCESS_EXPIRED_EVENT } from '@/lib/cfAccess';

function fireExpired() {
  act(() => {
    window.dispatchEvent(new Event(CF_ACCESS_EXPIRED_EVENT));
  });
}

describe('CloudflareAccessBanner', () => {
  it('初期状態では何も表示しない', () => {
    render(<CloudflareAccessBanner />);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('CF_ACCESS_EXPIRED_EVENT でバナーが表示され、再ログイン導線を持つ', () => {
    render(<CloudflareAccessBanner />);
    fireExpired();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/Cloudflare のログイン期限/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '再ログインする' })).toBeInTheDocument();
  });

  it('「閉じる」でバナーが消える', () => {
    render(<CloudflareAccessBanner />);
    fireExpired();
    fireEvent.click(screen.getByRole('button', { name: '閉じる' }));
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
