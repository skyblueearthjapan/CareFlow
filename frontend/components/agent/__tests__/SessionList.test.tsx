import * as React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { groupOf, groupSessions } from '../groups';
import { SessionList } from '../SessionList';
import type { SessionSummary } from '../types';

// 2026-10-14(水)の昼。今週の月曜は 10/12。
const NOW = new Date(2026, 9, 14, 12, 0, 0);
const at = (d: number, h = 9) => new Date(2026, 9, d, h, 0, 0).toISOString();

const list: SessionSummary[] = [
  { id: 'a', title: '決める相談', last_active_at: at(1), needs_you: true },
  { id: 'b', title: '今日の相談', last_active_at: at(14, 8) },
  { id: 'c', title: '昨日の相談', last_active_at: at(13) },
  { id: 'd', title: '今週の相談', last_active_at: at(12, 10) },
  { id: 'e', title: '先週の相談', last_active_at: at(8) },
];
const NAMES = ['決めてほしいもの', '今日', '昨日', '今週', 'それより前'];

describe('相談の一覧のグループ分け', () => {
  it('決めてほしいもの / 今日 / 昨日 / 今週 / それより前 に分ける', () => {
    expect(list.map((s) => groupOf(s, NOW))).toEqual(NAMES);
    expect(groupSessions(list, NOW).map((g) => g.name)).toEqual(NAMES);
  });

  it('空の群は出さず、検索で絞れる', () => {
    expect(groupSessions(list.slice(1, 2), NOW).map((g) => g.name)).toEqual(['今日']);
    expect(groupSessions(list, NOW, '昨日').flatMap((g) => g.items.map((i) => i.id))).toEqual(['c']);
  });
});

function renderList(sessions: SessionSummary[], over: Partial<React.ComponentProps<typeof SessionList>> = {}) {
  return render(
    <SessionList
      sessions={sessions}
      currentId={null}
      now={NOW}
      onSelect={vi.fn()}
      onNew={vi.fn()}
      onRename={vi.fn()}
      {...over}
    />,
  );
}

describe('SessionList(ドロワーの中身)', () => {
  it('群の見出しと相談を出し、選べる', () => {
    const onSelect = vi.fn();
    renderList(list, { currentId: 'b', onSelect });
    expect(screen.getByRole('region', { name: '決めてほしいもの' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'それより前' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '昨日の相談' }));
    expect(onSelect).toHaveBeenCalledWith('c');
    expect(screen.queryByRole('searchbox')).toBeNull(); // 10 件以下は検索欄なし
  });

  it('11 件以上で検索欄が出る', () => {
    const many = Array.from({ length: 11 }, (_, i) => ({
      id: `s${i}`,
      title: `相談${i}`,
      last_active_at: at(14, 8),
    }));
    renderList(many);
    expect(screen.getByRole('searchbox', { name: '相談を探す' })).toBeInTheDocument();
  });

  it('✎ で名前を変えられる', () => {
    const onRename = vi.fn();
    renderList(list, { onRename });
    fireEvent.click(screen.getByRole('button', { name: '今日の相談 の名前を変える' }));
    const input = screen.getByRole('textbox', { name: '相談の名前' });
    fireEvent.change(input, { target: { value: '新しい名前' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onRename).toHaveBeenCalledWith('b', '新しい名前');
  });
});
