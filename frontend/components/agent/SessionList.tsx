'use client';

import { useState } from 'react';

import { groupSessions } from './groups';
import type { SessionSummary } from './types';

export const SEARCH_THRESHOLD = 10;

/**
 * 相談の一覧(PC の小窓・スマホの ☰ ドロワー共通)。
 * 決めてほしいもの / 今日 / 昨日 / 今週 / それより前。✎ で名前を変える。11 件以上で検索欄。
 */
export function SessionList({
  sessions,
  currentId,
  onSelect,
  onNew,
  onRename,
  now,
}: {
  sessions: SessionSummary[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  now?: Date;
}) {
  const [q, setQ] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const groups = groupSessions(sessions, now, q);
  const commit = () => {
    if (editing && draft.trim()) onRename(editing, draft.trim());
    setEditing(null);
  };

  return (
    <div className="rk-sl">
      <button type="button" className="rk-btn rk-primary rk-big" onClick={onNew}>
        ＋ 新しい相談
      </button>
      {sessions.length > SEARCH_THRESHOLD && (
        <input
          type="search"
          className="rk-search"
          placeholder="相談を探す(人の名前・日付でも)"
          aria-label="相談を探す"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      )}
      {groups.length === 0 && <p className="rk-muted rk-pad">まだ相談はありません。</p>}
      {groups.map((g) => (
        <section key={g.name} aria-label={g.name} className="rk-sl-group">
          <h4>{g.name}</h4>
          <ul>
            {g.items.map((s) => (
              <li key={s.id} data-current={s.id === currentId}>
                {editing === s.id ? (
                  <input
                    autoFocus
                    aria-label="相談の名前"
                    className="rk-rename"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={commit}
                    onKeyDown={(e) => {
                      if (e.nativeEvent.isComposing) return;
                      if (e.key === 'Enter') commit();
                      if (e.key === 'Escape') setEditing(null);
                    }}
                  />
                ) : (
                  <>
                    <button type="button" className="rk-sl-item" onClick={() => onSelect(s.id)}>
                      {s.needs_you && <span className="rk-dot" data-st="ask" aria-label="確認待ち" />}
                      <span className="rk-sl-title">{s.title}</span>
                    </button>
                    <button
                      type="button"
                      className="rk-hbtn"
                      aria-label={`${s.title} の名前を変える`}
                      onClick={() => {
                        setDraft(s.title);
                        setEditing(s.id);
                      }}
                    >
                      ✎
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
