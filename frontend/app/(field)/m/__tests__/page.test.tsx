/**
 * /m 現場ボード (Phase 1) — レンダーテスト.
 *
 * カバー:
 *   1. manager セッションで現場ボードが描画される (ヘッダー「CareFlow」「現場ボード」)
 *   2. DEMO チップが表示される (ライブデータ誤認防止)
 *   3. 患者カード click → カルテシートが開く (患者コード表示)
 *   4. 「提案」ボタン click → 提案シートが開く (「自動提案する」)
 *   5. staff セッションでは /m/home へリダイレクトされ、ボードを描画しない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

const mockReplace = vi.fn();

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
}));

import { useSession } from 'next-auth/react';
import FieldBoardPage from '../page';

type Role = 'admin' | 'manager' | 'staff';

function setSession(role: Role | null) {
  (useSession as unknown as ReturnType<typeof vi.fn>).mockReturnValue(
    role === null
      ? { data: null, status: 'unauthenticated' }
      : { data: { user: { role } }, status: 'authenticated' },
  );
}

describe('/m 現場ボード', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('manager セッションで現場ボードを描画し、DEMO チップを示す', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    expect(screen.getByText('CareFlow')).toBeInTheDocument();
    expect(screen.getByText('現場ボード')).toBeInTheDocument();
    expect(screen.getByText('DEMO')).toBeInTheDocument();
  });

  it('患者カード click でカルテシートが開く', () => {
    setSession('admin');
    render(<FieldBoardPage />);
    // 月曜・稲A の先頭患者「青柳 あい」をクリック
    fireEvent.click(screen.getAllByText('青柳 あい')[0]!);
    // カルテシートのヘッダーに患者コードが出る
    expect(screen.getByText(/患者コード: P-1042/)).toBeInTheDocument();
  });

  it('「提案」ボタンで提案シートが開く', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    fireEvent.click(screen.getByText('提案'));
    expect(screen.getByText('自動提案する')).toBeInTheDocument();
    expect(screen.getByText('新規訪問の提案')).toBeInTheDocument();
  });

  it('一覧レイアウト固定でコース一覧と曜日ステッパーが表示され、レイアウト切替UIは無い', () => {
    setSession('manager');
    render(<FieldBoardPage />);
    // 一覧 (agenda) のコースヘッダー (月曜・稲毛の先頭コース「稲A」) が出る
    expect(screen.getByText('稲A')).toBeInTheDocument();
    // 曜日ステッパーの当日見出し (月曜) が出る
    expect(screen.getByText('月曜')).toBeInTheDocument();
    // 旧レイアウト切替 (単日/週全体/横送り/一覧) のラジオは撤去済み
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(screen.queryByText('週全体')).not.toBeInTheDocument();
  });

  it('staff セッションでは /m/home へリダイレクトし、ボードを描画しない', () => {
    setSession('staff');
    const { container } = render(<FieldBoardPage />);
    expect(mockReplace).toHaveBeenCalledWith('/m/home');
    // ボード本体 (CareFlow ヘッダー) は描画されない
    expect(within(container).queryByText('現場ボード')).not.toBeInTheDocument();
  });
});
